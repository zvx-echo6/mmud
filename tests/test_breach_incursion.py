"""Tests for the Breach Incursion mini-event (mini HtL)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from config import INCURSION_HOLD_HOURS, INCURSION_REGEN_ROOMS_PER_DAY, MSG_CHAR_LIMIT
from src.db.database import get_db, init_schema
from src.models.player import create_player, get_or_create_account
from src.systems.breach_incursion import (
    apply_incursion_regen,
    check_incursion_hold,
    clear_breach_room,
    format_incursion_status,
    get_breach_room_status,
    get_incursion_state,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with an incursion breach epoch."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="hold_the_line", breach_type="incursion")
    # Open the breach
    db.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    db.execute("UPDATE breach SET active = 1 WHERE id = 1")
    db.commit()
    return db


@pytest.fixture
def player(conn):
    """Player in a breach room."""
    acc = get_or_create_account(conn, "mesh_inc", "IncursionPlayer")
    p = create_player(conn, acc, "IncursionPlayer", "caster")
    room = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 AND htl_cleared = 0 ORDER BY id LIMIT 1"
    ).fetchone()
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2, room_id = ? WHERE id = ?",
        (room["id"], p["id"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


def _get_breach_rooms(conn):
    return conn.execute("SELECT id FROM rooms WHERE is_breach = 1 ORDER BY id").fetchall()


def _clear_all_breach_rooms(conn, player_id):
    rooms = _get_breach_rooms(conn)
    for r in rooms:
        clear_breach_room(conn, r["id"], player_id)


# ── Room Clearing ──


def test_clear_breach_room(conn, player):
    """Clearing a breach room marks it cleared."""
    result = clear_breach_room(conn, player["room_id"], player["id"])
    assert result["cleared"] is True

    room = conn.execute(
        "SELECT htl_cleared FROM rooms WHERE id = ?", (player["room_id"],)
    ).fetchone()
    assert room["htl_cleared"] == 1


def test_clear_already_cleared_noop(conn, player):
    """Clearing an already-cleared room is a no-op."""
    clear_breach_room(conn, player["room_id"], player["id"])
    result = clear_breach_room(conn, player["room_id"], player["id"])
    assert result["cleared"] is False


def test_clear_non_breach_room_noop(conn, player):
    """Clearing a non-breach room is a no-op."""
    normal_room = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 0 LIMIT 1"
    ).fetchone()
    result = clear_breach_room(conn, normal_room["id"], player["id"])
    assert result["cleared"] is False


# ── All Clear / Hold Timer ──


def test_all_clear_starts_hold(conn, player):
    """Clearing all breach rooms starts the hold timer."""
    rooms = _get_breach_rooms(conn)
    for i, r in enumerate(rooms):
        result = clear_breach_room(conn, r["id"], player["id"])

    assert result["all_clear"] is True
    assert result["hold_started"] is True

    state = get_incursion_state(conn)
    assert state["incursion_hold_started_at"] is not None


def test_hold_timer_not_complete_immediately(conn, player):
    """Hold timer doesn't complete immediately."""
    _clear_all_breach_rooms(conn, player["id"])
    done, msg = check_incursion_hold(conn)
    assert not done


def test_hold_timer_completes_after_48h(conn, player):
    """Hold timer completes after 48 hours."""
    _clear_all_breach_rooms(conn, player["id"])

    # Set hold started time to 49 hours ago
    past = (datetime.now(timezone.utc) - timedelta(hours=INCURSION_HOLD_HOURS + 1)).isoformat()
    conn.execute(
        "UPDATE breach SET incursion_hold_started_at = ? WHERE id = 1", (past,)
    )
    conn.commit()

    done, msg = check_incursion_hold(conn)
    assert done
    assert "secured" in msg.lower()

    state = get_incursion_state(conn)
    assert state["completed"] == 1


# ── Regen ──


def test_regen_reverts_rooms(conn, player):
    """Regen reverts cleared breach rooms."""
    _clear_all_breach_rooms(conn, player["id"])
    result = apply_incursion_regen(conn)
    assert result["reverted"] > 0
    assert result["reverted"] <= INCURSION_REGEN_ROOMS_PER_DAY


def test_regen_resets_hold_timer(conn, player):
    """Regen resets the hold timer if rooms are lost."""
    _clear_all_breach_rooms(conn, player["id"])

    state = get_incursion_state(conn)
    assert state["incursion_hold_started_at"] is not None

    result = apply_incursion_regen(conn)
    assert result["timer_reset"] is True

    state = get_incursion_state(conn)
    assert state["incursion_hold_started_at"] is None


def test_regen_spawns_monsters(conn, player):
    """Regen spawns monsters in reverted rooms."""
    _clear_all_breach_rooms(conn, player["id"])
    initial_monsters = conn.execute(
        "SELECT COUNT(*) as cnt FROM monsters WHERE name = 'Rift Spawn'"
    ).fetchone()["cnt"]

    apply_incursion_regen(conn)

    new_monsters = conn.execute(
        "SELECT COUNT(*) as cnt FROM monsters WHERE name = 'Rift Spawn'"
    ).fetchone()["cnt"]
    assert new_monsters > initial_monsters


def test_no_regen_when_completed(conn, player):
    """No regen after completion."""
    _clear_all_breach_rooms(conn, player["id"])
    past = (datetime.now(timezone.utc) - timedelta(hours=INCURSION_HOLD_HOURS + 1)).isoformat()
    conn.execute(
        "UPDATE breach SET incursion_hold_started_at = ? WHERE id = 1", (past,)
    )
    conn.commit()
    check_incursion_hold(conn)

    result = apply_incursion_regen(conn)
    assert result["reverted"] == 0


# ── Status ──


def test_room_status(conn, player):
    """Room status shows cleared/total."""
    status = get_breach_room_status(conn)
    assert status["total"] >= 5
    assert status["cleared"] == 0

    clear_breach_room(conn, player["room_id"], player["id"])
    status = get_breach_room_status(conn)
    assert status["cleared"] == 1


def test_format_status_active(conn):
    """Format status when active."""
    status = format_incursion_status(conn)
    assert "Incursion" in status
    assert "/" in status


def test_format_status_complete(conn, player):
    """Format status when complete."""
    _clear_all_breach_rooms(conn, player["id"])
    past = (datetime.now(timezone.utc) - timedelta(hours=INCURSION_HOLD_HOURS + 1)).isoformat()
    conn.execute(
        "UPDATE breach SET incursion_hold_started_at = ? WHERE id = 1", (past,)
    )
    conn.commit()
    check_incursion_hold(conn)

    status = format_incursion_status(conn)
    assert "secured" in status.lower()


# ── Broadcast Length ──


def test_all_incursion_broadcasts_under_150(conn, player):
    """All incursion broadcasts fit in 150 chars."""
    _clear_all_breach_rooms(conn, player["id"])
    apply_incursion_regen(conn)

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT, f"Too long: {b['message']}"
