"""
Tests for NPC Hidden Easter Eggs — personality depth features.
Covers: death log, Maren death memory, Torval gamble TX, Whisper prophecy/countdown,
Maren lullaby, Torval-Whisper inter-NPC secret.
"""

import os
import sys
import sqlite3
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import GAMBLE_MIN_BET, GAMBLE_MAX_BET_RATIO
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.models import player as player_model
from src.systems.npc_conversation import (
    NPCConversationHandler,
    NPC_PERSONALITIES,
    _build_system_prompt,
    _detect_dummy_tx,
    _validate_gamble,
    _execute_gamble,
)


# ── Test Database Setup ─────────────────────────────────────────────────────


def _create_test_db(day_number=5) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the MMUD schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', ?)""",
        (day_number,),
    )
    conn.execute(
        "INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!test01', 'TestHero')"
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, level, gold_carried, floor, combat_monster_id, bard_tokens, gold_banked,
           last_login)
           VALUES (1, 1, 'TestHero', 'warrior', 30, 50, 5, 4, 3,
                   'town', 3, 200, 0, NULL, 2, 100,
                   '2026-01-05T00:00:00')"""
    )
    conn.execute(
        "INSERT INTO node_sessions (mesh_id, player_id) VALUES ('!test01', 1)"
    )
    # Room and monster for death logging tests
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short)
           VALUES (1, 2, 'Dark Corridor', 'A dark corridor.', 'Dark.')"""
    )
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (1, 1, 'Shadow Lurker', 50, 50, 3, 1, 1, 10, 5, 10, 1)"""
    )
    # NPC dialogue fallback
    conn.execute(
        "INSERT INTO npc_dialogue (npc, context, dialogue) VALUES ('grist', 'greeting', 'Grist nods.')"
    )
    conn.commit()
    return conn


def _get_player(conn):
    row = conn.execute("SELECT * FROM players WHERE id = 1").fetchone()
    return dict(row)


# ── Test 1: Death Log Created on Death ──


class TestDeathLog(unittest.TestCase):
    """Test that apply_death() creates death_log entries."""

    def test_death_log_created_on_death(self):
        """apply_death() creates a death_log entry with monster name and floor."""
        conn = _create_test_db()
        # Put player in combat on floor 2 with a monster
        conn.execute(
            "UPDATE players SET state = 'combat', floor = 2, combat_monster_id = 1 WHERE id = 1"
        )
        conn.commit()

        player_model.apply_death(conn, 1)

        row = conn.execute("SELECT * FROM death_log WHERE player_id = 1").fetchone()
        self.assertIsNotNone(row, "No death_log entry created")
        self.assertEqual(row["floor"], 2)
        self.assertEqual(row["monster_name"], "Shadow Lurker")

    def test_death_log_unknown_monster(self):
        """Death without combat_monster_id logs 'unknown' monster."""
        conn = _create_test_db()
        conn.execute(
            "UPDATE players SET state = 'combat', floor = 1, combat_monster_id = NULL WHERE id = 1"
        )
        conn.commit()

        player_model.apply_death(conn, 1)

        row = conn.execute("SELECT * FROM death_log WHERE player_id = 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["monster_name"], "unknown")


# ── Tests 2-3: Maren Death Memory ──


class TestMarenDeathMemory(unittest.TestCase):
    """Test Maren's system prompt includes death history."""

    def test_maren_prompt_includes_deaths(self):
        """Maren's system prompt contains death history after player dies."""
        conn = _create_test_db()
        player = _get_player(conn)

        # Insert death log entries
        conn.execute(
            "INSERT INTO death_log (player_id, floor, monster_name) VALUES (1, 2, 'Shadow Lurker')"
        )
        conn.commit()

        prompt = _build_system_prompt(conn, "maren", "Epoch 1, Day 5.", player=player)
        self.assertIn("PLAYER DEATH HISTORY", prompt)
        self.assertIn("Shadow Lurker", prompt)
        self.assertIn("Floor 2", prompt)

    def test_maren_prompt_no_deaths(self):
        """Maren's system prompt has no death block for players who haven't died."""
        conn = _create_test_db()
        player = _get_player(conn)

        prompt = _build_system_prompt(conn, "maren", "Epoch 1, Day 5.", player=player)
        self.assertNotIn("PLAYER DEATH HISTORY", prompt)


# ── Tests 4-10: Torval Gamble ──


