"""Tests for free traversal on cleared floors and fast travel from town.

Free traversal: movement on a cleared floor (boss killed + visited) costs 0 dungeon actions.
Fast travel: ENTER <N> / WARP <N> / FT <N> from town warps to floor N hub.
Monster retreat: cleared floors show "retreats deeper" instead of combat encounter.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import DUNGEON_ACTIONS_PER_DAY, NUM_FLOORS
from src.core.engine import GameEngine
from src.db.database import init_schema


def _make_db() -> sqlite3.Connection:
    """Create an in-memory database with a multi-floor test world."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed_world(conn)
    return conn


def _seed_world(conn: sqlite3.Connection) -> None:
    """Seed a 3-floor dungeon for traversal tests."""
    # Epoch
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1)"""
    )

    # Town center (floor 0, hub)
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 0, 'Last Ember', 'Town center.', 'Town. [n]', 1)"""
    )

    # Floor 1: hub + monster room + empty room
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (10, 1, 'F1 Hub', 'Floor 1 hub.', 'F1 Hub. [n,e]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (11, 1, 'Monster Den', 'A dark den.', 'Monster Den. [s]', 0)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (12, 1, 'Empty Corridor', 'A quiet corridor.', 'Corridor. [w]', 0)"""
    )

    # Floor 2: hub
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (20, 2, 'F2 Hub', 'Floor 2 hub.', 'F2 Hub. [n]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (21, 2, 'F2 Room', 'Floor 2 room.', 'F2 Room. [s]', 0)"""
    )

    # Floor 3: hub
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (30, 3, 'F3 Hub', 'Floor 3 hub.', 'F3 Hub. [n]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (31, 3, 'F3 Room', 'Floor 3 room.', 'F3 Room. [s]', 0)"""
    )

    # Exits: F1 hub <-> monster den
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (10, 11, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (11, 10, 's')"
    )
    # F1 hub <-> corridor
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (10, 12, 'e')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (12, 10, 'w')"
    )
    # F2 hub <-> F2 room
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (20, 21, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (21, 20, 's')"
    )
    # F3 hub <-> F3 room
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (30, 31, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (31, 30, 's')"
    )

    # Monster in room 11 (floor 1)
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (1, 11, 'Cave Lurker', 10, 10, 2, 1, 1, 10, 2, 5, 1)"""
    )

    conn.commit()


def _register(engine: GameEngine, node_id: str = "!test1234", name: str = "Tester") -> None:
    """Register a warrior player."""
    engine.process_message(node_id, name, "hello")
    engine.process_message(node_id, name, "w")


def _get_player(conn: sqlite3.Connection, node_id: str = "!test1234") -> dict:
    """Get player by node id."""
    row = conn.execute(
        """SELECT p.* FROM players p
           JOIN accounts a ON p.account_id = a.id
           WHERE a.mesh_id = ?
           ORDER BY p.created_at DESC LIMIT 1""",
        (node_id,),
    ).fetchone()
    return dict(row)


def _place_in_dungeon(conn: sqlite3.Connection, player_id: int, room_id: int, floor: int) -> None:
    """Place player directly in a dungeon room."""
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = ?, room_id = ? WHERE id = ?",
        (floor, room_id, player_id),
    )
    conn.commit()


def _clear_floor(conn: sqlite3.Connection, player_id: int, floor: int) -> None:
    """Record that a floor is cleared for a player (boss killed + visited)."""
    conn.execute(
        """INSERT OR REPLACE INTO floor_progress
           (player_id, floor, boss_killed, boss_killed_at)
           VALUES (?, ?, 1, CURRENT_TIMESTAMP)""",
        (player_id, floor),
    )
    conn.commit()


# ── Free traversal ──


def test_move_on_cleared_floor_costs_zero_actions():
    """Moving on a cleared floor should NOT consume a dungeon action."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    _clear_floor(conn, player["id"], 1)
    _place_in_dungeon(conn, player["id"], 10, 1)

    actions_before = conn.execute(
        "SELECT dungeon_actions_remaining FROM players WHERE id = ?",
        (player["id"],),
    ).fetchone()["dungeon_actions_remaining"]

    engine.process_message("!test1234", "Tester", "e")  # Move to corridor

    actions_after = conn.execute(
        "SELECT dungeon_actions_remaining FROM players WHERE id = ?",
        (player["id"],),
    ).fetchone()["dungeon_actions_remaining"]

    assert actions_after == actions_before, (
        f"Expected {actions_before} actions, got {actions_after} (should be free on cleared floor)"
    )


