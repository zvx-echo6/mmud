-- Migration 004: Web dashboard tables (Last Ember consolidation)
-- Additive to MMUD schema — node config, admin log, ban list, NPC journals.

CREATE TABLE IF NOT EXISTS node_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT UNIQUE NOT NULL,        -- embr, dcrg, grst, mrn, trvl, wspr
    mesh_node_id TEXT,                -- Meshtastic node ID (!hex)
    display_name TEXT NOT NULL,       -- "EMBR", "GRST", etc.
    description TEXT,                 -- "The Last Ember — Game Server"
    active INTEGER DEFAULT 1,
    last_seen DATETIME
);

CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin TEXT NOT NULL,
    action TEXT NOT NULL,             -- ban, kick, reset, force_breach, etc.
    target TEXT,                      -- player name, node ID, etc.
    details TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS banned_players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mesh_node_id TEXT UNIQUE NOT NULL,
    reason TEXT,
    banned_by TEXT NOT NULL,
    banned_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS npc_journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc TEXT NOT NULL,                -- grist, maren, torval, whisper
    epoch_number INTEGER NOT NULL,
    day_number INTEGER NOT NULL,
    content TEXT NOT NULL,            -- Full journal entry (LLM generated)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(npc, epoch_number, day_number)
);

-- Seed default node config
INSERT OR IGNORE INTO node_config (role, display_name, description) VALUES
    ('embr', 'EMBR', 'The Last Ember — Game Server'),
    ('dcrg', 'DCRG', 'The Darkcragg Depths — Broadcast Node'),
    ('grst', 'GRST', 'Grist — Barkeep'),
    ('mrn',  'MRN',  'Maren — Healer'),
    ('trvl', 'TRVL', 'Torval — Merchant'),
    ('wspr', 'WSPR', 'Whisper — Sage');
