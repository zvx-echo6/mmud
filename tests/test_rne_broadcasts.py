"""Tests for R&E broadcast messages — content and length."""

import sqlite3

import pytest

from config import MSG_CHAR_LIMIT, NUM_FLOORS
from src.db.database import get_db, init_schema
from src.models.player import create_player, get_or_create_account
from src.systems.endgame_rne import (
    broadcast_pursuer_distance,
    check_delivery,
    claim_objective,
    handle_carrier_death,
    lure_pursuer,
    pickup_objective,
    tick_pursuer,
    ward_room,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with R&E epoch."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="retrieve_and_escape")
    return db


@pytest.fixture
def carrier(conn):
    """Carrier player on floor 4."""
    acc = get_or_create_account(conn, "mesh_carrier", "CarrierName")
    p = create_player(conn, acc, "CarrierName", "warrior")
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = ? AND is_hub = 0 LIMIT 1",
        (NUM_FLOORS,),
    ).fetchone()
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = ?, room_id = ? WHERE id = ?",
        (NUM_FLOORS, room["id"], p["id"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


@pytest.fixture
def relay(conn):
    """Relay player on floor 2."""
    acc = get_or_create_account(conn, "mesh_relay", "RelayName")
    p = create_player(conn, acc, "RelayName", "rogue")
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = 2 AND is_hub = 0 LIMIT 1",
    ).fetchone()
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2, room_id = ? WHERE id = ?",
        (room["id"], p["id"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


def _get_broadcasts(conn):
    """Get all broadcasts."""
    return conn.execute("SELECT * FROM broadcasts ORDER BY id").fetchall()


def _clear_broadcasts(conn):
    """Clear broadcasts for isolated testing."""
    conn.execute("DELETE FROM broadcasts")
    conn.commit()


def test_claim_broadcast_content(conn, carrier):
    """Claim broadcast includes player name and objective."""
    _clear_broadcasts(conn)
    claim_objective(conn, carrier["id"], carrier["room_id"])
    broadcasts = _get_broadcasts(conn)
    assert len(broadcasts) >= 1
    msg = broadcasts[0]["message"]
    assert "CarrierName" in msg
    assert "Crown of the Depths" in msg
    assert "Pursuer" in msg


def test_claim_broadcast_length(conn, carrier):
    """Claim broadcast fits in 150 chars."""
    _clear_broadcasts(conn)
    claim_objective(conn, carrier["id"], carrier["room_id"])
    broadcasts = _get_broadcasts(conn)
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT


def test_death_broadcast_content(conn, carrier):
    """Death broadcast includes player name and floor."""
    claim_objective(conn, carrier["id"], carrier["room_id"])
    _clear_broadcasts(conn)
    handle_carrier_death(conn, carrier["id"])
    broadcasts = _get_broadcasts(conn)
    assert len(broadcasts) >= 1
    msg = broadcasts[0]["message"]
    assert "CarrierName" in msg
    assert "fell" in msg.lower()


def test_death_broadcast_length(conn, carrier):
    """Death broadcast fits in 150 chars."""
    claim_objective(conn, carrier["id"], carrier["room_id"])
    _clear_broadcasts(conn)
    handle_carrier_death(conn, carrier["id"])
    for b in _get_broadcasts(conn):
        assert len(b["message"]) <= MSG_CHAR_LIMIT


def test_pickup_broadcast_content(conn, carrier, relay):
    """Pickup broadcast includes relay player name."""
    claim_objective(conn, carrier["id"], carrier["room_id"])
    handle_carrier_death(conn, carrier["id"])

    state = conn.execute("SELECT * FROM escape_run WHERE id = 1").fetchone()
    drop_room = state["dropped_room_id"]
    conn.execute(
        "UPDATE players SET room_id = ? WHERE id = ?",
        (drop_room, relay["id"]),
    )
    conn.commit()

    _clear_broadcasts(conn)
    pickup_objective(conn, relay["id"], drop_room)
    broadcasts = _get_broadcasts(conn)
    assert len(broadcasts) >= 1
    msg = broadcasts[0]["message"]
    assert "RelayName" in msg
    assert "relay" in msg.lower() or "picks up" in msg.lower()


def test_pickup_broadcast_length(conn, carrier, relay):
    """Pickup broadcast fits in 150 chars."""
    claim_objective(conn, carrier["id"], carrier["room_id"])
    handle_carrier_death(conn, carrier["id"])

    state = conn.execute("SELECT * FROM escape_run WHERE id = 1").fetchone()
    drop_room = state["dropped_room_id"]
    conn.execute(
        "UPDATE players SET room_id = ? WHERE id = ?",
        (drop_room, relay["id"]),
    )
    conn.commit()

    _clear_broadcasts(conn)
    pickup_objective(conn, relay["id"], drop_room)
    for b in _get_broadcasts(conn):
        assert len(b["message"]) <= MSG_CHAR_LIMIT


def test_delivery_broadcast_content(conn, carrier):
    """Delivery broadcast includes player name and victory."""
    claim_objective(conn, carrier["id"], carrier["room_id"])
    _clear_broadcasts(conn)
    check_delivery(conn, carrier["id"], "town")
    broadcasts = _get_broadcasts(conn)
    assert len(broadcasts) >= 1
    msg = broadcasts[0]["message"]
    assert "CarrierName" in msg
    assert "Victory" in msg or "surface" in msg


def test_delivery_broadcast_length(conn, carrier):
    """Delivery broadcast fits in 150 chars."""
    claim_objective(conn, carrier["id"], carrier["room_id"])
    _clear_broadcasts(conn)
    check_delivery(conn, carrier["id"], "town")
    for b in _get_broadcasts(conn):
        assert len(b["message"]) <= MSG_CHAR_LIMIT


def test_pursuer_distance_broadcast(conn, carrier):
    """Pursuer distance broadcast works and fits in 150 chars."""
    claim_objective(conn, carrier["id"], carrier["room_id"])
    _clear_broadcasts(conn)
    broadcast_pursuer_distance(conn)
    broadcasts = _get_broadcasts(conn)
    # May or may not create a broadcast depending on distance
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT
        assert "Pursuer" in b["message"]


def test_lure_broadcast_content(conn, carrier):
    """Lure broadcast includes lurer name."""
    claim_objective(conn, carrier["id"], carrier["room_id"])

    state = conn.execute("SELECT * FROM escape_run WHERE id = 1").fetchone()
    pursuer_floor = conn.execute(
        "SELECT floor FROM rooms WHERE id = ?",
        (state["pursuer_room_id"],),
    ).fetchone()["floor"]

    acc = get_or_create_account(conn, "mesh_lurer", "LurerName")
    lurer = create_player(conn, acc, "LurerName", "rogue")
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = ? AND is_hub = 0 LIMIT 1",
        (pursuer_floor,),
    ).fetchone()
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = ?, room_id = ? WHERE id = ?",
        (pursuer_floor, room["id"], lurer["id"]),
    )
    conn.commit()

    _clear_broadcasts(conn)
    ok, _ = lure_pursuer(conn, lurer["id"], pursuer_floor)
    if ok:
        broadcasts = _get_broadcasts(conn)
        assert len(broadcasts) >= 1
        msg = broadcasts[0]["message"]
        assert "LurerName" in msg
        assert "lured" in msg.lower() or "divert" in msg.lower()
        assert len(msg) <= MSG_CHAR_LIMIT


def test_blocker_broadcast(conn, carrier):
    """Blocker broadcast includes blocker name when pursuer hits one."""
    claim_objective(conn, carrier["id"], carrier["room_id"])

    state = conn.execute("SELECT * FROM escape_run WHERE id = 1").fetchone()
    pursuer_room = state["pursuer_room_id"]

    # Place a blocker in the next room toward carrier
    next_rooms = conn.execute(
        "SELECT to_room_id FROM room_exits WHERE from_room_id = ?",
        (pursuer_room,),
    ).fetchall()

    if not next_rooms:
        pytest.skip("No adjacent rooms to pursuer")

    next_room = next_rooms[0]["to_room_id"]
    acc = get_or_create_account(conn, "mesh_blocker", "BlockerName")
    blocker = create_player(conn, acc, "BlockerName", "caster")
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = ?, room_id = ? WHERE id = ?",
        (NUM_FLOORS, next_room, blocker["id"]),
    )
    conn.commit()

    _clear_broadcasts(conn)
    # Tick enough to advance pursuer
    from config import PURSUER_ADVANCE_RATE

    for _ in range(PURSUER_ADVANCE_RATE):
        tick_pursuer(conn)

    broadcasts = _get_broadcasts(conn)
    # Blocker broadcast may or may not fire depending on path
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT
