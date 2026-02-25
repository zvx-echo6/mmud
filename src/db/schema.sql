-- MMUD Database Schema
-- SQLite. Single file. No ORM. Parameterized queries only.
-- Epoch generation writes all content here. Runtime is reads + state updates.

-- =============================================================================
-- PERSISTENT (Survives Wipes)
-- =============================================================================

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mesh_id TEXT UNIQUE NOT NULL,       -- Meshtastic node ID
    handle TEXT UNIQUE NOT NULL,        -- Display name
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    total_epochs INTEGER DEFAULT 0,
    epoch_wins INTEGER DEFAULT 0,
    lifetime_kills INTEGER DEFAULT 0,
    longest_hardcore_streak INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    title TEXT NOT NULL,                -- "the Twice-Risen", "Completionist"
    epoch_earned INTEGER,
    earned_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hall_of_fame (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_number INTEGER NOT NULL,
    mode TEXT NOT NULL,                 -- endgame mode that ran
    breach_type TEXT,                   -- breach mini-event type
    narrative_theme TEXT,               -- epoch's narrative skin name
    completed INTEGER DEFAULT 0,       -- 1 if server won
    completed_at DATETIME,
    summary TEXT                        -- LLM-generated epoch summary
);

CREATE TABLE IF NOT EXISTS hall_of_fame_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id INTEGER NOT NULL REFERENCES hall_of_fame(id),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    role TEXT,                          -- "carrier", "blocker", "contributor", etc.
    score INTEGER DEFAULT 0
);

-- =============================================================================
-- EPOCH STATE (Reset Each Wipe)
-- =============================================================================

CREATE TABLE IF NOT EXISTS epoch (
    id INTEGER PRIMARY KEY,            -- Always 1 row
    epoch_number INTEGER NOT NULL,
    start_date DATETIME NOT NULL,
    end_date DATETIME NOT NULL,
    endgame_mode TEXT NOT NULL,         -- retrieve_and_escape, raid_boss, hold_the_line
    breach_type TEXT NOT NULL,          -- heist, emergence, incursion, resonance
    breach_open INTEGER DEFAULT 0,     -- 1 after day 15
    narrative_theme TEXT,
    day_number INTEGER DEFAULT 1
);

-- =============================================================================
-- PLAYERS (Reset Each Wipe)
-- =============================================================================

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    name TEXT NOT NULL,
    class TEXT NOT NULL,
    level INTEGER DEFAULT 1,
    xp INTEGER DEFAULT 0,
    hp INTEGER NOT NULL,
    hp_max INTEGER NOT NULL,
    pow INTEGER NOT NULL,
    def INTEGER NOT NULL,
    spd INTEGER NOT NULL,
    gold_carried INTEGER DEFAULT 0,
    gold_banked INTEGER DEFAULT 0,
    state TEXT DEFAULT 'town',         -- town, dungeon, combat, dead
    floor INTEGER DEFAULT 0,           -- 0 = town
    room_id INTEGER,
    combat_monster_id INTEGER,
    hardcore INTEGER DEFAULT 0,        -- 1 = permadeath enabled
    dungeon_actions_remaining INTEGER DEFAULT 12,
    social_actions_remaining INTEGER DEFAULT 2,
    special_actions_remaining INTEGER DEFAULT 1,
    stat_points INTEGER DEFAULT 0,
    bard_tokens INTEGER DEFAULT 0,
    secrets_found INTEGER DEFAULT 0,
    last_login DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    slot TEXT,                          -- weapon, armor, trinket, or NULL (backpack)
    equipped INTEGER DEFAULT 0
);

-- =============================================================================
-- WORLD (Generated at Epoch Start)
-- =============================================================================

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    floor INTEGER NOT NULL,
    name TEXT NOT NULL,                 -- Unique memorable name
    description TEXT NOT NULL,          -- Full description (≤150 chars)
    description_short TEXT NOT NULL,    -- Revisit abbreviated (≤150 chars)
    is_hub INTEGER DEFAULT 0,
    is_checkpoint INTEGER DEFAULT 0,
    is_stairway INTEGER DEFAULT 0,     -- Connects to next floor
    is_breach INTEGER DEFAULT 0,       -- Part of Breach zone
    is_vault INTEGER DEFAULT 0,
    trap_type TEXT,                     -- NULL, physical, status, environmental
    riddle_answer TEXT,                 -- NULL if no riddle
    htl_cleared INTEGER DEFAULT 0,     -- Hold the Line: 1 = currently clear
    htl_cleared_at DATETIME,
    ward_active INTEGER DEFAULT 0      -- R&E: warded room slows Pursuer
);

