"""
Broadcast Drain for MMUD — DCRG Outbound.

Periodically queries the broadcasts table for unsent broadcasts
(dcrg_sent = 0), sends them via the DCRG MeshTransport node,
and marks them as sent.

Tier handling:
  - Tier 1 (immediate): Sent as channel broadcast on DCRG.
  - Tier 2 (batched): Sent as channel broadcast on DCRG.
  - Targeted: Sent as DMs from DCRG to qualifying players.

Rate limiting prevents flooding the mesh network.
"""

import logging
import sqlite3
import time
from typing import Optional

from config import (
    BROADCAST_DRAIN_BATCH_SIZE,
    BROADCAST_DRAIN_INTERVAL,
    BROADCAST_DRAIN_RATE_LIMIT,
    LLM_OUTPUT_CHAR_LIMIT,
)
from src.transport.meshtastic import MeshTransport
from src.transport.message_logger import log_message

logger = logging.getLogger(__name__)


class BroadcastDrain:
    """Drains unsent broadcasts from the DB and sends them via DCRG.

    Designed to run as a background loop in the main daemon.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        dcrg_transport: Optional[MeshTransport] = None,
        rate_limit: float = BROADCAST_DRAIN_RATE_LIMIT,
    ):
        """Initialize the drain.

        Args:
            conn: Database connection.
            dcrg_transport: The DCRG MeshTransport (can be set later).
            rate_limit: Minimum seconds between sends (0 to disable).
        """
        self.conn = conn
        self.dcrg_transport = dcrg_transport
        self.rate_limit = rate_limit
        self._last_send_time: float = 0.0

    def set_transport(self, transport: MeshTransport) -> None:
        """Set or replace the DCRG transport."""
        self.dcrg_transport = transport

    def drain_once(self) -> int:
        """Run one drain cycle. Returns count of broadcasts sent.

        Queries up to BROADCAST_DRAIN_BATCH_SIZE unsent broadcasts,
        sends them via DCRG, and marks them as dcrg_sent = 1.
        """
        if not self.dcrg_transport:
            return 0

        # Get unsent broadcasts, tier 1 first
        rows = self.conn.execute(
            """SELECT id, tier, targeted, target_condition, message
               FROM broadcasts
               WHERE dcrg_sent = 0
               ORDER BY tier ASC, created_at ASC
               LIMIT ?""",
            (BROADCAST_DRAIN_BATCH_SIZE,),
        ).fetchall()

        sent = 0
        for row in rows:
            # Rate limit
            if self.rate_limit > 0:
                now = time.monotonic()
                elapsed = now - self._last_send_time
                if elapsed < self.rate_limit:
                    wait = self.rate_limit - elapsed
                    time.sleep(wait)

            try:
                if row["targeted"]:
                    self._send_targeted(row)
                else:
                    self._send_broadcast(row)

                # Mark as sent
                self.conn.execute(
                    "UPDATE broadcasts SET dcrg_sent = 1 WHERE id = ?",
                    (row["id"],),
                )
                self.conn.commit()
                self._last_send_time = time.monotonic()
                sent += 1

            except Exception as e:
                logger.error(f"Failed to send broadcast {row['id']}: {e}")

        return sent

    def _send_broadcast(self, row: sqlite3.Row) -> None:
        """Send a non-targeted broadcast as a channel message."""
        message = row["message"][:LLM_OUTPUT_CHAR_LIMIT]
        tier = row["tier"]
        msg_type = f"broadcast_tier{tier}"
        logger.info(f"DCRG broadcast (tier {tier}): {message[:60]}...")
        self.dcrg_transport.send_broadcast(message)
        log_message(
            self.conn, "DCRG", "outbound", message, msg_type,
            metadata={"broadcast_id": row["id"], "tier": tier},
        )

    def _send_targeted(self, row: sqlite3.Row) -> None:
        """Send a targeted broadcast as DMs to qualifying players.

        target_condition is a JSON string with conditions.
        For now, supports simple conditions like {"floor": N} or
        {"room_id": N} to match players who have visited specific locations.
        """
        import json

        message = row["message"][:LLM_OUTPUT_CHAR_LIMIT]
        condition = row["target_condition"]

        if not condition:
            # No condition — send as regular broadcast
            self.dcrg_transport.send_broadcast(message)
            log_message(
                self.conn, "DCRG", "outbound", message, "broadcast_targeted",
                metadata={"broadcast_id": row["id"], "condition": None},
            )
            return

        try:
            cond = json.loads(condition)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Invalid target_condition for broadcast {row['id']}")
            return

        # Find qualifying players
        players = self._find_qualifying_players(cond)
        for player in players:
            mesh_id = player["mesh_id"]
            logger.debug(f"DCRG targeted DM to {mesh_id}: {message[:40]}...")
            self.dcrg_transport.send_dm(mesh_id, message)
            log_message(
                self.conn, "DCRG", "outbound", message, "broadcast_targeted",
                recipient_id=mesh_id,
                metadata={"broadcast_id": row["id"], "condition": condition},
            )

    def _find_qualifying_players(self, condition: dict) -> list[dict]:
        """Find players matching a target condition."""
        if "floor" in condition:
            rows = self.conn.execute(
                """SELECT a.mesh_id FROM players p
                   JOIN accounts a ON p.account_id = a.id
                   WHERE p.floor = ?""",
                (condition["floor"],),
            ).fetchall()
            return [dict(r) for r in rows]

        if "room_id" in condition:
            rows = self.conn.execute(
                """SELECT a.mesh_id FROM players p
                   JOIN accounts a ON p.account_id = a.id
                   WHERE p.room_id = ?""",
                (condition["room_id"],),
            ).fetchall()
            return [dict(r) for r in rows]

        if "secret_progress" in condition:
            rows = self.conn.execute(
                """SELECT a.mesh_id FROM secret_progress sp
                   JOIN players p ON sp.player_id = p.id
                   JOIN accounts a ON p.account_id = a.id
                   WHERE sp.secret_id = ? AND sp.found = 1""",
                (condition["secret_progress"],),
            ).fetchall()
            return [dict(r) for r in rows]

        return []

    def get_pending_count(self) -> int:
        """Get the count of unsent broadcasts."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM broadcasts WHERE dcrg_sent = 0"
        ).fetchone()
        return row["cnt"] if row else 0
