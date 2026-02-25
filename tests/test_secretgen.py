"""Tests for secret placement generation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import HINT_FORBIDDEN_VERBS, LLM_OUTPUT_CHAR_LIMIT, SECRETS_PER_EPOCH
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.generation.secretgen import generate_secrets
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


def _generate_with_breach() -> tuple[sqlite3.Connection, dict]:
    conn = _make_db_with_world()
    # Create some breach rooms for breach secrets
    breach_ids = []
    for i in range(3):
        cursor = conn.execute(
            """INSERT INTO rooms (floor, name, description, description_short,
               is_breach, is_hub)
               VALUES (2, ?, 'A rift room.', 'Rift.', 1, 0)""",
            (f"Breach Room {i + 1}",),
        )
        breach_ids.append(cursor.lastrowid)
    conn.commit()

    backend = DummyBackend()
    stats = generate_secrets(conn, backend, breach_room_ids=breach_ids)
    return conn, stats


# ── Total count ──


def test_total_secrets_placed():
    conn, stats = _generate_with_breach()
    assert stats["total"] == SECRETS_PER_EPOCH
    # DB count >= SECRETS_PER_EPOCH because multi-room puzzles create
    # one row per room (2 per group), but stats count groups
    count = conn.execute("SELECT COUNT(*) as cnt FROM secrets").fetchone()
    assert count["cnt"] >= SECRETS_PER_EPOCH


# ── Type distribution ──


def test_observation_count():
    conn, stats = _generate_with_breach()
    assert stats["observation"] == 6


def test_puzzle_count():
    conn, stats = _generate_with_breach()
    assert stats["puzzle"] == 4


def test_lore_count():
    conn, stats = _generate_with_breach()
    assert stats["lore"] == 4


def test_stat_gated_count():
    conn, stats = _generate_with_breach()
    assert stats["stat_gated"] == 3


def test_breach_count():
    conn, stats = _generate_with_breach()
    assert stats["breach"] == 3


# ── Hint tiers ──


def test_all_secrets_have_3_hint_tiers():
    conn, stats = _generate_with_breach()
    secrets = conn.execute(
        "SELECT id, name, hint_tier1, hint_tier2, hint_tier3 FROM secrets"
    ).fetchall()
    for s in secrets:
        assert s["hint_tier1"], f"Secret {s['id']} ({s['name']}) missing hint_tier1"
        assert s["hint_tier2"], f"Secret {s['id']} ({s['name']}) missing hint_tier2"
        assert s["hint_tier3"], f"Secret {s['id']} ({s['name']}) missing hint_tier3"


def test_hints_under_char_limit():
    conn, stats = _generate_with_breach()
    secrets = conn.execute(
        "SELECT id, hint_tier1, hint_tier2, hint_tier3 FROM secrets"
    ).fetchall()
    for s in secrets:
        for tier in ["hint_tier1", "hint_tier2", "hint_tier3"]:
            assert len(s[tier]) <= LLM_OUTPUT_CHAR_LIMIT, (
                f"Secret {s['id']} {tier} too long: {len(s[tier])}"
            )


# ── Forbidden verbs ──


def test_no_forbidden_verbs_in_hints():
    conn, stats = _generate_with_breach()
    secrets = conn.execute(
        "SELECT id, hint_tier1, hint_tier2, hint_tier3 FROM secrets"
    ).fetchall()
    for s in secrets:
        for tier in ["hint_tier1", "hint_tier2", "hint_tier3"]:
            hint = s[tier].lower()
            for verb in HINT_FORBIDDEN_VERBS:
                assert verb not in hint, (
                    f"Secret {s['id']} {tier} contains '{verb}': {s[tier]}"
                )


# ── Puzzle symbols ──


def test_multi_room_puzzles_share_symbol():
    conn, stats = _generate_with_breach()
    groups = conn.execute(
        """SELECT puzzle_group, puzzle_symbol FROM secrets
           WHERE puzzle_group IS NOT NULL"""
    ).fetchall()

    group_symbols: dict[str, set] = {}
    for g in groups:
        grp = g["puzzle_group"]
        if grp not in group_symbols:
            group_symbols[grp] = set()
        group_symbols[grp].add(g["puzzle_symbol"])

    for grp, symbols in group_symbols.items():
        assert len(symbols) == 1, (
            f"Puzzle group '{grp}' has inconsistent symbols: {symbols}"
        )


def test_multi_room_puzzles_have_archetype():
    conn, stats = _generate_with_breach()
    multi = conn.execute(
        "SELECT id, puzzle_archetype FROM secrets WHERE puzzle_group IS NOT NULL"
    ).fetchall()
    for m in multi:
        assert m["puzzle_archetype"], f"Puzzle {m['id']} has no archetype"


# ── Stat-gated ──


def test_stat_gated_on_higher_floors():
    conn, stats = _generate_with_breach()
    stat_gated = conn.execute(
        "SELECT floor FROM secrets WHERE type = 'stat_gated'"
    ).fetchall()
    for s in stat_gated:
        assert s["floor"] >= 3, f"Stat-gated secret on floor {s['floor']} (expected 3+)"


def test_stat_gated_covers_all_stats():
    conn, stats = _generate_with_breach()
    stat_gated = conn.execute(
        "SELECT name FROM secrets WHERE type = 'stat_gated'"
    ).fetchall()
    names = [s["name"] for s in stat_gated]
    # Each stat-gated secret has the stat in its name (e.g., "POW Challenge in...")
    stats_found = set()
    for name in names:
        for stat in ["POW", "SPD", "DEF"]:
            if stat in name:
                stats_found.add(stat)
    assert stats_found == {"POW", "SPD", "DEF"}, f"Missing stats: {stats_found}"


# ── Observation ──


def test_observation_floors():
    conn, stats = _generate_with_breach()
    obs = conn.execute(
        "SELECT floor FROM secrets WHERE type = 'observation'"
    ).fetchall()
    floors = [o["floor"] for o in obs]
    # 4 on floors 1-2, 2 on floors 3-4
    low = sum(1 for f in floors if f <= 2)
    high = sum(1 for f in floors if f >= 3)
    assert low == 4, f"Expected 4 observation on floors 1-2, got {low}"
    assert high == 2, f"Expected 2 observation on floors 3-4, got {high}"


# ── No breach secrets without breach rooms ──


def test_no_breach_secrets_without_rooms():
    conn = _make_db_with_world()
    backend = DummyBackend()
    stats = generate_secrets(conn, backend, breach_room_ids=[])
    assert stats["breach"] == 0
    assert stats["total"] == SECRETS_PER_EPOCH - 3  # 17 without breach


# ── Room uniqueness ──


def test_no_duplicate_rooms_for_secrets():
    conn, stats = _generate_with_breach()
    secrets = conn.execute(
        "SELECT room_id FROM secrets WHERE puzzle_group IS NULL"
    ).fetchall()
    room_ids = [s["room_id"] for s in secrets]
    # Non-puzzle secrets should be in different rooms
    assert len(room_ids) == len(set(room_ids)), "Duplicate rooms for non-puzzle secrets"
