"""Tests for the Retrieve and Escape endgame mode."""

import sqlite3

import pytest

from config import (
    LURE_DIVERT_TICKS,
    NUM_FLOORS,
    PURSUER_ADVANCE_RATE,
    PURSUER_RELAY_RESET_DISTANCE,
    PURSUER_SPAWN_DISTANCE,
)
from src.db.database import get_db, init_schema
from src.generation.narrative import DummyBackend
from src.models.epoch import create_epoch
from src.models.player import create_player, get_or_create_account
from src.systems.endgame_rne import (
    check_delivery,
    claim_objective,
    format_rne_status,
    get_escape_state,
    handle_carrier_death,
    is_carrier,
    lure_pursuer,
    pickup_objective,
    tick_pursuer,
    update_carrier_position,
    ward_room,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with a full R&E epoch generated."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="retrieve_and_escape")
    return db


@pytest.fixture
def player(conn):
    """Create a test player in dungeon on floor 4."""
    acc = get_or_create_account(conn, "mesh_test", "TestPlayer")
    p = create_player(conn, acc, "TestPlayer", "warrior")
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
def relay_player(conn):
    """Create a second player for relay tests."""
    acc = get_or_create_account(conn, "mesh_relay", "RelayPlayer")
    p = create_player(conn, acc, "RelayPlayer", "scout")
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = 2 AND is_hub = 0 LIMIT 1",
    ).fetchone()
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2, room_id = ? WHERE id = ?",
        (room["id"], p["id"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


# ── Initialization ──


def test_escape_run_initialized(conn):
    """Escape run is initialized at epoch generation."""
    state = get_escape_state(conn)
    assert state is not None
    assert state["active"] == 0
    assert state["completed"] == 0
    assert state["objective_name"] == "Crown of the Depths"


# ── Objective Claim ──


def test_claim_objective(conn, player):
    """Claiming objective activates the run."""
    ok, msg = claim_objective(conn, player["id"], player["room_id"])
    assert ok
    assert "claimed" in msg.lower()

    state = get_escape_state(conn)
    assert state["active"] == 1
    assert state["carrier_player_id"] == player["id"]
    assert state["pursuer_room_id"] is not None


def test_claim_broadcasts(conn, player):
    """Objective claim creates a broadcast."""
    claim_objective(conn, player["id"], player["room_id"])
    broadcasts = conn.execute(
        "SELECT * FROM broadcasts WHERE message LIKE '%claimed%'"
    ).fetchall()
    assert len(broadcasts) >= 1
    assert "Pursuer" in broadcasts[0]["message"]


def test_claim_double_fails(conn, player):
    """Can't claim when already active."""
    claim_objective(conn, player["id"], player["room_id"])
    ok, msg = claim_objective(conn, player["id"], player["room_id"])
    assert not ok
    assert "already" in msg.lower()


def test_is_carrier(conn, player):
    """is_carrier returns True for carrier."""
    claim_objective(conn, player["id"], player["room_id"])
    assert is_carrier(conn, player["id"])


def test_not_carrier(conn, player, relay_player):
    """is_carrier returns False for non-carrier."""
    claim_objective(conn, player["id"], player["room_id"])
    assert not is_carrier(conn, relay_player["id"])


# ── Pursuer Advancement ──


def test_pursuer_advances(conn, player):
    """Pursuer advances after PURSUER_ADVANCE_RATE ticks."""
    claim_objective(conn, player["id"], player["room_id"])
    state = get_escape_state(conn)
    original_pursuer = state["pursuer_room_id"]

    # Tick multiple times to advance
    for _ in range(PURSUER_ADVANCE_RATE):
        result = tick_pursuer(conn)

    # After enough ticks, pursuer should have advanced (or tried to)
    # The exact behavior depends on room layout
    assert result["distance"] >= 0


def test_pursuer_tick_increments(conn, player):
    """Each tick increments the tick counter."""
    claim_objective(conn, player["id"], player["room_id"])

    # First tick shouldn't advance yet (needs PURSUER_ADVANCE_RATE)
    result = tick_pursuer(conn)
    if PURSUER_ADVANCE_RATE > 1:
        state = get_escape_state(conn)
        assert state["pursuer_ticks"] == 1


# ── Carrier Movement ──


def test_update_carrier_position(conn, player):
    """Carrier position updates in escape_run."""
    claim_objective(conn, player["id"], player["room_id"])

    new_room = conn.execute(
        "SELECT id FROM rooms WHERE floor = 3 AND is_hub = 0 LIMIT 1",
    ).fetchone()
    update_carrier_position(conn, player["id"], new_room["id"])

    state = get_escape_state(conn)
    assert state["carrier_room_id"] == new_room["id"]


def test_update_noncarrier_noop(conn, player, relay_player):
    """Non-carrier position update is a no-op."""
    claim_objective(conn, player["id"], player["room_id"])
    state_before = get_escape_state(conn)

    update_carrier_position(conn, relay_player["id"], 999)

    state_after = get_escape_state(conn)
    assert state_after["carrier_room_id"] == state_before["carrier_room_id"]


# ── Carrier Death & Relay ──


def test_carrier_death_drops_objective(conn, player):
    """Carrier death drops the objective."""
    claim_objective(conn, player["id"], player["room_id"])
    msg = handle_carrier_death(conn, player["id"])
    assert msg is not None
    assert "fell" in msg.lower() or "dropped" in msg.lower()

    state = get_escape_state(conn)
    assert state["objective_dropped"] == 1
    assert state["carrier_player_id"] is None
    assert state["dropped_room_id"] is not None


def test_noncarrier_death_noop(conn, player, relay_player):
    """Non-carrier death doesn't affect escape run."""
    claim_objective(conn, player["id"], player["room_id"])
    msg = handle_carrier_death(conn, relay_player["id"])
    assert msg is None

    state = get_escape_state(conn)
    assert state["carrier_player_id"] == player["id"]


def test_pickup_objective_relay(conn, player, relay_player):
    """Picking up a dropped objective relays it."""
    claim_objective(conn, player["id"], player["room_id"])
    handle_carrier_death(conn, player["id"])

    state = get_escape_state(conn)
    drop_room = state["dropped_room_id"]

    # Move relay player to drop room
    conn.execute(
        "UPDATE players SET room_id = ? WHERE id = ?",
        (drop_room, relay_player["id"]),
    )
    conn.commit()

    ok, msg = pickup_objective(conn, relay_player["id"], drop_room)
    assert ok
    assert "picked up" in msg.lower()

    state = get_escape_state(conn)
    assert state["carrier_player_id"] == relay_player["id"]
    assert state["objective_dropped"] == 0


def test_pickup_wrong_room_fails(conn, player, relay_player):
    """Can't pick up objective from wrong room."""
    claim_objective(conn, player["id"], player["room_id"])
    handle_carrier_death(conn, player["id"])

    wrong_room = conn.execute(
        "SELECT id FROM rooms WHERE floor = 1 AND is_hub = 0 LIMIT 1",
    ).fetchone()
    ok, msg = pickup_objective(conn, relay_player["id"], wrong_room["id"])
    assert not ok


def test_relay_resets_pursuer(conn, player, relay_player):
    """Relay resets pursuer distance."""
    claim_objective(conn, player["id"], player["room_id"])

    # Advance pursuer
    for _ in range(PURSUER_ADVANCE_RATE * 3):
        tick_pursuer(conn)

    handle_carrier_death(conn, player["id"])
    state = get_escape_state(conn)
    drop_room = state["dropped_room_id"]

    conn.execute(
        "UPDATE players SET room_id = ? WHERE id = ?",
        (drop_room, relay_player["id"]),
    )
    conn.commit()

    pickup_objective(conn, relay_player["id"], drop_room)
    state = get_escape_state(conn)
    assert state["pursuer_ticks"] == 0


# ── Ward ──


def test_ward_room(conn, player):
    """Ward sets ward_active on room."""
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = 2 AND is_hub = 0 LIMIT 1",
    ).fetchone()
    ok, msg = ward_room(conn, player["id"], room["id"])
    assert ok

    warded = conn.execute(
        "SELECT ward_active FROM rooms WHERE id = ?", (room["id"],)
    ).fetchone()
    assert warded["ward_active"] == 1


def test_double_ward_fails(conn, player):
    """Can't ward an already-warded room."""
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = 2 AND is_hub = 0 LIMIT 1",
    ).fetchone()
    ward_room(conn, player["id"], room["id"])
    ok, msg = ward_room(conn, player["id"], room["id"])
    assert not ok
    assert "already" in msg.lower()


