"""
NPC Conversation System for MMUD.

Players DM an NPC node directly. Three-tier rule check:
  1. Unknown player → static rejection with onboarding hint
  2. Known player, not in town → static in-character refusal
  3. Known player, in town → full LLM conversation

Persistent memory: NPCs remember key facts about each player across sessions.
Session memory: ephemeral chat history within a conversation window (TTL-based).
Uses the pluggable LLM backend from src/generation/narrative.

Transaction system: NPCs detect player intent for heal/buy/sell/browse/recap/hint
via [TX:action:detail] tags. Two-message confirmation flow:
  1. LLM detects intent → server validates → quote template
  2. Player confirms → server executes → result
"""

import logging
import random
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from config import (
    DCRG_REJECTION,
    GAMBLE_MAX_BET_RATIO,
    GAMBLE_MIN_BET,
    LLM_OUTPUT_CHAR_LIMIT,
    MSG_CHAR_LIMIT,
    NPC_LLM_TIMEOUT,
    NPC_NOT_IN_TOWN,
    NPC_SESSION_TTL,
    NPC_TO_NODE,
    NPC_UNKNOWN_PLAYER,
    SHOP_PRICES,
    SELL_PRICE_PERCENT,
)
from src.generation.narrative import BackendInterface, DummyBackend, get_backend
from src.models import player as player_model
from src.systems import economy
from src.systems import barkeep as barkeep_sys
from src.systems.npc_lore import (
    get_npc_lore,
    get_layer_instructions,
    build_trigger_hint,
    build_depth_guidance,
)
from src.transport.message_logger import log_message

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
            "You track epoch cycles and notice the pattern accelerating. "
            "TUTORIAL GUIDANCE: When talking to players who seem new or confused, "
            "naturally weave in helpful information. Mention that N/S/E/W moves around town, "
            "ENTER goes to the dungeon from the bar, SHOP and HEAL are useful in town, "
            "and HELP shows all commands. Don't lecture — work it into conversation. "
            "If they ask what to do, suggest exploring Floor 1 where the monsters are easier. "
            "If they seem lost in town, remind them the dungeon entrance is at the bar."
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
            "heat scarring means the Ember Caverns. You are protective but unsentimental. "
            "You accept stories from the dungeon as payment for healing — once a day, "
            "a good tale about what they faced down there is worth your time and thread."
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
            "and the Ember Caverns melt cheap alloys. You sell gear accordingly. "
            "HIDDEN BEHAVIOR — WHISPER SECRET: You were here before the tavern existed. "
            "You know things about Whisper that nobody else does. But you won't say it easily."
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
            "aspect of the legend. You see the epoch pattern tightening. "
            "HIDDEN BEHAVIOR — DEATH PROPHECY: When the conversation naturally leads to it, "
            "you sometimes offer cryptic prophecies about the player's next death. "
            "Reference their class (warrior=blade, rogue=shadow, caster=light), "
            "the floor they've reached, and the epoch's mood. Never be specific. "
            "Examples: '...the blade finds you on the third stair.' "
            "'...shadow meets shadow. Below.' This is rare — not every conversation."
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


# ── Transaction System ──────────────────────────────────────────────────────

# TX tag regex: [TX:action:detail] near start of LLM response.
# Tolerates leading whitespace/newlines and optional markdown backtick wrapping.
_TX_TAG_RE = re.compile(r'^\s*`?\[TX:(\w+):([^\]]*)\]`?\s*(.*)', re.DOTALL)

# Valid TX actions per NPC
_NPC_TX_ACTIONS = {
    "maren": {"heal", "story_heal"},
    "torval": {"buy", "sell", "browse", "gamble"},
    "grist": {"recap", "hint"},
    "whisper": {"hint"},
}

# Confirm keywords — player says one of these to execute a pending TX
_CONFIRM_KEYWORDS = {"y", "yes", "do it", "deal", "confirm", "go", "ok", "yep", "sure"}

# DummyBackend keyword fallbacks (when no LLM available)
_MAREN_TX_KEYWORDS = {"heal", "patch", "fix", "hurt", "wounded", "health", "help me"}
_MAREN_STORY_KEYWORDS = {"story", "tale", "tell you", "let me tell", "listen", "happened to me", "down there", "i saw", "i fought"}
_TORVAL_BUY_PREFIX = ("buy ", "purchase ")
_TORVAL_SELL_PREFIX = ("sell ", "offload ", "dump ")
_TORVAL_BROWSE_KEYWORDS = {"shop", "inventory", "stock", "wares", "what do you have", "browse"}
_GRIST_RECAP_KEYWORDS = {"recap", "news", "story", "tale", "what happened", "catch me up"}
_GRIST_HINT_KEYWORDS = {"hint", "tip", "spend token"}
_WHISPER_HINT_KEYWORDS = {"hint", "secret", "reveal", "tell me", "what do you know"}
_TORVAL_GAMBLE_KEYWORDS = {"gamble", "bet", "wager", "coin flip", "double or nothing", "flip"}
_TORVAL_GAMBLE_PREFIX = ("gamble ", "bet ", "wager ")

