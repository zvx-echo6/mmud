"""Tests for message logger: writes, JSON metadata, error resilience, pruning, types."""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from src.db.database import init_schema
from src.transport.message_logger import log_message, prune_old_logs


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
           state, last_login)
           VALUES (1, 1, 'Tester', 'warrior', 20, 20, 3, 2, 1, 'town',
                   '2026-01-01T00:00:00')"""
    )
    conn.commit()


# ── Basic writes ──


def test_log_message_basic_write():
    conn = make_test_db()
    log_message(conn, "EMBR", "inbound", "look", "command", sender_id="!abc")
    row = conn.execute("SELECT * FROM message_log").fetchone()
    assert row is not None
    assert row["node"] == "EMBR"
    assert row["direction"] == "inbound"
    assert row["message"] == "look"
    assert row["message_type"] == "command"
    assert row["sender_id"] == "!abc"


def test_log_message_all_fields():
    conn = make_test_db()
    log_message(
        conn, "GRST", "outbound", "Welcome!", "npc_llm",
        sender_id="!abc", sender_name="Tester",
        recipient_id="!abc", player_id=1,
        metadata={"llm_latency_ms": 42.5},
    )
    row = conn.execute("SELECT * FROM message_log").fetchone()
    assert row["node"] == "GRST"
    assert row["direction"] == "outbound"
    assert row["message"] == "Welcome!"
    assert row["message_type"] == "npc_llm"
    assert row["sender_id"] == "!abc"
    assert row["sender_name"] == "Tester"
    assert row["recipient_id"] == "!abc"
    assert row["player_id"] == 1
    assert row["timestamp"] is not None


def test_log_message_timestamp_set():
    conn = make_test_db()
    log_message(conn, "EMBR", "inbound", "test", "command")
    row = conn.execute("SELECT timestamp FROM message_log").fetchone()
    assert row["timestamp"] is not None


def test_log_message_optional_fields_null():
    conn = make_test_db()
    log_message(conn, "DCRG", "outbound", "rejected", "dcrg_rejection")
    row = conn.execute("SELECT * FROM message_log").fetchone()
    assert row["sender_id"] is None
    assert row["sender_name"] is None
    assert row["recipient_id"] is None
    assert row["player_id"] is None
    assert row["metadata"] is None


def test_log_multiple_messages():
    conn = make_test_db()
    log_message(conn, "EMBR", "inbound", "look", "command")
    log_message(conn, "EMBR", "outbound", "You see...", "response")
    log_message(conn, "DCRG", "outbound", "News!", "broadcast_tier1")
    count = conn.execute("SELECT COUNT(*) as cnt FROM message_log").fetchone()["cnt"]
    assert count == 3


# ── JSON metadata ──


def test_metadata_serialized_as_json():
    conn = make_test_db()
    meta = {"llm_latency_ms": 123.4, "token_count": 50}
    log_message(conn, "GRST", "outbound", "Hi", "npc_llm", metadata=meta)
    row = conn.execute("SELECT metadata FROM message_log").fetchone()
    parsed = json.loads(row["metadata"])
    assert parsed["llm_latency_ms"] == 123.4
    assert parsed["token_count"] == 50


def test_metadata_none_stored_as_null():
    conn = make_test_db()
    log_message(conn, "EMBR", "inbound", "look", "command", metadata=None)
    row = conn.execute("SELECT metadata FROM message_log").fetchone()
    assert row["metadata"] is None


def test_metadata_empty_dict_stored_as_json():
    conn = make_test_db()
    log_message(conn, "EMBR", "inbound", "look", "command", metadata={})
    row = conn.execute("SELECT metadata FROM message_log").fetchone()
    parsed = json.loads(row["metadata"])
    assert parsed == {}


def test_metadata_complex_nested():
    conn = make_test_db()
    meta = {
        "broadcast_id": 42,
        "condition": {"floor": 2},
        "recipients": ["!abc", "!def"],
    }
    log_message(conn, "DCRG", "outbound", "Alert", "broadcast_targeted", metadata=meta)
    row = conn.execute("SELECT metadata FROM message_log").fetchone()
    parsed = json.loads(row["metadata"])
    assert parsed["broadcast_id"] == 42
    assert parsed["condition"]["floor"] == 2
    assert len(parsed["recipients"]) == 2


def test_metadata_rule_matched():
    conn = make_test_db()
    meta = {"rule_matched": "npc_rule1"}
    log_message(
        conn, "GRST", "outbound", "Don't know you.", "npc_rule1",
        metadata=meta,
    )
    row = conn.execute("SELECT metadata FROM message_log").fetchone()
    parsed = json.loads(row["metadata"])
    assert parsed["rule_matched"] == "npc_rule1"


def test_metadata_fallback_reason():
    conn = make_test_db()
    meta = {"fallback_reason": "llm_error"}
    log_message(
        conn, "WSPR", "outbound", "...fragments.", "npc_fallback",
        metadata=meta,
    )
    row = conn.execute("SELECT metadata FROM message_log").fetchone()
    parsed = json.loads(row["metadata"])
    assert parsed["fallback_reason"] == "llm_error"


# ── Error resilience ──


def test_log_never_raises_on_db_error():
    """A failed log write must NEVER break game processing."""
    conn = make_test_db()
    conn.close()  # Close the connection to cause errors
    # This must NOT raise
    log_message(conn, "EMBR", "inbound", "test", "command")


def test_log_never_raises_on_bad_table():
    """Log to a DB without message_log table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # No schema — message_log table doesn't exist
    log_message(conn, "EMBR", "inbound", "test", "command")
    # Should not raise


