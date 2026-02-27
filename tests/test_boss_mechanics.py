"""Tests for floor boss mechanics across all floor tables."""

import json
import sqlite3

import pytest

from config import NUM_FLOORS
from src.db.database import get_db, init_schema
from src.generation.narrative import DummyBackend
from src.models.epoch import create_epoch
from src.models.player import create_player, get_or_create_account
from src.systems.endgame_htl import (
    apply_boss_mechanic,
    apply_boss_regen,
    handle_splitting,
    spawn_boss_add,
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
    return dict(p)


def _make_boss(conn, floor, mechanic, hp=100, hp_max=100):
    """Insert a floor boss with a specific mechanic and return it as dict."""
    room = conn.execute(
        "SELECT id FROM rooms WHERE floor = ? AND is_hub = 0 LIMIT 1",
        (floor,),
    ).fetchone()
    assert room, f"No room found on floor {floor}"

    cursor = conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier,
           is_floor_boss, mechanic)
           VALUES (?, ?, ?, ?, 10, 8, 6, 50, 10, 20, ?, 1, ?)""",
        (room["id"], f"Test Boss F{floor}", hp, hp_max,
         min(floor + 1, 5), mechanic),
    )
    conn.commit()
    boss_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM monsters WHERE id = ?", (boss_id,)).fetchone()
    return dict(row)


# ── Floor 1 Mechanics ──


def test_armored_above_50(conn, player):
    """Armored: half damage when above 50% HP."""
    boss = _make_boss(conn, 1, "armored", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 20)
    assert result["damage"] == 10  # Halved
    assert any("Armor" in m for m in result["messages"])


def test_armored_below_50(conn, player):
    """Armored: full damage when below 50% HP."""
    boss = _make_boss(conn, 1, "armored", hp=40, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 20)
    assert result["damage"] == 20  # Full damage


def test_enraged_above_50(conn, player):
    """Enraged: no bonus above 50% HP."""
    boss = _make_boss(conn, 1, "enraged", hp=60, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 20)
    assert result["extra_damage_to_player"] == 0
    assert result["damage"] == 20


def test_enraged_below_50(conn, player):
    """Enraged: extra damage + takes more below 50% HP."""
    boss = _make_boss(conn, 1, "enraged", hp=40, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 20)
    assert result["extra_damage_to_player"] > 0
    assert result["damage"] > 20  # 1.25x
    assert any("rages" in m or "rage" in m.lower() for m in result["messages"])


def test_regenerator(conn, player):
    """Regenerator: apply_boss_regen heals 10%."""
    boss = _make_boss(conn, 1, "regenerator", hp=50, hp_max=100)
    healed = apply_boss_regen(conn, boss["id"])
    assert healed > 0
    # Check new HP
    updated = conn.execute(
        "SELECT hp FROM monsters WHERE id = ?", (boss["id"],)
    ).fetchone()
    assert updated["hp"] == 60  # 50 + 10% of 100


def test_stalwart_blocks_flee(conn, player):
    """Stalwart: flee blocked on round 1."""
    boss = _make_boss(conn, 1, "stalwart", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 20, combat_round=1)
    assert result["flee_blocked"]
    assert any("blocks" in m.lower() for m in result["messages"])


def test_stalwart_allows_flee_later(conn, player):
    """Stalwart: flee allowed on round 2+."""
    boss = _make_boss(conn, 1, "stalwart", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 20, combat_round=2)
    assert not result["flee_blocked"]


# ── Floor 2 Mechanics ──


def test_warded_no_secrets(conn, player):
    """Warded: reduced damage when no secrets found on floor."""
    # Clear any discovered secrets on floor 2
    conn.execute(
        "UPDATE secrets SET discovered_by = NULL WHERE floor = 2"
    )
    conn.commit()

    boss = _make_boss(conn, 2, "warded", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 30)
    assert result["damage"] < 30  # Reduced (~67%)
    assert any("ward" in m.lower() or "secret" in m.lower() for m in result["messages"])


def test_warded_with_secrets(conn, player):
    """Warded: full damage when a secret on the floor is found."""
    # Discover a secret on floor 2
    secret = conn.execute(
        "SELECT id FROM secrets WHERE floor = 2 LIMIT 1"
    ).fetchone()
    if not secret:
        pytest.skip("No secrets on floor 2")

    conn.execute(
        "UPDATE secrets SET discovered_by = ? WHERE id = ?",
        (player["id"], secret["id"]),
    )
    conn.commit()

    boss = _make_boss(conn, 2, "warded", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 30)
    assert result["damage"] == 30  # Full damage


def test_phasing_even_day(conn, player):
    """Phasing: immune on even days."""
    conn.execute("UPDATE epoch SET day_number = 2 WHERE id = 1")
    conn.commit()

    boss = _make_boss(conn, 2, "phasing", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 20)
    assert result["boss_immune"]
    assert result["damage"] == 0
    assert any("phase" in m.lower() for m in result["messages"])


def test_phasing_odd_day(conn, player):
    """Phasing: vulnerable on odd days."""
    conn.execute("UPDATE epoch SET day_number = 3 WHERE id = 1")
    conn.commit()

    boss = _make_boss(conn, 2, "phasing", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 20)
    assert not result["boss_immune"]
    assert result["damage"] == 20


def test_draining(conn, player):
    """Draining: steals 10% of damage as HP from player."""
    boss = _make_boss(conn, 2, "draining", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 30)
    drain = max(1, 30 // 10)
    assert result["extra_damage_to_player"] >= drain
    assert any("drain" in m.lower() for m in result["messages"])


def test_splitting_at_50(conn, player):
    """Splitting: creates second boss at 50% HP."""
    boss = _make_boss(conn, 2, "splitting", hp=50, hp_max=100)
    msg = handle_splitting(conn, boss)
    assert msg is not None
    assert "split" in msg.lower() or "Split" in msg

    # Check split copy exists
    split = conn.execute(
        """SELECT * FROM monsters
           WHERE name LIKE '%Split%' AND is_floor_boss = 1""",
    ).fetchone()
    assert split is not None
    assert split["hp_max"] == 50  # Half HP


def test_splitting_only_once(conn, player):
    """Splitting: doesn't create duplicate splits."""
    boss = _make_boss(conn, 2, "splitting", hp=50, hp_max=100)
    handle_splitting(conn, boss)
    msg2 = handle_splitting(conn, boss)
    assert msg2 is None  # Already split


# ── Floor 3 Mechanics ──


def test_rotating_resistance(conn, player):
    """Rotating resistance: reduces damage based on player's highest stat."""
    # Make player POW-dominant for this test (warrior default is DEF-focused)
    conn.execute("UPDATE players SET pow = 10 WHERE id = ?", (player["id"],))
    conn.commit()
    player["pow"] = 10
    boss = _make_boss(conn, 3, "rotating_resistance", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 30)
    assert result["damage"] < 30  # Reduced
    assert any("resist" in m.lower() for m in result["messages"])


def test_retaliator(conn, player):
    """Retaliator: reflects 20% damage back."""
    boss = _make_boss(conn, 3, "retaliator", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 50)
    reflected = max(1, 50 // 5)
    assert result["extra_damage_to_player"] >= reflected
    assert any("reflect" in m.lower() for m in result["messages"])


def test_summoner_blocks_damage(conn, player):
    """Summoner: boss immune while add is alive."""
    boss = _make_boss(conn, 3, "summoner", hp=80, hp_max=100)

    # Spawn a minion
    spawn_boss_add(conn, boss)

    result = apply_boss_mechanic(conn, boss, player, 30)
    assert result["boss_immune"]
    assert result["damage"] == 0
    assert any("minion" in m.lower() for m in result["messages"])


def test_summoner_vulnerable_without_add(conn, player):
    """Summoner: boss vulnerable when no add alive."""
    boss = _make_boss(conn, 3, "summoner", hp=80, hp_max=100)
    # No minion spawned
    result = apply_boss_mechanic(conn, boss, player, 30)
    assert not result["boss_immune"]
    assert result["damage"] == 30


def test_cursed_passthrough(conn, player):
    """Cursed: no combat effect (tracked post-combat)."""
    boss = _make_boss(conn, 3, "cursed", hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 30)
    assert result["damage"] == 30
    assert result["extra_damage_to_player"] == 0


# ── Floor 4 (Warden) — Multi-mechanic ──


def test_warden_dual_mechanics(conn, player):
    """Warden rolls 2 mechanics — both apply."""
    mechanics = json.dumps(["armored", "retaliator"])
    boss = _make_boss(conn, 4, mechanics, hp=80, hp_max=100)
    result = apply_boss_mechanic(conn, boss, player, 40)
    # Armored (>50% HP) halves to 20, retaliator reflects 20//5=4
    assert result["damage"] == 20  # Halved by armored
    assert result["extra_damage_to_player"] >= 4  # Reflected


def test_boss_regen_standard(conn, player):
    """Standard Warden regen: 3% per engagement."""
    boss = _make_boss(conn, 4, "cursed", hp=90, hp_max=400)
    healed = apply_boss_regen(conn, boss["id"])
    assert healed > 0
    expected = max(1, int(400 * 0.03))
    assert healed == expected


def test_all_mechanic_messages_under_150(conn, player):
    """All mechanic messages fit in 150 chars."""
    mechanics = [
        "armored", "enraged", "stalwart", "warded", "phasing",
        "draining", "splitting", "rotating_resistance", "retaliator",
        "summoner", "cursed",
    ]
    for mech in mechanics:
        boss = _make_boss(conn, 2, mech, hp=40, hp_max=100)
        result = apply_boss_mechanic(conn, boss, player, 20, combat_round=1)
        for msg in result["messages"]:
            assert len(msg) <= 150, f"Mechanic {mech} message too long: {msg}"
