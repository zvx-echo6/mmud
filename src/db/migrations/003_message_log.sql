-- Migration 003: Add message_log table for full mesh traffic visibility
-- Logs every message flowing through all 6 mesh nodes.

CREATE TABLE IF NOT EXISTS message_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    node TEXT NOT NULL,               -- EMBR, DCRG, GRST, MRN, TRVL, WSPR
    direction TEXT NOT NULL,          -- inbound, outbound, system
    sender_id TEXT,                   -- Meshtastic node ID
    sender_name TEXT,
    recipient_id TEXT,                -- For targeted broadcasts and DMs
    message TEXT,
    message_type TEXT NOT NULL,       -- command, response, register, register_response,
                                      -- broadcast_tier1, broadcast_tier2, broadcast_targeted,
                                      -- dcrg_rejection, npc_rule1, npc_rule2, npc_llm,
                                      -- npc_fallback, npc_inbound, daytick, error
    player_id INTEGER,               -- Resolved player ID (NULL if unknown)
    metadata TEXT                     -- JSON: llm_latency_ms, token_count, session_id, etc.
);

CREATE INDEX IF NOT EXISTS idx_message_log_timestamp ON message_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_message_log_node ON message_log(node);
CREATE INDEX IF NOT EXISTS idx_message_log_type ON message_log(message_type);
CREATE INDEX IF NOT EXISTS idx_message_log_sender ON message_log(sender_id);
CREATE INDEX IF NOT EXISTS idx_message_log_player ON message_log(player_id);
