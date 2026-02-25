"""Tests for bounty pool generation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import (
    BOUNTIES_PER_EPOCH,
    BOUNTY_ACTIVE_MAX,
    BOUNTY_PHASE_DISTRIBUTION,
    LLM_OUTPUT_CHAR_LIMIT,
)
from src.db.database import init_schema
from src.generation.bountygen import generate_bounties
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
    stats = generate_bounties(conn, backend)
    return conn, stats


# ── Total count ──


def test_total_bounties():
    conn, stats = _generate()
    assert stats["total"] == BOUNTIES_PER_EPOCH
    count = conn.execute("SELECT COUNT(*) as cnt FROM bounties").fetchone()
    assert count["cnt"] == BOUNTIES_PER_EPOCH


# ── Phase distribution ──


def test_early_phase_count():
    conn, stats = _generate()
    assert stats["early"] == BOUNTY_PHASE_DISTRIBUTION["early"]["count"]


def test_mid_phase_count():
    conn, stats = _generate()
    assert stats["mid"] == BOUNTY_PHASE_DISTRIBUTION["mid"]["count"]


def test_late_phase_count():
    conn, stats = _generate()
    assert stats["late"] == BOUNTY_PHASE_DISTRIBUTION["late"]["count"]


# ── Active bounties ──


def test_initial_active_count():
    conn, stats = _generate()
    active = conn.execute(
        "SELECT COUNT(*) as cnt FROM bounties WHERE active = 1"
    ).fetchone()
    assert active["cnt"] == BOUNTY_ACTIVE_MAX


# ── Description limits ──


def test_descriptions_under_limit():
    conn, stats = _generate()
    bounties = conn.execute("SELECT id, description FROM bounties").fetchall()
    for b in bounties:
        assert len(b["description"]) <= LLM_OUTPUT_CHAR_LIMIT, (
            f"Bounty {b['id']} desc too long: {len(b['description'])}"
        )


# ── Bounty monsters ──


def test_bounty_monsters_exist():
    conn, stats = _generate()
    bounty_monsters = conn.execute(
        "SELECT COUNT(*) as cnt FROM monsters WHERE is_bounty = 1"
    ).fetchone()
    assert bounty_monsters["cnt"] == BOUNTIES_PER_EPOCH


def test_bounty_monsters_in_valid_rooms():
    conn, stats = _generate()
    bounty_monsters = conn.execute(
        """SELECT m.id, m.room_id, r.floor, r.is_hub, r.is_breach
           FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE m.is_bounty = 1"""
    ).fetchall()
    for m in bounty_monsters:
        assert m["is_hub"] == 0, f"Bounty monster {m['id']} in hub room"
        assert m["is_breach"] == 0, f"Bounty monster {m['id']} in breach room"


# ── Floor ranges ──


def test_early_bounties_on_correct_floors():
    conn, stats = _generate()
    early = conn.execute(
        "SELECT floor_min, floor_max FROM bounties WHERE phase = 'early'"
    ).fetchall()
    for b in early:
        assert b["floor_min"] >= 1
        assert b["floor_max"] <= 2


def test_late_bounties_on_correct_floors():
    conn, stats = _generate()
    late = conn.execute(
        "SELECT floor_min, floor_max FROM bounties WHERE phase = 'late'"
    ).fetchall()
    for b in late:
        assert b["floor_min"] >= 3
        assert b["floor_max"] <= 4


# ── Available day ordering ──


def test_early_bounties_available_days_1_10():
    conn, stats = _generate()
    early = conn.execute(
        "SELECT available_from_day FROM bounties WHERE phase = 'early'"
    ).fetchall()
    for b in early:
        assert 1 <= b["available_from_day"] <= 10


def test_mid_bounties_available_days_11_20():
    conn, stats = _generate()
    mid = conn.execute(
        "SELECT available_from_day FROM bounties WHERE phase = 'mid'"
    ).fetchall()
    for b in mid:
        assert 11 <= b["available_from_day"] <= 20


def test_late_bounties_available_days_21_30():
    conn, stats = _generate()
    late = conn.execute(
        "SELECT available_from_day FROM bounties WHERE phase = 'late'"
    ).fetchall()
    for b in late:
        assert 21 <= b["available_from_day"] <= 30


# ── HP ranges ──


def test_early_bounty_hp_range():
    conn, stats = _generate()
    monsters = conn.execute(
        """SELECT m.hp_max FROM monsters m
           JOIN bounties b ON b.target_monster_id = m.id
           WHERE b.phase = 'early'"""
    ).fetchall()
    for m in monsters:
        assert 100 <= m["hp_max"] <= 250, f"Early bounty HP {m['hp_max']} out of range"


def test_late_bounty_hp_range():
    conn, stats = _generate()
    monsters = conn.execute(
        """SELECT m.hp_max FROM monsters m
           JOIN bounties b ON b.target_monster_id = m.id
           WHERE b.phase = 'late'"""
    ).fetchall()
    for m in monsters:
        assert 400 <= m["hp_max"] <= 800, f"Late bounty HP {m['hp_max']} out of range"
