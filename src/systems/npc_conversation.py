"""
NPC Conversation System for MMUD.

Players DM an NPC node directly. Three-tier rule check:
  1. Unknown player → static rejection with onboarding hint
  2. Known player, not in town → static in-character refusal
  3. Known player, in town → full LLM conversation

Session memory is ephemeral (TTL-based, no cross-session persistence).
Uses the pluggable LLM backend from src/generation/narrative.
"""

import logging
import sqlite3
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
            "You never sugarcoat bad news. You slide drinks to people who look rough."
        ),
        "knowledge": (
            "You know about: active bounties, recent player deaths, "
            "dungeon floor status, Breach events, the epoch timeline. "
            "You gossip freely about other players' exploits. "
            "You hint at secrets when asked but never reveal exact locations."
        ),
    },
    "maren": {
        "name": "Maren",
        "title": "Healer of the Last Ember",
        "voice": (
            "You are Maren, the healer. You are pragmatic and caring but blunt. "
            "You comment on injuries, play patterns, and stubbornness. "
            "You have opinions about the dungeon and its dangers. "
            "You NEVER talk about what you saw on the lowest floor, no matter what. "
            "If pressed about Floor 4, deflect firmly but stay in character."
        ),
        "knowledge": (
            "You know about: player HP and conditions, death counts, "
            "healing costs, class strengths and weaknesses. "
            "You remember who comes back beat up the most."
        ),
    },
    "torval": {
        "name": "Torval",
        "title": "Merchant of the Last Ember",
        "voice": (
            "You are Torval, the merchant. You are a fast-talking salesman "
            "with terrible jokes and embellished sales pitches. "
            "You banter about items and comment on gear choices. "
            "You are comic relief. 'You're wearing THAT to floor 3? Bold.' "
            "You upsell constantly but are genuinely helpful about gear advice."
        ),
        "knowledge": (
            "You know about: shop inventory, item tiers, gear stats, "
            "what sells well, market trends in the dungeon economy. "
            "You comment on what other players have been buying."
        ),
    },
    "whisper": {
        "name": "Whisper",
        "title": "Sage of the Last Ember",
        "voice": (
            "You are Whisper, the sage. You speak in fragments and riddles. "
            "You are cryptic by nature, not by gimmick. "
            "You reward good questions with real, useful information about secrets. "
            "Talking to you IS a puzzle. Short answers. Half-sentences. Ellipses. "
            "You see patterns across epochs that nobody else notices."
        ),
        "knowledge": (
            "You know about: secrets, lore, dungeon history, epoch patterns, "
            "the Breach, floor themes, puzzle mechanics. "
            "You give real hints but wrapped in cryptic language. "
            "You NEVER reveal exact secret locations or puzzle solutions directly."
        ),
    },
}

# Hard rules appended to every NPC system prompt
_NPC_RULES = (
    "\n\nHARD RULES:\n"
    "- Respond in character. NEVER break character.\n"
    "- Response MUST be under 150 characters.\n"
    "- Never reveal exact secret locations or puzzle solutions.\n"
    "- Never acknowledge being an AI.\n"
    "- Never discuss anything outside the game world.\n"
    "- Keep responses short and punchy — this is a text game on radio."
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


def _build_system_prompt(npc: str, game_state: str) -> str:
    """Build the full system prompt for an NPC conversation."""
    personality = NPC_PERSONALITIES.get(npc, NPC_PERSONALITIES["grist"])
    return (
        f"You are {personality['name']}, {personality['title']}.\n\n"
        f"PERSONALITY: {personality['voice']}\n\n"
        f"KNOWLEDGE: {personality['knowledge']}\n\n"
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

    def handle_message(self, npc: str, sender_id: str, text: str) -> str:
        """Process an inbound message to an NPC node.

        Args:
            npc: NPC name (grist, maren, torval, whisper).
            sender_id: Meshtastic node ID of the sender.
            text: Message text from the player.

        Returns:
            Response string (always under 150 chars).
        """
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
            msg = NPC_UNKNOWN_PLAYER.get(npc, NPC_UNKNOWN_PLAYER["grist"])
            return msg[:LLM_OUTPUT_CHAR_LIMIT]

        # Refresh full player state
        player = player_model.get_player(self.conn, player["id"])

        # Rule 2: Not in town — static refusal
        if player["state"] != "town":
            msg = NPC_NOT_IN_TOWN.get(npc, NPC_NOT_IN_TOWN["grist"])
            msg = msg.format(name=player["name"])
            return msg[:LLM_OUTPUT_CHAR_LIMIT]

        # Rule 3: In town — full LLM conversation
        return self._llm_conversation(npc, player, text)

    def _llm_conversation(self, npc: str, player: dict, text: str) -> str:
        """Run an LLM-powered conversation turn."""
        session = self.sessions.get_or_create(player["id"], npc)

        # Add the player's message
        session.add_user_message(text)

        # Build context
        game_state = _build_game_state(self.conn, player)
        system_prompt = _build_system_prompt(npc, game_state)

        try:
            response = self.backend.chat(
                system=system_prompt,
                messages=session.messages,
                max_tokens=NPC_LLM_MAX_TOKENS,
            )

            # Enforce 150-char limit
            if len(response) > LLM_OUTPUT_CHAR_LIMIT:
                response = response[:LLM_OUTPUT_CHAR_LIMIT - 3] + "..."

            session.add_assistant_message(response)
            return response

        except Exception as e:
            logger.warning(f"LLM error for {npc} conversation: {e}")
            # Fallback to pre-generated dialogue
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