def test_move_on_uncleared_floor_costs_one_action():
    """Moving on an uncleared floor should consume 1 dungeon action."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    _place_in_dungeon(conn, player["id"], 10, 1)

    actions_before = conn.execute(
        "SELECT dungeon_actions_remaining FROM players WHERE id = ?",
        (player["id"],),
    ).fetchone()["dungeon_actions_remaining"]

    engine.process_message("!test1234", "Tester", "e")  # Move to corridor

    actions_after = conn.execute(
        "SELECT dungeon_actions_remaining FROM players WHERE id = ?",
        (player["id"],),
    ).fetchone()["dungeon_actions_remaining"]

    assert actions_after == actions_before - 1


def test_cannot_move_with_zero_actions_uncleared():
    """Cannot move on an uncleared floor when dungeon actions are exhausted."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    conn.execute(
        "UPDATE players SET dungeon_actions_remaining = 0 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()
    _place_in_dungeon(conn, player["id"], 10, 1)

    resp = engine.process_message("!test1234", "Tester", "e")
    assert "no dungeon actions" in resp.lower() or "actions left" in resp.lower()


def test_can_move_with_zero_actions_on_cleared_floor():
    """CAN move on a cleared floor even when dungeon actions are exhausted."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    _clear_floor(conn, player["id"], 1)
    conn.execute(
        "UPDATE players SET dungeon_actions_remaining = 0 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()
    _place_in_dungeon(conn, player["id"], 10, 1)

    resp = engine.process_message("!test1234", "Tester", "e")
    # Should succeed — corridor room
    assert "Corridor" in resp or "corridor" in resp.lower()
    assert "no dungeon actions" not in resp.lower()


# ── Monster retreat ──


def test_monster_retreat_flavor_on_cleared_floor():
    """Monster on a cleared floor shows retreat flavor instead of combat."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    _clear_floor(conn, player["id"], 1)
    _place_in_dungeon(conn, player["id"], 10, 1)

    resp = engine.process_message("!test1234", "Tester", "n")  # Move to monster den
    assert "retreats" in resp.lower(), f"Expected retreat flavor, got: {resp}"


def test_monster_encounter_on_uncleared_floor():
    """Monster on an uncleared floor shows normal encounter."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    _place_in_dungeon(conn, player["id"], 10, 1)

    resp = engine.process_message("!test1234", "Tester", "n")
    assert "blocks" in resp.lower() or "lurker" in resp.lower()


# ── Fast travel ──


def test_fast_travel_to_unlocked_floor():
    """ENTER <floor> warps to that floor's hub if unlocked."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    # Unlock floor 2 by setting deepest_floor_reached
    conn.execute(
        "UPDATE players SET deepest_floor_reached = 3 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()

    resp = engine.process_message("!test1234", "Tester", "enter 2")
    assert "F2" in resp or "Hub" in resp or "Floor 2" in resp


def test_cannot_fast_travel_beyond_deepest():
    """Cannot fast travel to a floor beyond deepest_floor_reached."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    # deepest_floor_reached defaults to 1

    resp = engine.process_message("!test1234", "Tester", "enter 3")
    assert "can't reach" in resp.lower() or "deepest" in resp.lower()


def test_fast_travel_warp_alias():
    """WARP <floor> works as alias for ENTER <floor>."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    conn.execute(
        "UPDATE players SET deepest_floor_reached = 3 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()

    resp = engine.process_message("!test1234", "Tester", "warp 2")
    assert "F2" in resp or "Hub" in resp or "Floor 2" in resp


def test_fast_travel_ft_alias():
    """FT <floor> works as alias for ENTER <floor>."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    conn.execute(
        "UPDATE players SET deepest_floor_reached = 3 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()

    resp = engine.process_message("!test1234", "Tester", "ft 2")
    assert "F2" in resp or "Hub" in resp or "Floor 2" in resp


def test_enter_no_args_goes_to_floor_1():
    """ENTER with no args goes to floor 1."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    resp = engine.process_message("!test1234", "Tester", "enter")
    assert "F1" in resp or "Hub" in resp or "Floor 1" in resp
