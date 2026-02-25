"""Tests for gear system: equip, unequip, drop, effective stats, loot drops."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import BACKPACK_SIZE, MSG_CHAR_LIMIT
from src.core.engine import GameEngine
from src.db.database import init_schema
from src.models import player as player_model
from src.systems import economy


def make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed_test_world(conn)
    return conn


def _seed_test_world(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Hub', 'Central hub. [n]', 'Hub. [n]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (2, 1, 'Arena', 'An arena. [s]', 'Arena. [s]', 0)"""
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (1, 2, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (2, 1, 's')"
    )
    conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (2, 'Test Rat', 1, 1, 1, 0, 0, 10, 5, 5, 1)"""
    )
    # Items
    conn.execute(
        """INSERT INTO items (id, name, slot, tier, pow_mod, def_mod, spd_mod)
           VALUES (1, 'Rusty Sword', 'weapon', 1, 2, 0, 0)"""
    )
    conn.execute(
        """INSERT INTO items (id, name, slot, tier, pow_mod, def_mod, spd_mod)
           VALUES (2, 'Leather Cap', 'armor', 1, 0, 2, 0)"""
    )
    conn.execute(
        """INSERT INTO items (id, name, slot, tier, pow_mod, def_mod, spd_mod)
           VALUES (3, 'Lucky Charm', 'trinket', 1, 0, 0, 2)"""
    )
    conn.execute(
        """INSERT INTO items (id, name, slot, tier, pow_mod, def_mod, spd_mod)
           VALUES (4, 'Iron Blade', 'weapon', 2, 4, 0, 0)"""
    )
    conn.commit()


def _register(engine: GameEngine, node_id: str = "!test1234"):
    engine.process_message(node_id, "Tester", "hello")
    engine.process_message(node_id, "Tester", "w")
    return node_id


def _give_item(conn: sqlite3.Connection, player_id: int, item_id: int):
    """Add an item to player's backpack."""
    conn.execute(
        "INSERT INTO inventory (player_id, item_id, equipped) VALUES (?, ?, 0)",
        (player_id, item_id),
    )
    conn.commit()


# ── Equip/Unequip Tests ────────────────────────────────────────────────────


def test_equip_item():
    """EQUIP moves item from backpack to slot."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    _give_item(conn, player["id"], 1)  # Rusty Sword

    resp = engine.process_message("!test1234", "Tester", "equip rusty sword")
    assert "Equipped" in resp
    assert "weapon" in resp.lower()
    assert len(resp) <= MSG_CHAR_LIMIT


def test_equip_replaces_existing():
    """Equipping a new item in same slot unequips the old one."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    _give_item(conn, player["id"], 1)  # Rusty Sword
    _give_item(conn, player["id"], 4)  # Iron Blade

    engine.process_message("!test1234", "Tester", "equip rusty sword")
    engine.process_message("!test1234", "Tester", "equip iron blade")

    items = economy.get_inventory(conn, player["id"])
    equipped = [i for i in items if i["equipped"]]
    backpack = [i for i in items if not i["equipped"]]

    assert len(equipped) == 1
    assert equipped[0]["name"] == "Iron Blade"
    assert len(backpack) == 1
    assert backpack[0]["name"] == "Rusty Sword"


def test_unequip_item():
    """UNEQUIP moves item from slot to backpack."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    _give_item(conn, player["id"], 1)
    engine.process_message("!test1234", "Tester", "equip rusty sword")

    resp = engine.process_message("!test1234", "Tester", "unequip weapon")
    assert "Unequipped" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_unequip_empty_slot():
    """UNEQUIP on empty slot shows error."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    resp = engine.process_message("!test1234", "Tester", "unequip weapon")
    assert "Nothing equipped" in resp


def test_drop_item():
    """DROP permanently removes item from inventory."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    _give_item(conn, player["id"], 1)

    resp = engine.process_message("!test1234", "Tester", "drop rusty sword")
    assert "Dropped" in resp
    assert len(resp) <= MSG_CHAR_LIMIT

    items = economy.get_inventory(conn, player["id"])
    assert len(items) == 0


# ── Effective Stats Tests ───────────────────────────────────────────────────


def test_effective_stats_no_gear():
    """Effective stats equal base stats with no gear."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    eff = economy.get_effective_stats(conn, player)
    assert eff["pow"] == player["pow"]
    assert eff["def"] == player["def"]
    assert eff["spd"] == player["spd"]