class TestGambleValidation(unittest.TestCase):
    """Test gamble validation rules."""

    def setUp(self):
        self.conn = _create_test_db()

    def test_gamble_win_awards_gold(self):
        """Winning gamble adds bet amount to gold."""
        player = _get_player(self.conn)
        gold_before = player["gold_carried"]
        with patch("src.systems.npc_conversation.random.random", return_value=0.1):
            ok, msg, meta = _execute_gamble(self.conn, player, "10")
        self.assertTrue(ok)
        self.assertEqual(meta["result"], "win")
        self.assertEqual(meta["amount"], 10)
        updated = _get_player(self.conn)
        self.assertEqual(updated["gold_carried"], gold_before + 10)

    def test_gamble_lose_deducts_gold(self):
        """Losing gamble deducts bet amount from gold."""
        player = _get_player(self.conn)
        gold_before = player["gold_carried"]
        with patch("src.systems.npc_conversation.random.random", return_value=0.9):
            ok, msg, meta = _execute_gamble(self.conn, player, "10")
        self.assertTrue(ok)
        self.assertEqual(meta["result"], "lose")
        self.assertEqual(meta["amount"], 10)
        updated = _get_player(self.conn)
        self.assertEqual(updated["gold_carried"], gold_before - 10)

    def test_gamble_min_bet_enforced(self):
        """Bet below GAMBLE_MIN_BET rejected."""
        player = _get_player(self.conn)
        valid, reason = _validate_gamble(self.conn, player, "2")
        self.assertFalse(valid)
        self.assertEqual(reason, "too_poor")

    def test_gamble_max_bet_enforced(self):
        """Bet above 50% of gold rejected."""
        player = _get_player(self.conn)
        # Player has 200g, max bet = 100g
        valid, reason = _validate_gamble(self.conn, player, "150")
        self.assertFalse(valid)
        self.assertEqual(reason, "bet_too_high")

    def test_gamble_daily_limit(self):
        """Second gamble same day rejected with 'already_gambled'."""
        player = _get_player(self.conn)
        # Simulate a completed gamble TX in message_log today
        self.conn.execute(
            """INSERT INTO message_log (node, direction, message, message_type, player_id)
               VALUES ('TRVL', 'npc_tx', 'gamble:10 for player TestHero', 'npc_tx', 1)"""
        )
        self.conn.commit()
        valid, reason = _validate_gamble(self.conn, player, "10")
        self.assertFalse(valid)
        self.assertEqual(reason, "already_gambled")

    def test_gamble_too_poor(self):
        """Player with less than 5g can't gamble."""
        self.conn.execute("UPDATE players SET gold_carried = 3 WHERE id = 1")
        self.conn.commit()
        player = _get_player(self.conn)
        valid, reason = _validate_gamble(self.conn, player, "5")
        self.assertFalse(valid)
        self.assertEqual(reason, "too_poor")

    def test_gamble_valid_bet(self):
        """Valid bet passes validation."""
        player = _get_player(self.conn)
        valid, reason = _validate_gamble(self.conn, player, "50")
        self.assertTrue(valid)
        self.assertEqual(reason, "")


class TestGambleDummyKeywords(unittest.TestCase):
    """Test DummyBackend detects gamble keywords."""

    def test_gamble_keyword(self):
        action, detail = _detect_dummy_tx("torval", "let's gamble")
        self.assertEqual(action, "gamble")

    def test_bet_keyword(self):
        action, detail = _detect_dummy_tx("torval", "I want to bet")
        self.assertEqual(action, "gamble")

    def test_wager_keyword(self):
        action, detail = _detect_dummy_tx("torval", "wager some gold")
        self.assertEqual(action, "gamble")

    def test_gamble_prefix_with_amount(self):
        action, detail = _detect_dummy_tx("torval", "gamble 20")
        self.assertEqual(action, "gamble")
        self.assertEqual(detail, "20")

    def test_bet_prefix_with_amount(self):
        action, detail = _detect_dummy_tx("torval", "bet 50")
        self.assertEqual(action, "gamble")
        self.assertEqual(detail, "50")


class TestGambleFullFlow(unittest.TestCase):
    """Test full gamble flow with DummyBackend."""

    def test_gamble_quote_then_confirm(self):
        """DummyBackend: 'gamble 10' → quote → 'yes' → result."""
        conn = _create_test_db()
        handler = NPCConversationHandler(conn, DummyBackend())

        response1 = handler.handle_message("torval", "!test01", "gamble 10")
        self.assertIn("coin flip", response1)
        self.assertIn("Deal?", response1)

        response2 = handler.handle_message("torval", "!test01", "yes")
        # Should be a win or lose message
        self.assertTrue(
            "win" in response2.lower() or "gone" in response2.lower(),
            f"Expected gamble result, got: {response2}",
        )


# ── Tests 11-13: Whisper Countdown ──


