"""
Economy system for MMUD.
Shop, bank, heal, loot drops, gear management, effective stats.
All town actions are free — never cost dungeon actions.
"""

import math
import random
import sqlite3
from typing import Optional

from config import (
    BACKPACK_SIZE,
    GEAR_SLOTS,
    HEAL_COST_PER_HP,
    HEAL_LEVEL_MULT,
    LOOT_DROP_CHANCE,
    SELL_PRICE_PERCENT,
    SHOP_PRICES,
    SHOP_TIER_UNLOCK_DAY,
)


# ── Effective Stats ──────────────────────────────────────────────────────────


def get_effective_stats(conn: sqlite3.Connection, player: dict) -> dict:
    """Calculate effective stats: base + gear bonuses.

    Returns:
        Dict with keys: pow, def, spd (each = base + gear mods).
    """
    base_pow = player["pow"]
    base_def = player["def"]
    base_spd = player["spd"]

    rows = conn.execute(
        """SELECT it.pow_mod, it.def_mod, it.spd_mod
           FROM inventory inv JOIN items it ON inv.item_id = it.id
           WHERE inv.player_id = ? AND inv.equipped = 1""",
        (player["id"],),
    ).fetchall()

    gear_pow = sum(r["pow_mod"] for r in rows)
    gear_def = sum(r["def_mod"] for r in rows)
    gear_spd = sum(r["spd_mod"] for r in rows)

    return {
        "pow": base_pow + gear_pow,
        "def": base_def + gear_def,
        "spd": base_spd + gear_spd,
    }


# ── Inventory / Gear ─────────────────────────────────────────────────────────


