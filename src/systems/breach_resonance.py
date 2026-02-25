"""
Breach Resonance — Puzzle dungeon within the Breach zone.

No combat focus. 3 breach secrets = completion.
Finding all 3 unlocks a bonus cache in the deepest room.
Knowledge-based, soloable.
"""

import json
import sqlite3
from typing import Optional

from config import BREACH_SECRETS, MSG_CHAR_LIMIT
from src.systems import broadcast as broadcast_sys


# ── State Helpers ─────────────────────────────────────────────────────────


def get_resonance_state(conn: sqlite3.Connection) -> Optional[dict]:
    """Get current resonance state from breach table."""
    row = conn.execute(
        """SELECT active, completed, mini_event
           FROM breach WHERE id = 1"""
    ).fetchone()
    if not row or row["mini_event"] != "resonance":
        return None
    return dict(row)


# ── Puzzle / Secret Interaction ───────────────────────────────────────────


def examine_breach_object(
    conn: sqlite3.Connection, player_id: int, room_id: int
) -> tuple[bool, str]:
    """Examine an object in a breach room to trigger puzzle check.

    Checks if a breach secret exists in this room that the player
    hasn't found yet.

    Returns:
        (found_secret, message)
    """
    state = get_resonance_state(conn)
    if not state or state["completed"]:
        return False, "Nothing of note here."

    # Find undiscovered breach secrets in this room
    secret = conn.execute(
        """SELECT id, name, description, reward_type, reward_data
           FROM secrets
           WHERE room_id = ? AND type = 'breach' AND discovered_by IS NULL
           LIMIT 1""",
        (room_id,),
    ).fetchone()

    if not secret:
        return False, "The resonance reveals nothing new here."

    # Discover the secret
    conn.execute(
        """UPDATE secrets SET discovered_by = ?, discovered_at = datetime('now')
           WHERE id = ?""",
        (player_id, secret["id"]),
    )

    # Track in secret_progress
    conn.execute(
        """INSERT INTO secret_progress (player_id, secret_id, found, found_at)
           VALUES (?, ?, 1, datetime('now'))
           ON CONFLICT(player_id, secret_id)
           DO UPDATE SET found = 1, found_at = datetime('now')""",
        (player_id, secret["id"]),
    )

    # Update player secrets_found count
    conn.execute(
        "UPDATE players SET secrets_found = secrets_found + 1 WHERE id = ?",
        (player_id,),
    )

    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    # Broadcast
    msg = f"! {name} deciphered a Breach resonance: {secret['name']}"
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    conn.commit()

    desc = secret["description"] if secret["description"] else "A pattern resolves."
    return True, desc[:MSG_CHAR_LIMIT]


# ── Completion Check ──────────────────────────────────────────────────────


def check_resonance_complete(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check if all breach secrets have been found.

    Returns:
        (completed, message)
    """
    state = get_resonance_state(conn)
    if not state or state["completed"]:
        return False, ""

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM secrets WHERE type = 'breach'"
    ).fetchone()["cnt"]

    found = conn.execute(
        "SELECT COUNT(*) as cnt FROM secrets WHERE type = 'breach' AND discovered_by IS NOT NULL"
    ).fetchone()["cnt"]

    if total > 0 and found >= total:
        conn.execute(
            "UPDATE breach SET completed = 1, completed_at = datetime('now') WHERE id = 1"
        )

        # Unlock bonus cache in deepest breach room
        _unlock_bonus_cache(conn)

        msg = "The Resonance is understood. The Breach yields its secrets."
        broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
        conn.commit()
        return True, msg

    return False, ""


def _unlock_bonus_cache(conn: sqlite3.Connection) -> None:
    """Create a bonus item cache in the deepest breach room."""
    deepest = conn.execute(
        "SELECT id FROM rooms WHERE is_breach = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not deepest:
        return

    # Create a bonus consumable item in the room
    conn.execute(
        """INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod,
           special, description, floor_source)
           VALUES ('Resonance Crystal', 'consumable', 5, 2, 2, 2,
                   ?, 'A crystal humming with Breach energy. +2 all stats.',
                   0)""",
        (json.dumps({"type": "all_stats_boost", "amount": 2, "duration_hours": 24}),),
    )
    cache_item_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]

    broadcast_sys.create_broadcast(
        conn, 1,
        "A cache of Breach energy crystallizes in the deepest room."[:MSG_CHAR_LIMIT],
    )


# ── Secret Progress ───────────────────────────────────────────────────────


def get_breach_secret_progress(conn: sqlite3.Connection) -> dict:
    """Get breach secret discovery progress."""
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM secrets WHERE type = 'breach'"
    ).fetchone()["cnt"]

    found = conn.execute(
        "SELECT COUNT(*) as cnt FROM secrets WHERE type = 'breach' AND discovered_by IS NOT NULL"
    ).fetchone()["cnt"]

    return {"found": found, "total": total}


# ── Status Display ────────────────────────────────────────────────────────


def format_resonance_status(conn: sqlite3.Connection) -> str:
    """Format resonance status for display."""
    state = get_resonance_state(conn)
    if not state:
        return "No resonance active."

    if state["completed"]:
        return "The Breach Resonance is understood. Bonus cache unlocked."

    progress = get_breach_secret_progress(conn)
    return f"Resonance: {progress['found']}/{progress['total']} secrets deciphered"
