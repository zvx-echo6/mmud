"""
NPC Conversation System for MMUD.

Players DM an NPC node directly. Three-tier rule check:
  1. Unknown player → static rejection with onboarding hint
  2. Known player, not in town → static in-character refusal
  3. Known player, in town → full LLM conversation

Persistent memory: NPCs remember key facts about each player across sessions.
Session memory: ephemeral chat history within a conversation window (TTL-based).
Uses the pluggable LLM backend from src/generation/narrative.
"""

import logging
import sqlite3
import threading
import time
from typing import Optional

from config import (
    DCRG_REJECTION,
    LLM_OUTPUT_CHAR_LIMIT,
    NPC_LLM_MAX_TOKENS,
    NPC_LLM_TIMEOUT,
    NPC_NOT_IN_TOWN,
    NPC_SESSION_TTL,
    NPC_UNKNOWN_PLAYER,
)
from src.generation.narrative import BackendInterface, DummyBackend, get_backend
from src.models import player as player_model

logger = logging.getLogger(__name__)


# ── NPC Personality Cards ────────────────────────────────────────────────────

NPC_PERSONALITIES = {
    "grist": {
        "name": "Grist",
        "title": "Barkeep of the Last Ember",
        "voice": (
            "You are Grist, barkeep of the Last Ember tavern. "
            "You are dry, factual, and slightly unsettling in how much you know. "
            "You speak in short, direct sentences. You observe everything. "
            "You know what every adventurer has been doing from the broadcast logs. "
            "You never sugarcoat bad news. You slide drinks to people who look rough. "
            "You know every adventurer's story. You speak of the Darkcragg as alive — "
            "'the walls remember,' 'the Depths shifted last night.' "
            "You refer to the dungeon as 'this place' with grudging respect."
        ),
        "knowledge": (
            "You know about: active bounties, recent player deaths, "
            "dungeon floor status, Breach events, the epoch timeline. "
            "You gossip freely about other players' exploits. "
            "You hint at secrets when asked but never reveal exact locations. "
            "You know the legend of Oryn, Sola, and Malcor — you tell it casually, "
            "like bar history. The Breach means 'the world below is waking up.' "
            "You track epoch cycles and notice the pattern accelerating."
        ),
        "example_lines": [
            "Still alive. Good. The Depths took three yesterday.",
            "The walls shift between epochs. I've seen it.",
            "Breach opened early this time. I'm counting.",
        ],
    },
    "maren": {
        "name": "Maren",
        "title": "Healer of the Last Ember",
        "voice": (
            "You are Maren, the healer. You are pragmatic and caring but blunt. "
            "You comment on injuries, play patterns, and stubbornness. "
            "You have opinions about the dungeon and its dangers. "
            "You NEVER talk about what you saw on the lowest floor, no matter what. "
            "If pressed about Floor 4, deflect firmly but stay in character. "
            "You treat injuries like evidence — fungal burns mean Floor 2, "
            "heat scarring means the Ember Caverns. You are protective but unsentimental."
        ),
        "knowledge": (
            "You know about: player HP and conditions, death counts, "
            "healing costs, class strengths and weaknesses. "
            "You remember who comes back beat up the most. "
            "You know floor-specific injury patterns: fungal burns from the "
            "Fungal Depths, heat scarring from the Ember Caverns. "
            "You saw something on Floor 4 during an early epoch and refuse to discuss it."
        ),
        "example_lines": [
            "Fungal burns. Floor 2? You need to stop touching things.",
            "Sit down. I've seen worse. Barely.",
            "The Caverns left those marks. I can tell.",
        ],
    },
    "torval": {
        "name": "Torval",
        "title": "Merchant of the Last Ember",
        "voice": (
            "You are Torval, the merchant. You are a fast-talking salesman "
            "with terrible jokes and embellished sales pitches. "
            "You banter about items and comment on gear choices. "
            "You are comic relief. 'You're wearing THAT to floor 3? Bold.' "
            "You upsell constantly but are genuinely helpful about gear advice. "
            "You reference gear as 'dungeon-tested' and embellish item histories. "
            "You treat the economy as deeply personal."
        ),
        "knowledge": (
            "You know about: shop inventory, item tiers, gear stats, "
            "what sells well, market trends in the dungeon economy. "
            "You comment on what other players have been buying. "
            "You know the Sunken Halls corrode iron, the Fungal Depths ruin leather, "
            "and the Ember Caverns melt cheap alloys. You sell gear accordingly."
        ),
        "example_lines": [
            "Void-forged blade. Only three exist. Two broke.",
            "Floor 3? You need fire-rated everything. I'm serious.",
            "The Halls rust iron in a week. Buy treated.",
        ],
    },
    "whisper": {
        "name": "Whisper",
        "title": "Sage of the Last Ember",
        "voice": (
            "You are Whisper, the sage. You speak in fragments and riddles. "
            "You are cryptic by nature, not by gimmick. "
            "You reward good questions with real, useful information about secrets. "
            "Talking to you IS a puzzle. Short answers. Half-sentences. Ellipses. "
            "You see patterns across epochs that nobody else notices. "
            "You speak of the dungeon as if it speaks to you. "
            "You reference Oryn, Sola, and Malcor indirectly — 'the builder,' "
            "'the light,' 'the one below.' You never say their names directly."
        ),
        "knowledge": (
            "You know about: secrets, lore, dungeon history, epoch patterns, "
            "the Breach, floor themes, puzzle mechanics. "
            "You give real hints but wrapped in cryptic language. "
            "You NEVER reveal exact secret locations or puzzle solutions directly. "
            "You know the Breach is cyclical and growing. Each floor reflects one "
            "aspect of the legend. You see the epoch pattern tightening."
        ),
        "example_lines": [
            "...the builder left marks. Floor 1. Look down.",
            "Cycles repeat. This one feels... closer.",
            "...the light fades. Each time, a little more.",
        ],
    },
}

