"""Tests for floor boss gate mechanics.

Boss gate: player cannot descend to next floor unless floor boss is dead
AND player has visited the boss room (per-player progress via floor_progress table).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import NUM_FLOORS
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
    """Seed a 2-floor dungeon with bosses and stairway connections."""
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

    # Floor 1: hub + boss room + stairway down
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (10, 1, 'F1 Hub', 'Floor 1 hub.', 'F1 Hub. [n,s]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (11, 1, 'F1 Boss Room', 'Boss lair.', 'Boss lair. [s]', 0)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub, is_stairway)
           VALUES (12, 1, 'F1 Stairs', 'Stairs down.', 'Stairs. [n,d]', 0, 1)"""
    )

    # Floor 2: hub + regular room
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (20, 2, 'F2 Hub', 'Floor 2 hub.', 'F2 Hub. [n,u]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (21, 2, 'F2 Room', 'A dark room.', 'Dark room. [s]', 0)"""
    )

    # Exits: town -> F1 hub
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (1, 10, 'n')"
    )
    # F1 hub <-> boss room
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (10, 11, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (11, 10, 's')"
    )
    # F1 hub <-> stairway
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (10, 12, 's')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (12, 10, 'n')"
    )
    # F1 stairway -> F2 hub (descend)
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (12, 20, 'd')"
    )
    # F2 hub -> F1 stairway (ascend)
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (20, 12, 'u')"
    )
    # F2 hub <-> F2 room
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (20, 21, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (21, 20, 's')"
    )

    # Floor 1 boss (alive, in room 11)
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier, is_floor_boss)
           VALUES (1, 11, 'Floor Guardian', 20, 20, 3, 2, 1, 30, 5, 10, 1, 1)"""
    )

    # Regular monster on floor 2
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (2, 21, 'Shadow Rat', 10, 10, 2, 1, 1, 10, 2, 5, 2)"""
    )

    conn.commit()


def _register(engine: GameEngine, node_id: str = "!test1234", name: str = "Tester") -> None:
    """Register a warrior player."""
    engine.process_message(node_id, name, "join")
    engine.process_message(node_id, name, name)
    engine.process_message(node_id, name, "testpass")
    engine.process_message(node_id, name, "w")


def _place_in_dungeon(conn: sqlite3.Connection, player_id: int, room_id: int, floor: int) -> None:
    """Place player directly in a dungeon room."""
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = ?, room_id = ? WHERE id = ?",
        (floor, room_id, player_id),
    )
    conn.commit()


def _get_player(conn: sqlite3.Connection, node_id: str = "!test1234") -> dict:
    """Get player by session."""
    from src.models import player as player_model
    p = player_model.get_player_by_session(conn, node_id)
    if p:
        return p
    row = conn.execute(
        """SELECT p.* FROM players p
           JOIN accounts a ON p.account_id = a.id
           WHERE a.mesh_id = ?
           ORDER BY p.created_at DESC LIMIT 1""",
        (node_id,),
    ).fetchone()
    return dict(row)


# ── Boss gate blocks descent ──


def test_cannot_descend_with_boss_alive():
    """Player cannot descend from F1 to F2 while floor boss is alive."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    _place_in_dungeon(conn, player["id"], 12, 1)  # F1 stairway

    resp = engine.process_message("!test1234", "Tester", "d")
    assert "sealed" in resp.lower() or "boss" in resp.lower()


def test_can_descend_after_boss_killed_and_visited():
    """Player CAN descend after killing the floor boss."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)

    # Record boss gate unlock for this player
    conn.execute(
        """INSERT INTO floor_progress (player_id, floor, boss_killed, boss_killed_at)
           VALUES (?, 1, 1, CURRENT_TIMESTAMP)""",
        (player["id"],),
    )
    conn.commit()

    _place_in_dungeon(conn, player["id"], 12, 1)  # F1 stairway

    resp = engine.process_message("!test1234", "Tester", "d")
    assert "F2" in resp or "Hub" in resp or "Floor 2" in resp or "sealed" not in resp.lower()


