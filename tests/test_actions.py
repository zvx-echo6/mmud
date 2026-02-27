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
    """TOWN/RETURN returns player from dungeon with narrative."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    engine.process_message("!test1234", "Tester", "enter")
    resp = engine.process_message("!test1234", "Tester", "town")
    assert "Town" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_action_budget_enforcement():
    """Running out of dungeon actions blocks further dungeon actions."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)

    engine.process_message("!test1234", "Tester", "enter")

    # Burn all movement actions on moves (back and forth)
    for i in range(DUNGEON_ACTIONS_PER_DAY):
        direction = "e" if i % 2 == 0 else "w"
        engine.process_message("!test1234", "Tester", direction)

    # Next action should be blocked
    resp = engine.process_message("!test1234", "Tester", "e")
    assert "No movement actions" in resp or "no movement" in resp.lower()
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


# ── Free Combat Tests ─────────────────────────────────────────────────────


def _enter_combat(engine, node_id="!test1234", name="Tester"):
    """Enter dungeon, move to monster room, enter combat."""
    engine.process_message(node_id, name, "enter")
    engine.process_message(node_id, name, "n")  # Move to rat room → combat


def _drain_actions(conn, player_id):
    """Set dungeon_actions_remaining to 0."""
    conn.execute(
        "UPDATE players SET dungeon_actions_remaining = 0 WHERE id = ?",
        (player_id,),
    )
    conn.commit()


def test_fight_with_zero_actions():
    """Fight works with 0 dungeon actions — combat is free."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    _enter_combat(engine)
    # Drain actions
    p = conn.execute("SELECT id FROM players LIMIT 1").fetchone()
    _drain_actions(conn, p["id"])
    # Fight should still work
    resp = engine.process_message("!test1234", "Tester", "fight")
    assert "No movement" not in resp
    assert "no dungeon" not in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_flee_with_zero_actions():
    """Flee works with 0 dungeon actions — combat is free."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    _enter_combat(engine)
    p = conn.execute("SELECT id FROM players LIMIT 1").fetchone()
    _drain_actions(conn, p["id"])
    resp = engine.process_message("!test1234", "Tester", "flee")
    assert "No movement" not in resp
    assert "no dungeon" not in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_move_with_zero_actions_blocked():
    """Movement is blocked with 0 actions."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    engine.process_message("!test1234", "Tester", "enter")
    p = conn.execute("SELECT id FROM players LIMIT 1").fetchone()
    _drain_actions(conn, p["id"])
    resp = engine.process_message("!test1234", "Tester", "n")
    assert "No movement actions" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_player_starts_with_8_actions():
    """New players start with 8 dungeon actions (not 12)."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    p = conn.execute("SELECT dungeon_actions_remaining FROM players LIMIT 1").fetchone()
    assert p["dungeon_actions_remaining"] == DUNGEON_ACTIONS_PER_DAY
    assert p["dungeon_actions_remaining"] == 8


# ── RETURN Command Tests ─────────────────────────────────────────────────


def test_return_from_dungeon_floor1():
    """RETURN from dungeon floor 1 gives narrative + back in town."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    engine.process_message("!test1234", "Tester", "enter")
    resp = engine.process_message("!test1234", "Tester", "return")
    assert "Town" in resp
    assert "first floor" in resp.lower() or "entrance" in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT
    # Verify player is in town state
    p = conn.execute("SELECT state FROM players LIMIT 1").fetchone()
    assert p["state"] == "town"


def test_return_from_town():
    """RETURN from town says already in town."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    resp = engine.process_message("!test1234", "Tester", "return")
    assert "already in town" in resp.lower()


def test_return_from_combat():
    """RETURN from combat says flee first."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    _enter_combat(engine)
    resp = engine.process_message("!test1234", "Tester", "return")
    assert "FLEE" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_retreat_alias():
    """RETREAT is an alias for RETURN."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    engine.process_message("!test1234", "Tester", "enter")
    resp = engine.process_message("!test1234", "Tester", "retreat")
    assert "Town" in resp
    p = conn.execute("SELECT state FROM players LIMIT 1").fetchone()
    assert p["state"] == "town"


def test_return_deep_floor_narrative():
    """RETURN from a deep floor gives longer narrative."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    engine.process_message("!test1234", "Tester", "enter")
    # Manually set floor to 6
    conn.execute("UPDATE players SET floor = 6")
    conn.commit()
    resp = engine.process_message("!test1234", "Tester", "return")
    assert "Town" in resp
    assert "6" in resp or "tavern" in resp.lower() or "shaking" in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_charge_with_zero_actions():
    """Charge works with 0 dungeon actions (costs Focus, not actions)."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine, cls="w")  # warrior
    _enter_combat(engine)
    p = conn.execute("SELECT id FROM players LIMIT 1").fetchone()
    _drain_actions(conn, p["id"])
    resp = engine.process_message("!test1234", "Tester", "charge")
    assert "No movement" not in resp
    assert "no dungeon" not in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_sneak_with_zero_actions():
    """Sneak works with 0 dungeon actions (costs Tricks, not actions)."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine, node_id="!rog1", name="Rogue1", cls="r")
    _enter_combat(engine, node_id="!rog1", name="Rogue1")
    p = conn.execute("SELECT id FROM players LIMIT 1").fetchone()
    _drain_actions(conn, p["id"])
    resp = engine.process_message("!rog1", "Rogue1", "sneak")
    assert "No movement" not in resp
    assert "no dungeon" not in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_cast_with_zero_actions():
    """Cast works with 0 dungeon actions (costs Mana, not actions)."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine, node_id="!cas1", name="Caster1", cls="c")
    _enter_combat(engine, node_id="!cas1", name="Caster1")
    p = conn.execute("SELECT id FROM players LIMIT 1").fetchone()
    _drain_actions(conn, p["id"])
    resp = engine.process_message("!cas1", "Caster1", "cast")
    assert "No movement" not in resp
    assert "no dungeon" not in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_full_combat_zero_actions():
    """Full combat scenario: fight until 0 actions, keep fighting, flee."""
    conn = make_test_db()
    engine = GameEngine(conn)
    register_player(engine)
    _enter_combat(engine)
    p = conn.execute("SELECT id FROM players LIMIT 1").fetchone()
    _drain_actions(conn, p["id"])

    # Fight multiple times with 0 actions
    for _ in range(3):
        resp = engine.process_message("!test1234", "Tester", "fight")
        assert "No movement" not in resp

    # Flee should also work
    resp = engine.process_message("!test1234", "Tester", "flee")
    assert "No movement" not in resp
    assert len(resp) <= MSG_CHAR_LIMIT


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
    test_fight_with_zero_actions()
    test_flee_with_zero_actions()
    test_move_with_zero_actions_blocked()
    test_player_starts_with_8_actions()
    test_return_from_dungeon_floor1()
    test_return_from_town()
    test_return_from_combat()
    test_retreat_alias()
    test_return_deep_floor_narrative()
    test_full_combat_zero_actions()
    print("All action tests passed!")