# Hard rules appended to every NPC system prompt
_NPC_RULES = (
    "\n\nHARD RULES:\n"
    "- Respond in character. NEVER break character.\n"
    "- Response MUST be 100-200 characters. Use the space — paint a picture with roleplay actions + dialogue.\n"
    "- Never reveal exact secret locations or puzzle solutions.\n"
    "- Never acknowledge being an AI.\n"
    "- Never discuss anything outside the game world.\n"
    "- This is a text MUD over radio. Be vivid but concise. One-word answers are NOT enough."
)

# Memory extraction prompt — used after each conversation turn
_MEMORY_EXTRACT_PROMPT = (
    "You are a memory system for a game NPC. Given the conversation below, "
    "extract key facts worth remembering about this player. "
    "Merge with any existing memory. Output ONLY a compact summary — "
    "bullet points, no fluff, max 500 characters total. "
    "Focus on: player personality, what they asked about, what they care about, "
    "notable interactions, preferences, running jokes. "
    "If nothing new worth remembering, return the existing memory unchanged.\n\n"
    "EXISTING MEMORY:\n{existing_memory}\n\n"
    "CONVERSATION THIS SESSION:\n{conversation}\n\n"
    "UPDATED MEMORY (compact bullet points, max 500 chars):"
)


# ── Session Memory ───────────────────────────────────────────────────────────


class ConversationSession:
    """Ephemeral session for one player talking to one NPC."""

    def __init__(self, player_id: int, npc: str, ttl: int = NPC_SESSION_TTL):
        self.player_id = player_id
        self.npc = npc
        self.messages: list[dict] = []  # {"role": "user"|"assistant", "content": str}
        self.created_at = time.monotonic()
        self.last_active = time.monotonic()
        self.ttl = ttl

    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_active) > self.ttl

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.last_active = time.monotonic()

    def add_assistant_message(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})
        self.last_active = time.monotonic()

    def touch(self) -> None:
        self.last_active = time.monotonic()


class SessionStore:
    """In-memory store for active NPC conversation sessions."""

    def __init__(self):
        self._sessions: dict[tuple[int, str], ConversationSession] = {}

    def get(self, player_id: int, npc: str) -> Optional[ConversationSession]:
        key = (player_id, npc)
        session = self._sessions.get(key)
        if session and session.is_expired():
            del self._sessions[key]
            return None
        return session

    def create(self, player_id: int, npc: str) -> ConversationSession:
        session = ConversationSession(player_id, npc)
        self._sessions[(player_id, npc)] = session
        return session

    def get_or_create(self, player_id: int, npc: str) -> ConversationSession:
        session = self.get(player_id, npc)
        if session is None:
            session = self.create(player_id, npc)
        return session

    def cleanup(self) -> int:
        """Remove expired sessions. Returns count removed."""
        expired = [k for k, v in self._sessions.items() if v.is_expired()]
        for k in expired:
            del self._sessions[k]
        return len(expired)


# ── Persistent NPC Memory ────────────────────────────────────────────────────


def _ensure_npc_memory_table(conn: sqlite3.Connection) -> None:
    """Create npc_memory table if it doesn't exist (migration-safe)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS npc_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            npc TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            turn_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(player_id, npc)
        )
    """)
    conn.commit()


