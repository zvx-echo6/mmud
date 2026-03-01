"""Tests for MeshTransport send queue, health monitor, watchdog, and reconnect."""

import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.transport.meshtastic import MeshTransport, PendingMessage


def _make_transport():
    """Create a MeshTransport with a fake interface (no real device)."""
    t = MeshTransport("fake:4403")
    t._interface = MagicMock()
    t._connected = True
    return t


def _start_send_loop(t):
    """Start the send loop thread manually (bypasses connect())."""
    t._stop_event.clear()
    t._send_thread = threading.Thread(
        target=t._send_loop, name="test-send", daemon=True
    )
    t._send_thread.start()


def _stop_send_loop(t):
    """Stop the send loop and wait for thread to exit."""
    t._stop_event.set()
    if t._send_thread:
        t._send_thread.join(timeout=5)


# ── Send Queue Tests ───────────────────────────────────────────────────────


def test_send_dm_queues_message():
    """send_dm() should put a message on the queue, not call sendText directly."""
    t = _make_transport()
    t.send_dm("!abcd1234", "hello")
    assert t.send_queue_depth == 1
    msg_type, target, text = t._send_queue.get_nowait()
    assert msg_type == "dm"
    assert target == "!abcd1234"
    assert text == "hello"
    # sendText should NOT have been called (no send loop running)
    t._interface.sendText.assert_not_called()


def test_send_broadcast_queues_message():
    """send_broadcast() should queue with the resolved channel."""
    t = _make_transport()
    t.send_broadcast("alert!", channel=2)
    msg_type, target, text = t._send_queue.get_nowait()
    assert msg_type == "broadcast"
    assert target == 2
    assert text == "alert!"


def test_send_broadcast_uses_default_channel():
    """send_broadcast() without explicit channel uses configured default."""
    t = MeshTransport("fake:4403", channel=5)
    t._interface = MagicMock()
    t._connected = True
    t.send_broadcast("test")
    _, target, _ = t._send_queue.get_nowait()
    assert target == 5


def test_send_dm_not_connected():
    """send_dm() when not connected should not queue."""
    t = MeshTransport("fake:4403")
    # _connected defaults to False
    t.send_dm("!abcd1234", "hello")
    assert t.send_queue_depth == 0


def test_send_loop_processes_dm():
    """Send loop should call sendText for queued DMs."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0  # No delay for testing
    _start_send_loop(t)
    try:
        t._send_queue.put(("dm", "!abcd1234", "hello"))
        t._send_queue.join()  # Wait for it to be processed
        t._interface.sendText.assert_called_once_with(
            text="hello", destinationId="!abcd1234", wantAck=True
        )
    finally:
        _stop_send_loop(t)


def test_send_loop_processes_broadcast():
    """Send loop should call sendText for queued broadcasts."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0
    _start_send_loop(t)
    try:
        t._send_queue.put(("broadcast", 0, "news"))
        t._send_queue.join()
        t._interface.sendText.assert_called_once_with(
            text="news", channelIndex=0
        )
    finally:
        _stop_send_loop(t)


def test_send_loop_paces_messages():
    """Multiple messages should be spaced by SEND_INTERVAL."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.2  # Short interval for testing
    send_times = []

    def recording_send(**kwargs):
        send_times.append(time.monotonic())

    t._interface.sendText = recording_send

    _start_send_loop(t)
    try:
        t._send_queue.put(("dm", "!a", "msg1"))
        t._send_queue.put(("dm", "!a", "msg2"))
        t._send_queue.put(("dm", "!a", "msg3"))
        t._send_queue.join()
        assert len(send_times) == 3
        # Gap between msg1 and msg2 should be >= SEND_INTERVAL
        gap1 = send_times[1] - send_times[0]
        gap2 = send_times[2] - send_times[1]
        assert gap1 >= 0.15, f"Gap1 too short: {gap1:.3f}s"
        assert gap2 >= 0.15, f"Gap2 too short: {gap2:.3f}s"
    finally:
        _stop_send_loop(t)


def test_disconnect_stops_threads():
    """disconnect() should stop both send and health threads."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0
    _start_send_loop(t)
    # Also start health thread
    t._health_thread = threading.Thread(
        target=t._health_loop, name="test-health", daemon=True
    )
    t._health_thread.start()
    assert t._send_thread.is_alive()
    assert t._health_thread.is_alive()
    t.disconnect()
    assert t._send_thread is None
    assert t._health_thread is None
    assert t._interface is None
    assert not t._connected