def get_backpack_count(conn: sqlite3.Connection, player_id: int) -> int:
    """Count non-equipped items in player's backpack."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM inventory WHERE player_id = ? AND equipped = 0",
        (player_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def get_inventory(conn: sqlite3.Connection, player_id: int) -> list[dict]:
    """Get all inventory items with item details."""
    rows = conn.execute(
        """SELECT inv.id as inv_id, inv.equipped, inv.slot,
                  it.id as item_id, it.name, it.slot as item_slot, it.tier,
                  it.pow_mod, it.def_mod, it.spd_mod
           FROM inventory inv JOIN items it ON inv.item_id = it.id
           WHERE inv.player_id = ?
           ORDER BY inv.equipped DESC, it.tier DESC""",
        (player_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def equip_item(
    conn: sqlite3.Connection, player_id: int, item_name: str
) -> tuple[bool, str]:
    """Equip an item from backpack to its appropriate slot.

    Returns:
        (success, message)
    """
    # Find the item in backpack (not equipped)
    row = conn.execute(
        """SELECT inv.id as inv_id, it.slot as item_slot, it.name
           FROM inventory inv JOIN items it ON inv.item_id = it.id
           WHERE inv.player_id = ? AND inv.equipped = 0
           AND LOWER(it.name) = LOWER(?)
           LIMIT 1""",
        (player_id, item_name),
    ).fetchone()

    if not row:
        return False, "Item not in backpack."

    slot = row["item_slot"]
    if slot not in GEAR_SLOTS:
        return False, "That item can't be equipped."

    # Unequip current item in that slot (move to backpack)
    conn.execute(
        """UPDATE inventory SET equipped = 0, slot = NULL
           WHERE player_id = ? AND equipped = 1 AND slot = ?""",
        (player_id, slot),
    )

    # Equip the new item
    conn.execute(
        "UPDATE inventory SET equipped = 1, slot = ? WHERE id = ?",
        (slot, row["inv_id"]),
    )
    conn.commit()
    return True, f"Equipped {row['name']} [{slot}]."


def unequip_slot(
    conn: sqlite3.Connection, player_id: int, slot: str
) -> tuple[bool, str]:
    """Unequip item from a slot to backpack.

    Returns:
        (success, message)
    """
    slot = slot.lower()
    if slot not in GEAR_SLOTS:
        return False, f"Invalid slot. Use: {', '.join(GEAR_SLOTS)}"

    # Check backpack space
    if get_backpack_count(conn, player_id) >= BACKPACK_SIZE:
        return False, "Backpack full! DROP something first."

    row = conn.execute(
        """SELECT inv.id as inv_id, it.name
           FROM inventory inv JOIN items it ON inv.item_id = it.id
           WHERE inv.player_id = ? AND inv.equipped = 1 AND inv.slot = ?
           LIMIT 1""",
        (player_id, slot),
    ).fetchone()

    if not row:
        return False, f"Nothing equipped in {slot}."

    conn.execute(
        "UPDATE inventory SET equipped = 0, slot = NULL WHERE id = ?",
        (row["inv_id"],),
    )
    conn.commit()
    return True, f"Unequipped {row['name']}."


def drop_item(
    conn: sqlite3.Connection, player_id: int, item_name: str
) -> tuple[bool, str]:
    """Drop an item from inventory permanently.

    Returns:
        (success, message)
    """
    row = conn.execute(
        """SELECT inv.id as inv_id, it.name, inv.equipped
           FROM inventory inv JOIN items it ON inv.item_id = it.id
           WHERE inv.player_id = ? AND LOWER(it.name) = LOWER(?)
           LIMIT 1""",
        (player_id, item_name),
    ).fetchone()

    if not row:
        return False, "Item not found in inventory."

    conn.execute("DELETE FROM inventory WHERE id = ?", (row["inv_id"],))
    conn.commit()
    return True, f"Dropped {row['name']}."


def add_item_to_inventory(
    conn: sqlite3.Connection, player_id: int, item_id: int
) -> tuple[bool, str]:
    """Add an item to player's backpack.

    Returns:
        (success, message) — fails if backpack is full.
    """
    if get_backpack_count(conn, player_id) >= BACKPACK_SIZE:
        item = conn.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()
        name = item["name"] if item else "item"
        return False, f"Backpack full! {name} left behind."

    item = conn.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.execute(
        "INSERT INTO inventory (player_id, item_id, equipped) VALUES (?, ?, 0)",
        (player_id, item_id),
    )
    conn.commit()
    name = item["name"] if item else "item"
    return True, f"Found {name}!"


# ── Shop ─────────────────────────────────────────────────────────────────────


def get_shop_items(
    conn: sqlite3.Connection, epoch_day: int
) -> list[dict]:
    """Get items available in shop for the current epoch day.

    Returns list of item dicts with added 'price' field.
    """
    # Determine max tier available
    max_tier = 0
    for tier, unlock_day in SHOP_TIER_UNLOCK_DAY.items():
        if epoch_day >= unlock_day:
            max_tier = max(max_tier, tier)

    if max_tier == 0:
        return []

    rows = conn.execute(
        """SELECT * FROM items WHERE tier <= ? AND slot IN ('weapon', 'armor', 'trinket')
           ORDER BY tier, slot, name""",
        (max_tier,),
    ).fetchall()

    result = []
    for r in rows:
        item = dict(r)
        item["price"] = SHOP_PRICES.get(item["tier"], 99999)
        result.append(item)
    return result


def buy_item(
    conn: sqlite3.Connection, player_id: int, item_name: str, epoch_day: int
) -> tuple[bool, str]:
    """Buy an item from the shop.

    Returns:
        (success, message)
    """
    # Find the item in shop
    available = get_shop_items(conn, epoch_day)
    item = None
    for i in available:
        if i["name"].lower() == item_name.lower():
            item = i
            break

    if not item:
        return False, "Item not available in shop."

    price = item["price"]

    # Check gold
    player = conn.execute(
        "SELECT gold_carried FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    if not player or player["gold_carried"] < price:
        return False, f"Not enough gold. Need {price}g."

    # Check backpack space
    if get_backpack_count(conn, player_id) >= BACKPACK_SIZE:
        return False, "Backpack full! DROP something first."

    # Deduct gold and add item
    conn.execute(
        "UPDATE players SET gold_carried = gold_carried - ? WHERE id = ?",
        (price, player_id),
    )
    conn.execute(
        "INSERT INTO inventory (player_id, item_id, equipped) VALUES (?, ?, 0)",
        (player_id, item["id"]),
    )
    conn.commit()
    return True, f"Bought {item['name']} for {price}g."


def sell_item(
    conn: sqlite3.Connection, player_id: int, item_name: str
) -> tuple[bool, str]:
    """Sell an item from inventory for 50% of buy price.

    Returns:
        (success, message)
    """
    row = conn.execute(
        """SELECT inv.id as inv_id, it.name, it.tier
           FROM inventory inv JOIN items it ON inv.item_id = it.id
           WHERE inv.player_id = ? AND LOWER(it.name) = LOWER(?)
           LIMIT 1""",
        (player_id, item_name),
    ).fetchone()

    if not row:
        return False, "Item not in inventory."

    buy_price = SHOP_PRICES.get(row["tier"], 100)
    sell_price = max(1, buy_price * SELL_PRICE_PERCENT // 100)

    conn.execute("DELETE FROM inventory WHERE id = ?", (row["inv_id"],))
    conn.execute(
        "UPDATE players SET gold_carried = gold_carried + ? WHERE id = ?",
        (sell_price, player_id),
    )
    conn.commit()
    return True, f"Sold {row['name']} for {sell_price}g."


# ── Bank ─────────────────────────────────────────────────────────────────────


def deposit_gold(
    conn: sqlite3.Connection, player_id: int, amount_str: str
) -> tuple[bool, str]:
    """Deposit gold to bank.

    Returns:
        (success, message)
    """
    player = conn.execute(
        "SELECT gold_carried, gold_banked FROM players WHERE id = ?",
        (player_id,),
    ).fetchone()
    if not player:
        return False, "Player not found."

    if amount_str.lower() == "all":
        amount = player["gold_carried"]
    else:
        try:
            amount = int(amount_str)
        except ValueError:
            return False, "Invalid amount. Use a number or 'all'."

    if amount <= 0:
        return False, "No gold to deposit."
    if amount > player["gold_carried"]:
        return False, f"Only have {player['gold_carried']}g carried."

    conn.execute(
        """UPDATE players SET
           gold_carried = gold_carried - ?,
           gold_banked = gold_banked + ?
           WHERE id = ?""",
        (amount, amount, player_id),
    )
    conn.commit()
    new_banked = player["gold_banked"] + amount
    return True, f"Deposited {amount}g. Bank: {new_banked}g."


def withdraw_gold(
    conn: sqlite3.Connection, player_id: int, amount_str: str
) -> tuple[bool, str]:
    """Withdraw gold from bank.

    Returns:
        (success, message)
    """
    player = conn.execute(
        "SELECT gold_carried, gold_banked FROM players WHERE id = ?",
        (player_id,),
    ).fetchone()
    if not player:
        return False, "Player not found."

    if amount_str.lower() == "all":
        amount = player["gold_banked"]
    else:
        try:
            amount = int(amount_str)
        except ValueError:
            return False, "Invalid amount. Use a number or 'all'."

    if amount <= 0:
        return False, "No gold to withdraw."
    if amount > player["gold_banked"]:
        return False, f"Only have {player['gold_banked']}g banked."

    conn.execute(
        """UPDATE players SET
           gold_carried = gold_carried + ?,
           gold_banked = gold_banked - ?
           WHERE id = ?""",
        (amount, amount, player_id),
    )
    conn.commit()
    new_carried = player["gold_carried"] + amount
    return True, f"Withdrew {amount}g. Carrying: {new_carried}g."


# ── Healer ───────────────────────────────────────────────────────────────────


def calc_heal_cost(player: dict) -> int:
    """Calculate gold cost to fully heal.

    Cost = missing_hp * (HEAL_COST_PER_HP + level * HEAL_LEVEL_MULT)
    Minimum 0 (if already at full HP).
    """
    missing = player["hp_max"] - player["hp"]
    if missing <= 0:
        return 0
    cost_per = HEAL_COST_PER_HP + player["level"] * HEAL_LEVEL_MULT
    return max(1, math.ceil(missing * cost_per))


def heal_player(
    conn: sqlite3.Connection, player_id: int, player: dict
) -> tuple[bool, str]:
    """Heal player to full HP for gold.

    Returns:
        (success, message)
    """
    if player["hp"] >= player["hp_max"]:
        return False, "Already at full HP."

    cost = calc_heal_cost(player)
    if player["gold_carried"] < cost:
        return False, f"Healing costs {cost}g. You have {player['gold_carried']}g."

    conn.execute(
        """UPDATE players SET
           hp = hp_max,
           gold_carried = gold_carried - ?
           WHERE id = ?""",
        (cost, player_id),
    )
    conn.commit()
    return True, f"Healed to full HP! Cost: {cost}g."


# ── Loot Drops ───────────────────────────────────────────────────────────────


def try_loot_drop(
    conn: sqlite3.Connection, player_id: int, monster_tier: int
) -> Optional[str]:
    """Roll for a loot drop after killing a monster.

    Returns:
        Loot message string if item dropped, None otherwise.
    """
    chance = LOOT_DROP_CHANCE.get(monster_tier, 0.10)
    if random.random() > chance:
        return None

    # Pick a random item matching the monster's tier
    item = conn.execute(
        """SELECT * FROM items WHERE tier = ?
           AND slot IN ('weapon', 'armor', 'trinket')
           ORDER BY RANDOM() LIMIT 1""",
        (monster_tier,),
    ).fetchone()

    if not item:
        return None

    ok, msg = add_item_to_inventory(conn, player_id, item["id"])
    return msg
