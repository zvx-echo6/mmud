"""Tests for the Epoch & Floor Unlock Broadcast System.

Covers:
- broadcast_floor_unlock composes message from floor_themes
- Floor 8 boss kill does NOT produce unlock broadcast
- Re-kills on already-dead boss are handled by caller (actions.py)
- Broadcast messages respect 200-char limit
- Epoch generation produces exactly 3 broadcasts (no per-floor atmospheric)
"""

import sqlite3

from config import BROADCAST_CHAR_LIMIT, NUM_FLOORS
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.generation.themegen import generate_floor_themes, get_floor_themes
from src.generation.worldgen import generate_town, generate_world
from src.models.epoch import create_epoch
from src.systems.broadcast import (
    broadcast_floor_unlock,
    create_broadcast,
    get_unseen_broadcasts,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_db():
    """Create an in-memory DB with schema and a basic epoch."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    create_epoch(conn, 1, "hold_the_line", "heist")
    return conn


def _seed_floor_themes(conn):
    """Insert floor themes for testing."""
    themes = {
        1: ("Sunken Halls", "Dripping water echoes", "Cold stone rising", "The halls sink deeper"),
        2: ("Fungal Depths", "Spore clouds drift", "Dim phosphorescence", "Growth overtakes the walls"),
        3: ("Ember Caverns", "Heat radiates from stone", "Orange glow pulses", "The stone burns hotter"),
        4: ("Iron Labyrinth", "Metal groans", "Rust and echoes", "Iron replaces stone"),
        5: ("Blighted Wastes", "Decay hangs thick", "Sickly green light", "The rot spreads"),
        6: ("Crystalline Abyss", "Sharp refractions", "Prismatic flicker", "Crystal formations grow"),
        7: ("Shadow Gauntlet", "Darkness shifts", "Cold pressure builds", "Shadows coalesce"),
        8: ("Void Reach", "Silence absolute", "Nothing reflects", "The void opens"),
    }
    for floor, (name, atmos, beat, trans) in themes.items():
        conn.execute(
            """INSERT INTO floor_themes (floor, floor_name, atmosphere, narrative_beat, floor_transition)
               VALUES (?, ?, ?, ?, ?)""",
            (floor, name, atmos, beat, trans),
        )
    conn.commit()


# ── Floor Unlock Broadcast Tests ──────────────────────────────────────────


def test_floor_unlock_produces_broadcast():
    """Killing floor 1 boss broadcasts floor 2 theme data."""
    conn = _make_db()
    _seed_floor_themes(conn)

    bid = broadcast_floor_unlock(conn, 1)
    assert bid is not None

    row = conn.execute("SELECT message FROM broadcasts WHERE id = ?", (bid,)).fetchone()
    msg = row["message"]

    # Should contain floor 2 data
    assert "Fungal Depths" in msg
    assert "Growth overtakes the walls" in msg


def test_floor_unlock_format():
    """Floor unlock message follows [transition]. [name]. [atmosphere]. format."""
    conn = _make_db()
    _seed_floor_themes(conn)

    bid = broadcast_floor_unlock(conn, 2)
    msg = conn.execute("SELECT message FROM broadcasts WHERE id = ?", (bid,)).fetchone()["message"]

    # Floor 3 theme: "The stone burns hotter. Ember Caverns. Heat radiates from stone."
    assert "The stone burns hotter." in msg
    assert "Ember Caverns." in msg
    assert "Heat radiates from stone." in msg


def test_floor_8_boss_no_unlock_broadcast():
    """Floor 8 boss kill does NOT produce a floor unlock broadcast."""
    conn = _make_db()
    _seed_floor_themes(conn)

    result = broadcast_floor_unlock(conn, 8)
    assert result is None

    # No broadcasts should have been created
    count = conn.execute("SELECT COUNT(*) FROM broadcasts").fetchone()[0]
    assert count == 0


def test_floor_7_boss_unlocks_floor_8():
    """Floor 7 boss kill broadcasts floor 8 unlock."""
    conn = _make_db()
    _seed_floor_themes(conn)

    bid = broadcast_floor_unlock(conn, 7)
    assert bid is not None

    msg = conn.execute("SELECT message FROM broadcasts WHERE id = ?", (bid,)).fetchone()["message"]
    assert "Void Reach" in msg


def test_floor_unlock_broadcast_under_200_chars():
    """All floor unlock broadcasts are under 200 characters."""
    conn = _make_db()
    _seed_floor_themes(conn)

    for floor in range(1, NUM_FLOORS):
        bid = broadcast_floor_unlock(conn, floor)
        assert bid is not None
        msg = conn.execute("SELECT message FROM broadcasts WHERE id = ?", (bid,)).fetchone()["message"]
        assert len(msg) <= BROADCAST_CHAR_LIMIT, f"Floor {floor+1} unlock too long: {len(msg)} chars"


def test_floor_unlock_no_themes_returns_none():
    """If no floor_themes data exists, broadcast_floor_unlock returns None."""
    conn = _make_db()
    # No floor themes seeded

    result = broadcast_floor_unlock(conn, 1)
    assert result is None


def test_floor_unlock_is_tier_1():
    """Floor unlock broadcasts are tier 1 (immediate)."""
    conn = _make_db()
    _seed_floor_themes(conn)

    bid = broadcast_floor_unlock(conn, 3)
    row = conn.execute("SELECT tier FROM broadcasts WHERE id = ?", (bid,)).fetchone()
    assert row["tier"] == 1


# ── Epoch Generation Broadcast Count ──────────────────────────────────────


def test_epoch_gen_no_atmospheric_broadcasts():
    """Epoch generation produces 0 per-floor atmospheric broadcasts."""
    conn = _make_db()
    b = DummyBackend()
    generate_floor_themes(conn, b)
    generate_town(conn, b)
    floor_themes = get_floor_themes(conn)
    generate_world(conn, b, floor_themes=floor_themes)

    # Count tier 2 broadcasts — should be 0 (atmospheric loop removed)
    count = conn.execute("SELECT COUNT(*) FROM broadcasts WHERE tier = 2").fetchone()[0]
    assert count == 0


# ── create_broadcast 200-char limit ───────────────────────────────────────


def test_create_broadcast_truncates_at_200():
    """create_broadcast truncates messages to BROADCAST_CHAR_LIMIT (200)."""
    conn = _make_db()
    long_msg = "X" * 300
    bid = create_broadcast(conn, 1, long_msg)
    row = conn.execute("SELECT message FROM broadcasts WHERE id = ?", (bid,)).fetchone()
    assert len(row["message"]) == BROADCAST_CHAR_LIMIT


def test_create_broadcast_allows_200():
    """create_broadcast allows exactly 200-char messages."""
    conn = _make_db()
    msg_200 = "Y" * 200
    bid = create_broadcast(conn, 1, msg_200)
    row = conn.execute("SELECT message FROM broadcasts WHERE id = ?", (bid,)).fetchone()
    assert len(row["message"]) == 200
    assert row["message"] == msg_200
