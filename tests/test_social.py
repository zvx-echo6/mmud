"""Tests for social systems: player messages, mail, who list, action handlers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import MSG_CHAR_LIMIT, PLAYER_MSG_CHAR_LIMIT
from src.core.engine import GameEngine
from src.db.database import init_schema
from src.systems import social as social_sys


def make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 5)"""
    )
    # Rooms
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Hub', 'Central hub. [n]', 'Hub. [n]', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (2, 1, 'Arena', 'An arena. [s]', 'Arena. [s]', 0)"""
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (1, 2, 'n')"
    )
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (2, 1, 's')"
    )
    # Players
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!abc', 'Hero')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, room_id, floor, social_actions_remaining, last_login)
           VALUES (1, 1, 'Hero', 'warrior', 20, 20, 3, 2, 1, 'dungeon', 1, 1, 2,
                   '2026-01-01T00:00:00')"""
    )
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (2, '!def', 'Sidekick')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, room_id, floor, social_actions_remaining, last_login, level)
           VALUES (2, 2, 'Sidekick', 'rogue', 18, 20, 2, 1, 3, 'dungeon', 2, 1, 2,
                   '2026-01-01T00:00:00', 3)"""
    )
    conn.commit()


# ── Player Messages ──


def test_leave_message():
    conn = make_test_db()
    ok, msg = social_sys.leave_message(conn, 1, 1, "beware trap")
    assert ok
    assert "beware trap" in msg
    assert len(msg) <= MSG_CHAR_LIMIT


def test_leave_message_truncates():
    conn = make_test_db()
    ok, msg = social_sys.leave_message(conn, 1, 1, "this is way too long for the limit")
    assert ok
    # Check DB — message should be truncated to 15 chars
    row = conn.execute("SELECT message FROM player_messages WHERE player_id = 1").fetchone()
    assert len(row["message"]) <= PLAYER_MSG_CHAR_LIMIT


