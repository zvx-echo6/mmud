"""
Epoch vote system for MMUD.
Day 30 triggers the vote. Players vote at the barkeep for next epoch's mode.

Votes are public (broadcast on cast), changeable, and use UPSERT.
Tally: most votes wins. Tiebreak: longest-unplayed. No quorum.
Zero votes: auto-select longest-unplayed.
"""

import json
import sqlite3
from typing import Optional

from config import ENDGAME_MODES, MSG_CHAR_LIMIT
from src.systems import broadcast as broadcast_sys


# Mode aliases for player convenience
MODE_ALIASES = {
    "retrieve": "retrieve_and_escape",
    "r&e": "retrieve_and_escape",
    "rne": "retrieve_and_escape",
    "1": "retrieve_and_escape",
    "raid": "raid_boss",
    "raidboss": "raid_boss",
    "2": "raid_boss",
    "hold": "hold_the_line",
    "htl": "hold_the_line",
    "3": "hold_the_line",
}

# Short display names for broadcasts
MODE_DISPLAY = {
    "retrieve_and_escape": "Retrieve & Escape",
    "raid_boss": "Raid Boss",
    "hold_the_line": "Hold the Line",
}


def cast_vote(
    conn: sqlite3.Connection, player_id: int, mode_input: str
) -> tuple[bool, str]:
    """Cast or change a vote for next epoch's endgame mode.

    Args:
        conn: Database connection.
        player_id: Voting player's ID.
        mode_input: Mode name or alias (retrieve, raid, hold, 1/2/3).

    Returns:
        (success, message)
    """
    mode = _resolve_mode(mode_input)
    if not mode:
        valid = "1)Retrieve 2)Raid Boss 3)Hold the Line"
        return False, f"Unknown mode. VOTE {valid}"

    # UPSERT the vote
    conn.execute(
        """INSERT INTO epoch_votes (player_id, mode, voted_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(player_id)
           DO UPDATE SET mode = ?, voted_at = datetime('now')""",
        (player_id, mode, mode),
    )

    # Get player name for broadcast
    player = conn.execute(
        "SELECT name FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    name = player["name"] if player else "Someone"

    # Get current tally
    tally = get_vote_tally(conn)
    tally_str = _format_tally(tally)

    # Public broadcast
    display = MODE_DISPLAY.get(mode, mode)
    msg = f"V {name} voted {display}. {tally_str}"
    broadcast_sys.create_broadcast(conn, 2, msg[:MSG_CHAR_LIMIT])

    conn.commit()
    return True, f"Vote cast: {display}. {tally_str}"


def get_vote_tally(conn: sqlite3.Connection) -> dict[str, int]:
    """Get current vote counts per mode."""
    rows = conn.execute(
        "SELECT mode, COUNT(*) as cnt FROM epoch_votes GROUP BY mode"
    ).fetchall()
    tally = {m: 0 for m in ENDGAME_MODES}
    for r in rows:
        if r["mode"] in tally:
            tally[r["mode"]] = r["cnt"]
    return tally


def tally_votes(conn: sqlite3.Connection) -> str:
    """Determine the winning mode for next epoch.

    Rules:
    - Most votes wins
    - Tiebreak: longest-unplayed mode
    - Zero votes: auto-select longest-unplayed

    Returns:
        Winning mode name.
    """
    tally = get_vote_tally(conn)
    max_votes = max(tally.values())

    if max_votes == 0:
        return _longest_unplayed(conn)

    # Find all modes tied at max
    tied = [m for m, v in tally.items() if v == max_votes]

    if len(tied) == 1:
        return tied[0]

    # Tiebreak: longest unplayed
    return _longest_unplayed_among(conn, tied)


def _resolve_mode(mode_input: str) -> Optional[str]:
    """Resolve user input to a canonical mode name."""
    cleaned = mode_input.strip().lower().replace(" ", "_")

    # Direct match
    if cleaned in ENDGAME_MODES:
        return cleaned

    # Alias match
    if cleaned in MODE_ALIASES:
        return MODE_ALIASES[cleaned]

    return None


def _longest_unplayed(conn: sqlite3.Connection) -> str:
    """Get the mode that hasn't been played the longest."""
    return _longest_unplayed_among(conn, ENDGAME_MODES)


def _longest_unplayed_among(
    conn: sqlite3.Connection, modes: list[str]
) -> str:
    """Among the given modes, find the one least recently played."""
    # Check hall of fame for last time each mode was used
    last_played = {}
    for mode in modes:
        row = conn.execute(
            """SELECT MAX(epoch_number) as last_epoch
               FROM hall_of_fame WHERE mode = ?""",
            (mode,),
        ).fetchone()
        last_played[mode] = row["last_epoch"] if row and row["last_epoch"] else 0

    # Return mode with lowest last_epoch (longest ago)
    return min(modes, key=lambda m: last_played.get(m, 0))


def _format_tally(tally: dict[str, int]) -> str:
    """Format vote tally for display."""
    parts = []
    for mode in ENDGAME_MODES:
        short = MODE_DISPLAY.get(mode, mode)
        # Use abbreviations to fit 150 chars
        abbrev = {"Retrieve & Escape": "R&E", "Raid Boss": "Raid", "Hold the Line": "HtL"}
        parts.append(f"{abbrev.get(short, short)}:{tally.get(mode, 0)}")
    return " ".join(parts)