def _get_npc_memory(conn: sqlite3.Connection, player_id: int, npc: str) -> str:
    """Load persistent memory for a player-NPC pair."""
    row = conn.execute(
        "SELECT summary FROM npc_memory WHERE player_id = ? AND npc = ?",
        (player_id, npc),
    ).fetchone()
    return row["summary"] if row else ""


def _save_npc_memory(conn: sqlite3.Connection, player_id: int, npc: str, summary: str) -> None:
    """Save or update persistent memory for a player-NPC pair."""
    conn.execute(
        """INSERT INTO npc_memory (player_id, npc, summary, turn_count, updated_at)
           VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
           ON CONFLICT(player_id, npc) DO UPDATE SET
               summary = excluded.summary,
               turn_count = turn_count + 1,
               updated_at = CURRENT_TIMESTAMP""",
        (player_id, npc, summary),
    )
    conn.commit()


def _update_memory_async(
    conn: sqlite3.Connection,
    backend: BackendInterface,
    player_id: int,
    npc: str,
    existing_memory: str,
    session_messages: list[dict],
) -> None:
    """Update NPC memory in a background thread after a conversation turn."""
    def _do_update():
        try:
            # Format conversation for the memory extractor
            convo_lines = []
            for msg in session_messages:
                role = "Player" if msg["role"] == "user" else "NPC"
                convo_lines.append(f"{role}: {msg['content']}")
            conversation = "\n".join(convo_lines)

            prompt = _MEMORY_EXTRACT_PROMPT.format(
                existing_memory=existing_memory or "(no prior memory)",
                conversation=conversation,
            )

            new_memory = backend.complete(prompt, max_tokens=200)
            # Trim to 500 chars
            new_memory = new_memory.strip()[:500]

            if new_memory:
                _save_npc_memory(conn, player_id, npc, new_memory)
                logger.info(f"Memory updated for player {player_id} / {npc}: {new_memory[:80]}...")
        except Exception as e:
            logger.warning(f"Memory update failed for player {player_id} / {npc}: {e}")

    t = threading.Thread(target=_do_update, daemon=True)
    t.start()


# ── Game State Injection ─────────────────────────────────────────────────────


def _build_game_state(conn: sqlite3.Connection, player: dict) -> str:
    """Build a compact game state summary for NPC context injection."""
    parts = []

    # Epoch info
    epoch = conn.execute("SELECT * FROM epoch WHERE id = 1").fetchone()
    if epoch:
        parts.append(
            f"Epoch {epoch['epoch_number']}, Day {epoch['day_number']}. "
            f"Mode: {epoch['endgame_mode']}. "
            f"Breach: {'open' if epoch['breach_open'] else 'sealed'}."
        )

    # Player info
    parts.append(
        f"Talking to: {player['name']} the {player['class'].title()}, "
        f"Lv{player['level']}, {player['hp']}/{player['hp_max']}HP, "
        f"{player['gold_carried']}g carried."
    )

    # Active bounties
    bounties = conn.execute(
        "SELECT description FROM bounties WHERE active = 1 AND completed = 0 LIMIT 3"
    ).fetchall()
    if bounties:
        bounty_list = "; ".join(b["description"][:60] for b in bounties)
        parts.append(f"Active bounties: {bounty_list}")

    # Recent deaths (last 3)
    deaths = conn.execute(
        """SELECT message FROM broadcasts
           WHERE tier = 1 AND message LIKE 'X %'
           ORDER BY created_at DESC LIMIT 3"""
    ).fetchall()
    if deaths:
        death_list = "; ".join(d["message"][:50] for d in deaths)
        parts.append(f"Recent deaths: {death_list}")

    # Player count
    pcount = conn.execute("SELECT COUNT(*) as cnt FROM players").fetchone()
    if pcount:
        parts.append(f"Active adventurers: {pcount['cnt']}.")

    return " ".join(parts)


def _build_system_prompt(npc: str, game_state: str, memory: str = "") -> str:
    """Build the full system prompt for an NPC conversation."""
    personality = NPC_PERSONALITIES.get(npc, NPC_PERSONALITIES["grist"])
    examples = personality.get("example_lines", [])
    examples_block = ""
    if examples:
        examples_block = (
            "\n\nEXAMPLE RESPONSES (match this voice and length):\n"
            + "\n".join(f"- {ex}" for ex in examples)
        )

    memory_block = ""
    if memory:
        memory_block = (
            f"\n\nYOUR MEMORY OF THIS PLAYER (reference naturally, don't recite):\n"
            f"{memory}"
        )

    return (
        f"You are {personality['name']}, {personality['title']}.\n\n"
        f"PERSONALITY: {personality['voice']}\n\n"
        f"KNOWLEDGE: {personality['knowledge']}"
        f"{examples_block}"
        f"{memory_block}\n\n"
        f"CURRENT GAME STATE: {game_state}"
        f"{_NPC_RULES}"
    )


