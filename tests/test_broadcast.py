"""Tests for broadcast system: tier 1/2, delivery, recap, seen tracking."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from datetime import datetime, timezone

from config import BROADCAST_CHAR_LIMIT, MSG_CHAR_LIMIT
from src.db.database import init_schema
from src.systems import broadcast as broadcast_sys


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
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1)"""
    )
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!abc', 'Tester')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, last_login)
           VALUES (1, 1, 'Tester', 'warrior', 20, 20, 3, 2, 1, 'town',
                   '2026-01-01T00:00:00')"""
    )
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (2, '!def', 'Other')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, last_login)
           VALUES (2, 2, 'Other', 'rogue', 20, 20, 2, 1, 3, 'town',
                   '2026-01-01T00:00:00')"""
    )
    conn.commit()


# ── create_broadcast ──


def test_create_broadcast_returns_id():
    conn = make_test_db()
    bid = broadcast_sys.create_broadcast(conn, 1, "Test message")
    assert bid >= 1


def test_create_broadcast_stores_tier():
    conn = make_test_db()
    bid = broadcast_sys.create_broadcast(conn, 2, "Level up!")
    row = conn.execute("SELECT tier FROM broadcasts WHERE id = ?", (bid,)).fetchone()
    assert row["tier"] == 2


def test_create_broadcast_truncates_to_200():
    conn = make_test_db()
    long_msg = "x" * 300
    bid = broadcast_sys.create_broadcast(conn, 1, long_msg)
    row = conn.execute("SELECT message FROM broadcasts WHERE id = ?", (bid,)).fetchone()
    assert len(row["message"]) <= BROADCAST_CHAR_LIMIT


# ── get_unseen_broadcasts ──


def test_unseen_returns_new_broadcasts():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "News flash!")
    unseen = broadcast_sys.get_unseen_broadcasts(conn, 1)
    assert len(unseen) == 1
    assert unseen[0]["message"] == "News flash!"


def test_unseen_excludes_seen():
    conn = make_test_db()
    bid = broadcast_sys.create_broadcast(conn, 1, "Old news")
    broadcast_sys.mark_seen(conn, 1, bid)
    unseen = broadcast_sys.get_unseen_broadcasts(conn, 1)
    assert len(unseen) == 0


def test_unseen_filters_by_tier():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "Tier 1")
    broadcast_sys.create_broadcast(conn, 2, "Tier 2")
    tier1 = broadcast_sys.get_unseen_broadcasts(conn, 1, tier=1)
    assert len(tier1) == 1
    assert tier1[0]["message"] == "Tier 1"


# ── deliver_unseen ──


def test_deliver_unseen_returns_tier1():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "Breaking news")
    result = broadcast_sys.deliver_unseen(conn, 1)
    assert result is not None
    assert "Breaking news" in result


def test_deliver_unseen_marks_seen():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "One-time news")
    broadcast_sys.deliver_unseen(conn, 1)
    # Second call should return None
    result = broadcast_sys.deliver_unseen(conn, 1)
    assert result is None


def test_deliver_unseen_under_150():
    conn = make_test_db()
    for i in range(5):
        broadcast_sys.create_broadcast(conn, 1, f"Event {i}")
    result = broadcast_sys.deliver_unseen(conn, 1, limit=3)
    assert result is not None
    assert len(result) <= MSG_CHAR_LIMIT


# ── generate_recap ──


def test_recap_no_unseen():
    conn = make_test_db()
    recap = broadcast_sys.generate_recap(conn, 1)
    assert len(recap) >= 1
    assert "Quiet" in recap[0] or "quiet" in recap[0].lower()


def test_recap_few_broadcasts():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "X Hero fell on Floor 2.")
    broadcast_sys.create_broadcast(conn, 2, "^ Hero reached level 3!")
    recap = broadcast_sys.generate_recap(conn, 1)
    assert len(recap) >= 1
    for msg in recap:
        assert len(msg) <= MSG_CHAR_LIMIT


def test_recap_many_broadcasts_summarizes():
    conn = make_test_db()
    for i in range(10):
        broadcast_sys.create_broadcast(conn, 2, f"^ Player{i} reached level {i}!")
    recap = broadcast_sys.generate_recap(conn, 1)
    assert len(recap) >= 1
    assert len(recap) <= 3
    for msg in recap:
        assert len(msg) <= MSG_CHAR_LIMIT


def test_recap_marks_all_seen():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "News A")
    broadcast_sys.create_broadcast(conn, 2, "News B")
    broadcast_sys.generate_recap(conn, 1)
    unseen = broadcast_sys.get_unseen_broadcasts(conn, 1)
    assert len(unseen) == 0


# ── Helper broadcast functions ──


def test_broadcast_death():
    conn = make_test_db()
    broadcast_sys.broadcast_death(conn, "Hero", 2)
    unseen = broadcast_sys.get_unseen_broadcasts(conn, 1, tier=1)
    assert len(unseen) == 1
    assert "Hero" in unseen[0]["message"]
    assert "Floor 2" in unseen[0]["message"]


def test_broadcast_level_up():
    conn = make_test_db()
    broadcast_sys.broadcast_level_up(conn, "Hero", 5)
    unseen = broadcast_sys.get_unseen_broadcasts(conn, 1, tier=2)
    assert len(unseen) == 1
    assert "level 5" in unseen[0]["message"]


def test_broadcast_bounty_complete():
    conn = make_test_db()
    broadcast_sys.broadcast_bounty_complete(conn, "Hero", "Dragon", ["Hero", "Sidekick"])
    unseen = broadcast_sys.get_unseen_broadcasts(conn, 1, tier=2)
    assert len(unseen) == 1
    assert "Dragon" in unseen[0]["message"]
    assert len(unseen[0]["message"]) <= MSG_CHAR_LIMIT


# ── Per-player isolation ──


def test_broadcasts_per_player():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "Global news")
    # Player 1 sees it
    unseen1 = broadcast_sys.get_unseen_broadcasts(conn, 1)
    assert len(unseen1) == 1
    # Player 2 also sees it
    unseen2 = broadcast_sys.get_unseen_broadcasts(conn, 2)
    assert len(unseen2) == 1
    # Player 1 marks seen — player 2 still unseen
    broadcast_sys.mark_seen(conn, 1, unseen1[0]["id"])
    assert len(broadcast_sys.get_unseen_broadcasts(conn, 1)) == 0
    assert len(broadcast_sys.get_unseen_broadcasts(conn, 2)) == 1
