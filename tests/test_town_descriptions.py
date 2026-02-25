"""
Tests for Town Descriptions and NPC-Initiated DMs.
Covers: town keywords, look in town/dungeon, heal+description, shop+description,
NPC DM trigger, cooldown, expiry, single-node mode, template lengths.
"""

import os
import sys
import sqlite3
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    COMMAND_NPC_DM_MAP,
    NPC_GREETING_COOLDOWN,
    NPC_GREETINGS,
    NPC_TO_NODE,
    TOWN_DESCRIPTIONS,
)
from src.core.actions import (
    action_barkeep,
    action_heal,
    action_healer_desc,
    action_look,
    action_merchant_desc,
    action_sage_desc,
    action_shop,
    handle_action,
)
from src.core.engine import GameEngine
from src.transport.formatter import fmt
from src.transport.parser import parse
from src.transport.router import NodeRouter
from src.generation.narrative import DummyBackend
from src.systems.npc_conversation import NPCConversationHandler


# ── Test Database Setup ─────────────────────────────────────────────────────


def _create_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the MMUD schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

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

        CREATE TABLE room_exits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_room_id INTEGER NOT NULL REFERENCES rooms(id),
            to_room_id INTEGER NOT NULL REFERENCES rooms(id),
            direction TEXT NOT NULL
        );

        CREATE TABLE monsters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            tier INTEGER NOT NULL,
            room_id INTEGER,
            hp INTEGER NOT NULL,
            hp_max INTEGER NOT NULL,
            pow INTEGER NOT NULL,
            def INTEGER NOT NULL,
            spd INTEGER NOT NULL,
            xp_reward INTEGER DEFAULT 10,
            gold_reward_min INTEGER DEFAULT 5,
            gold_reward_max INTEGER DEFAULT 15,
            respawn_hours INTEGER DEFAULT 24
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

        CREATE TABLE player_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(id),
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            message TEXT NOT NULL,
            helpful_votes INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE mail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            read INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE bounty_contributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bounty_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            damage_dealt INTEGER DEFAULT 0,
            UNIQUE(bounty_id, player_id)
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

    # Seed shop items
    conn.execute(
        "INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod) VALUES "
        "('Rusty Sword', 'weapon', 1, 2, 0, 0)"
    )
    conn.execute(
        "INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod) VALUES "
        "('Iron Blade', 'weapon', 2, 4, 0, 1)"
    )

    # Seed a dungeon room for dungeon look tests
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Entry Hall', 'A damp stone hall, water dripping from cracks.', 'Damp stone hall.', 1)"""
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, direction, to_room_id) VALUES (1, 'n', 1)"
    )
    conn.commit()

    return conn


def _get_player(conn: sqlite3.Connection) -> dict:
    """Get the test player as a dict."""
    row = conn.execute("SELECT * FROM players WHERE id = 1").fetchone()
    return dict(row)


# ── Mock Transport ──────────────────────────────────────────────────────────


class MockTransport:
    """Mock MeshTransport that records sent DMs."""

    def __init__(self, node_id: str = "!node"):
        self.my_node_id = node_id
        self.sent_dms: list[tuple[str, str]] = []  # (recipient_id, message)
        self._callback = None

    def send_dm(self, recipient_id: str, message: str) -> None:
        self.sent_dms.append((recipient_id, message))

    def set_message_callback(self, callback):
        self._callback = callback


# ── Test Cases ──────────────────────────────────────────────────────────────


