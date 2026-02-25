"""
Breach Heist — Mini Retrieve & Escape within the Breach zone.

Artifact in the deepest breach room. Kill mini-boss to claim.
Mini-pursuer operates within breach rooms only.
Relay mechanics compressed to 5-8 rooms.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import MSG_CHAR_LIMIT
from src.systems import broadcast as broadcast_sys


# ── State Helpers ─────────────────────────────────────────────────────────


def get_heist_state(conn: sqlite3.Connection) -> Optional[dict]:
    """Get current heist state from breach table."""
    row = conn.execute(
        """SELECT heist_artifact_room_id, heist_artifact_carrier,
                  active, completed, mini_event
           FROM breach WHERE id = 1"""
    ).fetchone()
    if not row or row["mini_event"] != "heist":
        return None
    return dict(row)


def _get_breach_room_ids(conn: sqlite3.Connection) -> list[int]:
    """Get all breach room IDs in order."""
    rows = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 ORDER BY id"
    ).fetchall()
    return [r["id"] for r in rows]


# ── Artifact Claim ────────────────────────────────────────────────────────


def claim_artifact(
    conn: sqlite3.Connection, player_id: int, player_room_id: int
) -> tuple[bool, str]:
    """Claim the artifact after defeating the breach mini-boss.

    Args:
        player_id: Player claiming the artifact.
        player_room_id: Room where player is standing.

    Returns:
        (success, message)
    """
    state = get_heist_state(conn)
    if not state:
        return False, "No heist active."

    if state["completed"]:
        return False, "Heist already completed."

    if state["heist_artifact_carrier"]:
        return False, "Artifact already claimed!"

    if state["heist_artifact_room_id"] != player_room_id:
        return False, "The artifact isn't here."

    # Check mini-boss is dead in this room
    boss_alive = conn.execute(
        """SELECT COUNT(*) as cnt FROM monsters
           WHERE room_id = ? AND is_breach_boss = 1 AND hp > 0""",
        (player_room_id,),
    ).fetchone()["cnt"]

    if boss_alive > 0:
        return False, "Defeat the guardian first!"

    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    # Set carrier and spawn pursuer
    breach_rooms = _get_breach_room_ids(conn)
    pursuer_room = _spawn_heist_pursuer(breach_rooms, player_room_id)

    conn.execute(
        """UPDATE breach SET
           heist_artifact_carrier = ?,
           heist_pursuer_room_id = ?,
           heist_pursuer_ticks = 0
           WHERE id = 1""",
        (player_id, pursuer_room),
    )

    msg = f"! {name} seized the Breach artifact! A shadow pursues."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    conn.commit()
    return True, "Artifact claimed! Get to town!"


def _spawn_heist_pursuer(breach_rooms: list[int], carrier_room: int) -> int:
    """Spawn pursuer 2 rooms behind carrier within breach rooms."""
    if carrier_room not in breach_rooms:
        return breach_rooms[0] if breach_rooms else carrier_room

    idx = breach_rooms.index(carrier_room)
    spawn_idx = max(0, idx - 2)
    return breach_rooms[spawn_idx]


# ── Carrier Movement ──────────────────────────────────────────────────────


def update_heist_carrier(
    conn: sqlite3.Connection, player_id: int, new_room_id: int
) -> None:
    """Update carrier position and tick the pursuer."""
    state = get_heist_state(conn)
    if not state or state["heist_artifact_carrier"] != player_id:
        return

    _tick_heist_pursuer(conn)
    conn.commit()


def is_heist_carrier(conn: sqlite3.Connection, player_id: int) -> bool:
    """Check if player is the heist artifact carrier."""
    state = get_heist_state(conn)
    return bool(state and state["heist_artifact_carrier"] == player_id)


# ── Pursuer ───────────────────────────────────────────────────────────────


def _tick_heist_pursuer(conn: sqlite3.Connection) -> None:
    """Advance the heist pursuer one step toward the carrier.

    Simpler than full R&E: advances every 2 carrier actions, breach-only.
    """
    row = conn.execute(
        """SELECT heist_artifact_carrier, heist_pursuer_room_id,
                  heist_pursuer_ticks
           FROM breach WHERE id = 1"""
    ).fetchone()
    if not row or not row["heist_pursuer_room_id"]:
        return

    ticks = (row["heist_pursuer_ticks"] or 0) + 1

    if ticks >= 2:
        # Advance pursuer one room toward exit (toward lower index = toward town)
        breach_rooms = _get_breach_room_ids(conn)
        current = row["heist_pursuer_room_id"]
        if current in breach_rooms:
            idx = breach_rooms.index(current)
            # Move toward index 0 (exit direction)
            if idx > 0:
                new_room = breach_rooms[idx - 1]
                conn.execute(
                    """UPDATE breach SET
                       heist_pursuer_room_id = ?, heist_pursuer_ticks = 0
                       WHERE id = 1""",
                    (new_room,),
                )
                return

        conn.execute(
            "UPDATE breach SET heist_pursuer_ticks = 0 WHERE id = 1"
        )
    else:
        conn.execute(
            "UPDATE breach SET heist_pursuer_ticks = ? WHERE id = 1",
            (ticks,),
        )


def get_heist_pursuer_distance(conn: sqlite3.Connection) -> int:
    """Get room distance between pursuer and carrier."""
    row = conn.execute(
        """SELECT heist_artifact_carrier, heist_pursuer_room_id
           FROM breach WHERE id = 1"""
    ).fetchone()
    if not row or not row["heist_pursuer_room_id"]:
        return 99

    breach_rooms = _get_breach_room_ids(conn)
    p_room = row["heist_pursuer_room_id"]

    # Get carrier's current room
    carrier = conn.execute(
        "SELECT room_id FROM players WHERE id = ?",
        (row["heist_artifact_carrier"],),
    ).fetchone()
    if not carrier:
        return 99

    c_room = carrier["room_id"]
    if p_room in breach_rooms and c_room in breach_rooms:
        return abs(breach_rooms.index(p_room) - breach_rooms.index(c_room))
    return 99


# ── Carrier Death & Relay ─────────────────────────────────────────────────


def handle_heist_carrier_death(
    conn: sqlite3.Connection, player_id: int
) -> Optional[str]:
    """Handle heist carrier dying — drops artifact."""
    state = get_heist_state(conn)
    if not state or state["heist_artifact_carrier"] != player_id:
        return None

    player = conn.execute(
        "SELECT name, room_id FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "The carrier"
    drop_room = player["room_id"] if player else state["heist_artifact_room_id"]

    conn.execute(
        """UPDATE breach SET
           heist_artifact_carrier = NULL,
           heist_artifact_room_id = ?
           WHERE id = 1""",
        (drop_room,),
    )

    msg = f"X {name} fell in the Breach. The artifact lies unguarded!"
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return msg


def pickup_heist_artifact(
    conn: sqlite3.Connection, player_id: int, player_room_id: int
) -> tuple[bool, str]:
    """Pick up a dropped heist artifact."""
    state = get_heist_state(conn)
    if not state:
        return False, "No heist active."

    if state["heist_artifact_carrier"]:
        return False, "Artifact isn't dropped."

    if state["heist_artifact_room_id"] != player_room_id:
        return False, "The artifact isn't here."

    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    # Reset pursuer further back
    breach_rooms = _get_breach_room_ids(conn)
    pursuer_room = _spawn_heist_pursuer(breach_rooms, player_room_id)

    conn.execute(
        """UPDATE breach SET
           heist_artifact_carrier = ?,
           heist_pursuer_room_id = ?,
           heist_pursuer_ticks = 0
           WHERE id = 1""",
        (player_id, pursuer_room),
    )

    msg = f"! {name} picked up the Breach artifact! The relay continues."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return True, "Artifact recovered! Get to town!"


# ── Delivery / Completion ─────────────────────────────────────────────────


def check_heist_delivery(
    conn: sqlite3.Connection, player_id: int, player_state: str
) -> tuple[bool, str]:
    """Check if heist carrier delivered artifact to town.

    Returns:
        (delivered, message)
    """
    state = get_heist_state(conn)
    if not state or state["completed"]:
        return False, ""

    if state["heist_artifact_carrier"] != player_id:
        return False, ""

    if player_state != "town":
        return False, ""

    conn.execute(
        "UPDATE breach SET completed = 1, completed_at = datetime('now') WHERE id = 1"
    )

    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    msg = f"! {name} extracted the Breach artifact! The Heist succeeds!"
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return True, "Artifact delivered! The Breach Heist is complete!"


def format_heist_status(conn: sqlite3.Connection) -> str:
    """Format heist status for display."""
    state = get_heist_state(conn)
    if not state:
        return "No heist active."

    if state["completed"]:
        return "The Breach Heist is complete."

    if not state["heist_artifact_carrier"]:
        if state["active"]:
            return "Breach artifact unclaimed. Defeat the guardian."
        return "Heist not yet active."

    carrier = conn.execute(
        "SELECT name FROM players WHERE id = ?",
        (state["heist_artifact_carrier"],),
    ).fetchone()
    name = carrier["name"] if carrier else "?"
    dist = get_heist_pursuer_distance(conn)
    return f"{name} carries the artifact. Shadow: {dist} rooms."