def test_log_never_raises_on_serialization_error():
    """Unserializable metadata should not crash."""
    conn = make_test_db()
    # Pass something that json.dumps can't handle
    class BadObj:
        pass
    log_message(conn, "EMBR", "inbound", "test", "command", metadata={"bad": BadObj()})
    # Should not raise — the whole call is wrapped in try/except


def test_prune_never_raises_on_db_error():
    conn = make_test_db()
    conn.close()
    # Must not raise
    result = prune_old_logs(conn, 90)
    assert result == 0


# ── Message type correctness ──


def test_all_message_types_writable():
    """All 16 defined message types can be written."""
    conn = make_test_db()
    types = [
        "command", "response", "register", "register_response",
        "broadcast_tier1", "broadcast_tier2", "broadcast_targeted",
        "dcrg_rejection", "npc_rule1", "npc_rule2", "npc_llm",
        "npc_fallback", "npc_inbound", "daytick", "error",
    ]
    for mt in types:
        log_message(conn, "EMBR", "inbound", f"test {mt}", mt)

    count = conn.execute("SELECT COUNT(*) as cnt FROM message_log").fetchone()["cnt"]
    assert count == len(types)

    # Verify each type was stored correctly
    for mt in types:
        row = conn.execute(
            "SELECT * FROM message_log WHERE message_type = ?", (mt,)
        ).fetchone()
        assert row is not None, f"message_type '{mt}' not found"


def test_embr_inbound_command_type():
    conn = make_test_db()
    log_message(conn, "EMBR", "inbound", "look", "command", sender_id="!abc")
    row = conn.execute("SELECT * FROM message_log").fetchone()
    assert row["message_type"] == "command"
    assert row["direction"] == "inbound"
    assert row["node"] == "EMBR"


def test_embr_outbound_response_type():
    conn = make_test_db()
    log_message(conn, "EMBR", "outbound", "Dark hall", "response", recipient_id="!abc")
    row = conn.execute("SELECT * FROM message_log").fetchone()
    assert row["message_type"] == "response"
    assert row["direction"] == "outbound"


def test_register_types():
    conn = make_test_db()
    log_message(conn, "EMBR", "inbound", "warrior", "register", sender_id="!new")
    log_message(conn, "EMBR", "outbound", "Welcome!", "register_response", recipient_id="!new")
    rows = conn.execute(
        "SELECT message_type FROM message_log ORDER BY id"
    ).fetchall()
    assert rows[0]["message_type"] == "register"
    assert rows[1]["message_type"] == "register_response"


def test_broadcast_tier_types():
    conn = make_test_db()
    log_message(conn, "DCRG", "outbound", "Death!", "broadcast_tier1")
    log_message(conn, "DCRG", "outbound", "Bounty!", "broadcast_tier2")
    rows = conn.execute(
        "SELECT message_type FROM message_log ORDER BY id"
    ).fetchall()
    assert rows[0]["message_type"] == "broadcast_tier1"
    assert rows[1]["message_type"] == "broadcast_tier2"