class TestTownKeywords(unittest.TestCase):
    """Test that town keywords return correct descriptions."""

    def setUp(self):
        self.conn = _create_test_db()
        self.player = _get_player(self.conn)

    def test_barkeep_returns_grist_description(self):
        """'bar'/'barkeep' returns Grist's description."""
        response = action_barkeep(self.conn, self.player, [])
        self.assertIn("Grist", response)
        self.assertIn("tallied names", response)

    def test_healer_returns_maren_description(self):
        """'healer' returns Maren's description."""
        response = action_healer_desc(self.conn, self.player, [])
        self.assertIn("Maren", response)
        self.assertIn("lavender", response)

    def test_merchant_returns_torval_description(self):
        """'merchant' returns Torval's description."""
        response = action_merchant_desc(self.conn, self.player, [])
        self.assertIn("Torval", response)
        self.assertIn("coin pouch", response)

    def test_sage_returns_whisper_description(self):
        """'sage' returns Whisper's description."""
        response = action_sage_desc(self.conn, self.player, [])
        self.assertIn("Whisper", response)
        self.assertIn("moth-eaten", response)

    def test_parser_aliases(self):
        """Verify all town keyword aliases parse correctly."""
        alias_map = {
            "tavern": "barkeep",
            "grist": "barkeep",
            "drink": "barkeep",
            "bar": "barkeep",
            "maren": "healer",
            "clinic": "healer",
            "infirmary": "healer",
            "torval": "merchant",
            "trader": "merchant",
            "sage": "sage",
            "whisper": "sage",
            "oracle": "sage",
            "corner": "sage",
        }
        for keyword, expected_cmd in alias_map.items():
            parsed = parse(keyword)
            self.assertIsNotNone(parsed, f"Parser returned None for '{keyword}'")
            self.assertEqual(
                parsed.command, expected_cmd,
                f"'{keyword}' parsed to '{parsed.command}', expected '{expected_cmd}'",
            )

    def test_not_in_town_rejects(self):
        """Town descriptions require being in town."""
        self.conn.execute("UPDATE players SET state = 'dungeon' WHERE id = 1")
        self.conn.commit()
        player = _get_player(self.conn)
        self.assertIn("town", action_healer_desc(self.conn, player, []).lower())
        self.assertIn("town", action_merchant_desc(self.conn, player, []).lower())
        self.assertIn("town", action_sage_desc(self.conn, player, []).lower())


class TestLookCommand(unittest.TestCase):
    """Test look command returns correct description in town vs dungeon."""

    def setUp(self):
        self.conn = _create_test_db()

    def test_look_in_town_returns_tavern_description(self):
        """'look' in town returns the general tavern description."""
        player = _get_player(self.conn)
        response = action_look(self.conn, player, [])
        self.assertIn("Last Ember", response)
        self.assertIn("dungeon breathes", response)

    def test_look_in_dungeon_returns_room_description(self):
        """'look' in dungeon returns the dungeon room, NOT the tavern."""
        self.conn.execute(
            "UPDATE players SET state = 'dungeon', room_id = 1 WHERE id = 1"
        )
        self.conn.commit()
        player = _get_player(self.conn)
        response = action_look(self.conn, player, [])
        # Should be the dungeon room, not the tavern
        self.assertNotIn("Last Ember", response)
        self.assertIn("Entry Hall", response)


class TestHealAndShopAdditive(unittest.TestCase):
    """Test that heal and shop still work AND trigger NPC DMs."""

    def setUp(self):
        self.conn = _create_test_db()
        self.engine = GameEngine(self.conn)

    def test_heal_still_heals(self):
        """'heal' command still executes heal action."""
        response = self.engine.process_message("!abc123", "TestPlayer", "heal")
        # Should show heal cost (player is hurt: 30/50)
        self.assertIn("Heal", response)
        self.assertIn("g", response)

    def test_heal_queues_maren_dm(self):
        """'heal' command queues a Maren greeting DM."""
        self.engine.process_message("!abc123", "TestPlayer", "heal")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)
        npc, recipient = self.engine.npc_dm_queue[0]
        self.assertEqual(npc, "maren")
        self.assertEqual(recipient, "!abc123")

    def test_shop_still_lists_items(self):
        """'shop' command still shows shop inventory."""
        response = self.engine.process_message("!abc123", "TestPlayer", "shop")
        self.assertIn("Rusty Sword", response)

    def test_shop_queues_torval_dm(self):
        """'shop' command queues a Torval greeting DM."""
        self.engine.process_message("!abc123", "TestPlayer", "shop")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)
        npc, recipient = self.engine.npc_dm_queue[0]
        self.assertEqual(npc, "torval")
        self.assertEqual(recipient, "!abc123")

    def test_barkeep_queues_grist_dm(self):
        """'bar' command queues a Grist greeting DM."""
        self.engine.process_message("!abc123", "TestPlayer", "bar")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)
        npc, recipient = self.engine.npc_dm_queue[0]
        self.assertEqual(npc, "grist")

    def test_sage_queues_whisper_dm(self):
        """'sage' command queues a Whisper greeting DM."""
        self.engine.process_message("!abc123", "TestPlayer", "sage")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)
        npc, recipient = self.engine.npc_dm_queue[0]
        self.assertEqual(npc, "whisper")