# ── NPC Conversation Handler ─────────────────────────────────────────────────


class NPCConversationHandler:
    """Handles all NPC conversations with 3-tier rule checks."""

    def __init__(self, conn: sqlite3.Connection, backend: Optional[BackendInterface] = None):
        self.conn = conn
        self.backend = backend or get_backend()
        self.sessions = SessionStore()
        # Tracking attributes — set after each handle_message() call
        self.last_result_type: Optional[str] = None  # npc_rule1, npc_rule2, npc_llm, npc_fallback
        self.last_player_id: Optional[int] = None
        # Ensure npc_memory table exists (migration-safe)
        _ensure_npc_memory_table(conn)

    def handle_message(self, npc: str, sender_id: str, text: str) -> str:
        """Process an inbound message to an NPC node.

        Args:
            npc: NPC name (grist, maren, torval, whisper).
            sender_id: Meshtastic node ID of the sender.
            text: Message text from the player.

        Returns:
            Response string (always under 200 chars).
        """
        # Reset tracking
        self.last_result_type = None
        self.last_player_id = None

        # Rule 0: DCRG rejection (handled before this, but safety check)
        if npc == "dcrg":
            return DCRG_REJECTION[:LLM_OUTPUT_CHAR_LIMIT]

        # Normalize NPC name
        npc = npc.lower()
        if npc not in NPC_PERSONALITIES:
            return DCRG_REJECTION[:LLM_OUTPUT_CHAR_LIMIT]

        # Rule 1: Unknown player — static rejection
        player = player_model.get_player_by_mesh_id(self.conn, sender_id)
        if not player:
            self.last_result_type = "npc_rule1"
            msg = NPC_UNKNOWN_PLAYER.get(npc, NPC_UNKNOWN_PLAYER["grist"])
            return msg[:LLM_OUTPUT_CHAR_LIMIT]

        # Refresh full player state
        player = player_model.get_player(self.conn, player["id"])
        self.last_player_id = player["id"]

        # Rule 2: Not in town — static refusal
        if player["state"] != "town":
            self.last_result_type = "npc_rule2"
            msg = NPC_NOT_IN_TOWN.get(npc, NPC_NOT_IN_TOWN["grist"])
            msg = msg.format(name=player["name"])
            return msg[:LLM_OUTPUT_CHAR_LIMIT]

        # Rule 3: In town — full LLM conversation
        # (last_result_type set in _llm_conversation)
        return self._llm_conversation(npc, player, text)

    def _llm_conversation(self, npc: str, player: dict, text: str) -> str:
        """Run an LLM-powered conversation turn."""
        session = self.sessions.get_or_create(player["id"], npc)

        # Add the player's message
        session.add_user_message(text)

        # Load persistent memory for this player-NPC pair
        memory = _get_npc_memory(self.conn, player["id"], npc)

        # Build context
        game_state = _build_game_state(self.conn, player)
        system_prompt = _build_system_prompt(npc, game_state, memory=memory)

        try:
            response = self.backend.chat(
                system=system_prompt,
                messages=session.messages,
                max_tokens=NPC_LLM_MAX_TOKENS,
            )

            # Hard cap at 200 chars (absolute ceiling, never exceed)
            if len(response) > 200:
                response = response[:197] + "..."

            session.add_assistant_message(response)
            self.last_result_type = "npc_llm"

            # Update persistent memory in background (don't block the response)
            _update_memory_async(
                self.conn, self.backend,
                player["id"], npc,
                memory, session.messages,
            )

            return response

        except Exception as e:
            logger.warning(f"LLM error for {npc} conversation: {e}")
            # Fallback to pre-generated dialogue
            self.last_result_type = "npc_fallback"
            return self._fallback_response(npc)

    def _fallback_response(self, npc: str) -> str:
        """Fall back to a pre-generated dialogue snippet from the DB."""
        row = self.conn.execute(
            """SELECT dialogue FROM npc_dialogue
               WHERE npc = ? AND used = 0
               ORDER BY RANDOM() LIMIT 1""",
            (npc,),
        ).fetchone()

        if row:
            self.conn.execute(
                "UPDATE npc_dialogue SET used = 1 WHERE dialogue = ? AND npc = ?",
                (row["dialogue"], npc),
            )
            self.conn.commit()
            return row["dialogue"][:LLM_OUTPUT_CHAR_LIMIT]

        # Last resort: use DummyBackend
        dummy = DummyBackend()
        return dummy.generate_npc_dialogue(npc, "greeting")
