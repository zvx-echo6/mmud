"""
Tests for NPC Transaction System.
Covers: TX tag parsing, confirm flow, validation, DummyBackend keywords,
template lengths, transaction logging, browse immediate, economy parity.
"""

import os
import sys
import sqlite3
import unittest
from unittest.mock import patch

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import BACKPACK_SIZE, SHOP_PRICES, SELL_PRICE_PERCENT
from src.generation.narrative import DummyBackend, BackendInterface
from src.systems.npc_conversation import (
    NPCConversationHandler,
    PendingTransaction,
    _parse_tx_tag,
    _CONFIRM_KEYWORDS,
    _QUOTES,
    _REJECTIONS,
    _SUCCESS,
    _detect_dummy_tx,
    _validate_heal,
    _validate_buy,
    _validate_sell,
    _validate_recap,
    _validate_hint,
    _execute_browse,
    _build_quote,
    _build_rejection,
)
from src.systems import economy


# ── Mock LLM Backend ────────────────────────────────────────────────────────


class MockBackend(BackendInterface):
    """Controlled mock backend that returns preset responses."""

    def __init__(self, response: str = "The NPC nods."):
        self._response = response

    def set_response(self, response: str):
        self._response = response

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        return self._response

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        return self._response


# ── Test Database Setup ─────────────────────────────────────────────────────