class TestNPCDMCooldown(unittest.TestCase):
    """Test NPC DM cooldown prevents spam."""

    def setUp(self):
        self.conn = _create_test_db()
        self.engine = GameEngine(self.conn)

    def test_first_interaction_queues_dm(self):
        """First interaction with an NPC queues a DM."""
        self.engine.process_message("!abc123", "TestPlayer", "bar")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)

    def test_repeat_within_cooldown_no_dm(self):
        """Repeat interaction within cooldown does NOT queue a DM."""
        self.engine.process_message("!abc123", "TestPlayer", "bar")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)

        # Second call within cooldown
        self.engine.process_message("!abc123", "TestPlayer", "bar")
        # Queue was cleared at start of process_message, but no new DM queued
        self.assertEqual(len(self.engine.npc_dm_queue), 0)

    def test_cooldown_expires_dm_fires_again(self):
        """After cooldown expires, NPC DM fires again."""
        self.engine.process_message("!abc123", "TestPlayer", "bar")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)

        # Manually expire the cooldown
        for key in self.engine._npc_dm_cooldowns:
            self.engine._npc_dm_cooldowns[key] -= NPC_GREETING_COOLDOWN + 1

        # Now should fire again
        self.engine.process_message("!abc123", "TestPlayer", "bar")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)

    def test_different_npcs_independent_cooldowns(self):
        """Different NPCs have independent cooldowns."""
        self.engine.process_message("!abc123", "TestPlayer", "bar")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)

        self.engine.process_message("!abc123", "TestPlayer", "sage")
        self.assertEqual(len(self.engine.npc_dm_queue), 1)
        npc, _ = self.engine.npc_dm_queue[0]
        self.assertEqual(npc, "whisper")

    def test_look_in_town_no_npc_dm(self):
        """'look' in town does NOT trigger any NPC DM."""
        self.engine.process_message("!abc123", "TestPlayer", "look")
        self.assertEqual(len(self.engine.npc_dm_queue), 0)

    def test_not_in_town_no_dm_queued(self):
        """Commands from dungeon do NOT queue NPC DMs."""
        self.conn.execute(
            "UPDATE players SET state = 'dungeon', room_id = 1, "
            "floor = 1 WHERE id = 1"
        )
        self.conn.commit()
        self.engine.process_message("!abc123", "TestPlayer", "look")
        self.assertEqual(len(self.engine.npc_dm_queue), 0)