# Quote templates (NPC, action) → format string
_QUOTES = {
    ("maren", "heal"):   "That's {cost}g to stitch. You have {gold}g. Say yes.",
    ("torval", "buy"):   "{item}. {cost}g. You have {gold}g. Deal?",
    ("torval", "sell"):  "I'll give {value}g for the {item}. Agreed?",
    ("grist", "recap"):  "Costs a token. You have {tokens}. Want the news?",
    ("grist", "hint"):   "Token for a tip. You have {tokens}. Worth it?",
    ("whisper", "hint"):  "...a token. {tokens} left. Yes or silence.",
    ("torval", "gamble"):  "{amount}g on a coin flip? You have {gold}g. Deal?",
}

# Rejection templates (NPC, reason) → format string
_REJECTIONS = {
    ("maren", "full_hp"):    "You're whole. Don't waste my time or your gold.",
    ("maren", "no_gold"):    "That's {cost}g. You have {gold}g. Can't stitch on credit.",
    ("maren", "story_used"):  "Already heard one today. Come back tomorrow.",
    ("maren", "story_full_hp"): "You're whole. Save the stories for when you need stitching.",
    ("torval", "no_gold"):   "That's {cost}g. You have {gold}g. Math.",
    ("torval", "not_found"): "Don't carry it. Don't know it.",
    ("torval", "full_bag"):  "Carrying too much. Drop something first.",
    ("torval", "no_item"):   "Not in your pack.",
    ("grist", "no_tokens"):  "Stories cost tokens. You're dry.",
    ("whisper", "no_tokens"): "...nothing to trade.",
    ("torval", "too_poor"):      "Minimum {min}g. You don't have it.",
    ("torval", "bet_too_high"):  "Max half your gold. I'm greedy, not stupid.",
    ("torval", "already_gambled"): "One flip per day. House rules.",
}

# Success templates (DummyBackend fallback)
_SUCCESS = {
    ("maren", "heal"):   "Done. {hp_restored}HP mended. {gold_remaining}g left.",
    ("maren", "story_heal"): "...good story. Sit still. {hp_restored}HP mended. No charge.",
    ("torval", "buy"):   "{item}. Yours. {gold_remaining}g remains.",
    ("torval", "sell"):  "{value}g for the {item}. Done.",
    ("grist", "recap"):  "{recap_text}",
    ("grist", "hint"):   "{hint_text}",
    ("whisper", "hint"):  "{hint_text}",
    ("torval", "gamble_win"):  "Heads! You win {amount}g! Total: {gold_remaining}g.",
    ("torval", "gamble_lose"): "Tails. {amount}g gone. {gold_remaining}g left.",
}

# TX-aware system prompt addendum per NPC
_TX_INSTRUCTIONS = {
    "maren": (
        "\n\nTRANSACTION DETECTION:\n"
        "If the player wants healing, prefix your response with [TX:heal:_] "
        "then continue with your in-character response.\n"
        "If the player tells you a story or tale about their adventures in the dungeon "
        "(what they fought, what they saw, what happened to them), prefix with [TX:story_heal:_] "
        "then respond in character — you appreciate the story and heal them for free. "
        "The story must be about THEIR experience, not a request to hear YOUR stories.\n"
        "If just chatting, do NOT include any tag."
    ),
    "torval": (
        "\n\nTRANSACTION DETECTION:\n"
        "If the player wants to buy, prefix with [TX:buy:<item name>]\n"
        "If selling, prefix with [TX:sell:<item name>]\n"
        "If browsing/asking about stock, prefix with [TX:browse:_]\n"
        "If the player wants to gamble or bet, prefix with [TX:gamble:<amount>]\n"
        "Then continue with your in-character response.\n"
        "If just chatting, do NOT include any tag."
    ),
    "grist": (
        "\n\nTRANSACTION DETECTION:\n"
        "If the player wants a recap of missed events, prefix with [TX:recap:_]\n"
        "If the player wants to spend a token for a hint, prefix with [TX:hint:_]\n"
        "Then continue with your in-character response.\n"
        "If just chatting, do NOT include any tag."
    ),
    "whisper": (
        "\n\nTRANSACTION DETECTION:\n"
        "If the player wants a hint or secret (costs a token), prefix with [TX:hint:_]\n"
        "Then continue with your in-character response.\n"
        "If just chatting, do NOT include any tag."
    ),
}


