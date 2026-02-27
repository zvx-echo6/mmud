"""
Meshtastic transport layer for MMUD.
Wraps the Meshtastic Python API for message send/receive.
Supports both serial (USB) and TCP connections.

Outbound messages are queued and sent with inter-message delays
to respect LoRa half-duplex timing (LONG_FAST ~1.5s per message).

Connection health is monitored via periodic heartbeat. Dead connections
trigger automatic reconnect with exponential backoff.
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from config import MSG_CHAR_LIMIT

logger = logging.getLogger(__name__)


@dataclass
class MeshMessage:
    """Incoming message from a Meshtastic node."""
    sender_id: str        # Meshtastic node ID (hex string like "!abcd1234")
    sender_name: str      # Long name or short name
    text: str             # Message body
    is_dm: bool           # True if direct message, False if channel broadcast
    channel: int          # Channel index


@dataclass
class PendingMessage:
    """Outbound DM awaiting delivery confirmation."""
    dest_id: str
    text: str
    sent_at: float
    retry_count: int = 0


class MeshTransport:
    """Meshtastic device interface for MMUD.

    Handles connection to a Meshtastic device via serial or TCP,
    and provides send/receive for game messages.

    Outbound messages are queued and sent by a background thread
    with SEND_INTERVAL spacing to avoid overwhelming the radio.

    A health monitor thread periodically checks the connection and
    triggers auto-reconnect on failure.

    Outbound DMs are tracked for delivery. If a message is not
    acknowledged within ACK_TIMEOUT seconds, it is resent with a
    retry tag ([R1], [R2], ...) when the player's next command arrives.
    """

    # Minimum seconds between sends. LONG_FAST airtime ~1.5s per message
    # plus ACK window. 3 seconds is safe for back-to-back responses.
    SEND_INTERVAL = 3.0

    # Health monitor settings
    HEARTBEAT_INTERVAL = 30.0   # Seconds between connection health checks
    RECONNECT_DELAY = 5.0       # Seconds to wait before each reconnect attempt
    MAX_RECONNECT_ATTEMPTS = 10

    # ACK tracking settings
    ACK_TIMEOUT = 60.0          # Seconds before a message is considered unacked
    MAX_RETRIES = 5             # Stop retrying after this many attempts
    PENDING_EXPIRE = 300.0      # Seconds before stale pending entries are cleaned up

    def __init__(self, connection_string: str, channel: int = 0):
        """Initialize transport.

        Args:
            connection_string: Serial port (e.g., "/dev/ttyUSB0") or
                              TCP address (e.g., "192.168.1.100:4403").
            channel: Default channel index for broadcasts.
        """
        self._conn_str = connection_string
        self._channel = channel
        self._interface = None
        self._my_node_id: Optional[str] = None
        self._on_message: Optional[Callable] = None
        self._connected: bool = False
        self._send_queue: queue.Queue = queue.Queue()
        self._send_thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_lock = threading.Lock()
        self._last_send_time: float = 0.0
        self._subscribed: bool = False
        self._pending_acks: dict[str, PendingMessage] = {}  # dest_id → last unacked msg

    def connect(self) -> None:
        """Connect to the Meshtastic device."""
        self._establish_connection()

        # Start send queue thread
        self._stop_event.clear()
        self._send_thread = threading.Thread(
            target=self._send_loop,
            name=f"mesh-send-{self._conn_str}",
            daemon=True,
        )
        self._send_thread.start()

        # Start health monitor thread
        self._health_thread = threading.Thread(
            target=self._health_loop,
            name=f"mesh-health-{self._conn_str}",
            daemon=True,
        )
        self._health_thread.start()

    def _establish_connection(self) -> None:
        """Create the Meshtastic interface and subscribe to messages."""
        import meshtastic
        import meshtastic.serial_interface
        import meshtastic.tcp_interface
        from pubsub import pub

        if ":" in self._conn_str and not self._conn_str.startswith("/dev"):
            # TCP connection
            host, port = self._conn_str.rsplit(":", 1)
            logger.info(f"Connecting via TCP to {host}:{port}")
            self._interface = meshtastic.tcp_interface.TCPInterface(
                hostname=host, portNumber=int(port)
            )
        else:
            # Serial connection
            logger.info(f"Connecting via serial to {self._conn_str}")
            self._interface = meshtastic.serial_interface.SerialInterface(
                devPath=self._conn_str
            )

        # Get our node ID
        node_info = self._interface.getMyNodeInfo()
        self._my_node_id = node_info.get("user", {}).get("id", "")
        logger.info(f"Connected as {self._my_node_id}")

        # Subscribe to incoming messages (only once — pubsub deduplicates
        # by listener identity, but we guard explicitly to be safe)
        if not self._subscribed:
            pub.subscribe(self._handle_packet, "meshtastic.receive.text")
            self._subscribed = True

        self._connected = True

    def disconnect(self) -> None:
        """Disconnect from the device."""
        self._stop_event.set()
        if self._send_thread:
            self._send_thread.join(timeout=5)
            self._send_thread = None
        if self._health_thread:
            self._health_thread.join(timeout=5)
            self._health_thread = None
        self._connected = False
        if self._interface:
            try:
                self._interface.close()
            except Exception:
                pass
            self._interface = None
            logger.info("Disconnected from Meshtastic device")

    def set_message_callback(self, callback: Callable[[MeshMessage], None]) -> None:
        """Set the callback for incoming messages.

        Args:
            callback: Function to call with each incoming MeshMessage.
        """
        self._on_message = callback

    def send_dm(self, dest_id: str, text: str) -> None:
        """Queue a direct message for sending.

        Args:
            dest_id: Destination node ID.
            text: Message text (must be <= 175 chars).
        """
        if not self._connected:
            logger.error("Not connected — cannot send DM")
            return
        self._send_queue.put(("dm", dest_id, text))

    def send_broadcast(self, text: str, channel: Optional[int] = None) -> None:
        """Queue a broadcast message for sending.

        Args:
            text: Message text (must be <= 175 chars).
            channel: Channel index (defaults to configured channel).
        """
        if not self._connected:
            logger.error("Not connected — cannot send broadcast")
            return
        ch = channel if channel is not None else self._channel
        self._send_queue.put(("broadcast", ch, text))

    @property
    def send_queue_depth(self) -> int:
        """Current number of messages waiting to send."""
        return self._send_queue.qsize()

    @property
    def connected(self) -> bool:
        """Whether the transport believes it has a live connection."""
        return self._connected

    @property
    def my_node_id(self) -> Optional[str]:
        """Our Meshtastic node ID."""
        return self._my_node_id

    def get_node_config(self) -> dict:
        """Read current node configuration from the connected device.

        Returns dict with identity, LoRa, device role, and channel info.
        """
        if not self._interface:
            return {}

        node = self._interface.localNode
        lora = node.localConfig.lora
        device = node.localConfig.device

        # User info lives on the interface's nodeInfo, not on localNode
        user_info = self._interface.getMyNodeInfo().get("user", {})

        channels = []
        for ch in node.channels:
            channels.append({
                "index": ch.index,
                "role": int(ch.role),  # 0=DISABLED, 1=PRIMARY, 2=SECONDARY
                "name": ch.settings.name,
                "psk": ch.settings.psk.hex() if ch.settings.psk else "",
            })

        return {
            "long_name": user_info.get("longName", ""),
            "short_name": user_info.get("shortName", ""),
            "hw_model": user_info.get("hwModel", ""),
            "node_id": self._my_node_id,
            "lora": {
                "modem_preset": int(lora.modem_preset),
                "tx_power": lora.tx_power,
                "region": int(lora.region),
                "channel_num": lora.channel_num,
                "tx_enabled": lora.tx_enabled,
            },
            "device_role": int(device.role),
            "channels": channels,
        }

    def set_owner(self, long_name: str, short_name: str) -> None:
        """Set the device owner (long and short name)."""
        if not self._interface:
            logger.error("Not connected — cannot set owner")
            return
        self._interface.localNode.setOwner(long_name=long_name, short_name=short_name)
        logger.info(f"Owner set: {long_name} ({short_name})")

    def set_channel(self, index: int, name: str = None, psk_hex: str = None) -> None:
        """Set channel name and/or PSK by channel index.

        Args:
            index: Channel index (0 = primary).
            name: New channel name (None to leave unchanged).
            psk_hex: New PSK as hex string (None to leave unchanged, "" to clear).
        """
        if not self._interface:
            logger.error("Not connected — cannot set channel")
            return

        ch = self._interface.localNode.getChannelByChannelIndex(index)
        if not ch:
            logger.error(f"Channel {index} not found")
            return

        if name is not None:
            ch.settings.name = name
        if psk_hex is not None:
            ch.settings.psk = bytes.fromhex(psk_hex) if psk_hex else b""

        self._interface.localNode.writeChannel(index)
        logger.info(f"Channel {index} updated")

    def set_lora(self, **kwargs) -> None:
        """Set LoRa config fields.

        Valid keys: modem_preset, tx_power, region, channel_num, tx_enabled.
        """
        if not self._interface:
            logger.error("Not connected — cannot set LoRa config")
            return

        lora = self._interface.localNode.localConfig.lora
        for k, v in kwargs.items():
            if hasattr(lora, k):
                setattr(lora, k, v)
            else:
                logger.warning(f"Unknown LoRa field: {k}")

        self._interface.localNode.writeConfig("lora")
        logger.info(f"LoRa config updated: {kwargs}")

    # ── Connection Health & Reconnect ──────────────────────────────────────

    def _health_loop(self) -> None:
        """Background thread: periodically check connection, reconnect if dead."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self.HEARTBEAT_INTERVAL)
            if self._stop_event.is_set():
                break

            # Clean up stale pending ACK entries
            self._expire_pending()

            if not self._connected or not self._interface:
                self._attempt_reconnect()
                continue

            try:
                self._interface.getMyNodeInfo()
            except Exception as e:
                logger.error(f"Connection health check failed: {e}")
                self._connected = False
                self._attempt_reconnect()

    def _attempt_reconnect(self) -> bool:
        """Tear down dead connection and re-establish.

        Uses a lock to prevent concurrent reconnect from health thread
        and send thread. Returns True on success.
        """
        if not self._reconnect_lock.acquire(blocking=False):
            return self._connected  # Another thread is already reconnecting

        try:
            for attempt in range(1, self.MAX_RECONNECT_ATTEMPTS + 1):
                if self._stop_event.is_set():
                    return False

                logger.info(
                    f"Reconnect attempt {attempt}/{self.MAX_RECONNECT_ATTEMPTS}"
                    f" for {self._conn_str}"
                )

                # Tear down old interface
                try:
                    if self._interface:
                        self._interface.close()
                except Exception:
                    pass
                self._interface = None
                self._connected = False

                self._stop_event.wait(self.RECONNECT_DELAY)
                if self._stop_event.is_set():
                    return False

                try:
                    self._establish_connection()
                    logger.info(
                        f"Reconnected to {self._conn_str} as {self._my_node_id}"
                    )
                    return True
                except Exception as e:
                    logger.warning(f"Reconnect attempt {attempt} failed: {e}")

            logger.error(
                f"Failed to reconnect after {self.MAX_RECONNECT_ATTEMPTS} attempts"
            )
            return False
        finally:
            self._reconnect_lock.release()

    def _safe_send(self, send_fn) -> bool:
        """Execute a send function, reconnect on failure, retry once.

        Args:
            send_fn: Callable that performs the actual sendText().

        Returns:
            True if the message was sent successfully.
        """
        for attempt in range(2):
            if not self._connected or not self._interface:
                if not self._attempt_reconnect():
                    return False

            try:
                send_fn()
                return True
            except Exception as e:
                logger.warning(f"Send failed (attempt {attempt + 1}): {e}")
                self._connected = False
                if attempt == 0:
                    self._attempt_reconnect()

        return False

    # ── ACK Tracking ──────────────────────────────────────────────────────

    def _do_send_dm(self, dest_id: str, text: str) -> None:
        """Send a DM via the interface and track for ACK.

        Stores the message as pending for delivery confirmation.
        Retry-tagged messages ([R1], [R2], ...) and [LOST] notices
        do not overwrite the pending entry.
        """
        self._interface.sendText(
            text=text, destinationId=dest_id, wantAck=True
        )
        # Only track non-retry messages as new pending
        if not text.startswith("[R") and not text.startswith("[LOST]"):
            self._pending_acks[dest_id] = PendingMessage(
                dest_id=dest_id, text=text, sent_at=time.time()
            )

    def get_unacked_for(self, node_id: str) -> Optional[str]:
        """Check for unacked outbound message to this node.

        Called by the router when a new inbound arrives. Returns:
        - None if no pending or message is still within ACK window
          (player sending a new command = implicit ACK).
        - Tagged retry text "[R1] ..." if message was likely lost.
        - "[LOST] ..." notice if max retries exhausted.
        """
        pending = self._pending_acks.get(node_id)
        if not pending:
            return None

        age = time.time() - pending.sent_at
        if age < self.ACK_TIMEOUT:
            # Within ACK window — player sending a new command is implicit ACK
            del self._pending_acks[node_id]
            return None

        # Past timeout — message likely lost
        if pending.retry_count >= self.MAX_RETRIES:
            logger.warning(
                f"Max retries reached for {node_id}, dropping: "
                f"{pending.text[:60]}..."
            )
            del self._pending_acks[node_id]
            return "[LOST] Last response failed to deliver. Send your command again."

        pending.retry_count += 1
        pending.sent_at = time.time()  # Reset timer for next check

        tag = f"[R{pending.retry_count}] "
        tagged = tag + pending.text
        if len(tagged) > MSG_CHAR_LIMIT:
            tagged = tag + pending.text[:MSG_CHAR_LIMIT - len(tag)]

        return tagged

    def _expire_pending(self) -> None:
        """Remove stale pending entries older than PENDING_EXPIRE."""
        now = time.time()
        expired = [k for k, v in self._pending_acks.items()
                   if now - v.sent_at > self.PENDING_EXPIRE]
        for k in expired:
            del self._pending_acks[k]

    # ── Send Queue ─────────────────────────────────────────────────────────

    def _send_loop(self) -> None:
        """Background thread: process send queue with inter-message delay."""
        while not self._stop_event.is_set():
            try:
                msg_type, target, text = self._send_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            # Warn on queue buildup
            depth = self._send_queue.qsize()
            if depth > 10:
                logger.warning(f"Send queue depth: {depth}")

            # Wait for minimum interval since last send
            elapsed = time.time() - self._last_send_time
            if elapsed < self.SEND_INTERVAL:
                wait = self.SEND_INTERVAL - elapsed
                self._stop_event.wait(wait)
                if self._stop_event.is_set():
                    break

            # Send with connection recovery
            if msg_type == "dm":
                success = self._safe_send(
                    lambda t=target, x=text: self._do_send_dm(t, x)
                )
            else:
                success = self._safe_send(
                    lambda t=target, x=text: self._interface.sendText(
                        text=x, channelIndex=t
                    )
                )

            if success:
                self._last_send_time = time.time()
            else:
                logger.error(f"Message dropped after reconnect: {text[:60]}...")

            self._send_queue.task_done()

    # ── Inbound ────────────────────────────────────────────────────────────

    def _handle_packet(self, packet: dict, interface=None) -> None:
        """Handle an incoming text packet from the Meshtastic device."""
        try:
            # Only process packets from our own interface
            if interface is not None and interface != self._interface:
                return

            sender_id = packet.get("fromId", "")
            # Ignore our own messages
            if sender_id == self._my_node_id:
                return

            text = packet.get("decoded", {}).get("text", "")
            if not text:
                return

            # Determine sender name
            sender_name = sender_id
            if "from" in packet:
                node_num = packet["from"]
                nodes = self._interface.nodes if self._interface else {}
                for nid, node in nodes.items():
                    if node.get("num") == node_num:
                        user = node.get("user", {})
                        sender_name = user.get("longName") or user.get("shortName") or nid
                        break

            # Determine if DM
            to_id = packet.get("toId", "")
            is_dm = to_id == self._my_node_id
            channel = packet.get("channel", 0)

            msg = MeshMessage(
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                is_dm=is_dm,
                channel=channel,
            )

            dm_label = "DM" if is_dm else "CH"
            logger.info(
                f"[{self._my_node_id}] {dm_label} from {sender_name} ({sender_id}): {text[:80]}"
            )

            if self._on_message:
                self._on_message(msg)

        except Exception as e:
            logger.error(f"Error handling packet: {e}", exc_info=True)