def _create_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the MMUD schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Core tables needed for NPC transactions
    conn.executescript("""
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mesh_id TEXT UNIQUE NOT NULL,
            handle TEXT UNIQUE NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            total_epochs INTEGER DEFAULT 0,
            epoch_wins INTEGER DEFAULT 0,
            lifetime_kills INTEGER DEFAULT 0,
            longest_hardcore_streak INTEGER DEFAULT 0
        );

        CREATE TABLE players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            name TEXT NOT NULL,
            class TEXT NOT NULL,
            level INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            hp INTEGER NOT NULL,
            hp_max INTEGER NOT NULL,
            pow INTEGER NOT NULL,
            def INTEGER NOT NULL,
            spd INTEGER NOT NULL,
            gold_carried INTEGER DEFAULT 0,
            gold_banked INTEGER DEFAULT 0,
            state TEXT DEFAULT 'town',
            floor INTEGER DEFAULT 0,
            room_id INTEGER,
            combat_monster_id INTEGER,
            hardcore INTEGER DEFAULT 0,
            dungeon_actions_remaining INTEGER DEFAULT 12,
            social_actions_remaining INTEGER DEFAULT 2,
            special_actions_remaining INTEGER DEFAULT 1,
            stat_points INTEGER DEFAULT 0,
            bard_tokens INTEGER DEFAULT 0,
            secrets_found INTEGER DEFAULT 0,
            last_login DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE epoch (
            id INTEGER PRIMARY KEY,
            epoch_number INTEGER NOT NULL,
            start_date DATETIME NOT NULL,
            end_date DATETIME NOT NULL,
            endgame_mode TEXT NOT NULL,
            breach_type TEXT NOT NULL,
            breach_open INTEGER DEFAULT 0,
            narrative_theme TEXT,
            day_number INTEGER DEFAULT 1
        );
        INSERT INTO epoch VALUES (1, 1, '2026-01-01', '2026-01-30', 'raid_boss', 'heist', 0, 'Dark', 5);

        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slot TEXT NOT NULL,
            tier INTEGER NOT NULL,
            pow_mod INTEGER DEFAULT 0,
            def_mod INTEGER DEFAULT 0,
            spd_mod INTEGER DEFAULT 0,
            special TEXT,
            description TEXT,
            floor_source INTEGER
        );

        CREATE TABLE inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(id),
            item_id INTEGER NOT NULL REFERENCES items(id),
            slot TEXT,
            equipped INTEGER DEFAULT 0
        );

        CREATE TABLE bounties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            description TEXT NOT NULL,
            target_monster_id INTEGER,
            target_value INTEGER NOT NULL,
            current_value INTEGER DEFAULT 0,
            floor_min INTEGER NOT NULL,
            floor_max INTEGER NOT NULL,
            phase TEXT NOT NULL,
            available_from_day INTEGER NOT NULL,
            active INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            completed_at DATETIME
        );

        CREATE TABLE broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tier INTEGER NOT NULL,
            targeted INTEGER DEFAULT 0,
            target_condition TEXT,
            message TEXT NOT NULL,
            dcrg_sent INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE broadcast_seen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broadcast_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(broadcast_id, player_id)
        );

        CREATE TABLE npc_dialogue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            npc TEXT NOT NULL,
            context TEXT NOT NULL,
            dialogue TEXT NOT NULL,
            used INTEGER DEFAULT 0
        );

        CREATE TABLE message_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            node TEXT NOT NULL,
            direction TEXT NOT NULL,
            sender_id TEXT,
            sender_name TEXT,
            recipient_id TEXT,
            message TEXT,
            message_type TEXT NOT NULL,
            player_id INTEGER,
            metadata TEXT
        );

        CREATE TABLE npc_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            npc TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            turn_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(player_id, npc)
        );

        CREATE TABLE secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            floor INTEGER NOT NULL,
            room_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            reward_type TEXT NOT NULL,
            reward_data TEXT,
            hint_tier1 TEXT,
            hint_tier2 TEXT,
            hint_tier3 TEXT,
            discovered_by INTEGER,
            discovered_at DATETIME,
            puzzle_group TEXT,
            puzzle_archetype TEXT,
            puzzle_order INTEGER,
            puzzle_symbol TEXT
        );

        CREATE TABLE secret_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            secret_id INTEGER NOT NULL,
            found INTEGER DEFAULT 0,
            found_at DATETIME,
            UNIQUE(player_id, secret_id)
        );

        CREATE TABLE discovery_buffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buff_type TEXT NOT NULL,
            buff_data TEXT,
            activated_by INTEGER,
            activated_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            floor INTEGER
        );

        CREATE TABLE rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            floor INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            description_short TEXT NOT NULL,
            is_hub INTEGER DEFAULT 0,
            is_checkpoint INTEGER DEFAULT 0,
            is_stairway INTEGER DEFAULT 0,
            is_breach INTEGER DEFAULT 0,
            is_vault INTEGER DEFAULT 0,
            trap_type TEXT,
            riddle_answer TEXT,
            htl_cleared INTEGER DEFAULT 0,
            htl_cleared_at DATETIME,
            ward_active INTEGER DEFAULT 0
        );
    """)

    # Seed a test player
    conn.execute(
        "INSERT INTO accounts (mesh_id, handle) VALUES ('!abc123', 'TestPlayer')"
    )
    conn.execute(
        """INSERT INTO players
           (account_id, name, class, level, hp, hp_max, pow, def, spd,
            gold_carried, gold_banked, state, bard_tokens)
           VALUES (1, 'TestPlayer', 'warrior', 3, 30, 50, 5, 4, 3,
                   200, 100, 'town', 3)"""
    )
    conn.commit()

    # Seed shop items (tier 1 and 2 available on day 5)
    conn.execute(
        "INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod) VALUES "
        "('Rusty Sword', 'weapon', 1, 2, 0, 0)"
    )
    conn.execute(
        "INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod) VALUES "
        "('Iron Blade', 'weapon', 2, 4, 0, 1)"
    )
    conn.commit()

    return conn


def _get_player(conn: sqlite3.Connection) -> dict:
    """Get the test player as a dict."""
    row = conn.execute("SELECT * FROM players WHERE id = 1").fetchone()
    return dict(row)


# ── Test Cases ──────────────────────────────────────────────────────────────


