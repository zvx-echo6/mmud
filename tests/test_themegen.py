"""Tests for floor sub-theme generation."""

import sqlite3
import pytest

from config import LLM_OUTPUT_CHAR_LIMIT, NUM_FLOORS
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.generation.themegen import generate_floor_themes, get_floor_themes
from src.generation.worldgen import generate_town, generate_world
from src.generation.bossgen import generate_bosses
from src.generation.bountygen import generate_bounties
from src.generation.secretgen import generate_secrets
from src.generation.breachgen import generate_breach
from src.generation.validation import validate_epoch
from src.models.epoch import create_epoch
from src.models import player as player_model
from src.core import world as world_mgr
from src.models import world as world_data


@pytest.fixture
def conn():
    """In-memory DB with schema."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    return c


@pytest.fixture
def backend():
    return DummyBackend()


@pytest.fixture
def epoch_conn(conn, backend):
    """DB with epoch, floor themes, town, and world generated."""
    create_epoch(conn, 1, "hold_the_line", "heist")
    generate_floor_themes(conn, backend)
    generate_town(conn, backend)
    floor_themes = get_floor_themes(conn)
    generate_world(conn, backend, floor_themes=floor_themes)
    return conn


# ── DummyBackend tests ────────────────────────────────────────────────────


def test_dummy_produces_all_floors(backend):
    """DummyBackend produces entries for all 4 floors."""
    themes = backend.generate_floor_themes()
    assert len(themes) == NUM_FLOORS
    for floor in range(1, NUM_FLOORS + 1):
        assert floor in themes


def test_each_floor_has_required_fields(backend):
    """Each floor has floor_name, atmosphere, narrative_beat, floor_transition."""
    themes = backend.generate_floor_themes()
    required = {"floor_name", "atmosphere", "narrative_beat", "floor_transition"}
    for floor in range(1, NUM_FLOORS + 1):
        assert required == set(themes[floor].keys())


def test_all_fields_under_char_limit(backend):
    """All text fields <= 150 chars."""
    themes = backend.generate_floor_themes()
    for floor, theme in themes.items():
        for field, val in theme.items():
            assert len(val) <= LLM_OUTPUT_CHAR_LIMIT, (
                f"Floor {floor} {field} is {len(val)} chars"
            )


def test_all_fields_non_empty(backend):
    """All text fields are non-empty."""
    themes = backend.generate_floor_themes()
    for floor, theme in themes.items():
        for field, val in theme.items():
            assert val, f"Floor {floor} {field} is empty"


# ── DB storage tests ──────────────────────────────────────────────────────


def test_themes_stored_in_db(conn, backend):
    """Floor themes stored correctly in DB."""
    create_epoch(conn, 1, "hold_the_line", "heist")
    stats = generate_floor_themes(conn, backend)
    assert stats["floor_themes"] == NUM_FLOORS

    rows = conn.execute("SELECT * FROM floor_themes").fetchall()
    assert len(rows) == NUM_FLOORS


def test_get_floor_themes_returns_dict(conn, backend):
    """get_floor_themes() returns dict keyed by floor number."""
    create_epoch(conn, 1, "hold_the_line", "heist")
    generate_floor_themes(conn, backend)

    themes = get_floor_themes(conn)
    assert isinstance(themes, dict)
    assert len(themes) == NUM_FLOORS
    for floor in range(1, NUM_FLOORS + 1):
        assert floor in themes
        assert "floor_name" in themes[floor]
        assert "atmosphere" in themes[floor]
        assert "narrative_beat" in themes[floor]
        assert "floor_transition" in themes[floor]


def test_get_floor_themes_empty_db(conn):
    """get_floor_themes() returns empty dict when no themes exist."""
    themes = get_floor_themes(conn)
    assert themes == {}


# ── Backward compatibility tests ──────────────────────────────────────────


def test_generate_world_without_floor_themes(conn, backend):
    """generate_world(floor_themes=None) still works."""
    create_epoch(conn, 1, "hold_the_line", "heist")
    generate_town(conn, backend)
    stats = generate_world(conn, backend)
    assert stats["rooms"] > 0
    assert stats["monsters"] > 0


def test_generate_bosses_without_floor_themes(conn, backend):
    """generate_bosses(floor_themes=None) still works."""
    create_epoch(conn, 1, "hold_the_line", "heist")
    generate_town(conn, backend)
    generate_world(conn, backend)
    stats = generate_bosses(conn, backend)
    assert stats["floor_bosses"] == NUM_FLOORS


def test_generate_bounties_without_floor_themes(conn, backend):
    """generate_bounties(floor_themes=None) still works."""
    create_epoch(conn, 1, "hold_the_line", "heist")
    generate_town(conn, backend)
    generate_world(conn, backend)
    stats = generate_bounties(conn, backend)
    assert stats["total"] > 0


def test_generate_secrets_without_floor_themes(conn, backend):
    """generate_secrets(floor_themes=None) still works."""
    create_epoch(conn, 1, "hold_the_line", "heist")
    generate_town(conn, backend)
    generate_world(conn, backend)
    breach_stats = generate_breach(conn, backend)
    stats = generate_secrets(
        conn, backend, breach_room_ids=breach_stats.get("breach_room_ids", [])
    )
    assert stats["total"] > 0


# ── Floor transition tests ────────────────────────────────────────────────


def test_floor_transition_on_floor_change(epoch_conn):
    """Floor transition shown when player crosses floors."""
    conn = epoch_conn

    # Create a player in dungeon
    conn.execute(
        """INSERT INTO accounts (mesh_id, handle) VALUES ('test123', 'Tester')"""
    )
    conn.execute(
        """INSERT INTO players (account_id, name, class, state, floor, room_id,
           hp, hp_max, pow, def, spd, resource, resource_max,
           dungeon_actions_remaining)
           VALUES (1, 'Tester', 'warrior', 'dungeon', 1, NULL, 50, 50, 3, 2, 1, 5, 5, 12)"""
    )
    conn.commit()

    # Get floor 1 hub and a floor 2 room
    hub1 = conn.execute(
        "SELECT id FROM rooms WHERE floor = 1 AND is_hub = 1"
    ).fetchone()
    hub2 = conn.execute(
        "SELECT id FROM rooms WHERE floor = 2 AND is_hub = 1"
    ).fetchone()

    if not hub1 or not hub2:
        pytest.skip("Need rooms on floors 1 and 2")

    # Place player in floor 1 hub
    conn.execute(
        "UPDATE players SET room_id = ?, floor = 1 WHERE id = 1",
        (hub1["id"],),
    )
    conn.commit()

    # Create direct exit from floor 1 hub to floor 2 hub for test
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'u')",
        (hub1["id"], hub2["id"]),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'd')",
        (hub2["id"], hub1["id"]),
    )
    # Unlock boss gate for floor 1 so player can cross to floor 2
    conn.execute(
        """INSERT INTO floor_progress (player_id, floor, boss_killed, boss_killed_at)
           VALUES (1, 1, 1, CURRENT_TIMESTAMP)"""
    )
    conn.commit()

    player = dict(conn.execute("SELECT * FROM players WHERE id = 1").fetchone())
    room, error = world_mgr.move_player(conn, player, "u")

    assert room is not None
    assert error == ""
    assert "_floor_transition" in room


def test_no_transition_same_floor(epoch_conn):
    """No transition when moving within same floor."""
    conn = epoch_conn

    conn.execute(
        "INSERT INTO accounts (mesh_id, handle) VALUES ('test123', 'Tester')"
    )
    conn.execute(
        """INSERT INTO players (account_id, name, class, state, floor, room_id,
           hp, hp_max, pow, def, spd, resource, resource_max,
           dungeon_actions_remaining)
           VALUES (1, 'Tester', 'warrior', 'dungeon', 1, NULL, 50, 50, 3, 2, 1, 5, 5, 12)"""
    )
    conn.commit()

    # Get two rooms on floor 1
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE floor = 1 LIMIT 2"
    ).fetchall()
    if len(rooms) < 2:
        pytest.skip("Need at least 2 rooms on floor 1")

    r1, r2 = rooms[0]["id"], rooms[1]["id"]

    # Ensure exit exists
    conn.execute(
        "INSERT OR IGNORE INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r1, r2),
    )
    conn.execute(
        "UPDATE players SET room_id = ?, floor = 1 WHERE id = 1", (r1,)
    )
    conn.commit()

    player = dict(conn.execute("SELECT * FROM players WHERE id = 1").fetchone())
    room, error = world_mgr.move_player(conn, player, "n")

    if room:
        assert "_floor_transition" not in room


def test_no_transition_to_town(epoch_conn):
    """No transition when moving to town (floor 0)."""
    conn = epoch_conn

    conn.execute(
        "INSERT INTO accounts (mesh_id, handle) VALUES ('test123', 'Tester')"
    )
    conn.execute(
        """INSERT INTO players (account_id, name, class, state, floor, room_id,
           hp, hp_max, pow, def, spd, resource, resource_max,
           dungeon_actions_remaining)
           VALUES (1, 'Tester', 'warrior', 'dungeon', 1, NULL, 50, 50, 3, 2, 1, 5, 5, 12)"""
    )
    conn.commit()

    # Get floor 0 room and floor 1 room
    town_room = conn.execute("SELECT id FROM rooms WHERE floor = 0 LIMIT 1").fetchone()
    f1_room = conn.execute("SELECT id FROM rooms WHERE floor = 1 LIMIT 1").fetchone()

    if not town_room or not f1_room:
        pytest.skip("Need rooms on floor 0 and 1")

    # Create exit from floor 1 to town
    conn.execute(
        "INSERT OR IGNORE INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'u')",
        (f1_room["id"], town_room["id"]),
    )
    conn.execute(
        "UPDATE players SET room_id = ?, floor = 1 WHERE id = 1", (f1_room["id"],)
    )
    conn.commit()

    player = dict(conn.execute("SELECT * FROM players WHERE id = 1").fetchone())
    room, error = world_mgr.move_player(conn, player, "u")

    if room:
        # Floor 0 should not get transition
        assert "_floor_transition" not in room


def test_enter_dungeon_shows_transition(epoch_conn):
    """Enter dungeon shows Floor 1 transition."""
    conn = epoch_conn

    conn.execute(
        "INSERT INTO accounts (mesh_id, handle) VALUES ('test123', 'Tester')"
    )
    # Place player in town at the hub (bar)
    hub0 = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1"
    ).fetchone()
    if not hub0:
        pytest.skip("No town hub")

    conn.execute(
        """INSERT INTO players (account_id, name, class, state, floor, room_id,
           hp, hp_max, pow, def, spd, resource, resource_max,
           dungeon_actions_remaining)
           VALUES (1, 'Tester', 'warrior', 'town', 0, ?, 50, 50, 3, 2, 1, 5, 5, 12)""",
        (hub0["id"],),
    )
    conn.commit()

    player = dict(conn.execute("SELECT * FROM players WHERE id = 1").fetchone())
    room = world_mgr.enter_dungeon(conn, player)

    assert room is not None
    # The transition is applied in actions.py, not world.py for enter_dungeon
    # So we check _get_floor_transition directly
    transition = world_mgr._get_floor_transition(conn, 1)
    assert transition is not None
    assert len(transition) > 0


# ── Validation tests ──────────────────────────────────────────────────────


def test_validation_passes_with_floor_themes(epoch_conn):
    """Validation passes when floor themes exist."""
    conn = epoch_conn
    generate_bosses(conn, DummyBackend())
    result = validate_epoch(conn)
    # Should not have floor theme errors
    theme_errors = [e for e in result["errors"] if "floor_theme" in e.lower() or "Floor" in e and "theme" in e]
    assert theme_errors == [], f"Unexpected floor theme errors: {theme_errors}"


def test_validation_warns_missing_floor_themes(conn, backend):
    """Validation warns when no floor themes exist."""
    create_epoch(conn, 1, "hold_the_line", "heist")
    generate_town(conn, backend)
    generate_world(conn, backend)
    generate_bosses(conn, backend)
    result = validate_epoch(conn)
    theme_warnings = [w for w in result["warnings"] if "floor themes" in w.lower()]
    assert len(theme_warnings) == 1
