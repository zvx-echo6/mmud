"""
Retrieve and Escape — Endgame mode runtime logic.

Cooperative relay: claim objective on floor 4, carry to surface.
Pursuer tracks carrier. Three support roles: blocker, warder, lurer.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import (
    ESCAPE_SPAWN_RATE_MULTIPLIER,
    LURE_ACTION_COST,
    LURE_DIVERT_TICKS,
    LURE_TOTAL_DELAY_TICKS,
    MSG_CHAR_LIMIT,
    NUM_FLOORS,
    PURSUER_ADVANCE_RATE,
    PURSUER_FLEE_BASE_CHANCE,
    PURSUER_RELAY_RESET_DISTANCE,
    PURSUER_SPAWN_DISTANCE,
    WARD_ACTION_COST,
    WARD_PURSUER_SLOWDOWN,
)
from src.systems import broadcast as broadcast_sys


# ── Initialization ─────────────────────────────────────────────────────────


def init_escape_run(conn: sqlite3.Connection, objective_name: str = "Crown of the Depths") -> None:
    """Initialize the escape run table for this epoch.

    Called at epoch generation when mode is retrieve_and_escape.
    """
    conn.execute(
        """INSERT OR REPLACE INTO escape_run
           (id, objective_name, active, completed, objective_dropped, pursuer_ticks)
           VALUES (1, ?, 0, 0, 0, 0)""",
        (objective_name,),
    )
    conn.commit()


def get_escape_state(conn: sqlite3.Connection) -> Optional[dict]:
    """Get current escape run state."""
    row = conn.execute("SELECT * FROM escape_run WHERE id = 1").fetchone()
    return dict(row) if row else None


# ── Objective Claim ────────────────────────────────────────────────────────


def claim_objective(
    conn: sqlite3.Connection, player_id: int, player_room_id: int
) -> tuple[bool, str]:
    """Player claims the objective after defeating the guardian.

    Args:
        conn: Database connection.
        player_id: Claiming player's ID.
        player_room_id: Room where guardian was defeated.

    Returns:
        (success, message)
    """
    state = get_escape_state(conn)
    if not state:
        return False, "No escape run this epoch."

    if state["active"]:
        return False, "Objective already claimed!"

    if state["completed"]:
        return False, "The objective has already been delivered!"

    # Get player name
    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    # Calculate pursuer spawn position (N rooms behind)
    pursuer_room = _find_room_n_behind(conn, player_room_id, PURSUER_SPAWN_DISTANCE)

    conn.execute(
        """UPDATE escape_run SET
           active = 1,
           carrier_player_id = ?,
           carrier_room_id = ?,
           pursuer_room_id = ?,
           pursuer_ticks = 0,
           objective_dropped = 0,
           started_at = ?
           WHERE id = 1""",
        (player_id, player_room_id, pursuer_room,
         datetime.now(timezone.utc).isoformat()),
    )

    # Record participant
    _record_participant(conn, player_id, "carrier")

    # Broadcast
    obj_name = state["objective_name"]
    msg = f"! {name} claimed the {obj_name}! The Pursuer stirs."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    conn.commit()
    return True, f"You claimed the {obj_name}! RUN!"


# ── Pursuer Advancement ───────────────────────────────────────────────────


def tick_pursuer(conn: sqlite3.Connection) -> dict:
    """Advance the pursuer based on carrier actions.

    Called after each carrier action. Increments ticks, advances when threshold met.

    Returns:
        Status dict with advanced, distance, reached_carrier.
    """
    result = {"advanced": False, "distance": 0, "reached_carrier": False}

    state = get_escape_state(conn)
    if not state or not state["active"] or state["completed"]:
        return result

    # Check for lure diversion
    if _is_pursuer_diverted(conn, state):
        _decrement_divert(conn)
        return result

    new_ticks = state["pursuer_ticks"] + 1

    if new_ticks >= PURSUER_ADVANCE_RATE:
        # Advance pursuer 1 room toward carrier
        new_ticks = 0
        new_room = _advance_pursuer_one_room(conn, state)
        if new_room:
            conn.execute(
                "UPDATE escape_run SET pursuer_room_id = ?, pursuer_ticks = 0 WHERE id = 1",
                (new_room,),
            )
            result["advanced"] = True

            # Check if pursuer reached carrier
            state_updated = get_escape_state(conn)
            if state_updated and state_updated["pursuer_room_id"] == state_updated["carrier_room_id"]:
                result["reached_carrier"] = True
                msg = "! The Pursuer has reached the carrier!"
                broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
        else:
            conn.execute(
                "UPDATE escape_run SET pursuer_ticks = 0 WHERE id = 1"
            )
    else:
        conn.execute(
            "UPDATE escape_run SET pursuer_ticks = ? WHERE id = 1",
            (new_ticks,),
        )

    # Calculate distance
    result["distance"] = _calc_distance(conn)

    conn.commit()
    return result


def _advance_pursuer_one_room(
    conn: sqlite3.Connection, state: dict
) -> Optional[int]:
    """Move pursuer one room toward the carrier via shortest path.

    Respects warded rooms (takes extra ticks).

    Returns new room ID or None.
    """
    pursuer_room = state["pursuer_room_id"]
    carrier_room = state["carrier_room_id"]

    if not pursuer_room or not carrier_room:
        return None

    if pursuer_room == carrier_room:
        return None

    # Check for blocker in adjacent rooms
    next_room = _get_next_room_toward(conn, pursuer_room, carrier_room)
    if not next_room:
        return None

    # Check if warded
    ward = conn.execute(
        "SELECT ward_active FROM rooms WHERE id = ?", (next_room,)
    ).fetchone()
    if ward and ward["ward_active"]:
        # Ward slows: consume the ward instead of advancing
        conn.execute(
            "UPDATE rooms SET ward_active = 0 WHERE id = ?", (next_room,)
        )
        conn.commit()
        return None  # Pursuer spent this advance breaking the ward

    # Check for blocker
    blocker = conn.execute(
        """SELECT p.id, p.name FROM players p
           WHERE p.room_id = ? AND p.state = 'dungeon'
           AND p.id != ?
           LIMIT 1""",
        (next_room, state.get("carrier_player_id", -1)),
    ).fetchone()

    if blocker:
        # Pursuer fights blocker instead of advancing
        msg = f"! {blocker['name']} is blocking the Pursuer!"
        broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
        _record_participant(conn, blocker["id"], "blocker")
        conn.commit()
        return None  # Pursuer stopped by blocker

    return next_room


def _get_next_room_toward(
    conn: sqlite3.Connection, from_room: int, to_room: int
) -> Optional[int]:
    """BFS to find next room on shortest path from -> to.

    Returns the first step room ID or None.
    """
    if from_room == to_room:
        return None

    visited = {from_room}
    queue = [(from_room, [from_room])]

    while queue:
        current, path = queue.pop(0)
        exits = conn.execute(
            "SELECT to_room_id FROM room_exits WHERE from_room_id = ?",
            (current,),
        ).fetchall()

        for ex in exits:
            next_id = ex["to_room_id"]
            if next_id == to_room:
                return path[1] if len(path) > 1 else next_id
            if next_id not in visited:
                visited.add(next_id)
                queue.append((next_id, path + [next_id]))

    return None


# ── Carrier Movement ───────────────────────────────────────────────────────


def update_carrier_position(
    conn: sqlite3.Connection, player_id: int, new_room_id: int
) -> None:
    """Update carrier's room position after movement."""
    state = get_escape_state(conn)
    if not state or not state["active"]:
        return
    if state["carrier_player_id"] != player_id:
        return

    conn.execute(
        "UPDATE escape_run SET carrier_room_id = ? WHERE id = 1",
        (new_room_id,),
    )
    conn.commit()