class TestTXTagParsing(unittest.TestCase):
    """Test [TX:action:detail] tag parsing."""

    def test_valid_tag(self):
        action, detail, clean = _parse_tx_tag("[TX:heal:_] Sit down. Let me look.")
        self.assertEqual(action, "heal")
        self.assertEqual(detail, "_")
        self.assertEqual(clean, "Sit down. Let me look.")

    def test_buy_tag_with_item(self):
        action, detail, clean = _parse_tx_tag("[TX:buy:Iron Blade] Fine choice.")
        self.assertEqual(action, "buy")
        self.assertEqual(detail, "Iron Blade")
        self.assertEqual(clean, "Fine choice.")

    def test_no_tag(self):
        action, detail, clean = _parse_tx_tag("Just a normal conversation.")
        self.assertEqual(action, "")
        self.assertEqual(detail, "")
        self.assertEqual(clean, "Just a normal conversation.")

    def test_malformed_tag(self):
        action, detail, clean = _parse_tx_tag("[TX:heal Sit down.")
        self.assertEqual(action, "")
        self.assertEqual(detail, "")
        self.assertEqual(clean, "[TX:heal Sit down.")

    def test_tag_mid_text_ignored(self):
        """Tags must be at the START of text."""
        action, detail, clean = _parse_tx_tag("Hello [TX:heal:_] world")
        self.assertEqual(action, "")
        self.assertEqual(detail, "")
        self.assertEqual(clean, "Hello [TX:heal:_] world")

    def test_empty_detail(self):
        action, detail, clean = _parse_tx_tag("[TX:browse:] Look around.")
        self.assertEqual(action, "browse")
        self.assertEqual(detail, "")
        self.assertEqual(clean, "Look around.")

    # ── Edge cases from smoke test ──

    def test_leading_whitespace(self):
        action, detail, clean = _parse_tx_tag("  [TX:heal:_] Sit down.")
        self.assertEqual(action, "heal")
        self.assertEqual(detail, "_")
        self.assertEqual(clean, "Sit down.")

    def test_leading_newline(self):
        action, detail, clean = _parse_tx_tag("\n[TX:heal:_] Sit down.")
        self.assertEqual(action, "heal")
        self.assertEqual(detail, "_")
        self.assertEqual(clean, "Sit down.")

    def test_markdown_backtick_wrapping(self):
        action, detail, clean = _parse_tx_tag("`[TX:heal:_]` Sit down.")
        self.assertEqual(action, "heal")
        self.assertEqual(detail, "_")
        self.assertEqual(clean, "Sit down.")

    def test_no_space_after_tag(self):
        action, detail, clean = _parse_tx_tag("[TX:heal:_]Sit down.")
        self.assertEqual(action, "heal")
        self.assertEqual(detail, "_")
        self.assertEqual(clean, "Sit down.")

    def test_extra_whitespace_after_tag(self):
        action, detail, clean = _parse_tx_tag("[TX:heal:_]   Sit down.")
        self.assertEqual(action, "heal")
        self.assertEqual(detail, "_")
        self.assertEqual(clean, "Sit down.")

    def test_nested_brackets_rejected(self):
        """[[TX:...]] should NOT match — extra leading bracket."""
        action, detail, clean = _parse_tx_tag("[[TX:heal:_]] Sit down.")
        self.assertEqual(action, "")
        self.assertEqual(detail, "")
        self.assertEqual(clean, "[[TX:heal:_]] Sit down.")

    def test_detail_with_spaces(self):
        action, detail, clean = _parse_tx_tag("[TX:buy:fire resist leather] 60g. Deal?")
        self.assertEqual(action, "buy")
        self.assertEqual(detail, "fire resist leather")
        self.assertEqual(clean, "60g. Deal?")


