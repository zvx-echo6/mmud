"""
MMUD — Mesh Multi-User Dungeon
Main entry point. Connects to Meshtastic device and runs the game loop.

Usage:
    python -m src.main --connection /dev/ttyUSB0
    python -m src.main --connection 192.168.1.100:4403
    python -m src.main --connection 192.168.1.100:4403 --db mmud.db --channel 0
"""

import argparse
import logging
import signal
import sys
import time

from src.core.engine import GameEngine
from src.db.database import get_db
from src.transport.meshtastic import MeshMessage, MeshTransport

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    parser = argparse.ArgumentParser(
        description="MMUD — Mesh Multi-User Dungeon Server",
        prog="mmud",
    )
    parser.add_argument(
        "--connection", "-c",
        required=True,
        help="Meshtastic connection: serial port or TCP host:port",
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

    # Initialize Meshtastic transport
    transport = MeshTransport(args.connection, channel=args.channel)

    def on_message(msg: MeshMessage) -> None:
        """Handle incoming message through the game engine."""
        if not msg.is_dm:
            return  # Only process DMs

        try:
            response = engine.process_message(msg.sender_id, msg.sender_name, msg.text)
            if response:
                transport.send_dm(msg.sender_id, response)
        except Exception as e:
            logger.error(f"Error processing message from {msg.sender_id}: {e}", exc_info=True)

    transport.set_message_callback(on_message)

    # Handle signals
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        logger.info(f"Received signal {sig}, shutting down...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Connect and run
    logger.info("Connecting to Meshtastic device...")
    transport.connect()
    logger.info(f"MMUD server running. Node ID: {transport.my_node_id}")

    try:
        while running:
            time.sleep(1)
    finally:
        transport.disconnect()
        conn.close()
        logger.info("MMUD server stopped.")


if __name__ == "__main__":
    main()