def test_npc_types():
    conn = make_test_db()
    log_message(conn, "GRST", "outbound", "Don't know you.", "npc_rule1")
    log_message(conn, "MRN", "outbound", "Not in bar.", "npc_rule2")
    log_message(conn, "TRVL", "outbound", "Nice gear!", "npc_llm")
    log_message(conn, "WSPR", "outbound", "...fragments.", "npc_fallback")
    log_message(conn, "GRST", "inbound", "Hello", "npc_inbound")
    rows = conn.execute(
        "SELECT message_type FROM message_log ORDER BY id"
    ).fetchall()
    types = [r["message_type"] for r in rows]
    assert types == ["npc_rule1", "npc_rule2", "npc_llm", "npc_fallback", "npc_inbound"]


def test_daytick_type():
    conn = make_test_db()
    log_message(
        conn, "EMBR", "system", "Day tick: day 2", "daytick",
        metadata={"new_day": 2, "actions_reset": 5},
    )
    row = conn.execute("SELECT * FROM message_log").fetchone()
    assert row["message_type"] == "daytick"
    assert row["direction"] == "system"
    assert row["node"] == "EMBR"
    parsed = json.loads(row["metadata"])
    assert parsed["new_day"] == 2


def test_error_type():
    conn = make_test_db()
    log_message(
        conn, "EMBR", "system", "Engine crash", "error",
        sender_id="!abc",
        metadata={"original_message": "look"},
    )
    row = conn.execute("SELECT * FROM message_log").fetchone()
    assert row["message_type"] == "error"
    assert row["direction"] == "system"


# ── Pruning ──


def test_prune_deletes_old_entries():
    conn = make_test_db()
    # Insert an entry with an old timestamp
    old_ts = (datetime.utcnow() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO message_log (timestamp, node, direction, message, message_type)
           VALUES (?, 'EMBR', 'inbound', 'old msg', 'command')""",
        (old_ts,),
    )
    # Insert a recent entry
    log_message(conn, "EMBR", "inbound", "new msg", "command")
    conn.commit()

    deleted = prune_old_logs(conn, 90)
    assert deleted == 1

    remaining = conn.execute("SELECT COUNT(*) as cnt FROM message_log").fetchone()["cnt"]
    assert remaining == 1
    row = conn.execute("SELECT message FROM message_log").fetchone()
    assert row["message"] == "new msg"


def test_prune_keeps_recent_entries():
    conn = make_test_db()
    # Insert entries within retention period
    log_message(conn, "EMBR", "inbound", "msg1", "command")
    log_message(conn, "EMBR", "inbound", "msg2", "command")
    deleted = prune_old_logs(conn, 90)
    assert deleted == 0
    count = conn.execute("SELECT COUNT(*) as cnt FROM message_log").fetchone()["cnt"]
    assert count == 2


def test_prune_empty_table():
    conn = make_test_db()
    deleted = prune_old_logs(conn, 90)
    assert deleted == 0


def test_prune_returns_count():
    conn = make_test_db()
    old_ts = (datetime.utcnow() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(5):
        conn.execute(
            """INSERT INTO message_log (timestamp, node, direction, message, message_type)
               VALUES (?, 'EMBR', 'inbound', ?, 'command')""",
            (old_ts, f"old {i}"),
        )
    conn.commit()
    deleted = prune_old_logs(conn, 90)
    assert deleted == 5


def test_prune_with_custom_retention():
    conn = make_test_db()
    # Insert entry from 10 days ago
    ts = (datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO message_log (timestamp, node, direction, message, message_type)
           VALUES (?, 'EMBR', 'inbound', 'semi-old', 'command')""",
        (ts,),
    )
    conn.commit()

    # 30-day retention — should keep it
    deleted = prune_old_logs(conn, 30)
    assert deleted == 0

    # 7-day retention — should delete it
    deleted = prune_old_logs(conn, 7)
    assert deleted == 1


# ── Router integration (log_message called from router) ──


def test_router_logs_embr_inbound():
    """Verify the router logs EMBR inbound messages."""
    from src.core.engine import GameEngine
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler
    from src.transport.meshtastic import MeshMessage
    from src.transport.router import NodeRouter

    conn = make_test_db()
    engine = GameEngine(conn)
    npc_handler = NPCConversationHandler(conn, DummyBackend())
    transports = {"EMBR": MagicMock()}
    router = NodeRouter(engine, npc_handler, transports)

    msg = MeshMessage(
        sender_id="!abc", sender_name="Tester",
        text="look", is_dm=True, channel=0,
    )
    router.route_message("EMBR", msg)

    rows = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'command'"
    ).fetchall()
    assert len(rows) >= 1
    assert rows[0]["sender_id"] == "!abc"
    assert rows[0]["direction"] == "inbound"


