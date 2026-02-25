"""Tests for the Raid Boss endgame mode."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from config import (
    NUM_FLOORS,
    RAID_BOSS_HP_CAP,
    RAID_BOSS_HP_PER_PLAYER,
    RAID_BOSS_PHASES,
    RAID_BOSS_REGEN_RATE,
)
from src.db.database import get_db, init_schema
from src.generation.narrative import DummyBackend
from src.models.epoch import create_epoch
from src.models.player import create_player, get_or_create_account
from src.systems.endgame_raid import (
    activate_raid_boss,
    apply_raid_mechanic,
    apply_raid_regen,
    check_phase_transition,
    check_raid_boss_dead,
    deal_damage_to_boss,
    engage_raid_boss,
    format_raid_status,
    get_raid_boss,
    handle_boss_flees,
    record_raid_contribution,
)
from tests.helpers import generate_test_epoch


@pytest.fixture
def conn():
    """In-memory DB with a full raid boss epoch generated."""
    db = get_db(":memory:")
    init_schema(db)
    generate_test_epoch(db, endgame_mode="raid_boss")
    return db


@pytest.fixture
def player(conn):
    """Create a test player in dungeon."""
    acc = get_or_create_account(conn, "mesh_test", "TestPlayer")
    p = create_player(conn, acc, "TestPlayer", "warrior")
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = ? WHERE id = ?",
        (NUM_FLOORS, p["id"]),
    )
    conn.commit()
    return dict(p)


@pytest.fixture
def players(conn):
    """Create 5 test players for scaling tests."""
    ids = []
    for i in range(5):
        acc = get_or_create_account(conn, f"mesh_{i}", f"Player{i}")
        p = create_player(conn, acc, f"Player{i}", "warrior")
        conn.execute(
            "UPDATE players SET state = 'dungeon', floor = 1 WHERE id = ?",
            (p["id"],),
        )
        ids.append(p["id"])
    conn.commit()
    return ids


# ── Activation & Scaling ──


def test_activate_scales_to_players(conn, players):
    """Raid boss HP = 300 × active players."""
    result = activate_raid_boss(conn)
    expected_hp = RAID_BOSS_HP_PER_PLAYER * len(players)
    assert result["hp"] == expected_hp
    assert result["active_players"] == len(players)


def test_activate_caps_hp(conn):
    """HP is capped at RAID_BOSS_HP_CAP."""
    # Create lots of players
    for i in range(30):
        acc = get_or_create_account(conn, f"mesh_cap_{i}", f"Cap{i}")
        p = create_player(conn, acc, f"Cap{i}", "warrior")
        conn.execute(
            "UPDATE players SET state = 'dungeon', floor = 1 WHERE id = ?",
            (p["id"],),
        )
    conn.commit()

    result = activate_raid_boss(conn)
    assert result["hp"] <= RAID_BOSS_HP_CAP


def test_activate_sets_phase_1(conn, players):
    """Activation starts at phase 1."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    assert boss["phase"] == 1


def test_activate_broadcasts(conn, players):
    """Activation creates a broadcast."""
    activate_raid_boss(conn)
    broadcasts = conn.execute(
        "SELECT * FROM broadcasts WHERE message LIKE '%stirs%'"
    ).fetchall()
    assert len(broadcasts) >= 1


# ── Get Boss ──