CREATE TABLE IF NOT EXISTS room_exits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_room_id INTEGER NOT NULL REFERENCES rooms(id),
    to_room_id INTEGER NOT NULL REFERENCES rooms(id),
    direction TEXT NOT NULL             -- n, s, e, w, u, d
);

CREATE TABLE IF NOT EXISTS monsters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    name TEXT NOT NULL,
    hp INTEGER NOT NULL,
    hp_max INTEGER NOT NULL,
    pow INTEGER NOT NULL,
    def INTEGER NOT NULL,
    spd INTEGER NOT NULL,
    xp_reward INTEGER NOT NULL,
    gold_reward_min INTEGER NOT NULL,
    gold_reward_max INTEGER NOT NULL,
    tier INTEGER NOT NULL,             -- 1-6
    is_bounty INTEGER DEFAULT 0,
    is_floor_boss INTEGER DEFAULT 0,
    is_breach_boss INTEGER DEFAULT 0,
    mechanic TEXT,                      -- Rolled mechanic name or NULL
    respawns_remaining INTEGER DEFAULT 0  -- For HtL bounty monsters
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slot TEXT NOT NULL,                 -- weapon, armor, trinket, consumable
    tier INTEGER NOT NULL,
    pow_mod INTEGER DEFAULT 0,
    def_mod INTEGER DEFAULT 0,
    spd_mod INTEGER DEFAULT 0,
    special TEXT,                       -- JSON for special effects
    description TEXT,
    floor_source INTEGER               -- Which floor this drops on
);

-- =============================================================================
-- SECRETS & DISCOVERIES
-- =============================================================================

CREATE TABLE IF NOT EXISTS secrets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                 -- observation, puzzle, lore, stat_gated, breach
    floor INTEGER NOT NULL,
    room_id INTEGER REFERENCES rooms(id),
    name TEXT NOT NULL,
    description TEXT,                   -- What the player sees on discovery
    reward_type TEXT NOT NULL,          -- lore_fragment, stat_bump, consumable, shortcut
    reward_data TEXT,                   -- JSON for reward details
    hint_tier1 TEXT,                    -- Vague barkeep hint
    hint_tier2 TEXT,                    -- Directional hint
    hint_tier3 TEXT,                    -- Targeted hint
    discovered_by INTEGER REFERENCES players(id),
    discovered_at DATETIME,
    -- Multi-room puzzle fields
    puzzle_group TEXT,                  -- Shared ID for linked puzzle rooms
    puzzle_archetype TEXT,              -- paired_mechanism, sequence_lock, cooperative_trigger
    puzzle_order INTEGER,              -- Sequence position (for sequence_lock)
    puzzle_symbol TEXT                  -- Shared descriptive element
);

CREATE TABLE IF NOT EXISTS secret_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    secret_id INTEGER NOT NULL REFERENCES secrets(id),
    found INTEGER DEFAULT 0,
    found_at DATETIME,
    UNIQUE(player_id, secret_id)
);

CREATE TABLE IF NOT EXISTS discovery_buffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buff_type TEXT NOT NULL,            -- stat_boost, regen_halve, ambush_reveal, etc.
    buff_data TEXT,                     -- JSON for specifics
    activated_by INTEGER REFERENCES players(id),
    activated_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    floor INTEGER                       -- Which floor affected, NULL = all
);

-- =============================================================================
-- BOUNTIES
-- =============================================================================

CREATE TABLE IF NOT EXISTS bounties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                 -- kill, explore, deliver, slay_count
    description TEXT NOT NULL,          -- ≤150 chars
    target_monster_id INTEGER REFERENCES monsters(id),
    target_value INTEGER NOT NULL,      -- HP total, room count, gold amount, kill count
    current_value INTEGER DEFAULT 0,    -- Progress toward target
    floor_min INTEGER NOT NULL,
    floor_max INTEGER NOT NULL,
    phase TEXT NOT NULL,                -- early, mid, late
    available_from_day INTEGER NOT NULL,
    active INTEGER DEFAULT 0,          -- 1 = currently on the board
    completed INTEGER DEFAULT 0,
    completed_at DATETIME
);

CREATE TABLE IF NOT EXISTS bounty_contributors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bounty_id INTEGER NOT NULL REFERENCES bounties(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    contribution INTEGER DEFAULT 0,     -- Damage dealt, rooms explored, etc.
    UNIQUE(bounty_id, player_id)
);

-- =============================================================================
-- RAID BOSS (Epoch-specific, only exists during Raid Boss mode)
-- =============================================================================

