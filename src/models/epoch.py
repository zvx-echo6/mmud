"""
Epoch state management for MMUD.
Tracks current epoch number, day, mode, and breach state.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import EPOCH_DAYS


def create_epoch(
    conn: sqlite3.Connection,
    epoch_number: int,
    endgame_mode: str,
    breach_type: str,
    narrative_theme: str = "",
) -> None:
    """Initialize the epoch state row.

    Args:
        conn: Database connection.
        epoch_number: Sequential epoch number.
        endgame_mode: Selected endgame mode.
        breach_type: Random breach mini-event type.
        narrative_theme: LLM-generated theme name.
    """
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=EPOCH_DAYS)

    conn.execute(
        """INSERT OR REPLACE INTO epoch
           (id, epoch_number, start_date, end_date, endgame_mode,
            breach_type, narrative_theme, day_number)
           VALUES (1, ?, ?, ?, ?, ?, ?, 1)""",
        (
            epoch_number,
            start.isoformat(),
            end.isoformat(),
            endgame_mode,
            breach_type,
            narrative_theme,
        ),
    )
    conn.commit()


def get_epoch(conn: sqlite3.Connection) -> Optional[dict]:
    """Get current epoch state."""
    row = conn.execute("SELECT * FROM epoch WHERE id = 1").fetchone()
    return dict(row) if row else None


def advance_day(conn: sqlite3.Connection) -> int:
    """Advance the epoch day counter. Returns the new day number."""
    conn.execute(
        "UPDATE epoch SET day_number = day_number + 1 WHERE id = 1"
    )
    conn.commit()
    epoch = get_epoch(conn)
    return epoch["day_number"] if epoch else 0
