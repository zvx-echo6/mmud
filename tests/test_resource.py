"""Tests for the class resource system (Focus/Tricks/Mana)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import random
import sqlite3

from config import (
    CHARGE_RESOURCE_COST,
    DUNGEON_ACTIONS_PER_DAY,
    RESOURCE_MAX,
    RESOURCE_NAMES,
    RESOURCE_REGEN_DAYTICK,
    RESOURCE_REGEN_REST,
    RESOURCE_REGEN_TOWN,
    SOCIAL_ACTIONS_PER_DAY,
    SPECIAL_ACTIONS_PER_DAY,
)
from src.core.actions import handle_action
from src.core.world import return_to_town
from src.db.database import init_schema
from src.models.player import (
    create_player,
    get_or_create_account,
    get_player,
    restore_resource,
    use_resource,
)
from src.systems.daytick import run_day_tick


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1)"""
    )
    conn.commit()
    return conn


def _make_player(conn, cls="warrior", name="TestHero", mesh_id="!test1"):
    acc = get_or_create_account(conn, mesh_id, name)
    return create_player(conn, acc, name, cls)


# ── Player creation ──


def test_player_created_with_resource():
    conn = _make_db()
    p = _make_player(conn, "warrior")
    assert p["resource"] == RESOURCE_MAX
    assert p["resource_max"] == RESOURCE_MAX


def test_caster_created_correctly():
    conn = _make_db()
    p = _make_player(conn, "caster", "Merlin", "!caster1")
    assert p["class"] == "caster"
    assert p["hp"] == 35
    assert p["pow"] == 4
    assert p["def"] == 1
    assert p["spd"] == 2
    assert p["resource"] == 5


def test_rogue_created_correctly():
    conn = _make_db()
    p = _make_player(conn, "rogue", "Shadow", "!rogue1")
    assert p["class"] == "rogue"
    assert p["hp"] == 40
    assert p["pow"] == 3
    assert p["def"] == 3
    assert p["spd"] == 3


def test_warrior_created_correctly():
    conn = _make_db()
    p = _make_player(conn, "warrior", "Tank", "!war99")
    assert p["class"] == "warrior"
    assert p["hp"] == 60
    assert p["pow"] == 2
    assert p["def"] == 4
    assert p["spd"] == 1


# ── use_resource / restore_resource ──


def test_use_resource_success():
    conn = _make_db()
    p = _make_player(conn)
    assert use_resource(conn, p["id"], 2) is True
    updated = get_player(conn, p["id"])
    assert updated["resource"] == 3


def test_use_resource_insufficient():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET resource = 1 WHERE id = ?", (p["id"],))
    conn.commit()
    assert use_resource(conn, p["id"], 2) is False
    updated = get_player(conn, p["id"])
    assert updated["resource"] == 1  # Unchanged


def test_restore_resource_capped():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET resource = 4 WHERE id = ?", (p["id"],))
    conn.commit()
    restore_resource(conn, p["id"], 5)
    updated = get_player(conn, p["id"])
    assert updated["resource"] == 5  # Capped at max


# ── Charge (Warrior) ──


def test_charge_warrior_only():
    conn = _make_db()
    p = _make_player(conn, "rogue", "NotWarrior", "!rogue2")
    result = handle_action(conn, dict(p), "charge", [])
    assert "Only Warriors" in result


def test_charge_town_rejected():
    conn = _make_db()
    p = _make_player(conn)
    result = handle_action(conn, dict(p), "charge", [])
    assert "town" in result.lower()


def test_charge_no_focus():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET resource = 0, state = 'combat', combat_monster_id = 1 WHERE id = ?", (p["id"],))
    conn.commit()
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "charge", [])
    assert "Focus" in result


# ── Sneak (Rogue) ──


def test_sneak_rogue_only():
    conn = _make_db()
    p = _make_player(conn, "warrior", "NotRogue", "!war2")
    result = handle_action(conn, dict(p), "sneak", [])
    assert "Only Rogues" in result


def test_sneak_town_rejected():
    conn = _make_db()
    p = _make_player(conn, "rogue", "Sneaker", "!rogue3")
    result = handle_action(conn, dict(p), "sneak", [])
    assert "town" in result.lower()


# ── Cast (Caster) ──


def test_cast_caster_only():
    conn = _make_db()
    p = _make_player(conn, "warrior", "NotCaster", "!war3")
    result = handle_action(conn, dict(p), "cast", [])
    assert "Only Casters" in result


def test_cast_town_rejected():
    conn = _make_db()
    p = _make_player(conn, "caster", "Mage", "!caster2")
    result = handle_action(conn, dict(p), "cast", [])
    assert "town" in result.lower()


