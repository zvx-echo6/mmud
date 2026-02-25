"""
Node Router for MMUD — 6-Node Mesh Architecture.

Routes inbound messages from 6 MeshTransport instances to the
appropriate handler:
  EMBR → GameEngine.process_message() → DM response via EMBR
  DCRG → static rejection (broadcast-only, no inbound)
  GRST/MRN/TRVL/WSPR → NPCConversationHandler → DM response via NPC node

Outbound broadcasts are handled by BroadcastDrain (separate module).
All routing decisions are logged to message_log for traffic visibility.
"""

import logging
import sqlite3
import time
from typing import Optional

from config import DCRG_REJECTION, LLM_OUTPUT_CHAR_LIMIT, MESH_NODES
from src.core.engine import GameEngine
from src.models import player as player_model
from src.systems.npc_conversation import NPCConversationHandler
from src.transport.meshtastic import MeshMessage, MeshTransport
from src.transport.message_logger import log_message

logger = logging.getLogger(__name__)


class NodeRouter:
    """Central message router for the 6-node mesh architecture.

    Holds references to all MeshTransport instances and routes
    inbound messages to the correct handler.
    """

    def __init__(
        self,
        engine: GameEngine,
        npc_handler: NPCConversationHandler,
        transports: Optional[dict[str, MeshTransport]] = None,
    ):
        """Initialize the router.

        Args:
            engine: Game engine for EMBR message processing.
            npc_handler: NPC conversation handler for NPC node messages.
            transports: Dict of node_name → MeshTransport. Can be set later.
        """
        self.engine = engine
        self.npc_handler = npc_handler
        self.transports: dict[str, MeshTransport] = transports or {}

    @property
    def conn(self) -> sqlite3.Connection:
        """Access the database connection via the engine."""
        return self.engine.conn

    def register_transport(self, node_name: str, transport: MeshTransport) -> None:
        """Register a MeshTransport for a node."""
        self.transports[node_name] = transport

    def wire_callbacks(self) -> None:
        """Set message callbacks on all registered transports."""
        # Build set of our own node IDs to filter cross-talk on UDP multicast
        self._own_node_ids = set()
        for transport in self.transports.values():
            if transport.my_node_id:
                self._own_node_ids.add(transport.my_node_id)

        for node_name, transport in self.transports.items():
            # Capture node_name in closure
            def make_callback(name: str):
                def callback(msg: MeshMessage) -> None:
                    self.route_message(name, msg)
                return callback
            transport.set_message_callback(make_callback(node_name))

    def route_message(self, node_name: str, msg: MeshMessage) -> None:
        """Route an inbound message to the correct handler.

        Args:
            node_name: Which node received the message (EMBR, DCRG, etc.).
            msg: The incoming MeshMessage.
        """
        if not msg.is_dm:
            return  # Only process DMs

        # Ignore cross-talk from our own mesh nodes (UDP multicast echo)
        if msg.sender_id in self._own_node_ids:
            return

        node_config = MESH_NODES.get(node_name, {})
        role = node_config.get("role", "")

        try:
            if role == "game":
                self._handle_embr(msg)
            elif role == "broadcast":
                self._handle_dcrg(msg)
            elif role == "npc":
                npc = node_config.get("npc", "")
                self._handle_npc(node_name, npc, msg)
            else:
                logger.warning(f"Unknown node role for {node_name}: {role}")
        except Exception as e:
            logger.error(
                f"Error routing message on {node_name} from {msg.sender_id}: {e}",
                exc_info=True,
            )
            log_message(
                self.conn, node_name, "system", str(e), "error",
                sender_id=msg.sender_id, sender_name=msg.sender_name,
                metadata={"original_message": msg.text},
            )

    def _handle_embr(self, msg: MeshMessage) -> None:
        """Handle a message on the EMBR (game) node."""
        # Detect register vs command by checking if player exists
        player = player_model.get_player_by_mesh_id(self.conn, msg.sender_id)
        is_register = player is None
        player_id = player["id"] if player else None

        # Log inbound
        inbound_type = "register" if is_register else "command"
        log_message(
            self.conn, "EMBR", "inbound", msg.text, inbound_type,
            sender_id=msg.sender_id, sender_name=msg.sender_name,
            player_id=player_id,
        )

        response = self.engine.process_message(
            msg.sender_id, msg.sender_name, msg.text
        )
        if response:
            # Log outbound
            outbound_type = "register_response" if is_register else "response"
            # Re-resolve player_id for newly registered players
            if is_register and player_id is None:
                new_player = player_model.get_player_by_mesh_id(self.conn, msg.sender_id)
                if new_player:
                    player_id = new_player["id"]

            log_message(
                self.conn, "EMBR", "outbound", response, outbound_type,
                sender_id=msg.sender_id, sender_name=msg.sender_name,
                recipient_id=msg.sender_id, player_id=player_id,
            )

            transport = self.transports.get("EMBR")
            if transport:
                transport.send_dm(msg.sender_id, response)

    def _handle_dcrg(self, msg: MeshMessage) -> None:
        """Handle a message on the DCRG (broadcast) node — always reject."""
        rejection = DCRG_REJECTION[:LLM_OUTPUT_CHAR_LIMIT]

        # Log inbound + outbound rejection
        log_message(
            self.conn, "DCRG", "inbound", msg.text, "dcrg_rejection",
            sender_id=msg.sender_id, sender_name=msg.sender_name,
        )
        log_message(
            self.conn, "DCRG", "outbound", rejection, "dcrg_rejection",
            sender_id=msg.sender_id, sender_name=msg.sender_name,
            recipient_id=msg.sender_id,
        )

        transport = self.transports.get("DCRG")
        if transport:
            transport.send_dm(msg.sender_id, rejection)

    def _handle_npc(self, node_name: str, npc: str, msg: MeshMessage) -> None:
        """Handle a message on an NPC node."""
        # Log inbound
        log_message(
            self.conn, node_name, "inbound", msg.text, "npc_inbound",
            sender_id=msg.sender_id, sender_name=msg.sender_name,
        )

        t0 = time.monotonic()
        response = self.npc_handler.handle_message(npc, msg.sender_id, msg.text)
        elapsed_ms = (time.monotonic() - t0) * 1000

        if response:
            # Determine outbound type from NPC handler tracking
            result_type = self.npc_handler.last_result_type or "npc_llm"
            player_id = self.npc_handler.last_player_id

            meta = {}
            if result_type == "npc_llm":
                meta["llm_latency_ms"] = round(elapsed_ms, 1)
            if result_type in ("npc_rule1", "npc_rule2"):
                meta["rule_matched"] = result_type
            if result_type == "npc_fallback":
                meta["fallback_reason"] = "llm_error"

            log_message(
                self.conn, node_name, "outbound", response, result_type,
                sender_id=msg.sender_id, sender_name=msg.sender_name,
                recipient_id=msg.sender_id, player_id=player_id,
                metadata=meta if meta else None,
            )

            transport = self.transports.get(node_name)
            if transport:
                transport.send_dm(msg.sender_id, response)