def test_queue_depth_property():
    """send_queue_depth should reflect queued messages."""
    t = _make_transport()
    assert t.send_queue_depth == 0
    t.send_dm("!a", "one")
    t.send_dm("!a", "two")
    assert t.send_queue_depth == 2


def test_connected_property():
    """connected property should reflect connection state."""
    t = MeshTransport("fake:4403")
    assert not t.connected
    t._connected = True
    assert t.connected


# ── Safe Send Tests ────────────────────────────────────────────────────────


def test_safe_send_success():
    """_safe_send returns True when send succeeds."""
    t = _make_transport()
    result = t._safe_send(lambda: None)
    assert result is True


def test_safe_send_marks_dead_on_broken_pipe():
    """_safe_send on BrokenPipeError should set _connected=False, _interface=None."""
    t = _make_transport()

    def broken():
        raise BrokenPipeError("dead socket")

    result = t._safe_send(broken)
    assert result is False
    assert not t._connected
    assert t._interface is None


def test_safe_send_marks_dead_on_connection_error():
    """_safe_send on ConnectionError should mark transport dead."""
    t = _make_transport()

    def conn_error():
        raise ConnectionResetError("reset by peer")

    result = t._safe_send(conn_error)
    assert result is False
    assert not t._connected
    assert t._interface is None


def test_safe_send_when_disconnected_returns_false():
    """_safe_send with _connected=False returns False (no inline reconnect)."""
    t = _make_transport()
    t._connected = False
    result = t._safe_send(lambda: None)
    assert result is False


# ── is_healthy Tests ──────────────────────────────────────────────────────


def test_is_healthy_true_when_connected():
    """is_healthy() returns True on a live transport."""
    t = _make_transport()
    assert t.is_healthy() is True


def test_is_healthy_false_when_interface_none():
    """is_healthy() returns False when _interface is None."""
    t = _make_transport()
    t._interface = None
    assert t.is_healthy() is False


def test_is_healthy_false_when_not_connected():
    """is_healthy() returns False when _connected is False."""
    t = _make_transport()
    t._connected = False
    assert t.is_healthy() is False


def test_is_healthy_false_when_socket_dead():
    """is_healthy() returns False when TCP socket fileno is -1."""
    t = _make_transport()
    # Simulate dead socket
    mock_socket = MagicMock()
    mock_socket.fileno.return_value = -1
    t._interface._socket = mock_socket
    assert t.is_healthy() is False


def test_is_healthy_false_when_socket_none():
    """is_healthy() returns False when TCP socket is None."""
    t = _make_transport()
    t._interface._socket = None
    assert t.is_healthy() is False


# ── Reconnect Tests ────────────────────────────────────────────────────────


def test_reconnect_success():
    """reconnect() should teardown and re-establish."""
    t = _make_transport()
    old_interface = t._interface
    t._connected = False

    establish_called = [False]

    def mock_establish():
        establish_called[0] = True
        t._interface = MagicMock()
        t._my_node_id = "!test1234"
        t._connected = True

    t._establish_connection = mock_establish
    result = t.reconnect()
    assert result is True
    assert establish_called[0]
    assert t._connected
    old_interface.close.assert_called_once()


def test_reconnect_failure():
    """reconnect() should return False on connection failure."""
    t = _make_transport()
    t._connected = False

    def fail_establish():
        raise RuntimeError("connection refused")

    t._establish_connection = fail_establish
    result = t.reconnect()
    assert result is False
    assert not t._connected


def test_teardown_preserves_threads():
    """teardown() should null the interface but not stop background threads."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0
    _start_send_loop(t)
    assert t._send_thread.is_alive()

    t.teardown()
    assert t._interface is None
    assert not t._connected
    # Send thread should still be alive
    assert t._send_thread.is_alive()

    _stop_send_loop(t)


# ── Health Monitor Tests ───────────────────────────────────────────────────


def test_health_check_marks_disconnected_on_dead_socket():
    """Health check detects dead socket via is_healthy() and marks disconnected."""
    t = _make_transport()
    t.HEARTBEAT_INTERVAL = 0.05
    assert t._connected

    # Simulate dead socket
    t._interface._socket = None

    t._stop_event.clear()
    t._health_thread = threading.Thread(
        target=t._health_loop, name="test-health", daemon=True
    )
    t._health_thread.start()

    time.sleep(0.2)
    t._stop_event.set()
    t._health_thread.join(timeout=5)

    assert not t._connected


# ── Send Loop + Dead Socket ───────────────────────────────────────────────


def test_send_loop_drops_message_on_dead_socket():
    """Send loop marks transport dead on BrokenPipeError, drops message."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0

    t._interface.sendText = MagicMock(side_effect=BrokenPipeError("dead"))

    _start_send_loop(t)
    try:
        t._send_queue.put(("dm", "!a", "lost"))
        t._send_queue.join()
        assert t.send_queue_depth == 0
        assert not t._connected
        assert t._interface is None
    finally:
        _stop_send_loop(t)


