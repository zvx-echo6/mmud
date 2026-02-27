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
import os
import signal
import sys
import threading
import time

from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    BROADCAST_DRAIN_INTERVAL,
    DAYTICK_HOUR,
    DAYTICK_TIMEZONE,
    MESH_NODES,
    MESSAGE_LOG_RETENTION_DAYS,
)
from src.web import create_app
from src.web import config as web_config
from src.core.engine import GameEngine
from src.db.database import get_db
from src.generation.narrative import get_backend
from src.systems.daytick import run_day_tick
from src.systems.npc_conversation import NPCConversationHandler
from src.transport.broadcast_drain import BroadcastDrain
from src.transport.meshtastic import MeshMessage, MeshTransport
from src.transport.message_logger import log_message, prune_old_logs
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


def _sync_node_info(conn, router: NodeRouter, nodes: dict) -> None:
    """Write discovered node IDs and connection strings to node_config table.

    Called at startup after all nodes connect so the web dashboard
    can display accurate connection info without manual entry.
    """
    # Ensure connection column exists (migration for existing DBs)
    try:
        conn.execute("SELECT connection FROM node_config LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE node_config ADD COLUMN connection TEXT")
        conn.commit()

    for name, cfg in nodes.items():
        transport = router.transports.get(name)
        node_id = transport.my_node_id if transport else None
        conn_str = cfg.get("connection", "")
        role = name.lower()

        conn.execute(
            """UPDATE node_config
               SET mesh_node_id = ?, connection = ?, last_seen = CURRENT_TIMESTAMP
               WHERE role = ?""",
            (node_id, conn_str, role),
        )
    conn.commit()
    logger.info("Node info synced to database")


def _load_node_configs_from_db(conn) -> dict:
    """Load connection strings from node_config table, fall back to env vars.

    On first boot the DB has NULL connections — env vars are used and
    written back to the DB so subsequent boots read from DB only.
    """
    rows = conn.execute(
        "SELECT role, connection FROM node_config WHERE active = 1"
    ).fetchall()

    active_nodes = {}
    for row in rows:
        role_upper = row["role"].upper()
        db_conn = row["connection"] or ""
        env_conn = os.environ.get(f"MMUD_NODE_{role_upper}", "")
        connection = db_conn or env_conn

        if connection:
            # Seed DB from env var on first boot
            if not db_conn and env_conn:
                conn.execute(
                    "UPDATE node_config SET connection = ? WHERE role = ?",
                    (env_conn, row["role"]),
                )
                conn.commit()

            cfg = MESH_NODES.get(role_upper, {})
            active_nodes[role_upper] = {**cfg, "connection": connection}

    return active_nodes


def _check_day_tick(conn, last_tick_date: str) -> str:
    """Check if a wall-clock day tick is due and run it if needed.

    Fires the day tick when:
      - today (America/Boise) > last_tick_date, AND
      - current hour >= DAYTICK_HOUR (10 AM), OR
      - last_tick_date is more than 1 day behind (catch-up)

    Catches up one day at a time if multiple days were missed (e.g. server
    was down for 3 days → fires 3 ticks across successive 5-second cycles).

    Args:
        conn: Database connection.
        last_tick_date: ISO date string (YYYY-MM-DD) of the last tick.

    Returns:
        Updated last_tick_date string.
    """
    from datetime import date, timedelta
    from src.models.epoch import get_epoch

    try:
        epoch = get_epoch(conn)
        if not epoch:
            return last_tick_date

        tz = ZoneInfo(DAYTICK_TIMEZONE)
        now = datetime.now(tz)
        today = now.date()
        last_date = date.fromisoformat(last_tick_date)

        # Already ticked today or in the future (clock skew) — nothing to do
        if last_date >= today:
            return last_tick_date

        yesterday = today - timedelta(days=1)

        if last_date == yesterday:
            # Last tick was yesterday — only fire if it's past DAYTICK_HOUR
            if now.hour < DAYTICK_HOUR:
                return last_tick_date
            # It's past 10 AM — fire today's tick
            next_date = today
        else:
            # Last tick is 2+ days old — catch up one day at a time
            # (fires regardless of hour to catch up quickly)
            next_date = last_date + timedelta(days=1)
            # But don't advance past today
            if next_date > today:
                next_date = today

        # Fire one tick
        stats = run_day_tick(conn)
        new_day = stats.get("new_day", epoch["day_number"])

        log_message(
            conn, "EMBR", "system", f"Day tick: day {new_day}", "daytick",
            metadata=stats,
        )
        logger.info(f"Day tick executed: day {new_day}")

        # Prune old logs once per day tick
        prune_old_logs(conn, MESSAGE_LOG_RETENTION_DAYS)

        return next_date.isoformat()

    except Exception as e:
        logger.error(f"Day tick error: {e}", exc_info=True)
        return last_tick_date


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
    parser.add_argument(
        "--web-port",
        type=int,
        default=None,
        help="Port for the Last Ember web dashboard (default: 5000, or MMUD_WEB_PORT env)",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable the web dashboard entirely",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    # Initialize database
    logger.info(f"Opening database: {args.db}")
    conn = get_db(args.db)

    # Initialize game engine
    engine = GameEngine(conn)

    # Initialize LLM backend and NPC handler
    db_path = os.path.abspath(args.db)
    backend = get_backend(db_path=db_path)
    npc_handler = NPCConversationHandler(conn, backend)

    # Initialize broadcast drain
    drain = BroadcastDrain(conn)

    # Initialize router
    router = NodeRouter(engine, npc_handler)

    # Determine mode: 6-node (DB-first, env fallback) or single-node (--connection)
    active_nodes = _load_node_configs_from_db(conn)

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

    # Track last tick date for wall-clock day tick detection
    from src.models.epoch import get_epoch
    from datetime import date, timedelta
    epoch = get_epoch(conn)
    if epoch and epoch["last_tick_date"]:
        last_tick_date = epoch["last_tick_date"]
    else:
        # No tick recorded yet — default to yesterday so the first check
        # at or after DAYTICK_HOUR will fire immediately
        tz = ZoneInfo(DAYTICK_TIMEZONE)
        last_tick_date = (date.today() - timedelta(days=1)).isoformat()

    # Sync discovered node info to DB for the web dashboard
    if active_nodes:
        _sync_node_info(conn, router, active_nodes)

    # Start web dashboard (Last Ember) in background thread
    if not args.no_web:
        web_port = args.web_port or int(os.environ.get("MMUD_WEB_PORT", web_config.WEB_PORT))
        web_host = os.environ.get("MMUD_WEB_HOST", web_config.WEB_HOST)

        app = create_app(db_path=db_path)
        app.config["NPC_HANDLER"] = npc_handler
        app.config["NODE_ROUTER"] = router
        web_thread = threading.Thread(
            target=app.run,
            kwargs={
                "host": web_host,
                "port": web_port,
                "use_reloader": False,
                "threaded": True,
            },
            daemon=True,
            name="last-ember-web",
        )
        web_thread.start()
        logger.info(f"Last Ember web dashboard started on http://{web_host}:{web_port}")

    # Main loop
    logger.info("MMUD server running. Press Ctrl+C to stop.")
    try:
        while not stop_event.is_set():
            # Periodic session cleanup
            npc_handler.sessions.cleanup()
            # Check for wall-clock day tick
            last_tick_date = _check_day_tick(conn, last_tick_date)
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