CREATE TABLE IF NOT EXISTS raid_boss (
    id INTEGER PRIMARY KEY,            -- Always 1 row when active
    name TEXT NOT NULL,
    hp INTEGER NOT NULL,
    hp_max INTEGER NOT NULL,
    floor INTEGER NOT NULL,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    regen_rate REAL NOT NULL,          -- 0.03 = 3%
    mechanics TEXT NOT NULL,           -- JSON array of rolled mechanic names
    phase INTEGER DEFAULT 1,           -- Current phase (1, 2, 3)
    last_regen_at DATETIME,
    last_burst_at DATETIME             -- For regen_burst mechanic
);

CREATE TABLE IF NOT EXISTS raid_boss_contributors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    total_damage INTEGER DEFAULT 0,
    last_engaged_at DATETIME,
    lockout_until DATETIME,            -- For lockout mechanic
    UNIQUE(player_id)
);

-- =============================================================================
-- RETRIEVE AND ESCAPE (Epoch-specific)
-- =============================================================================

CREATE TABLE IF NOT EXISTS escape_run (
    id INTEGER PRIMARY KEY,            -- Always 1 row when active
    objective_name TEXT NOT NULL,
    carrier_player_id INTEGER REFERENCES players(id),
    carrier_room_id INTEGER REFERENCES rooms(id),
    pursuer_room_id INTEGER REFERENCES rooms(id),
    pursuer_ticks INTEGER DEFAULT 0,   -- Tracks fractional advancement
    objective_dropped INTEGER DEFAULT 0,
    dropped_room_id INTEGER REFERENCES rooms(id),
    active INTEGER DEFAULT 0,          -- 1 = run in progress
    started_at DATETIME,
    completed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS escape_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    role TEXT NOT NULL,                 -- carrier, blocker, warder, lurer, guardian_fighter, route_clearer
    contribution TEXT,                  -- JSON details
    UNIQUE(player_id, role)
);

-- =============================================================================
-- HOLD THE LINE (Epoch-specific)
-- =============================================================================

CREATE TABLE IF NOT EXISTS htl_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    floor INTEGER NOT NULL,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    position TEXT NOT NULL,             -- hub, midpoint, stairway, warden
    established INTEGER DEFAULT 0,
    established_at DATETIME,
    established_by INTEGER REFERENCES players(id)
);

-- Floor boss state tracked in monsters table (is_floor_boss = 1)

-- =============================================================================
-- BREACH
-- =============================================================================

CREATE TABLE IF NOT EXISTS breach (
    id INTEGER PRIMARY KEY,            -- Always 1 row
    mini_event TEXT NOT NULL,           -- heist, emergence, incursion, resonance
    active INTEGER DEFAULT 0,
    opened_at DATETIME,
    -- Emergence fields
    emergence_hp INTEGER,
    emergence_hp_max INTEGER,
    -- Incursion fields
    incursion_hold_started_at DATETIME,
    -- Heist fields
    heist_artifact_room_id INTEGER REFERENCES rooms(id),
    heist_artifact_carrier INTEGER REFERENCES players(id),
    heist_pursuer_room_id INTEGER REFERENCES rooms(id),
    heist_pursuer_ticks INTEGER DEFAULT 0,
    -- Completion
    completed INTEGER DEFAULT 0,
    completed_at DATETIME
);

-- =============================================================================
-- BREACH EMERGENCE CONTRIBUTORS
-- =============================================================================

CREATE TABLE IF NOT EXISTS breach_emergence_contributors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    total_damage INTEGER DEFAULT 0,
    UNIQUE(player_id)
);

-- =============================================================================
-- SOCIAL
-- =============================================================================

CREATE TABLE IF NOT EXISTS player_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    message TEXT NOT NULL,              -- ≤15 chars
    helpful_votes INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_player_id INTEGER NOT NULL REFERENCES players(id),
    to_player_id INTEGER NOT NULL REFERENCES players(id),
    message TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tier INTEGER NOT NULL,              -- 1, 2
    targeted INTEGER DEFAULT 0,         -- 1 = conditional delivery
    target_condition TEXT,              -- JSON for targeted broadcast conditions
    message TEXT NOT NULL,              -- ≤150 chars
    dcrg_sent INTEGER DEFAULT 0,       -- 1 = already sent via DCRG node
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS broadcast_seen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(broadcast_id, player_id)
);

-- =============================================================================
-- VOTES
-- =============================================================================

CREATE TABLE IF NOT EXISTS epoch_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    mode TEXT NOT NULL,
    voted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id)                   -- One vote per player, changeable via UPSERT
);

-- =============================================================================
-- NARRATIVE CONTENT (Generated at Epoch Start)
-- =============================================================================

CREATE TABLE IF NOT EXISTS narrative_skins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,               -- endgame_mode, breach, floor_boss_1, etc.
    skin_type TEXT NOT NULL,            -- title, description, broadcast_template, briefing
    content TEXT NOT NULL,              -- ≤150 chars
    variable_slots TEXT                 -- JSON list of {variable} names in template
);