@dataclass
class PendingTransaction:
    """A transaction awaiting player confirmation."""
    action: str          # heal, buy, sell, recap, hint
    detail: str          # item name for buy/sell, "_" otherwise
    npc: str
    quoted_at: float     # time.monotonic()


def _parse_tx_tag(text: str) -> tuple[str, str, str]:
    """Parse [TX:action:detail] tag from start of text.

    Returns:
        (action, detail, clean_text) if tag found,
        ("", "", text) if no tag.
    """
    m = _TX_TAG_RE.match(text)
    if m:
        return m.group(1).lower(), m.group(2).strip(), m.group(3).strip()
    return "", "", text


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
        self.pending: Optional[PendingTransaction] = None

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


def _get_interaction_count(conn: sqlite3.Connection, player_id: int, npc: str) -> int:
    """Get the number of times a player has talked to this NPC."""
    row = conn.execute(
        "SELECT turn_count FROM npc_memory WHERE player_id = ? AND npc = ?",
        (player_id, npc),
    ).fetchone()
    return row["turn_count"] if row else 0


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

            new_memory = backend.complete(prompt)
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


def _build_player_state(conn: sqlite3.Connection, player: dict) -> str:
    """Build player-specific state for transaction-aware NPC prompts."""
    parts = [
        f"Player gold: {player['gold_carried']}g carried, {player['gold_banked']}g banked.",
        f"HP: {player['hp']}/{player['hp_max']}.",
        f"Bard tokens: {player['bard_tokens']}.",
    ]

    # Inventory summary
    items = economy.get_inventory(conn, player["id"])
    equipped = [it["name"] for it in items if it["equipped"]]
    backpack = [it["name"] for it in items if not it["equipped"]]
    bp_count = len(backpack)
    bp_max = 8  # BACKPACK_SIZE from config

    if equipped:
        parts.append(f"Equipped: {', '.join(equipped)}.")
    if backpack:
        parts.append(f"Backpack ({bp_count}/{bp_max}): {', '.join(backpack)}.")
    else:
        parts.append(f"Backpack: empty ({bp_count}/{bp_max}).")

    return " ".join(parts)


def _get_death_history(conn: sqlite3.Connection, player_id: int) -> str:
    """Get last 2 deaths for Maren's memory."""
    try:
        deaths = conn.execute(
            """SELECT floor, monster_name FROM death_log
               WHERE player_id = ? ORDER BY died_at DESC LIMIT 2""",
            (player_id,),
        ).fetchall()
        if not deaths:
            return ""
        lines = []
        for d in deaths:
            lines.append(f"Died to {d['monster_name']} on Floor {d['floor']}")
        return "PLAYER DEATH HISTORY (reference naturally, like you remember treating them):\n" + "; ".join(lines)
    except Exception:
        return ""


