"""
Node Router for MMUD — 6-Node Mesh Architecture.

Routes inbound messages from 6 MeshTransport instances to the
appropriate handler:
  EMBR → GameEngine.process_message() → DM response via EMBR
  DCRG → static rejection (broadcast-only, no inbound)
  GRST/MRN/TRVL/WSPR → NPCConversationHandler → DM response via NPC node

Outbound broadcasts are handled by BroadcastDrain (separate module).
"""

import logging
from typing import Optional

from config import DCRG_REJECTION, LLM_OUTPUT_CHAR_LIMIT, MESH_NODES
from src.core.engine import GameEngine
from src.systems.npc_conversation import NPCConversationHandler
from src.transport.meshtastic import MeshMessage, MeshTransport

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

    def register_transport(self, node_name: str, transport: MeshTransport) -> None:
        """Register a MeshTransport for a node."""
        self.transports[node_name] = transport

    def wire_callbacks(self) -> None:
        """Set message callbacks on all registered transports."""
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

    def _handle_embr(self, msg: MeshMessage) -> None:
        """Handle a message on the EMBR (game) node."""
        response = self.engine.process_message(
            msg.sender_id, msg.sender_name, msg.text
        )
        if response:
            transport = self.transports.get("EMBR")
            if transport:
                transport.send_dm(msg.sender_id, response)

    def _handle_dcrg(self, msg: MeshMessage) -> None:
        """Handle a message on the DCRG (broadcast) node — always reject."""
        rejection = DCRG_REJECTION[:LLM_OUTPUT_CHAR_LIMIT]
        transport = self.transports.get("DCRG")
        if transport:
            transport.send_dm(msg.sender_id, rejection)

    def _handle_npc(self, node_name: str, npc: str, msg: MeshMessage) -> None:
        """Handle a message on an NPC node."""
        response = self.npc_handler.handle_message(npc, msg.sender_id, msg.text)
        if response:
            transport = self.transports.get(node_name)
            if transport:
                transport.send_dm(msg.sender_id, response)
