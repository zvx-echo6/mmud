"""
Breach Incursion — Mini Hold the Line within the Breach zone.

Clear all breach rooms, hold them all for 48 hours.
Rooms revert at 2/day. Timer resets if any room reverts.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import (
    INCURSION_HOLD_HOURS,
    INCURSION_REGEN_ROOMS_PER_DAY,
    MSG_CHAR_LIMIT,
)
from src.systems import broadcast as broadcast_sys


# ── State Helpers ─────────────────────────────────────────────────────────


def get_incursion_state(conn: sqlite3.Connection) -> Optional[dict]:
    """Get current incursion state from breach table."""
    row = conn.execute(
        """SELECT incursion_hold_started_at,
                  active, completed, mini_event
           FROM breach WHERE id = 1"""
    ).fetchone()
    if not row or row["mini_event"] != "incursion":
        return None
    return dict(row)


# ── Room Clearing ─────────────────────────────────────────────────────────


def clear_breach_room(
    conn: sqlite3.Connection, room_id: int, player_id: int
) -> dict:
    """Clear a breach room for incursion mode.

    Returns:
        Status dict with cleared, all_clear, hold_started.
    """
    result = {"cleared": False, "all_clear": False, "hold_started": False}

    room = conn.execute(
        "SELECT is_breach, htl_cleared FROM rooms WHERE id = ?", (room_id,)
    ).fetchone()
    if not room or not room["is_breach"]:
        return result

    if room["htl_cleared"]:
        return result  # Already cleared

    conn.execute(
        "UPDATE rooms SET htl_cleared = 1, htl_cleared_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), room_id),
    )
    result["cleared"] = True

    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"
    broadcast_sys.create_broadcast(
        conn, 2,
        f"{name} secured a Breach room."[:MSG_CHAR_LIMIT],
    )

    # Check if all breach rooms are now cleared
    if _all_breach_rooms_cleared(conn):
        result["all_clear"] = True
        # Start or keep the hold timer
        state = get_incursion_state(conn)
        if state and not state["incursion_hold_started_at"]:
            conn.execute(
                "UPDATE breach SET incursion_hold_started_at = ? WHERE id = 1",
                (datetime.now(timezone.utc).isoformat(),),
            )
            result["hold_started"] = True
            broadcast_sys.create_broadcast(
                conn, 1,
                f"All Breach rooms secured! Hold for {INCURSION_HOLD_HOURS}h."[:MSG_CHAR_LIMIT],
            )

    conn.commit()
    return result


def _all_breach_rooms_cleared(conn: sqlite3.Connection) -> bool:
    """Check if every breach room is cleared."""
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_breach = 1"
    ).fetchone()["cnt"]
    cleared = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_breach = 1 AND htl_cleared = 1"
    ).fetchone()["cnt"]
    return total > 0 and cleared >= total


# ── Regen Tick ────────────────────────────────────────────────────────────


def apply_incursion_regen(conn: sqlite3.Connection) -> dict:
    """Revert breach rooms daily. Resets hold timer if any reverted.

    Returns:
        Dict with reverted count and timer_reset flag.
    """
    result = {"reverted": 0, "timer_reset": False}

    state = get_incursion_state(conn)
    if not state or state["completed"]:
        return result

    # Pick cleared breach rooms to revert
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
        # Respawn a minion
        _respawn_breach_monster(conn, room["id"])

    result["reverted"] = len(cleared)

    # If any rooms reverted and hold timer was running, reset it
    if len(cleared) > 0 and state["incursion_hold_started_at"]:
        conn.execute(
            "UPDATE breach SET incursion_hold_started_at = NULL WHERE id = 1"
        )
        result["timer_reset"] = True
        broadcast_sys.create_broadcast(
            conn, 1,
            f"Breach rooms lost! Hold timer reset. {len(cleared)} rooms reclaimed."[:MSG_CHAR_LIMIT],
        )

    conn.commit()
    return result


def _respawn_breach_monster(conn: sqlite3.Connection, room_id: int) -> None:
    """Respawn a breach creature in a reverted room."""
    conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (?, 'Rift Spawn', 25, 25, 7, 5, 5, 12, 4, 12, 3)""",
        (room_id,),
    )


# ── Hold Timer Check ─────────────────────────────────────────────────────


def check_incursion_hold(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check if the 48-hour hold timer has completed.

    Returns:
        (completed, message)
    """
    state = get_incursion_state(conn)
    if not state or state["completed"]:
        return False, ""

    if not state["incursion_hold_started_at"]:
        return False, ""

    # Check all rooms still cleared
    if not _all_breach_rooms_cleared(conn):
        return False, ""

    # Check if hold timer has elapsed
    try:
        started = datetime.fromisoformat(state["incursion_hold_started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False, ""

    elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 3600

    if elapsed >= INCURSION_HOLD_HOURS:
        conn.execute(
            "UPDATE breach SET completed = 1, completed_at = datetime('now') WHERE id = 1"
        )
        msg = "The Breach is secured! The incursion is contained."
        broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
        conn.commit()
        return True, msg

    hours_left = INCURSION_HOLD_HOURS - elapsed
    return False, f"Hold: {hours_left:.0f}h remaining."


# ── Status Display ────────────────────────────────────────────────────────


def get_breach_room_status(conn: sqlite3.Connection) -> dict:
    """Get breach room clear/total counts."""
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_breach = 1"
    ).fetchone()["cnt"]
    cleared = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_breach = 1 AND htl_cleared = 1"
    ).fetchone()["cnt"]
    return {"cleared": cleared, "total": total}


def format_incursion_status(conn: sqlite3.Connection) -> str:
    """Format incursion status for display."""
    state = get_incursion_state(conn)
    if not state:
        return "No incursion active."

    if state["completed"]:
        return "The Breach has been secured."

    status = get_breach_room_status(conn)
    msg = f"Incursion: {status['cleared']}/{status['total']} rooms held"

    if state["incursion_hold_started_at"]:
        try:
            started = datetime.fromisoformat(state["incursion_hold_started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 3600
            hours_left = max(0, INCURSION_HOLD_HOURS - elapsed)
            msg += f" ({hours_left:.0f}h left)"
        except (ValueError, TypeError):
            pass

    return msg