def test_boss_gate_does_not_block_upward():
    """Boss gate only blocks descending, not ascending."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)

    # Manually place on F2 without any floor_progress
    _place_in_dungeon(conn, player["id"], 20, 2)

    resp = engine.process_message("!test1234", "Tester", "u")
    # Should go back to F1 stairway — no gate blocks upward
    assert "sealed" not in resp.lower()


# ── Per-player visit mechanic ──


def test_visiting_dead_boss_room_records_progress():
    """Walking into a room with a dead floor boss auto-records floor_progress."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)

    # Kill the boss directly in DB
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = 1")
    conn.commit()

    # Place player at F1 hub and move to boss room
    _place_in_dungeon(conn, player["id"], 10, 1)
    engine.process_message("!test1234", "Tester", "n")  # Move to boss room 11

    # Check floor_progress was auto-recorded
    row = conn.execute(
        "SELECT boss_killed FROM floor_progress WHERE player_id = ? AND floor = 1",
        (player["id"],),
    ).fetchone()
    assert row is not None
    assert row["boss_killed"] == 1


def test_player_without_visit_still_blocked():
    """Another player who hasn't visited the dead boss room is still blocked."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine, "!test1234", "Alice")
    _register(engine, "!test5678", "Bob")

    alice = _get_player(conn, "!test1234")
    bob = _get_player(conn, "!test5678")

    # Kill the boss
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = 1")
    conn.commit()

    # Alice visits the boss room — gets progress
    _place_in_dungeon(conn, alice["id"], 10, 1)
    engine.process_message("!test1234", "Alice", "n")

    # Bob goes straight to stairway without visiting boss room
    _place_in_dungeon(conn, bob["id"], 12, 1)
    resp = engine.process_message("!test5678", "Bob", "d")
    assert "sealed" in resp.lower() or "boss" in resp.lower()


# ── deepest_floor_reached tracking ──


def test_deepest_floor_updates_on_boss_kill():
    """Killing a floor boss updates deepest_floor_reached."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)

    # Set boss HP low enough to kill in one hit
    conn.execute("UPDATE monsters SET hp = 1 WHERE id = 1")
    # Give player high POW to guarantee kill
    conn.execute("UPDATE players SET pow = 50 WHERE id = ?", (player["id"],))
    conn.commit()

    # Enter dungeon and fight boss
    _place_in_dungeon(conn, player["id"], 11, 1)
    # Enter combat state
    conn.execute(
        "UPDATE players SET state = 'combat', combat_monster_id = 1 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()

    engine.process_message("!test1234", "Tester", "fight")

    # Check deepest_floor_reached updated
    updated = _get_player(conn)
    assert updated["deepest_floor_reached"] >= 2


def test_deepest_floor_updates_on_dead_boss_visit():
    """Visiting a dead boss room updates deepest_floor_reached."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)
    assert player["deepest_floor_reached"] == 1

    # Kill boss in DB
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = 1")
    conn.commit()

    # Visit the boss room
    _place_in_dungeon(conn, player["id"], 10, 1)
    engine.process_message("!test1234", "Tester", "n")

    updated = _get_player(conn)
    assert updated["deepest_floor_reached"] >= 2


# ── floor_progress table ──


def test_floor_progress_populated_on_boss_kill():
    """Killing a floor boss creates a floor_progress row."""
    conn = _make_db()
    engine = GameEngine(conn)
    _register(engine)

    player = _get_player(conn)

    # Set boss HP low, player POW high
    conn.execute("UPDATE monsters SET hp = 1 WHERE id = 1")
    conn.execute("UPDATE players SET pow = 50 WHERE id = ?", (player["id"],))
    conn.commit()

    _place_in_dungeon(conn, player["id"], 11, 1)
    conn.execute(
        "UPDATE players SET state = 'combat', combat_monster_id = 1 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()

    engine.process_message("!test1234", "Tester", "fight")

    row = conn.execute(
        "SELECT * FROM floor_progress WHERE player_id = ? AND floor = 1",
        (player["id"],),
    ).fetchone()
    assert row is not None
    assert row["boss_killed"] == 1
    assert row["boss_killed_at"] is not None
