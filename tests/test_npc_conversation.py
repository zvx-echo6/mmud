"""Tests for NPC conversation system: 3-tier rules, session memory, LLM fallback."""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import LLM_OUTPUT_CHAR_LIMIT, NPC_NOT_IN_TOWN, NPC_UNKNOWN_PLAYER, DCRG_REJECTION
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.systems.npc_conversation import (
    ConversationSession,
    NPCConversationHandler,
    NPC_PERSONALITIES,
    SessionStore,
    _build_game_state,
    _build_system_prompt,
)


def make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 5)"""
    )
    # Player in town
    conn.execute(
        "INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!town01', 'Kira')"
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, level, gold_carried, last_login)
           VALUES (1, 1, 'Kira', 'warrior', 18, 20, 3, 2, 1, 'town', 3, 150,
                   '2026-01-05T00:00:00')"""
    )
    # Player in dungeon
    conn.execute(
        "INSERT INTO accounts (id, mesh_id, handle) VALUES (2, '!dung01', 'Rex')"
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, level, gold_carried, last_login)
           VALUES (2, 2, 'Rex', 'rogue', 10, 20, 2, 1, 3, 'dungeon', 5, 50,
                   '2026-01-05T00:00:00')"""
    )
    # An active bounty
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short)
           VALUES (1, 1, 'Test Room', 'A test room.', 'Test.')"""
    )
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (1, 1, 'Giant Rat', 50, 50, 3, 1, 1, 10, 5, 10, 1)"""
    )
    conn.execute(
        """INSERT INTO bounties (id, type, description, target_monster_id, target_value,
           floor_min, floor_max, phase, available_from_day, active)
           VALUES (1, 'kill', 'Slay the Giant Rat on Floor 1.', 1, 50, 1, 1,
                   'early', 1, 1)"""
    )
    # A recent death broadcast
    conn.execute(
        "INSERT INTO broadcasts (tier, message) VALUES (1, 'X Hero fell on Floor 2.')"
    )
    # Pre-generated NPC dialogue for fallback
    conn.execute(
        "INSERT INTO npc_dialogue (npc, context, dialogue) VALUES ('grist', 'greeting', 'Grist slides a drink across the bar.')"
    )
    conn.commit()


# ── Rule 1: Unknown player → static rejection ──


def test_unknown_player_grist():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("grist", "!unknown99", "Hello")
    assert result == NPC_UNKNOWN_PLAYER["grist"]


def test_unknown_player_maren():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("maren", "!unknown99", "Help me")
    assert result == NPC_UNKNOWN_PLAYER["maren"]


def test_unknown_player_torval():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("torval", "!unknown99", "Buy sword")
    assert result == NPC_UNKNOWN_PLAYER["torval"]


def test_unknown_player_whisper():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("whisper", "!unknown99", "Tell me")
    assert result == NPC_UNKNOWN_PLAYER["whisper"]


# ── Rule 2: Known player, not in town → static refusal ──


def test_not_in_town_grist():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("grist", "!dung01", "Hello")
    expected = NPC_NOT_IN_TOWN["grist"].format(name="Rex")
    assert result == expected


def test_not_in_town_maren():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("maren", "!dung01", "Heal me")
    expected = NPC_NOT_IN_TOWN["maren"].format(name="Rex")
    assert result == expected


def test_not_in_town_torval():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("torval", "!dung01", "Shop")
    expected = NPC_NOT_IN_TOWN["torval"].format(name="Rex")
    assert result == expected


def test_not_in_town_whisper():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("whisper", "!dung01", "Secrets")
    expected = NPC_NOT_IN_TOWN["whisper"].format(name="Rex")
    assert result == expected


# ── DCRG rejection ──


def test_dcrg_rejection():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("dcrg", "!town01", "Hello")
    assert result == DCRG_REJECTION[:LLM_OUTPUT_CHAR_LIMIT]


def test_invalid_npc_name():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("nobody", "!town01", "Hello")
    assert result == DCRG_REJECTION[:LLM_OUTPUT_CHAR_LIMIT]


# ── Rule 3: Known player, in town → LLM conversation ──


def test_town_player_gets_llm_response():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("grist", "!town01", "What's happening?")
    assert result is not None
    assert len(result) > 0
    assert len(result) <= LLM_OUTPUT_CHAR_LIMIT


def test_town_player_all_npcs():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    for npc in ("grist", "maren", "torval", "whisper"):
        result = handler.handle_message(npc, "!town01", "Hello")
        assert result is not None
        assert len(result) > 0
        assert len(result) <= LLM_OUTPUT_CHAR_LIMIT


def test_response_under_200_chars():
    conn = make_test_db()
    # Mock a backend that returns a long string — NPC DMs cap at 200 chars
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "x" * 300
    handler = NPCConversationHandler(conn, mock_backend)
    result = handler.handle_message("grist", "!town01", "Tell me everything")
    assert len(result) <= 200


# ── Session Memory ──


def test_session_stores_messages():
    conn = make_test_db()
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "Grist nods."
    handler = NPCConversationHandler(conn, mock_backend)

    handler.handle_message("grist", "!town01", "First message")
    handler.handle_message("grist", "!town01", "Second message")

    session = handler.sessions.get(1, "grist")
    assert session is not None
    # 2 user + 2 assistant = 4 messages
    assert len(session.messages) == 4
    assert session.messages[0] == {"role": "user", "content": "First message"}
    assert session.messages[1] == {"role": "assistant", "content": "Grist nods."}
    assert session.messages[2] == {"role": "user", "content": "Second message"}


def test_session_passes_history_to_backend():
    conn = make_test_db()
    call_log = []

    class TrackingBackend:
        def chat(self, system, messages, max_tokens=80):
            # Snapshot messages at call time (list is mutated after)
            call_log.append([dict(m) for m in messages])
            return "Response"

        def complete(self, prompt, max_tokens=200):
            return "Response"

    handler = NPCConversationHandler(conn, TrackingBackend())

    handler.handle_message("grist", "!town01", "Hello")
    handler.handle_message("grist", "!town01", "Tell me more")

    # First call: just the user message
    assert len(call_log[0]) == 1
    # Second call: user + assistant + user = 3
    assert len(call_log[1]) == 3


def test_session_expiry():
    session = ConversationSession(1, "grist", ttl=0)
    time.sleep(0.01)
    assert session.is_expired()


def test_session_not_expired():
    session = ConversationSession(1, "grist", ttl=300)
    assert not session.is_expired()


# ── SessionStore ──


def test_store_get_or_create():
    store = SessionStore()
    s1 = store.get_or_create(1, "grist")
    s2 = store.get_or_create(1, "grist")
    assert s1 is s2  # Same session


def test_store_different_npcs():
    store = SessionStore()
    s1 = store.get_or_create(1, "grist")
    s2 = store.get_or_create(1, "maren")
    assert s1 is not s2


def test_store_cleanup():
    store = SessionStore()
    store.create(1, "grist")
    # Force expiry
    store._sessions[(1, "grist")].ttl = 0
    time.sleep(0.01)
    removed = store.cleanup()
    assert removed == 1
    assert store.get(1, "grist") is None


def test_store_expired_session_returns_none():
    store = SessionStore()
    session = store.create(1, "grist")
    session.ttl = 0
    time.sleep(0.01)
    assert store.get(1, "grist") is None


# ── Game State Injection ──


def test_game_state_includes_epoch():
    conn = make_test_db()
    player = dict(conn.execute("SELECT * FROM players WHERE id = 1").fetchone())
    state = _build_game_state(conn, player)
    assert "Epoch 1" in state
    assert "Day 5" in state


def test_game_state_includes_player():
    conn = make_test_db()
    player = dict(conn.execute("SELECT * FROM players WHERE id = 1").fetchone())
    state = _build_game_state(conn, player)
    assert "Kira" in state
    assert "Warrior" in state


def test_game_state_includes_bounties():
    conn = make_test_db()
    player = dict(conn.execute("SELECT * FROM players WHERE id = 1").fetchone())
    state = _build_game_state(conn, player)
    assert "bounties" in state.lower() or "Giant Rat" in state


def test_game_state_includes_deaths():
    conn = make_test_db()
    player = dict(conn.execute("SELECT * FROM players WHERE id = 1").fetchone())
    state = _build_game_state(conn, player)
    assert "Hero" in state or "death" in state.lower()


# ── System Prompt Building ──


def test_system_prompt_contains_personality():
    conn = make_test_db()
    state = "Epoch 1, Day 5."
    prompt = _build_system_prompt(conn, "grist", state)
    assert "Grist" in prompt
    assert "barkeep" in prompt.lower()
    assert "EXAMPLE RESPONSES" in prompt


def test_system_prompt_contains_rules():
    conn = make_test_db()
    state = "Epoch 1, Day 5."
    prompt = _build_system_prompt(conn, "whisper", state)
    assert "100-200 characters" in prompt
    assert "NEVER break character" in prompt


def test_system_prompt_contains_game_state():
    conn = make_test_db()
    state = "Epoch 1, Day 5. Breach: sealed."
    prompt = _build_system_prompt(conn, "maren", state)
    assert "Epoch 1" in prompt
    assert "sealed" in prompt


# ── NPC Personality Cards ──


def test_all_npcs_have_personalities():
    for npc in ("grist", "maren", "torval", "whisper"):
        assert npc in NPC_PERSONALITIES
        card = NPC_PERSONALITIES[npc]
        assert "name" in card
        assert "title" in card
        assert "voice" in card
        assert "knowledge" in card
        assert "example_lines" in card
        assert len(card["example_lines"]) >= 2


# ── Fallback on LLM Error ──


def test_fallback_on_llm_error():
    conn = make_test_db()
    mock_backend = MagicMock()
    mock_backend.chat.side_effect = Exception("API timeout")
    handler = NPCConversationHandler(conn, mock_backend)
    result = handler.handle_message("grist", "!town01", "Hello")
    assert result is not None
    assert len(result) > 0
    assert len(result) <= LLM_OUTPUT_CHAR_LIMIT


def test_fallback_uses_npc_dialogue_table():
    conn = make_test_db()
    mock_backend = MagicMock()
    mock_backend.chat.side_effect = Exception("API timeout")
    handler = NPCConversationHandler(conn, mock_backend)
    result = handler.handle_message("grist", "!town01", "Hello")
    # Should use the pre-generated dialogue we seeded
    assert "Grist" in result or "NPC" in result


def test_fallback_marks_dialogue_used():
    conn = make_test_db()
    mock_backend = MagicMock()
    mock_backend.chat.side_effect = Exception("API timeout")
    handler = NPCConversationHandler(conn, mock_backend)
    handler.handle_message("grist", "!town01", "Hello")
    row = conn.execute(
        "SELECT used FROM npc_dialogue WHERE npc = 'grist' LIMIT 1"
    ).fetchone()
    assert row["used"] == 1


# ── Case Insensitivity ──


def test_npc_name_case_insensitive():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("GRIST", "!town01", "Hello")
    assert result is not None
    assert len(result) > 0


def test_npc_name_mixed_case():
    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    result = handler.handle_message("Maren", "!town01", "Help")
    assert result is not None
    assert len(result) > 0


# ── Worldbuilding Integration ──


_WORLDBUILDING_KEYWORDS = {
    "grist": ["Darkcragg", "epoch", "walls", "Breach", "Oryn", "Sola", "Malcor"],
    "maren": ["Floor", "burns", "Caverns", "Depths", "injuries"],
    "torval": ["Halls", "Depths", "Caverns", "dungeon-tested", "fire-rated"],
    "whisper": ["builder", "light", "below", "pattern", "Breach", "epoch"],
}


def test_personality_has_worldbuilding_keywords():
    for npc, keywords in _WORLDBUILDING_KEYWORDS.items():
        card = NPC_PERSONALITIES[npc]
        combined = card["voice"] + " " + card["knowledge"]
        found = any(kw.lower() in combined.lower() for kw in keywords)
        assert found, f"{npc} voice/knowledge missing worldbuilding keywords"


def test_example_lines_under_150_chars():
    for npc, card in NPC_PERSONALITIES.items():
        for line in card.get("example_lines", []):
            assert len(line) <= 150, (
                f"{npc} example line too long ({len(line)}): {line}"
            )


def test_system_prompt_includes_examples():
    conn = make_test_db()
    state = "Epoch 1, Day 5."
    for npc in ("grist", "maren", "torval", "whisper"):
        prompt = _build_system_prompt(conn, npc, state)
        assert "EXAMPLE RESPONSES" in prompt
        for line in NPC_PERSONALITIES[npc]["example_lines"]:
            assert line in prompt