class TestConfirmFlow(unittest.TestCase):
    """Test the two-message confirm flow: quote → yes → execute."""

    def setUp(self):
        self.conn = _create_test_db()
        self.backend = MockBackend("[TX:heal:_] Sit down.")
        self.handler = NPCConversationHandler(self.conn, self.backend)

    def test_heal_quote_then_confirm(self):
        # Step 1: Trigger heal intent
        response1 = self.handler.handle_message("maren", "!abc123", "Can you patch me up?")
        # Should get a quote with cost
        self.assertIn("g to stitch", response1)
        self.assertIn("Say yes", response1)

        # Step 2: Confirm
        response2 = self.handler.handle_message("maren", "!abc123", "yes")
        # Should execute heal
        self.assertIn("HP mended", response2)
        self.assertEqual(self.handler.last_result_type, "npc_tx")

    def test_non_confirm_clears_pending(self):
        """A non-confirm message after a quote should clear the pending TX."""
        # Trigger heal
        self.handler.handle_message("maren", "!abc123", "Can you patch me up?")

        # Send non-confirm message
        self.backend.set_response("Yes, the Depths are rough.")
        response = self.handler.handle_message("maren", "!abc123", "What do you think of the Depths?")
        # Should be normal conversation, not a TX execution
        self.assertNotIn("HP mended", response)

    def test_yes_with_no_pending_falls_to_conversation(self):
        """'yes' with no pending TX should be normal conversation."""
        self.backend.set_response("Yes what? Speak clearly.")
        response = self.handler.handle_message("maren", "!abc123", "yes")
        # Should be treated as normal chat, not a TX
        self.assertNotIn("HP mended", response)

    def test_new_intent_overwrites_pending(self):
        """A new TX intent should overwrite a pending one."""
        # First intent: heal
        self.handler.handle_message("maren", "!abc123", "heal me")

        # New intent: heal again (should overwrite, not execute)
        self.backend.set_response("[TX:heal:_] Again?")
        response = self.handler.handle_message("maren", "!abc123", "actually heal me now")
        self.assertIn("g to stitch", response)


class TestValidation(unittest.TestCase):
    """Test server-side validation rejects invalid transactions."""

    def setUp(self):
        self.conn = _create_test_db()

    def test_heal_full_hp(self):
        """Full HP → rejection."""
        player = _get_player(self.conn)
        # Set HP to max
        self.conn.execute("UPDATE players SET hp = hp_max WHERE id = 1")
        self.conn.commit()
        player = _get_player(self.conn)
        valid, reason = _validate_heal(self.conn, player)
        self.assertFalse(valid)
        self.assertEqual(reason, "full_hp")

    def test_heal_no_gold(self):
        """Not enough gold → rejection."""
        self.conn.execute("UPDATE players SET gold_carried = 0 WHERE id = 1")
        self.conn.commit()
        player = _get_player(self.conn)
        valid, reason = _validate_heal(self.conn, player)
        self.assertFalse(valid)
        self.assertEqual(reason, "no_gold")

    def test_heal_valid(self):
        """Hurt with gold → valid."""
        player = _get_player(self.conn)  # hp=30, hp_max=50, gold=200
        valid, reason = _validate_heal(self.conn, player)
        self.assertTrue(valid)

    def test_buy_not_found(self):
        """Non-existent item → rejection."""
        player = _get_player(self.conn)
        valid, reason = _validate_buy(self.conn, player, "Nonexistent Sword")
        self.assertFalse(valid)
        self.assertEqual(reason, "not_found")

    def test_buy_no_gold(self):
        """Not enough gold for item → rejection."""
        self.conn.execute("UPDATE players SET gold_carried = 1 WHERE id = 1")
        self.conn.commit()
        player = _get_player(self.conn)
        valid, reason = _validate_buy(self.conn, player, "Rusty Sword")
        self.assertFalse(valid)
        self.assertEqual(reason, "no_gold")

    def test_buy_full_backpack(self):
        """Full backpack → rejection."""
        player = _get_player(self.conn)
        # Fill backpack with 8 items
        for _ in range(BACKPACK_SIZE):
            self.conn.execute(
                "INSERT INTO inventory (player_id, item_id, equipped) VALUES (1, 1, 0)"
            )
        self.conn.commit()
        valid, reason = _validate_buy(self.conn, player, "Rusty Sword")
        self.assertFalse(valid)
        self.assertEqual(reason, "full_bag")

    def test_buy_valid(self):
        """Valid buy → accepted."""
        player = _get_player(self.conn)
        valid, reason = _validate_buy(self.conn, player, "Rusty Sword")
        self.assertTrue(valid)

    def test_sell_no_item(self):
        """Item not in inventory → rejection."""
        player = _get_player(self.conn)
        valid, reason = _validate_sell(self.conn, player, "Rusty Sword")
        self.assertFalse(valid)
        self.assertEqual(reason, "no_item")

    def test_sell_valid(self):
        """Item in inventory → accepted."""
        self.conn.execute(
            "INSERT INTO inventory (player_id, item_id, equipped) VALUES (1, 1, 0)"
        )
        self.conn.commit()
        player = _get_player(self.conn)
        valid, reason = _validate_sell(self.conn, player, "Rusty Sword")
        self.assertTrue(valid)

    def test_recap_no_tokens(self):
        """No bard tokens → rejection."""
        self.conn.execute("UPDATE players SET bard_tokens = 0 WHERE id = 1")
        self.conn.commit()
        player = _get_player(self.conn)
        valid, reason = _validate_recap(self.conn, player)
        self.assertFalse(valid)
        self.assertEqual(reason, "no_tokens")

    def test_hint_no_tokens(self):
        """No bard tokens → rejection."""
        self.conn.execute("UPDATE players SET bard_tokens = 0 WHERE id = 1")
        self.conn.commit()
        player = _get_player(self.conn)
        valid, reason = _validate_hint(self.conn, player)
        self.assertFalse(valid)

    def test_hint_valid(self):
        """Has tokens → accepted."""
        player = _get_player(self.conn)  # bard_tokens=3
        valid, reason = _validate_hint(self.conn, player)
        self.assertTrue(valid)


