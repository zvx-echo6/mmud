"""Tests for the Breach Heist mini-event (mini R&E)."""

import sqlite3

import pytest

from config import MSG_CHAR_LIMIT
from src.db.database import get_db, init_schema
from src.models.player import create_player, get_or_create_account
from src.systems.breach_heist import (
    check_heist_delivery,
    claim_artifact,
    format_heist_status,
    get_heist_pursuer_distance,
    get_heist_state,
    handle_heist_carrier_death,
    is_heist_carrier,
    pickup_heist_artifact,
    update_heist_carrier,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with a heist breach epoch."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="hold_the_line", breach_type="heist")
    # Open the breach
    db.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    db.execute("UPDATE breach SET active = 1 WHERE id = 1")
    db.commit()
    return db


@pytest.fixture
def player(conn):
    """Player in the artifact room with mini-boss dead."""
    acc = get_or_create_account(conn, "mesh_heist", "HeistPlayer")
    p = create_player(conn, acc, "HeistPlayer", "scout")

    # Get artifact room
    state = conn.execute("SELECT heist_artifact_room_id FROM breach WHERE id = 1").fetchone()
    art_room = state["heist_artifact_room_id"]

    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2, room_id = ? WHERE id = ?",
        (art_room, p["id"]),
    )
    # Kill the breach boss in that room
    conn.execute(
        "UPDATE monsters SET hp = 0 WHERE room_id = ? AND is_breach_boss = 1",
        (art_room,),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


@pytest.fixture
def relay(conn):
    """Second player for relay tests."""
    acc = get_or_create_account(conn, "mesh_relay", "RelayPlayer")
    p = create_player(conn, acc, "RelayPlayer", "warrior")
    room = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2, room_id = ? WHERE id = ?",
        (room["id"], p["id"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


# ── Artifact Claim ──


def test_claim_artifact(conn, player):
    """Claiming artifact sets carrier."""
    ok, msg = claim_artifact(conn, player["id"], player["room_id"])
    assert ok
    assert "claimed" in msg.lower() or "artifact" in msg.lower()

    state = get_heist_state(conn)
    assert state["heist_artifact_carrier"] == player["id"]


def test_claim_requires_dead_boss(conn):
    """Can't claim artifact with living boss."""
    acc = get_or_create_account(conn, "mesh_alive", "AliveTest")
    p = create_player(conn, acc, "AliveTest", "warrior")

    art_room = conn.execute(
        "SELECT heist_artifact_room_id FROM breach WHERE id = 1"
    ).fetchone()["heist_artifact_room_id"]

    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2, room_id = ? WHERE id = ?",
        (art_room, p["id"]),
    )
    # Boss alive — reset HP
    conn.execute(
        "UPDATE monsters SET hp = hp_max WHERE room_id = ? AND is_breach_boss = 1",
        (art_room,),
    )
    conn.commit()

    ok, msg = claim_artifact(conn, p["id"], art_room)
    assert not ok
    assert "guardian" in msg.lower() or "defeat" in msg.lower()


def test_double_claim_fails(conn, player):
    """Can't claim twice."""
    claim_artifact(conn, player["id"], player["room_id"])
    ok, msg = claim_artifact(conn, player["id"], player["room_id"])
    assert not ok


def test_claim_broadcasts(conn, player):
    """Claim creates a broadcast."""
    conn.execute("DELETE FROM broadcasts")
    conn.commit()
    claim_artifact(conn, player["id"], player["room_id"])
    broadcasts = conn.execute("SELECT * FROM broadcasts").fetchall()
    assert len(broadcasts) >= 1
    assert "HeistPlayer" in broadcasts[0]["message"]


# ── Carrier Identity ──


def test_is_heist_carrier(conn, player):
    """is_heist_carrier returns True for carrier."""
    claim_artifact(conn, player["id"], player["room_id"])
    assert is_heist_carrier(conn, player["id"])


def test_not_heist_carrier(conn, player, relay):
    """is_heist_carrier returns False for non-carrier."""
    claim_artifact(conn, player["id"], player["room_id"])
    assert not is_heist_carrier(conn, relay["id"])


# ── Carrier Death & Relay ──


def test_carrier_death_drops_artifact(conn, player):
    """Carrier death drops artifact."""
    claim_artifact(conn, player["id"], player["room_id"])
    msg = handle_heist_carrier_death(conn, player["id"])
    assert msg is not None
    assert "fell" in msg.lower()

    state = get_heist_state(conn)
    assert state["heist_artifact_carrier"] is None


def test_pickup_dropped_artifact(conn, player, relay):
    """Relay player can pick up dropped artifact."""
    claim_artifact(conn, player["id"], player["room_id"])
    handle_heist_carrier_death(conn, player["id"])

    # Move relay to the drop room
    drop_room = conn.execute(
        "SELECT heist_artifact_room_id FROM breach WHERE id = 1"
    ).fetchone()["heist_artifact_room_id"]
    conn.execute(
        "UPDATE players SET room_id = ? WHERE id = ?",
        (drop_room, relay["id"]),
    )
    conn.commit()

    ok, msg = pickup_heist_artifact(conn, relay["id"], drop_room)
    assert ok
    assert "recovered" in msg.lower() or "artifact" in msg.lower()

    state = get_heist_state(conn)
    assert state["heist_artifact_carrier"] == relay["id"]


def test_pickup_wrong_room_fails(conn, player, relay):
    """Can't pick up artifact from wrong room."""
    claim_artifact(conn, player["id"], player["room_id"])
    handle_heist_carrier_death(conn, player["id"])

    wrong_room = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    ok, msg = pickup_heist_artifact(conn, relay["id"], wrong_room)
    # Might succeed if wrong_room happens to be the drop room, otherwise fails
    if wrong_room != player["room_id"]:
        assert not ok


# ── Delivery ──


def test_delivery_completes_heist(conn, player):
    """Delivering artifact to town completes heist."""
    claim_artifact(conn, player["id"], player["room_id"])
    conn.execute("UPDATE players SET state = 'town' WHERE id = ?", (player["id"],))
    conn.commit()

    delivered, msg = check_heist_delivery(conn, player["id"], "town")
    assert delivered
    assert "complete" in msg.lower() or "delivered" in msg.lower()

    state = get_heist_state(conn)
    assert state["completed"] == 1


def test_delivery_not_in_town(conn, player):
    """No delivery when not in town."""
    claim_artifact(conn, player["id"], player["room_id"])
    delivered, msg = check_heist_delivery(conn, player["id"], "dungeon")
    assert not delivered


def test_non_carrier_cannot_deliver(conn, player, relay):
    """Non-carrier can't deliver."""
    claim_artifact(conn, player["id"], player["room_id"])
    delivered, msg = check_heist_delivery(conn, relay["id"], "town")
    assert not delivered


# ── Status Display ──


def test_status_unclaimed(conn):
    """Status shows unclaimed state."""
    status = format_heist_status(conn)
    assert "unclaimed" in status.lower() or "guardian" in status.lower()


def test_status_active(conn, player):
    """Status shows carrier when active."""
    claim_artifact(conn, player["id"], player["room_id"])
    status = format_heist_status(conn)
    assert "HeistPlayer" in status


def test_status_complete(conn, player):
    """Status shows complete."""
    claim_artifact(conn, player["id"], player["room_id"])
    conn.execute("UPDATE players SET state = 'town' WHERE id = ?", (player["id"],))
    conn.commit()
    check_heist_delivery(conn, player["id"], "town")
    status = format_heist_status(conn)
    assert "complete" in status.lower()


# ── Broadcast Length ──


def test_all_heist_broadcasts_under_150(conn, player, relay):
    """All heist broadcasts fit in 150 chars."""
    claim_artifact(conn, player["id"], player["room_id"])
    handle_heist_carrier_death(conn, player["id"])

    drop_room = conn.execute(
        "SELECT heist_artifact_room_id FROM breach WHERE id = 1"
    ).fetchone()["heist_artifact_room_id"]
    conn.execute(
        "UPDATE players SET room_id = ? WHERE id = ?",
        (drop_room, relay["id"]),
    )
    conn.commit()
    pickup_heist_artifact(conn, relay["id"], drop_room)

    conn.execute("UPDATE players SET state = 'town' WHERE id = ?", (relay["id"],))
    conn.commit()
    check_heist_delivery(conn, relay["id"], "town")

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT, f"Too long: {b['message']}"
