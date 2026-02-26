"""
World state management for MMUD.
Rooms, monsters, items — all generated at epoch start, read at runtime.
"""

import random
import sqlite3
from typing import Optional


def get_room(conn: sqlite3.Connection, room_id: int) -> Optional[dict]:
    """Get a room by ID."""
    row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    return dict(row) if row else None


def get_room_exits(conn: sqlite3.Connection, room_id: int) -> list[dict]:
    """Get all exits from a room.

    Returns:
        List of dicts with 'direction' and 'to_room_id'.
    """
    rows = conn.execute(
        "SELECT direction, to_room_id FROM room_exits WHERE from_room_id = ?",
        (room_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_exit_target(
    conn: sqlite3.Connection, from_room_id: int, direction: str
) -> Optional[int]:
    """Get the room ID in a given direction from a room.

    Returns:
        Target room_id or None if no exit in that direction.
    """
    row = conn.execute(
        "SELECT to_room_id FROM room_exits WHERE from_room_id = ? AND direction = ?",
        (from_room_id, direction),
    ).fetchone()
    return row["to_room_id"] if row else None


def get_room_monster(conn: sqlite3.Connection, room_id: int) -> Optional[dict]:
    """Get the living monster in a room (if any).

    Returns the first monster with hp > 0 in the room.
    """
    row = conn.execute(
        "SELECT * FROM monsters WHERE room_id = ? AND hp > 0 ORDER BY id LIMIT 1",
        (room_id,),
    ).fetchone()
    return dict(row) if row else None


def get_monster(conn: sqlite3.Connection, monster_id: int) -> Optional[dict]:
    """Get a monster by ID."""
    row = conn.execute(
        "SELECT * FROM monsters WHERE id = ?", (monster_id,)
    ).fetchone()
    return dict(row) if row else None


def damage_monster(
    conn: sqlite3.Connection, monster_id: int, damage: int
) -> dict:
    """Apply damage to a monster. Returns updated monster dict.

    Uses atomic UPDATE to handle concurrent access.
    """
    conn.execute(
        "UPDATE monsters SET hp = MAX(0, hp - ?) WHERE id = ?",
        (damage, monster_id),
    )
    conn.commit()
    return get_monster(conn, monster_id)


def get_hub_room(conn: sqlite3.Connection, floor: int) -> Optional[dict]:
    """Get the hub room for a given floor."""
    row = conn.execute(
        "SELECT * FROM rooms WHERE floor = ? AND is_hub = 1 LIMIT 1",
        (floor,),
    ).fetchone()
    return dict(row) if row else None


def get_floor_rooms(conn: sqlite3.Connection, floor: int) -> list[dict]:
    """Get all rooms on a floor."""
    rows = conn.execute(
        "SELECT * FROM rooms WHERE floor = ? ORDER BY id",
        (floor,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_stairway_room(conn: sqlite3.Connection, floor: int) -> Optional[dict]:
    """Get the stairway room on a floor (connects to next floor)."""
    row = conn.execute(
        "SELECT * FROM rooms WHERE floor = ? AND is_stairway = 1 LIMIT 1",
        (floor,),
    ).fetchone()
    return dict(row) if row else None


def has_player_revealed(
    conn: sqlite3.Connection, player_id: int, room_id: int
) -> bool:
    """Check if a player has already revealed this room."""
    row = conn.execute(
        "SELECT 1 FROM player_reveals WHERE player_id = ? AND room_id = ?",
        (player_id, room_id),
    ).fetchone()
    return row is not None


def record_player_reveal(
    conn: sqlite3.Connection, player_id: int, room_id: int
) -> None:
    """Record that a player has revealed a room."""
    conn.execute(
        "INSERT OR IGNORE INTO player_reveals (player_id, room_id) VALUES (?, ?)",
        (player_id, room_id),
    )
    conn.commit()
