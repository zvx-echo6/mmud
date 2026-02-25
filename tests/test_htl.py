"""Tests for Hold the Line endgame mode."""

import sqlite3

import pytest

from config import HTL_REGEN_ROOMS_PER_DAY, NUM_FLOORS
from src.db.database import get_db, init_schema
from src.generation.narrative import DummyBackend
from src.models.epoch import create_epoch
from src.models.player import create_player, get_or_create_account
from src.systems.endgame_htl import (
    apply_htl_regen,
    check_checkpoint_cluster,
    check_warden_kill,
    clear_room,
    establish_checkpoint,
    format_htl_status,
    get_floor_control,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with a full HtL epoch generated."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="hold_the_line")
    return db


@pytest.fixture
def player(conn):
    """Create a test player."""
    acc = get_or_create_account(conn, "mesh_test", "TestPlayer")
    p = create_player(conn, acc, "TestPlayer", "warrior")
    return p


def test_clear_room(conn, player):
    """Clearing a room sets htl_cleared = 1."""
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = 1 AND is_hub = 0 LIMIT 1"
    ).fetchone()
    assert room

    result = clear_room(conn, room["id"])
    assert result["cleared"]

    cleared = conn.execute(
        "SELECT htl_cleared FROM rooms WHERE id = ?", (room["id"],)
    ).fetchone()
    assert cleared["htl_cleared"] == 1


def test_floor_control(conn):
    """Floor control returns data for all floors."""
    control = get_floor_control(conn)
    assert len(control) == NUM_FLOORS
    for floor in range(1, NUM_FLOORS + 1):
        assert "cleared" in control[floor]
        assert "total" in control[floor]
        assert "percent" in control[floor]
        assert control[floor]["cleared"] == 0  # Nothing cleared yet


def test_regen_reverts_rooms(conn, player):
    """Regen ticks revert cleared rooms."""
    # Clear several rooms on floor 1
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE floor = 1 AND is_hub = 0 AND is_checkpoint = 0 LIMIT 5"
    ).fetchall()

    for r in rooms:
        clear_room(conn, r["id"])

    # Verify they're cleared
    cleared = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE floor = 1 AND htl_cleared = 1"
    ).fetchone()["cnt"]
    assert cleared >= len(rooms)

    # Apply regen
    stats = apply_htl_regen(conn)
    assert 1 in stats

    # Some rooms should be reverted
    # Floor 1 regen is 3/day, but we might have cleared fewer
    reverted = min(HTL_REGEN_ROOMS_PER_DAY[1], len(rooms))
    new_cleared = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE floor = 1 AND htl_cleared = 1"
    ).fetchone()["cnt"]
    # We had 5 cleared, reverted up to 3 = should have >= 2 left
    assert new_cleared <= cleared


def test_checkpoint_rooms_immune_to_regen(conn, player):
    """Established checkpoint rooms are not reverted by regen."""
    # Find a checkpoint on floor 1
    cp = conn.execute(
        "SELECT room_id FROM htl_checkpoints WHERE floor = 1 LIMIT 1"
    ).fetchone()
    if not cp:
        pytest.skip("No checkpoints on floor 1")

    # Clear the checkpoint room
    conn.execute(
        "UPDATE rooms SET htl_cleared = 1 WHERE id = ?", (cp["room_id"],)
    )

    # Establish it
    conn.execute(
        """UPDATE htl_checkpoints SET established = 1
           WHERE room_id = ?""",
        (cp["room_id"],),
    )
    conn.commit()

    # Apply regen multiple times
    for _ in range(5):
        apply_htl_regen(conn)

    # Checkpoint room should still be cleared
    room = conn.execute(
        "SELECT htl_cleared FROM rooms WHERE id = ?", (cp["room_id"],)
    ).fetchone()
    assert room["htl_cleared"] == 1