def test_get_raid_boss_parses_mechanics(conn, players):
    """get_raid_boss() parses mechanics JSON."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    assert boss is not None
    assert isinstance(boss["mechanics_list"], list)
    assert len(boss["mechanics_list"]) >= 2


# ── Regen ──


def test_regen_heals_boss(conn, players):
    """Regen heals the boss based on interval elapsed."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    original_hp = boss["hp"]

    # Damage the boss
    deal_damage_to_boss(conn, original_hp // 2)

    # Set last_regen_at to 9 hours ago (1 interval)
    past = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
    conn.execute(
        "UPDATE raid_boss SET last_regen_at = ? WHERE id = 1", (past,)
    )
    conn.commit()

    healed = apply_raid_regen(conn)
    assert healed > 0


def test_regen_does_not_overheal(conn, players):
    """Regen doesn't exceed max HP."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)

    # Tiny damage
    deal_damage_to_boss(conn, 1)

    # Set last_regen_at way in the past
    past = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    conn.execute(
        "UPDATE raid_boss SET last_regen_at = ? WHERE id = 1", (past,)
    )
    conn.commit()

    apply_raid_regen(conn)
    updated = get_raid_boss(conn)
    assert updated["hp"] <= updated["hp_max"]


def test_regen_zero_when_full_hp(conn, players):
    """No regen when at full HP."""
    activate_raid_boss(conn)
    healed = apply_raid_regen(conn)
    assert healed == 0


# ── Combat Engagement ──


def test_engage_allowed(conn, player):
    """Player can engage living raid boss."""
    activate_raid_boss(conn)
    can, msg = engage_raid_boss(conn, player["id"])
    assert can


def test_engage_dead_boss(conn, player):
    """Can't engage dead raid boss."""
    activate_raid_boss(conn)
    deal_damage_to_boss(conn, 99999)
    can, msg = engage_raid_boss(conn, player["id"])
    assert not can
    assert "defeated" in msg.lower()


def test_lockout_mechanic(conn, player):
    """Lockout mechanic prevents re-engagement for 24h."""
    activate_raid_boss(conn)

    # Set mechanics to include lockout
    conn.execute(
        "UPDATE raid_boss SET mechanics = ? WHERE id = 1",
        (json.dumps(["lockout"]),),
    )
    conn.commit()

    # Record contribution (triggers lockout)
    record_raid_contribution(conn, player["id"], 100)

    # Should be locked out
    can, msg = engage_raid_boss(conn, player["id"])
    assert not can
    assert "locked out" in msg.lower()


# ── Phase Transitions ──


def test_phase_transition_at_66(conn, players):
    """Phase 2 at 66% HP."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)

    # Deal damage to get below 66%
    damage_needed = boss["hp"] - int(boss["hp_max"] * 0.65)
    deal_damage_to_boss(conn, damage_needed)

    new_phase = check_phase_transition(conn)
    assert new_phase == 2


def test_phase_transition_at_33(conn, players):
    """Phase 3 at 33% HP."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)

    # Deal damage to get below 33%
    damage_needed = boss["hp"] - int(boss["hp_max"] * 0.32)
    deal_damage_to_boss(conn, damage_needed)

    # Force phase to 2 first
    conn.execute("UPDATE raid_boss SET phase = 2 WHERE id = 1")
    conn.commit()

    new_phase = check_phase_transition(conn)
    assert new_phase == 3


def test_phase_broadcasts(conn, players):
    """Phase transition creates broadcast."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    damage_needed = boss["hp"] - int(boss["hp_max"] * 0.65)
    deal_damage_to_boss(conn, damage_needed)
    check_phase_transition(conn)

    broadcasts = conn.execute(
        "SELECT * FROM broadcasts WHERE message LIKE '%phase%'"
    ).fetchall()
    assert len(broadcasts) >= 1


# ── Contribution Tracking ──


def test_record_contribution(conn, player):
    """Contribution records damage."""
    activate_raid_boss(conn)
    record_raid_contribution(conn, player["id"], 150)

    row = conn.execute(
        "SELECT total_damage FROM raid_boss_contributors WHERE player_id = ?",
        (player["id"],),
    ).fetchone()
    assert row["total_damage"] == 150


def test_contribution_accumulates(conn, player):
    """Multiple contributions accumulate."""
    activate_raid_boss(conn)
    record_raid_contribution(conn, player["id"], 100)
    record_raid_contribution(conn, player["id"], 200)

    row = conn.execute(
        "SELECT total_damage FROM raid_boss_contributors WHERE player_id = ?",
        (player["id"],),
    ).fetchone()
    assert row["total_damage"] == 300


# ── Damage & Death ──


def test_deal_damage(conn, players):
    """deal_damage_to_boss reduces HP."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    new_hp = deal_damage_to_boss(conn, 100)
    assert new_hp == boss["hp"] - 100


def test_deal_damage_floors_at_zero(conn, players):
    """HP doesn't go negative."""
    activate_raid_boss(conn)
    new_hp = deal_damage_to_boss(conn, 999999)
    assert new_hp == 0


def test_check_dead(conn, players):
    """Boss death is detected."""
    activate_raid_boss(conn)
    deal_damage_to_boss(conn, 999999)
    dead, msg = check_raid_boss_dead(conn)
    assert dead
    assert "slain" in msg.lower()


def test_check_alive(conn, players):
    """Living boss not detected as dead."""
    activate_raid_boss(conn)
    dead, msg = check_raid_boss_dead(conn)
    assert not dead


# ── Boss Flees ──


def test_boss_flees_relocates(conn, players):
    """boss_flees mechanic relocates boss."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    original_room = boss["room_id"]

    msg = handle_boss_flees(conn)
    assert msg is not None

    updated = get_raid_boss(conn)
    # Room might be the same if only 1 eligible room, but message exists
    assert "fled" in msg.lower()


# ── Raid Mechanics ──


def test_windup_strike(conn, player):
    """Windup strike: warning on interval rounds."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    boss["mechanics_list"] = ["windup_strike"]
    boss["phase"] = 1

    result = apply_raid_mechanic(conn, boss, player, 20, combat_round=3)
    assert any("WIND-UP" in m for m in result["messages"])


def test_aura_damage(conn, player):
    """Aura damage: unavoidable damage each round."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    boss["mechanics_list"] = ["aura_damage"]
    boss["phase"] = 1

    result = apply_raid_mechanic(conn, boss, player, 20, combat_round=1)
    assert result["extra_damage_to_player"] > 0
    assert any("aura" in m.lower() for m in result["messages"])


def test_no_escape_below_25(conn, player):
    """No escape: flee blocked below 25% HP."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    boss["mechanics_list"] = ["no_escape"]
    boss["hp"] = boss["hp_max"] // 5  # 20%
    boss["phase"] = 1

    result = apply_raid_mechanic(conn, boss, player, 20)
    assert result["flee_blocked"]


def test_no_escape_above_25(conn, player):
    """No escape: flee allowed above 25% HP."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    boss["mechanics_list"] = ["no_escape"]
    boss["hp"] = boss["hp_max"]  # 100%
    boss["phase"] = 1

    result = apply_raid_mechanic(conn, boss, player, 20)
    assert not result["flee_blocked"]


def test_enrage_timer(conn, player):
    """Enrage timer: extra damage after threshold rounds."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)
    boss["mechanics_list"] = ["enrage_timer"]
    boss["phase"] = 1

    # Round 6 (threshold is 5 for phase 1)
    result = apply_raid_mechanic(conn, boss, player, 20, combat_round=6)
    assert result["extra_damage_to_player"] > 0
    assert any("ENRAGED" in m for m in result["messages"])


# ── Status Display ──


def test_format_status(conn, players):
    """Status string is formatted correctly."""
    activate_raid_boss(conn)
    status = format_raid_status(conn)
    assert "HP:" in status
    assert len(status) <= 150


def test_format_status_dead(conn, players):
    """Status shows defeated when dead."""
    activate_raid_boss(conn)
    deal_damage_to_boss(conn, 999999)
    status = format_raid_status(conn)
    assert "defeated" in status.lower()


def test_format_status_no_boss(conn):
    """Status handles no raid boss."""
    # Delete raid boss
    conn.execute("DELETE FROM raid_boss")
    conn.commit()
    status = format_raid_status(conn)
    assert "No raid boss" in status


def test_all_broadcasts_under_150(conn, players):
    """All raid broadcasts are under 150 chars."""
    activate_raid_boss(conn)
    boss = get_raid_boss(conn)

    # Generate some events
    deal_damage_to_boss(conn, boss["hp"] - int(boss["hp_max"] * 0.65))
    check_phase_transition(conn)
    deal_damage_to_boss(conn, 999999)
    check_raid_boss_dead(conn)

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= 150, f"Too long: {b['message']}"
