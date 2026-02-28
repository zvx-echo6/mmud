"""
Social systems for MMUD.
Player messages (15-char room graffiti), town bulletin board, who list.
"""

import sqlite3
from datetime import datetime, timezone

from config import BOARD_LIST_COUNT, BOARD_POST_CHAR_LIMIT, MSG_CHAR_LIMIT, PLAYER_MSG_CHAR_LIMIT


# ── Player Messages ─────────────────────────────────────────────────────────


def leave_message(
    conn: sqlite3.Connection, player_id: int, room_id: int, text: str
) -> tuple[bool, str]:
    """Leave a message in the current room.

    One message per player per room (overwrites). Costs 1 social action.

    Returns:
        (success, message)
    """
    if not text or not text.strip():
        return False, "MSG <text> (max 15 chars)."

    text = text.strip()[:PLAYER_MSG_CHAR_LIMIT]

    # Upsert: one message per player per room
    existing = conn.execute(
        "SELECT id FROM player_messages WHERE player_id = ? AND room_id = ?",
        (player_id, room_id),
    ).fetchone()

    now = datetime.now(timezone.utc).isoformat()
    if existing:
        conn.execute(
            "UPDATE player_messages SET message = ?, helpful_votes = 0, created_at = ? WHERE id = ?",
            (text, now, existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO player_messages (player_id, room_id, message, created_at)
               VALUES (?, ?, ?, ?)""",
            (player_id, room_id, text, now),
        )
    conn.commit()
    return True, f"Message left: '{text}'"


def get_room_messages(
    conn: sqlite3.Connection, room_id: int, exclude_player: int = 0
) -> list[dict]:
    """Get messages in a room, excluding the viewing player's own message."""
    rows = conn.execute(
        """SELECT pm.message, pm.helpful_votes, p.name
           FROM player_messages pm
           JOIN players p ON pm.player_id = p.id
           WHERE pm.room_id = ? AND pm.player_id != ?
           ORDER BY pm.helpful_votes DESC, pm.created_at DESC
           LIMIT 3""",
        (room_id, exclude_player),
    ).fetchall()
    return [dict(r) for r in rows]


def format_room_messages(messages: list[dict]) -> str:
    """Format room messages for display, appended to room description."""
    if not messages:
        return ""

    parts = []
    for m in messages[:2]:  # Max 2 to keep under 175 chars
        votes = f"(+{m['helpful_votes']})" if m["helpful_votes"] > 0 else ""
        parts.append(f"'{m['message']}'-{m['name']}{votes}")

    return " | ".join(parts)


def vote_helpful(
    conn: sqlite3.Connection, player_id: int, room_id: int
) -> tuple[bool, str]:
    """Rate the most recent message in the current room as helpful."""
    msg = conn.execute(
        """SELECT pm.id, pm.player_id, pm.message, p.name
           FROM player_messages pm
           JOIN players p ON pm.player_id = p.id
           WHERE pm.room_id = ? AND pm.player_id != ?
           ORDER BY pm.created_at DESC LIMIT 1""",
        (room_id, player_id),
    ).fetchone()

    if not msg:
        return False, "No messages here to rate."

    conn.execute(
        "UPDATE player_messages SET helpful_votes = helpful_votes + 1 WHERE id = ?",
        (msg["id"],),
    )
    conn.commit()
    return True, f"Marked '{msg['message']}' by {msg['name']} as helpful."


# ── Town Bulletin Board ────────────────────────────────────────────────────


def post_to_board(
    conn: sqlite3.Connection, player_id: int, player_name: str, message: str
) -> tuple[bool, str]:
    """Post a message to the town bulletin board.

    Truncates to BOARD_POST_CHAR_LIMIT (140 chars). Costs 1 social action.

    Returns:
        (success, response_message)
    """
    if not message or not message.strip():
        return False, "POST <text> to write on the board."

    text = message.strip()[:BOARD_POST_CHAR_LIMIT]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO town_board (player_id, message, created_at) VALUES (?, ?, ?)",
        (player_id, text, now),
    )
    conn.commit()
    return True, f"Posted to board: '{text[:40]}{'...' if len(text) > 40 else ''}'"


def get_board_posts(
    conn: sqlite3.Connection, limit: int = BOARD_LIST_COUNT, offset: int = 0
) -> list[dict]:
    """Get board posts with player names, ascending by id.

    Args:
        limit: Max posts to return.
        offset: Number of rows to skip from the start.

    Returns:
        List of dicts with id, player_name, message, created_at.
    """
    rows = conn.execute(
        """SELECT tb.id, tb.message, tb.created_at, p.name as player_name
           FROM town_board tb
           JOIN players p ON tb.player_id = p.id
           ORDER BY tb.id ASC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def get_board_post(
    conn: sqlite3.Connection, post_number: int
) -> tuple[bool, str]:
    """Get a single post by its sequential number (1-based, oldest = #1).

    Returns:
        (success, formatted_post_string)
    """
    if post_number < 1:
        return False, "Invalid post number."

    # Post number N = the Nth row when ordered by id ASC (0-indexed offset = N-1)
    row = conn.execute(
        """SELECT tb.id, tb.message, tb.created_at, p.name as player_name
           FROM town_board tb
           JOIN players p ON tb.player_id = p.id
           ORDER BY tb.id ASC
           LIMIT 1 OFFSET ?""",
        (post_number - 1,),
    ).fetchone()

    if not row:
        return False, f"Post #{post_number} not found."

    # Calculate epoch day from created_at
    day_str = ""
    try:
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        day_str = f"D{created.day}"
    except Exception:
        day_str = ""

    return True, f"#{post_number} {row['player_name']} ({day_str}): {row['message']}"


def get_board_count(conn: sqlite3.Connection) -> int:
    """Get total number of posts on the board."""
    row = conn.execute("SELECT COUNT(*) as cnt FROM town_board").fetchone()
    return row["cnt"] if row else 0


def format_board_listing(
    posts: list[dict], start_num: int, total: int
) -> str:
    """Format board posts for compact mesh display.

    Template: Board S-E/T: N.Name:preview N.Name:preview ...

    Args:
        posts: List of post dicts from get_board_posts.
        start_num: The 1-based number of the first post in the list.
        total: Total post count.

    Returns:
        Formatted string fitting 175-char target.
    """
    if not posts:
        return "Board is empty. POST <text> to write."

    end_num = start_num + len(posts) - 1
    header = f"Board {start_num}-{end_num}/{total}: "

    entries = []
    for i, p in enumerate(posts):
        num = start_num + i
        name = p["player_name"][:6]
        # Truncate message preview to keep compact
        preview = p["message"][:20]
        entries.append(f"{num}.{name}:{preview}")

    result = header + " ".join(entries)
    return result


# ── Who List ────────────────────────────────────────────────────────────────


def get_who_list(conn: sqlite3.Connection) -> list[dict]:
    """Get players who have been active today (have a last_login)."""
    rows = conn.execute(
        """SELECT name, level, floor, state
           FROM players
           WHERE last_login IS NOT NULL
           ORDER BY level DESC, name ASC
           LIMIT 10""",
    ).fetchall()
    return [dict(r) for r in rows]


def format_who_list(players: list[dict]) -> str:
    """Format the who list for display."""
    if not players:
        return "Nobody around. The dungeon echoes."

    parts = []
    for p in players:
        loc = f"F{p['floor']}" if p["state"] == "dungeon" else "Town"
        parts.append(f"{p['name']}(Lv{p['level']},{loc})")

    return "Online: " + " ".join(parts)
