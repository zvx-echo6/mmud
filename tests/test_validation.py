"""Tests for post-generation validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import LLM_OUTPUT_CHAR_LIMIT
from src.db.database import init_schema
from src.generation.bossgen import generate_bosses
from src.generation.bountygen import generate_bounties
from src.generation.breachgen import generate_breach
from src.generation.narrative import DummyBackend
from src.generation.secretgen import generate_secrets
from src.generation.validation import validate_epoch
from src.generation.worldgen import generate_world


def _make_full_epoch() -> sqlite3.Connection:
    """Generate a complete epoch for validation testing."""
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
    breach_stats = generate_breach(conn, backend)
    generate_secrets(conn, backend, breach_room_ids=breach_stats["breach_room_ids"])
    generate_bounties(conn, backend)
    generate_bosses(conn, backend)

    return conn


# ── Clean epoch ──


def test_full_epoch_validates_clean():
    conn = _make_full_epoch()
    result = validate_epoch(conn)
    assert len(result["errors"]) == 0, f"Validation errors: {result['errors']}"


# ── 150-char enforcement ──


def test_catches_long_description():
    conn = _make_full_epoch()
    # Inject an overly long description
    rid = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    conn.execute(
        "UPDATE rooms SET description = ? WHERE id = ?",
        ("x" * (LLM_OUTPUT_CHAR_LIMIT + 10), rid),
    )
    conn.commit()

    result = validate_epoch(conn)
    found = any("exceeds" in e and str(rid) in e for e in result["errors"])
    assert found, "Should catch oversized room description"


def test_catches_long_hint():
    conn = _make_full_epoch()
    sid = conn.execute("SELECT id FROM secrets LIMIT 1").fetchone()["id"]
    conn.execute(
        "UPDATE secrets SET hint_tier1 = ? WHERE id = ?",
        ("x" * (LLM_OUTPUT_CHAR_LIMIT + 10), sid),
    )
    conn.commit()

    result = validate_epoch(conn)
    found = any("exceeds" in e and str(sid) in e for e in result["errors"])
    assert found, "Should catch oversized hint"


# ── Forbidden verb detection ──


def test_catches_forbidden_verb_in_hint():
    conn = _make_full_epoch()
    sid = conn.execute("SELECT id FROM secrets LIMIT 1").fetchone()["id"]
    conn.execute(
        "UPDATE secrets SET hint_tier1 = ? WHERE id = ?",
        ("examine the wall carefully", sid),
    )
    conn.commit()

    result = validate_epoch(conn)
    found = any("forbidden verb" in e for e in result["errors"])
    assert found, "Should catch forbidden verb 'examine' in hint"


def test_catches_push_verb():
    conn = _make_full_epoch()
    sid = conn.execute("SELECT id FROM secrets LIMIT 1").fetchone()["id"]
    conn.execute(
        "UPDATE secrets SET hint_tier2 = ? WHERE id = ?",
        ("push the block to reveal the path", sid),
    )
    conn.commit()

    result = validate_epoch(conn)
    found = any("forbidden verb" in e and "'push'" in e for e in result["errors"])
    assert found, "Should catch forbidden verb 'push' in hint"


# ── Missing hints ──


def test_catches_missing_hint():
    conn = _make_full_epoch()
    sid = conn.execute("SELECT id FROM secrets LIMIT 1").fetchone()["id"]
    conn.execute(
        "UPDATE secrets SET hint_tier3 = NULL WHERE id = ?", (sid,)
    )
    conn.commit()

    result = validate_epoch(conn)
    found = any("missing" in e and "hint_tier3" in e for e in result["errors"])
    assert found, "Should catch missing hint tier"


# ── Floor bosses ──


def test_catches_missing_floor_boss():
    conn = _make_full_epoch()
    # Remove all floor bosses
    conn.execute("DELETE FROM monsters WHERE is_floor_boss = 1")
    conn.commit()

    result = validate_epoch(conn)
    found = any("No floor boss" in e for e in result["errors"])
    assert found, "Should catch missing floor bosses"


# ── Orphan rooms ──


def test_catches_orphan_room():
    conn = _make_full_epoch()
    # Insert a room with no exits
    conn.execute(
        """INSERT INTO rooms (floor, name, description, description_short, is_hub)
           VALUES (1, 'Orphan', 'Abandoned.', 'Abandoned.', 0)"""
    )
    conn.commit()

    result = validate_epoch(conn)
    found = any("orphan" in e for e in result["errors"])
    assert found, "Should catch orphan room"


# ── Template variables ──


def test_warns_unresolved_template():
    conn = _make_full_epoch()
    rid = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    conn.execute(
        "UPDATE rooms SET description = ? WHERE id = ?",
        ("The {monster} lurks here.", rid),
    )
    conn.commit()

    result = validate_epoch(conn)
    found = any("unresolved template" in w for w in result["warnings"])
    assert found, "Should warn about unresolved template variables"


# ── Puzzle consistency ──


def test_catches_inconsistent_puzzle_symbols():
    conn = _make_full_epoch()
    # Find a puzzle group and break its symbol consistency
    group = conn.execute(
        "SELECT puzzle_group FROM secrets WHERE puzzle_group IS NOT NULL LIMIT 1"
    ).fetchone()
    if group:
        conn.execute(
            "UPDATE secrets SET puzzle_symbol = 'broken' "
            "WHERE puzzle_group = ? AND rowid = ("
            "  SELECT MIN(rowid) FROM secrets WHERE puzzle_group = ?)",
            (group["puzzle_group"], group["puzzle_group"]),
        )
        conn.commit()

        result = validate_epoch(conn)
        found = any("inconsistent symbols" in e for e in result["errors"])
        assert found, "Should catch inconsistent puzzle symbols"


# ── No rooms ──


def test_catches_empty_world():
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

    result = validate_epoch(conn)
    found = any("No rooms" in e for e in result["errors"])
    assert found, "Should catch empty world"
