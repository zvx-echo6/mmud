"""Tests for the Breach Emergence mini-event (mini Raid Boss)."""

import sqlite3

import pytest

from config import EMERGENCE_HP_MAX, EMERGENCE_HP_MIN, MSG_CHAR_LIMIT
from src.db.database import get_db, init_schema
from src.models.player import create_player, get_or_create_account
from src.systems.breach_emergence import (
    apply_emergence_regen,
    check_emergence_complete,
    deal_emergence_damage,
    format_emergence_status,
    get_emergence_state,
    respawn_emergence_minions,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with an emergence breach epoch."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="raid_boss", breach_type="emergence")
    # Open the breach
    db.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    db.execute("UPDATE breach SET active = 1 WHERE id = 1")
    db.commit()
    return db


@pytest.fixture
def player(conn):
    """Player in a breach room."""
    acc = get_or_create_account(conn, "mesh_emr", "FighterPlayer")
    p = create_player(conn, acc, "FighterPlayer", "warrior")
    room = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = 2, room_id = ? WHERE id = ?",
        (room["id"], p["id"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


# ── Emergence State ──


def test_emergence_state_initialized(conn):
    """Emergence has HP set during generation."""
    state = get_emergence_state(conn)
    assert state is not None
    assert state["emergence_hp"] is not None
    assert state["emergence_hp"] >= EMERGENCE_HP_MIN
    assert state["emergence_hp"] <= EMERGENCE_HP_MAX


def test_emergence_hp_equals_max(conn):
    """HP starts equal to max."""
    state = get_emergence_state(conn)
    assert state["emergence_hp"] == state["emergence_hp_max"]


# ── Damage ──


def test_deal_damage(conn, player):
    """Damage reduces emergence HP."""
    state = get_emergence_state(conn)
    original_hp = state["emergence_hp"]

    new_hp, msg = deal_emergence_damage(conn, player["id"], 100)
    assert new_hp == original_hp - 100
    assert "100" in msg or str(original_hp - 100) in msg


def test_deal_lethal_damage(conn, player):
    """Lethal damage completes the emergence."""
    new_hp, msg = deal_emergence_damage(conn, player["id"], 99999)
    assert new_hp == 0
    assert "destroyed" in msg.lower() or "victory" in msg.lower()

    state = get_emergence_state(conn)
    assert state["completed"] == 1


def test_damage_floors_at_zero(conn, player):
    """HP can't go negative."""
    new_hp, _ = deal_emergence_damage(conn, player["id"], 99999)
    assert new_hp == 0


def test_damage_to_dead_creature(conn, player):
    """Can't damage already-dead creature."""
    deal_emergence_damage(conn, player["id"], 99999)
    new_hp, msg = deal_emergence_damage(conn, player["id"], 100)
    assert "defeated" in msg.lower() or "already" in msg.lower()


# ── Regen ──


def test_regen_heals(conn, player):
    """Regen heals the creature."""
    state = get_emergence_state(conn)
    # Damage it first
    deal_emergence_damage(conn, player["id"], state["emergence_hp"] // 2)

    healed = apply_emergence_regen(conn)
    assert healed > 0


def test_regen_does_not_overheal(conn, player):
    """Regen can't exceed max HP."""
    # Tiny damage
    deal_emergence_damage(conn, player["id"], 1)

    # Multiple regens
    for _ in range(10):
        apply_emergence_regen(conn)

    state = get_emergence_state(conn)
    assert state["emergence_hp"] <= state["emergence_hp_max"]


def test_regen_zero_at_full(conn):
    """No regen when at full HP."""
    healed = apply_emergence_regen(conn)
    assert healed == 0


def test_regen_zero_when_dead(conn, player):
    """No regen when dead."""
    deal_emergence_damage(conn, player["id"], 99999)
    healed = apply_emergence_regen(conn)
    assert healed == 0


# ── Minion Respawn ──


def test_minion_respawn(conn):
    """Minions spawn in breach rooms."""
    count = respawn_emergence_minions(conn)
    assert count >= 1

    minions = conn.execute(
        "SELECT * FROM monsters WHERE name LIKE '%Breach Spawn%'"
    ).fetchall()
    assert len(minions) >= 1


def test_minion_no_double_spawn(conn):
    """Minions don't double-spawn in rooms that already have them."""
    first = respawn_emergence_minions(conn)
    second = respawn_emergence_minions(conn)
    assert second == 0  # All rooms already have minions


# ── Completion ──


def test_check_complete_on_kill(conn, player):
    """Completion detected when HP reaches 0."""
    deal_emergence_damage(conn, player["id"], 99999)
    done, msg = check_emergence_complete(conn)
    # Already completed by deal_emergence_damage
    assert done


def test_check_not_complete_alive(conn):
    """Not complete when creature alive."""
    done, msg = check_emergence_complete(conn)
    assert not done


# ── Status ──


def test_status_active(conn):
    """Status shows HP when active."""
    status = format_emergence_status(conn)
    assert "HP:" in status


def test_status_complete(conn, player):
    """Status shows destroyed when complete."""
    deal_emergence_damage(conn, player["id"], 99999)
    status = format_emergence_status(conn)
    assert "destroyed" in status.lower()


# ── Broadcast Length ──


def test_all_emergence_broadcasts_under_150(conn, player):
    """All emergence broadcasts fit in 150 chars."""
    state = get_emergence_state(conn)
    # Damage to 25% for low-HP broadcast
    deal_emergence_damage(conn, player["id"], int(state["emergence_hp"] * 0.8))
    # Kill it
    deal_emergence_damage(conn, player["id"], 99999)
    # Minions
    respawn_emergence_minions(conn)

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT, f"Too long: {b['message']}"
