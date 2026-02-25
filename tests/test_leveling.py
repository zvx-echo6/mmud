"""Tests for XP, leveling, and stat training."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import MAX_LEVEL, MSG_CHAR_LIMIT, STAT_POINTS_PER_LEVEL, XP_PER_LEVEL
from src.core.engine import GameEngine
from src.db.database import init_schema
from src.models import player as player_model


def make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed_test_world(conn)
    return conn


def _seed_test_world(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Hub', 'Central hub. [n,e]', 'Hub. [n,e]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (2, 1, 'Arena', 'Training ground. [s]', 'Arena. [s]', 0)"""
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (1, 2, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (2, 1, 's')"
    )
    # Weak monster that gives lots of XP for testing
    conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (2, 'Training Dummy', 1, 1, 1, 0, 0, 200, 10, 20, 1)"""
    )
    conn.execute(
        """INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod)
           VALUES ('Rusty Sword', 'weapon', 1, 2, 0, 0)"""
    )
    conn.commit()


def _register_and_enter(engine: GameEngine, node_id: str = "!test1234"):
    """Register a player and enter the dungeon."""
    engine.process_message(node_id, "Tester", "hello")
    engine.process_message(node_id, "Tester", "w")  # warrior
    engine.process_message(node_id, "Tester", "enter")
    return node_id


def test_xp_awarded_on_kill():
    """Killing a monster awards XP."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    engine.process_message("!test1234", "Tester", "n")  # Move to arena
    resp = engine.process_message("!test1234", "Tester", "fight")

    assert "xp" in resp.lower() or "+200xp" in resp
    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    assert player["xp"] >= 200


def test_level_up_grants_stat_points():
    """Level up grants STAT_POINTS_PER_LEVEL stat points."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    assert player["stat_points"] == 0

    # Manually give enough XP to level up
    player_model.award_xp(conn, player["id"], XP_PER_LEVEL[1])

    player = player_model.get_player(conn, player["id"])
    assert player["level"] == 2
    assert player["stat_points"] == STAT_POINTS_PER_LEVEL


def test_multi_level_up():
    """Gaining enough XP for multiple levels grants cumulative stat points."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    # Give enough XP to go from level 1 to level 4 (XP_PER_LEVEL[3] = 500)
    player_model.award_xp(conn, player["id"], 500)

    player = player_model.get_player(conn, player["id"])
    assert player["level"] == 4
    assert player["stat_points"] == 3 * STAT_POINTS_PER_LEVEL  # 3 levels gained


def test_train_stat_pow():
    """TRAIN POW spends a stat point and increases POW."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    original_pow = player["pow"]

    # Give stat points
    player_model.update_state(conn, player["id"], stat_points=2)
    engine.process_message("!test1234", "Tester", "town")

    resp = engine.process_message("!test1234", "Tester", "train pow")
    assert "POW" in resp
    assert len(resp) <= MSG_CHAR_LIMIT

    player = player_model.get_player(conn, player["id"])
    assert player["pow"] == original_pow + 1
    assert player["stat_points"] == 1


def test_train_stat_def():
    """TRAIN DEF spends a stat point and increases DEF."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    original_def = player["def"]
    player_model.update_state(conn, player["id"], stat_points=1)
    engine.process_message("!test1234", "Tester", "town")

    resp = engine.process_message("!test1234", "Tester", "train def")
    assert "DEF" in resp

    player = player_model.get_player(conn, player["id"])
    assert player["def"] == original_def + 1
    assert player["stat_points"] == 0


def test_train_stat_spd():
    """TRAIN SPD spends a stat point and increases SPD."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    original_spd = player["spd"]
    player_model.update_state(conn, player["id"], stat_points=1)
    engine.process_message("!test1234", "Tester", "town")

    resp = engine.process_message("!test1234", "Tester", "train spd")
    assert "SPD" in resp

    player = player_model.get_player(conn, player["id"])
    assert player["spd"] == original_spd + 1


def test_train_no_points():
    """TRAIN with 0 stat points fails."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)
    engine.process_message("!test1234", "Tester", "town")

    resp = engine.process_message("!test1234", "Tester", "train pow")
    assert "No stat points" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_train_only_in_town():
    """TRAIN fails when not in town."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    resp = engine.process_message("!test1234", "Tester", "train pow")
    assert "town" in resp.lower()


def test_train_no_arg_shows_points():
    """TRAIN with no argument shows available stat points."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    player_model.update_state(conn, player["id"], stat_points=3)
    engine.process_message("!test1234", "Tester", "town")

    resp = engine.process_message("!test1234", "Tester", "train")
    assert "3" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_level_up_increases_hp():
    """Level up increases HP max by 5 per level."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    original_hp_max = player["hp_max"]

    player_model.award_xp(conn, player["id"], XP_PER_LEVEL[1])

    player = player_model.get_player(conn, player["id"])
    assert player["hp_max"] == original_hp_max + 5


def test_stats_shows_stat_points():
    """STATS display includes stat points when > 0."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register_and_enter(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    player_model.update_state(conn, player["id"], stat_points=2)

    resp = engine.process_message("!test1234", "Tester", "stats")
    assert "SP:2" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


if __name__ == "__main__":
    test_xp_awarded_on_kill()
    test_level_up_grants_stat_points()
    test_multi_level_up()
    test_train_stat_pow()
    test_train_stat_def()
    test_train_stat_spd()
    test_train_no_points()
    test_train_only_in_town()
    test_train_no_arg_shows_points()
    test_level_up_increases_hp()
    test_stats_shows_stat_points()
    print("All leveling tests passed!")