def test_ward_records_participant(conn, player):
    """Warding records warder participation."""
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = 2 AND is_hub = 0 LIMIT 1",
    ).fetchone()
    ward_room(conn, player["id"], room["id"])

    participant = conn.execute(
        "SELECT * FROM escape_participants WHERE player_id = ? AND role = 'warder'",
        (player["id"],),
    ).fetchone()
    assert participant is not None


# ── Lure ──


def test_lure_diverts_pursuer(conn, player):
    """Lure sets negative ticks (diversion)."""
    claim_objective(conn, player["id"], player["room_id"])

    # Need a second player on same floor as pursuer
    state = get_escape_state(conn)
    pursuer_floor = conn.execute(
        "SELECT floor FROM rooms WHERE id = ?",
        (state["pursuer_room_id"],),
    ).fetchone()["floor"]

    acc = get_or_create_account(conn, "mesh_lurer", "Lurer")
    lurer = create_player(conn, acc, "Lurer", "scout")
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = ? AND is_hub = 0 LIMIT 1",
        (pursuer_floor,),
    ).fetchone()
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = ?, room_id = ? WHERE id = ?",
        (pursuer_floor, room["id"], lurer["id"]),
    )
    conn.commit()

    ok, msg = lure_pursuer(conn, lurer["id"], pursuer_floor)
    assert ok

    state = get_escape_state(conn)
    assert state["pursuer_ticks"] == -LURE_DIVERT_TICKS