def test_router_logs_embr_outbound():
    """Verify the router logs EMBR outbound responses."""
    from src.core.engine import GameEngine
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler
    from src.transport.meshtastic import MeshMessage
    from src.transport.router import NodeRouter

    conn = make_test_db()
    engine = GameEngine(conn)
    npc_handler = NPCConversationHandler(conn, DummyBackend())
    transports = {"EMBR": MagicMock()}
    router = NodeRouter(engine, npc_handler, transports)

    msg = MeshMessage(
        sender_id="!abc", sender_name="Tester",
        text="look", is_dm=True, channel=0,
    )
    router.route_message("EMBR", msg)

    rows = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'response'"
    ).fetchall()
    assert len(rows) >= 1
    assert rows[0]["direction"] == "outbound"
    assert rows[0]["recipient_id"] == "!abc"


def test_router_logs_dcrg_rejection():
    """Verify the router logs DCRG rejection."""
    from config import DCRG_REJECTION
    from src.core.engine import GameEngine
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler
    from src.transport.meshtastic import MeshMessage
    from src.transport.router import NodeRouter

    conn = make_test_db()
    engine = GameEngine(conn)
    npc_handler = NPCConversationHandler(conn, DummyBackend())
    transports = {"DCRG": MagicMock()}
    router = NodeRouter(engine, npc_handler, transports)

    msg = MeshMessage(
        sender_id="!abc", sender_name="Tester",
        text="Hello", is_dm=True, channel=0,
    )
    router.route_message("DCRG", msg)

    rows = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'dcrg_rejection'"
    ).fetchall()
    assert len(rows) == 2  # inbound + outbound
    directions = {r["direction"] for r in rows}
    assert directions == {"inbound", "outbound"}


def test_router_logs_npc_with_result_type():
    """Verify the router logs NPC responses with correct result type."""
    from src.core.engine import GameEngine
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler
    from src.transport.meshtastic import MeshMessage
    from src.transport.router import NodeRouter

    conn = make_test_db()
    engine = GameEngine(conn)
    npc_handler = NPCConversationHandler(conn, DummyBackend())
    transports = {"GRST": MagicMock()}
    router = NodeRouter(engine, npc_handler, transports)

    # Player is in town, known — should hit Rule 3 (LLM/fallback)
    msg = MeshMessage(
        sender_id="!abc", sender_name="Tester",
        text="Hello Grist", is_dm=True, channel=0,
    )
    router.route_message("GRST", msg)

    # Check inbound logged
    inbound = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'npc_inbound'"
    ).fetchone()
    assert inbound is not None
    assert inbound["sender_id"] == "!abc"

    # Check outbound logged (DummyBackend → npc_fallback or npc_llm)
    outbound = conn.execute(
        "SELECT * FROM message_log WHERE direction = 'outbound'"
    ).fetchone()
    assert outbound is not None
    assert outbound["message_type"] in ("npc_llm", "npc_fallback")
    assert outbound["player_id"] == 1


def test_router_logs_npc_rule1_unknown_player():
    """Verify router logs npc_rule1 for unknown players."""
    from src.core.engine import GameEngine
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler
    from src.transport.meshtastic import MeshMessage
    from src.transport.router import NodeRouter

    conn = make_test_db()
    engine = GameEngine(conn)
    npc_handler = NPCConversationHandler(conn, DummyBackend())
    transports = {"GRST": MagicMock()}
    router = NodeRouter(engine, npc_handler, transports)

    msg = MeshMessage(
        sender_id="!unknown99", sender_name="Nobody",
        text="Hello", is_dm=True, channel=0,
    )
    router.route_message("GRST", msg)

    outbound = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'npc_rule1'"
    ).fetchone()
    assert outbound is not None
    assert outbound["direction"] == "outbound"
    meta = json.loads(outbound["metadata"])
    assert meta["rule_matched"] == "npc_rule1"


def test_router_logs_register_flow():
    """Verify router logs register inbound and register_response outbound."""
    from src.core.engine import GameEngine
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler
    from src.transport.meshtastic import MeshMessage
    from src.transport.router import NodeRouter

    conn = make_test_db()
    engine = GameEngine(conn)
    npc_handler = NPCConversationHandler(conn, DummyBackend())
    transports = {"EMBR": MagicMock()}
    router = NodeRouter(engine, npc_handler, transports)

    # New player sends first message
    msg = MeshMessage(
        sender_id="!newplayer", sender_name="NewGuy",
        text="hello", is_dm=True, channel=0,
    )
    router.route_message("EMBR", msg)

    inbound = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'register'"
    ).fetchone()
    assert inbound is not None
    assert inbound["direction"] == "inbound"
    assert inbound["player_id"] is None  # Not yet registered

    outbound = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'register_response'"
    ).fetchone()
    assert outbound is not None
    assert outbound["direction"] == "outbound"