def test_cast_no_mana():
    conn = _make_db()
    p = _make_player(conn, "caster", "EmptyMage", "!caster3")
    conn.execute("UPDATE players SET resource = 0, state = 'combat', combat_monster_id = 1 WHERE id = ?", (p["id"],))
    conn.commit()
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    assert "Mana" in result


# ── Rest ──


def test_rest_restores_resource():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET resource = 3 WHERE id = ?", (p["id"],))
    conn.commit()
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "rest", [])
    assert "Focus" in result
    updated = get_player(conn, p["id"])
    assert updated["resource"] == 3 + RESOURCE_REGEN_REST


def test_rest_uses_special_action():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET resource = 3 WHERE id = ?", (p["id"],))
    conn.commit()
    p = get_player(conn, p["id"])
    handle_action(conn, dict(p), "rest", [])
    updated = get_player(conn, p["id"])
    assert updated["special_actions_remaining"] == 0
    # Try again — should fail
    result = handle_action(conn, dict(updated), "rest", [])
    assert "Already rested" in result


def test_rest_full_resource():
    conn = _make_db()
    p = _make_player(conn)
    result = handle_action(conn, dict(p), "rest", [])
    assert "full" in result.lower()


def test_rest_dungeon_rejected():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET state = 'dungeon', resource = 3 WHERE id = ?", (p["id"],))
    conn.commit()
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "rest", [])
    assert "town" in result.lower()


# ── Resource regen on day tick ──


def test_resource_regen_daytick():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET resource = 2 WHERE id = ?", (p["id"],))
    conn.commit()
    run_day_tick(conn)
    updated = get_player(conn, p["id"])
    assert updated["resource"] == 2 + RESOURCE_REGEN_DAYTICK


def test_resource_regen_daytick_capped():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET resource = 4 WHERE id = ?", (p["id"],))
    conn.commit()
    run_day_tick(conn)
    updated = get_player(conn, p["id"])
    assert updated["resource"] == RESOURCE_MAX  # 4 + 2 = 6, capped to 5


# ── Resource regen on return to town ──


def test_resource_regen_town():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET resource = 3, state = 'dungeon', floor = 1 WHERE id = ?", (p["id"],))
    conn.commit()
    return_to_town(conn, p["id"])
    updated = get_player(conn, p["id"])
    assert updated["resource"] == 3 + RESOURCE_REGEN_TOWN


# ── Stats display ──


def test_stats_shows_resource():
    conn = _make_db()
    p = _make_player(conn)
    # Need items table for economy.get_effective_stats
    result = handle_action(conn, dict(p), "stats", [])
    assert "Focus:5/5" in result


def test_stats_shows_rogue_resource():
    conn = _make_db()
    p = _make_player(conn, "rogue", "Thief", "!rogue4")
    result = handle_action(conn, dict(p), "stats", [])
    assert "Tricks:5/5" in result


def test_stats_shows_caster_resource():
    conn = _make_db()
    p = _make_player(conn, "caster", "Wizard", "!caster4")
    result = handle_action(conn, dict(p), "stats", [])
    assert "Mana:5/5" in result


# ── Death halves resource ──


def test_death_halves_resource():
    conn = _make_db()
    p = _make_player(conn)
    from src.models.player import apply_death

    conn.execute(
        "UPDATE players SET state = 'combat', floor = 1, resource = 5 WHERE id = ?",
        (p["id"],),
    )
    conn.commit()
    apply_death(conn, p["id"])
    updated = get_player(conn, p["id"])
    assert updated["resource"] == 2  # 5 // 2 = 2


# ── Help text shows class ability ──


def test_help_combat_warrior():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute("UPDATE players SET state = 'combat' WHERE id = ?", (p["id"],))
    conn.commit()
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "help", [])
    assert "CH(arge)" in result


def test_help_combat_rogue():
    conn = _make_db()
    p = _make_player(conn, "rogue", "Shade", "!rogue5")
    conn.execute("UPDATE players SET state = 'combat' WHERE id = ?", (p["id"],))
    conn.commit()
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "help", [])
    assert "SN(eak)" in result


def test_help_combat_caster():
    conn = _make_db()
    p = _make_player(conn, "caster", "Arcane", "!caster5")
    conn.execute("UPDATE players SET state = 'combat' WHERE id = ?", (p["id"],))
    conn.commit()
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "help", [])
    assert "CA(st)" in result


def test_help_town_shows_rest():
    conn = _make_db()
    p = _make_player(conn)
    result = handle_action(conn, dict(p), "help", [])
    assert "REST" in result