class TestWhisperCountdown(unittest.TestCase):
    """Test Whisper's countdown injection into system prompt."""

    def test_whisper_countdown_injected(self):
        """Whisper's prompt contains days_remaining number."""
        conn = _create_test_db(day_number=10)
        prompt = _build_system_prompt(conn, "whisper", "Epoch 1, Day 10.")
        self.assertIn("COUNTDOWN RULE", prompt)
        self.assertIn("20", prompt)  # 30 - 10 = 20

    def test_whisper_countdown_day1(self):
        """Day 1 → 29 in prompt."""
        conn = _create_test_db(day_number=1)
        prompt = _build_system_prompt(conn, "whisper", "Epoch 1, Day 1.")
        self.assertIn("29", prompt)

    def test_whisper_countdown_day30(self):
        """Day 30 → 0 in prompt."""
        conn = _create_test_db(day_number=30)
        prompt = _build_system_prompt(conn, "whisper", "Epoch 1, Day 30.")
        self.assertIn("COUNTDOWN RULE", prompt)
        # 30 - 30 = 0
        self.assertIn("The number 0 is sacred", prompt)


# ── Tests 14-15: Maren Lullaby ──


class TestMarenLullaby(unittest.TestCase):
    """Test Maren's late-epoch vulnerability block."""

    def test_maren_lullaby_before_day25(self):
        """No lullaby block in prompt before day 25."""
        conn = _create_test_db(day_number=20)
        player = _get_player(conn)
        prompt = _build_system_prompt(conn, "maren", "Epoch 1, Day 20.", player=player)
        self.assertNotIn("LATE EPOCH", prompt)

    def test_maren_lullaby_after_day25(self):
        """Lullaby block present in prompt on day 25+."""
        conn = _create_test_db(day_number=25)
        player = _get_player(conn)
        prompt = _build_system_prompt(conn, "maren", "Epoch 1, Day 25.", player=player)
        self.assertIn("LATE EPOCH", prompt)
        self.assertIn("vulnerable", prompt)

    def test_maren_lullaby_day30(self):
        """Lullaby block present on last day."""
        conn = _create_test_db(day_number=30)
        player = _get_player(conn)
        prompt = _build_system_prompt(conn, "maren", "Epoch 1, Day 30.", player=player)
        self.assertIn("LATE EPOCH", prompt)


# ── Tests 16-18: Torval Whisper Secret ──


class TestTorvalWhisperSecret(unittest.TestCase):
    """Test Torval's response to Whisper mentions."""

    def test_torval_whisper_no_mention(self):
        """No Whisper block in Torval prompt when not mentioned."""
        conn = _create_test_db()
        player = _get_player(conn)
        prompt = _build_system_prompt(
            conn, "torval", "Epoch 1, Day 5.",
            player=player, whisper_mentions=0,
        )
        self.assertNotIn("asked about Whisper", prompt)
        self.assertNotIn("Deflect", prompt)

    def test_torval_whisper_first_mention(self):
        """'deflect' in Torval prompt after 1 Whisper mention."""
        conn = _create_test_db()
        player = _get_player(conn)
        prompt = _build_system_prompt(
            conn, "torval", "Epoch 1, Day 5.",
            player=player, whisper_mentions=1,
        )
        self.assertIn("Deflect", prompt)

    def test_torval_whisper_second_mention(self):
        """'crack' / 'before the tavern' in prompt after 2+ mentions."""
        conn = _create_test_db()
        player = _get_player(conn)
        prompt = _build_system_prompt(
            conn, "torval", "Epoch 1, Day 5.",
            player=player, whisper_mentions=2,
        )
        self.assertIn("crack", prompt)
        self.assertIn("before the tavern", prompt)


# ── Test 19: Whisper Prophecy in Knowledge ──


class TestWhisperProphecy(unittest.TestCase):
    """Test Whisper's knowledge field contains prophecy instructions."""

    def test_whisper_prophecy_in_knowledge(self):
        """Whisper's knowledge field contains DEATH PROPHECY instructions."""
        knowledge = NPC_PERSONALITIES["whisper"]["knowledge"]
        self.assertIn("DEATH PROPHECY", knowledge)
        self.assertIn("blade", knowledge)
        self.assertIn("shadow", knowledge)
        self.assertIn("light", knowledge)


# ── Test 20: Torval Whisper Secret in Knowledge ──


class TestTorvalWhisperKnowledge(unittest.TestCase):
    """Test Torval's knowledge includes Whisper secret."""

    def test_torval_whisper_secret_in_knowledge(self):
        """Torval's knowledge field contains WHISPER SECRET."""
        knowledge = NPC_PERSONALITIES["torval"]["knowledge"]
        self.assertIn("WHISPER SECRET", knowledge)
        self.assertIn("before the tavern", knowledge)


if __name__ == "__main__":
    unittest.main()
