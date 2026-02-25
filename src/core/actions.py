"""
Action handlers for MMUD.
Each handler takes (conn, player, args) and returns a response string.
Actions are atomic: one command in, one response out.
"""

import random
import sqlite3
from typing import Optional

from config import CLASSES, DUNGEON_ACTIONS_PER_DAY
from src.core import combat as combat_engine
from src.core import world as world_mgr
from src.models import player as player_model
from src.models import world as world_data
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
    "shop", "heal", "bank", "barkeep", "train",
    "enter", "town",
}


def handle_action(
    conn: sqlite3.Connection, player: dict, command: str, args: list[str]
) -> Optional[str]:
    """Route a command to the correct handler.

    Args:
        conn: Database connection.
        player: Player dict from database.
        command: Normalized command name.
        args: Command arguments.

    Returns:
        Response string, or None if unrecognized.
    """
    handlers = {
        "look": action_look,
        "move": action_move,
        "fight": action_fight,
        "flee": action_flee,
        "stats": action_stats,
        "enter": action_enter_dungeon,
        "town": action_return_town,
        "help": action_help,
        "inventory": action_inventory,
    }

    handler = handlers.get(command)
    if not handler:
        return fmt(f"Unknown command: {command}. Type H for help.")

    # Check action budget for dungeon-cost actions
    if command in DUNGEON_COST_ACTIONS:
        if player["state"] in ("dungeon", "combat"):
            if player["dungeon_actions_remaining"] <= 0:
                return fmt("No dungeon actions left today! Return to town and rest.")

    return handler(conn, player, args)


def action_look(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Look at the current room or town."""
    if player["state"] == "town":
        return fmt("Town square. Barkeep, Healer, Shop, Bank. Type ENTER to descend.")

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
    """Fight a monster — one round per command."""
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

    # Resolve one round
    result = combat_engine.resolve_round(
        player_pow=player["pow"], player_def=player["def"],
        player_spd=player["spd"], player_hp=player["hp"],
        monster_pow=monster["pow"], monster_def=monster["def"],
        monster_spd=monster["spd"], monster_hp=monster["hp"],
        monster_name=monster["name"],
    )

    # Update player HP
    player_model.update_state(conn, player["id"], hp=result.player_hp)

    # Update monster HP
    if result.player_damage_dealt > 0:
        world_data.damage_monster(conn, monster["id"], result.player_damage_dealt)

    # Check outcomes
    if result.monster_dead:
        world_mgr.exit_combat(conn, player["id"])
        xp = monster["xp_reward"]
        gold = random.randint(monster["gold_reward_min"], monster["gold_reward_max"])
        new_level = player_model.award_xp(conn, player["id"], xp)
        player_model.award_gold(conn, player["id"], gold)

        victory = fmt(f"{result.narrative} +{xp}xp +{gold}g")
        if new_level:
            victory += " " + fmt_level_up(new_level)
        return fmt(victory)

    if result.player_dead:
        losses = player_model.apply_death(conn, player["id"])
        return fmt_death(losses["gold_lost"], losses["xp_lost"])

    # Combat continues
    narrative = fmt_combat_narrative(result.narrative)
    status = fmt_combat_status(
        result.player_hp, player["hp_max"],
        monster["name"], result.monster_hp, monster["hp_max"],
    )
    return fmt(f"{narrative} {status}")


def action_flee(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Attempt to flee from combat."""
    if player["state"] != "combat":
        return fmt("You're not in combat.")

    monster = world_data.get_monster(conn, player["combat_monster_id"])
    if not monster:
        world_mgr.exit_combat(conn, player["id"])
        return fmt("The monster is gone. You're safe.")

    # Cost a dungeon action
    if not player_model.use_dungeon_action(conn, player["id"]):
        return fmt("No dungeon actions left today!")

    result = combat_engine.attempt_flee(
        player_spd=player["spd"],
        player_hp=player["hp"],
        monster_pow=monster["pow"],
        player_def=player["def"],
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
    """Show player stats."""
    return fmt_stats(
        name=player["name"],
        cls=player["class"],
        level=player["level"],
        hp=player["hp"],
        hp_max=player["hp_max"],
        pow_=player["pow"],
        def_=player["def"],
        spd=player["spd"],
        gold=player["gold_carried"],
        xp=player["xp"],
        actions=player["dungeon_actions_remaining"],
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
    return fmt("You return to town. HP restored. Barkeep, Healer, Shop, Bank await.")


def action_help(conn: sqlite3.Connection, player: dict, args: list[str]) -> str:
    """Show available commands."""
    if player["state"] == "town":
        return fmt("ENTER LOOK STATS SHOP HEAL BANK BARKEEP HELP INV")
    if player["state"] == "combat":
        return fmt("FIGHT(F) FLEE STATS LOOK HELP")
    if player["state"] == "dungeon":
        return fmt("N/S/E/W LOOK(L) FIGHT(F) FLEE STATS(ST) INV(I) TOWN HELP(H)")
    return fmt("LOOK STATS HELP")


def action_inventory(
    conn: sqlite3.Connection, player: dict, args: list[str]
) -> str:
    """Show inventory."""
    rows = conn.execute(
        """SELECT i.*, it.name as item_name, it.slot as item_slot
           FROM inventory i JOIN items it ON i.item_id = it.id
           WHERE i.player_id = ?""",
        (player["id"],),
    ).fetchall()

    if not rows:
        return fmt("Inventory: empty. Visit the shop in town.")

    equipped = []
    backpack = []
    for r in rows:
        label = f"{r['item_name']}"
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
