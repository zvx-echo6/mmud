"""Tests for economy system: shop, bank, healer, gold, death penalties."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import (
    DEATH_GOLD_LOSS_PERCENT,
    MSG_CHAR_LIMIT,
    SELL_PRICE_PERCENT,
    SHOP_PRICES,
)
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
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 5)"""
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
    # Monster for combat tests
    conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (2, 'Test Rat', 1, 1, 1, 0, 0, 10, 5, 5, 1)"""
    )
    # Items for shop — tier 1, 2, 3
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
           VALUES (3, 'Iron Blade', 'weapon', 2, 4, 0, 0)"""
    )
    conn.execute(
        """INSERT INTO items (id, name, slot, tier, pow_mod, def_mod, spd_mod)
           VALUES (4, 'Crystal Wand', 'weapon', 3, 6, 0, 1)"""
    )
    conn.commit()


def _register(engine: GameEngine, node_id: str = "!test1234"):
    engine.process_message(node_id, "Tester", "join")
    engine.process_message(node_id, "Tester", "Tester")
    engine.process_message(node_id, "Tester", "testpass")
    engine.process_message(node_id, "Tester", "w")
    return node_id


# ── Shop Tests ──────────────────────────────────────────────────────────────


def test_shop_shows_items():
    """SHOP lists available items with prices."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    resp = engine.process_message("!test1234", "Tester", "shop")
    assert "Rusty Sword" in resp
    assert "65g" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_shop_tier_unlock():
    """Shop only shows items unlocked by epoch day."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    # Day 5 → tier 1 and 2 available, tier 3 unlocks at day 5
    resp = engine.process_message("!test1234", "Tester", "shop")
    assert "Rusty Sword" in resp  # tier 1
    assert "Iron Blade" in resp   # tier 2
    assert "Crystal Wand" in resp  # tier 3, unlocks day 5


def test_buy_item():
    """BUY deducts gold and adds item to backpack."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.award_gold(conn, player["id"], 200)

    resp = engine.process_message("!test1234", "Tester", "buy rusty sword")
    assert "Bought" in resp
    assert len(resp) <= MSG_CHAR_LIMIT

    player = player_model.get_player(conn, player["id"])
    assert player["gold_carried"] == 200 - SHOP_PRICES[1]


def test_buy_not_enough_gold():
    """BUY fails when player doesn't have enough gold."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    resp = engine.process_message("!test1234", "Tester", "buy rusty sword")
    assert "Not enough gold" in resp


def test_sell_item():
    """SELL gives 50% of buy price and removes item."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.award_gold(conn, player["id"], 200)
    engine.process_message("!test1234", "Tester", "buy rusty sword")

    resp = engine.process_message("!test1234", "Tester", "sell rusty sword")
    sell_price = max(1, SHOP_PRICES[1] * SELL_PRICE_PERCENT // 100)
    assert f"{sell_price}g" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_shop_only_in_town():
    """SHOP/BUY/SELL only work in town."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)
    engine.process_message("!test1234", "Tester", "enter")

    for cmd in ["shop", "buy rusty sword", "sell rusty sword"]:
        resp = engine.process_message("!test1234", "Tester", cmd)
        assert "town" in resp.lower()


# ── Bank Tests ──────────────────────────────────────────────────────────────


def test_bank_shows_balance():
    """BANK shows carried and banked gold."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    resp = engine.process_message("!test1234", "Tester", "bank")
    assert "Bank" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_deposit_gold():
    """DEP deposits gold from carried to bank."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.award_gold(conn, player["id"], 100)

    resp = engine.process_message("!test1234", "Tester", "dep 50")
    assert "Deposited" in resp
    assert "50g" in resp
    assert len(resp) <= MSG_CHAR_LIMIT

    player = player_model.get_player(conn, player["id"])
    assert player["gold_carried"] == 50
    assert player["gold_banked"] == 50


def test_deposit_all():
    """DEP ALL deposits all carried gold."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.award_gold(conn, player["id"], 75)

    resp = engine.process_message("!test1234", "Tester", "dep all")
    assert "75g" in resp

    player = player_model.get_player(conn, player["id"])
    assert player["gold_carried"] == 0
    assert player["gold_banked"] == 75


def test_withdraw_gold():
    """WD withdraws gold from bank to carried."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.award_gold(conn, player["id"], 100)
    economy.deposit_gold(conn, player["id"], "100")

    resp = engine.process_message("!test1234", "Tester", "wd 40")
    assert "Withdrew" in resp
    assert "40g" in resp

    player = player_model.get_player(conn, player["id"])
    assert player["gold_carried"] == 40
    assert player["gold_banked"] == 60


def test_bank_only_in_town():
    """BANK/DEP/WD only work in town."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)
    engine.process_message("!test1234", "Tester", "enter")

    for cmd in ["bank", "dep 10", "wd 10"]:
        resp = engine.process_message("!test1234", "Tester", cmd)
        assert "town" in resp.lower()


# ── Healer Tests ────────────────────────────────────────────────────────────


def test_heal_prompt():
    """HEAL shows cost before confirming."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.update_state(conn, player["id"], hp=10)
    player_model.award_gold(conn, player["id"], 500)

    resp = engine.process_message("!test1234", "Tester", "heal")
    assert "Cost" in resp
    assert "HEAL Y" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_heal_confirm():
    """HEAL Y heals to full and deducts gold."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.update_state(conn, player["id"], hp=10)
    player_model.award_gold(conn, player["id"], 500)

    resp = engine.process_message("!test1234", "Tester", "heal y")
    assert "Healed" in resp
    assert len(resp) <= MSG_CHAR_LIMIT

    player = player_model.get_player(conn, player["id"])
    assert player["hp"] == player["hp_max"]


def test_heal_full_hp():
    """HEAL at full HP returns already full."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    resp = engine.process_message("!test1234", "Tester", "heal")
    assert "full HP" in resp


