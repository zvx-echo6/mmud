"""
Social systems for MMUD.
Player messages (15-char room graffiti), mail, who list.
"""

import sqlite3
from datetime import datetime, timezone

from config import MSG_CHAR_LIMIT, PLAYER_MSG_CHAR_LIMIT


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


# ── Mail System ─────────────────────────────────────────────────────────────


def get_inbox(conn: sqlite3.Connection, player_id: int) -> tuple[int, int]:
    """Get inbox stats: (unread_count, total_count)."""
    row = conn.execute(
        """SELECT
           COUNT(*) as total,
           SUM(CASE WHEN read = 0 THEN 1 ELSE 0 END) as unread
           FROM mail WHERE to_player_id = ?""",
        (player_id,),
    ).fetchone()
    return (row["unread"] or 0, row["total"] or 0)


def read_oldest_unread(
    conn: sqlite3.Connection, player_id: int
) -> tuple[bool, str]:
    """Read the oldest unread mail.

    Returns:
        (success, message_content)
    """
    row = conn.execute(
        """SELECT m.id, m.message, p.name as from_name
           FROM mail m
           JOIN players p ON m.from_player_id = p.id
           WHERE m.to_player_id = ? AND m.read = 0
           ORDER BY m.sent_at ASC LIMIT 1""",
        (player_id,),
    ).fetchone()

    if not row:
        return False, "No unread mail."

    conn.execute("UPDATE mail SET read = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    return True, f"From {row['from_name']}: {row['message']}"


def send_mail(
    conn: sqlite3.Connection, from_id: int, to_name: str, message: str
) -> tuple[bool, str]:
    """Send mail to another player. Costs 1 social action.

    Returns:
        (success, message)
    """
    if not message.strip():
        return False, "MAIL <player> <message>"

    # Find recipient by name
    recipient = conn.execute(
        "SELECT id, name FROM players WHERE LOWER(name) = LOWER(?)",
        (to_name.strip(),),
    ).fetchone()

    if not recipient:
        return False, f"Player '{to_name}' not found."

    if recipient["id"] == from_id:
        return False, "Can't mail yourself."

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO mail (from_player_id, to_player_id, message, sent_at)
           VALUES (?, ?, ?, ?)""",
        (from_id, recipient["id"], message[:MSG_CHAR_LIMIT], now),
    )
    conn.commit()
    return True, f"Mail sent to {recipient['name']}."


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
