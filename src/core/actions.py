"""
Action handlers for MMUD.
Each handler takes (conn, player, args) and returns a response string.
Actions are atomic: one command in, one response out.
"""

import random
import sqlite3
from typing import Optional

from config import (
    CLASSES,
    DUNGEON_ACTIONS_PER_DAY,
    SOCIAL_ACTIONS_PER_DAY,
    STAT_POINTS_PER_LEVEL,
    TOWN_DESCRIPTIONS,
)
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


# Actions that cost a dungeon action
DUNGEON_COST_ACTIONS = {"move", "fight", "flee", "examine"}

# Actions that are always free
FREE_ACTIONS = {
    "look", "stats", "inventory", "help", "who",
    "shop", "buy", "sell", "heal", "bank", "deposit", "withdraw",
    "barkeep", "token", "spend", "train", "equip", "unequip", "drop",
    "enter", "town", "leave", "bounty", "read", "helpful", "message", "mail",
    "healer", "merchant", "sage",
}


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
        "town": action_return_town,
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
        "healer": action_healer_desc,
        "merchant": action_merchant_desc,
        "sage": action_sage_desc,
        "token": action_token,
        "spend": action_spend,
        "bounty": action_bounty,
        "read": action_read,
        "helpful": action_helpful,
        "message": action_message,
        "mail": action_mail,
        "who": action_who,
    }

    handler = handlers.get(command)
    if not handler:
        return _smart_error(player)

    # Check action budget for dungeon-cost actions
    if command in DUNGEON_COST_ACTIONS:
        if player["state"] in ("dungeon", "combat"):
            if player["dungeon_actions_remaining"] <= 0:
                return fmt("No dungeon actions left today! Return to town and rest.")

    return handler(conn, player, args)


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
    if player["state"] == "town":
        return fmt("You're in town. Type ENTER to go to the dungeon.")

    if player["state"] == "combat":
        return fmt("You're in combat! FIGHT or FLEE.")

    if player["state"] == "dead":
        return fmt("You are dead.")

    if not args:
        return fmt("Move where? N/S/E/W/U/D")

    direction = args[0].lower()

    # Costs a dungeon action
    if not player_model.use_dungeon_action(conn, player["id"]):
        return fmt("No dungeon actions left today!")

    room, error = world_mgr.move_player(conn, player, direction)
    if error:
        return fmt(error)

    # Check for monster in new room
    monster = world_data.get_room_monster(conn, room["id"])
    exits = world_data.get_room_exits(conn, room["id"])
    exit_dirs = [e["direction"] for e in exits]

    if monster:
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

    # Cost a dungeon action
    if not player_model.use_dungeon_action(conn, player["id"]):
        return fmt("No dungeon actions left today!")

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

    # Cost a dungeon action
    if not player_model.use_dungeon_action(conn, player["id"]):
        return fmt("No dungeon actions left today!")

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
    )


def action_enter_dungeon(
    conn: sqlite3.Connection, player: dict, args: list[str]
) -> str:
    """Enter the dungeon from town."""
    if player["state"] != "town":
        return fmt("You're already in the dungeon.")

    room = world_mgr.enter_dungeon(conn, player)
    if not room:
        return fmt("The dungeon entrance is sealed.")

    exits = world_data.get_room_exits(conn, room["id"])
    exit_dirs = [e["direction"] for e in exits]
    return fmt_room(room["name"], room["description"], exit_dirs)


def action_return_town(
    conn: sqlite3.Connection, player: dict, args: list[str]
) -> str:
    """Return to town."""
    if player["state"] == "town":
        return fmt("You're already in town.")

    if player["state"] == "combat":
        return fmt("You're in combat! FLEE first.")

    world_mgr.return_to_town(conn, player["id"])
    return fmt(TOWN_DESCRIPTIONS["tavern"])


def action_leave(
    conn: sqlite3.Connection, player: dict, args: list[str]
) -> str:
    """Leave current location. In town: show exterior. In dungeon: return to town."""
    if player["state"] == "town":
        return fmt(TOWN_DESCRIPTIONS["tavern"])

    if player["state"] == "combat":
        return fmt("You're in combat! FLEE first.")

    world_mgr.return_to_town(conn, player["id"])
    return fmt(TOWN_DESCRIPTIONS["tavern"])


def action_help(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show available commands."""
    if player["state"] == "town":
        return fmt("BAR ENTER SHOP BUY SELL HEAL BANK TOK BOUNTY MAIL WHO TRAIN INV LEAVE HELP")
    if player["state"] == "combat":
        return fmt("FIGHT(F) FLEE STATS LOOK HELP")
    if player["state"] == "dungeon":
        return fmt("N/S/E/W LOOK(L) FIGHT(F) FLEE MSG HELPFUL STATS(ST) INV(I) TOWN HELP")
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

    return fmt(TOWN_DESCRIPTIONS["bar"])


def action_healer_desc(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Approach Maren. Short acknowledgment, NPC DM follows. Town only."""
    if player["state"] != "town":
        return fmt("Maren is in town. Head back first.")

    return fmt(TOWN_DESCRIPTIONS["maren"])


def action_merchant_desc(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Approach Torval. Short acknowledgment, NPC DM follows. Town only."""
    if player["state"] != "town":
        return fmt("Torval is in town. Head back first.")

    return fmt(TOWN_DESCRIPTIONS["torval"])


def action_sage_desc(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Approach Whisper. Short acknowledgment, NPC DM follows. Town only."""
    if player["state"] != "town":
        return fmt("Whisper is in town. Head back first.")

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