def is_carrier(conn: sqlite3.Connection, player_id: int) -> bool:
    """Check if player is the current carrier."""
    state = get_escape_state(conn)
    return bool(state and state["active"] and state["carrier_player_id"] == player_id)


# ── Carrier Death & Relay ──────────────────────────────────────────────────


def handle_carrier_death(
    conn: sqlite3.Connection, player_id: int
) -> Optional[str]:
    """Handle the carrier dying. Drops objective.

    Returns broadcast message or None.
    """
    state = get_escape_state(conn)
    if not state or not state["active"]:
        return None
    if state["carrier_player_id"] != player_id:
        return None

    player = conn.execute(
        "SELECT name, room_id, floor FROM players WHERE id = ?", (player_id,)
    ).fetchone()

    drop_room = state["carrier_room_id"]

    conn.execute(
        """UPDATE escape_run SET
           carrier_player_id = NULL,
           objective_dropped = 1,
           dropped_room_id = ?
           WHERE id = 1""",
        (drop_room,),
    )

    name = player["name"] if player else "The carrier"
    floor = player["floor"] if player else "?"
    obj = state["objective_name"]
    msg = f"X {name} fell on Floor {floor}. The {obj} lies unguarded."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    conn.commit()
    return msg


def pickup_objective(
    conn: sqlite3.Connection, player_id: int, player_room_id: int
) -> tuple[bool, str]:
    """Pick up a dropped objective.

    Returns:
        (success, message)
    """
    state = get_escape_state(conn)
    if not state:
        return False, "No escape run active."

    if not state["objective_dropped"]:
        return False, "Nothing to pick up here."

    if state["dropped_room_id"] != player_room_id:
        return False, "The objective isn't in this room."

    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    # Reset pursuer further back
    pursuer_room = _find_room_n_behind(conn, player_room_id, PURSUER_RELAY_RESET_DISTANCE)

    conn.execute(
        """UPDATE escape_run SET
           carrier_player_id = ?,
           carrier_room_id = ?,
           pursuer_room_id = ?,
           pursuer_ticks = 0,
           objective_dropped = 0,
           dropped_room_id = NULL
           WHERE id = 1""",
        (player_id, player_room_id, pursuer_room),
    )

    _record_participant(conn, player_id, "carrier")

    obj = state["objective_name"]
    msg = f"! {name} picks up the {obj}! Pursuer resets. The relay continues."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    conn.commit()
    return True, f"You picked up the {obj}! RUN!"