CREATE TABLE IF NOT EXISTS npc_dialogue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc TEXT NOT NULL,                  -- grist, maren, torval, whisper
    context TEXT NOT NULL,              -- greeting, hint, recap, token_spend, etc.
    dialogue TEXT NOT NULL,             -- ≤150 chars
    used INTEGER DEFAULT 0             -- Track usage to avoid repetition
);

-- =============================================================================
-- MESSAGE LOG (Full Mesh Traffic Visibility)
-- =============================================================================

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

-- =============================================================================
-- WEB DASHBOARD (Last Ember)
-- =============================================================================

CREATE TABLE IF NOT EXISTS node_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT UNIQUE NOT NULL,        -- embr, dcrg, grst, mrn, trvl, wspr
    mesh_node_id TEXT,                -- Meshtastic node ID (!hex), auto-discovered
    connection TEXT,                  -- TCP host:port or serial path
    display_name TEXT NOT NULL,       -- "EMBR", "GRST", etc.
    description TEXT,                 -- "The Last Ember — Game Server"
    active INTEGER DEFAULT 1,
    last_seen DATETIME
);

INSERT OR IGNORE INTO node_config (role, display_name, description) VALUES
    ('embr', 'EMBR', 'The Last Ember — Game Server'),
    ('dcrg', 'DCRG', 'The Darkcragg Depths — Broadcast Node'),
    ('grst', 'GRST', 'Grist — Barkeep'),
    ('mrn',  'MRN',  'Maren — Healer'),
    ('trvl', 'TRVL', 'Torval — Merchant'),
    ('wspr', 'WSPR', 'Whisper — Sage');

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

CREATE TABLE IF NOT EXISTS llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    backend TEXT NOT NULL DEFAULT 'dummy',  -- dummy, anthropic, openai, google
    api_key TEXT DEFAULT '',
    model TEXT DEFAULT '',
    base_url TEXT DEFAULT '',               -- OpenAI-compatible endpoints only
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT DEFAULT ''
);
INSERT OR IGNORE INTO llm_config (id, backend) VALUES (1, 'dummy');

CREATE TABLE IF NOT EXISTS join_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    channel_name TEXT DEFAULT '',
    channel_psk TEXT DEFAULT '',           -- displayed to players (hex or base64)
    modem_preset TEXT DEFAULT 'LONG_FAST',
    region TEXT DEFAULT 'US',
    channel_num INTEGER DEFAULT 0,
    game_node_name TEXT DEFAULT 'EMBR',    -- which node players DM to join
    custom_instructions TEXT DEFAULT '',    -- free-form operator notes
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT DEFAULT ''
);
INSERT OR IGNORE INTO join_config (id) VALUES (1);

-- NPC persistent memory — one summary per player-NPC pair, updated each conversation
CREATE TABLE IF NOT EXISTS npc_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    npc TEXT NOT NULL,                -- grist, maren, torval, whisper
    summary TEXT NOT NULL DEFAULT '', -- Compact memory summary (key facts, impressions)
    turn_count INTEGER DEFAULT 0,    -- Total conversation turns with this NPC
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, npc)
);
CREATE INDEX IF NOT EXISTS idx_npc_memory_player ON npc_memory(player_id, npc);

-- =============================================================================
-- INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_rooms_floor ON rooms(floor);
CREATE INDEX IF NOT EXISTS idx_monsters_room ON monsters(room_id);
CREATE INDEX IF NOT EXISTS idx_room_exits_from ON room_exits(from_room_id);
CREATE INDEX IF NOT EXISTS idx_players_account ON players(account_id);
CREATE INDEX IF NOT EXISTS idx_inventory_player ON inventory(player_id);
CREATE INDEX IF NOT EXISTS idx_secrets_floor ON secrets(floor);
CREATE INDEX IF NOT EXISTS idx_secret_progress_player ON secret_progress(player_id);
CREATE INDEX IF NOT EXISTS idx_bounty_contributors_bounty ON bounty_contributors(bounty_id);
CREATE INDEX IF NOT EXISTS idx_broadcasts_tier ON broadcasts(tier);
CREATE INDEX IF NOT EXISTS idx_broadcast_seen_player ON broadcast_seen(player_id);
CREATE INDEX IF NOT EXISTS idx_player_messages_room ON player_messages(room_id);
CREATE INDEX IF NOT EXISTS idx_message_log_timestamp ON message_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_message_log_node ON message_log(node);
CREATE INDEX IF NOT EXISTS idx_message_log_type ON message_log(message_type);
CREATE INDEX IF NOT EXISTS idx_message_log_sender ON message_log(sender_id);
CREATE INDEX IF NOT EXISTS idx_message_log_player ON message_log(player_id);
