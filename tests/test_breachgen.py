"""Tests for breach zone generation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import (
    BREACH_CONNECTS_FLOORS_RANGE,
    BREACH_MINI_EVENTS,
    BREACH_ROOMS_MAX,
    BREACH_ROOMS_MIN,
    BREACH_SECRETS,
)
from src.db.database import init_schema
from src.generation.breachgen import generate_breach
from src.generation.narrative import DummyBackend
from src.generation.worldgen import generate_world


def _make_db_with_world() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1)"""
    )
    conn.commit()
    backend = DummyBackend()
    generate_world(conn, backend)
    return conn


def _generate() -> tuple[sqlite3.Connection, dict]:
    conn = _make_db_with_world()
    backend = DummyBackend()
    stats = generate_breach(conn, backend)
    return conn, stats


# ── Room count ──


def test_breach_room_count_in_range():
    conn, stats = _generate()
    assert BREACH_ROOMS_MIN <= stats["rooms"] <= BREACH_ROOMS_MAX


def test_breach_rooms_marked():
    conn, stats = _generate()
    breach_rooms = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_breach = 1"
    ).fetchone()
    assert breach_rooms["cnt"] == stats["rooms"]


# ── Floor placement ──


def test_breach_rooms_on_correct_floors():
    conn, stats = _generate()
    breach_rooms = conn.execute(
        "SELECT floor FROM rooms WHERE is_breach = 1"
    ).fetchall()
    # Breach floors are randomized within BREACH_CONNECTS_FLOORS_RANGE
    valid_floors = set()
    for f in range(BREACH_CONNECTS_FLOORS_RANGE[0], BREACH_CONNECTS_FLOORS_RANGE[1]):
        valid_floors.add(f)
        valid_floors.add(f + 1)
    for r in breach_rooms:
        assert r["floor"] in valid_floors, (
            f"Breach room on floor {r['floor']}, expected one of {sorted(valid_floors)}"
        )


# ── Connectivity ──


def test_breach_rooms_connected():
    """All breach rooms should have exits."""
    conn, stats = _generate()
    breach_rooms = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1"
    ).fetchall()
    for room in breach_rooms:
        exits = conn.execute(
            "SELECT COUNT(*) as cnt FROM room_exits "
            "WHERE from_room_id = ? OR to_room_id = ?",
            (room["id"], room["id"]),
        ).fetchone()
        assert exits["cnt"] > 0, f"Breach room {room['id']} has no exits"


def test_breach_connects_to_main_dungeon():
    """Breach should connect to main dungeon rooms on adjacent floors."""
    conn, stats = _generate()
    breach_ids = set(stats["breach_room_ids"])
    if not breach_ids:
        return

    # First breach room should connect to a non-breach room on the entry floor
    first_breach = stats["breach_room_ids"][0]
    incoming = conn.execute(
        """SELECT re.from_room_id FROM room_exits re
           JOIN rooms r ON re.from_room_id = r.id
           WHERE re.to_room_id = ? AND r.is_breach = 0""",
        (first_breach,),
    ).fetchall()
    assert len(incoming) > 0, "First breach room has no connection from main dungeon"

    # Last breach room should connect to a non-breach room on the exit floor
    last_breach = stats["breach_room_ids"][-1]
    outgoing = conn.execute(
        """SELECT re.to_room_id FROM room_exits re
           JOIN rooms r ON re.to_room_id = r.id
           WHERE re.from_room_id = ? AND r.is_breach = 0""",
        (last_breach,),
    ).fetchall()
    assert len(outgoing) > 0, "Last breach room has no connection to main dungeon"


# ── Mini-event ──


def test_mini_event_valid():
    conn, stats = _generate()
    assert stats["mini_event"] in BREACH_MINI_EVENTS


def test_breach_state_created():
    conn, stats = _generate()
    breach = conn.execute("SELECT * FROM breach WHERE id = 1").fetchone()
    assert breach is not None
    assert breach["mini_event"] in BREACH_MINI_EVENTS
    assert breach["active"] == 0  # Not active until day 15


# ── Mini-boss ──


def test_mini_boss_placed():
    conn, stats = _generate()
    assert stats["mini_boss_id"] is not None
    boss = conn.execute(
        "SELECT * FROM monsters WHERE is_breach_boss = 1"
    ).fetchone()
    assert boss is not None


def test_mini_boss_in_breach_zone():
    conn, stats = _generate()
    boss = conn.execute(
        """SELECT m.room_id, r.is_breach FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE m.is_breach_boss = 1"""
    ).fetchone()
    assert boss["is_breach"] == 1


def test_mini_boss_in_deepest_breach_room():
    conn, stats = _generate()
    boss = conn.execute(
        "SELECT room_id FROM monsters WHERE is_breach_boss = 1"
    ).fetchone()
    assert boss["room_id"] == stats["breach_room_ids"][-1]


# ── Breach room IDs returned ──


def test_breach_room_ids_returned():
    conn, stats = _generate()
    assert len(stats["breach_room_ids"]) == stats["rooms"]
    for rid in stats["breach_room_ids"]:
        room = conn.execute(
            "SELECT is_breach FROM rooms WHERE id = ?", (rid,)
        ).fetchone()
        assert room["is_breach"] == 1


# ── Emergence event ──


def test_emergence_has_hp():
    """Emergence event should set emergence HP in breach table."""
    conn = _make_db_with_world()
    backend = DummyBackend()
    # Force emergence event
    import src.generation.breachgen as bg
    import config
    original = config.BREACH_MINI_EVENTS
    config.BREACH_MINI_EVENTS = ["emergence"]
    try:
        stats = bg.generate_breach(conn, backend)
    finally:
        config.BREACH_MINI_EVENTS = original

    if stats["mini_event"] == "emergence":
        breach = conn.execute("SELECT * FROM breach WHERE id = 1").fetchone()
        assert breach["emergence_hp"] is not None
        assert breach["emergence_hp"] > 0