# ── ACK Tracking Tests ────────────────────────────────────────────────────


def test_do_send_dm_stores_pending():
    """_do_send_dm should store a PendingMessage for the destination."""
    t = _make_transport()
    t._do_send_dm("!abcd1234", "hello")
    assert "!abcd1234" in t._pending_acks
    pending = t._pending_acks["!abcd1234"]
    assert pending.text == "hello"
    assert pending.retry_count == 0
    t._interface.sendText.assert_called_once_with(
        text="hello", destinationId="!abcd1234", wantAck=True
    )


def test_do_send_dm_retry_does_not_overwrite_pending():
    """Sending a retry-tagged message should not overwrite the original pending."""
    t = _make_transport()
    # Store original
    t._pending_acks["!a"] = PendingMessage(
        dest_id="!a", text="original", sent_at=time.time(), retry_count=1
    )
    # Send a retry
    t._do_send_dm("!a", "[R1] original")
    # Original pending should be unchanged
    assert t._pending_acks["!a"].text == "original"
    assert t._pending_acks["!a"].retry_count == 1


def test_do_send_dm_lost_does_not_overwrite_pending():
    """Sending a [LOST] notice should not create a pending entry."""
    t = _make_transport()
    t._do_send_dm("!a", "[LOST] Last response failed to deliver.")
    assert "!a" not in t._pending_acks


def test_get_unacked_returns_none_no_pending():
    """get_unacked_for returns None when no pending exists."""
    t = _make_transport()
    assert t.get_unacked_for("!a") is None


def test_get_unacked_clears_within_timeout():
    """get_unacked_for clears pending and returns None within ACK window."""
    t = _make_transport()
    t.ACK_TIMEOUT = 60.0
    # Pending sent 5 seconds ago — well within timeout
    t._pending_acks["!a"] = PendingMessage(
        dest_id="!a", text="response", sent_at=time.time() - 5
    )
    result = t.get_unacked_for("!a")
    assert result is None
    # Pending should be cleared (implicit ACK)
    assert "!a" not in t._pending_acks


def test_get_unacked_returns_tagged_after_timeout():
    """get_unacked_for returns [R1] tagged message after ACK timeout."""
    t = _make_transport()
    t.ACK_TIMEOUT = 60.0
    # Pending sent 90 seconds ago — past timeout
    t._pending_acks["!a"] = PendingMessage(
        dest_id="!a", text="response text", sent_at=time.time() - 90
    )
    result = t.get_unacked_for("!a")
    assert result == "[R1] response text"
    # Pending should still exist with incremented retry count
    assert t._pending_acks["!a"].retry_count == 1


def test_get_unacked_increments_retry_count():
    """Successive calls to get_unacked_for increment the retry tag."""
    t = _make_transport()
    t.ACK_TIMEOUT = 0.0  # Immediate timeout for testing
    t._pending_acks["!a"] = PendingMessage(
        dest_id="!a", text="test", sent_at=time.time() - 1
    )
    r1 = t.get_unacked_for("!a")
    assert r1 == "[R1] test"

    # Reset sent_at to past timeout again
    t._pending_acks["!a"].sent_at = time.time() - 1
    r2 = t.get_unacked_for("!a")
    assert r2 == "[R2] test"

    t._pending_acks["!a"].sent_at = time.time() - 1
    r3 = t.get_unacked_for("!a")
    assert r3 == "[R3] test"


def test_get_unacked_truncates_to_char_limit():
    """Tagged message should be truncated to MSG_CHAR_LIMIT."""
    from config import MSG_CHAR_LIMIT

    t = _make_transport()
    t.ACK_TIMEOUT = 0.0
    # Create a message that fills the limit
    long_text = "A" * MSG_CHAR_LIMIT
    t._pending_acks["!a"] = PendingMessage(
        dest_id="!a", text=long_text, sent_at=time.time() - 1
    )
    result = t.get_unacked_for("!a")
    assert result.startswith("[R1] ")
    assert len(result) <= MSG_CHAR_LIMIT


