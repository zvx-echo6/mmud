"""
Action handlers for MMUD.
Each handler takes (conn, player, args) and returns a response string.
Actions are atomic: one command in, one response out.
"""

import random
import sqlite3
from typing import Optional

from config import (
    BARD_TOKEN_CAP,
    CAST_DAMAGE_MULT,
    CAST_RESOURCE_COST,
    CHARGE_DAMAGE_MULT,
    CHARGE_RESOURCE_COST,
    CLASSES,
    DUNGEON_ACTIONS_PER_DAY,
    FAST_TRAVEL_ENABLED,
    FREE_TRAVERSAL_ON_CLEARED,
    MONSTER_RETREAT_ON_CLEARED,
    NUM_FLOORS,
    RESOURCE_NAMES,
    RESOURCE_REGEN_REST,
    SNEAK_BYPASS_CHANCE,
    SNEAK_RESOURCE_COST,
    SOCIAL_ACTIONS_PER_DAY,
    STAT_POINTS_PER_LEVEL,
    TOWN_DESCRIPTIONS,
)
from src.models.epoch import get_epoch
from src.core import combat as combat_engine
from src.core import world as world_mgr
from src.models import player as player_model
from src.models import world as world_data
from src.systems import broadcast as broadcast_sys
from src.systems import bounty as bounty_sys
from src.systems import economy
from src.systems import social as social_sys
from src.systems import barkeep as barkeep_sys
from src.transport.formatter import (
    fmt,
    fmt_combat_narrative,
    fmt_combat_status,
    fmt_death,
    fmt_level_up,
    fmt_room,
    fmt_stats,
)


# Actions that cost a dungeon action (movement only — combat is free)
DUNGEON_COST_ACTIONS = {"move"}

# Actions that are always free
FREE_ACTIONS = {
    "look", "stats", "inventory", "help", "who", "rest", "examine",
    "shop", "buy", "sell", "heal", "bank", "deposit", "withdraw",
    "barkeep", "token", "spend", "train", "equip", "unequip", "drop",
    "enter", "return", "leave", "bounty", "read", "helpful", "message", "mail",
    "grist", "healer", "merchant", "rumor",
    "logout",
}


def _is_player_floor_cleared(conn: sqlite3.Connection, player: dict) -> bool:
    """Check if the player's current floor boss is dead and they've visited it."""
    floor = player.get("floor", 0)
    if floor <= 0:
        return False
    try:
        row = conn.execute(
            "SELECT boss_killed FROM floor_progress WHERE player_id = ? AND floor = ?",
            (player["id"], floor),
        ).fetchone()
        return row is not None and row["boss_killed"] == 1
    except Exception:
        return False


def handle_action(
    conn: sqlite3.Connection, player: dict, command: str, args: list[str]
) -> Optional[str]:
    """Route a command to the correct handler."""
    handlers = {
        "look": action_look,
        "move": action_move,
        "fight": action_fight,
        "flee": action_flee,
        "stats": action_stats,
        "enter": action_enter_dungeon,
        "return": action_return,
        "leave": action_leave,
        "help": action_help,
        "inventory": action_inventory,
        # Phase 2: Economy & Progression
        "train": action_train,
        "shop": action_shop,
        "buy": action_buy,
        "sell": action_sell,
        "equip": action_equip,
        "unequip": action_unequip,
        "drop": action_drop,
        "bank": action_bank,
        "deposit": action_deposit,
        "withdraw": action_withdraw,
        "heal": action_heal,
        # Phase 3: Social Systems
        "barkeep": action_barkeep,
        "grist": action_grist_desc,
        "healer": action_healer_desc,
        "merchant": action_merchant_desc,
        "rumor": action_rumor_desc,
        "token": action_token,
        "spend": action_spend,
        "bounty": action_bounty,
        "read": action_read,
        "helpful": action_helpful,
        "message": action_message,
        "mail": action_mail,
        "who": action_who,
        # Examine (town secrets + dungeon)
        "examine": action_examine,
        # Class abilities + rest
        "rest": action_rest,
        "charge": action_charge,
        "sneak": action_sneak,
        "cast": action_cast,
    }

    handler = handlers.get(command)
    if not handler:
        return _smart_error(player)

    return handler(conn, player, args)


def _sync_town_position(
    conn: sqlite3.Connection, player_id: int, location: str
) -> None:
    """Sync both room_id and town_location for a town position.

    Every command that changes town_location must call this to keep room_id
    in sync with the symbolic location.

    Args:
        location: 'grist', 'maren', 'torval', 'whisper', or 'bar'.
    """
    if location in ("grist", "maren", "torval", "whisper"):
        try:
            row = conn.execute(
                "SELECT id FROM rooms WHERE floor = 0 AND npc_name = ? LIMIT 1",
                (location,),
            ).fetchone()
        except Exception:
            row = None
        if row:
            player_model.update_state(
                conn, player_id, room_id=row["id"], town_location=location
            )
            return
    else:
        center = world_data.get_hub_room(conn, floor=0)
        if center:
            player_model.update_state(
                conn, player_id, room_id=center["id"], town_location=location
            )
            return
    # No floor 0 rooms (test/legacy): update town_location only
    player_model.update_state(conn, player_id, town_location=location)


