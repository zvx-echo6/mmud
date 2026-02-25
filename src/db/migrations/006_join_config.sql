-- Join configuration — editable mesh join instructions for players
CREATE TABLE IF NOT EXISTS join_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    channel_name TEXT DEFAULT '',
    channel_psk TEXT DEFAULT '',           -- displayed to players (hex or base64)
    modem_preset TEXT DEFAULT 'LONG_FAST',
    region TEXT DEFAULT 'US',
    channel_num INTEGER DEFAULT 0,
    game_node_name TEXT DEFAULT 'EMBR',    -- which node players DM to join
    custom_instructions TEXT DEFAULT '',    -- free-form operator notes (markdown-ish)
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT DEFAULT ''
);
INSERT OR IGNORE INTO join_config (id) VALUES (1);
