"""Tests for breach-endgame interaction."""

import json
import sqlite3

import pytest

from config import MSG_CHAR_LIMIT
from src.db.database import get_db, init_schema
from src.models.player import create_player, get_or_create_account
from src.systems.breach_endgame import (
    apply_breach_completion_reward,
    get_htl_bonus_from_breach,
    has_raid_damage_buff,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn_raid():
    """In-memory DB with raid boss endgame + emergence breach."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="raid_boss", breach_type="emergence")
    db.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    db.execute("UPDATE breach SET active = 1 WHERE id = 1")
    db.commit()
    return db


@pytest.fixture
def conn_htl():
    """In-memory DB with HtL endgame + incursion breach."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="hold_the_line", breach_type="incursion")
    db.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    db.execute("UPDATE breach SET active = 1 WHERE id = 1")
    db.commit()
    return db


@pytest.fixture
def conn_rne():
    """In-memory DB with R&E endgame + heist breach."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="retrieve_and_escape", breach_type="heist")
    db.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    db.execute("UPDATE breach SET active = 1 WHERE id = 1")
    db.commit()
    return db


@pytest.fixture
def player_raid(conn_raid):
    """Player in raid boss epoch."""
    acc = get_or_create_account(conn_raid, "mesh_raid", "RaidPlayer")
    p = create_player(conn_raid, acc, "RaidPlayer", "warrior")
    conn_raid.execute(
        "UPDATE players SET state = 'dungeon', floor = 2 WHERE id = ?", (p["id"],)
    )
    conn_raid.commit()
    return dict(conn_raid.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


@pytest.fixture
def player_htl(conn_htl):
    """Player in HtL epoch."""
    acc = get_or_create_account(conn_htl, "mesh_htl", "HtlPlayer")
    p = create_player(conn_htl, acc, "HtlPlayer", "caster")
    conn_htl.execute(
        "UPDATE players SET state = 'dungeon', floor = 2 WHERE id = ?", (p["id"],)
    )
    conn_htl.commit()
    return dict(conn_htl.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone())


# ── Raid Boss: +20% Damage Buff ──


def test_raid_buff_on_completion(conn_raid, player_raid):
    """Breach completion grants +20% damage buff item to players."""
    msg = apply_breach_completion_reward(conn_raid)
    assert msg is not None
    assert "20%" in msg

    assert has_raid_damage_buff(conn_raid, player_raid["id"])


def test_raid_buff_item_created(conn_raid, player_raid):
    """Breach Resonance Shard item is created."""
    apply_breach_completion_reward(conn_raid)

    item = conn_raid.execute(
        "SELECT * FROM items WHERE name = 'Breach Resonance Shard'"
    ).fetchone()
    assert item is not None
    assert "raid_damage_boost" in item["special"]


def test_raid_buff_in_inventory(conn_raid, player_raid):
    """Buff item appears in player inventory."""
    apply_breach_completion_reward(conn_raid)

    inv = conn_raid.execute(
        """SELECT i.*, it.name FROM inventory i
           JOIN items it ON i.item_id = it.id
           WHERE i.player_id = ? AND it.name = 'Breach Resonance Shard'""",
        (player_raid["id"],),
    ).fetchone()
    assert inv is not None


def test_no_raid_buff_without_completion(conn_raid, player_raid):
    """No buff without calling completion."""
    assert not has_raid_damage_buff(conn_raid, player_raid["id"])


# ── Hold the Line: Bonus Territory ──


def test_htl_bonus_territory(conn_htl, player_htl):
    """Cleared breach rooms count as bonus territory."""
    # Clear some breach rooms
    rooms = conn_htl.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 LIMIT 3"
    ).fetchall()
    for r in rooms:
        conn_htl.execute(
            "UPDATE rooms SET htl_cleared = 1 WHERE id = ?", (r["id"],)
        )
    conn_htl.commit()

    bonus = get_htl_bonus_from_breach(conn_htl)
    assert bonus["bonus_rooms"] == len(rooms)
    assert 2 in bonus["floors"]
    assert 3 in bonus["floors"]


def test_htl_no_bonus_no_cleared(conn_htl):
    """No bonus rooms when nothing cleared."""
    bonus = get_htl_bonus_from_breach(conn_htl)
    assert bonus["bonus_rooms"] == 0


def test_htl_completion_reward(conn_htl, player_htl):
    """HtL breach completion broadcasts bonus territory."""
    # Clear some breach rooms first
    conn_htl.execute("UPDATE rooms SET htl_cleared = 1 WHERE is_breach = 1")
    conn_htl.commit()

    msg = apply_breach_completion_reward(conn_htl)
    assert msg is not None
    assert "bonus" in msg.lower() or "Floors 2-3" in msg


# ── Retrieve & Escape: Shortcut ──


def test_rne_shortcut_reward(conn_rne):
    """R&E breach completion notes shortcut availability."""
    msg = apply_breach_completion_reward(conn_rne)
    assert msg is not None
    assert "shortcut" in msg.lower() or "path" in msg.lower()


# ── Broadcast Length ──


def test_raid_reward_broadcast_under_150(conn_raid, player_raid):
    """Raid reward broadcast fits in 150 chars."""
    conn_raid.execute("DELETE FROM broadcasts")
    conn_raid.commit()
    apply_breach_completion_reward(conn_raid)

    broadcasts = conn_raid.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT, f"Too long: {b['message']}"


def test_htl_reward_broadcast_under_150(conn_htl, player_htl):
    """HtL reward broadcast fits in 150 chars."""
    conn_htl.execute("UPDATE rooms SET htl_cleared = 1 WHERE is_breach = 1")
    conn_htl.execute("DELETE FROM broadcasts")
    conn_htl.commit()
    apply_breach_completion_reward(conn_htl)

    broadcasts = conn_htl.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT, f"Too long: {b['message']}"


def test_rne_reward_broadcast_under_150(conn_rne):
    """R&E reward broadcast fits in 150 chars."""
    conn_rne.execute("DELETE FROM broadcasts")
    conn_rne.commit()
    apply_breach_completion_reward(conn_rne)

    broadcasts = conn_rne.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= MSG_CHAR_LIMIT, f"Too long: {b['message']}"