def _smart_error(player: dict) -> str:
    """State-specific error with valid command suggestions."""
    state = player.get("state", "town")
    if state == "town":
        return fmt("Unknown. Try: L(ook) BAR ENTER SHOP HEAL H(elp)")
    if state == "dungeon":
        return fmt("Unknown. Try: F(ight) FL(ee) L(ook) N/S/E/W H(elp)")
    if state == "combat":
        return fmt("Unknown. Try: F(ight) FL(ee) STATS")
    if state == "dead":
        return fmt("Unknown. You're dead. Type RESPAWN.")
    return fmt("Unknown. Type H for help.")


def action_look(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Look at the current room or town."""
    if player["state"] == "town":
        room = world_data.get_room(conn, player["room_id"]) if player.get("room_id") else None
        if room:
            exits = world_data.get_room_exits(conn, room["id"])
            exit_dirs = [e["direction"] for e in exits]
            loc = player.get("town_location")
            if loc and loc in TOWN_DESCRIPTIONS:
                desc = TOWN_DESCRIPTIONS[loc]
            else:
                desc = room["description"]
            return fmt_room(room["name"], desc, exit_dirs)
        # Fallback for pre-migration players without room_id
        loc = player.get("town_location")
        if loc and loc in TOWN_DESCRIPTIONS:
            return fmt(TOWN_DESCRIPTIONS[loc])
        return fmt(TOWN_DESCRIPTIONS["tavern"])

    if player["state"] == "dead":
        return fmt("You are dead. Respawning...")

    room = world_data.get_room(conn, player["room_id"])
    if not room:
        return fmt("You're nowhere. Something is wrong.")

    exits = world_data.get_room_exits(conn, player["room_id"])
    exit_dirs = [e["direction"] for e in exits]

    # Check for monster
    monster = world_data.get_room_monster(conn, player["room_id"])
    if monster:
        monster_info = f" {monster['name']} lurks here!"
        desc = room["description_short"] + monster_info
    else:
        desc = room["description"]

    # Append player messages
    msgs = social_sys.get_room_messages(conn, player["room_id"], player["id"])
    msg_str = social_sys.format_room_messages(msgs)
    if msg_str:
        desc = desc + " " + msg_str

    return fmt_room(room["name"], desc, exit_dirs)


def action_move(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Move in a direction."""
    if player["state"] == "combat":
        return fmt("You're in combat! FIGHT or FLEE.")

    if player["state"] == "dead":
        return fmt("You are dead.")

    if player["state"] not in ("town", "dungeon"):
        return fmt("You can't move right now.")

    if not args:
        return fmt("Move where? N/S/E/W")

    direction = args[0].lower()

    # Only charge dungeon action in the dungeon (free on cleared floors)
    if player["state"] == "dungeon":
        if not (FREE_TRAVERSAL_ON_CLEARED and _is_player_floor_cleared(conn, player)):
            if not player_model.use_dungeon_action(conn, player["id"]):
                return fmt("No movement actions left today. RETURN to town and rest.")

    room, error = world_mgr.move_player(conn, player, direction)
    if error:
        return fmt(error)

    exits = world_data.get_room_exits(conn, room["id"])
    exit_dirs = [e["direction"] for e in exits]

    # Floor 0: update town_location if NPC room
    if room.get("floor") == 0:
        npc = room.get("npc_name")
        if npc:
            player_model.update_state(conn, player["id"], town_location=npc)
        else:
            player_model.update_state(conn, player["id"], town_location=None)
        return fmt_room(room["name"], room["description_short"], exit_dirs)

    # Auto-record floor boss visit if boss is dead in this room
    dead_boss = conn.execute(
        "SELECT id FROM monsters WHERE room_id = ? AND is_floor_boss = 1 AND hp <= 0",
        (room["id"],),
    ).fetchone()
    if dead_boss:
        floor = room.get("floor", 0)
        conn.execute(
            """INSERT OR IGNORE INTO floor_progress
               (player_id, floor, boss_killed, boss_killed_at)
               VALUES (?, ?, 1, CURRENT_TIMESTAMP)""",
            (player["id"], floor),
        )
        new_deepest = floor + 1
        if new_deepest <= NUM_FLOORS:
            conn.execute(
                "UPDATE players SET deepest_floor_reached = MAX(deepest_floor_reached, ?) WHERE id = ?",
                (new_deepest, player["id"]),
            )
        conn.commit()

    # Refresh cleared status after potential boss visit recording
    # (need updated player for floor cleared check)
    updated_player = player_model.get_player(conn, player["id"])
    floor_cleared = FREE_TRAVERSAL_ON_CLEARED and _is_player_floor_cleared(conn, updated_player or player)

    # Floor transition text overrides description for this one move
    transition = room.get("_floor_transition")
    if transition:
        monster = world_data.get_room_monster(conn, room["id"])
        if monster:
            if MONSTER_RETREAT_ON_CLEARED and floor_cleared:
                desc = transition + f" A {monster['name']} retreats deeper."
                return fmt_room(room["name"], desc, exit_dirs)
            world_mgr.enter_combat(conn, player["id"], monster["id"])
            desc = transition + f" {monster['name']} blocks your path!"
            return fmt_room(room["name"], desc, exit_dirs)
        return fmt_room(room["name"], transition, exit_dirs)

    # Dungeon: check for monster
    monster = world_data.get_room_monster(conn, room["id"])
    if monster:
        if MONSTER_RETREAT_ON_CLEARED and floor_cleared:
            desc = room["description_short"] + f" A {monster['name']} retreats deeper."
            return fmt_room(room["name"], desc, exit_dirs)
        world_mgr.enter_combat(conn, player["id"], monster["id"])
        desc = room["description_short"] + f" {monster['name']} blocks your path!"
        return fmt_room(room["name"], desc, exit_dirs)

    return fmt_room(room["name"], room["description_short"], exit_dirs)


def action_fight(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Fight a monster — one round per command. Uses effective stats (base + gear)."""
    if player["state"] == "town":
        return fmt("Nothing to fight in town. Type ENTER to explore.")

    if player["state"] == "dead":
        return fmt("You are dead.")

    # Get or enter combat
    monster = None
    if player["state"] == "combat" and player["combat_monster_id"]:
        monster = world_data.get_monster(conn, player["combat_monster_id"])

    if not monster or monster["hp"] <= 0:
        # Look for a monster in the room
        if not player["room_id"]:
            return fmt("No monster here.")
        monster = world_data.get_room_monster(conn, player["room_id"])
        if not monster:
            return fmt("No monster here. Room is clear.")

        # Enter combat
        world_mgr.enter_combat(conn, player["id"], monster["id"])

    # Use effective stats (base + gear bonuses)
    eff = economy.get_effective_stats(conn, player)

    # Resolve one round
    result = combat_engine.resolve_round(
        player_pow=eff["pow"], player_def=eff["def"],
        player_spd=eff["spd"], player_hp=player["hp"],
        monster_pow=monster["pow"], monster_def=monster["def"],
        monster_spd=monster["spd"], monster_hp=monster["hp"],
        monster_name=monster["name"],
    )

    # Update player HP
    player_model.update_state(conn, player["id"], hp=result.player_hp)

    # Update monster HP
    if result.player_damage_dealt > 0:
        world_data.damage_monster(conn, monster["id"], result.player_damage_dealt)

    # Track bounty contribution
    bounty = bounty_sys.get_bounty_by_monster(conn, monster["id"])
    if bounty and result.player_damage_dealt > 0:
        bounty_sys.record_contribution(
            conn, bounty["id"], player["id"], result.player_damage_dealt
        )
        bounty_sys.check_halfway_broadcast(conn, bounty["id"], monster["id"])

    # Check outcomes
    if result.monster_dead:
        world_mgr.exit_combat(conn, player["id"])
        xp = monster["xp_reward"]
        gold = random.randint(monster["gold_reward_min"], monster["gold_reward_max"])
        new_level = player_model.award_xp(conn, player["id"], xp)
        player_model.award_gold(conn, player["id"], gold)

        parts = [f"{result.narrative} +{xp}xp +{gold}g"]

        # Floor boss kill tracking
        if monster.get("is_floor_boss"):
            floor = player.get("floor", 0)
            conn.execute(
                """INSERT OR REPLACE INTO floor_progress
                   (player_id, floor, boss_killed, boss_killed_at)
                   VALUES (?, ?, 1, CURRENT_TIMESTAMP)""",
                (player["id"], floor),
            )
            new_deepest = floor + 1
            if new_deepest <= NUM_FLOORS:
                conn.execute(
                    "UPDATE players SET deepest_floor_reached = MAX(deepest_floor_reached, ?) WHERE id = ?",
                    (new_deepest, player["id"]),
                )
            conn.commit()
            broadcast_sys.create_broadcast(
                conn, 1,
                f"{player['name']} felled {monster['name']}. Floor {floor} falls silent.",
            )
            # Floor unlock broadcast (skip floor 8 — that's endgame)
            if floor < NUM_FLOORS:
                broadcast_sys.broadcast_floor_unlock(conn, floor)

        # Check bounty completion
        if bounty:
            bounty_msg = bounty_sys.check_bounty_completion(
                conn, bounty["id"], player["id"]
            )
            if bounty_msg:
                parts.append(bounty_msg)

        # Loot drop roll
        loot_msg = economy.try_loot_drop(conn, player["id"], monster["tier"])
        if loot_msg:
            parts.append(loot_msg)

        if new_level:
            sp = (new_level - player["level"]) * STAT_POINTS_PER_LEVEL
            parts.append(fmt_level_up(new_level, sp))
            broadcast_sys.broadcast_level_up(conn, player["name"], new_level)

        return fmt(" ".join(parts))

    if result.player_dead:
        losses = player_model.apply_death(conn, player["id"])
        floor = player.get("floor", 1) or 1
        broadcast_sys.broadcast_death(conn, player["name"], floor)
        return fmt_death(losses["gold_lost"], losses["xp_lost"])

    # Combat continues
    narrative = fmt_combat_narrative(result.narrative)
    status = fmt_combat_status(
        result.player_hp, player["hp_max"],
        monster["name"], result.monster_hp, monster["hp_max"],
    )
    return fmt(f"{narrative} {status}")


def action_flee(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Attempt to flee from combat. Uses effective stats."""
    if player["state"] != "combat":
        return fmt("You're not in combat.")

    monster = world_data.get_monster(conn, player["combat_monster_id"])
    if not monster:
        world_mgr.exit_combat(conn, player["id"])
        return fmt("The monster is gone. You're safe.")

    # Use effective stats
    eff = economy.get_effective_stats(conn, player)

    result = combat_engine.attempt_flee(
        player_spd=eff["spd"],
        player_hp=player["hp"],
        monster_pow=monster["pow"],
        player_def=eff["def"],
        monster_name=monster["name"],
    )

    player_model.update_state(conn, player["id"], hp=result.player_hp)

    if result.success:
        world_mgr.exit_combat(conn, player["id"])
        return fmt(result.narrative)

    if result.player_dead:
        losses = player_model.apply_death(conn, player["id"])
        return fmt_death(losses["gold_lost"], losses["xp_lost"])

    return fmt(result.narrative)


def action_stats(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show player stats with effective stats (base + gear)."""
    eff = economy.get_effective_stats(conn, player)
    res_name = RESOURCE_NAMES.get(player["class"])
    return fmt_stats(
        name=player["name"],
        cls=player["class"],
        level=player["level"],
        hp=player["hp"],
        hp_max=player["hp_max"],
        pow_=eff["pow"],
        def_=eff["def"],
        spd=eff["spd"],
        gold=player["gold_carried"],
        xp=player["xp"],
        actions=player["dungeon_actions_remaining"],
        banked=player["gold_banked"],
        stat_points=player["stat_points"],
        resource=player.get("resource"),
        resource_max=player.get("resource_max"),
        resource_name=res_name,
    )


def action_enter_dungeon(
    conn: sqlite3.Connection, player: dict, args: list[str]
) -> str:
    """Enter the dungeon from town. Supports fast travel: ENTER <floor>."""
    if player["state"] != "town":
        return fmt("You're already in the dungeon.")

    # Parse optional floor argument for fast travel
    target_floor = 0
    if args and FAST_TRAVEL_ENABLED:
        try:
            target_floor = int(args[0])
        except ValueError:
            pass
        if target_floor > 0:
            deepest = player.get("deepest_floor_reached", 1) or 1
            if target_floor > deepest:
                return fmt(f"Can't reach F{target_floor}. Deepest unlocked: F{deepest}.")
            if target_floor > NUM_FLOORS:
                return fmt(f"Floor {target_floor} doesn't exist.")

    room = world_mgr.enter_dungeon(conn, player, target_floor=target_floor)
    if not room:
        return fmt("The dungeon entrance is sealed.")

    exits = world_data.get_room_exits(conn, room["id"])
    exit_dirs = [e["direction"] for e in exits]

    # Show floor transition text if available
    floor = target_floor if target_floor > 0 else 1
    transition = world_mgr._get_floor_transition(conn, floor)
    if transition:
        return fmt_room(room["name"], transition, exit_dirs)
    return fmt_room(room["name"], room["description"], exit_dirs)


def action_return(
    conn: sqlite3.Connection, player: dict, args: list[str]
) -> str:
    """Retreat to town with narrative summary of the journey back."""
    if player["state"] == "town":
        return fmt("You're already in town.")

    if player["state"] == "combat":
        return fmt("You're in combat! FLEE first.")

    if player["state"] == "dead":
        return fmt("You are dead.")

    current_floor = player.get("floor", 1)

    if current_floor <= 1:
        narrative = "You retrace your steps through the first floor. The entrance light grows. Town."
    elif current_floor <= 3:
        narrative = (
            f"You climb back through {current_floor} floors. "
            f"Cleared rooms echo with your footsteps. The air warms as you ascend. Town."
        )
    elif current_floor <= 5:
        narrative = (
            f"The long climb from floor {current_floor}. "
            f"Familiar corridors, old bloodstains, the smell of the upper floors. "
            f"You emerge into lamplight and smoke. Town."
        )
    else:
        narrative = (
            f"Floor {current_floor} to the surface. A long retreat through "
            f"stone and silence. Each floor lighter than the last. "
            f"By the time you see the tavern door, your legs are shaking. Town."
        )

    world_mgr.return_to_town(conn, player["id"])
    return fmt(narrative)


def action_leave(
    conn: sqlite3.Connection, player: dict, args: list[str]
) -> str:
    """Leave current location. In town: navigate back. In dungeon: return to town."""
    if player["state"] == "town":
        _sync_town_position(conn, player["id"], "tavern")
        return fmt(TOWN_DESCRIPTIONS["tavern"])

    if player["state"] == "combat":
        return fmt("You're in combat! FLEE first.")

    world_mgr.return_to_town(conn, player["id"])
    return fmt(TOWN_DESCRIPTIONS["tavern"])


def action_help(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show available commands."""
    ability_hints = {"warrior": " CHARGE", "rogue": " SNEAK", "caster": " CAST"}
    hint = ability_hints.get(player["class"], "")
    if player["state"] == "town":
        return fmt("N/S/E/W ENTER SHOP HEAL BANK EX TOK BOUNTY MAIL WHO TRAIN REST HELP")
    if player["state"] == "combat":
        return fmt(f"FIGHT(F) FLEE{hint} STATS LOOK HELP")
    if player["state"] == "dungeon":
        return fmt(f"N/S/E/W LOOK(L) FIGHT(F) FLEE{hint} RETURN STATS(ST) INV(I) HELP")
    return fmt("LOOK STATS HELP")


def action_inventory(
    conn: sqlite3.Connection, player: dict, args: list[str]
) -> str:
    """Show inventory."""
    items = economy.get_inventory(conn, player["id"])

    if not items:
        return fmt("Inventory: empty. Visit the SHOP in town.")

    equipped = []
    backpack = []
    for r in items:
        label = r["name"]
        if r["equipped"]:
            equipped.append(f"[{r['item_slot']}]{label}")
        else:
            backpack.append(label)

    parts = []
    if equipped:
        parts.append("Eq:" + ",".join(equipped))
    if backpack:
        parts.append("Bag:" + ",".join(backpack))

    return fmt(" ".join(parts) if parts else "Inventory: empty")


# ── Phase 2: Economy & Progression Actions ──────────────────────────────────


def action_train(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Train a stat (spend stat points)."""
    if player["state"] != "town":
        return fmt("You can only train in town.")

    if not args:
        sp = player["stat_points"]
        return fmt(f"TRAIN POW/DEF/SPD to spend stat points. You have {sp} pts.")

    ok, msg = player_model.train_stat(conn, player["id"], args[0])
    return fmt(msg)


def action_shop(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show shop items available for purchase."""
    if player["state"] != "town":
        return fmt("You can only shop in town.")

    epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
    day = epoch["day_number"] if epoch else 1

    items = economy.get_shop_items(conn, day)
    if not items:
        return fmt("Shop is empty. Check back later.")

    # Compact listing: "T1:Rusty Sword(65g) T2:Iron Blade(250g)"
    listing = []
    for it in items:
        listing.append(f"{it['name']}({it['price']}g)")

    return fmt("Shop: " + " ".join(listing))


def action_buy(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Buy an item from the shop."""
    if player["state"] != "town":
        return fmt("You can only buy in town.")

    if not args:
        return fmt("BUY <item name>. Type SHOP to see available items.")

    item_name = " ".join(args)
    epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
    day = epoch["day_number"] if epoch else 1

    ok, msg = economy.buy_item(conn, player["id"], item_name, day)
    return fmt(msg)


def action_sell(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Sell an item from inventory."""
    if player["state"] != "town":
        return fmt("You can only sell in town.")

    if not args:
        return fmt("SELL <item name>. Sells for 50% of shop price.")

    item_name = " ".join(args)
    ok, msg = economy.sell_item(conn, player["id"], item_name)
    return fmt(msg)


def action_equip(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Equip an item from backpack."""
    if not args:
        return fmt("EQUIP <item name>. Equips item from backpack to its slot.")

    item_name = " ".join(args)
    ok, msg = economy.equip_item(conn, player["id"], item_name)
    return fmt(msg)


def action_unequip(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Unequip an item from a gear slot."""
    if not args:
        return fmt("UNEQUIP <slot>. Slots: weapon, armor, trinket.")

    ok, msg = economy.unequip_slot(conn, player["id"], args[0])
    return fmt(msg)


def action_drop(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Drop an item from inventory permanently."""
    if not args:
        return fmt("DROP <item name>. Permanently destroys the item.")

    item_name = " ".join(args)
    ok, msg = economy.drop_item(conn, player["id"], item_name)
    return fmt(msg)


def action_bank(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show bank balance."""
    if player["state"] != "town":
        return fmt("You can only use the bank in town.")

    return fmt(
        f"Bank: {player['gold_banked']}g. Carried: {player['gold_carried']}g. "
        f"DEP <amt> / WD <amt>"
    )


def action_deposit(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Deposit gold to bank."""
    if player["state"] != "town":
        return fmt("You can only use the bank in town.")

    if not args:
        return fmt("DEP <amount> or DEP ALL")

    ok, msg = economy.deposit_gold(conn, player["id"], args[0])
    return fmt(msg)


def action_withdraw(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Withdraw gold from bank."""
    if player["state"] != "town":
        return fmt("You can only use the bank in town.")

    if not args:
        return fmt("WD <amount> or WD ALL")

    ok, msg = economy.withdraw_gold(conn, player["id"], args[0])
    return fmt(msg)


def action_heal(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Heal at the healer in town."""
    if player["state"] != "town":
        return fmt("You can only heal in town.")

    if player["hp"] >= player["hp_max"]:
        return fmt("Already at full HP.")

    cost = economy.calc_heal_cost(player)
    if args and args[0].lower() == "y":
        ok, msg = economy.heal_player(conn, player["id"], player)
        return fmt(msg)

    return fmt(f"Heal to full? Cost: {cost}g. You have {player['gold_carried']}g. HEAL Y")


# ── Phase 3: Social System Actions ────────────────────────────────────────


def action_barkeep(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Enter the bar. Shows all four NPCs. Free action, town only."""
    if player["state"] != "town":
        return fmt("The bar is in town. Head back first.")

    _sync_town_position(conn, player["id"], "bar")
    return fmt(TOWN_DESCRIPTIONS["bar"])


def action_grist_desc(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Approach Grist. Short acknowledgment, NPC DM follows. Town only."""
    if player["state"] != "town":
        return fmt("Grist is in town. Head back first.")

    _sync_town_position(conn, player["id"], "grist")
    return fmt(TOWN_DESCRIPTIONS["grist"])


def action_healer_desc(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Approach Maren. Short acknowledgment, NPC DM follows. Town only."""
    if player["state"] != "town":
        return fmt("Maren is in town. Head back first.")

    _sync_town_position(conn, player["id"], "maren")
    return fmt(TOWN_DESCRIPTIONS["maren"])


def action_merchant_desc(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Approach Torval. Short acknowledgment, NPC DM follows. Town only."""
    if player["state"] != "town":
        return fmt("Torval is in town. Head back first.")

    _sync_town_position(conn, player["id"], "torval")
    return fmt(TOWN_DESCRIPTIONS["torval"])


def action_rumor_desc(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Approach Whisper. Short acknowledgment, NPC DM follows. Town only."""
    if player["state"] != "town":
        return fmt("Whisper is in town. Head back first.")

    _sync_town_position(conn, player["id"], "whisper")
    return fmt(TOWN_DESCRIPTIONS["whisper"])


def action_token(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show bard token balance and spending menu."""
    if player["state"] != "town":
        return fmt("Visit the barkeep in town for tokens.")

    return fmt(barkeep_sys.get_token_info(player))


def action_spend(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Spend bard tokens at the barkeep."""
    if player["state"] != "town":
        return fmt("Visit the barkeep in town to spend tokens.")

    if len(args) < 2:
        return fmt("SPEND <cost> <choice>. Type TOK to see options.")

    ok, msg = barkeep_sys.spend_tokens(conn, player["id"], args[0], args[1])
    return fmt(msg)


def action_bounty(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show active bounties."""
    return fmt(bounty_sys.format_bounty_list(conn))


def action_read(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Read mail or messages."""
    ok, msg = social_sys.read_oldest_unread(conn, player["id"])
    return fmt(msg)


def action_helpful(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Vote a room message as helpful. Dungeon only."""
    if player["state"] not in ("dungeon", "combat"):
        return fmt("No messages to rate here.")

    if not player["room_id"]:
        return fmt("No messages here.")

    ok, msg = social_sys.vote_helpful(conn, player["id"], player["room_id"])
    return fmt(msg)


def action_message(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Leave a message in the current dungeon room. Costs 1 social action."""
    if player["state"] not in ("dungeon", "combat"):
        return fmt("You can only leave messages in the dungeon.")

    if not player["room_id"]:
        return fmt("Nowhere to leave a message.")

    if not args:
        return fmt("MSG <text> (max 15 chars).")

    # Check social action budget
    if player["social_actions_remaining"] <= 0:
        return fmt("No social actions left today.")

    text = " ".join(args)
    ok, msg = social_sys.leave_message(conn, player["id"], player["room_id"], text)
    if ok:
        conn.execute(
            "UPDATE players SET social_actions_remaining = social_actions_remaining - 1 WHERE id = ?",
            (player["id"],),
        )
        conn.commit()
    return fmt(msg)


def action_mail(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Send mail or check inbox. Sending costs 1 social action."""
    if not args:
        unread, total = social_sys.get_inbox(conn, player["id"])
        return fmt(f"Mail: {unread} unread / {total} total. MAIL <player> <msg> or READ")

    if len(args) == 1:
        # Might be trying to read
        return fmt("MAIL <player> <message> to send. READ to check mail.")

    # Sending mail — costs social action
    if player["social_actions_remaining"] <= 0:
        return fmt("No social actions left today.")

    to_name = args[0]
    message = " ".join(args[1:])
    ok, msg = social_sys.send_mail(conn, player["id"], to_name, message)
    if ok:
        conn.execute(
            "UPDATE players SET social_actions_remaining = social_actions_remaining - 1 WHERE id = ?",
            (player["id"],),
        )
        conn.commit()
    return fmt(msg)


def action_who(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show who is online."""
    players = social_sys.get_who_list(conn)
    return fmt(social_sys.format_who_list(players))


# ── Examine ────────────────────────────────────────────────────────────────


def action_examine(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Examine the current room for hidden details."""
    if player["state"] == "dead":
        return fmt("You are dead.")
    if not player.get("room_id"):
        return fmt("Nothing to examine here.")

    # Check for undiscovered secret in this room
    secret = conn.execute(
        "SELECT id, name, description FROM secrets WHERE room_id = ? AND discovered_by IS NULL LIMIT 1",
        (player["room_id"],),
    ).fetchone()
    if not secret:
        return fmt("You examine the area. Nothing unusual.")

    # Discover it
    conn.execute(
        "UPDATE secrets SET discovered_by = ?, discovered_at = CURRENT_TIMESTAMP WHERE id = ?",
        (player["id"], secret["id"]),
    )
    conn.execute(
        "UPDATE players SET secrets_found = secrets_found + 1 WHERE id = ?",
        (player["id"],),
    )

    # Town secrets: bard tokens only
    room = world_data.get_room(conn, player["room_id"])
    if room and room["floor"] == 0:
        conn.execute(
            "UPDATE players SET bard_tokens = MIN(bard_tokens + 1, ?) WHERE id = ?",
            (BARD_TOKEN_CAP, player["id"]),
        )
        conn.commit()
        return fmt(f"Found: {secret['name']}! +1 bard token.")

    conn.commit()
    return fmt(f"Found: {secret['name']}! {secret['description'][:80]}")


# ── Class Abilities & Resource Actions ─────────────────────────────────────


def action_rest(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Rest to recover 1 resource point. Town only, uses special action."""
    if player["state"] != "town":
        return fmt("You can only rest in town.")
    if player.get("resource", 0) >= player.get("resource_max", 5):
        res_name = RESOURCE_NAMES.get(player["class"], "resource")
        return fmt(f"{res_name} is full.")
    if player["special_actions_remaining"] <= 0:
        return fmt("Already rested today.")
    player_model.restore_resource(conn, player["id"], RESOURCE_REGEN_REST)
    conn.execute(
        "UPDATE players SET special_actions_remaining = special_actions_remaining - 1 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()
    res_name = RESOURCE_NAMES.get(player["class"], "resource")
    updated = player_model.get_player(conn, player["id"])
    return fmt(f"You rest. {res_name}: {updated['resource']}/{updated['resource_max']}")


def action_charge(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Warrior only. Costs 2 Focus. Heavy strike or charge into room."""
    if player["class"] != "warrior":
        return fmt("Only Warriors can charge.")
    if player["state"] == "town":
        return fmt("Nothing to charge at in town.")
    if not player_model.use_resource(conn, player["id"], CHARGE_RESOURCE_COST):
        return fmt(f"Not enough Focus. Need {CHARGE_RESOURCE_COST}.")

    if player["state"] == "combat":
        eff = economy.get_effective_stats(conn, player)
        monster = world_data.get_monster(conn, player["combat_monster_id"])
        if not monster:
            world_mgr.exit_combat(conn, player["id"])
            return fmt("No target. Combat ended.")
        boosted_pow = int(eff["pow"] * CHARGE_DAMAGE_MULT)
        dmg = combat_engine.calc_damage(boosted_pow, monster["def"])
        world_data.damage_monster(conn, monster["id"], dmg)
        new_mhp = max(0, monster["hp"] - dmg)
        if new_mhp <= 0:
            world_mgr.exit_combat(conn, player["id"])
            xp = monster["xp_reward"]
            gold = random.randint(monster["gold_reward_min"], monster["gold_reward_max"])
            player_model.award_xp(conn, player["id"], xp)
            player_model.award_gold(conn, player["id"], gold)
            return fmt(f"CHARGE! {monster['name']} falls! {dmg}dmg +{xp}xp +{gold}g")
        return fmt(f"CHARGE! {dmg}dmg to {monster['name']}! {monster['name']}:{new_mhp}/{monster['hp_max']}")

    # Dungeon (not combat): charge through 2 rooms
    if not args:
        return fmt("Charge where? CHARGE N/S/E/W")
    direction = args[0].lower()
    room1, error = world_mgr.move_player(conn, player, direction)
    if error:
        return fmt(error)

    # Check room 1 for monster
    monster = world_data.get_room_monster(conn, room1["id"])
    if monster:
        world_mgr.enter_combat(conn, player["id"], monster["id"])
        eff = economy.get_effective_stats(conn, player)
        boosted_pow = int(eff["pow"] * CHARGE_DAMAGE_MULT)
        dmg = combat_engine.calc_damage(boosted_pow, monster["def"])
        world_data.damage_monster(conn, monster["id"], dmg)
        new_mhp = max(0, monster["hp"] - dmg)
        if new_mhp <= 0:
            world_mgr.exit_combat(conn, player["id"])
            xp = monster["xp_reward"]
            gold = random.randint(monster["gold_reward_min"], monster["gold_reward_max"])
            player_model.award_xp(conn, player["id"], xp)
            player_model.award_gold(conn, player["id"], gold)
            return fmt(f"CHARGE into {room1['name']}! {monster['name']} crushed! {dmg}dmg +{xp}xp +{gold}g")
        return fmt(f"CHARGE into {room1['name']}! {dmg}dmg to {monster['name']}! {new_mhp}hp left")

    # Room 1 clear — attempt second room (random direction)
    exits = world_data.get_room_exits(conn, room1["id"])
    if not exits:
        return fmt(f"CHARGE into {room1['name']}! Dead end.")

    # Refresh player state after move
    updated_player = player_model.get_player(conn, player["id"])
    random_exit = random.choice(exits)
    room2, error2 = world_mgr.move_player(conn, updated_player, random_exit["direction"])
    if error2:
        return fmt(f"CHARGE into {room1['name']}! Path blocked beyond.")

    # Check room 2 for monster
    monster2 = world_data.get_room_monster(conn, room2["id"])
    if monster2:
        world_mgr.enter_combat(conn, player["id"], monster2["id"])
        eff = economy.get_effective_stats(conn, player)
        boosted_pow = int(eff["pow"] * CHARGE_DAMAGE_MULT)
        dmg = combat_engine.calc_damage(boosted_pow, monster2["def"])
        world_data.damage_monster(conn, monster2["id"], dmg)
        new_mhp = max(0, monster2["hp"] - dmg)
        if new_mhp <= 0:
            world_mgr.exit_combat(conn, player["id"])
            xp = monster2["xp_reward"]
            gold = random.randint(monster2["gold_reward_min"], monster2["gold_reward_max"])
            player_model.award_xp(conn, player["id"], xp)
            player_model.award_gold(conn, player["id"], gold)
            return fmt(f"CHARGE through to {room2['name']}! {monster2['name']} crushed! {dmg}dmg +{xp}xp +{gold}g")
        return fmt(f"CHARGE through to {room2['name']}! {dmg}dmg to {monster2['name']}! {new_mhp}hp left")

    return fmt(f"CHARGE through {room1['name']} into {room2['name']}! Clear.")


def action_sneak(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Rogue only. Costs 1 Trick. Backstab or sneak past monsters."""
    if player["class"] != "rogue":
        return fmt("Only Rogues can sneak.")
    if player["state"] == "town":
        return fmt("Nothing to sneak past in town.")
    if not player_model.use_resource(conn, player["id"], SNEAK_RESOURCE_COST):
        return fmt("Not enough Tricks. Need 1.")

    if player["state"] == "combat":
        eff = economy.get_effective_stats(conn, player)
        monster = world_data.get_monster(conn, player["combat_monster_id"])
        if not monster:
            world_mgr.exit_combat(conn, player["id"])
            return fmt("No target.")
        if random.random() < SNEAK_BYPASS_CHANCE:
            dmg = combat_engine.calc_damage(eff["pow"] * 2, monster["def"])
            world_data.damage_monster(conn, monster["id"], dmg)
            new_mhp = max(0, monster["hp"] - dmg)
            if new_mhp <= 0:
                world_mgr.exit_combat(conn, player["id"])
                xp = monster["xp_reward"]
                gold = random.randint(monster["gold_reward_min"], monster["gold_reward_max"])
                player_model.award_xp(conn, player["id"], xp)
                player_model.award_gold(conn, player["id"], gold)
                return fmt(f"Backstab! {monster['name']} falls! {dmg}dmg +{xp}xp +{gold}g")
            world_mgr.exit_combat(conn, player["id"])
            return fmt(f"Backstab {dmg}dmg! You slip away. {monster['name']}:{new_mhp}/{monster['hp_max']}")
        else:
            result = combat_engine.resolve_round(
                eff["pow"], eff["def"], eff["spd"], player["hp"],
                monster["pow"], monster["def"], monster["spd"], monster["hp"],
                monster["name"],
            )
            player_model.update_state(conn, player["id"], hp=result.player_hp)
            if result.player_damage_dealt > 0:
                world_data.damage_monster(conn, monster["id"], result.player_damage_dealt)
            if result.player_dead:
                losses = player_model.apply_death(conn, player["id"])
                return fmt_death(losses["gold_lost"], losses["xp_lost"])
            return fmt(f"Sneak failed! {result.narrative}")

    # Dungeon: sneak through room
    if not args:
        return fmt("Sneak where? SNEAK N/S/E/W")
    direction = args[0].lower()
    room, error = world_mgr.move_player(conn, player, direction)
    if error:
        return fmt(error)
    monster = world_data.get_room_monster(conn, room["id"])
    if monster:
        if random.random() < SNEAK_BYPASS_CHANCE:
            exits = world_data.get_room_exits(conn, room["id"])
            exit_dirs = [e["direction"] for e in exits]
            return fmt_room(room["name"], f"You slip past {monster['name']} unseen.", exit_dirs)
        else:
            world_mgr.enter_combat(conn, player["id"], monster["id"])
            return fmt(f"Spotted! {monster['name']} blocks your path in {room['name']}!")
    exits = world_data.get_room_exits(conn, room["id"])
    exit_dirs = [e["direction"] for e in exits]
    return fmt_room(room["name"], room["description_short"], exit_dirs)


def _get_random_spell_name(conn: sqlite3.Connection) -> str:
    """Pick a random spell name from the epoch's spell list."""
    epoch = get_epoch(conn)
    if epoch and epoch.get("spell_names"):
        names = [s.strip() for s in epoch["spell_names"].split(",") if s.strip()]
        if names:
            return random.choice(names)
    return "Arcane Bolt"


def action_cast(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Caster only. Costs 1 Mana. Pure magic damage or room reveal."""
    if player["class"] != "caster":
        return fmt("Only Casters can cast.")
    if player["state"] == "town":
        return fmt("Nothing to cast at in town.")
    if not player_model.use_resource(conn, player["id"], CAST_RESOURCE_COST):
        return fmt("Not enough Mana. Need 1.")

    if player["state"] == "combat":
        eff = economy.get_effective_stats(conn, player)
        monster = world_data.get_monster(conn, player["combat_monster_id"])
        if not monster:
            world_mgr.exit_combat(conn, player["id"])
            return fmt("No target.")
        dmg = max(1, int(eff["pow"] * CAST_DAMAGE_MULT * random.uniform(0.8, 1.2)))
        world_data.damage_monster(conn, monster["id"], dmg)
        new_mhp = max(0, monster["hp"] - dmg)
        # Pick spell name from epoch
        spell_name = _get_random_spell_name(conn)
        if new_mhp <= 0:
            world_mgr.exit_combat(conn, player["id"])
            xp = monster["xp_reward"]
            gold = random.randint(monster["gold_reward_min"], monster["gold_reward_max"])
            player_model.award_xp(conn, player["id"], xp)
            player_model.award_gold(conn, player["id"], gold)
            return fmt(f"{spell_name}! {monster['name']} crumbles. {dmg}dmg +{xp}xp +{gold}g")
        return fmt(f"{spell_name} hits {monster['name']} for {dmg}! {monster['name']}:{new_mhp}/{monster['hp_max']}")

    # Dungeon: reveal room content
    room = world_data.get_room(conn, player["room_id"])
    if not room:
        return fmt("Nothing to sense here.")

    # Check if already revealed
    if world_data.has_player_revealed(conn, player["id"], room["id"]):
        return fmt("Already revealed this room.")

    # Record the reveal
    world_data.record_player_reveal(conn, player["id"], room["id"])

    parts = []

    # Gold reveal
    if room.get("reveal_gold", 0) > 0:
        player_model.award_gold(conn, player["id"], room["reveal_gold"])
        parts.append(f"Found {room['reveal_gold']}g hidden here!")

    # Lore reveal
    if room.get("reveal_lore", ""):
        conn.execute(
            "UPDATE players SET bard_tokens = MIN(bard_tokens + 1, ?) WHERE id = ?",
            (BARD_TOKEN_CAP, player["id"]),
        )
        conn.commit()
        parts.append(room["reveal_lore"])

    # Auto-detect undiscovered secrets in this room
    secret = conn.execute(
        "SELECT name FROM secrets WHERE room_id = ? AND discovered_by IS NULL LIMIT 1",
        (room["id"],),
    ).fetchone()
    if secret:
        parts.append(f"Something hidden here: {secret['name']}")

    if not parts:
        return fmt("The room is hollow. Nothing hidden.")

    return fmt(" ".join(parts))