def test_get_unacked_max_retries_returns_lost():
    """After MAX_RETRIES, get_unacked_for returns [LOST] notice."""
    t = _make_transport()
    t.ACK_TIMEOUT = 0.0
    t.MAX_RETRIES = 3
    t._pending_acks["!a"] = PendingMessage(
        dest_id="!a", text="lost msg", sent_at=time.time() - 1,
        retry_count=3,  # Already at max
    )
    result = t.get_unacked_for("!a")
    assert result.startswith("[LOST]")
    # Pending should be cleared
    assert "!a" not in t._pending_acks


def test_expire_pending_removes_old_entries():
    """_expire_pending should remove entries older than PENDING_EXPIRE."""
    t = _make_transport()
    t.PENDING_EXPIRE = 300.0
    # One fresh, one stale
    t._pending_acks["!fresh"] = PendingMessage(
        dest_id="!fresh", text="new", sent_at=time.time()
    )
    t._pending_acks["!stale"] = PendingMessage(
        dest_id="!stale", text="old", sent_at=time.time() - 600
    )
    t._expire_pending()
    assert "!fresh" in t._pending_acks
    assert "!stale" not in t._pending_acks


def test_send_loop_stores_pending_for_dm():
    """Send loop should store pending ACK when sending a DM."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0
    _start_send_loop(t)
    try:
        t._send_queue.put(("dm", "!abcd1234", "hello"))
        t._send_queue.join()
        # Pending should be stored
        assert "!abcd1234" in t._pending_acks
        assert t._pending_acks["!abcd1234"].text == "hello"
    finally:
        _stop_send_loop(t)


def test_broadcast_does_not_store_pending():
    """Broadcasts should NOT store pending ACK entries."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0
    _start_send_loop(t)
    try:
        t._send_queue.put(("broadcast", 0, "news"))
        t._send_queue.join()
        assert len(t._pending_acks) == 0
    finally:
        _stop_send_loop(t)


def test_new_dm_replaces_old_pending():
    """Sending a new DM to the same dest should replace the old pending."""
    t = _make_transport()
    t._pending_acks["!a"] = PendingMessage(
        dest_id="!a", text="old response", sent_at=time.time() - 30
    )
    t._do_send_dm("!a", "new response")
    assert t._pending_acks["!a"].text == "new response"
    assert t._pending_acks["!a"].retry_count == 0


# ── Watchdog Tests ─────────────────────────────────────────────────────────


def test_watchdog_detects_unhealthy_node():
    """Watchdog should log warning when a node becomes unhealthy."""
    from src.transport.router import NodeRouter
    from src.transport.broadcast_drain import BroadcastDrain

    # Create mock router with one unhealthy transport
    t = _make_transport()
    t._connected = False  # Unhealthy
    t._interface = None

    mock_engine = MagicMock()
    mock_npc = MagicMock()
    router = NodeRouter(mock_engine, mock_npc)
    router.transports = {"EMBR": t}

    drain = BroadcastDrain(MagicMock())

    # Mock reconnect to succeed
    establish_called = [False]

    def mock_establish():
        establish_called[0] = True
        t._interface = MagicMock()
        t._my_node_id = "!reconnected"
        t._connected = True

    t._establish_connection = mock_establish

    stop = threading.Event()

    from src.main import _run_watchdog
    watchdog = threading.Thread(
        target=_run_watchdog,
        args=(router, drain, {"EMBR": {"connection": "fake:4403"}}, stop, 0.05),
        daemon=True,
    )
    watchdog.start()

    time.sleep(0.3)
    stop.set()
    watchdog.join(timeout=5)

    assert establish_called[0], "Watchdog should have called reconnect"
    assert t._connected


