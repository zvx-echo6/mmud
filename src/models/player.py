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
    RESOURCE_MAX,
    SOCIAL_ACTIONS_PER_DAY,
    SPECIAL_ACTIONS_PER_DAY,
    STAT_POINTS_PER_LEVEL,
    XP_PER_LEVEL,
)


# Base HP by class at level 1
BASE_HP = {
    "warrior": 50,
    "rogue": 40,
    "caster": 35,
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
        cls: Class name (warrior, rogue, caster).

    Returns:
        Player dict from the database.
    """
    cls = cls.lower()
    if cls not in CLASSES:
        raise ValueError(f"Unknown class: {cls}. Choose from: {', '.join(CLASSES)}")

    stats = CLASSES[cls]
    hp = BASE_HP.get(cls, 45)

    # Find town center room for spawn
    center = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1 LIMIT 1"
    ).fetchone()
    center_id = center["id"] if center else None

    cursor = conn.execute(
        """INSERT INTO players
           (account_id, name, class, hp, hp_max, pow, def, spd,
            resource, resource_max,
            dungeon_actions_remaining, social_actions_remaining,
            special_actions_remaining, last_login, room_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            account_id, name, cls, hp, hp,
            stats["POW"], stats["DEF"], stats["SPD"],
            RESOURCE_MAX, RESOURCE_MAX,
            DUNGEON_ACTIONS_PER_DAY,
            SOCIAL_ACTIONS_PER_DAY,
            SPECIAL_ACTIONS_PER_DAY,
            datetime.now(timezone.utc).isoformat(),
            center_id,
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


def use_resource(conn: sqlite3.Connection, player_id: int, cost: int = 1) -> bool:
    """Decrement resource. Returns False if insufficient."""
    row = conn.execute(
        "SELECT resource FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    if not row or row["resource"] < cost:
        return False
    conn.execute(
        "UPDATE players SET resource = resource - ? WHERE id = ?",
        (cost, player_id),
    )
    conn.commit()
    return True


def restore_resource(conn: sqlite3.Connection, player_id: int, amount: int) -> None:
    """Restore resource up to resource_max."""
    conn.execute(
        "UPDATE players SET resource = MIN(resource + ?, resource_max) WHERE id = ?",
        (amount, player_id),
    )
    conn.commit()


def _ensure_death_log_table(conn: sqlite3.Connection) -> None:
    """Create death_log table if it doesn't exist (migration-safe)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS death_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            floor INTEGER NOT NULL,
            monster_name TEXT NOT NULL,
            died_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _log_death(conn: sqlite3.Connection, player: dict) -> None:
    """Log death with monster name and floor for NPC memory."""
    _ensure_death_log_table(conn)
    monster_name = "unknown"
    floor = player.get("floor", 0) or 0
    if player.get("combat_monster_id"):
        monster = conn.execute(
            "SELECT name FROM monsters WHERE id = ?",
            (player["combat_monster_id"],),
        ).fetchone()
        if monster:
            monster_name = monster["name"]
    conn.execute(
        "INSERT INTO death_log (player_id, floor, monster_name) VALUES (?, ?, ?)",
        (player["id"], floor, monster_name),
    )


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

    # Capture death info before state is reset
    _log_death(conn, player)

    gold_lost = math.floor(player["gold_carried"] * DEATH_GOLD_LOSS_PERCENT / 100)
    xp_lost = math.floor(player["xp"] * 0.15)
    new_hp = max(1, player["hp_max"] // 2)
    new_actions = max(0, player["dungeon_actions_remaining"] - 1)

    new_resource = max(1, player.get("resource_max", RESOURCE_MAX) // 2)

    # Respawn at town center
    center = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1 LIMIT 1"
    ).fetchone()
    center_id = center["id"] if center else None

    conn.execute(
        """UPDATE players SET
           gold_carried = gold_carried - ?,
           xp = xp - ?,
           hp = ?,
           resource = ?,
           state = 'town',
           floor = 0,
           room_id = ?,
           combat_monster_id = NULL,
           town_location = NULL,
           dungeon_actions_remaining = ?
           WHERE id = ?""",
        (gold_lost, xp_lost, new_hp, new_resource, center_id, new_actions, player_id),
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
        levels_gained = new_level - current_level
        hp_gain = levels_gained * 5
        sp_gain = levels_gained * STAT_POINTS_PER_LEVEL
        updates["level"] = new_level
        updates["hp_max"] = player["hp_max"] + hp_gain
        updates["hp"] = min(player["hp"] + hp_gain, player["hp_max"] + hp_gain)
        updates["stat_points"] = player["stat_points"] + sp_gain

    update_state(conn, player_id, **updates)
    return new_level if new_level > current_level else None


def award_gold(conn: sqlite3.Connection, player_id: int, gold: int) -> None:
    """Award gold to a player's carried stash."""
    conn.execute(
        "UPDATE players SET gold_carried = gold_carried + ? WHERE id = ?",
        (gold, player_id),
    )
    conn.commit()


def train_stat(
    conn: sqlite3.Connection, player_id: int, stat: str
) -> tuple[bool, str]:
    """Spend a stat point to increase a stat by 1.

    Args:
        conn: Database connection.
        player_id: Player ID.
        stat: Stat name (pow, def, spd).

    Returns:
        (success, message)
    """
    stat = stat.lower()
    stat_map = {"pow": "pow", "def": "def", "spd": "spd",
                "power": "pow", "defense": "def", "speed": "spd"}

    col = stat_map.get(stat)
    if not col:
        return False, "Train what? Use: TRAIN POW, TRAIN DEF, or TRAIN SPD"

    player = get_player(conn, player_id)
    if not player:
        return False, "Player not found."

    if player["stat_points"] <= 0:
        return False, "No stat points. Level up to earn more."

    new_val = player[col] + 1
    conn.execute(
        f"UPDATE players SET {col} = ?, stat_points = stat_points - 1 WHERE id = ?",
        (new_val, player_id),
    )
    conn.commit()
    return True, f"+1 {col.upper()}! Now {new_val}. ({player['stat_points'] - 1} pts left)"


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