class TestRouterNPCDMSend(unittest.TestCase):
    """Test that the router sends NPC DMs from the correct nodes."""

    def setUp(self):
        self.conn = _create_test_db()
        self.engine = GameEngine(self.conn)
        self.npc_handler = NPCConversationHandler(self.conn, DummyBackend())

        # Create mock transports
        self.embr_transport = MockTransport("!embr")
        self.grst_transport = MockTransport("!grst")
        self.mrn_transport = MockTransport("!mrn")
        self.trvl_transport = MockTransport("!trvl")
        self.wspr_transport = MockTransport("!wspr")

        self.router = NodeRouter(self.engine, self.npc_handler)
        self.router.register_transport("EMBR", self.embr_transport)
        self.router.register_transport("GRST", self.grst_transport)
        self.router.register_transport("MRN", self.mrn_transport)
        self.router.register_transport("TRVL", self.trvl_transport)
        self.router.register_transport("WSPR", self.wspr_transport)
        self.router._own_node_ids = {"!embr", "!grst", "!mrn", "!trvl", "!wspr"}

    def _make_msg(self, text: str, sender_id: str = "!abc123"):
        """Create a mock MeshMessage."""
        msg = MagicMock()
        msg.sender_id = sender_id
        msg.sender_name = "TestPlayer"
        msg.text = text
        msg.is_dm = True
        return msg

    def test_bar_sends_embr_description_and_grist_dm(self):
        """'bar' → EMBR sends description + GRST sends greeting."""
        msg = self._make_msg("bar")
        self.router.route_message("EMBR", msg)

        # EMBR should have sent the description
        self.assertTrue(len(self.embr_transport.sent_dms) > 0)
        embr_response = self.embr_transport.sent_dms[0][1]
        self.assertIn("Grist", embr_response)

        # GRST should have sent a greeting
        self.assertTrue(len(self.grst_transport.sent_dms) > 0)
        greeting = self.grst_transport.sent_dms[0][1]
        self.assertTrue(len(greeting) > 0)
        self.assertTrue(len(greeting) <= 150)

    def test_heal_sends_heal_response_and_maren_dm(self):
        """'heal' → EMBR sends heal response + MRN sends greeting."""
        msg = self._make_msg("heal")
        self.router.route_message("EMBR", msg)

        # EMBR should have sent heal response
        self.assertTrue(len(self.embr_transport.sent_dms) > 0)

        # MRN should have sent a greeting
        self.assertTrue(len(self.mrn_transport.sent_dms) > 0)
        greeting = self.mrn_transport.sent_dms[0][1]
        self.assertTrue(len(greeting) <= 150)

    def test_single_node_mode_no_crash(self):
        """If NPC transport not registered, skip DM without crash."""
        # Create router with EMBR only
        router = NodeRouter(self.engine, self.npc_handler)
        router.register_transport("EMBR", self.embr_transport)
        router._own_node_ids = {"!embr"}

        # Reset engine cooldowns so DM would trigger
        self.engine._npc_dm_cooldowns.clear()

        msg = self._make_msg("bar")
        # Should not raise
        router.route_message("EMBR", msg)

        # EMBR should have sent description
        # (previous test may have added to sent_dms, check last one)
        self.assertTrue(len(self.embr_transport.sent_dms) > 0)

    def test_greeting_logged(self):
        """NPC greeting DMs are logged to message_log."""
        msg = self._make_msg("bar")
        self.router.route_message("EMBR", msg)

        row = self.conn.execute(
            "SELECT * FROM message_log WHERE message_type = 'npc_greeting'"
        ).fetchone()
        self.assertIsNotNone(row, "No npc_greeting log entry found")
        self.assertEqual(row["node"], "GRST")
        self.assertEqual(row["direction"], "outbound")


class TestTemplateLengths(unittest.TestCase):
    """Verify all description and greeting templates are under 150 chars."""

    def test_town_descriptions_under_150(self):
        for key, desc in TOWN_DESCRIPTIONS.items():
            self.assertLessEqual(
                len(desc), 150,
                f"TOWN_DESCRIPTIONS['{key}'] is {len(desc)} chars: {desc}",
            )

    def test_npc_greetings_under_150(self):
        for npc, greetings in NPC_GREETINGS.items():
            for i, greeting in enumerate(greetings):
                self.assertLessEqual(
                    len(greeting), 150,
                    f"NPC_GREETINGS['{npc}'][{i}] is {len(greeting)} chars: {greeting}",
                )

    def test_formatted_descriptions_under_150(self):
        """Descriptions after fmt() are still under 150 chars."""
        for key, desc in TOWN_DESCRIPTIONS.items():
            formatted = fmt(desc)
            self.assertLessEqual(
                len(formatted), 150,
                f"fmt(TOWN_DESCRIPTIONS['{key}']) is {len(formatted)} chars",
            )


class TestCommandNPCMapping(unittest.TestCase):
    """Verify COMMAND_NPC_DM_MAP is consistent with NPC_TO_NODE."""

    def test_all_mapped_npcs_have_nodes(self):
        for cmd, npc in COMMAND_NPC_DM_MAP.items():
            self.assertIn(
                npc, NPC_TO_NODE,
                f"COMMAND_NPC_DM_MAP['{cmd}'] maps to '{npc}' "
                f"but '{npc}' is not in NPC_TO_NODE",
            )

    def test_all_mapped_npcs_have_greetings(self):
        for cmd, npc in COMMAND_NPC_DM_MAP.items():
            self.assertIn(
                npc, NPC_GREETINGS,
                f"COMMAND_NPC_DM_MAP['{cmd}'] maps to '{npc}' "
                f"but '{npc}' has no greetings",
            )


if __name__ == "__main__":
    unittest.main()