# ── Support Roles ──────────────────────────────────────────────────────────


def ward_room(
    conn: sqlite3.Connection, player_id: int, room_id: int
) -> tuple[bool, str]:
    """Ward a cleared room to slow the Pursuer.

    Returns:
        (success, message)
    """
    room = conn.execute(
        "SELECT htl_cleared, ward_active, is_breach, name FROM rooms WHERE id = ?",
        (room_id,),
    ).fetchone()

    if not room:
        return False, "Room not found."

    if room["ward_active"]:
        return False, "This room is already warded."

    conn.execute(
        "UPDATE rooms SET ward_active = 1 WHERE id = ?", (room_id,)
    )

    _record_participant(conn, player_id, "warder")
    conn.commit()
    return True, f"Room warded. Pursuer slowed here. (-{WARD_ACTION_COST} action)"


def lure_pursuer(
    conn: sqlite3.Connection, player_id: int, player_floor: int
) -> tuple[bool, str]:
    """Lure the Pursuer toward this player temporarily.

    Returns:
        (success, message)
    """
    state = get_escape_state(conn)
    if not state or not state["active"]:
        return False, "No active escape run."

    # Check player is on same floor as pursuer
    pursuer_room = conn.execute(
        "SELECT floor FROM rooms WHERE id = ?",
        (state["pursuer_room_id"],),
    ).fetchone()

    if not pursuer_room or pursuer_room["floor"] != player_floor:
        return False, "Must be on same floor as the Pursuer."

    player = conn.execute(
        "SELECT name, room_id FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    # Set lure diversion (stored as extra ticks on pursuer)
    conn.execute(
        """UPDATE escape_run SET pursuer_ticks = ?
           WHERE id = 1""",
        (-LURE_DIVERT_TICKS,),  # Negative ticks = diverted
    )

    _record_participant(conn, player_id, "lurer")

    room_name = conn.execute(
        "SELECT name FROM rooms WHERE id = ?", (player["room_id"],)
    ).fetchone()
    rname = room_name["name"] if room_name else "unknown"

    msg = f"! {name} lured the Pursuer into {rname}! It diverts."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    conn.commit()
    return True, f"Pursuer diverted! (-{LURE_ACTION_COST} actions)"


def _is_pursuer_diverted(conn: sqlite3.Connection, state: dict) -> bool:
    """Check if pursuer is currently diverted by a lure."""
    return state.get("pursuer_ticks", 0) < 0


def _decrement_divert(conn: sqlite3.Connection) -> None:
    """Decrement the divert counter."""
    conn.execute(
        "UPDATE escape_run SET pursuer_ticks = pursuer_ticks + 1 WHERE id = 1"
    )
    conn.commit()


# ── Win Condition ──────────────────────────────────────────────────────────


def check_delivery(
    conn: sqlite3.Connection, player_id: int, player_state: str
) -> tuple[bool, str]:
    """Check if carrier has reached town (delivered the objective).

    Returns:
        (delivered, message)
    """
    state = get_escape_state(conn)
    if not state or not state["active"]:
        return False, ""

    if state["carrier_player_id"] != player_id:
        return False, ""

    if player_state != "town":
        return False, ""

    # Delivered!
    conn.execute(
        "UPDATE escape_run SET active = 0, completed = 1 WHERE id = 1"
    )

    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    obj = state["objective_name"]
    msg = f"! The {obj} reached the surface! Victory! Delivered by {name}."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    conn.commit()
    return True, f"The {obj} has been delivered! VICTORY!"


# ── Distance Broadcasts ───────────────────────────────────────────────────


def broadcast_pursuer_distance(conn: sqlite3.Connection) -> None:
    """Broadcast Pursuer distance (called periodically)."""
    dist = _calc_distance(conn)
    if dist <= 0:
        return

    if dist <= 3:
        msg = f"! Pursuer is {dist} rooms behind. It's closing."
    else:
        msg = f"! Pursuer is {dist} rooms behind the carrier."

    broadcast_sys.create_broadcast(conn, 2, msg[:MSG_CHAR_LIMIT])
    conn.commit()


def _calc_distance(conn: sqlite3.Connection) -> int:
    """Calculate room distance between pursuer and carrier."""
    state = get_escape_state(conn)
    if not state or not state["pursuer_room_id"] or not state["carrier_room_id"]:
        return 0

    # BFS distance
    start = state["pursuer_room_id"]
    end = state["carrier_room_id"]
    if start == end:
        return 0

    visited = {start}
    queue = [(start, 0)]
    while queue:
        current, dist = queue.pop(0)
        exits = conn.execute(
            "SELECT to_room_id FROM room_exits WHERE from_room_id = ?",
            (current,),
        ).fetchall()
        for ex in exits:
            nid = ex["to_room_id"]
            if nid == end:
                return dist + 1
            if nid not in visited:
                visited.add(nid)
                queue.append((nid, dist + 1))

    return 99  # Unreachable


# ── Status Display ─────────────────────────────────────────────────────────


def format_rne_status(conn: sqlite3.Connection) -> str:
    """Format R&E status for display."""
    state = get_escape_state(conn)
    if not state:
        return "No escape run this epoch."

    if state["completed"]:
        return f"The {state['objective_name']} has been delivered! Victory!"

    if not state["active"]:
        return f"The {state['objective_name']} awaits on Floor {NUM_FLOORS}."

    if state["objective_dropped"]:
        room = conn.execute(
            "SELECT name, floor FROM rooms WHERE id = ?",
            (state["dropped_room_id"],),
        ).fetchone()
        if room:
            return f"{state['objective_name']} dropped in {room['name']} (F{room['floor']})"
        return f"{state['objective_name']} dropped! Someone pick it up!"

    dist = _calc_distance(conn)
    carrier = conn.execute(
        "SELECT name FROM players WHERE id = ?",
        (state["carrier_player_id"],),
    ).fetchone()
    cname = carrier["name"] if carrier else "?"

    return f"{cname} carries {state['objective_name']}. Pursuer: {dist} rooms behind."


# ── Helpers ────────────────────────────────────────────────────────────────


def _find_room_n_behind(
    conn: sqlite3.Connection, from_room: int, n: int
) -> int:
    """Find a room approximately N steps from from_room (toward floor 4).

    Used for Pursuer spawn positioning.
    """
    # BFS backward through exits
    visited = {from_room}
    current = from_room
    for _ in range(n):
        exits = conn.execute(
            "SELECT from_room_id FROM room_exits WHERE to_room_id = ?",
            (current,),
        ).fetchall()
        unvisited = [e["from_room_id"] for e in exits if e["from_room_id"] not in visited]
        if not unvisited:
            # Try forward exits instead
            fwd = conn.execute(
                "SELECT to_room_id FROM room_exits WHERE from_room_id = ?",
                (current,),
            ).fetchall()
            unvisited = [e["to_room_id"] for e in fwd if e["to_room_id"] not in visited]
        if unvisited:
            current = unvisited[0]
            visited.add(current)
        else:
            break

    return current


def _record_participant(
    conn: sqlite3.Connection, player_id: int, role: str
) -> None:
    """Record a participant in the escape run."""
    conn.execute(
        """INSERT INTO escape_participants (player_id, role)
           VALUES (?, ?)
           ON CONFLICT(player_id, role) DO NOTHING""",
        (player_id, role),
    )
    conn.commit()
