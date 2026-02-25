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
        - First contact: "Welcome! Pick class: W)arrior G)uardian S)cout"
        - Second contact: class letter → character created
        """
        # Check if they're picking a class
        choice = parsed.raw.strip().lower()

        class_map = {
            "w": "warrior", "warrior": "warrior",
            "g": "guardian", "guardian": "guardian",
            "s": "scout", "scout": "scout",
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
        return fmt("Welcome to meshMUD! Pick class: W)arrior G)uardian S)cout")

    def _maybe_queue_npc_dm(
        self, sender_id: str, player: dict, command: str
    ) -> None:
        """Queue an NPC greeting DM if the command triggers one and cooldown allows."""
        if player["state"] != "town":
            return

        npc = COMMAND_NPC_DM_MAP.get(command)
        if not npc:
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
