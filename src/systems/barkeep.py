"""
Barkeep system for MMUD — Grist the bartender.
Recap, bard tokens, token spending menu.
All barkeep interactions are free actions.
"""

import random
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import (
    BARD_TOKEN_CAP,
    BARD_TOKEN_RATE,
    DUNGEON_ACTIONS_PER_DAY,
    TEMP_BUFF_AMOUNT,
    TEMP_BUFF_ROUNDS,
)
from src.models import player as player_model
from src.systems import broadcast as broadcast_sys


# ── Grist's Story Pool ─────────────────────────────────────────────────────

_GRIST_STORIES = [
    "Grist: 'Oryn built this place. Sola lit it. Malcor... well.'",
    "Grist: 'They say the builder's hands are in the walls.'",
    "Grist: 'Every epoch the Darkcragg resets. But it remembers.'",
    "Grist: 'The light fades a little more each cycle.'",
    "Grist: 'The Breach opened earlier last time. I'm counting.'",
    "Grist: 'Floor 4 takes more than it gives back.'",
]


# ── Bard Token Accrual ─────────────────────────────────────────────────────


def accrue_tokens(conn: sqlite3.Connection, player_id: int) -> int:
    """Calculate and grant bard tokens based on days since last login.

    Called on each login/action. Grants BARD_TOKEN_RATE per day missed,
    up to BARD_TOKEN_CAP total.

    Returns:
        Number of new tokens granted.
    """
    player = player_model.get_player(conn, player_id)
    if not player:
        return 0

    now = datetime.now(timezone.utc)
    last_login = player.get("last_login")

    if not last_login:
        # First login — grant 1 token
        player_model.update_state(
            conn, player_id,
            bard_tokens=min(BARD_TOKEN_CAP, player["bard_tokens"] + 1),
            last_login=now.isoformat(),
        )
        return 1

    # Parse last login
    try:
        last = datetime.fromisoformat(last_login)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        last = now

    days_away = max(0, (now - last).days)
    if days_away == 0:
        # Same day — update last_login only
        player_model.update_state(conn, player_id, last_login=now.isoformat())
        return 0

    tokens_earned = min(days_away * BARD_TOKEN_RATE, BARD_TOKEN_CAP - player["bard_tokens"])
    tokens_earned = max(0, tokens_earned)

    if tokens_earned > 0:
        player_model.update_state(
            conn, player_id,
            bard_tokens=player["bard_tokens"] + tokens_earned,
            last_login=now.isoformat(),
        )
    else:
        player_model.update_state(conn, player_id, last_login=now.isoformat())

    return tokens_earned


# ── Recap ───────────────────────────────────────────────────────────────────


def get_recap(conn: sqlite3.Connection, player_id: int) -> list[str]:
    """Get Grist's recap for the player. Delegates to broadcast system."""
    return broadcast_sys.generate_recap(conn, player_id)


# ── Token Info ──────────────────────────────────────────────────────────────


def get_token_info(player: dict) -> str:
    """Show token balance and spending menu."""
    tokens = player["bard_tokens"]
    menu_parts = []
    if tokens >= 1:
        menu_parts.append("1:hint/buff")
    if tokens >= 2:
        menu_parts.append("2:reveal/bonus")
    if tokens >= 3:
        menu_parts.append("3:consumable")
    if tokens >= 5:
        menu_parts.append("5:intel")

    if not menu_parts:
        return f"Tokens: {tokens}/{BARD_TOKEN_CAP}. Earn 1/day. Nothing available yet."

    menu = " ".join(menu_parts)
    return f"Tokens: {tokens}/{BARD_TOKEN_CAP}. SPEND <n> <choice>. {menu}"


# ── Token Spending ──────────────────────────────────────────────────────────


def spend_tokens(
    conn: sqlite3.Connection, player_id: int, cost_str: str, choice: str
) -> tuple[bool, str]:
    """Spend bard tokens on a menu option.

    Args:
        conn: Database connection.
        player_id: Player ID.
        cost_str: Token cost as string ("1", "2", "3", "5").
        choice: What to spend on ("hint", "buff", "reveal", "bonus",
                "consumable", "intel").

    Returns:
        (success, message)
    """
    try:
        cost = int(cost_str)
    except ValueError:
        return False, "SPEND <cost> <choice>. Costs: 1, 2, 3, or 5."

    player = player_model.get_player(conn, player_id)
    if not player:
        return False, "Player not found."

    if player["bard_tokens"] < cost:
        return False, f"Need {cost} tokens. You have {player['bard_tokens']}."

    choice = choice.lower()

    # Validate cost+choice combo
    valid = {
        1: {"hint", "buff"},
        2: {"reveal", "bonus"},
        3: {"consumable"},
        5: {"intel"},
    }

    if cost not in valid or choice not in valid[cost]:
        options = ", ".join(valid.get(cost, set())) if cost in valid else "invalid cost"
        return False, f"For {cost} tokens, choose: {options}."

    # Deduct tokens
    conn.execute(
        "UPDATE players SET bard_tokens = bard_tokens - ? WHERE id = ?",
        (cost, player_id),
    )
    conn.commit()

    # Execute the choice
    if choice == "hint":
        return _spend_hint(conn, player)
    elif choice == "buff":
        return _spend_buff(conn, player)
    elif choice == "reveal":
        return _spend_reveal(conn, player)
    elif choice == "bonus":
        return _spend_bonus(conn, player)
    elif choice == "consumable":
        return _spend_consumable(conn, player)
    elif choice == "intel":
        return _spend_intel(conn, player)

    return False, "Unknown choice."