def test_heal_only_in_town():
    """HEAL only works in town."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)
    engine.process_message("!test1234", "Tester", "enter")

    resp = engine.process_message("!test1234", "Tester", "heal")
    assert "town" in resp.lower()


# ── Death Penalty Tests ─────────────────────────────────────────────────────


def test_death_loses_carried_gold_only():
    """Death loses DEATH_GOLD_LOSS_PERCENT% of carried gold, banked gold is safe."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.award_gold(conn, player["id"], 100)
    economy.deposit_gold(conn, player["id"], "50")

    # Player now has 50 carried, 50 banked
    losses = player_model.apply_death(conn, player["id"])
    expected_loss = 50 * DEATH_GOLD_LOSS_PERCENT // 100  # 20% of 50 = 10

    assert losses["gold_lost"] == expected_loss
    player = player_model.get_player(conn, player["id"])
    assert player["gold_banked"] == 50  # Banked gold untouched
    assert player["gold_carried"] == 50 - expected_loss


def test_marens_mercy_triggers():
    """Maren's Mercy: free heal to 50% if broke and below 50% HP."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    hp_max = player["hp_max"]
    # Set to broke and badly hurt (below 50% HP)
    player_model.update_state(conn, player["id"], hp=5)
    conn.execute("UPDATE players SET gold_carried = 0 WHERE id = ?", (player["id"],))
    conn.commit()

    resp = engine.process_message("!test1234", "Tester", "heal")
    assert "Maren sighs" in resp

    player = player_model.get_player(conn, player["id"])
    assert player["hp"] == hp_max // 2


def test_marens_mercy_not_if_has_gold():
    """Maren's Mercy does NOT trigger if player has gold."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.update_state(conn, player["id"], hp=5)
    player_model.award_gold(conn, player["id"], 10)

    resp = engine.process_message("!test1234", "Tester", "heal")
    assert "Maren sighs" not in resp
    assert "Cost" in resp


def test_marens_mercy_not_if_above_50_percent():
    """Maren's Mercy does NOT trigger if HP is at or above 50%."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    hp_max = player["hp_max"]
    # Set HP to exactly 50% and broke
    player_model.update_state(conn, player["id"], hp=hp_max // 2)
    conn.execute("UPDATE players SET gold_carried = 0 WHERE id = ?", (player["id"],))
    conn.commit()

    resp = engine.process_message("!test1234", "Tester", "heal")
    # Should NOT trigger mercy (hp is not BELOW 50%)
    assert "Maren sighs" not in resp


def test_death_respawn_at_60_percent():
    """Death respawns at 60% HP instead of 50%."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    hp_max = player["hp_max"]
    expected_hp = max(1, hp_max * 3 // 5)

    player_model.apply_death(conn, player["id"])
    player = player_model.get_player(conn, player["id"])
    assert player["hp"] == expected_hp


def test_gold_awarded_on_kill():
    """Killing a monster awards gold."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)
    engine.process_message("!test1234", "Tester", "enter")
    engine.process_message("!test1234", "Tester", "n")

    resp = engine.process_message("!test1234", "Tester", "fight")
    # Monster gives 5g (min=max=5)
    assert "5g" in resp

    player = player_model.get_player_by_session(conn, "!test1234")
    assert player["gold_carried"] >= 5


# ── All Responses Under 150 ────────────────────────────────────────────────


def test_economy_responses_under_150():
    """All economy-related responses fit under 150 chars."""
    conn = make_test_db()
    engine = GameEngine(conn)
    _register(engine)

    player = player_model.get_player_by_session(conn, "!test1234")
    player_model.award_gold(conn, player["id"], 1000)
    player_model.update_state(conn, player["id"], stat_points=2, hp=10)

    responses = [
        engine.process_message("!test1234", "Tester", "shop"),
        engine.process_message("!test1234", "Tester", "buy rusty sword"),
        engine.process_message("!test1234", "Tester", "sell rusty sword"),
        engine.process_message("!test1234", "Tester", "bank"),
        engine.process_message("!test1234", "Tester", "dep 100"),
        engine.process_message("!test1234", "Tester", "wd 50"),
        engine.process_message("!test1234", "Tester", "heal"),
        engine.process_message("!test1234", "Tester", "heal y"),
        engine.process_message("!test1234", "Tester", "train pow"),
        engine.process_message("!test1234", "Tester", "train"),
        engine.process_message("!test1234", "Tester", "stats"),
    ]

    for i, resp in enumerate(responses):
        assert resp is not None, f"Response {i} was None"
        assert len(resp) <= MSG_CHAR_LIMIT, (
            f"Response {i} exceeds {MSG_CHAR_LIMIT} chars ({len(resp)}): {resp}"
        )


if __name__ == "__main__":
    test_shop_shows_items()
    test_shop_tier_unlock()
    test_buy_item()
    test_buy_not_enough_gold()
    test_sell_item()
    test_shop_only_in_town()
    test_bank_shows_balance()
    test_deposit_gold()
    test_deposit_all()
    test_withdraw_gold()
    test_bank_only_in_town()
    test_heal_prompt()
    test_heal_confirm()
    test_heal_full_hp()
    test_heal_only_in_town()
    test_death_loses_carried_gold_only()
    test_gold_awarded_on_kill()
    test_economy_responses_under_150()
    print("All economy tests passed!")
