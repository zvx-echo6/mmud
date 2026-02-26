"""Tests for action handlers — full pipeline integration tests.

Uses in-memory SQLite with the real schema. No mocking.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import DUNGEON_ACTIONS_PER_DAY, MSG_CHAR_LIMIT
from src.core.engine import GameEngine
from src.db.database import init_schema


def make_test_db() -> sqlite3.Connection:
    """Create an in-memory database with schema and test world."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed_test_world(conn)
    return conn


def _seed_test_world(conn: sqlite3.Connection) -> None:
    """Minimal test world for action tests."""
    # Epoch
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1)"""
    )

    # Rooms: hub + one room with a monster
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Hub', 'Central hub. Passages everywhere. [n,e]',
                   'Hub. [n,e]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (2, 1, 'Rat Room', 'Rats gnaw at bones. [s]',
                   'Rat Room. [s]', 0)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (3, 1, 'Empty Hall', 'An empty hall. [w]',
                   'Empty Hall. [w]', 0)"""
    )

    # Exits
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (1, 2, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (2, 1, 's')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (1, 3, 'e')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (3, 1, 'w')"
    )

    # Monster in room 2
    conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (2, 'Giant Rat', 10, 10, 2, 1, 1, 10, 2, 5, 1)"""
    )

    conn.commit()


def register_player(engine: GameEngine, node_id: str = "!test1234", name: str = "Tester", cls: str = "w") -> str:
    """Register a new player via the engine. Returns the welcome response."""
    resp = engine.process_message(node_id, name, "join")
    assert "name" in resp.lower() or "Name" in resp
    resp = engine.process_message(node_id, name, name)
    assert "password" in resp.lower() or "Password" in resp
    resp = engine.process_message(node_id, name, "testpass")
    assert "class" in resp.lower() or "Pick" in resp
    resp = engine.process_message(node_id, name, cls)
    assert "Welcome" in resp
    return resp


def test_new_player_registration():
    """New player gets name prompt, then password, then class, then registers."""
    conn = make_test_db()
    engine = GameEngine(conn)

    resp = engine.process_message("!abc", "Alice", "join")
    assert "name" in resp.lower() or "Name" in resp
    assert len(resp) <= MSG_CHAR_LIMIT

    resp = engine.process_message("!abc", "Alice", "Alice")
    assert "password" in resp.lower() or "Password" in resp
    assert len(resp) <= MSG_CHAR_LIMIT

    resp = engine.process_message("!abc", "Alice", "test1234")
    assert "Pick" in resp or "class" in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT

    resp = engine.process_message("!abc", "Alice", "w")
    assert "Welcome" in resp
    assert "warrior" in resp.lower() or "Warrior" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_look_in_town():
    """LOOK in town shows town description."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "look")
    assert "Last Ember" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_enter_dungeon():
    """ENTER takes player from town to floor 1 hub."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "enter")
    assert "Hub" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_move_in_dungeon():
    """Moving in the dungeon navigates to a new room."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    engine.process_message("!test1234", "Tester", "enter")
    resp = engine.process_message("!test1234", "Tester", "e")
    assert "Empty Hall" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_move_invalid_direction():
    """Moving in an invalid direction shows error."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    engine.process_message("!test1234", "Tester", "enter")
    resp = engine.process_message("!test1234", "Tester", "w")
    assert "No exit" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_stats():
    """STATS shows player info."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "stats")
    assert "Tester" in resp
    assert "Lv1" in resp
    assert "POW" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_help_in_town():
    """HELP in town shows town commands."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "help")
    assert "ENTER" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_help_in_dungeon():
    """HELP in dungeon shows dungeon commands."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    engine.process_message("!test1234", "Tester", "enter")
    resp = engine.process_message("!test1234", "Tester", "help")
    assert "FIGHT" in resp or "N/S/E/W" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_fight_monster():
    """Fighting a monster produces combat output."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    engine.process_message("!test1234", "Tester", "enter")
    engine.process_message("!test1234", "Tester", "n")  # Move to rat room
    resp = engine.process_message("!test1234", "Tester", "fight")
    assert "Rat" in resp or "rat" in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_return_to_town():
    """TOWN returns player from dungeon."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    engine.process_message("!test1234", "Tester", "enter")
    resp = engine.process_message("!test1234", "Tester", "town")
    assert "Last Ember" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_action_budget_enforcement():
    """Running out of dungeon actions blocks further dungeon actions."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    engine.process_message("!test1234", "Tester", "enter")

    # Burn all 12 dungeon actions on moves (back and forth)
    for i in range(DUNGEON_ACTIONS_PER_DAY):
        direction = "e" if i % 2 == 0 else "w"
        engine.process_message("!test1234", "Tester", direction)

    # 13th action should be blocked
    resp = engine.process_message("!test1234", "Tester", "e")
    assert "No dungeon actions" in resp or "no dungeon" in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_all_responses_under_150():
    """Every response in a full session must be under 150 chars."""
    conn = make_test_db()
    engine = GameEngine(conn)

    responses = []
    responses.append(engine.process_message("!test1234", "Tester", "hello"))
    responses.append(engine.process_message("!test1234", "Tester", "w"))
    responses.append(engine.process_message("!test1234", "Tester", "look"))
    responses.append(engine.process_message("!test1234", "Tester", "stats"))
    responses.append(engine.process_message("!test1234", "Tester", "help"))
    responses.append(engine.process_message("!test1234", "Tester", "enter"))
    responses.append(engine.process_message("!test1234", "Tester", "look"))
    responses.append(engine.process_message("!test1234", "Tester", "help"))
    responses.append(engine.process_message("!test1234", "Tester", "n"))
    responses.append(engine.process_message("!test1234", "Tester", "fight"))
    responses.append(engine.process_message("!test1234", "Tester", "town"))

    for i, resp in enumerate(responses):
        assert resp is not None, f"Response {i} was None"
        assert len(resp) <= MSG_CHAR_LIMIT, (
            f"Response {i} exceeds {MSG_CHAR_LIMIT} chars ({len(resp)}): {resp}"
        )


if __name__ == "__main__":
    test_new_player_registration()
    test_look_in_town()
    test_enter_dungeon()
    test_move_in_dungeon()
    test_move_invalid_direction()
    test_stats()
    test_help_in_town()
    test_help_in_dungeon()
    test_fight_monster()
    test_return_to_town()
    test_action_budget_enforcement()
    test_all_responses_under_150()
    print("All action tests passed!")
