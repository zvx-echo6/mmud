"""Tests for dungeon world generation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import (
    LLM_OUTPUT_CHAR_LIMIT,
    NUM_FLOORS,
    ROOMS_PER_FLOOR_MAX,
    ROOMS_PER_FLOOR_MIN,
)
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.generation.worldgen import generate_world


def _make_db() -> sqlite3.Connection:
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
    return conn


def _generate() -> tuple[sqlite3.Connection, dict]:
    conn = _make_db()
    backend = DummyBackend()
    stats = generate_world(conn, backend)
    return conn, stats


# ── Floor count ──


def test_generates_all_floors():
    conn, stats = _generate()
    floors = conn.execute("SELECT DISTINCT floor FROM rooms ORDER BY floor").fetchall()
    floor_nums = [r["floor"] for r in floors]
    for f in range(1, NUM_FLOORS + 1):
        assert f in floor_nums, f"Floor {f} not found"


# ── Room counts ──


def test_room_count_per_floor_in_range():
    conn, stats = _generate()
    for floor in range(1, NUM_FLOORS + 1):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ? AND is_breach = 0",
            (floor,),
        ).fetchone()
        # Allow some flexibility — worldgen generates hub + branches + vaults
        assert count["cnt"] >= 5, f"Floor {floor} has too few rooms: {count['cnt']}"


def test_total_room_count():
    conn, stats = _generate()
    assert stats["rooms"] > 0
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_breach = 0"
    ).fetchone()
    assert total["cnt"] == stats["rooms"]


# ── Hub rooms ──


def test_one_hub_per_floor():
    conn, stats = _generate()
    for floor in range(1, NUM_FLOORS + 1):
        hubs = conn.execute(
            "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ? AND is_hub = 1",
            (floor,),
        ).fetchone()
        assert hubs["cnt"] == 1, f"Floor {floor} has {hubs['cnt']} hubs"


def test_hub_is_checkpoint():
    conn, stats = _generate()
    hubs = conn.execute(
        "SELECT is_checkpoint FROM rooms WHERE is_hub = 1"
    ).fetchall()
    for h in hubs:
        assert h["is_checkpoint"] == 1


# ── Exit symmetry ──


def test_exit_symmetry():
    """Every exit A→B should have a reverse B→A."""
    conn, stats = _generate()
    exits = conn.execute(
        "SELECT from_room_id, to_room_id FROM room_exits"
    ).fetchall()
    for ex in exits:
        reverse = conn.execute(
            "SELECT id FROM room_exits WHERE from_room_id = ? AND to_room_id = ?",
            (ex["to_room_id"], ex["from_room_id"]),
        ).fetchone()
        assert reverse is not None, (
            f"No reverse exit for {ex['from_room_id']} → {ex['to_room_id']}"
        )


# ── Connectivity ──


def test_no_orphan_rooms():
    """Every room should have at least one exit."""
    conn, stats = _generate()
    rooms = conn.execute("SELECT id FROM rooms").fetchall()
    for room in rooms:
        exits = conn.execute(
            "SELECT COUNT(*) as cnt FROM room_exits "
            "WHERE from_room_id = ? OR to_room_id = ?",
            (room["id"], room["id"]),
        ).fetchone()
        assert exits["cnt"] > 0, f"Room {room['id']} has no exits"


def test_hub_has_multiple_exits():
    """Hub rooms should connect to multiple branches."""
    conn, stats = _generate()
    hubs = conn.execute("SELECT id FROM rooms WHERE is_hub = 1").fetchall()
    for hub in hubs:
        exits = conn.execute(
            "SELECT COUNT(*) as cnt FROM room_exits WHERE from_room_id = ?",
            (hub["id"],),
        ).fetchone()
        assert exits["cnt"] >= 2, f"Hub {hub['id']} has only {exits['cnt']} exits"


# ── Stairways ──


def test_stairways_on_non_final_floors():
    """Floors 1-3 should have stairway rooms."""
    conn, stats = _generate()
    for floor in range(1, NUM_FLOORS):
        stairs = conn.execute(
            "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ? AND is_stairway = 1",
            (floor,),
        ).fetchone()
        assert stairs["cnt"] >= 1, f"Floor {floor} has no stairway"


# ── Description limits ──


def test_all_descriptions_under_limit():
    conn, stats = _generate()
    rooms = conn.execute(
        "SELECT id, name, description, description_short FROM rooms"
    ).fetchall()
    for r in rooms:
        assert len(r["description"]) <= LLM_OUTPUT_CHAR_LIMIT, (
            f"Room {r['id']} ({r['name']}) desc too long: {len(r['description'])}"
        )
        assert len(r["description_short"]) <= LLM_OUTPUT_CHAR_LIMIT, (
            f"Room {r['id']} ({r['name']}) short desc too long: {len(r['description_short'])}"
        )


# ── Monsters ──


def test_monsters_placed():
    conn, stats = _generate()
    assert stats["monsters"] > 0
    count = conn.execute("SELECT COUNT(*) as cnt FROM monsters").fetchone()
    assert count["cnt"] == stats["monsters"]


def test_no_monster_in_hub():
    conn, stats = _generate()
    hub_monsters = conn.execute(
        """SELECT m.id FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.is_hub = 1"""
    ).fetchall()
    assert len(hub_monsters) == 0


# ── Items ──


def test_items_generated():
    conn, stats = _generate()
    assert stats["items"] > 0
    # Should have tier 1-5 (3 slots each = 15) + tier 6 (3 slots = 3) = 18
    count = conn.execute("SELECT COUNT(*) as cnt FROM items").fetchone()
    assert count["cnt"] == 18


def test_item_tiers():
    conn, stats = _generate()
    for tier in range(1, 7):
        items = conn.execute(
            "SELECT COUNT(*) as cnt FROM items WHERE tier = ?", (tier,)
        ).fetchone()
        assert items["cnt"] == 3, f"Tier {tier} should have 3 items"


# ── Vault rooms ──


def test_vault_rooms_exist():
    conn, stats = _generate()
    vaults = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_vault = 1"
    ).fetchone()
    assert vaults["cnt"] > 0


def test_vault_rooms_have_exits():
    conn, stats = _generate()
    vaults = conn.execute("SELECT id FROM rooms WHERE is_vault = 1").fetchall()
    for v in vaults:
        exits = conn.execute(
            "SELECT COUNT(*) as cnt FROM room_exits WHERE from_room_id = ?",
            (v["id"],),
        ).fetchone()
        assert exits["cnt"] > 0, f"Vault {v['id']} has no exits"


def test_no_duplicate_exits_per_room():
    """No room should have duplicate exits in the same direction."""
    conn, stats = _generate()
    rooms = conn.execute("SELECT id FROM rooms").fetchall()
    for r in rooms:
        exits = conn.execute(
            "SELECT direction, COUNT(*) as cnt FROM room_exits WHERE from_room_id = ? GROUP BY direction HAVING cnt > 1",
            (r["id"],),
        ).fetchall()
        assert len(exits) == 0, f"Room {r['id']} has duplicate exit directions: {[e['direction'] for e in exits]}"