# ── Broadcast drain integration ──


def test_drain_logs_broadcast():
    """Verify broadcast drain logs DCRG broadcasts."""
    from src.systems import broadcast as broadcast_sys
    from src.transport.broadcast_drain import BroadcastDrain

    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "News flash!")
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    drain.drain_once()

    row = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'broadcast_tier1'"
    ).fetchone()
    assert row is not None
    assert row["direction"] == "outbound"
    assert row["node"] == "DCRG"
    meta = json.loads(row["metadata"])
    assert meta["tier"] == 1


def test_drain_logs_tier2():
    """Verify broadcast drain logs tier 2 broadcasts."""
    from src.systems import broadcast as broadcast_sys
    from src.transport.broadcast_drain import BroadcastDrain

    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 2, "Bounty progress!")
    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    drain.drain_once()

    row = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'broadcast_tier2'"
    ).fetchone()
    assert row is not None
    meta = json.loads(row["metadata"])
    assert meta["tier"] == 2


def test_drain_logs_targeted():
    """Verify broadcast drain logs targeted broadcasts with recipient."""
    from src.transport.broadcast_drain import BroadcastDrain

    conn = make_test_db()
    # Player 1 is on floor 0 (town), update to floor 2 for targeting
    conn.execute("UPDATE players SET floor = 2, state = 'dungeon' WHERE id = 1")
    conn.execute(
        """INSERT INTO broadcasts (tier, targeted, target_condition, message)
           VALUES (1, 1, '{"floor": 2}', 'Floor 2 alert!')"""
    )
    conn.commit()

    mock_transport = MagicMock()
    drain = BroadcastDrain(conn, mock_transport, rate_limit=0)
    drain.drain_once()

    row = conn.execute(
        "SELECT * FROM message_log WHERE message_type = 'broadcast_targeted'"
    ).fetchone()
    assert row is not None
    assert row["recipient_id"] == "!abc"
    meta = json.loads(row["metadata"])
    assert meta["condition"] == '{"floor": 2}'


# ── NPC handler tracking attributes ──


def test_npc_handler_sets_last_result_type_rule1():
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler

    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    handler.handle_message("grist", "!unknown", "hello")
    assert handler.last_result_type == "npc_rule1"
    assert handler.last_player_id is None


def test_npc_handler_sets_last_result_type_rule2():
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler

    conn = make_test_db()
    # Put player in dungeon
    conn.execute("UPDATE players SET state = 'dungeon', floor = 1 WHERE id = 1")
    conn.commit()
    handler = NPCConversationHandler(conn, DummyBackend())
    handler.handle_message("grist", "!abc", "hello")
    assert handler.last_result_type == "npc_rule2"
    assert handler.last_player_id == 1


def test_npc_handler_sets_last_result_type_llm():
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler

    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    # Player is in town — should hit Rule 3
    handler.handle_message("grist", "!abc", "hello")
    # DummyBackend.chat() returns a string, so it's npc_llm
    assert handler.last_result_type == "npc_llm"
    assert handler.last_player_id == 1


def test_npc_handler_sets_last_result_type_fallback():
    from src.systems.npc_conversation import NPCConversationHandler

    conn = make_test_db()
    bad_backend = MagicMock()
    bad_backend.chat.side_effect = Exception("LLM down")
    handler = NPCConversationHandler(conn, bad_backend)
    handler.handle_message("grist", "!abc", "hello")
    assert handler.last_result_type == "npc_fallback"
    assert handler.last_player_id == 1


def test_npc_handler_resets_tracking_each_call():
    from src.generation.narrative import DummyBackend
    from src.systems.npc_conversation import NPCConversationHandler

    conn = make_test_db()
    handler = NPCConversationHandler(conn, DummyBackend())
    # First call — known player
    handler.handle_message("grist", "!abc", "hello")
    assert handler.last_result_type == "npc_llm"
    assert handler.last_player_id == 1
    # Second call — unknown player
    handler.handle_message("grist", "!unknown", "hello")
    assert handler.last_result_type == "npc_rule1"
    assert handler.last_player_id is None
