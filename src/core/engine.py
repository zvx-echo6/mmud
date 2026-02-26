"""
MMUD Game Engine — Main message processing loop.

Pipeline:
  receive_message() → parse_command() → check_action_budget()
  → execute_action() → format_response() → send_message()
"""

import logging
import sqlite3
import time
from typing import Optional

from config import CLASSES, COMMAND_NPC_DM_MAP, NPC_GREETING_COOLDOWN, NPC_TO_NODE
from src.core.actions import handle_action
from src.models import player as player_model
from src.models import world as world_data
from src.systems import barkeep as barkeep_sys
from src.systems import broadcast as broadcast_sys
from src.transport.formatter import fmt
from src.transport.parser import ParsedCommand, parse

logger = logging.getLogger(__name__)


class GameEngine:
    """Main game engine. Processes inbound messages and produces responses."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        # NPC DM queue: populated by process_message, drained by router
        # Each entry: (npc_name, recipient_mesh_id)
        self.npc_dm_queue: list[tuple[str, str]] = []
        # Per-player per-NPC cooldown timestamps {(mesh_id, npc): monotonic_time}
        self._npc_dm_cooldowns: dict[tuple[str, str], float] = {}

        # Schema migration: add town_location column (idempotent)
        try:
            conn.execute("ALTER TABLE players ADD COLUMN town_location TEXT")
            conn.commit()
        except Exception:
            pass  # Column already exists

        # Schema migration: add resource columns (idempotent)
        try:
            conn.execute("ALTER TABLE players ADD COLUMN resource INTEGER DEFAULT 5")
            conn.execute("ALTER TABLE players ADD COLUMN resource_max INTEGER DEFAULT 5")
            conn.commit()
        except Exception:
            pass  # Columns already exist

        # Schema migration: reveal system + spell names (idempotent)
        try:
            conn.execute("ALTER TABLE rooms ADD COLUMN reveal_gold INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE rooms ADD COLUMN reveal_lore TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass

        # Schema migration: npc_name for Floor 0 town rooms (idempotent)
        try:
            conn.execute("ALTER TABLE rooms ADD COLUMN npc_name TEXT")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE epoch ADD COLUMN spell_names TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS player_reveals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL REFERENCES players(id),
                room_id INTEGER NOT NULL REFERENCES rooms(id),
                revealed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(player_id, room_id)
            )""")
            conn.commit()
        except Exception:
            pass

        # Schema migration: broadcasts.dcrg_sent (migration 002)
        try:
            conn.execute("ALTER TABLE broadcasts ADD COLUMN dcrg_sent INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        # Schema migration: death log — Maren's memory (migration 010)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS death_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL REFERENCES players(id),
                floor INTEGER NOT NULL,
                monster_name TEXT NOT NULL,
                died_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_death_log_player ON death_log(player_id)")
            conn.commit()
        except Exception:
            pass

        # Schema migration: floor themes — epoch sub-themes (migration 011)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS floor_themes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                floor INTEGER NOT NULL,
                floor_name TEXT NOT NULL,
                atmosphere TEXT NOT NULL,
                narrative_beat TEXT NOT NULL,
                floor_transition TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_floor_themes_floor ON floor_themes(floor)")
            conn.commit()
        except Exception:
            pass

        # Schema migration: floor progress + boss gates (migration 012)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS floor_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL REFERENCES players(id),
                floor INTEGER NOT NULL,
                boss_killed INTEGER DEFAULT 0,
                boss_killed_at DATETIME,
                UNIQUE(player_id, floor)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_floor_progress_player ON floor_progress(player_id)")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE players ADD COLUMN deepest_floor_reached INTEGER DEFAULT 1")
            conn.commit()
        except Exception:
            pass

        # Schema migration: epoch announcements (migration 013)
        try:
            conn.execute("ALTER TABLE epoch ADD COLUMN announcements TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass

        # Schema migration: NPC persistent memory
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS npc_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL REFERENCES players(id),
                npc TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                turn_count INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(player_id, npc)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_npc_memory_player ON npc_memory(player_id, npc)")
            conn.commit()
        except Exception:
            pass

        # Schema migration: floor boss flag on monsters
        try:
            conn.execute("ALTER TABLE monsters ADD COLUMN is_floor_boss INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        # Schema migration: node_config.connection
        try:
            conn.execute("ALTER TABLE node_config ADD COLUMN connection TEXT")
            conn.commit()
        except Exception:
            pass

    def process_message(self, sender_id: str, sender_name: str, text: str) -> Optional[str]:
        """Process an inbound message and return a response.

        This is the entire game loop for one message.

        Args:
            sender_id: Meshtastic node ID of the sender.
            sender_name: Display name of the sender.
            text: Raw message text.

        Returns:
            Response string, or None if no response needed.
        """
        # Clear NPC DM queue from previous call
        self.npc_dm_queue.clear()

        # Parse command
        parsed = parse(text)
        if not parsed:
            return None

        # Look up or create player
        player = player_model.get_player_by_mesh_id(self.conn, sender_id)

        if not player:
            # New player — route to registration
            return self._handle_new_player(sender_id, sender_name, parsed)

        # Refresh player state
        player = player_model.get_player(self.conn, player["id"])

        # Accrue bard tokens on each interaction (checks internally if day changed)
        barkeep_sys.accrue_tokens(self.conn, player["id"])
        # Re-fetch after token accrual may have updated last_login
        player = player_model.get_player(self.conn, player["id"])

        # Execute action
        response = handle_action(self.conn, player, parsed.command, parsed.args)

        # Queue NPC greeting DM if this command triggers one
        self._maybe_queue_npc_dm(sender_id, player, parsed.command)

        # Prepend unseen tier 1 broadcasts
        news = broadcast_sys.deliver_unseen(self.conn, player["id"], limit=1)
        if news and response:
            combined = f"[{news}] {response}"
            if len(combined) <= 150:
                response = combined

        if response:
            logger.info(f"[{sender_name}] {parsed.command} → {response[:60]}...")

        return response

    def _handle_new_player(
        self, sender_id: str, sender_name: str, parsed: ParsedCommand
    ) -> str:
        """Handle messages from unregistered players.

        Registration flow:
        1. Any message → show class picker
        2. Player sends class choice → create character

        Uses a two-message flow:
        - First contact: "Welcome! Pick class: W)arrior C)aster R)ogue"
        - Second contact: class letter → character created
        """
        # Check if they're picking a class
        choice = parsed.raw.strip().lower()

        class_map = {
            "w": "warrior", "warrior": "warrior",
            "c": "caster", "caster": "caster",
            "r": "rogue", "rogue": "rogue",
        }

        if choice in class_map:
            cls = class_map[choice]
            account_id = player_model.get_or_create_account(
                self.conn, sender_id, sender_name
            )
            player = player_model.create_player(
                self.conn, account_id, sender_name, cls
            )
            stats = CLASSES[cls]
            return fmt(
                f"Welcome {sender_name} the {cls.title()}! "
                f"POW:{stats['POW']} DEF:{stats['DEF']} SPD:{stats['SPD']} "
                f"Move:N/S/E/W Fight:F Look:L Flee:FL Stats:ST Help:H"
            )

        # First contact — show class picker
        return fmt("Welcome to meshMUD! Pick class: W)arrior C)aster R)ogue")

    def _maybe_queue_npc_dm(
        self, sender_id: str, player: dict, command: str
    ) -> None:
        """Queue an NPC greeting DM if the command triggers one and cooldown allows.

        Note: `player` was fetched BEFORE handle_action, so player["town_location"]
        reflects the state before the action. If the player was already at the NPC's
        location, no transition occurred → no greeting.
        """
        if player["state"] != "town":
            return

        # Command-based NPC detection (existing)
        npc = COMMAND_NPC_DM_MAP.get(command)

        # Room-based NPC detection (Floor 0 movement)
        if not npc and command == "move":
            updated = player_model.get_player(self.conn, player["id"])
            if updated and updated.get("room_id"):
                room = world_data.get_room(self.conn, updated["room_id"])
                if room and room.get("npc_name"):
                    npc = room["npc_name"]

        if not npc:
            return

        # Don't re-greet if player was already at this NPC's location
        npc_locations = {"grist": "grist", "maren": "maren", "torval": "torval", "whisper": "whisper"}
        expected_loc = npc_locations.get(npc)
        if expected_loc and player.get("town_location") == expected_loc:
            return

        # Check cooldown
        now = time.monotonic()
        cooldown_key = (sender_id, npc)
        last_dm = self._npc_dm_cooldowns.get(cooldown_key, 0.0)
        if (now - last_dm) < NPC_GREETING_COOLDOWN:
            return

        # Queue the DM and set cooldown
        node = NPC_TO_NODE.get(npc)
        if node:
            self.npc_dm_queue.append((npc, sender_id))
            self._npc_dm_cooldowns[cooldown_key] = now
