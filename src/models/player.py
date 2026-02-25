"""
Player state management for MMUD.
One row per player. No joins on the hot path.
"""

import math
import random
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import (
    CLASSES,
    DEATH_GOLD_LOSS_PERCENT,
    DUNGEON_ACTIONS_PER_DAY,
    MAX_LEVEL,
    SOCIAL_ACTIONS_PER_DAY,
    SPECIAL_ACTIONS_PER_DAY,
    XP_PER_LEVEL,
)


# Base HP by class at level 1
BASE_HP = {
    "warrior": 50,
    "guardian": 45,
    "scout": 40,
}


def get_or_create_account(conn: sqlite3.Connection, mesh_id: str, handle: str) -> int:
    """Get or create a persistent account.

    Args:
        conn: Database connection.
        mesh_id: Meshtastic node ID.
        handle: Player display name.

    Returns:
        account_id (integer primary key).
    """
    row = conn.execute(
        "SELECT id FROM accounts WHERE mesh_id = ?", (mesh_id,)
    ).fetchone()
    if row:
        return row["id"]

    cursor = conn.execute(
        "INSERT INTO accounts (mesh_id, handle) VALUES (?, ?)",
        (mesh_id, handle),
    )
    conn.commit()
    return cursor.lastrowid


def create_player(
    conn: sqlite3.Connection,
    account_id: int,
    name: str,
    cls: str,
) -> dict:
    """Create a new player for the current epoch.

    Args:
        conn: Database connection.
        account_id: Account ID from accounts table.
        name: Character name.
        cls: Class name (warrior, guardian, scout).

    Returns:
        Player dict from the database.
    """
    cls = cls.lower()
    if cls not in CLASSES:
        raise ValueError(f"Unknown class: {cls}. Choose from: {', '.join(CLASSES)}")

    stats = CLASSES[cls]
    hp = BASE_HP.get(cls, 45)

    cursor = conn.execute(
        """INSERT INTO players
           (account_id, name, class, hp, hp_max, pow, def, spd,
            dungeon_actions_remaining, social_actions_remaining,
            special_actions_remaining, last_login)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            account_id, name, cls, hp, hp,
            stats["POW"], stats["DEF"], stats["SPD"],
            DUNGEON_ACTIONS_PER_DAY,
            SOCIAL_ACTIONS_PER_DAY,
            SPECIAL_ACTIONS_PER_DAY,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return get_player(conn, cursor.lastrowid)


def get_player(conn: sqlite3.Connection, player_id: int) -> Optional[dict]:
    """Get a player by ID."""
    row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    return dict(row) if row else None


def get_player_by_mesh_id(conn: sqlite3.Connection, mesh_id: str) -> Optional[dict]:
    """Get a player by their Meshtastic node ID (via account).

    Returns the player for the current epoch, or None if not registered.
    """
    row = conn.execute(
        """SELECT p.* FROM players p
           JOIN accounts a ON p.account_id = a.id
           WHERE a.mesh_id = ?
           ORDER BY p.created_at DESC LIMIT 1""",
        (mesh_id,),
    ).fetchone()
    return dict(row) if row else None


def get_account_by_mesh_id(conn: sqlite3.Connection, mesh_id: str) -> Optional[dict]:
    """Get an account by Meshtastic node ID."""
    row = conn.execute(
        "SELECT * FROM accounts WHERE mesh_id = ?", (mesh_id,)
    ).fetchone()
    return dict(row) if row else None


def update_state(conn: sqlite3.Connection, player_id: int, **fields) -> None:
    """Update arbitrary player fields.

    Args:
        conn: Database connection.
        player_id: Player ID.
        **fields: Column name → value pairs to update.
    """
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [player_id]
    conn.execute(f"UPDATE players SET {set_clause} WHERE id = ?", values)
    conn.commit()


def use_dungeon_action(conn: sqlite3.Connection, player_id: int) -> bool:
    """Decrement dungeon action budget. Returns False if none remain."""
    row = conn.execute(
        "SELECT dungeon_actions_remaining FROM players WHERE id = ?",
        (player_id,),
    ).fetchone()
    if not row or row["dungeon_actions_remaining"] <= 0:
        return False
    conn.execute(
        "UPDATE players SET dungeon_actions_remaining = dungeon_actions_remaining - 1 WHERE id = ?",
        (player_id,),
    )
    conn.commit()
    return True


def apply_death(conn: sqlite3.Connection, player_id: int) -> dict:
    """Apply death penalties and return loss details.

    Penalties:
    - Lose DEATH_GOLD_LOSS_PERCENT% of carried gold
    - Lose 15% of current XP
    - Lose 1 daily dungeon action
    - Respawn in town at 50% HP
    - State → 'town', floor → 0

    Returns:
        Dict with gold_lost and xp_lost.
    """
    player = get_player(conn, player_id)
    if not player:
        return {"gold_lost": 0, "xp_lost": 0}

    gold_lost = math.floor(player["gold_carried"] * DEATH_GOLD_LOSS_PERCENT / 100)
    xp_lost = math.floor(player["xp"] * 0.15)
    new_hp = max(1, player["hp_max"] // 2)
    new_actions = max(0, player["dungeon_actions_remaining"] - 1)

    conn.execute(
        """UPDATE players SET
           gold_carried = gold_carried - ?,
           xp = xp - ?,
           hp = ?,
           state = 'town',
           floor = 0,
           room_id = NULL,
           combat_monster_id = NULL,
           dungeon_actions_remaining = ?
           WHERE id = ?""",
        (gold_lost, xp_lost, new_hp, new_actions, player_id),
    )
    conn.commit()
    return {"gold_lost": gold_lost, "xp_lost": xp_lost}


def award_xp(conn: sqlite3.Connection, player_id: int, xp: int) -> Optional[int]:
    """Award XP and check for level up.

    Args:
        conn: Database connection.
        player_id: Player ID.
        xp: XP to award.

    Returns:
        New level if leveled up, None otherwise.
    """
    player = get_player(conn, player_id)
    if not player:
        return None

    new_xp = player["xp"] + xp
    current_level = player["level"]

    # Check for level up
    new_level = current_level
    for lvl in range(current_level, MAX_LEVEL):
        if lvl < len(XP_PER_LEVEL) and new_xp >= XP_PER_LEVEL[lvl]:
            new_level = lvl + 1
        else:
            break

    updates = {"xp": new_xp}
    if new_level > current_level:
        # Level up: increase HP max by 5 per level gained
        hp_gain = (new_level - current_level) * 5
        updates["level"] = new_level
        updates["hp_max"] = player["hp_max"] + hp_gain
        updates["hp"] = min(player["hp"] + hp_gain, player["hp_max"] + hp_gain)

    update_state(conn, player_id, **updates)
    return new_level if new_level > current_level else None


def award_gold(conn: sqlite3.Connection, player_id: int, gold: int) -> None:
    """Award gold to a player's carried stash."""
    conn.execute(
        "UPDATE players SET gold_carried = gold_carried + ? WHERE id = ?",
        (gold, player_id),
    )
    conn.commit()


def reset_daily_actions(conn: sqlite3.Connection) -> None:
    """Reset all players' daily action budgets. Called at day rollover."""
    conn.execute(
        """UPDATE players SET
           dungeon_actions_remaining = ?,
           social_actions_remaining = ?,
           special_actions_remaining = ?""",
        (DUNGEON_ACTIONS_PER_DAY, SOCIAL_ACTIONS_PER_DAY, SPECIAL_ACTIONS_PER_DAY),
    )
    conn.commit()
