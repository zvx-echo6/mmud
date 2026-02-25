"""
Breach-Endgame interaction logic.

Regardless of mini-event type, breach completion benefits the active endgame:
  - R&E: Breach shortcut (floors 2↔3) as alternate escape route.
  - Raid Boss: +20% damage buff item granted on completion.
  - HtL: Breach rooms count as bonus territory for floors 2 and 3.
"""

import json
import sqlite3
from typing import Optional

from config import MSG_CHAR_LIMIT, NUM_FLOORS
from src.models.epoch import get_epoch
from src.systems import broadcast as broadcast_sys


def apply_breach_completion_reward(conn: sqlite3.Connection) -> Optional[str]:
    """Apply endgame-specific reward when breach is completed.

    Called after any breach mini-event completes.

    Returns:
        Reward message or None.
    """
    epoch = get_epoch(conn)
    if not epoch:
        return None

    mode = epoch["endgame_mode"]

    if mode == "raid_boss":
        return _grant_raid_damage_buff(conn)
    elif mode == "hold_the_line":
        return _credit_htl_bonus_territory(conn)
    elif mode == "retrieve_and_escape":
        return _note_rne_shortcut(conn)

    return None


def _grant_raid_damage_buff(conn: sqlite3.Connection) -> str:
    """Grant +20% raid boss damage buff to all active players."""
    # Create a buff item
    special = json.dumps({
        "type": "raid_damage_boost",
        "multiplier": 1.20,
        "permanent": True,
    })
    conn.execute(
        """INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod,
           special, description, floor_source)
           VALUES ('Breach Resonance Shard', 'trinket', 5, 0, 0, 0,
                   ?, 'Breach energy. +20% damage vs Raid Boss.', 0)""",
        (special,),
    )
    item_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]

    # Give to all active dungeon players
    players = conn.execute(
        "SELECT id FROM players WHERE state IN ('dungeon', 'town')"
    ).fetchall()

    for p in players:
        conn.execute(
            "INSERT INTO inventory (player_id, item_id, equipped) VALUES (?, ?, 0)",
            (p["id"], item_id),
        )

    msg = "Breach complete! All delvers gain +20% vs the Raid Boss."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return msg


def _credit_htl_bonus_territory(conn: sqlite3.Connection) -> str:
    """Credit cleared breach rooms toward both floors 2 and 3."""
    cleared = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_breach = 1 AND htl_cleared = 1"
    ).fetchone()["cnt"]

    msg = f"Breach secured! {cleared} bonus rooms count for Floors 2-3."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return msg


def _note_rne_shortcut(conn: sqlite3.Connection) -> str:
    """Note that the breach shortcut is available for R&E carriers."""
    msg = "Breach open! Carriers can path through Floors 2-3 shortcut."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return msg


# ── HtL Bonus Territory Query ────────────────────────────────────────────


def get_htl_bonus_from_breach(conn: sqlite3.Connection) -> dict:
    """Get cleared breach room count as bonus for HtL floors 2-3.

    Returns:
        Dict with bonus_rooms count and which floors benefit.
    """
    epoch = get_epoch(conn)
    if not epoch or epoch["endgame_mode"] != "hold_the_line":
        return {"bonus_rooms": 0, "floors": []}

    cleared = conn.execute(
        "SELECT COUNT(*) as cnt FROM rooms WHERE is_breach = 1 AND htl_cleared = 1"
    ).fetchone()["cnt"]

    return {"bonus_rooms": cleared, "floors": [2, 3]}


def has_raid_damage_buff(conn: sqlite3.Connection, player_id: int) -> bool:
    """Check if a player has the breach raid damage buff."""
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM inventory i
           JOIN items it ON i.item_id = it.id
           WHERE i.player_id = ?
           AND it.special LIKE '%raid_damage_boost%'""",
        (player_id,),
    ).fetchone()
    return row["cnt"] > 0
