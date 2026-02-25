"""Tests for boss generation."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import (
    FLOOR_BOSS_MECHANICS,
    NUM_FLOORS,
    RAID_BOSS_MECHANIC_ROLLS,
    RAID_BOSS_MECHANIC_TABLE,
    WARDEN_HP_MAX,
    WARDEN_HP_MIN,
)
from src.db.database import init_schema
from src.generation.bossgen import generate_bosses
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
    stats = generate_bosses(conn, backend)
    return conn, stats


# ── Floor boss count ──


def test_one_boss_per_floor():
    conn, stats = _generate()
    assert stats["floor_bosses"] == NUM_FLOORS


def test_floor_bosses_on_each_floor():
    conn, stats = _generate()
    for floor in range(1, NUM_FLOORS + 1):
        boss = conn.execute(
            """SELECT m.id FROM monsters m
               JOIN rooms r ON m.room_id = r.id
               WHERE m.is_floor_boss = 1 AND r.floor = ?""",
            (floor,),
        ).fetchone()
        assert boss is not None, f"No floor boss on floor {floor}"


# ── Mechanics ──


def test_floor_bosses_have_mechanics():
    conn, stats = _generate()
    bosses = conn.execute(
        "SELECT id, name, mechanic FROM monsters WHERE is_floor_boss = 1"
    ).fetchall()
    for boss in bosses:
        assert boss["mechanic"], f"Boss {boss['id']} ({boss['name']}) has no mechanic"


def test_floor_1_3_boss_has_single_mechanic():
    conn, stats = _generate()
    for floor in range(1, NUM_FLOORS):
        boss = conn.execute(
            """SELECT m.mechanic FROM monsters m
               JOIN rooms r ON m.room_id = r.id
               WHERE m.is_floor_boss = 1 AND r.floor = ?""",
            (floor,),
        ).fetchone()
        mechanic = boss["mechanic"]
        # Should be a plain string (not JSON array) for floors 1-3
        # Check it's a valid mechanic from the floor's table
        valid_mechanics = FLOOR_BOSS_MECHANICS[floor]
        assert mechanic in valid_mechanics, (
            f"Floor {floor} boss mechanic '{mechanic}' not in table"
        )


def test_floor_4_warden_has_two_mechanics():
    conn, stats = _generate()
    boss = conn.execute(
        """SELECT m.mechanic FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE m.is_floor_boss = 1 AND r.floor = ?""",
        (NUM_FLOORS,),
    ).fetchone()
    mechanic = boss["mechanic"]
    # Floor 4 stores mechanics as JSON array
    mechanics = json.loads(mechanic)
    assert len(mechanics) == 2, f"Warden should have 2 mechanics, got {len(mechanics)}"

    # All mechanics should be from floors 1-3 tables combined
    all_valid = []
    for f in range(1, NUM_FLOORS):
        all_valid.extend(FLOOR_BOSS_MECHANICS[f])
    for m in mechanics:
        assert m in all_valid, f"Warden mechanic '{m}' not in any floor table"


# ── Warden HP ──


def test_warden_hp_range():
    conn, stats = _generate()
    warden = conn.execute(
        """SELECT m.hp_max FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE m.is_floor_boss = 1 AND r.floor = ?""",
        (NUM_FLOORS,),
    ).fetchone()
    assert WARDEN_HP_MIN <= warden["hp_max"] <= WARDEN_HP_MAX, (
        f"Warden HP {warden['hp_max']} out of range {WARDEN_HP_MIN}-{WARDEN_HP_MAX}"
    )


# ── Boss stats scale with floor ──


def test_boss_stats_scale():
    conn, stats = _generate()
    bosses = conn.execute(
        """SELECT m.hp_max, r.floor FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE m.is_floor_boss = 1
           ORDER BY r.floor"""
    ).fetchall()
    # HP should generally increase with floor
    # (with some randomness, check floor 1 < floor 4)
    if len(bosses) >= 2:
        assert bosses[0]["hp_max"] < bosses[-1]["hp_max"]


# ── Raid boss ──


def test_raid_boss_pre_generated():
    conn, stats = _generate()
    raid = conn.execute("SELECT * FROM raid_boss WHERE id = 1").fetchone()
    assert raid is not None, "No raid boss found"


def test_raid_boss_hp_zero():
    """Raid boss HP should be 0 (scaled at epoch start to active player count)."""
    conn, stats = _generate()
    raid = conn.execute("SELECT hp, hp_max FROM raid_boss WHERE id = 1").fetchone()
    assert raid["hp"] == 0
    assert raid["hp_max"] == 0


def test_raid_boss_mechanics_count():
    conn, stats = _generate()
    raid = conn.execute("SELECT mechanics FROM raid_boss WHERE id = 1").fetchone()
    mechanics = json.loads(raid["mechanics"])
    min_rolls, max_rolls = RAID_BOSS_MECHANIC_ROLLS
    assert min_rolls <= len(mechanics) <= max_rolls, (
        f"Raid boss has {len(mechanics)} mechanics, expected {min_rolls}-{max_rolls}"
    )


def test_raid_boss_mechanics_valid():
    conn, stats = _generate()
    raid = conn.execute("SELECT mechanics FROM raid_boss WHERE id = 1").fetchone()
    mechanics = json.loads(raid["mechanics"])

    all_valid = []
    for cat_mechs in RAID_BOSS_MECHANIC_TABLE.values():
        all_valid.extend(cat_mechs)

    for m in mechanics:
        assert m in all_valid, f"Raid boss mechanic '{m}' not in table"


def test_raid_boss_mechanics_unique():
    conn, stats = _generate()
    raid = conn.execute("SELECT mechanics FROM raid_boss WHERE id = 1").fetchone()
    mechanics = json.loads(raid["mechanics"])
    assert len(mechanics) == len(set(mechanics)), "Raid boss has duplicate mechanics"


def test_raid_boss_on_deepest_floor():
    conn, stats = _generate()
    raid = conn.execute("SELECT floor FROM raid_boss WHERE id = 1").fetchone()
    assert raid["floor"] == NUM_FLOORS
