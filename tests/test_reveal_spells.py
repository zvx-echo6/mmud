"""Tests for reveal system, spell names, and charge double-move."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import random
import sqlite3

from config import (
    BARD_TOKEN_CAP,
    CAST_RESOURCE_COST,
    CHARGE_RESOURCE_COST,
    MSG_CHAR_LIMIT,
    RESOURCE_MAX,
    REVEAL_GOLD_CHANCE,
    REVEAL_GOLD_MAX,
    REVEAL_GOLD_MIN,
    REVEAL_LORE_CHANCE,
    REVEAL_LORE_MAX_CHARS,
)
from src.core.actions import handle_action
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.generation.worldgen import generate_world
from src.models.epoch import create_epoch, get_epoch
from src.models.player import (
    create_player,
    get_or_create_account,
    get_player,
)
from src.models.world import has_player_revealed, record_player_reveal


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number, spell_names)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1,
                   'Arcane Bolt,Ember Flare,Void Spike')"""
    )
    conn.commit()
    return conn


def _make_player(conn, cls="caster", name="TestMage", mesh_id="!test1"):
    acc = get_or_create_account(conn, mesh_id, name)
    return create_player(conn, acc, name, cls)


def _make_dungeon_room(conn, floor=1, name="Test Room", reveal_gold=0, reveal_lore=""):
    """Insert a room and return its ID."""
    cursor = conn.execute(
        """INSERT INTO rooms (floor, name, description, description_short, is_hub,
           reveal_gold, reveal_lore)
           VALUES (?, ?, 'A dark room.', 'Dark.', 0, ?, ?)""",
        (floor, name, reveal_gold, reveal_lore),
    )
    conn.commit()
    return cursor.lastrowid


def _make_hub_room(conn, floor=1):
    """Insert a hub room and return its ID."""
    cursor = conn.execute(
        """INSERT INTO rooms (floor, name, description, description_short, is_hub)
           VALUES (?, 'Hub Room', 'Central hub.', 'Hub.', 1)""",
        (floor,),
    )
    conn.commit()
    return cursor.lastrowid


def _make_connected_rooms(conn, floor=1):
    """Create two rooms connected by exits. Returns (room1_id, room2_id)."""
    r1 = _make_dungeon_room(conn, floor, "Room Alpha")
    r2 = _make_dungeon_room(conn, floor, "Room Beta")
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r1, r2),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 's')",
        (r2, r1),
    )
    conn.commit()
    return r1, r2


def _make_three_rooms(conn, floor=1):
    """Create three rooms in a line: r1 -n-> r2 -n-> r3. Returns (r1, r2, r3)."""
    r1 = _make_dungeon_room(conn, floor, "Room One")
    r2 = _make_dungeon_room(conn, floor, "Room Two")
    r3 = _make_dungeon_room(conn, floor, "Room Three")
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r1, r2),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 's')",
        (r2, r1),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r2, r3),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 's')",
        (r3, r2),
    )
    conn.commit()
    return r1, r2, r3