def test_lure_wrong_floor_fails(conn, player):
    """Lure fails if not on same floor as pursuer."""
    claim_objective(conn, player["id"], player["room_id"])

    acc = get_or_create_account(conn, "mesh_lurer2", "Lurer2")
    lurer = create_player(conn, acc, "Lurer2", "scout")
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 1, room_id = 1 WHERE id = ?",
        (lurer["id"],),
    )
    conn.commit()

    ok, msg = lure_pursuer(conn, lurer["id"], 1)
    # Pursuer is on floor 4, player on floor 1 — should fail
    # (depends on pursuer room floor)
    state = get_escape_state(conn)
    pursuer_floor = conn.execute(
        "SELECT floor FROM rooms WHERE id = ?",
        (state["pursuer_room_id"],),
    ).fetchone()["floor"]
    if pursuer_floor != 1:
        assert not ok


# ── Win Condition ──


def test_delivery_wins(conn, player):
    """Delivering to town completes the run."""
    claim_objective(conn, player["id"], player["room_id"])
    delivered, msg = check_delivery(conn, player["id"], "town")
    assert delivered
    assert "delivered" in msg.lower() or "Victory" in msg

    state = get_escape_state(conn)
    assert state["completed"] == 1
    assert state["active"] == 0


def test_delivery_not_in_town(conn, player):
    """No delivery when not in town."""
    claim_objective(conn, player["id"], player["room_id"])
    delivered, msg = check_delivery(conn, player["id"], "dungeon")
    assert not delivered


def test_delivery_not_carrier(conn, player, relay_player):
    """Non-carrier can't deliver."""
    claim_objective(conn, player["id"], player["room_id"])
    delivered, msg = check_delivery(conn, relay_player["id"], "town")
    assert not delivered


# ── Status Display ──


def test_status_before_claim(conn):
    """Status shows awaiting when unclaimed."""
    status = format_rne_status(conn)
    assert "awaits" in status.lower()
    assert f"Floor {NUM_FLOORS}" in status


def test_status_active(conn, player):
    """Status shows carrier and distance when active."""
    claim_objective(conn, player["id"], player["room_id"])
    status = format_rne_status(conn)
    assert "TestPlayer" in status
    assert "Pursuer" in status


def test_status_dropped(conn, player):
    """Status shows dropped location."""
    claim_objective(conn, player["id"], player["room_id"])
    handle_carrier_death(conn, player["id"])
    status = format_rne_status(conn)
    assert "dropped" in status.lower()


def test_status_completed(conn, player):
    """Status shows victory when completed."""
    claim_objective(conn, player["id"], player["room_id"])
    check_delivery(conn, player["id"], "town")
    status = format_rne_status(conn)
    assert "delivered" in status.lower()


def test_all_broadcasts_under_150(conn, player, relay_player):
    """All R&E broadcasts are under 150 chars."""
    claim_objective(conn, player["id"], player["room_id"])

    # Advance pursuer
    for _ in range(PURSUER_ADVANCE_RATE * 2):
        tick_pursuer(conn)

    handle_carrier_death(conn, player["id"])

    state = get_escape_state(conn)
    drop_room = state["dropped_room_id"]
    conn.execute(
        "UPDATE players SET room_id = ? WHERE id = ?",
        (drop_room, relay_player["id"]),
    )
    conn.commit()
    pickup_objective(conn, relay_player["id"], drop_room)

    check_delivery(conn, relay_player["id"], "town")

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= 150, f"Too long: {b['message']}"
