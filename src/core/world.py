"""
World state management for MMUD core engine.
Handles room transitions, dungeon entry/exit, floor navigation.
"""

import sqlite3
from typing import Optional

from config import BOSS_GATE_ENABLED, FAST_TRAVEL_ENABLED, NUM_FLOORS, RESOURCE_REGEN_TOWN
from src.models import world as world_model


def _get_floor_transition(conn: sqlite3.Connection, floor: int) -> Optional[str]:
    """Look up the floor transition text for a given floor.

    Returns None if no floor themes exist (backward compat).
    """
    try:
        row = conn.execute(
            "SELECT floor_transition FROM floor_themes WHERE floor = ?", (floor,)
        ).fetchone()
        return row["floor_transition"] if row else None
    except Exception:
        return None


def _check_boss_gate(conn: sqlite3.Connection, player_id: int, floor: int) -> bool:
    """Check if player has unlocked passage past this floor.

    Per-player visit: boss must be dead AND player must have visited boss room.
    """
    row = conn.execute(
        "SELECT boss_killed FROM floor_progress WHERE player_id = ? AND floor = ?",
        (player_id, floor),
    ).fetchone()
    return row is not None and row["boss_killed"] == 1


def enter_dungeon(conn: sqlite3.Connection, player: dict, target_floor: int = 0) -> Optional[dict]:
    """Enter the dungeon from town. Places player in target floor hub.

    Requires the player to be at the town center room (bar).
    If target_floor > 1, validates against deepest_floor_reached for fast travel.

    Args:
        conn: Database connection.
        player: Player dict.
        target_floor: Floor to enter (0 = default floor 1).

    Returns:
        Hub room dict, or None if no hub exists, not at center, or floor locked.
    """
    # Check player is at the dungeon entrance (town center)
    center = world_model.get_hub_room(conn, floor=0)
    if center and player.get("room_id") and player["room_id"] != center["id"]:
        return None

    # Determine which floor to enter
    floor = target_floor if target_floor > 0 else 1

    # Fast travel validation
    if floor > 1 and FAST_TRAVEL_ENABLED:
        deepest = player.get("deepest_floor_reached", 1) or 1
        if floor > deepest or floor > NUM_FLOORS:
            return None

    hub = world_model.get_hub_room(conn, floor=floor)
    if not hub:
        return None

    conn.execute(
        """UPDATE players SET
           state = 'dungeon', floor = ?, room_id = ?,
           town_location = NULL
           WHERE id = ?""",
        (floor, hub["id"], player["id"]),
    )
    conn.commit()
    return hub


def move_player(
    conn: sqlite3.Connection, player: dict, direction: str
) -> tuple[Optional[dict], str]:
    """Move a player in a direction.

    Args:
        conn: Database connection.
        player: Player dict (must be in dungeon or town state).
        direction: Direction code (n, s, e, w, u, d).

    Returns:
        Tuple of (new_room or None, error_message).
    """
    if player["state"] not in ("dungeon", "town"):
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
    old_floor = player.get("floor", 0)

    # Boss gate check: can't descend to next floor unless boss gate unlocked
    if BOSS_GATE_ENABLED and new_floor > old_floor and old_floor > 0:
        if not _check_boss_gate(conn, player["id"], old_floor):
            return None, "The way is sealed. Defeat the floor boss."

    conn.execute(
        """UPDATE players SET
           room_id = ?, floor = ?
           WHERE id = ?""",
        (target_id, new_floor, player["id"]),
    )
    conn.commit()

    # Attach floor transition text if crossing floors into dungeon
    old_floor = player.get("floor", 0)
    if new_floor != old_floor and new_floor > 0:
        transition = _get_floor_transition(conn, new_floor)
        if transition:
            # Return a mutable dict copy with transition attached
            room_dict = dict(target_room)
            room_dict["_floor_transition"] = transition
            return room_dict, ""

    return target_room, ""


def return_to_town(conn: sqlite3.Connection, player_id: int) -> None:
    """Return a player to town center. Restores some resource."""
    center = world_model.get_hub_room(conn, floor=0)
    center_id = center["id"] if center else None
    conn.execute(
        """UPDATE players SET
           state = 'town', floor = 0, room_id = ?,
           combat_monster_id = NULL, town_location = NULL,
           resource = MIN(resource + ?, resource_max)
           WHERE id = ?""",
        (center_id, RESOURCE_REGEN_TOWN, player_id),
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