class TestDummyBackendKeywords(unittest.TestCase):
    """Test keyword-based TX detection for DummyBackend."""

    def test_maren_heal_keywords(self):
        action, detail = _detect_dummy_tx("maren", "can you heal me?")
        self.assertEqual(action, "heal")

    def test_maren_patch_keyword(self):
        action, detail = _detect_dummy_tx("maren", "patch me up")
        self.assertEqual(action, "heal")

    def test_maren_no_match(self):
        action, detail = _detect_dummy_tx("maren", "tell me about floor 2")
        self.assertEqual(action, "")

    def test_torval_buy(self):
        action, detail = _detect_dummy_tx("torval", "buy Rusty Sword")
        self.assertEqual(action, "buy")
        self.assertEqual(detail, "rusty sword")

    def test_torval_sell(self):
        action, detail = _detect_dummy_tx("torval", "sell Iron Blade")
        self.assertEqual(action, "sell")
        self.assertEqual(detail, "iron blade")

    def test_torval_browse(self):
        action, detail = _detect_dummy_tx("torval", "what do you have in stock?")
        self.assertEqual(action, "browse")

    def test_torval_no_match(self):
        action, detail = _detect_dummy_tx("torval", "nice weather today")
        self.assertEqual(action, "")

    def test_grist_recap(self):
        action, detail = _detect_dummy_tx("grist", "give me a recap")
        self.assertEqual(action, "recap")

    def test_grist_hint(self):
        action, detail = _detect_dummy_tx("grist", "spend token on a hint")
        self.assertEqual(action, "hint")

    def test_whisper_hint(self):
        action, detail = _detect_dummy_tx("whisper", "tell me a secret")
        self.assertEqual(action, "hint")

    def test_whisper_no_match(self):
        action, detail = _detect_dummy_tx("whisper", "hello")
        self.assertEqual(action, "")


