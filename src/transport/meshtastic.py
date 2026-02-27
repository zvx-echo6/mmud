"""
Meshtastic transport layer for MMUD.
Wraps the Meshtastic Python API for message send/receive.
Supports both serial (USB) and TCP connections.

Outbound messages are queued and sent with inter-message delays
to respect LoRa half-duplex timing (LONG_FAST ~1.5s per message).
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class MeshMessage:
    """Incoming message from a Meshtastic node."""
    sender_id: str        # Meshtastic node ID (hex string like "!abcd1234")
    sender_name: str      # Long name or short name
    text: str             # Message body
    is_dm: bool           # True if direct message, False if channel broadcast
    channel: int          # Channel index


class MeshTransport:
    """Meshtastic device interface for MMUD.

    Handles connection to a Meshtastic device via serial or TCP,
    and provides send/receive for game messages.

    Outbound messages are queued and sent by a background thread
    with SEND_INTERVAL spacing to avoid overwhelming the radio.
    """

    # Minimum seconds between sends. LONG_FAST airtime ~1.5s per message
    # plus ACK window. 3 seconds is safe for back-to-back responses.
    SEND_INTERVAL = 3.0

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
        self._send_queue: queue.Queue = queue.Queue()
        self._send_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_send_time: float = 0.0

    def connect(self) -> None:
        """Connect to the Meshtastic device."""
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

        # Subscribe to incoming messages
        pub.subscribe(self._handle_packet, "meshtastic.receive.text")

        # Start send queue thread
        self._stop_event.clear()
        self._send_thread = threading.Thread(
            target=self._send_loop,
            name=f"mesh-send-{self._conn_str}",
            daemon=True,
        )
        self._send_thread.start()

    def disconnect(self) -> None:
        """Disconnect from the device."""
        self._stop_event.set()
        if self._send_thread:
            self._send_thread.join(timeout=5)
            self._send_thread = None
        if self._interface:
            self._interface.close()
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
        if not self._interface:
            logger.error("Not connected — cannot send DM")
            return
        self._send_queue.put(("dm", dest_id, text))

    def send_broadcast(self, text: str, channel: Optional[int] = None) -> None:
        """Queue a broadcast message for sending.

        Args:
            text: Message text (must be <= 175 chars).
            channel: Channel index (defaults to configured channel).
        """
        if not self._interface:
            logger.error("Not connected — cannot send broadcast")
            return
        ch = channel if channel is not None else self._channel
        self._send_queue.put(("broadcast", ch, text))

    @property
    def send_queue_depth(self) -> int:
        """Current number of messages waiting to send."""
        return self._send_queue.qsize()

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

            # Send with one retry on failure
            success = False
            for attempt in range(2):
                try:
                    if msg_type == "dm":
                        logger.debug(f"DM to {target}: {text[:50]}...")
                        self._interface.sendText(
                            text=text,
                            destinationId=target,
                            wantAck=True,
                        )
                    else:
                        logger.debug(f"Broadcast ch{target}: {text[:50]}...")
                        self._interface.sendText(
                            text=text,
                            channelIndex=target,
                        )
                    self._last_send_time = time.time()
                    success = True
                    break
                except Exception as e:
                    logger.warning(f"Send failed (attempt {attempt + 1}): {e}")
                    if attempt == 0:
                        time.sleep(1.0)

            if not success:
                logger.error(f"Message dropped after retries: {text[:60]}...")

            self._send_queue.task_done()

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