def test_watchdog_backoff_on_repeated_failures():
    """Watchdog should back off on repeated reconnect failures."""
    from src.transport.router import NodeRouter
    from src.transport.broadcast_drain import BroadcastDrain

    t = _make_transport()
    t._connected = False
    t._interface = None

    mock_engine = MagicMock()
    mock_npc = MagicMock()
    router = NodeRouter(mock_engine, mock_npc)
    router.transports = {"EMBR": t}

    drain = BroadcastDrain(MagicMock())

    attempt_count = [0]

    def fail_establish():
        attempt_count[0] += 1
        raise RuntimeError("connection refused")

    t._establish_connection = fail_establish

    stop = threading.Event()

    from src.main import _run_watchdog
    watchdog = threading.Thread(
        target=_run_watchdog,
        args=(router, drain, {"EMBR": {"connection": "fake:4403"}}, stop, 0.05),
        daemon=True,
    )
    watchdog.start()

    # Run for a bit — backoff should limit attempts
    time.sleep(0.5)
    stop.set()
    watchdog.join(timeout=5)

    # Should have attempted but not hammered (backoff limits it)
    assert attempt_count[0] >= 1
    assert attempt_count[0] < 15  # Would be ~10 without backoff at 0.05s interval


def test_watchdog_rewires_callbacks_after_reconnect():
    """Watchdog should re-wire router callbacks after successful reconnect."""
    from src.transport.router import NodeRouter
    from src.transport.broadcast_drain import BroadcastDrain

    t = _make_transport()
    t._connected = False
    t._interface = None

    mock_engine = MagicMock()
    mock_npc = MagicMock()
    router = NodeRouter(mock_engine, mock_npc)
    router.transports = {"EMBR": t}

    wire_count = [0]
    original_wire = router.wire_callbacks

    def counting_wire():
        wire_count[0] += 1
        original_wire()

    router.wire_callbacks = counting_wire

    drain = BroadcastDrain(MagicMock())

    def mock_establish():
        t._interface = MagicMock()
        t._my_node_id = "!test"
        t._connected = True

    t._establish_connection = mock_establish

    stop = threading.Event()

    from src.main import _run_watchdog
    watchdog = threading.Thread(
        target=_run_watchdog,
        args=(router, drain, {"EMBR": {"connection": "fake:4403"}}, stop, 0.05),
        daemon=True,
    )
    watchdog.start()

    time.sleep(0.3)
    stop.set()
    watchdog.join(timeout=5)

    assert wire_count[0] >= 1, "wire_callbacks should be called after reconnect"


def test_watchdog_reattaches_dcrg_drain():
    """Watchdog should re-attach DCRG to broadcast drain after reconnect."""
    from src.transport.router import NodeRouter
    from src.transport.broadcast_drain import BroadcastDrain

    t = _make_transport()
    t._connected = False
    t._interface = None

    mock_engine = MagicMock()
    mock_npc = MagicMock()
    router = NodeRouter(mock_engine, mock_npc)
    router.transports = {"DCRG": t}

    drain = BroadcastDrain(MagicMock())
    drain_set_count = [0]
    original_set = drain.set_transport

    def counting_set(transport):
        drain_set_count[0] += 1
        original_set(transport)

    drain.set_transport = counting_set

    def mock_establish():
        t._interface = MagicMock()
        t._my_node_id = "!dcrg"
        t._connected = True

    t._establish_connection = mock_establish

    stop = threading.Event()

    from src.main import _run_watchdog
    watchdog = threading.Thread(
        target=_run_watchdog,
        args=(router, drain, {"DCRG": {"connection": "fake:4403"}}, stop, 0.05),
        daemon=True,
    )
    watchdog.start()

    time.sleep(0.3)
    stop.set()
    watchdog.join(timeout=5)

    assert drain_set_count[0] >= 1, "drain.set_transport should be called for DCRG"


def test_watchdog_skips_healthy_nodes():
    """Watchdog should not attempt reconnect on healthy nodes."""
    from src.transport.router import NodeRouter
    from src.transport.broadcast_drain import BroadcastDrain

    t = _make_transport()  # Healthy by default

    mock_engine = MagicMock()
    mock_npc = MagicMock()
    router = NodeRouter(mock_engine, mock_npc)
    router.transports = {"EMBR": t}

    drain = BroadcastDrain(MagicMock())

    reconnect_called = [False]
    original_reconnect = t.reconnect

    def mock_reconnect():
        reconnect_called[0] = True
        return original_reconnect()

    t.reconnect = mock_reconnect

    stop = threading.Event()

    from src.main import _run_watchdog
    watchdog = threading.Thread(
        target=_run_watchdog,
        args=(router, drain, {"EMBR": {"connection": "fake:4403"}}, stop, 0.05),
        daemon=True,
    )
    watchdog.start()

    time.sleep(0.3)
    stop.set()
    watchdog.join(timeout=5)

    assert not reconnect_called[0], "Healthy node should not trigger reconnect"
