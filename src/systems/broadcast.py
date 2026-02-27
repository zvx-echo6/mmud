"""
Broadcast system for MMUD.
Three tiers: immediate (tier 1), batched (tier 2), targeted.
All broadcasts stored in DB. Delivery tracked via broadcast_seen.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import BROADCAST_CHAR_LIMIT, MSG_CHAR_LIMIT


def create_broadcast(
    conn: sqlite3.Connection,
    tier: int,
    message: str,
    targeted: bool = False,
    target_condition: Optional[str] = None,
) -> int:
    """Create a new broadcast message.

    Args:
        conn: Database connection.
        tier: 1 (immediate) or 2 (batched into recap).
        message: Broadcast text (should be under 175 chars).
        targeted: If True, only deliver to players matching target_condition.
        target_condition: JSON condition string for targeted broadcasts.

    Returns:
        Broadcast ID.
    """
    cursor = conn.execute(
        """INSERT INTO broadcasts (tier, targeted, target_condition, message, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (tier, 1 if targeted else 0, target_condition, message[:BROADCAST_CHAR_LIMIT],
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


def get_unseen_broadcasts(
    conn: sqlite3.Connection, player_id: int, tier: Optional[int] = None
) -> list[dict]:
    """Get broadcasts the player hasn't seen yet.

    Returns oldest first. Tier 1 before tier 2 when no tier filter.
    Excludes targeted broadcasts that don't match the player.
    """
    if tier is not None:
        rows = conn.execute(
            """SELECT b.* FROM broadcasts b
               WHERE b.id NOT IN (
                   SELECT broadcast_id FROM broadcast_seen WHERE player_id = ?
               )
               AND b.tier = ?
               AND b.targeted = 0
               ORDER BY b.created_at ASC""",
            (player_id, tier),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT b.* FROM broadcasts b
               WHERE b.id NOT IN (
                   SELECT broadcast_id FROM broadcast_seen WHERE player_id = ?
               )
               AND b.targeted = 0
               ORDER BY b.tier ASC, b.created_at ASC""",
            (player_id,),
        ).fetchall()

    return [dict(r) for r in rows]


def mark_seen(
    conn: sqlite3.Connection, player_id: int, broadcast_id: int
) -> None:
    """Mark a broadcast as seen by a player."""
    conn.execute(
        """INSERT OR IGNORE INTO broadcast_seen (broadcast_id, player_id, seen_at)
           VALUES (?, ?, ?)""",
        (broadcast_id, player_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def mark_all_seen(
    conn: sqlite3.Connection, player_id: int, broadcast_ids: list[int]
) -> None:
    """Mark multiple broadcasts as seen."""
    now = datetime.now(timezone.utc).isoformat()
    for bid in broadcast_ids:
        conn.execute(
            """INSERT OR IGNORE INTO broadcast_seen (broadcast_id, player_id, seen_at)
               VALUES (?, ?, ?)""",
            (bid, player_id, now),
        )
    conn.commit()


def deliver_unseen(
    conn: sqlite3.Connection, player_id: int, limit: int = 3
) -> Optional[str]:
    """Deliver unseen tier 1 broadcasts to a player.

    Returns a single formatted string of up to `limit` broadcasts,
    or None if nothing new. Marks delivered broadcasts as seen.
    """
    unseen = get_unseen_broadcasts(conn, player_id, tier=1)
    if not unseen:
        return None

    to_deliver = unseen[:limit]
    ids = [b["id"] for b in to_deliver]
    mark_all_seen(conn, player_id, ids)

    remaining = len(unseen) - limit
    lines = [b["message"] for b in to_deliver]
    result = " | ".join(lines)
    if remaining > 0:
        result += f" (+{remaining} more at barkeep)"

    # Truncate to MSG_CHAR_LIMIT if needed
    if len(result) > MSG_CHAR_LIMIT:
        result = result[:MSG_CHAR_LIMIT - 3] + "..."
    return result


def generate_recap(
    conn: sqlite3.Connection, player_id: int
) -> list[str]:
    """Generate a barkeep recap of all missed broadcasts.

    Groups broadcasts and produces 1-3 messages of up to 175 chars each.
    Marks all recapped broadcasts as seen.

    Returns:
        List of recap message strings.
    """
    unseen = get_unseen_broadcasts(conn, player_id)
    if not unseen:
        return ["Grist polishes a glass. 'Quiet day. Nothing to report.'"]

    ids = [b["id"] for b in unseen]
    mark_all_seen(conn, player_id, ids)

    # Group by type prefix (emoji or first word)
    deaths = []
    levels = []
    bounty_updates = []
    other = []

    for b in unseen:
        msg = b["message"]
        if msg.startswith("X ") or "fell" in msg or "died" in msg:
            deaths.append(msg)
        elif "level" in msg.lower() or "Lv" in msg:
            levels.append(msg)
        elif "bounty" in msg.lower() or "Bounty" in msg:
            bounty_updates.append(msg)
        else:
            other.append(msg)

    # Build recap messages
    recap_parts = []

    if len(unseen) <= 3:
        # Few enough to show individually
        for b in unseen:
            recap_parts.append(b["message"])
    else:
        # Summarize counts
        summary_parts = []
        if deaths:
            summary_parts.append(f"{len(deaths)} death{'s' if len(deaths) > 1 else ''}")
        if bounty_updates:
            summary_parts.append(f"{len(bounty_updates)} bounty update{'s' if len(bounty_updates) > 1 else ''}")
        if levels:
            summary_parts.append(f"{len(levels)} level up{'s' if len(levels) > 1 else ''}")
        if other:
            summary_parts.append(f"{len(other)} other")

        recap_parts.append("Grist: While you were away: " + ", ".join(summary_parts) + ".")

        # Add 1-2 most recent individual messages
        recent = unseen[-2:]
        for b in recent:
            recap_parts.append(b["message"])

    # Ensure each part is under 175 chars
    result = []
    for part in recap_parts[:3]:
        if len(part) > MSG_CHAR_LIMIT:
            part = part[:MSG_CHAR_LIMIT - 3] + "..."
        result.append(part)

    return result


# ── Broadcast helper functions for game events ──────────────────────────────


def broadcast_death(conn: sqlite3.Connection, player_name: str, floor: int) -> None:
    """Broadcast a player death (tier 1)."""
    create_broadcast(conn, 1, f"X {player_name} fell on Floor {floor}.")


def broadcast_level_up(conn: sqlite3.Connection, player_name: str, level: int) -> None:
    """Broadcast a level up (tier 2)."""
    create_broadcast(conn, 2, f"^ {player_name} reached level {level}!")


def broadcast_bounty_progress(
    conn: sqlite3.Connection, monster_name: str, hp: int, hp_max: int
) -> None:
    """Broadcast bounty halfway point (tier 2)."""
    create_broadcast(conn, 2, f"# Bounty {monster_name}: {hp}/{hp_max}HP. Keep pushing.")


def broadcast_bounty_complete(
    conn: sqlite3.Connection,
    killer_name: str,
    monster_name: str,
    contributors: list[str],
) -> None:
    """Broadcast bounty completion (tier 2)."""
    names = ", ".join(contributors[:5])
    if len(contributors) > 5:
        names += f" +{len(contributors) - 5}"
    create_broadcast(conn, 2, f"# {killer_name} finished {monster_name}! Contributors: {names}")


def broadcast_new_bounty(
    conn: sqlite3.Connection, description: str
) -> None:
    """Broadcast new bounty activation (tier 2)."""
    create_broadcast(conn, 2, f"# New bounty: {description}")


def broadcast_floor_unlock(
    conn: sqlite3.Connection, cleared_floor: int
) -> Optional[int]:
    """Broadcast that a floor boss was killed, unlocking the next floor.

    Composes the message from floor_themes data for the NEXT floor
    (the one being unlocked). Format:
      [transition flavor]. [floor_name]. [atmosphere snippet].

    Args:
        conn: Database connection.
        cleared_floor: The floor whose boss was just killed.

    Returns:
        Broadcast ID, or None if next floor has no theme data or is floor 8+.
    """
    from config import NUM_FLOORS

    next_floor = cleared_floor + 1
    if next_floor > NUM_FLOORS:
        return None

    row = conn.execute(
        "SELECT floor_name, atmosphere, floor_transition FROM floor_themes WHERE floor = ?",
        (next_floor,),
    ).fetchone()
    if not row:
        return None

    transition = row["floor_transition"] if row["floor_transition"] else ""
    floor_name = row["floor_name"] if row["floor_name"] else f"Floor {next_floor}"
    atmosphere = row["atmosphere"] if row["atmosphere"] else ""

    parts = []
    if transition:
        parts.append(transition.rstrip('.') + '.')
    parts.append(floor_name.rstrip('.') + '.')
    if atmosphere:
        parts.append(atmosphere.rstrip('.') + '.')

    message = " ".join(parts)
    return create_broadcast(conn, 1, message)