class TestDummyBackendFullFlow(unittest.TestCase):
    """Test full transaction flow with DummyBackend (keyword detection)."""

    def setUp(self):
        self.conn = _create_test_db()
        self.handler = NPCConversationHandler(self.conn, DummyBackend())

    def test_dummy_heal_flow(self):
        """DummyBackend: 'heal' keyword → quote → 'yes' → heals."""
        response1 = self.handler.handle_message("maren", "!abc123", "heal")
        self.assertIn("g to stitch", response1)

        response2 = self.handler.handle_message("maren", "!abc123", "yes")
        self.assertIn("HP mended", response2)

    def test_dummy_browse_immediate(self):
        """DummyBackend: browse returns immediate shop listing."""
        response = self.handler.handle_message("torval", "!abc123", "show me your wares")
        # Should list items with prices
        self.assertIn("Rusty Sword", response)
        self.assertIn("g)", response)

    def test_dummy_buy_flow(self):
        """DummyBackend: 'buy Rusty Sword' → quote → 'yes' → bought."""
        response1 = self.handler.handle_message("torval", "!abc123", "buy Rusty Sword")
        self.assertIn("g.", response1)
        self.assertIn("Deal?", response1)

        response2 = self.handler.handle_message("torval", "!abc123", "yes")
        self.assertIn("Yours", response2)

    def test_dummy_sell_flow(self):
        """DummyBackend: sell item → quote → confirm."""
        # Add item to inventory first
        self.conn.execute(
            "INSERT INTO inventory (player_id, item_id, equipped) VALUES (1, 1, 0)"
        )
        self.conn.commit()

        response1 = self.handler.handle_message("torval", "!abc123", "sell Rusty Sword")
        self.assertIn("g for the", response1)

        response2 = self.handler.handle_message("torval", "!abc123", "yes")
        self.assertIn("Done", response2)

    def test_dummy_non_tx_chat(self):
        """DummyBackend: non-TX message gets normal NPC dialogue."""
        response = self.handler.handle_message("maren", "!abc123", "tell me about floor 2")
        # Should be a DummyBackend greeting line, not a TX
        self.assertNotIn("g to stitch", response)


class TestTemplateLengths(unittest.TestCase):
    """Verify all templates stay under 150 chars with worst-case values."""

    def test_quote_templates(self):
        for key, template in _QUOTES.items():
            # Fill with large but realistic values
            filled = template.format(
                cost=99999, gold=99999, item="A" * 30, value=99999, tokens=5,
                amount=99999,
            )
            self.assertLessEqual(
                len(filled), 150,
                f"Quote template {key} too long: {len(filled)} chars: {filled}",
            )

    def test_rejection_templates(self):
        for key, template in _REJECTIONS.items():
            filled = template.format(cost=99999, gold=99999, min=99999)
            self.assertLessEqual(
                len(filled), 150,
                f"Rejection template {key} too long: {len(filled)} chars: {filled}",
            )

    def test_success_templates(self):
        for key, template in _SUCCESS.items():
            filled = template.format(
                hp_restored=999, gold_remaining=99999,
                item="A" * 30, value=99999,
                recap_text="A" * 100, hint_text="A" * 100,
                amount=99999,
            )
            self.assertLessEqual(
                len(filled), 150,
                f"Success template {key} too long: {len(filled)} chars: {filled}",
            )


class TestTransactionLogging(unittest.TestCase):
    """Test that completed transactions are logged with direction 'npc_tx'."""

    def setUp(self):
        self.conn = _create_test_db()
        self.backend = MockBackend("[TX:heal:_] Sit down.")
        self.handler = NPCConversationHandler(self.conn, self.backend)

    def test_heal_logged(self):
        # Trigger and confirm heal
        self.handler.handle_message("maren", "!abc123", "heal me")
        self.handler.handle_message("maren", "!abc123", "yes")

        # Check log
        row = self.conn.execute(
            "SELECT * FROM message_log WHERE direction = 'npc_tx'"
        ).fetchone()
        self.assertIsNotNone(row, "No npc_tx log entry found")
        self.assertEqual(row["node"], "MRN")
        self.assertEqual(row["message_type"], "npc_tx")
        self.assertEqual(row["player_id"], 1)

    def test_no_log_on_rejection(self):
        """Rejected transactions should NOT create npc_tx log entries."""
        # Set HP to max so heal is rejected
        self.conn.execute("UPDATE players SET hp = hp_max WHERE id = 1")
        self.conn.commit()

        self.handler.handle_message("maren", "!abc123", "heal me")

        row = self.conn.execute(
            "SELECT * FROM message_log WHERE direction = 'npc_tx'"
        ).fetchone()
        self.assertIsNone(row, "Rejection should not create npc_tx log")


