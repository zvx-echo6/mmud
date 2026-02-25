"""
Bounty system for MMUD.
Shared HP pools, lazy regen, contribution tracking, rewards.
Bounties are per-world, not per-character — everyone sees the same bounties.
"""

import math
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import (
    BOUNTY_ACTIVE_MAX,
    BOUNTY_REGEN_INTERVAL_HOURS,
    BOUNTY_REGEN_RATE,
)
from src.models import player as player_model
from src.systems import broadcast as broadcast_sys


# ── Bounty Queries ──────────────────────────────────────────────────────────


def get_active_bounties(conn: sqlite3.Connection) -> list[dict]:
    """Get currently active bounties."""
    rows = conn.execute(
        """SELECT b.*, m.name as monster_name, m.hp as monster_hp,
                  m.hp_max as monster_hp_max, m.room_id
           FROM bounties b
           LEFT JOIN monsters m ON b.target_monster_id = m.id
           WHERE b.active = 1 AND b.completed = 0
           ORDER BY b.id""",
    ).fetchall()
    return [dict(r) for r in rows]


def get_bounty_by_monster(
    conn: sqlite3.Connection, monster_id: int
) -> Optional[dict]:
    """Check if a monster is a bounty target."""
    row = conn.execute(
        """SELECT b.* FROM bounties b
           WHERE b.target_monster_id = ? AND b.active = 1 AND b.completed = 0
           LIMIT 1""",
        (monster_id,),
    ).fetchone()
    return dict(row) if row else None


def format_bounty_list(conn: sqlite3.Connection) -> str:
    """Format active bounties for display."""
    bounties = get_active_bounties(conn)
    if not bounties:
        return "No active bounties. Check back later."

    parts = []
    for b in bounties:
        if b.get("monster_name"):
            # Apply lazy regen before displaying
            apply_regen(conn, b["id"])
            # Re-fetch monster HP after regen
            m = conn.execute(
                "SELECT hp, hp_max FROM monsters WHERE id = ?",
                (b["target_monster_id"],),
            ).fetchone()
            if m:
                parts.append(f"{b['monster_name']} {m['hp']}/{m['hp_max']}HP")
            else:
                parts.append(f"{b['description']}")
        else:
            parts.append(f"{b['description']} ({b['current_value']}/{b['target_value']})")

    return "Bounties: " + " | ".join(parts)


# ── Bounty Regen (Lazy Evaluation) ─────────────────────────────────────────


def apply_regen(conn: sqlite3.Connection, bounty_id: int) -> None:
    """Apply lazy-evaluated regen to a bounty monster.

    Calculates how many regen intervals have passed since last check,
    applies accumulated regen. Called on access, not on a timer.
    """
    bounty = conn.execute(
        "SELECT * FROM bounties WHERE id = ?", (bounty_id,)
    ).fetchone()
    if not bounty or bounty["completed"]:
        return

    monster = conn.execute(
        "SELECT * FROM monsters WHERE id = ?",
        (bounty["target_monster_id"],),
    ).fetchone()
    if not monster or monster["hp"] >= monster["hp_max"]:
        return

    # Check last regen time from monster table
    # We use the bounty's last known time — stored as a separate tracking mechanism
    last_regen = _get_last_regen(conn, bounty_id)
    now = datetime.now(timezone.utc)

    if not last_regen:
        _set_last_regen(conn, bounty_id, now)
        return

    hours_elapsed = (now - last_regen).total_seconds() / 3600
    intervals = int(hours_elapsed / BOUNTY_REGEN_INTERVAL_HOURS)

    if intervals <= 0:
        return

    regen_per_interval = math.ceil(monster["hp_max"] * BOUNTY_REGEN_RATE)
    total_regen = regen_per_interval * intervals
    new_hp = min(monster["hp_max"], monster["hp"] + total_regen)

    conn.execute(
        "UPDATE monsters SET hp = ? WHERE id = ?",
        (new_hp, monster["id"]),
    )
    _set_last_regen(conn, bounty_id, now)
    conn.commit()