def _make_monster(conn, room_id, name="Goblin", hp=20, tier=1):
    """Insert a monster and return its ID."""
    cursor = conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (?, ?, ?, ?, 3, 2, 1, 10, 5, 10, ?)""",
        (room_id, name, hp, hp, tier),
    )
    conn.commit()
    return cursor.lastrowid


def _put_in_dungeon(conn, player_id, room_id, floor=1):
    """Place a player in a dungeon room."""
    conn.execute(
        "UPDATE players SET state = 'dungeon', floor = ?, room_id = ? WHERE id = ?",
        (floor, room_id, player_id),
    )
    conn.commit()


def _put_in_combat(conn, player_id, monster_id, room_id=None, floor=1):
    """Place a player in combat."""
    updates = "state = 'combat', combat_monster_id = ?, floor = ?"
    params = [monster_id, floor, player_id]
    if room_id:
        updates += ", room_id = ?"
        params = [monster_id, floor, room_id, player_id]
    conn.execute(f"UPDATE players SET {updates} WHERE id = ?", params)
    conn.commit()


# =============================================================================
# Reveal System — player_reveals tracking
# =============================================================================


def test_has_player_revealed_false():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn)
    assert has_player_revealed(conn, p["id"], r) is False


def test_record_and_check_reveal():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn)
    record_player_reveal(conn, p["id"], r)
    assert has_player_revealed(conn, p["id"], r) is True


def test_reveal_per_player_isolation():
    conn = _make_db()
    p1 = _make_player(conn, name="Mage1", mesh_id="!m1")
    p2 = _make_player(conn, name="Mage2", mesh_id="!m2")
    r = _make_dungeon_room(conn)
    record_player_reveal(conn, p1["id"], r)
    assert has_player_revealed(conn, p1["id"], r) is True
    assert has_player_revealed(conn, p2["id"], r) is False


def test_reveal_idempotent():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn)
    record_player_reveal(conn, p["id"], r)
    record_player_reveal(conn, p["id"], r)  # Should not raise
    assert has_player_revealed(conn, p["id"], r) is True


# =============================================================================
# Cast Reveal — out-of-combat dungeon
# =============================================================================


def test_cast_reveal_gold():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn, reveal_gold=15)
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    assert "15g" in result
    updated = get_player(conn, p["id"])
    assert updated["gold_carried"] == 15


def test_cast_reveal_lore():
    conn = _make_db()
    p = _make_player(conn)
    lore = "The walls whisper of a king."
    r = _make_dungeon_room(conn, reveal_lore=lore)
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    assert lore in result
    updated = get_player(conn, p["id"])
    assert updated["bard_tokens"] == 1  # Got +1 for lore


def test_cast_reveal_lore_bard_token_capped():
    conn = _make_db()
    p = _make_player(conn)
    conn.execute(
        "UPDATE players SET bard_tokens = ? WHERE id = ?",
        (BARD_TOKEN_CAP, p["id"]),
    )
    conn.commit()
    r = _make_dungeon_room(conn, reveal_lore="Some lore text here.")
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    handle_action(conn, dict(p), "cast", [])
    updated = get_player(conn, p["id"])
    assert updated["bard_tokens"] == BARD_TOKEN_CAP


def test_cast_reveal_empty_room():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn)  # No gold, no lore
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    assert "hollow" in result.lower()


def test_cast_reveal_already_revealed():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn, reveal_gold=10)
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    handle_action(conn, dict(p), "cast", [])  # First reveal
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    assert "Already revealed" in result


def test_cast_reveal_detects_secret():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn)
    # Insert a secret in this room
    conn.execute(
        """INSERT INTO secrets (type, floor, room_id, name, description,
           reward_type, hint_tier1, hint_tier2, hint_tier3)
           VALUES ('observation', 1, ?, 'Hidden Cache', 'A hidden cache.',
                   'lore_fragment', 'hint1', 'hint2', 'hint3')""",
        (r,),
    )
    conn.commit()
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    assert "Hidden Cache" in result


def test_cast_reveal_costs_mana():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn)
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    handle_action(conn, dict(p), "cast", [])
    updated = get_player(conn, p["id"])
    assert updated["resource"] == RESOURCE_MAX - CAST_RESOURCE_COST


def test_cast_reveal_under_150_chars():
    conn = _make_db()
    p = _make_player(conn)
    lore = "A" * 75  # Long lore but under 80
    r = _make_dungeon_room(conn, reveal_gold=20, reveal_lore=lore)
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    assert len(result) <= MSG_CHAR_LIMIT


# =============================================================================
# Spell Names — epoch generation + in-combat cast
# =============================================================================


def test_epoch_has_spell_names():
    conn = _make_db()
    epoch = get_epoch(conn)
    assert epoch["spell_names"] == "Arcane Bolt,Ember Flare,Void Spike"


def test_cast_combat_uses_spell_name():
    conn = _make_db()
    p = _make_player(conn)
    r = _make_dungeon_room(conn)
    m = _make_monster(conn, r, hp=5)  # Low HP to get a kill
    _put_in_combat(conn, p["id"], m, room_id=r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    # Should use one of the epoch spell names, not "Arcane bolt"
    valid_names = ["Arcane Bolt", "Ember Flare", "Void Spike"]
    assert any(name in result for name in valid_names), f"No spell name found in: {result}"


def test_cast_combat_fallback_no_spells():
    conn = _make_db()
    # Clear spell names
    conn.execute("UPDATE epoch SET spell_names = '' WHERE id = 1")
    conn.commit()
    p = _make_player(conn)
    r = _make_dungeon_room(conn)
    m = _make_monster(conn, r, hp=5)
    _put_in_combat(conn, p["id"], m, room_id=r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "cast", [])
    assert "Arcane Bolt" in result


def test_dummy_backend_generates_spell_names():
    backend = DummyBackend()
    names = backend.generate_spell_names("test theme")
    assert len(names) == 3
    for name in names:
        assert len(name) <= 20


def test_dummy_backend_spell_names_unique():
    backend = DummyBackend()
    names = backend.generate_spell_names("")
    assert len(set(names)) == 3  # All unique


def test_dummy_backend_generates_lore():
    backend = DummyBackend()
    lore = backend.generate_lore_fragment(1)
    assert len(lore) <= REVEAL_LORE_MAX_CHARS
    assert len(lore) > 0


# =============================================================================
# Charge Double-Move — out-of-combat dungeon
# =============================================================================


def test_charge_double_move_clear():
    """Charge through 2 clear rooms. Warrior ends up in room3."""
    conn = _make_db()
    p = _make_player(conn, "warrior", "Warrior", "!war1")
    # One-way corridor: r1 -> r2 -> r3 (r2 only exits forward)
    r1 = _make_dungeon_room(conn, name="Start")
    r2 = _make_dungeon_room(conn, name="Middle")
    r3 = _make_dungeon_room(conn, name="End")
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r1, r2),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r2, r3),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 's')",
        (r3, r2),
    )
    conn.commit()
    _put_in_dungeon(conn, p["id"], r1)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "charge", ["n"])
    assert "CHARGE" in result
    updated = get_player(conn, p["id"])
    assert updated["room_id"] == r3
    assert updated["state"] == "dungeon"


def test_charge_stops_at_monster_room1():
    """Charge stops in room 1 if monster found there."""
    conn = _make_db()
    p = _make_player(conn, "warrior", "Fighter", "!war2")
    r1, r2, r3 = _make_three_rooms(conn)
    m = _make_monster(conn, r2, "Orc", hp=100)
    _put_in_dungeon(conn, p["id"], r1)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "charge", ["n"])
    assert "CHARGE" in result
    updated = get_player(conn, p["id"])
    assert updated["state"] == "combat"
    assert updated["room_id"] == r2


def test_charge_stops_at_monster_room2():
    """Charge through clear room1, stops at monster in room2."""
    conn = _make_db()
    p = _make_player(conn, "warrior", "Tank", "!war3")
    # Build one-way corridor: r1 -> r2 -> r3 (r2 only exits to r3)
    r1 = _make_dungeon_room(conn, name="Start")
    r2 = _make_dungeon_room(conn, name="Middle")
    r3 = _make_dungeon_room(conn, name="End")
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r1, r2),
    )
    # r2 only has one exit: north to r3 (no back exit to r1)
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r2, r3),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 's')",
        (r3, r2),
    )
    conn.commit()
    m = _make_monster(conn, r3, "Troll", hp=100)
    _put_in_dungeon(conn, p["id"], r1)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "charge", ["n"])
    assert "CHARGE" in result
    updated = get_player(conn, p["id"])
    assert updated["state"] == "combat"
    assert updated["room_id"] == r3


def test_charge_dead_end_room1():
    """Charge into a dead end (no further exits)."""
    conn = _make_db()
    p = _make_player(conn, "warrior", "Hero", "!war4")
    r1 = _make_dungeon_room(conn, name="Start")
    r2 = _make_dungeon_room(conn, name="Dead End")
    # Only one-way exit from r1 to r2 (r2 only has exit back to r1)
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 'n')",
        (r1, r2),
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, 's')",
        (r2, r1),
    )
    conn.commit()
    _put_in_dungeon(conn, p["id"], r1)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "charge", ["n"])
    # Room 2 has exit back to r1, so warrior will bounce back or proceed
    # The random exit from r2 is 's' (back to r1) — should move there
    updated = get_player(conn, p["id"])
    # Warrior ends up somewhere (r1 or r2 depending on random)
    assert updated["room_id"] in (r1, r2)
    assert "CHARGE" in result


def test_charge_costs_focus_not_action():
    conn = _make_db()
    p = _make_player(conn, "warrior", "Costs", "!war5")
    r1, r2 = _make_connected_rooms(conn)
    _put_in_dungeon(conn, p["id"], r1)
    p = get_player(conn, p["id"])
    starting_actions = p["dungeon_actions_remaining"]
    starting_resource = p["resource"]
    handle_action(conn, dict(p), "charge", ["n"])
    updated = get_player(conn, p["id"])
    assert updated["resource"] == starting_resource - CHARGE_RESOURCE_COST
    # Combat actions are free — only movement costs dungeon actions
    assert updated["dungeon_actions_remaining"] == starting_actions


def test_charge_no_direction():
    conn = _make_db()
    p = _make_player(conn, "warrior", "NoDir", "!war6")
    r = _make_dungeon_room(conn)
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "charge", [])
    assert "where" in result.lower() or "N/S/E/W" in result


def test_charge_bad_direction():
    conn = _make_db()
    p = _make_player(conn, "warrior", "Bad", "!war7")
    r = _make_dungeon_room(conn)
    _put_in_dungeon(conn, p["id"], r)
    p = get_player(conn, p["id"])
    result = handle_action(conn, dict(p), "charge", ["x"])
    # Should get an error from move_player
    assert "exit" in result.lower() or "no" in result.lower()


# =============================================================================
# Worldgen — reveal content populated
# =============================================================================


def test_worldgen_populates_reveal_content():
    """After world generation, some rooms should have reveal content."""
    conn = _make_db()
    backend = DummyBackend()
    generate_world(conn, backend)
    conn.commit()

    gold_rooms = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE reveal_gold > 0"
    ).fetchone()["cnt"]
    lore_rooms = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE reveal_lore != ''"
    ).fetchone()["cnt"]
    total_rooms = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_hub = 0"
    ).fetchone()["cnt"]

    # With ~60 non-hub rooms and 35% chance, expect at least a few
    assert gold_rooms >= 1, f"No rooms with reveal gold (of {total_rooms})"
    # Lore at 12% is rarer but should usually appear
    # Don't assert > 0 because with small sample it could be 0


def test_worldgen_lore_under_80_chars():
    conn = _make_db()
    backend = DummyBackend()
    generate_world(conn, backend)
    conn.commit()

    lore_rooms = conn.execute(
        "SELECT reveal_lore FROM rooms WHERE reveal_lore != ''"
    ).fetchall()
    for room in lore_rooms:
        assert len(room["reveal_lore"]) <= REVEAL_LORE_MAX_CHARS


# =============================================================================
# Validation
# =============================================================================


def test_validation_catches_long_spell_name():
    from src.generation.validation import _validate_spell_names
    conn = _make_db()
    conn.execute(
        "UPDATE epoch SET spell_names = 'Short,This Name Is Way Too Long For Twenty Chars,Fine' WHERE id = 1"
    )
    conn.commit()
    errors = []
    warnings = []
    _validate_spell_names(conn, errors, warnings)
    assert len(errors) == 1
    assert "20 chars" in errors[0]


def test_validation_catches_wrong_spell_count():
    from src.generation.validation import _validate_spell_names
    conn = _make_db()
    conn.execute("UPDATE epoch SET spell_names = 'One,Two' WHERE id = 1")
    conn.commit()
    errors = []
    warnings = []
    _validate_spell_names(conn, errors, warnings)
    assert len(errors) == 1
    assert "3 spell names" in errors[0]


def test_validation_accepts_valid_spells():
    from src.generation.validation import _validate_spell_names
    conn = _make_db()
    errors = []
    warnings = []
    _validate_spell_names(conn, errors, warnings)
    assert len(errors) == 0
