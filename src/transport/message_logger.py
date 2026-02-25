"""
Message Logger for MMUD — Full Mesh Traffic Visibility.

Non-blocking write-through logger. A failed log write must NEVER
break game processing. Every call is wrapped in try/except.

Logs every message flowing through all 6 mesh nodes to the
message_log table for the Last Ember dashboard.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def log_message(
    conn: sqlite3.Connection,
    node: str,
    direction: str,
    message: str,
    message_type: str,
    sender_id: Optional[str] = None,
    sender_name: Optional[str] = None,
    recipient_id: Optional[str] = None,
    player_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Log a message to the message_log table.

    This function is non-blocking: if the write fails, it logs a warning
    and returns silently. It must NEVER raise an exception.

    Args:
        conn: Database connection.
        node: Which mesh node (EMBR, DCRG, GRST, MRN, TRVL, WSPR).
        direction: Message direction (inbound, outbound, system).
        message: The message text.
        message_type: One of the 16 defined types (command, response, etc.).
        sender_id: Meshtastic node ID of the sender.
        sender_name: Display name of the sender.
        recipient_id: Meshtastic node ID of the recipient (for DMs/targeted).
        player_id: Resolved player ID (NULL if unknown).
        metadata: Optional dict of extra data (serialized as JSON).
    """
    try:
        meta_json = json.dumps(metadata) if metadata is not None else None
        conn.execute(
            """INSERT INTO message_log
               (node, direction, sender_id, sender_name, recipient_id,
                message, message_type, player_id, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node,
                direction,
                sender_id,
                sender_name,
                recipient_id,
                message,
                message_type,
                player_id,
                meta_json,
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to log message ({message_type}): {e}")


def prune_old_logs(conn: sqlite3.Connection, retention_days: int) -> int:
    """Delete log entries older than retention_days.

    Returns count deleted. Non-blocking: failures are logged and swallowed.
    """
    try:
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            "DELETE FROM message_log WHERE timestamp < ?", (cutoff_str,)
        )
        conn.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Pruned {deleted} log entries older than {retention_days} days")
        return deleted
    except Exception as e:
        logger.warning(f"Failed to prune message logs: {e}")
        return 0
