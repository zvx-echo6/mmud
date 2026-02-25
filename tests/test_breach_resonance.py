"""Tests for the Breach Resonance mini-event (puzzle dungeon)."""

import sqlite3

import pytest

from config import BREACH_SECRETS, MSG_CHAR_LIMIT
from src.db.database import get_db, init_schema
from src.models.player import create_player, get_or_create_account
from src.systems.breach_resonance import (
    check_resonance_complete,
    examine_breach_object,
    format_resonance_status,
    get_breach_secret_progress,
    get_resonance_state,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with a resonance breach epoch."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="hold_the_line", breach_type="resonance")
    # Open the breach
    db.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    db.execute("UPDATE breach SET active = 1 WHERE id = 1")
    db.commit()
    return db


@pytest.fixture
def player(conn):
    """Player in a breach room with a secret."""
    acc = get_or_create_account(conn, "mesh_res", "PuzzlePlayer")
    p = create_player(conn, acc, "PuzzlePlayer", "scout")

    # Find a breach room with a secret
    secret_room = conn.execute(
        """SELECT s.room_id FROM secrets s
           JOIN rooms r ON s.room_id = r.id
           WHERE s.type = 'breach' AND r.is_breach = 1
           LIMIT 1"""
    ).fetchone()

    if secret_room:
        room_id = secret_room["room_id"]
    else:
        # Fallback: just use first breach room
        room_id = conn.execute(
            "SELECT id FROM rooms WHERE is_breach = 1 LIMIT 1"
        ).fetchone()["id"]

    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2, room_id = ? WHERE id = ?",
        (room_id, p["id"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


def _get_breach_secret_rooms(conn):
    """Get rooms that have breach secrets."""
    return conn.execute(
        """SELECT DISTINCT s.room_id FROM secrets s
           WHERE s.type = 'breach'"""
    ).fetchall()


# ── State ──


def test_resonance_state(conn):
    """Resonance state is initialized."""
    state = get_resonance_state(conn)
    assert state is not None
    assert state["mini_event"] == "resonance"
    assert state["completed"] == 0


# ── Secret Discovery ──


def test_examine_finds_secret(conn, player):
    """Examining a room with a breach secret discovers it."""
    # Ensure there's a secret in this room
    secret = conn.execute(
        "SELECT id FROM secrets WHERE room_id = ? AND type = 'breach' AND discovered_by IS NULL",
        (player["room_id"],),
    ).fetchone()

    if not secret:
        pytest.skip("No undiscovered breach secret in player's room")

    found, msg = examine_breach_object(conn, player["id"], player["room_id"])
    assert found
    assert len(msg) <= MSG_CHAR_LIMIT


def test_examine_empty_room(conn, player):
    """Examining a room with no secret returns nothing."""
    # Use a room without a breach secret
    no_secret_room = conn.execute(
        """SELECT r.id FROM rooms r
           WHERE r.is_breach = 1
           AND r.id NOT IN (SELECT room_id FROM secrets WHERE type = 'breach')
           LIMIT 1"""
    ).fetchone()

    if not no_secret_room:
        pytest.skip("All breach rooms have secrets")

    found, msg = examine_breach_object(conn, player["id"], no_secret_room["id"])
    assert not found
    assert "nothing" in msg.lower() or "reveals nothing" in msg.lower()


def test_examine_already_found(conn, player):
    """Re-examining a room where secret was already found returns nothing new."""
    secret = conn.execute(
        "SELECT id FROM secrets WHERE room_id = ? AND type = 'breach' AND discovered_by IS NULL",
        (player["room_id"],),
    ).fetchone()

    if not secret:
        pytest.skip("No secret in player's room")

    # Find it
    examine_breach_object(conn, player["id"], player["room_id"])
    # Try again
    found, msg = examine_breach_object(conn, player["id"], player["room_id"])
    assert not found


def test_examine_updates_player_count(conn, player):
    """Finding a secret increments player secrets_found."""
    secret = conn.execute(
        "SELECT id FROM secrets WHERE room_id = ? AND type = 'breach' AND discovered_by IS NULL",
        (player["room_id"],),
    ).fetchone()

    if not secret:
        pytest.skip("No secret in player's room")

    before = conn.execute(
        "SELECT secrets_found FROM players WHERE id = ?", (player["id"],)
    ).fetchone()["secrets_found"]

    examine_breach_object(conn, player["id"], player["room_id"])

    after = conn.execute(
        "SELECT secrets_found FROM players WHERE id = ?", (player["id"],)
    ).fetchone()["secrets_found"]

    assert after == before + 1


def test_examine_broadcasts(conn, player):
    """Finding a secret creates a broadcast."""
    secret = conn.execute(
        "SELECT id FROM secrets WHERE room_id = ? AND type = 'breach' AND discovered_by IS NULL",
        (player["room_id"],),
    ).fetchone()

    if not secret:
        pytest.skip("No secret in player's room")

    conn.execute("DELETE FROM broadcasts")
    conn.commit()
    examine_breach_object(conn, player["id"], player["room_id"])

    broadcasts = conn.execute("SELECT * FROM broadcasts").fetchall()
    assert len(broadcasts) >= 1
    assert "PuzzlePlayer" in broadcasts[0]["message"]


# ── Secret Progress ──


def test_secret_progress_initial(conn):
    """Initial progress shows 0 found."""
    progress = get_breach_secret_progress(conn)
    assert progress["found"] == 0
    assert progress["total"] >= 1  # At least 1 breach secret should exist


# ── Completion ──


def test_resonance_completes_on_all_secrets(conn, player):
    """Finding all breach secrets completes resonance."""
    secrets = conn.execute(
        "SELECT id FROM secrets WHERE type = 'breach'"
    ).fetchall()

    if not secrets:
        pytest.skip("No breach secrets generated")

    # Discover all secrets
    for s in secrets:
        conn.execute(
            "UPDATE secrets SET discovered_by = ? WHERE id = ?",
            (player["id"], s["id"]),
        )
    conn.commit()

    done, msg = check_resonance_complete(conn)
    assert done
    assert "understood" in msg.lower() or "secrets" in msg.lower()


def test_resonance_not_complete_partial(conn, player):
    """Partial secret discovery doesn't complete resonance."""
    secrets = conn.execute(
        "SELECT id FROM secrets WHERE type = 'breach'"
    ).fetchall()

    if len(secrets) < 2:
        pytest.skip("Need at least 2 breach secrets")

    # Discover only the first
    conn.execute(
        "UPDATE secrets SET discovered_by = ? WHERE id = ?",
        (player["id"], secrets[0]["id"]),
    )
    conn.commit()

    done, msg = check_resonance_complete(conn)
    assert not done


def test_bonus_cache_on_completion(conn, player):
    """Bonus cache item created on completion."""
    secrets = conn.execute(
        "SELECT id FROM secrets WHERE type = 'breach'"
    ).fetchall()

    if not secrets:
        pytest.skip("No breach secrets")

    for s in secrets:
        conn.execute(
            "UPDATE secrets SET discovered_by = ? WHERE id = ?",
            (player["id"], s["id"]),
        )
    conn.commit()
    check_resonance_complete(conn)

    crystal = conn.execute(
        "SELECT * FROM items WHERE name = 'Resonance Crystal'"
    ).fetchone()
    assert crystal is not None
    assert crystal["tier"] == 5


# ── Status ──


def test_status_active(conn):
    """Status shows progress when active."""
    status = format_resonance_status(conn)
    assert "Resonance" in status
    assert "/" in status


def test_status_complete(conn, player):
    """Status shows complete after all secrets found."""
    secrets = conn.execute(
        "SELECT id FROM secrets WHERE type = 'breach'"
    ).fetchall()

    for s in secrets:
        conn.execute(
            "UPDATE secrets SET discovered_by = ? WHERE id = ?",
            (player["id"], s["id"]),
        )
    conn.commit()
    check_resonance_complete(conn)

    status = format_resonance_status(conn)
    assert "understood" in status.lower() or "bonus" in status.lower()


# ── Broadcast Length ──


def test_all_resonance_broadcasts_under_150(conn, player):
    """All resonance broadcasts fit in 150 chars."""
    # Find all secrets to generate broadcasts
    rooms = _get_breach_secret_rooms(conn)
    for r in rooms:
        examine_breach_object(conn, player["id"], r["room_id"])

    secrets = conn.execute("SELECT id FROM secrets WHERE type = 'breach'").fetchall()
    for s in secrets:
        conn.execute(
            "UPDATE secrets SET discovered_by = ? WHERE id = ?",
            (player["id"], s["id"]),
        )
    conn.commit()
    check_resonance_complete(conn)

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT, f"Too long: {b['message']}"
