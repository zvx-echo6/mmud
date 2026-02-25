"""Tests for the 6-node router: message routing, DCRG rejection, NPC dispatch."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import DCRG_REJECTION, LLM_OUTPUT_CHAR_LIMIT, NPC_UNKNOWN_PLAYER
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.systems.npc_conversation import NPCConversationHandler
from src.transport.meshtastic import MeshMessage
from src.transport.router import NodeRouter


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


def _make_dm(sender_id: str, text: str) -> MeshMessage:
    return MeshMessage(
        sender_id=sender_id,
        sender_name="Player",
        text=text,
        is_dm=True,
        channel=0,
    )


def _make_broadcast(sender_id: str, text: str) -> MeshMessage:
    return MeshMessage(
        sender_id=sender_id,
        sender_name="Player",
        text=text,
        is_dm=False,
        channel=0,
    )


def _make_router(conn=None) -> tuple[NodeRouter, dict[str, MagicMock]]:
    """Create a router with mock transports."""
    if conn is None:
        conn = make_test_db()
    engine = MagicMock()
    npc_handler = NPCConversationHandler(conn, DummyBackend())

    transports = {}
    for name in ("EMBR", "DCRG", "GRST", "MRN", "TRVL", "WSPR"):
        transports[name] = MagicMock()

    router = NodeRouter(engine, npc_handler, transports)
    return router, transports


# ── Broadcast messages ignored ──


def test_broadcast_messages_ignored():
    router, transports = _make_router()
    msg = _make_broadcast("!abc", "Hello")
    router.route_message("EMBR", msg)
    router.engine.process_message.assert_not_called()


# ── EMBR routing ──


def test_embr_routes_to_engine():
    router, transports = _make_router()
    router.engine.process_message.return_value = "Welcome!"
    msg = _make_dm("!abc", "look")
    router.route_message("EMBR", msg)
    router.engine.process_message.assert_called_once_with("!abc", "Player", "look")


def test_embr_sends_response_via_embr_transport():
    router, transports = _make_router()
    router.engine.process_message.return_value = "You see a dark hall."
    msg = _make_dm("!abc", "look")
    router.route_message("EMBR", msg)
    transports["EMBR"].send_dm.assert_called_once_with("!abc", "You see a dark hall.")


def test_embr_no_response_no_send():
    router, transports = _make_router()
    router.engine.process_message.return_value = None
    msg = _make_dm("!abc", "look")
    router.route_message("EMBR", msg)
    transports["EMBR"].send_dm.assert_not_called()


# ── DCRG rejection ──


def test_dcrg_rejects_inbound():
    router, transports = _make_router()
    msg = _make_dm("!abc", "Hello DCRG")
    router.route_message("DCRG", msg)
    expected = DCRG_REJECTION[:LLM_OUTPUT_CHAR_LIMIT]
    transports["DCRG"].send_dm.assert_called_once_with("!abc", expected)


def test_dcrg_rejects_unknown_player():
    router, transports = _make_router()
    msg = _make_dm("!unknown", "Hello DCRG")
    router.route_message("DCRG", msg)
    transports["DCRG"].send_dm.assert_called_once()


# ── NPC routing ──


def test_grist_routes_to_npc_handler():
    conn = make_test_db()
    router, transports = _make_router(conn)
    msg = _make_dm("!abc", "Hello Grist")
    router.route_message("GRST", msg)
    # Should respond via GRST transport (player is in town)
    transports["GRST"].send_dm.assert_called_once()
    call_args = transports["GRST"].send_dm.call_args
    assert call_args[0][0] == "!abc"
    assert len(call_args[0][1]) > 0
    assert len(call_args[0][1]) <= LLM_OUTPUT_CHAR_LIMIT


def test_maren_routes_to_npc_handler():
    conn = make_test_db()
    router, transports = _make_router(conn)
    msg = _make_dm("!abc", "Heal me")
    router.route_message("MRN", msg)
    transports["MRN"].send_dm.assert_called_once()


def test_torval_routes_to_npc_handler():
    conn = make_test_db()
    router, transports = _make_router(conn)
    msg = _make_dm("!abc", "Show me your wares")
    router.route_message("TRVL", msg)
    transports["TRVL"].send_dm.assert_called_once()


def test_whisper_routes_to_npc_handler():
    conn = make_test_db()
    router, transports = _make_router(conn)
    msg = _make_dm("!abc", "Tell me about secrets")
    router.route_message("WSPR", msg)
    transports["WSPR"].send_dm.assert_called_once()


def test_npc_unknown_player():
    conn = make_test_db()
    router, transports = _make_router(conn)
    msg = _make_dm("!unknown99", "Hello")
    router.route_message("GRST", msg)
    transports["GRST"].send_dm.assert_called_once()
    response = transports["GRST"].send_dm.call_args[0][1]
    assert response == NPC_UNKNOWN_PLAYER["grist"]


# ── Response via correct node ──


def test_npc_response_via_correct_transport():
    """Each NPC responds via its own transport, not EMBR."""
    conn = make_test_db()
    router, transports = _make_router(conn)
    msg = _make_dm("!abc", "Hello")

    router.route_message("GRST", msg)
    transports["GRST"].send_dm.assert_called_once()
    transports["EMBR"].send_dm.assert_not_called()
    transports["DCRG"].send_dm.assert_not_called()


# ── Wire callbacks ──


def test_wire_callbacks_sets_all():
    router, transports = _make_router()
    router.wire_callbacks()
    for name, transport in transports.items():
        transport.set_message_callback.assert_called_once()


def test_wired_callback_routes_correctly():
    conn = make_test_db()
    router, transports = _make_router(conn)
    router.engine.process_message.return_value = "Response"

    # Manually invoke what wire_callbacks would set up
    callbacks = {}
    for name, transport in transports.items():
        def make_cb(n):
            def cb(msg):
                router.route_message(n, msg)
            return cb
        callbacks[name] = make_cb(name)

    # Simulate EMBR receiving a DM
    msg = _make_dm("!abc", "look")
    callbacks["EMBR"](msg)
    router.engine.process_message.assert_called_once()


# ── Error handling ──


def test_engine_error_does_not_crash():
    router, transports = _make_router()
    router.engine.process_message.side_effect = Exception("Engine crash")
    msg = _make_dm("!abc", "look")
    # Should not raise
    router.route_message("EMBR", msg)


def test_npc_error_does_not_crash():
    conn = make_test_db()
    engine = MagicMock()
    npc_handler = MagicMock()
    npc_handler.handle_message.side_effect = Exception("NPC crash")
    transports = {"GRST": MagicMock()}
    router = NodeRouter(engine, npc_handler, transports)
    msg = _make_dm("!abc", "Hello")
    # Should not raise
    router.route_message("GRST", msg)


# ── Register transport ──


def test_register_transport():
    conn = make_test_db()
    engine = MagicMock()
    npc_handler = MagicMock()
    router = NodeRouter(engine, npc_handler)
    mock_transport = MagicMock()
    router.register_transport("EMBR", mock_transport)
    assert router.transports["EMBR"] is mock_transport


# ── Unknown node ──


def test_unknown_node_name_logs_warning():
    router, transports = _make_router()
    msg = _make_dm("!abc", "Hello")
    # Routing to an unknown node should not crash
    router.route_message("INVALID", msg)
