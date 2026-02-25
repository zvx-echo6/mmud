"""
MMUD — Mesh Multi-User Dungeon
Main entry point. 6-node mesh architecture.

Nodes:
  EMBR  — Game server (commands → engine → DM responses)
  DCRG  — Broadcast drain (outbound only, rejects inbound DMs)
  GRST  — Grist (NPC barkeep)
  MRN   — Maren (NPC healer)
  TRVL  — Torval (NPC merchant)
  WSPR  — Whisper (NPC sage)

Usage:
    # All 6 nodes via env vars
    export MMUD_NODE_EMBR=192.168.1.100:4403
    export MMUD_NODE_DCRG=192.168.1.101:4403
    export MMUD_NODE_GRST=192.168.1.102:4403
    export MMUD_NODE_MRN=192.168.1.103:4403
    export MMUD_NODE_TRVL=192.168.1.104:4403
    export MMUD_NODE_WSPR=192.168.1.105:4403
    python -m src.main --db mmud.db

    # Single-node mode (backwards compatible)
    python -m src.main --connection 192.168.1.100:4403 --db mmud.db
"""

import argparse
import logging
import signal
import sys
import threading
import time

from config import BROADCAST_DRAIN_INTERVAL, MESH_NODES
from src.core.engine import GameEngine
from src.db.database import get_db
from src.generation.narrative import get_backend
from src.systems.npc_conversation import NPCConversationHandler
from src.transport.broadcast_drain import BroadcastDrain
from src.transport.meshtastic import MeshMessage, MeshTransport
from src.transport.router import NodeRouter

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _connect_node(name: str, conn_str: str, channel: int) -> MeshTransport:
    """Connect a single node transport with retry logic."""
    transport = MeshTransport(conn_str, channel=channel)
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            transport.connect()
            logger.info(f"  {name} connected → {transport.my_node_id}")
            return transport
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"  {name} connect failed (attempt {attempt}): {e}")
                time.sleep(2 * attempt)
            else:
                logger.error(f"  {name} connect failed after {max_retries} attempts: {e}")
                raise
    return transport  # unreachable, satisfies type checker


def _run_drain_loop(drain: BroadcastDrain, interval: float, stop_event: threading.Event) -> None:
    """Background thread: periodically drain broadcasts via DCRG."""
    while not stop_event.is_set():
        try:
            sent = drain.drain_once()
            if sent > 0:
                logger.debug(f"Broadcast drain: sent {sent} messages")
        except Exception as e:
            logger.error(f"Broadcast drain error: {e}", exc_info=True)
        stop_event.wait(interval)


def main():
    parser = argparse.ArgumentParser(
        description="MMUD — Mesh Multi-User Dungeon Server (6-Node Architecture)",
        prog="mmud",
    )
    parser.add_argument(
        "--connection", "-c",
        help="Single-node connection (backwards compatible). "
             "For 6-node mode, use MMUD_NODE_* env vars instead.",
    )
    parser.add_argument(
        "--db",
        default="mmud.db",
        help="Path to SQLite database (default: mmud.db)",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=0,
        help="Meshtastic channel for broadcasts (default: 0)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    # Initialize database
    logger.info(f"Opening database: {args.db}")
    conn = get_db(args.db)

    # Initialize game engine
    engine = GameEngine(conn)

    # Initialize LLM backend and NPC handler
    backend = get_backend()
    npc_handler = NPCConversationHandler(conn, backend)

    # Initialize broadcast drain
    drain = BroadcastDrain(conn)

    # Initialize router
    router = NodeRouter(engine, npc_handler)

    # Determine mode: 6-node (env vars) or single-node (--connection)
    active_nodes = {
        name: cfg for name, cfg in MESH_NODES.items() if cfg["connection"]
    }

    if active_nodes:
        # 6-node mode
        logger.info(f"Starting 6-node mesh architecture ({len(active_nodes)} nodes configured)")
        _start_multi_node(router, drain, active_nodes, args.channel)
    elif args.connection:
        # Single-node backwards-compatible mode
        logger.info("Starting single-node mode (backwards compatible)")
        _start_single_node(engine, args.connection, args.channel)
    else:
        logger.error(
            "No nodes configured. Set MMUD_NODE_* env vars for 6-node mode, "
            "or use --connection for single-node mode."
        )
        sys.exit(1)

    # Signal handling
    stop_event = threading.Event()

    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start broadcast drain thread (6-node mode only)
    drain_thread = None
    if active_nodes and drain.dcrg_transport:
        drain_thread = threading.Thread(
            target=_run_drain_loop,
            args=(drain, BROADCAST_DRAIN_INTERVAL, stop_event),
            daemon=True,
            name="broadcast-drain",
        )
        drain_thread.start()
        logger.info("Broadcast drain started")

    # Main loop
    logger.info("MMUD server running. Press Ctrl+C to stop.")
    try:
        while not stop_event.is_set():
            # Periodic session cleanup
            npc_handler.sessions.cleanup()
            stop_event.wait(5)
    finally:
        _shutdown(router, conn, drain_thread, stop_event)


def _start_multi_node(
    router: NodeRouter,
    drain: BroadcastDrain,
    nodes: dict,
    channel: int,
) -> None:
    """Connect all configured nodes and wire up routing."""
    logger.info("Connecting mesh nodes:")
    for name, cfg in nodes.items():
        try:
            transport = _connect_node(name, cfg["connection"], channel)
            router.register_transport(name, transport)

            # Wire DCRG transport to the drain
            if name == "DCRG":
                drain.set_transport(transport)

        except Exception as e:
            logger.error(f"Failed to connect {name}: {e}")
            logger.error("Continuing with remaining nodes...")

    # Wire message callbacks
    router.wire_callbacks()

    connected = list(router.transports.keys())
    logger.info(f"Connected nodes: {', '.join(connected)}")

    if "EMBR" not in router.transports:
        logger.warning("EMBR not connected — game commands will not work!")


def _start_single_node(engine: GameEngine, conn_str: str, channel: int) -> None:
    """Backwards-compatible single-node mode."""
    transport = MeshTransport(conn_str, channel=channel)

    def on_message(msg: MeshMessage) -> None:
        if not msg.is_dm:
            return
        try:
            response = engine.process_message(msg.sender_id, msg.sender_name, msg.text)
            if response:
                transport.send_dm(msg.sender_id, response)
        except Exception as e:
            logger.error(f"Error processing message from {msg.sender_id}: {e}", exc_info=True)

    transport.set_message_callback(on_message)
    transport.connect()
    logger.info(f"Single-node mode. Node ID: {transport.my_node_id}")


def _shutdown(
    router: NodeRouter,
    conn,
    drain_thread: threading.Thread | None,
    stop_event: threading.Event,
) -> None:
    """Graceful shutdown: disconnect all transports, close DB."""
    logger.info("Shutting down...")

    # Stop drain thread
    stop_event.set()
    if drain_thread and drain_thread.is_alive():
        drain_thread.join(timeout=5)

    # Disconnect all transports
    for name, transport in router.transports.items():
        try:
            transport.disconnect()
            logger.info(f"  {name} disconnected")
        except Exception as e:
            logger.warning(f"  {name} disconnect error: {e}")

    # Close database
    conn.close()
    logger.info("MMUD server stopped.")


if __name__ == "__main__":
    main()