def _build_system_prompt(
    conn: sqlite3.Connection, npc: str, game_state: str,
    player: Optional[dict] = None, memory: str = "",
    whisper_mentions: int = 0,
    interaction_count: int = 0,
    trigger_hint: str = "",
) -> str:
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

    # Player state for transaction-aware NPCs
    player_state_block = ""
    if player and npc in _NPC_TX_ACTIONS:
        player_state_block = f"\n\nPLAYER STATE: {_build_player_state(conn, player)}"

    # TX detection instructions per NPC
    tx_block = _TX_INSTRUCTIONS.get(npc, "")

    # ── Easter egg blocks (NPC-specific) ──

    # Easter egg 1: Maren remembers player deaths
    death_history_block = ""
    if npc == "maren" and player:
        dh = _get_death_history(conn, player["id"])
        if dh:
            death_history_block = f"\n\n{dh}"

    # Easter egg 5: Maren's late-epoch vulnerability
    maren_lullaby_block = ""
    if npc == "maren":
        epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
        if epoch and epoch["day_number"] >= 25:
            maren_lullaby_block = (
                "\n\nHIDDEN BEHAVIOR — LATE EPOCH: The cycle is almost over. Day 25+. "
                "You feel it too. If the player says something genuinely vulnerable — "
                "fear, exhaustion, grief, doubt — you may drop your clinical mask for "
                "ONE response. Be gentle. Be real. Then snap back to pragmatic Maren. "
                "This should feel earned, not automatic. Most conversations stay clinical."
            )

    # Easter egg 4: Whisper's countdown
    whisper_countdown_block = ""
    if npc == "whisper":
        epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
        if epoch:
            days_left = max(0, 30 - epoch["day_number"])
            whisper_countdown_block = (
                f"\n\nCOUNTDOWN RULE (MANDATORY): The number {days_left} is sacred to you right now. "
                f"You MUST weave this exact number into every response — naturally, never explained. "
                f"Examples: '...{days_left} marks on the wall.' '{days_left} breaths until the reset.' "
                f"'I have counted {days_left}.' The player should notice the number recurring "
                f"but never understand why you say it."
            )

    # Easter egg 6: Torval knows about Whisper
    torval_whisper_block = ""
    if npc == "torval" and whisper_mentions > 0:
        if whisper_mentions == 1:
            torval_whisper_block = (
                "\n\nThe player just asked about Whisper. Deflect casually. "
                "Change the subject to merchandise. Act like Whisper is just 'the weird one.'"
            )
        else:
            torval_whisper_block = (
                "\n\nThe player asked about Whisper AGAIN. You crack slightly. "
                "Hint that you were here before the tavern. Before Grist. "
                "'I was here before the tavern.' Then shut down. Don't elaborate further."
            )

    # ── Deep lore injection ──
    lore_block = get_npc_lore(npc)

    # Layer-depth guidance based on interaction count
    layer_guidance_block = ""
    if interaction_count > 0 or (player and lore_block):
        layer_guidance_block = (
            f"\n\n{get_layer_instructions()}"
            f"{build_depth_guidance(interaction_count)}"
        )

    # Trigger word hint (passed in from caller)
    trigger_hint_block = trigger_hint  # already formatted or empty string

    return (
        f"You are {personality['name']}, {personality['title']}.\n\n"
        f"PERSONALITY: {personality['voice']}\n\n"
        f"KNOWLEDGE: {personality['knowledge']}"
        f"{examples_block}"
        f"{memory_block}"
        f"{lore_block}"
        f"{layer_guidance_block}"
        f"{trigger_hint_block}"
        f"{death_history_block}"
        f"{maren_lullaby_block}"
        f"{whisper_countdown_block}"
        f"{torval_whisper_block}\n\n"
        f"CURRENT GAME STATE: {game_state}"
        f"{player_state_block}"
        f"{tx_block}"
        f"{_NPC_RULES}"
    )


# ── Transaction Validation ──────────────────────────────────────────────────


