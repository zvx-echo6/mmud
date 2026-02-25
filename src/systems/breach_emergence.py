"""
Breach Emergence — Mini Raid Boss within the Breach zone.

Shared HP pool creature (500-800 HP) in central breach room.
Minion respawn every 8 hours. Chip-and-run combat. 3%/8h regen.
"""

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import (
    EMERGENCE_HP_MAX,
    EMERGENCE_HP_MIN,
    MSG_CHAR_LIMIT,
)
from src.systems import broadcast as broadcast_sys

EMERGENCE_REGEN_RATE = 0.03
EMERGENCE_REGEN_INTERVAL_HOURS = 8
EMERGENCE_MINION_RESPAWN_HOURS = 8


# ── State Helpers ─────────────────────────────────────────────────────────


def get_emergence_state(conn: sqlite3.Connection) -> Optional[dict]:
    """Get current emergence state from breach table."""
    row = conn.execute(
        """SELECT emergence_hp, emergence_hp_max,
                  active, completed, mini_event
           FROM breach WHERE id = 1"""
    ).fetchone()
    if not row or row["mini_event"] != "emergence":
        return None
    return dict(row)


def _get_central_breach_room(conn: sqlite3.Connection) -> Optional[int]:
    """Get the central breach room (middle of the chain)."""
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 ORDER BY id"
    ).fetchall()
    if not rooms:
        return None
    return rooms[len(rooms) // 2]["id"]


# ── Damage ────────────────────────────────────────────────────────────────


def deal_emergence_damage(
    conn: sqlite3.Connection, player_id: int, damage: int
) -> tuple[int, str]:
    """Deal damage to the emergence creature.

    Args:
        player_id: Player dealing damage.
        damage: Amount of damage.

    Returns:
        (new_hp, message)
    """
    state = get_emergence_state(conn)
    if not state or state["completed"]:
        return 0, "The creature is already defeated."

    if state["emergence_hp"] is None or state["emergence_hp"] <= 0:
        return 0, "The creature is already defeated."

    new_hp = max(0, state["emergence_hp"] - damage)
    conn.execute(
        "UPDATE breach SET emergence_hp = ? WHERE id = 1", (new_hp,)
    )

    # Record contribution
    _record_contribution(conn, player_id, damage)

    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    if new_hp <= 0:
        conn.execute(
            "UPDATE breach SET completed = 1, completed_at = datetime('now') WHERE id = 1"
        )
        msg = f"! {name} struck the final blow! The Breach creature falls!"
        broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
        conn.commit()
        return 0, "The Breach creature is destroyed! Victory!"

    pct = new_hp / state["emergence_hp_max"] * 100 if state["emergence_hp_max"] else 0
    if pct <= 25:
        broadcast_sys.create_broadcast(
            conn, 2, f"Breach creature weakens! {new_hp}HP remain."[:MSG_CHAR_LIMIT]
        )

    conn.commit()
    return new_hp, f"You deal {damage}. Creature: {new_hp}/{state['emergence_hp_max']}HP"


def _record_contribution(
    conn: sqlite3.Connection, player_id: int, damage: int
) -> None:
    """Record player contribution to emergence fight."""
    conn.execute(
        """INSERT INTO breach_emergence_contributors (player_id, total_damage)
           VALUES (?, ?)
           ON CONFLICT(player_id)
           DO UPDATE SET total_damage = total_damage + ?""",
        (player_id, damage, damage),
    )


# ── Regen ─────────────────────────────────────────────────────────────────


def apply_emergence_regen(conn: sqlite3.Connection) -> int:
    """Apply regen to emergence creature. Returns HP healed."""
    state = get_emergence_state(conn)
    if not state or state["completed"]:
        return 0

    hp = state["emergence_hp"]
    hp_max = state["emergence_hp_max"]
    if hp is None or hp_max is None or hp >= hp_max or hp <= 0:
        return 0

    regen = max(1, math.ceil(hp_max * EMERGENCE_REGEN_RATE))
    new_hp = min(hp_max, hp + regen)
    conn.execute(
        "UPDATE breach SET emergence_hp = ? WHERE id = 1", (new_hp,)
    )
    conn.commit()
    return new_hp - hp


# ── Minion Respawn ────────────────────────────────────────────────────────


def respawn_emergence_minions(conn: sqlite3.Connection) -> int:
    """Respawn minions in breach rooms surrounding the creature.

    Called on a timer (every 8 hours). Returns count spawned.
    """
    state = get_emergence_state(conn)
    if not state or state["completed"]:
        return 0

    # Get non-central breach rooms
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 ORDER BY id"
    ).fetchall()
    if len(rooms) < 3:
        return 0

    central_idx = len(rooms) // 2
    minion_rooms = [r["id"] for i, r in enumerate(rooms) if i != central_idx]

    count = 0
    for room_id in minion_rooms:
        # Skip if a living minion already exists in this room
        existing = conn.execute(
            """SELECT COUNT(*) as cnt FROM monsters
               WHERE room_id = ? AND hp > 0 AND name LIKE '%Breach Spawn%'""",
            (room_id,),
        ).fetchone()["cnt"]
        if existing > 0:
            continue

        # Spawn a minion
        conn.execute(
            """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
               xp_reward, gold_reward_min, gold_reward_max, tier)
               VALUES (?, 'Breach Spawn', 30, 30, 8, 6, 5, 15, 5, 15, 3)""",
            (room_id,),
        )
        count += 1

    if count > 0:
        broadcast_sys.create_broadcast(
            conn, 2,
            f"Minions stir in the Breach. {count} new threats."[:MSG_CHAR_LIMIT],
        )

    conn.commit()
    return count


# ── Completion Check ──────────────────────────────────────────────────────


def check_emergence_complete(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check if emergence creature is dead."""
    state = get_emergence_state(conn)
    if not state:
        return False, ""

    if state["emergence_hp"] is not None and state["emergence_hp"] <= 0:
        if not state["completed"]:
            conn.execute(
                "UPDATE breach SET completed = 1, completed_at = datetime('now') WHERE id = 1"
            )
            msg = "The Breach creature has been destroyed!"
            broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
            conn.commit()
            return True, msg
        return True, "Already complete."

    return False, ""


# ── Status Display ────────────────────────────────────────────────────────


def format_emergence_status(conn: sqlite3.Connection) -> str:
    """Format emergence status for display."""
    state = get_emergence_state(conn)
    if not state:
        return "No emergence active."

    if state["completed"]:
        return "The Breach creature has been destroyed."

    hp = state["emergence_hp"] or 0
    hp_max = state["emergence_hp_max"] or 1
    pct = hp / hp_max * 100

    contrib = conn.execute(
        """SELECT COUNT(*) as cnt FROM breach_emergence_contributors
           WHERE total_damage > 0"""
    ).fetchone()
    fighters = contrib["cnt"] if contrib else 0

    return f"Breach Creature HP:{hp}/{hp_max} ({pct:.0f}%) {fighters} fighters"
