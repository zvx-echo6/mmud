"""Tests for breach activation — day 15 trigger, foreshadowing, accessibility."""

import sqlite3

import pytest

from config import BREACH_DAY, MSG_CHAR_LIMIT, NUM_FLOORS
from src.db.database import get_db, init_schema
from src.models.epoch import get_epoch
from src.models.player import create_player, get_or_create_account
from src.systems.breach import can_enter_breach, get_breach_rooms, get_breach_state, is_breach_open
from src.systems.daytick import run_day_tick
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with a full epoch."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="hold_the_line", breach_type="heist")
    return db


@pytest.fixture
def player(conn):
    """Create a test player."""
    acc = get_or_create_account(conn, "mesh_test", "TestPlayer")
    p = create_player(conn, acc, "TestPlayer", "warrior")
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2 WHERE id = ?",
        (p["id"],),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


# ── Foreshadowing ──


def test_foreshadow_day_12(conn):
    """Foreshadowing broadcast on day 12."""
    # Advance to day 12
    conn.execute("UPDATE epoch SET day_number = ? WHERE id = 1", (BREACH_DAY - 4,))
    conn.commit()
    run_day_tick(conn)

    broadcasts = conn.execute(
        "SELECT message FROM broadcasts WHERE message LIKE '%trembles%' OR message LIKE '%Cracks%'"
    ).fetchall()
    assert len(broadcasts) >= 1


def test_foreshadow_day_13(conn):
    """Foreshadowing broadcast on day 13."""
    conn.execute("UPDATE epoch SET day_number = ? WHERE id = 1", (BREACH_DAY - 3,))
    conn.commit()
    run_day_tick(conn)

    broadcasts = conn.execute(
        "SELECT message FROM broadcasts WHERE message LIKE '%Cracks%' OR message LIKE '%trembles%'"
    ).fetchall()
    assert len(broadcasts) >= 1


# ── Day 15 Trigger ──


def test_breach_opens_day_15(conn):
    """Breach opens on day 15."""
    assert not is_breach_open(conn)

    conn.execute("UPDATE epoch SET day_number = ? WHERE id = 1", (BREACH_DAY - 1,))
    conn.commit()
    stats = run_day_tick(conn)

    assert stats["breach_opened"] is True
    assert is_breach_open(conn)


def test_breach_state_active_after_open(conn):
    """Breach state is active after opening."""
    conn.execute("UPDATE epoch SET day_number = ? WHERE id = 1", (BREACH_DAY - 1,))
    conn.commit()
    run_day_tick(conn)

    state = get_breach_state(conn)
    assert state is not None
    assert state["active"] == 1


def test_breach_open_broadcast(conn):
    """Opening creates a broadcast."""
    conn.execute("UPDATE epoch SET day_number = ? WHERE id = 1", (BREACH_DAY - 1,))
    conn.commit()
    run_day_tick(conn)

    broadcasts = conn.execute(
        "SELECT message FROM broadcasts WHERE message LIKE '%BREACH HAS OPENED%'"
    ).fetchall()
    assert len(broadcasts) >= 1


# ── Accessibility ──


def test_cannot_enter_before_day_15(conn, player):
    """Players cannot enter breach before day 15."""
    can, msg = can_enter_breach(conn, player["id"])
    assert not can
    assert "sealed" in msg.lower() or "days" in msg.lower()


def test_can_enter_after_day_15(conn, player):
    """Players can enter breach after day 15."""
    conn.execute("UPDATE epoch SET day_number = ? WHERE id = 1", (BREACH_DAY - 1,))
    conn.commit()
    run_day_tick(conn)

    can, msg = can_enter_breach(conn, player["id"])
    assert can


def test_cannot_enter_completed_breach(conn, player):
    """Players cannot enter completed breach."""
    conn.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    conn.execute("UPDATE breach SET active = 1, completed = 1 WHERE id = 1")
    conn.commit()

    can, msg = can_enter_breach(conn, player["id"])
    assert not can
    assert "complete" in msg.lower()


# ── Breach Rooms ──


def test_breach_rooms_exist(conn):
    """Breach rooms are generated."""
    rooms = get_breach_rooms(conn)
    assert len(rooms) >= 5


def test_breach_rooms_are_breach_flagged(conn):
    """All breach rooms have is_breach = 1."""
    rooms = conn.execute(
        "SELECT is_breach FROM rooms WHERE is_breach = 1"
    ).fetchall()
    assert len(rooms) >= 5
    for r in rooms:
        assert r["is_breach"] == 1


def test_breach_rooms_connected(conn):
    """Breach rooms have exits (linear chain)."""
    rooms = get_breach_rooms(conn)
    for room in rooms:
        exits = conn.execute(
            "SELECT COUNT(*) as cnt FROM room_exits WHERE from_room_id = ?",
            (room["id"],),
        ).fetchone()
        assert exits["cnt"] >= 1  # At least one connection


# ── Broadcasts Under 150 ──


def test_all_activation_broadcasts_under_150(conn):
    """All activation-related broadcasts fit in 150 chars."""
    conn.execute("UPDATE epoch SET day_number = ? WHERE id = 1", (BREACH_DAY - 4,))
    conn.commit()
    # Day 12 foreshadow
    run_day_tick(conn)
    # Day 13 foreshadow
    run_day_tick(conn)
    # Days 14
    run_day_tick(conn)
    # Day 15 — breach opens
    run_day_tick(conn)

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT, f"Too long: {b['message']}"