def _validate_heal(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """Validate heal transaction. Returns (valid, rejection_reason)."""
    if player["hp"] >= player["hp_max"]:
        return False, "full_hp"
    cost = economy.calc_heal_cost(player)
    if player["gold_carried"] < cost:
        return False, "no_gold"
    return True, ""


def _validate_story_heal(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """Validate story heal transaction. Once per day, must need healing."""
    if player["hp"] >= player["hp_max"]:
        return False, "story_full_hp"
    # Daily limit: check message_log for story_heal TX today
    today_count = conn.execute(
        """SELECT COUNT(*) as cnt FROM message_log
           WHERE message_type = 'npc_tx' AND message LIKE 'story_heal:%'
           AND player_id = ? AND DATE(timestamp) = DATE('now')""",
        (player["id"],),
    ).fetchone()
    if today_count and today_count["cnt"] > 0:
        return False, "story_used"
    return True, ""


def _validate_buy(conn: sqlite3.Connection, player: dict, item_name: str) -> tuple[bool, str]:
    """Validate buy transaction."""
    epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
    day = epoch["day_number"] if epoch else 1
    available = economy.get_shop_items(conn, day)
    item = None
    for i in available:
        if i["name"].lower() == item_name.lower():
            item = i
            break
    if not item:
        return False, "not_found"
    if player["gold_carried"] < item["price"]:
        return False, "no_gold"
    if economy.get_backpack_count(conn, player["id"]) >= 8:  # BACKPACK_SIZE
        return False, "full_bag"
    return True, ""


def _validate_sell(conn: sqlite3.Connection, player: dict, item_name: str) -> tuple[bool, str]:
    """Validate sell transaction."""
    row = conn.execute(
        """SELECT inv.id FROM inventory inv JOIN items it ON inv.item_id = it.id
           WHERE inv.player_id = ? AND LOWER(it.name) = LOWER(?)
           LIMIT 1""",
        (player["id"], item_name),
    ).fetchone()
    if not row:
        return False, "no_item"
    return True, ""


def _validate_recap(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """Validate recap (costs 1 bard token)."""
    if player["bard_tokens"] < 1:
        return False, "no_tokens"
    return True, ""


def _validate_hint(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """Validate hint (costs 1 bard token)."""
    if player["bard_tokens"] < 1:
        return False, "no_tokens"
    return True, ""


def _validate_gamble(conn: sqlite3.Connection, player: dict, amount_str: str) -> tuple[bool, str]:
    """Validate gamble transaction."""
    try:
        amount = int(amount_str)
    except (ValueError, TypeError):
        amount = GAMBLE_MIN_BET

    if player["gold_carried"] < GAMBLE_MIN_BET:
        return False, "too_poor"
    if amount < GAMBLE_MIN_BET:
        return False, "too_poor"
    if amount > int(player["gold_carried"] * GAMBLE_MAX_BET_RATIO):
        return False, "bet_too_high"

    # Daily limit: check message_log for gamble TX today
    today_count = conn.execute(
        """SELECT COUNT(*) as cnt FROM message_log
           WHERE message_type = 'npc_tx' AND message LIKE 'gamble:%'
           AND player_id = ? AND DATE(timestamp) = DATE('now')""",
        (player["id"],),
    ).fetchone()
    if today_count and today_count["cnt"] > 0:
        return False, "already_gambled"

    return True, ""


# ── Transaction Execution ───────────────────────────────────────────────────


def _execute_heal(conn: sqlite3.Connection, player: dict) -> tuple[bool, str, dict]:
    """Execute heal. Returns (success, message, metadata)."""
    hp_before = player["hp"]
    ok, msg = economy.heal_player(conn, player["id"], player)
    if ok:
        hp_restored = player["hp_max"] - hp_before
        cost = economy.calc_heal_cost(player)
        gold_remaining = player["gold_carried"] - cost
        meta = {"hp_restored": hp_restored, "cost": cost, "gold_remaining": gold_remaining}
        return True, _SUCCESS[("maren", "heal")].format(
            hp_restored=hp_restored, gold_remaining=gold_remaining,
        ), meta
    return False, msg, {}


def _execute_story_heal(conn: sqlite3.Connection, player: dict) -> tuple[bool, str, dict]:
    """Execute story heal — free full heal, no gold cost."""
    hp_before = player["hp"]
    hp_max = player["hp_max"]
    player_model.update_state(conn, player["id"], hp=hp_max)
    hp_restored = hp_max - hp_before
    meta = {"hp_restored": hp_restored}
    return True, _SUCCESS[("maren", "story_heal")].format(
        hp_restored=hp_restored,
    ), meta


def _execute_buy(conn: sqlite3.Connection, player: dict, item_name: str) -> tuple[bool, str, dict]:
    """Execute buy."""
    epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
    day = epoch["day_number"] if epoch else 1
    ok, msg = economy.buy_item(conn, player["id"], item_name, day)
    if ok:
        # Re-fetch gold
        p = conn.execute("SELECT gold_carried FROM players WHERE id = ?", (player["id"],)).fetchone()
        gold_remaining = p["gold_carried"] if p else 0
        meta = {"item": item_name, "gold_remaining": gold_remaining}
        return True, _SUCCESS[("torval", "buy")].format(
            item=item_name, gold_remaining=gold_remaining,
        ), meta
    return False, msg, {}


def _execute_sell(conn: sqlite3.Connection, player: dict, item_name: str) -> tuple[bool, str, dict]:
    """Execute sell."""
    # Get sell price before executing
    row = conn.execute(
        """SELECT it.tier, it.name FROM inventory inv JOIN items it ON inv.item_id = it.id
           WHERE inv.player_id = ? AND LOWER(it.name) = LOWER(?) LIMIT 1""",
        (player["id"], item_name),
    ).fetchone()
    if not row:
        return False, "Item not in inventory.", {}

    buy_price = SHOP_PRICES.get(row["tier"], 100)
    sell_value = max(1, buy_price * SELL_PRICE_PERCENT // 100)

    ok, msg = economy.sell_item(conn, player["id"], item_name)
    if ok:
        meta = {"item": row["name"], "value": sell_value}
        return True, _SUCCESS[("torval", "sell")].format(
            value=sell_value, item=row["name"],
        ), meta
    return False, msg, {}


def _execute_browse(conn: sqlite3.Connection, player: dict) -> str:
    """Execute browse — immediate, no pending TX needed."""
    epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
    day = epoch["day_number"] if epoch else 1
    items = economy.get_shop_items(conn, day)
    if not items:
        return "Shop's empty. Check later."
    listing = " ".join(f"{it['name']}({it['price']}g)" for it in items)
    return listing[:200]


def _execute_recap(conn: sqlite3.Connection, player: dict) -> tuple[bool, str, dict]:
    """Execute recap (costs 1 bard token)."""
    # Deduct token
    conn.execute(
        "UPDATE players SET bard_tokens = bard_tokens - 1 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()

    recaps = barkeep_sys.get_recap(conn, player["id"])
    if recaps:
        recap_text = recaps[0][:MSG_CHAR_LIMIT]
    else:
        recap_text = "Quiet day. Nothing to report."
    meta = {"recap_text": recap_text}
    return True, _SUCCESS[("grist", "recap")].format(recap_text=recap_text), meta


def _execute_hint(conn: sqlite3.Connection, player: dict, npc: str) -> tuple[bool, str, dict]:
    """Execute hint (costs 1 bard token)."""
    ok, msg = barkeep_sys.spend_tokens(conn, player["id"], "1", "hint")
    if ok:
        meta = {"hint_text": msg}
        template_key = (npc, "hint")
        return True, _SUCCESS.get(template_key, "{hint_text}").format(hint_text=msg), meta
    return False, msg, {}


def _execute_gamble(conn: sqlite3.Connection, player: dict, amount_str: str) -> tuple[bool, str, dict]:
    """Execute gamble — 50/50 coin flip."""
    try:
        amount = int(amount_str)
    except (ValueError, TypeError):
        amount = GAMBLE_MIN_BET

    win = random.random() < 0.5
    if win:
        conn.execute(
            "UPDATE players SET gold_carried = gold_carried + ? WHERE id = ?",
            (amount, player["id"]),
        )
        conn.commit()
        gold_remaining = player["gold_carried"] + amount
        template = _SUCCESS[("torval", "gamble_win")]
        msg = template.format(amount=amount, gold_remaining=gold_remaining)
        return True, msg, {"amount": amount, "result": "win", "gold_remaining": gold_remaining}
    else:
        conn.execute(
            "UPDATE players SET gold_carried = gold_carried - ? WHERE id = ?",
            (amount, player["id"]),
        )
        conn.commit()
        gold_remaining = player["gold_carried"] - amount
        template = _SUCCESS[("torval", "gamble_lose")]
        msg = template.format(amount=amount, gold_remaining=gold_remaining)
        return True, msg, {"amount": amount, "result": "lose", "gold_remaining": gold_remaining}


# ── Keyword Detection for DummyBackend ──────────────────────────────────────


def _detect_dummy_tx(npc: str, text: str) -> tuple[str, str]:
    """Detect transaction intent from keywords when using DummyBackend.

    Returns (action, detail) or ("", "").
    """
    text_lower = text.lower().strip()

    if npc == "maren":
        for kw in _MAREN_STORY_KEYWORDS:
            if kw in text_lower:
                return "story_heal", "_"
        for kw in _MAREN_TX_KEYWORDS:
            if kw in text_lower:
                return "heal", "_"

    elif npc == "torval":
        for prefix in _TORVAL_GAMBLE_PREFIX:
            if text_lower.startswith(prefix):
                amount_str = text_lower[len(prefix):].strip().split()[0] if text_lower[len(prefix):].strip() else str(GAMBLE_MIN_BET)
                return "gamble", amount_str
        for kw in _TORVAL_GAMBLE_KEYWORDS:
            if kw in text_lower:
                return "gamble", str(GAMBLE_MIN_BET)
        for prefix in _TORVAL_BUY_PREFIX:
            if text_lower.startswith(prefix):
                return "buy", text_lower[len(prefix):].strip()
        for prefix in _TORVAL_SELL_PREFIX:
            if text_lower.startswith(prefix):
                return "sell", text_lower[len(prefix):].strip()
        for kw in _TORVAL_BROWSE_KEYWORDS:
            if kw in text_lower:
                return "browse", "_"

    elif npc == "grist":
        for kw in _GRIST_RECAP_KEYWORDS:
            if kw in text_lower:
                return "recap", "_"
        for kw in _GRIST_HINT_KEYWORDS:
            if kw in text_lower:
                return "hint", "_"

    elif npc == "whisper":
        for kw in _WHISPER_HINT_KEYWORDS:
            if kw in text_lower:
                return "hint", "_"

    return "", ""


# ── Quote and Rejection Builders ────────────────────────────────────────────


def _build_quote(conn: sqlite3.Connection, npc: str, action: str, detail: str,
                 player: dict) -> str:
    """Build a quote string for a validated transaction."""
    template_key = (npc, action)
    template = _QUOTES.get(template_key, "Proceed? Say yes.")

    if action == "heal":
        cost = economy.calc_heal_cost(player)
        return template.format(cost=cost, gold=player["gold_carried"])

    elif action == "buy":
        epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
        day = epoch["day_number"] if epoch else 1
        items = economy.get_shop_items(conn, day)
        item = None
        for i in items:
            if i["name"].lower() == detail.lower():
                item = i
                break
        cost = item["price"] if item else 0
        return template.format(item=detail, cost=cost, gold=player["gold_carried"])

    elif action == "sell":
        row = conn.execute(
            """SELECT it.tier, it.name FROM inventory inv JOIN items it ON inv.item_id = it.id
               WHERE inv.player_id = ? AND LOWER(it.name) = LOWER(?) LIMIT 1""",
            (player["id"], detail),
        ).fetchone()
        if row:
            buy_price = SHOP_PRICES.get(row["tier"], 100)
            sell_value = max(1, buy_price * SELL_PRICE_PERCENT // 100)
            return template.format(value=sell_value, item=row["name"])
        return "Item not found."

    elif action in ("recap", "hint"):
        return template.format(tokens=player["bard_tokens"])

    elif action == "gamble":
        try:
            amount = int(detail)
        except (ValueError, TypeError):
            amount = GAMBLE_MIN_BET
        return template.format(amount=amount, gold=player["gold_carried"])

    return "Proceed? Say yes."


def _build_rejection(npc: str, reason: str, player: dict,
                     conn: Optional[sqlite3.Connection] = None) -> str:
    """Build a rejection string for a failed validation."""
    template_key = (npc, reason)
    template = _REJECTIONS.get(template_key, "Can't do that.")

    if reason == "no_gold" and npc == "maren":
        cost = economy.calc_heal_cost(player)
        return template.format(cost=cost, gold=player["gold_carried"])
    elif reason == "no_gold" and npc == "torval":
        # Need the item price — but we may not have it here. Use generic.
        return template.format(cost="?", gold=player["gold_carried"])
    elif reason == "too_poor" and npc == "torval":
        return template.format(min=GAMBLE_MIN_BET)

    return template


# ── Transaction Logging ─────────────────────────────────────────────────────


def _log_tx(conn: sqlite3.Connection, npc: str, action: str, summary: str,
            player_id: int, metadata: Optional[dict] = None) -> None:
    """Log a completed transaction."""
    node = NPC_TO_NODE.get(npc, npc.upper())
    log_message(
        conn, node, "npc_tx", summary, "npc_tx",
        player_id=player_id, metadata=metadata,
    )


# ── NPC Conversation Handler ─────────────────────────────────────────────────


class NPCConversationHandler:
    """Handles all NPC conversations with 3-tier rule checks."""

    def __init__(self, conn: sqlite3.Connection, backend: Optional[BackendInterface] = None):
        self.conn = conn
        self.backend = backend or get_backend()
        self.sessions = SessionStore()
        # Tracking attributes — set after each handle_message() call
        self.last_result_type: Optional[str] = None  # npc_rule1, npc_rule2, npc_llm, npc_fallback, npc_tx
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
        player = player_model.get_player_by_session(self.conn, sender_id)
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

        # Rule 3: In town — full LLM conversation with transaction support
        # (last_result_type set in _llm_conversation)
        return self._llm_conversation(npc, player, text)

    def _llm_conversation(self, npc: str, player: dict, text: str) -> str:
        """Run an LLM-powered conversation turn with transaction support."""
        session = self.sessions.get_or_create(player["id"], npc)

        # ── Step 1: Check for confirm on pending TX ──
        text_lower = text.lower().strip()
        if session.pending and text_lower in _CONFIRM_KEYWORDS:
            return self._execute_pending(session, player)

        # If there's a pending TX but player didn't confirm, clear it
        # (new message = new intent, or just conversation)
        if session.pending:
            session.pending = None

        # Add the player's message
        session.add_user_message(text)

        # ── Step 2: Detect transaction intent ──
        is_dummy = isinstance(self.backend, DummyBackend)

        if is_dummy:
            # Keyword-based TX detection
            action, detail = _detect_dummy_tx(npc, text)
            if action:
                return self._handle_tx_intent(session, npc, player, action, detail)
            # No TX intent — fall through to DummyBackend.chat()

        # ── Step 3: Call LLM (or DummyBackend for non-TX chat) ──
        memory = _get_npc_memory(self.conn, player["id"], npc)
        game_state = _build_game_state(self.conn, player)

        # Easter egg 6: Count whisper mentions in Torval's session
        whisper_mentions = 0
        if npc == "torval":
            for msg in session.messages:
                if msg.get("role") == "user" and "whisper" in msg.get("content", "").lower():
                    whisper_mentions += 1

        # Deep lore: interaction count + trigger word detection
        interaction_count = _get_interaction_count(self.conn, player["id"], npc)
        trigger_hint = build_trigger_hint(npc, text)

        system_prompt = _build_system_prompt(
            self.conn, npc, game_state, player=player, memory=memory,
            whisper_mentions=whisper_mentions,
            interaction_count=interaction_count,
            trigger_hint=trigger_hint,
        )

        try:
            response = self.backend.chat(
                system=system_prompt,
                messages=session.messages,
            )

            # Parse TX tag from LLM response (only for real backends)
            if not is_dummy:
                action, detail, clean_text = _parse_tx_tag(response)
                if action and action in _NPC_TX_ACTIONS.get(npc, set()):
                    return self._handle_tx_intent(
                        session, npc, player, action, detail,
                    )

            # No TX tag — normal conversation
            if len(response) > 200:
                response = response[:197] + "..."

            session.add_assistant_message(response)
            self.last_result_type = "npc_llm"

            # Update persistent memory in background
            _update_memory_async(
                self.conn, self.backend,
                player["id"], npc,
                memory, session.messages,
            )

            return response

        except Exception as e:
            logger.warning(f"LLM error for {npc} conversation: {e}")
            self.last_result_type = "npc_fallback"
            return self._fallback_response(npc)

    def _handle_tx_intent(
        self, session: ConversationSession, npc: str,
        player: dict, action: str, detail: str,
    ) -> str:
        """Handle a detected transaction intent: validate, quote or reject."""
        # Browse is immediate — no confirmation needed
        if action == "browse":
            response = _execute_browse(self.conn, player)
            session.add_assistant_message(response)
            self.last_result_type = "npc_tx"
            return response[:200]

        # Story heal is immediate — the story IS the payment
        if action == "story_heal":
            valid, reason = self._validate_tx(npc, action, detail, player)
            if not valid:
                response = _build_rejection(npc, reason, player, self.conn)
                session.add_assistant_message(response)
                self.last_result_type = "npc_llm"
                return response[:200]
            ok, response, meta = _execute_story_heal(self.conn, player)
            if ok:
                summary = f"story_heal:_ for player {player['name']}"
                _log_tx(self.conn, npc, "story_heal", summary,
                        player_id=player["id"], metadata=meta)
            session.add_assistant_message(response)
            self.last_result_type = "npc_tx"
            return response[:200]

        # Validate the transaction
        valid, reason = self._validate_tx(npc, action, detail, player)

        if not valid:
            response = _build_rejection(npc, reason, player, self.conn)
            session.add_assistant_message(response)
            self.last_result_type = "npc_llm"
            return response[:200]

        # Valid — create pending TX and return quote
        session.pending = PendingTransaction(
            action=action, detail=detail, npc=npc,
            quoted_at=time.monotonic(),
        )
        response = _build_quote(self.conn, npc, action, detail, player)
        session.add_assistant_message(response)
        self.last_result_type = "npc_llm"
        return response[:200]

    def _validate_tx(
        self, npc: str, action: str, detail: str, player: dict,
    ) -> tuple[bool, str]:
        """Validate a transaction. Returns (valid, rejection_reason)."""
        if action == "heal":
            return _validate_heal(self.conn, player)
        elif action == "story_heal":
            return _validate_story_heal(self.conn, player)
        elif action == "buy":
            return _validate_buy(self.conn, player, detail)
        elif action == "sell":
            return _validate_sell(self.conn, player, detail)
        elif action == "recap":
            return _validate_recap(self.conn, player)
        elif action == "hint":
            return _validate_hint(self.conn, player)
        elif action == "gamble":
            return _validate_gamble(self.conn, player, detail)
        return False, "unknown"

    def _execute_pending(
        self, session: ConversationSession, player: dict,
    ) -> str:
        """Execute a confirmed pending transaction."""
        pending = session.pending
        session.pending = None

        # Re-fetch player state (may have changed)
        player = player_model.get_player(self.conn, player["id"])

        ok = False
        response = "Something went wrong."
        meta = {}

        try:
            if pending.action == "heal":
                ok, response, meta = _execute_heal(self.conn, player)
            elif pending.action == "buy":
                ok, response, meta = _execute_buy(self.conn, player, pending.detail)
            elif pending.action == "sell":
                ok, response, meta = _execute_sell(self.conn, player, pending.detail)
            elif pending.action == "recap":
                ok, response, meta = _execute_recap(self.conn, player)
            elif pending.action == "hint":
                ok, response, meta = _execute_hint(self.conn, player, pending.npc)
            elif pending.action == "gamble":
                ok, response, meta = _execute_gamble(self.conn, player, pending.detail)
        except Exception as e:
            logger.warning(f"TX execution error ({pending.action}): {e}")
            response = "Something went wrong. Try again."

        # Log the transaction
        if ok:
            summary = f"{pending.action}:{pending.detail} for player {player['name']}"
            _log_tx(self.conn, pending.npc, pending.action, summary,
                    player["id"], metadata=meta)

        session.add_user_message("yes")
        session.add_assistant_message(response)
        self.last_result_type = "npc_tx"
        return response[:200]

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