def test_effective_stats_with_gear():
    """Gear bonuses add to effective stats."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    _give_item(conn, player["id"], 1)  # Rusty Sword: +2 POW
    _give_item(conn, player["id"], 2)  # Leather Cap: +2 DEF
    _give_item(conn, player["id"], 3)  # Lucky Charm: +2 SPD

    economy.equip_item(conn, player["id"], "rusty sword")
    economy.equip_item(conn, player["id"], "leather cap")
    economy.equip_item(conn, player["id"], "lucky charm")

    eff = economy.get_effective_stats(conn, player)
    assert eff["pow"] == player["pow"] + 2
    assert eff["def"] == player["def"] + 2
    assert eff["spd"] == player["spd"] + 2


def test_stats_display_shows_effective():
    """STATS command shows effective stats (base + gear)."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    _give_item(conn, player["id"], 1)  # Rusty Sword: +2 POW
    economy.equip_item(conn, player["id"], "rusty sword")

    resp = engine.process_message("!test1234", "Tester", "stats")
    # Warrior base POW is 3, +2 from sword = 5
    assert "POW:5" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


# ── Inventory Tests ─────────────────────────────────────────────────────────


def test_inventory_shows_equipped_and_backpack():
    """INV shows both equipped items and backpack items."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    _give_item(conn, player["id"], 1)
    _give_item(conn, player["id"], 2)
    economy.equip_item(conn, player["id"], "rusty sword")

    resp = engine.process_message("!test1234", "Tester", "inv")
    assert "Eq:" in resp
    assert "Bag:" in resp
    assert "Rusty Sword" in resp
    assert "Leather Cap" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_inventory_empty():
    """INV shows empty message when no items."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    resp = engine.process_message("!test1234", "Tester", "inv")
    assert "empty" in resp.lower()


def test_backpack_limit():
    """Cannot exceed BACKPACK_SIZE items in backpack."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")

    # Fill backpack to capacity
    for _ in range(BACKPACK_SIZE):
        _give_item(conn, player["id"], 1)

    # Try to add one more
    ok, msg = economy.add_item_to_inventory(conn, player["id"], 1)
    assert not ok
    assert "full" in msg.lower()


# ── Loot Drop Tests ─────────────────────────────────────────────────────────


def test_loot_drop_function():
    """try_loot_drop returns a string or None."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")

    # Run it many times to exercise both paths
    results = set()
    for _ in range(100):
        result = economy.try_loot_drop(conn, player["id"], 1)
        results.add(type(result))
        if result is not None:
            # Clean up so we don't hit backpack limit
            conn.execute(
                "DELETE FROM inventory WHERE player_id = ? AND equipped = 0",
                (player["id"],),
            )
            conn.commit()

    # Should have gotten both None and str results
    assert type(None) in results
    assert str in results


# ── All Gear Responses Under 150 ───────────────────────────────────────────


def test_gear_responses_under_150():
    """All gear-related responses fit under 150 chars."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_mesh_id(conn, "!test1234")
    _give_item(conn, player["id"], 1)
    _give_item(conn, player["id"], 2)
    _give_item(conn, player["id"], 3)

    responses = [
        engine.process_message("!test1234", "Tester", "inv"),
        engine.process_message("!test1234", "Tester", "equip rusty sword"),
        engine.process_message("!test1234", "Tester", "equip leather cap"),
        engine.process_message("!test1234", "Tester", "equip lucky charm"),
        engine.process_message("!test1234", "Tester", "inv"),
        engine.process_message("!test1234", "Tester", "unequip weapon"),
        engine.process_message("!test1234", "Tester", "drop rusty sword"),
        engine.process_message("!test1234", "Tester", "stats"),
    ]

    for i, resp in enumerate(responses):
        assert resp is not None, f"Response {i} was None"
        assert len(resp) <= MSG_CHAR_LIMIT, (
            f"Response {i} exceeds {MSG_CHAR_LIMIT} chars ({len(resp)}): {resp}"
        )


if __name__ == "__main__":
    test_equip_item()
    test_equip_replaces_existing()
    test_unequip_item()
    test_unequip_empty_slot()
    test_drop_item()
    test_effective_stats_no_gear()
    test_effective_stats_with_gear()
    test_stats_display_shows_effective()
    test_inventory_shows_equipped_and_backpack()
    test_inventory_empty()
    test_backpack_limit()
    test_loot_drop_function()
    test_gear_responses_under_150()
    print("All gear tests passed!")