def _spend_hint(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """1 token: Random hint about undiscovered content on current floor."""
    floor = player.get("floor", 1) or 1

    # Look for undiscovered secrets on this floor
    secret = conn.execute(
        """SELECT hint_tier1 FROM secrets
           WHERE floor = ? AND discovered_by IS NULL
           AND id NOT IN (
               SELECT secret_id FROM secret_progress WHERE player_id = ? AND found = 1
           )
           ORDER BY RANDOM() LIMIT 1""",
        (floor, player["id"]),
    ).fetchone()

    if secret and secret["hint_tier1"]:
        return True, f"Grist: '{secret['hint_tier1']}'"

    return True, random.choice(_GRIST_STORIES)


def _spend_buff(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """1 token: +2 to a random stat for 5 combat rounds."""
    stat = random.choice(["pow", "def", "spd"])
    # Store buff in discovery_buffs table
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO discovery_buffs (buff_type, buff_data, activated_by, activated_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            "stat_boost",
            f'{{"stat": "{stat}", "amount": {TEMP_BUFF_AMOUNT}, "rounds": {TEMP_BUFF_ROUNDS}}}',
            player["id"],
            now.isoformat(),
            now.isoformat(),  # Expires tracked by rounds, not time
        ),
    )
    conn.commit()

    # Apply directly to player stats for simplicity
    new_val = player[stat] + TEMP_BUFF_AMOUNT
    player_model.update_state(conn, player["id"], **{stat: new_val})

    return True, f"+{TEMP_BUFF_AMOUNT} {stat.upper()} for {TEMP_BUFF_ROUNDS} rounds!"


def _spend_reveal(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """2 tokens: Reveal traps and ambush monsters on current floor."""
    floor = player.get("floor", 1) or 1

    traps = conn.execute(
        "SELECT name, trap_type FROM rooms WHERE floor = ? AND trap_type IS NOT NULL",
        (floor,),
    ).fetchall()

    if traps:
        trap_list = ", ".join(f"{t['name']}({t['trap_type']})" for t in traps[:3])
        return True, f"Grist reveals F{floor} traps: {trap_list}"

    return True, f"Grist: 'Floor {floor} looks clear of traps.'"


def _spend_bonus(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """2 tokens: +1 dungeon action today."""
    conn.execute(
        "UPDATE players SET dungeon_actions_remaining = dungeon_actions_remaining + 1 WHERE id = ?",
        (player["id"],),
    )
    conn.commit()
    new_acts = player["dungeon_actions_remaining"] + 1
    return True, f"+1 dungeon action! Now {new_acts} remaining."


def _spend_consumable(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """3 tokens: Random item from current-tier pool."""
    floor = player.get("floor", 1) or 1
    tier = min(floor, 5)  # Cap at tier 5

    item = conn.execute(
        """SELECT id, name FROM items WHERE tier = ?
           AND slot IN ('weapon', 'armor', 'trinket')
           ORDER BY RANDOM() LIMIT 1""",
        (tier,),
    ).fetchone()

    if not item:
        return True, "Grist: 'Sorry, nothing in stock right now.'"

    from src.systems.economy import add_item_to_inventory
    ok, msg = add_item_to_inventory(conn, player["id"], item["id"])
    if ok:
        return True, f"Grist hands you a {item['name']}!"
    return True, msg  # Backpack full


def _spend_intel(conn: sqlite3.Connection, player: dict) -> tuple[bool, str]:
    """5 tokens: Exact room + floor of an unclaimed stash."""
    # Find an undiscovered secret with a reward
    secret = conn.execute(
        """SELECT s.floor, r.name as room_name FROM secrets s
           JOIN rooms r ON s.room_id = r.id
           WHERE s.discovered_by IS NULL
           AND s.id NOT IN (
               SELECT secret_id FROM secret_progress WHERE player_id = ? AND found = 1
           )
           ORDER BY RANDOM() LIMIT 1""",
        (player["id"],),
    ).fetchone()

    if secret:
        return True, f"Grist: 'Floor {secret['floor']}, {secret['room_name']}. Don't tell anyone.'"

    return True, random.choice(_GRIST_STORIES)
