"""
Breach system for MMUD.
Handles day-15 activation and breach state management.

The breach zone connects floors 2-3 and hosts a mini-event:
  - heist: Mini Retrieve & Escape
  - emergence: Mini Raid Boss (shared HP pool)
  - incursion: Mini Hold the Line (rooms revert over time)
  - resonance: Puzzle dungeon (no combat focus)
"""

import sqlite3
from typing import Optional

from config import (
    BREACH_DAY,
    INCURSION_REGEN_ROOMS_PER_DAY,
    MSG_CHAR_LIMIT,
)
from src.models.epoch import get_epoch


def is_breach_open(conn: sqlite3.Connection) -> bool:
    """Check if the breach is currently open."""
    epoch = get_epoch(conn)
    return bool(epoch and epoch["breach_open"])


def get_breach_state(conn: sqlite3.Connection) -> Optional[dict]:
    """Get current breach state."""
    row = conn.execute("SELECT * FROM breach WHERE id = 1").fetchone()
    return dict(row) if row else None


def get_breach_rooms(conn: sqlite3.Connection) -> list[dict]:
    """Get all breach zone rooms."""
    rows = conn.execute(
        "SELECT id, floor, name, description FROM rooms WHERE is_breach = 1"
    ).fetchall()
    return [dict(r) for r in rows]


def can_enter_breach(conn: sqlite3.Connection, player_id: int) -> tuple[bool, str]:
    """Check if a player can enter the breach zone.

    Returns:
        (can_enter, reason)
    """
    if not is_breach_open(conn):
        epoch = get_epoch(conn)
        if epoch:
            days_left = BREACH_DAY - epoch["day_number"]
            if days_left > 0:
                return False, f"Breach sealed. {days_left} days remain."
        return False, "The Breach is sealed."

    breach = get_breach_state(conn)
    if not breach:
        return False, "No breach zone exists."

    if breach["completed"]:
        return False, "The Breach event is complete."

    return True, "The Breach awaits."


def apply_incursion_tick(conn: sqlite3.Connection) -> int:
    """For incursion events: revert rooms back to uncleared.

    Called daily. Returns count of rooms reverted.
    """
    breach = get_breach_state(conn)
    if not breach or breach["mini_event"] != "incursion":
        return 0

    if breach["completed"]:
        return 0

    # Get breach rooms that are cleared (htl_cleared = 1)
    cleared = conn.execute(
        """SELECT id FROM rooms
           WHERE is_breach = 1 AND htl_cleared = 1
           ORDER BY RANDOM()
           LIMIT ?""",
        (INCURSION_REGEN_ROOMS_PER_DAY,),
    ).fetchall()

    for room in cleared:
        conn.execute(
            "UPDATE rooms SET htl_cleared = 0, htl_cleared_at = NULL WHERE id = ?",
            (room["id"],),
        )

    conn.commit()
    return len(cleared)


def check_breach_completion(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check if the breach mini-event has been completed.

    Returns:
        (completed, message)
    """
    breach = get_breach_state(conn)
    if not breach or breach["completed"]:
        return False, ""

    event = breach["mini_event"]

    if event == "emergence":
        # Check if emergence boss HP is 0
        if breach["emergence_hp"] is not None and breach["emergence_hp"] <= 0:
            _complete_breach(conn)
            return True, "The emergence has been defeated!"

    elif event == "heist":
        # Check if artifact has been extracted (carrier reached exit)
        # This is handled by the game engine during movement
        pass

    elif event == "incursion":
        # Check if all breach rooms held for the required duration
        # Tracked via incursion_hold_started_at
        if breach["incursion_hold_started_at"]:
            return False, "Hold the breach rooms!"

    elif event == "resonance":
        # Check if all breach secrets discovered
        breach_rooms = conn.execute(
            "SELECT id FROM rooms WHERE is_breach = 1"
        ).fetchall()
        breach_ids = [r["id"] for r in breach_rooms]

        if breach_ids:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM secrets WHERE type = 'breach'"
            ).fetchone()
            found = conn.execute(
                "SELECT COUNT(*) as cnt FROM secrets WHERE type = 'breach' AND discovered_by IS NOT NULL"
            ).fetchone()
            if total["cnt"] > 0 and found["cnt"] >= total["cnt"]:
                _complete_breach(conn)
                return True, "All breach secrets uncovered!"

    return False, ""


def _complete_breach(conn: sqlite3.Connection) -> None:
    """Mark the breach event as completed."""
    conn.execute(
        "UPDATE breach SET completed = 1, completed_at = datetime('now') WHERE id = 1"
    )
    msg = "THE BREACH EVENT IS COMPLETE. Well done, delvers."
    conn.execute(
        "INSERT INTO broadcasts (tier, message) VALUES (1, ?)",
        (msg[:MSG_CHAR_LIMIT],),
    )
    conn.commit()
