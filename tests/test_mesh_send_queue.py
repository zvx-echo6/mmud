"""Tests for MeshTransport send queue."""

import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.transport.meshtastic import MeshTransport


def _make_transport():
    """Create a MeshTransport with a fake interface (no real device)."""
    t = MeshTransport("fake:4403")
    t._interface = MagicMock()
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
    t.send_broadcast("test")
    _, target, _ = t._send_queue.get_nowait()
    assert target == 5


def test_send_dm_no_interface():
    """send_dm() with no interface should not queue."""
    t = MeshTransport("fake:4403")
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
    original_sendText = t._interface.sendText

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


def test_send_loop_retries_on_failure():
    """Failed send should retry once then log error."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0
    t._interface.sendText = MagicMock(side_effect=RuntimeError("serial error"))
    _start_send_loop(t)
    try:
        t._send_queue.put(("dm", "!a", "fail"))
        t._send_queue.join()
        # Should have been called twice (initial + 1 retry)
        assert t._interface.sendText.call_count == 2
    finally:
        _stop_send_loop(t)


def test_send_loop_succeeds_on_retry():
    """If first attempt fails but second succeeds, message is delivered."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0
    call_count = [0]

    def flaky_send(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("transient error")
        # Second call succeeds

    t._interface.sendText = flaky_send
    _start_send_loop(t)
    try:
        t._send_queue.put(("dm", "!a", "retry"))
        t._send_queue.join()
        assert call_count[0] == 2
    finally:
        _stop_send_loop(t)


def test_disconnect_stops_send_thread():
    """disconnect() should stop the send loop thread."""
    t = _make_transport()
    t.SEND_INTERVAL = 0.0
    _start_send_loop(t)
    assert t._send_thread.is_alive()
    t.disconnect()
    assert t._send_thread is None
    assert t._interface is None


def test_queue_depth_property():
    """send_queue_depth should reflect queued messages."""
    t = _make_transport()
    assert t.send_queue_depth == 0
    t.send_dm("!a", "one")
    t.send_dm("!a", "two")
    assert t.send_queue_depth == 2


if __name__ == "__main__":
    test_send_dm_queues_message()
    test_send_broadcast_queues_message()
    test_send_broadcast_uses_default_channel()
    test_send_dm_no_interface()
    test_send_loop_processes_dm()
    test_send_loop_processes_broadcast()
    test_send_loop_paces_messages()
    test_send_loop_retries_on_failure()
    test_send_loop_succeeds_on_retry()
    test_disconnect_stops_send_thread()
    test_queue_depth_property()
    print("All send queue tests passed!")