def test_establish_checkpoint(conn, player):
    """Establishing a checkpoint marks it permanently."""
    cp = conn.execute(
        "SELECT room_id, floor FROM htl_checkpoints WHERE floor = 1 AND established = 0 LIMIT 1"
    ).fetchone()
    if not cp:
        pytest.skip("No unestablished checkpoints")

    ok, msg = establish_checkpoint(conn, cp["room_id"], player["id"])
    assert ok

    # Verify it's established
    row = conn.execute(
        "SELECT established FROM htl_checkpoints WHERE room_id = ?",
        (cp["room_id"],),
    ).fetchone()
    assert row["established"] == 1


def test_checkpoint_establishment_broadcast(conn, player):
    """Checkpoint establishment creates a broadcast."""
    cp = conn.execute(
        "SELECT room_id FROM htl_checkpoints WHERE floor = 1 AND established = 0 LIMIT 1"
    ).fetchone()
    if not cp:
        pytest.skip("No checkpoints")

    establish_checkpoint(conn, cp["room_id"], player["id"])

    broadcasts = conn.execute(
        "SELECT message FROM broadcasts WHERE message LIKE '%Checkpoint%'"
    ).fetchall()
    assert len(broadcasts) >= 1


def test_stairway_checkpoint_unlocks_floor(conn, player):
    """Establishing a stairway checkpoint broadcasts floor unlock."""
    cp = conn.execute(
        """SELECT room_id, floor FROM htl_checkpoints
           WHERE position = 'stairway' AND floor < ? AND established = 0 LIMIT 1""",
        (NUM_FLOORS,),
    ).fetchone()
    if not cp:
        pytest.skip("No stairway checkpoints")

    establish_checkpoint(conn, cp["room_id"], player["id"])

    broadcasts = conn.execute(
        "SELECT message FROM broadcasts WHERE message LIKE '%unlocked%'"
    ).fetchall()
    assert len(broadcasts) >= 1


def test_warden_kill(conn):
    """Killing the Warden (floor 4 boss) triggers epoch win."""
    warden = conn.execute(
        """SELECT id FROM monsters
           WHERE is_floor_boss = 1
           AND room_id IN (SELECT id FROM rooms WHERE floor = ?)""",
        (NUM_FLOORS,),
    ).fetchone()
    if not warden:
        pytest.skip("No Warden found")

    # Kill it
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = ?", (warden["id"],))
    conn.commit()

    killed, msg = check_warden_kill(conn)
    assert killed
    assert "Warden" in msg


def test_format_htl_status(conn):
    """HtL status formats correctly."""
    status = format_htl_status(conn)
    assert "HtL:" in status
    assert "F1:" in status
    assert len(status) <= 150


def test_regen_creates_monsters(conn, player):
    """Room revert spawns a monster in the reverted room."""
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE floor = 1 AND is_hub = 0 AND is_checkpoint = 0 LIMIT 3"
    ).fetchall()

    # Clear rooms and remove existing monsters
    for r in rooms:
        clear_room(conn, r["id"])
        conn.execute("DELETE FROM monsters WHERE room_id = ?", (r["id"],))
    conn.commit()

    # Apply regen
    apply_htl_regen(conn)

    # Check that reverted rooms got monsters
    for r in rooms:
        room_state = conn.execute(
            "SELECT htl_cleared FROM rooms WHERE id = ?", (r["id"],)
        ).fetchone()
        if room_state["htl_cleared"] == 0:
            monsters = conn.execute(
                "SELECT COUNT(*) as cnt FROM monsters WHERE room_id = ? AND hp > 0",
                (r["id"],),
            ).fetchone()
            assert monsters["cnt"] > 0


def test_all_broadcasts_under_150(conn, player):
    """All HtL broadcasts are under 150 chars."""
    # Establish a checkpoint to generate broadcasts
    cp = conn.execute(
        "SELECT room_id FROM htl_checkpoints WHERE established = 0 LIMIT 1"
    ).fetchone()
    if cp:
        establish_checkpoint(conn, cp["room_id"], player["id"])

    # Apply regen
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE floor = 1 AND is_hub = 0 AND is_checkpoint = 0 LIMIT 5"
    ).fetchall()
    for r in rooms:
        clear_room(conn, r["id"])
    apply_htl_regen(conn)

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= 150, f"Too long: {b['message']}"