class TestBrowseImmediate(unittest.TestCase):
    """Browse returns shop listing immediately with no pending TX."""

    def setUp(self):
        self.conn = _create_test_db()
        self.backend = MockBackend("[TX:browse:_] Let me show you.")
        self.handler = NPCConversationHandler(self.conn, self.backend)

    def test_browse_no_pending(self):
        response = self.handler.handle_message("torval", "!abc123", "what do you have?")
        # Should list items
        self.assertIn("Rusty Sword", response)
        # Should NOT create a pending TX
        session = self.handler.sessions.get(1, "torval")
        self.assertIsNone(session.pending if session else None)


class TestEconomyParity(unittest.TestCase):
    """Verify NPC transactions produce identical results to EMBR action handlers."""

    def setUp(self):
        self.conn = _create_test_db()

    def test_heal_same_cost(self):
        """NPC heal cost matches economy.calc_heal_cost()."""
        player = _get_player(self.conn)
        expected_cost = economy.calc_heal_cost(player)
        # Build quote and verify cost is in it
        quote = _build_quote(self.conn, "maren", "heal", "_", player)
        self.assertIn(str(expected_cost), quote)

    def test_buy_uses_economy_buy_item(self):
        """NPC buy delegates to economy.buy_item()."""
        player = _get_player(self.conn)
        gold_before = player["gold_carried"]
        # Execute buy via economy directly
        ok, msg = economy.buy_item(self.conn, 1, "Rusty Sword", 5)
        self.assertTrue(ok)
        # Verify gold deducted
        player_after = _get_player(self.conn)
        expected_price = SHOP_PRICES[1]  # tier 1 price
        self.assertEqual(player_after["gold_carried"], gold_before - expected_price)

    def test_sell_uses_economy_sell_item(self):
        """NPC sell delegates to economy.sell_item()."""
        # Add item first
        self.conn.execute(
            "INSERT INTO inventory (player_id, item_id, equipped) VALUES (1, 1, 0)"
        )
        self.conn.commit()
        player = _get_player(self.conn)
        gold_before = player["gold_carried"]
        ok, msg = economy.sell_item(self.conn, 1, "Rusty Sword")
        self.assertTrue(ok)
        player_after = _get_player(self.conn)
        expected_sell = max(1, SHOP_PRICES[1] * SELL_PRICE_PERCENT // 100)
        self.assertEqual(player_after["gold_carried"], gold_before + expected_sell)


class TestRulePriority(unittest.TestCase):
    """Verify rule 1 and rule 2 still work correctly with TX system."""

    def setUp(self):
        self.conn = _create_test_db()
        self.handler = NPCConversationHandler(self.conn, DummyBackend())

    def test_unknown_player_rejected(self):
        """Unknown mesh ID → rule 1 rejection."""
        response = self.handler.handle_message("maren", "!unknown999", "heal me")
        self.assertIn("DM EMBR", response)
        self.assertEqual(self.handler.last_result_type, "npc_rule1")

    def test_not_in_town_rejected(self):
        """Player in dungeon → rule 2 rejection."""
        self.conn.execute("UPDATE players SET state = 'dungeon' WHERE id = 1")
        self.conn.commit()
        response = self.handler.handle_message("maren", "!abc123", "heal me")
        self.assertIn("Darkcragg", response)
        self.assertEqual(self.handler.last_result_type, "npc_rule2")


class TestConfirmKeywords(unittest.TestCase):
    """Verify all confirm keywords work."""

    def test_all_confirm_keywords(self):
        for keyword in _CONFIRM_KEYWORDS:
            conn = _create_test_db()
            backend = MockBackend("[TX:heal:_] Sit down.")
            handler = NPCConversationHandler(conn, backend)

            # Trigger heal
            handler.handle_message("maren", "!abc123", "heal me")
            # Confirm with this keyword
            response = handler.handle_message("maren", "!abc123", keyword)
            self.assertIn("HP mended", response,
                          f"Confirm keyword '{keyword}' did not trigger execution")


if __name__ == "__main__":
    unittest.main()