def _get_last_regen(conn: sqlite3.Connection, bounty_id: int) -> Optional[datetime]:
    """Get last regen timestamp for a bounty. Stored in bounty description field suffix."""
    # Use a simple approach: track in a column we can add or use completed_at as proxy
    # For Phase 3, we'll track regen time using the monsters table's respawns_remaining
    # as a timestamp proxy. Better approach: use bounty's own tracking.
    # Simple: store last regen check time in the bounty row.
    row = conn.execute(
        "SELECT completed_at FROM bounties WHERE id = ?", (bounty_id,)
    ).fetchone()
    if row and row["completed_at"] and not conn.execute(
        "SELECT completed FROM bounties WHERE id = ?", (bounty_id,)
    ).fetchone()["completed"]:
        # We repurpose completed_at as last_regen_at for active bounties
        try:
            dt = datetime.fromisoformat(row["completed_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None
    return None


def _set_last_regen(conn: sqlite3.Connection, bounty_id: int, when: datetime) -> None:
    """Set last regen timestamp for a bounty."""
    # Repurpose completed_at for active bounties as last_regen_at
    conn.execute(
        "UPDATE bounties SET completed_at = ? WHERE id = ? AND completed = 0",
        (when.isoformat(), bounty_id),
    )
    conn.commit()


# ── Bounty Combat ───────────────────────────────────────────────────────────


def record_contribution(
    conn: sqlite3.Connection, bounty_id: int, player_id: int, damage: int
) -> None:
    """Record a player's damage contribution to a bounty."""
    conn.execute(
        """INSERT INTO bounty_contributors (bounty_id, player_id, contribution)
           VALUES (?, ?, ?)
           ON CONFLICT(bounty_id, player_id)
           DO UPDATE SET contribution = contribution + ?""",
        (bounty_id, player_id, damage, damage),
    )
    conn.commit()


def check_bounty_completion(
    conn: sqlite3.Connection, bounty_id: int, killer_id: int
) -> Optional[str]:
    """Check if a bounty is complete and process rewards.

    Called after a bounty monster's HP hits 0.

    Returns:
        Completion message, or None if not complete.
    """
    bounty = conn.execute(
        "SELECT * FROM bounties WHERE id = ?", (bounty_id,)
    ).fetchone()
    if not bounty or bounty["completed"]:
        return None

    monster = conn.execute(
        "SELECT * FROM monsters WHERE id = ?",
        (bounty["target_monster_id"],),
    ).fetchone()
    if not monster or monster["hp"] > 0:
        return None

    # Mark bounty as completed
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE bounties SET completed = 1, completed_at = ?, active = 0 WHERE id = ?",
        (now, bounty_id),
    )

    # Get all contributors
    contributors = conn.execute(
        """SELECT bc.player_id, bc.contribution, p.name
           FROM bounty_contributors bc
           JOIN players p ON bc.player_id = p.id
           WHERE bc.bounty_id = ?
           ORDER BY bc.contribution DESC""",
        (bounty_id,),
    ).fetchall()

    # Reward all contributors (threshold model)
    xp_reward = monster["xp_reward"] * 2  # Bounty bonus
    gold_reward = monster["gold_reward_max"] * 3
    killer_bonus_gold = gold_reward // 2  # Killing blow bonus

    killer_name = None
    contributor_names = []
    for c in contributors:
        player_model.award_xp(conn, c["player_id"], xp_reward)
        player_model.award_gold(conn, c["player_id"], gold_reward)
        contributor_names.append(c["name"])
        if c["player_id"] == killer_id:
            killer_name = c["name"]
            player_model.award_gold(conn, c["player_id"], killer_bonus_gold)

    if not killer_name:
        killer_name = conn.execute(
            "SELECT name FROM players WHERE id = ?", (killer_id,)
        ).fetchone()
        if killer_name:
            killer_name = killer_name["name"]
        else:
            killer_name = "Someone"

    # Spawn weaker replacement
    _spawn_replacement(conn, monster)

    # Broadcast completion
    broadcast_sys.broadcast_bounty_complete(
        conn, killer_name, monster["name"], contributor_names
    )

    # Activate next bounty
    _activate_next_bounty(conn)

    conn.commit()
    return f"Bounty complete! {monster['name']} slain. +{xp_reward}xp +{gold_reward}g"


def check_halfway_broadcast(
    conn: sqlite3.Connection, bounty_id: int, monster_id: int
) -> None:
    """Check if a bounty monster crossed the halfway point and broadcast."""
    monster = conn.execute(
        "SELECT * FROM monsters WHERE id = ?", (monster_id,)
    ).fetchone()
    if not monster:
        return

    bounty = conn.execute(
        "SELECT * FROM bounties WHERE id = ?", (bounty_id,)
    ).fetchone()
    if not bounty:
        return

    halfway = monster["hp_max"] // 2
    # Check if we just crossed the halfway mark
    # Use current_value in bounty to track if halfway broadcast was sent
    if monster["hp"] <= halfway and bounty["current_value"] == 0:
        conn.execute(
            "UPDATE bounties SET current_value = 1 WHERE id = ?", (bounty_id,)
        )
        conn.commit()
        broadcast_sys.broadcast_bounty_progress(
            conn, monster["name"], monster["hp"], monster["hp_max"]
        )


def _spawn_replacement(conn: sqlite3.Connection, original: dict) -> None:
    """Spawn a weaker regular monster to replace a completed bounty."""
    # Half stats, not a bounty
    conn.execute(
        """UPDATE monsters SET
           hp = ?, hp_max = ?,
           pow = ?, def = ?, spd = ?,
           xp_reward = ?, gold_reward_min = ?, gold_reward_max = ?,
           is_bounty = 0
           WHERE id = ?""",
        (
            original["hp_max"] // 2, original["hp_max"] // 2,
            max(1, original["pow"] // 2),
            max(1, original["def"] // 2),
            max(1, original["spd"] // 2),
            original["xp_reward"] // 2,
            original["gold_reward_min"] // 2,
            original["gold_reward_max"] // 2,
            original["id"],
        ),
    )
    conn.commit()


def _activate_next_bounty(conn: sqlite3.Connection) -> None:
    """Activate the next bounty from the pool matching current phase."""
    active_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM bounties WHERE active = 1 AND completed = 0"
    ).fetchone()["cnt"]

    if active_count >= BOUNTY_ACTIVE_MAX:
        return

    epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
    day = epoch["day_number"] if epoch else 1

    # Find next inactive bounty for the current day
    next_bounty = conn.execute(
        """SELECT id, description FROM bounties
           WHERE active = 0 AND completed = 0 AND available_from_day <= ?
           ORDER BY id LIMIT 1""",
        (day,),
    ).fetchone()

    if next_bounty:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE bounties SET active = 1, completed_at = ? WHERE id = ?",
            (now, next_bounty["id"]),
        )
        conn.commit()
        broadcast_sys.broadcast_new_bounty(conn, next_bounty["description"])
