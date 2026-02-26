"""Tests for broadcast drain: DCRG outbound, rate limiting, targeted broadcasts."""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, call

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import BROADCAST_DRAIN_BATCH_SIZE, LLM_OUTPUT_CHAR_LIMIT
from src.db.database import init_schema
from src.systems import broadcast as broadcast_sys
from src.transport.broadcast_drain import BroadcastDrain


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
        "INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!abc', 'Tester')"
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, floor, last_login)
           VALUES (1, 1, 'Tester', 'warrior', 20, 20, 3, 2, 1, 'dungeon', 2,
                   '2026-01-01T00:00:00')"""
    )
    conn.execute(
        "INSERT INTO accounts (id, mesh_id, handle) VALUES (2, '!def', 'Other')"
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, floor, last_login)
           VALUES (2, 2, 'Other', 'rogue', 20, 20, 2, 1, 3, 'town', 0,
                   '2026-01-01T00:00:00')"""
    )
    conn.commit()


# ── Basic drain ──


def test_drain_sends_unsent_broadcasts():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "News flash!")
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    sent = drain.drain_once()
    assert sent == 1
    mock_transport.send_broadcast.assert_called_once_with("News flash!")


def test_drain_marks_as_sent():
    conn = make_test_db()
    bid = broadcast_sys.create_broadcast(conn, 1, "Marked test")
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    drain.drain_once()
    row = conn.execute(
        "SELECT dcrg_sent FROM broadcasts WHERE id = ?", (bid,)
    ).fetchone()
    assert row["dcrg_sent"] == 1


def test_drain_skips_already_sent():
    conn = make_test_db()
    bid = broadcast_sys.create_broadcast(conn, 1, "Already sent")
    conn.execute("UPDATE broadcasts SET dcrg_sent = 1 WHERE id = ?", (bid,))
    conn.commit()
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport)
    sent = drain.drain_once()
    assert sent == 0
    mock_transport.send_broadcast.assert_not_called()


def test_drain_empty_queue():
    conn = make_test_db()
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport)
    sent = drain.drain_once()
    assert sent == 0


def test_drain_no_transport():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "No transport")
    drain = BroadcastDrain(conn, None)
    sent = drain.drain_once()
    assert sent == 0


# ── Tier ordering ──


def test_tier1_before_tier2():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 2, "Tier 2 first")
    broadcast_sys.create_broadcast(conn, 1, "Tier 1 second")
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    drain.drain_once()
    calls = mock_transport.send_broadcast.call_args_list
    assert calls[0] == call("Tier 1 second")
    assert calls[1] == call("Tier 2 first")


# ── Batch size limiting ──


def test_drain_respects_batch_size():
    conn = make_test_db()
    for i in range(BROADCAST_DRAIN_BATCH_SIZE + 5):
        broadcast_sys.create_broadcast(conn, 1, f"Event {i}")
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    sent = drain.drain_once()
    assert sent == BROADCAST_DRAIN_BATCH_SIZE


def test_drain_multiple_cycles():
    conn = make_test_db()
    total = BROADCAST_DRAIN_BATCH_SIZE + 2
    for i in range(total):
        broadcast_sys.create_broadcast(conn, 1, f"Event {i}")
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)

    sent1 = drain.drain_once()
    assert sent1 == BROADCAST_DRAIN_BATCH_SIZE

    drain._last_send_time = 0
    sent2 = drain.drain_once()
    assert sent2 == 2


# ── Targeted broadcasts ──


def test_targeted_broadcast_sends_dm():
    conn = make_test_db()
    # Create targeted broadcast for players on floor 2
    conn.execute(
        """INSERT INTO broadcasts (tier, targeted, target_condition, message)
           VALUES (1, 1, '{"floor": 2}', 'Floor 2 alert!')"""
    )
    conn.commit()
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    sent = drain.drain_once()
    assert sent == 1
    # Should DM player 1 (on floor 2), not player 2 (in town, floor 0)
    mock_transport.send_dm.assert_called_once_with("!abc", "Floor 2 alert!")
    mock_transport.send_broadcast.assert_not_called()


def test_targeted_no_qualifying_players():
    conn = make_test_db()
    conn.execute(
        """INSERT INTO broadcasts (tier, targeted, target_condition, message)
           VALUES (1, 1, '{"floor": 99}', 'Nobody here!')"""
    )
    conn.commit()
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    sent = drain.drain_once()
    assert sent == 1  # Still marks as sent
    mock_transport.send_dm.assert_not_called()


def test_targeted_invalid_condition():
    conn = make_test_db()
    conn.execute(
        """INSERT INTO broadcasts (tier, targeted, target_condition, message)
           VALUES (1, 1, 'invalid json', 'Bad condition')"""
    )
    conn.commit()
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    sent = drain.drain_once()
    assert sent == 1  # Still marks as sent


def test_targeted_null_condition_falls_back():
    conn = make_test_db()
    conn.execute(
        """INSERT INTO broadcasts (tier, targeted, target_condition, message)
           VALUES (1, 1, NULL, 'Null condition broadcast')"""
    )
    conn.commit()
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    sent = drain.drain_once()
    assert sent == 1
    mock_transport.send_broadcast.assert_called_once()


# ── Message truncation ──


def test_drain_truncates_long_messages():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "x" * 200)
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    drain.drain_once()
    sent_msg = mock_transport.send_broadcast.call_args[0][0]
    assert len(sent_msg) <= LLM_OUTPUT_CHAR_LIMIT


# ── Pending count ──


def test_pending_count():
    conn = make_test_db()
    assert BroadcastDrain(conn).get_pending_count() == 0
    broadcast_sys.create_broadcast(conn, 1, "Event 1")
    broadcast_sys.create_broadcast(conn, 1, "Event 2")
    assert BroadcastDrain(conn).get_pending_count() == 2


def test_pending_count_excludes_sent():
    conn = make_test_db()
    bid = broadcast_sys.create_broadcast(conn, 1, "Sent event")
    conn.execute("UPDATE broadcasts SET dcrg_sent = 1 WHERE id = ?", (bid,))
    conn.commit()
    broadcast_sys.create_broadcast(conn, 1, "Unsent event")
    assert BroadcastDrain(conn).get_pending_count() == 1


# ── Set transport ──


def test_set_transport():
    conn = make_test_db()
    drain = BroadcastDrain(conn)
    assert drain.dcrg_transport is None
    mock = MagicMock()
    drain.set_transport(mock)
    assert drain.dcrg_transport is mock


# ── Error handling ──


def test_send_error_does_not_crash():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "Error test")
    mock_transport = MagicMock()
    mock_transport.send_broadcast.side_effect = Exception("Send failed")
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    sent = drain.drain_once()
    assert sent == 0  # Failed to send


def test_partial_send_on_error():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "OK message")
    broadcast_sys.create_broadcast(conn, 1, "Fail message")
    mock_transport = MagicMock()
    mock_transport.send_broadcast.side_effect = [None, Exception("Fail")]
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    sent = drain.drain_once()
    assert sent == 1  # First succeeded, second failed
