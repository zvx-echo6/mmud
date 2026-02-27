"""Tests for floor reveal system — only unlocked floors shown on dashboard."""

import pytest

from src.db.database import get_db, init_schema
from src.web import create_app
from src.web.services import gamedb


@pytest.fixture
def app(tmp_path):
    """Create test Flask app with fresh database."""
    db_path = str(tmp_path / "test.db")
    conn = get_db(db_path)
    conn.close()
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def seed_floors(app):
    """Insert 8 floor themes and a player for floor_progress."""
    db_path = app.config["MMUD_DB_PATH"]
    conn = get_db(db_path)
    # Insert 8 floor themes
    for i in range(1, 9):
        conn.execute(
            "INSERT INTO floor_themes (floor, floor_name, atmosphere, narrative_beat, floor_transition) "
            "VALUES (?, ?, ?, ?, ?)",
            (i, f"Floor {i} Name", f"Atmosphere for floor {i}", f"Beat {i}", f"Transition {i}"),
        )
    # Insert a dummy account and player for floor_progress
    conn.execute(
        "INSERT INTO accounts (mesh_id, handle) VALUES ('test', 'tester')"
    )
    conn.execute(
        "INSERT INTO players (account_id, name, class, hp, hp_max, pow, def, spd, state) "
        "VALUES (1, 'Tester', 'warrior', 50, 50, 3, 2, 1, 'town')"
    )
    conn.commit()
    conn.close()


def test_only_floor_1_visible_no_bosses(app, seed_floors):
    """With no bosses killed, only floor 1 is visible."""
    with app.app_context():
        floors, total = gamedb.get_floor_themes_public()
        assert total == 8
        assert len(floors) == 1
        assert floors[0]["floor"] == 1


def test_floors_1_2_visible_after_floor1_boss(app, seed_floors):
    """After floor 1 boss killed, floors 1-2 are visible."""
    db_path = app.config["MMUD_DB_PATH"]
    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO floor_progress (player_id, floor, boss_killed) VALUES (1, 1, 1)"
    )
    conn.commit()
    conn.close()

    with app.app_context():
        floors, total = gamedb.get_floor_themes_public()
        assert total == 8
        assert len(floors) == 2
        assert [f["floor"] for f in floors] == [1, 2]


def test_floors_1_to_3_visible_after_floor2_boss(app, seed_floors):
    """After floor 2 boss killed, floors 1-3 are visible."""
    db_path = app.config["MMUD_DB_PATH"]
    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO floor_progress (player_id, floor, boss_killed) VALUES (1, 1, 1)"
    )
    conn.execute(
        "INSERT INTO floor_progress (player_id, floor, boss_killed) VALUES (1, 2, 1)"
    )
    conn.commit()
    conn.close()

    with app.app_context():
        floors, total = gamedb.get_floor_themes_public()
        assert total == 8
        assert len(floors) == 3
        assert [f["floor"] for f in floors] == [1, 2, 3]


def test_all_floors_visible_all_bosses_killed(app, seed_floors):
    """After all bosses killed, all 8 floors visible."""
    db_path = app.config["MMUD_DB_PATH"]
    conn = get_db(db_path)
    for f in range(1, 8):
        conn.execute(
            "INSERT INTO floor_progress (player_id, floor, boss_killed) VALUES (1, ?, 1)",
            (f,),
        )
    conn.commit()
    conn.close()

    with app.app_context():
        floors, total = gamedb.get_floor_themes_public()
        assert total == 8
        assert len(floors) == 8


def test_any_player_boss_kill_reveals_floor(app, seed_floors):
    """A boss kill by ANY player reveals the floor for everyone."""
    db_path = app.config["MMUD_DB_PATH"]
    conn = get_db(db_path)
    # Second player
    conn.execute(
        "INSERT INTO accounts (mesh_id, handle) VALUES ('test2', 'tester2')"
    )
    conn.execute(
        "INSERT INTO players (account_id, name, class, hp, hp_max, pow, def, spd, state) "
        "VALUES (2, 'Other', 'rogue', 40, 40, 2, 1, 3, 'town')"
    )
    # Player 2 kills floor 1 boss
    conn.execute(
        "INSERT INTO floor_progress (player_id, floor, boss_killed) VALUES (2, 1, 1)"
    )
    conn.commit()
    conn.close()

    with app.app_context():
        floors, total = gamedb.get_floor_themes_public()
        assert len(floors) == 2
        assert [f["floor"] for f in floors] == [1, 2]


def test_no_floor_themes_returns_empty(app):
    """With no floor themes at all, returns empty list."""
    with app.app_context():
        floors, total = gamedb.get_floor_themes_public()
        assert len(floors) == 0
        assert total == 0