def test_leave_message_overwrites():
    conn = make_test_db()
    social_sys.leave_message(conn, 1, 1, "first msg")
    social_sys.leave_message(conn, 1, 1, "second msg")
    rows = conn.execute(
        "SELECT message FROM player_messages WHERE player_id = 1 AND room_id = 1"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["message"] == "second msg"


def test_leave_message_empty():
    conn = make_test_db()
    ok, msg = social_sys.leave_message(conn, 1, 1, "")
    assert not ok


def test_get_room_messages_excludes_self():
    conn = make_test_db()
    social_sys.leave_message(conn, 1, 1, "my msg")
    social_sys.leave_message(conn, 2, 1, "their msg")
    msgs = social_sys.get_room_messages(conn, 1, exclude_player=1)
    assert len(msgs) == 1
    assert msgs[0]["message"] == "their msg"


def test_format_room_messages():
    conn = make_test_db()
    social_sys.leave_message(conn, 2, 1, "watch out")
    msgs = social_sys.get_room_messages(conn, 1, exclude_player=1)
    formatted = social_sys.format_room_messages(msgs)
    assert "watch out" in formatted
    assert "Sidekick" in formatted


def test_format_room_messages_empty():
    conn = make_test_db()
    msgs = social_sys.get_room_messages(conn, 1)
    formatted = social_sys.format_room_messages(msgs)
    assert formatted == ""


# ── Helpful Votes ──


def test_vote_helpful():
    conn = make_test_db()
    social_sys.leave_message(conn, 2, 1, "good tip")
    ok, msg = social_sys.vote_helpful(conn, 1, 1)
    assert ok
    assert "helpful" in msg.lower()
    assert len(msg) <= MSG_CHAR_LIMIT


def test_vote_helpful_no_messages():
    conn = make_test_db()
    ok, msg = social_sys.vote_helpful(conn, 1, 1)
    assert not ok
    assert "No messages" in msg


def test_vote_helpful_increments():
    conn = make_test_db()
    social_sys.leave_message(conn, 2, 1, "hint here")
    social_sys.vote_helpful(conn, 1, 1)
    social_sys.vote_helpful(conn, 1, 1)  # Can vote multiple times (intentional)
    row = conn.execute(
        "SELECT helpful_votes FROM player_messages WHERE player_id = 2 AND room_id = 1"
    ).fetchone()
    assert row["helpful_votes"] == 2


# ── Mail System ──


def test_send_mail():
    conn = make_test_db()
    ok, msg = social_sys.send_mail(conn, 1, "Sidekick", "hello friend")
    assert ok
    assert "Mail sent" in msg
    assert len(msg) <= MSG_CHAR_LIMIT


def test_send_mail_unknown_player():
    conn = make_test_db()
    ok, msg = social_sys.send_mail(conn, 1, "Nobody", "hi")
    assert not ok
    assert "not found" in msg


def test_send_mail_self():
    conn = make_test_db()
    ok, msg = social_sys.send_mail(conn, 1, "Hero", "talking to myself")
    assert not ok
    assert "yourself" in msg.lower()


def test_send_mail_empty():
    conn = make_test_db()
    ok, msg = social_sys.send_mail(conn, 1, "Sidekick", "   ")
    assert not ok


def test_inbox_stats():
    conn = make_test_db()
    social_sys.send_mail(conn, 1, "Sidekick", "msg 1")
    social_sys.send_mail(conn, 1, "Sidekick", "msg 2")
    unread, total = social_sys.get_inbox(conn, 2)
    assert unread == 2
    assert total == 2


def test_read_oldest_unread():
    conn = make_test_db()
    social_sys.send_mail(conn, 1, "Sidekick", "first")
    social_sys.send_mail(conn, 1, "Sidekick", "second")
    ok, msg = social_sys.read_oldest_unread(conn, 2)
    assert ok
    assert "first" in msg
    assert "Hero" in msg
    assert len(msg) <= MSG_CHAR_LIMIT


def test_read_marks_as_read():
    conn = make_test_db()
    social_sys.send_mail(conn, 1, "Sidekick", "only one")
    social_sys.read_oldest_unread(conn, 2)
    unread, total = social_sys.get_inbox(conn, 2)
    assert unread == 0
    assert total == 1


def test_read_no_unread():
    conn = make_test_db()
    ok, msg = social_sys.read_oldest_unread(conn, 1)
    assert not ok
    assert "No unread" in msg


# ── Who List ──


def test_who_list():
    conn = make_test_db()
    players = social_sys.get_who_list(conn)
    assert len(players) == 2


def test_format_who_list():
    conn = make_test_db()
    players = social_sys.get_who_list(conn)
    text = social_sys.format_who_list(players)
    assert "Hero" in text
    assert "Sidekick" in text
    assert len(text) <= MSG_CHAR_LIMIT


def test_format_who_list_empty():
    conn = make_test_db()
    conn.execute("UPDATE players SET last_login = NULL")
    conn.commit()
    players = social_sys.get_who_list(conn)
    text = social_sys.format_who_list(players)
    assert "Nobody" in text


# ── Integration via Engine ──


def _make_engine_db() -> tuple[sqlite3.Connection, GameEngine]:
    conn = make_test_db()
    # Add a weak monster for combat
    conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (2, 'Test Rat', 1, 1, 1, 0, 0, 10, 5, 5, 1)"""
    )
    conn.commit()
    engine = GameEngine(conn)
    return conn, engine


def test_engine_who_command():
    conn, engine = _make_engine_db()
    resp = engine.process_message("!abc", "Hero", "who")
    assert resp is not None
    assert "Hero" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_engine_mail_inbox():
    conn, engine = _make_engine_db()
    resp = engine.process_message("!abc", "Hero", "mail")
    assert resp is not None
    assert "Mail" in resp or "unread" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_engine_bounty_command():
    conn, engine = _make_engine_db()
    # No bounties in engine test db
    resp = engine.process_message("!abc", "Hero", "bounty")
    assert resp is not None
    assert len(resp) <= MSG_CHAR_LIMIT


def test_engine_msg_command():
    conn, engine = _make_engine_db()
    resp = engine.process_message("!abc", "Hero", "msg beware")
    assert resp is not None
    assert len(resp) <= MSG_CHAR_LIMIT


def test_engine_msg_costs_social_action():
    conn, engine = _make_engine_db()
    engine.process_message("!abc", "Hero", "msg trap here")
    p = conn.execute("SELECT social_actions_remaining FROM players WHERE id = 1").fetchone()
    assert p["social_actions_remaining"] == 1  # Was 2, now 1


def test_engine_mail_send_costs_social_action():
    conn, engine = _make_engine_db()
    engine.process_message("!abc", "Hero", "mail Sidekick hello there")
    p = conn.execute("SELECT social_actions_remaining FROM players WHERE id = 1").fetchone()
    assert p["social_actions_remaining"] == 1


def test_engine_read_command():
    conn, engine = _make_engine_db()
    social_sys.send_mail(conn, 2, "Hero", "test mail")
    resp = engine.process_message("!abc", "Hero", "read")
    assert resp is not None
    assert "test mail" in resp or "Sidekick" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_engine_helpful_in_town():
    conn, engine = _make_engine_db()
    # Set player to town
    conn.execute("UPDATE players SET state = 'town', room_id = NULL WHERE id = 1")
    conn.commit()
    resp = engine.process_message("!abc", "Hero", "helpful")
    assert "No messages" in resp


def test_all_responses_under_150():
    """Meta-test: verify all social command responses are under 150 chars."""
    conn, engine = _make_engine_db()
    commands = [
        "who", "mail", "bounty", "read", "helpful",
        "mail Sidekick hi there", "msg beware",
    ]
    for cmd in commands:
        resp = engine.process_message("!abc", "Hero", cmd)
        if resp:
            assert len(resp) <= MSG_CHAR_LIMIT, f"'{cmd}' response too long: {len(resp)}"
