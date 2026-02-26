"""
Day tick system for MMUD.
Runs once per real-world day to advance epoch state.

Daily actions:
  1. Increment epoch day counter
  2. Reset all player action budgets
  3. Grant passive bard tokens
  4. Apply bounty HP regen
  5. Rotate bounty board (activate new bounties)
  6. Days 12-13: breach foreshadowing broadcasts
  7. Day 15: open the breach
  8. Day 30: trigger epoch vote
"""

import sqlite3
from datetime import datetime, timedelta

from config import (
    BARD_TOKEN_CAP,
    BARD_TOKEN_RATE,
    BOUNTY_ACTIVE_MAX,
    BOUNTY_REGEN_INTERVAL_HOURS,
    BOUNTY_REGEN_RATE,
    BREACH_DAY,
    DUNGEON_ACTIONS_PER_DAY,
    EPOCH_DAYS,
    LLM_OUTPUT_CHAR_LIMIT,
    MSG_CHAR_LIMIT,
    RESOURCE_REGEN_DAYTICK,
    SOCIAL_ACTIONS_PER_DAY,
    SPECIAL_ACTIONS_PER_DAY,
)
from src.models.epoch import advance_day, get_epoch


def run_day_tick(conn: sqlite3.Connection) -> dict:
    """Execute all daily maintenance tasks.

    Args:
        conn: Database connection.

    Returns:
        Stats dict with what happened.
    """
    epoch = get_epoch(conn)
    if not epoch:
        return {"error": "No epoch found"}

    old_day = epoch["day_number"]
    new_day = advance_day(conn)

    stats = {
        "old_day": old_day,
        "new_day": new_day,
        "actions_reset": 0,
        "tokens_granted": 0,
        "bounties_regened": 0,
        "bounties_activated": 0,
        "breach_opened": False,
        "vote_triggered": False,
    }

    # 1. Reset action budgets
    stats["actions_reset"] = _reset_action_budgets(conn)

    # 2. Grant bard tokens
    stats["tokens_granted"] = _grant_bard_tokens(conn)

    # 3. Bounty regen
    stats["bounties_regened"] = _apply_bounty_regen(conn)

    # 4. Bounty rotation
    stats["bounties_activated"] = _rotate_bounties(conn, new_day)

    # 5. Breach foreshadowing (days 12-13)
    if new_day in (BREACH_DAY - 3, BREACH_DAY - 2):
        _broadcast_breach_foreshadow(conn, new_day)

    # 6. Breach opening (day 15)
    if new_day == BREACH_DAY:
        _open_breach(conn)
        stats["breach_opened"] = True

    # 7. Epoch vote (day 30)
    if new_day == EPOCH_DAYS:
        _trigger_epoch_vote(conn)
        stats["vote_triggered"] = True

    conn.commit()
    return stats


def _reset_action_budgets(conn: sqlite3.Connection) -> int:
    """Reset all player daily action budgets and regen resource. Returns count of players reset."""
    cursor = conn.execute(
        """UPDATE players SET
           dungeon_actions_remaining = ?,
           social_actions_remaining = ?,
           special_actions_remaining = ?,
           resource = MIN(resource + ?, resource_max)""",
        (DUNGEON_ACTIONS_PER_DAY, SOCIAL_ACTIONS_PER_DAY, SPECIAL_ACTIONS_PER_DAY,
         RESOURCE_REGEN_DAYTICK),
    )
    return cursor.rowcount


def _grant_bard_tokens(conn: sqlite3.Connection) -> int:
    """Grant passive bard tokens. Returns count of players granted."""
    cursor = conn.execute(
        """UPDATE players SET bard_tokens = MIN(bard_tokens + ?, ?)""",
        (BARD_TOKEN_RATE, BARD_TOKEN_CAP),
    )
    return cursor.rowcount


def _apply_bounty_regen(conn: sqlite3.Connection) -> int:
    """Apply HP regen to active bounty monsters. Returns count regened."""
    # Get active bounty monsters that haven't been killed
    bounties = conn.execute(
        """SELECT b.id, b.target_monster_id, m.hp, m.hp_max
           FROM bounties b
           JOIN monsters m ON b.target_monster_id = m.id
           WHERE b.active = 1 AND b.completed = 0 AND m.hp > 0"""
    ).fetchall()

    count = 0
    for b in bounties:
        regen_amount = max(1, int(b["hp_max"] * BOUNTY_REGEN_RATE))
        new_hp = min(b["hp"] + regen_amount, b["hp_max"])
        if new_hp != b["hp"]:
            conn.execute(
                "UPDATE monsters SET hp = ? WHERE id = ?",
                (new_hp, b["target_monster_id"]),
            )
            count += 1

    return count


def _rotate_bounties(conn: sqlite3.Connection, day: int) -> int:
    """Activate new bounties that become available on this day.

    Returns count of newly activated bounties.
    """
    # Count currently active bounties
    active = conn.execute(
        "SELECT COUNT(*) as cnt FROM bounties WHERE active = 1 AND completed = 0"
    ).fetchone()

    slots = max(0, BOUNTY_ACTIVE_MAX - active["cnt"])
    if slots <= 0:
        return 0

    # Find bounties available on this day that aren't active yet
    available = conn.execute(
        """SELECT id FROM bounties
           WHERE active = 0 AND completed = 0 AND available_from_day <= ?
           ORDER BY available_from_day ASC
           LIMIT ?""",
        (day, slots),
    ).fetchall()

    for b in available:
        conn.execute("UPDATE bounties SET active = 1 WHERE id = ?", (b["id"],))

    return len(available)


def _broadcast_breach_foreshadow(conn: sqlite3.Connection, day: int) -> None:
    """Insert breach foreshadowing broadcast."""
    epoch = get_epoch(conn)
    if not epoch:
        return

    if day == BREACH_DAY - 3:
        msg = "The dungeon trembles. Something stirs between the floors."
    else:
        msg = "Cracks widen in the walls. The Breach approaches."

    conn.execute(
        "INSERT INTO broadcasts (tier, message) VALUES (1, ?)",
        (msg[:MSG_CHAR_LIMIT],),
    )


def _open_breach(conn: sqlite3.Connection) -> None:
    """Open the breach zone on day 15."""
    conn.execute("UPDATE epoch SET breach_open = 1 WHERE id = 1")
    conn.execute("UPDATE breach SET active = 1 WHERE id = 1")

    msg = "THE BREACH HAS OPENED. New paths between Floors 2-3."
    conn.execute(
        "INSERT INTO broadcasts (tier, message) VALUES (1, ?)",
        (msg[:MSG_CHAR_LIMIT],),
    )


def _trigger_epoch_vote(conn: sqlite3.Connection) -> None:
    """Trigger the epoch vote on day 30."""
    msg = "Day 30. Vote for the next endgame: vote <mode>"
    conn.execute(
        "INSERT INTO broadcasts (tier, message) VALUES (1, ?)",
        (msg[:MSG_CHAR_LIMIT],),
    )
