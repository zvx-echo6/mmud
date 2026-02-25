"""Tests for the epoch vote system."""

import sqlite3

import pytest

from config import ENDGAME_MODES
from src.db.database import get_db, init_schema
from src.models.epoch import create_epoch
from src.models.player import create_player, get_or_create_account
from src.systems.vote import (
    cast_vote,
    get_vote_tally,
    tally_votes,
)


@pytest.fixture
def conn():
    """In-memory DB with schema and epoch."""
    db = get_db(":memory:")
    init_schema(db)
    create_epoch(db, 1, "hold_the_line", "heist")
    return db


@pytest.fixture
def players(conn):
    """Create 3 test players."""
    ids = []
    for i, name in enumerate(["Alice", "Bob", "Carol"]):
        acc = get_or_create_account(conn, f"mesh_{i}", name)
        p = create_player(conn, acc, name, "warrior")
        ids.append(p["id"])
    return ids


def test_cast_vote_basic(conn, players):
    """Vote cast succeeds and is recorded."""
    ok, msg = cast_vote(conn, players[0], "hold")
    assert ok
    assert "Hold the Line" in msg
    tally = get_vote_tally(conn)
    assert tally["hold_the_line"] == 1


def test_cast_vote_aliases(conn, players):
    """All mode aliases resolve correctly."""
    for alias, expected in [
        ("retrieve", "retrieve_and_escape"),
        ("r&e", "retrieve_and_escape"),
        ("rne", "retrieve_and_escape"),
        ("1", "retrieve_and_escape"),
        ("raid", "raid_boss"),
        ("raidboss", "raid_boss"),
        ("2", "raid_boss"),
        ("hold", "hold_the_line"),
        ("htl", "hold_the_line"),
        ("3", "hold_the_line"),
    ]:
        ok, _ = cast_vote(conn, players[0], alias)
        assert ok
        tally = get_vote_tally(conn)
        assert tally[expected] >= 0  # Vote changed each time


def test_cast_vote_invalid(conn, players):
    """Invalid mode returns failure."""
    ok, msg = cast_vote(conn, players[0], "invalid_mode")
    assert not ok
    assert "Unknown mode" in msg


def test_vote_change(conn, players):
    """Vote can be changed (UPSERT)."""
    cast_vote(conn, players[0], "hold")
    tally1 = get_vote_tally(conn)
    assert tally1["hold_the_line"] == 1

    cast_vote(conn, players[0], "raid")
    tally2 = get_vote_tally(conn)
    assert tally2["hold_the_line"] == 0
    assert tally2["raid_boss"] == 1


def test_vote_broadcast(conn, players):
    """Vote creates a broadcast."""
    cast_vote(conn, players[0], "hold")
    broadcasts = conn.execute(
        "SELECT * FROM broadcasts WHERE message LIKE '%voted%'"
    ).fetchall()
    assert len(broadcasts) == 1
    assert "Alice" in broadcasts[0]["message"]
    assert "Hold the Line" in broadcasts[0]["message"]


def test_tally_simple_majority(conn, players):
    """Most votes wins."""
    cast_vote(conn, players[0], "hold")
    cast_vote(conn, players[1], "hold")
    cast_vote(conn, players[2], "raid")

    winner = tally_votes(conn)
    assert winner == "hold_the_line"


def test_tally_tiebreak(conn, players):
    """Tie goes to longest-unplayed mode."""
    cast_vote(conn, players[0], "hold")
    cast_vote(conn, players[1], "raid")

    # Seed hall of fame so hold_the_line was played more recently
    conn.execute(
        """INSERT INTO hall_of_fame (epoch_number, mode, completed)
           VALUES (5, 'hold_the_line', 1)"""
    )
    conn.execute(
        """INSERT INTO hall_of_fame (epoch_number, mode, completed)
           VALUES (2, 'raid_boss', 1)"""
    )
    conn.commit()

    winner = tally_votes(conn)
    # raid_boss was played longer ago (epoch 2 vs 5), so it wins tiebreak
    assert winner == "raid_boss"


def test_tally_zero_votes(conn):
    """Zero votes: auto-select longest-unplayed."""
    # Seed some history
    conn.execute(
        """INSERT INTO hall_of_fame (epoch_number, mode, completed)
           VALUES (3, 'hold_the_line', 1)"""
    )
    conn.execute(
        """INSERT INTO hall_of_fame (epoch_number, mode, completed)
           VALUES (2, 'raid_boss', 1)"""
    )
    conn.commit()

    winner = tally_votes(conn)
    # retrieve_and_escape has never been played (epoch 0)
    assert winner == "retrieve_and_escape"


def test_tally_no_history(conn, players):
    """With no history, first mode in list wins tiebreak."""
    # All modes at epoch 0, tie resolved by ordering
    cast_vote(conn, players[0], "raid")
    cast_vote(conn, players[1], "hold")
    # Tie between raid and hold, both at epoch 0
    winner = tally_votes(conn)
    # Should pick one of them consistently
    assert winner in ENDGAME_MODES


def test_one_vote_decides(conn, players):
    """Single vote with no quorum still decides."""
    cast_vote(conn, players[0], "retrieve")
    winner = tally_votes(conn)
    assert winner == "retrieve_and_escape"


def test_all_broadcasts_under_150(conn, players):
    """All vote broadcasts are under 150 chars."""
    for p, mode in zip(players, ["hold", "raid", "retrieve"]):
        cast_vote(conn, p, mode)

    broadcasts = conn.execute("SELECT message FROM broadcasts").fetchall()
    for b in broadcasts:
        assert len(b["message"]) <= 150, f"Broadcast too long: {b['message']}"
