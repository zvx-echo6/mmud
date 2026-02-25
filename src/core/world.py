"""
World state management for MMUD core engine.
Handles room transitions, dungeon entry/exit, floor navigation.
"""

import sqlite3
from typing import Optional

from src.models import world as world_model


def enter_dungeon(conn: sqlite3.Connection, player: dict) -> Optional[dict]:
    """Enter the dungeon from town. Places player in floor 1 hub.

    Args:
        conn: Database connection.
        player: Player dict.

    Returns:
        Hub room dict, or None if no floor 1 hub exists.
    """
    hub = world_model.get_hub_room(conn, floor=1)
    if not hub:
        return None

    conn.execute(
        """UPDATE players SET
           state = 'dungeon', floor = 1, room_id = ?
           WHERE id = ?""",
        (hub["id"], player["id"]),
    )
    conn.commit()
    return hub


def move_player(
    conn: sqlite3.Connection, player: dict, direction: str
) -> tuple[Optional[dict], str]:
    """Move a player in a direction.

    Args:
        conn: Database connection.
        player: Player dict (must be in dungeon state).
        direction: Direction code (n, s, e, w, u, d).

    Returns:
        Tuple of (new_room or None, error_message).
    """
    if player["state"] != "dungeon":
        return None, "You can't move right now."

    room_id = player["room_id"]
    if not room_id:
        return None, "You're not in a room."

    target_id = world_model.get_exit_target(conn, room_id, direction)
    if not target_id:
        return None, "No exit that way."

    target_room = world_model.get_room(conn, target_id)
    if not target_room:
        return None, "That room doesn't exist."

    new_floor = target_room["floor"]
    conn.execute(
        """UPDATE players SET
           room_id = ?, floor = ?
           WHERE id = ?""",
        (target_id, new_floor, player["id"]),
    )
    conn.commit()
    return target_room, ""


def return_to_town(conn: sqlite3.Connection, player_id: int) -> None:
    """Return a player to town."""
    conn.execute(
        """UPDATE players SET
           state = 'town', floor = 0, room_id = NULL, combat_monster_id = NULL
           WHERE id = ?""",
        (player_id,),
    )
    conn.commit()


def enter_combat(
    conn: sqlite3.Connection, player_id: int, monster_id: int
) -> None:
    """Put a player into combat state with a monster."""
    conn.execute(
        """UPDATE players SET
           state = 'combat', combat_monster_id = ?
           WHERE id = ?""",
        (monster_id, player_id),
    )
    conn.commit()


def exit_combat(conn: sqlite3.Connection, player_id: int) -> None:
    """Return a player from combat to dungeon state."""
    conn.execute(
        """UPDATE players SET
           state = 'dungeon', combat_monster_id = NULL
           WHERE id = ?""",
        (player_id,),
    )
    conn.commit()
