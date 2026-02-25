"""
MMUD Game Constants
All tunable values in one place. Every number here was deliberately chosen
during design — check docs/planned.md for rationale before changing.
"""

# =============================================================================
# CORE CONSTRAINTS
# =============================================================================

MSG_CHAR_LIMIT = 150          # Meshtastic LoRa hard ceiling
EPOCH_DAYS = 30               # Full wipe cycle length
BREACH_DAY = 15               # Mid-epoch event trigger

# =============================================================================
# DAILY ACTION BUDGET
# =============================================================================

DUNGEON_ACTIONS_PER_DAY = 12  # Primary scarcity mechanic
SOCIAL_ACTIONS_PER_DAY = 2    # Mail, board posts
SPECIAL_ACTIONS_PER_DAY = 1   # Reserved for future mechanics
BONUS_ACTION_TOKEN_COST = 2   # Bard tokens for +1 dungeon action

# =============================================================================
# CHARACTER SYSTEM
# =============================================================================

MAX_LEVEL = 10
STAT_NAMES = ["POW", "DEF", "SPD"]

# XP curve tuned for 30-day epoch
# Levels 1-4: days 1-5, Levels 5-7: days 6-14, Levels 8-10: days 15-30
XP_PER_LEVEL = [0, 100, 250, 500, 900, 1500, 2300, 3400, 4800, 6500]

GEAR_SLOTS = ["weapon", "armor", "trinket"]
ITEM_TIERS = 6                # Tier 6 = endgame loot drops only
BACKPACK_SIZE = 8

# Classes — three pure, three hybrid, one generalist
CLASSES = {
    "warrior":  {"POW": 3, "DEF": 2, "SPD": 1},
    "guardian":  {"POW": 1, "DEF": 3, "SPD": 2},
    "scout":    {"POW": 2, "DEF": 1, "SPD": 3},
    # Hybrids and generalist TBD during implementation
}

# =============================================================================
# DUNGEON
# =============================================================================

NUM_FLOORS = 4
ROOMS_PER_FLOOR_MIN = 15
ROOMS_PER_FLOOR_MAX = 20
BRANCHES_PER_FLOOR = 3        # Hub-spoke layout, 3-4 branches
ROOMS_PER_BRANCH_MIN = 3
ROOMS_PER_BRANCH_MAX = 5
LOOPS_PER_FLOOR = 1           # 1-2 loops connecting branches
VAULT_ROOMS_PER_FLOOR_MIN = 3
VAULT_ROOMS_PER_FLOOR_MAX = 5
TRAPS_PER_FLOOR = 1           # 1-2 traps guarding optional vault rooms

# Floor themes (narrative skins override these per epoch)
FLOOR_THEMES = {
    1: "Sunken Halls",
    2: "Fungal Depths",
    3: "Ember Caverns",
    4: "Void Reach",
}

# =============================================================================
# COMBAT
# =============================================================================

ACTIONS_PER_ROOM_CLEAR_MIN = 2   # Move in + fight
ACTIONS_PER_ROOM_CLEAR_MAX = 3
FLEE_BASE_CHANCE = 0.6           # Modified by SPD
FLEE_FAIL_DAMAGE_MULT = 1.0     # Take a hit on failed flee
DEATH_GOLD_LOSS_PERCENT = 20    # % of carried gold lost on death

# =============================================================================
# ECONOMY
# =============================================================================

# Single currency, no inflation possible with 30-day wipes
# Bank is a vault — no interest, death penalty tradeoff

STAT_POINTS_PER_LEVEL = 2        # Free stat points awarded on level-up

# Shop prices per tier — exponential matching LORD model
SHOP_PRICES = {
    1: 65,      # Day 1
    2: 250,     # Day 2-3
    3: 900,     # Day 4-5
    4: 3250,    # Day 6-8
    5: 12500,   # Day 9-11
    # Tier 6: loot drops only, never sold
}

SELL_PRICE_PERCENT = 50           # Sell items back at 50% of buy price

# Shop stock unlocks by epoch day
SHOP_TIER_UNLOCK_DAY = {
    1: 1,
    2: 1,
    3: 5,
    4: 8,
    5: 20,
}

# Heal cost: base + (missing_hp * level_mult)
HEAL_COST_PER_HP = 1             # Gold per missing HP
HEAL_LEVEL_MULT = 0.5            # Additional cost multiplier per level

# Loot drop chance on monster kill, by monster tier
LOOT_DROP_CHANCE = {
    1: 0.15,
    2: 0.18,
    3: 0.22,
    4: 0.25,
    5: 0.30,
}

# =============================================================================
# BARD TOKENS
# =============================================================================

BARD_TOKEN_RATE = 1           # Tokens earned per real-world day (passive)
BARD_TOKEN_CAP = 5
BARD_TOKEN_MENU = {
    1: ["hint", "temporary_buff"],       # Hint OR +2 stat buff for 5 rounds
    2: ["floor_reveal", "bonus_action"], # Floor reveal OR +1 dungeon action
    3: ["free_consumable"],              # Random item from current-tier pool
    5: ["rare_item_intel"],              # Exact room + floor of unclaimed stash
}
TEMP_BUFF_AMOUNT = 2          # +2 to chosen stat
TEMP_BUFF_ROUNDS = 5

# =============================================================================
# BOUNTY SYSTEM
# =============================================================================

BOUNTIES_PER_EPOCH = 40
BOUNTY_PHASE_DISTRIBUTION = {
    "early":  {"count": 15, "days": (1, 10),  "floors": (1, 2)},
    "mid":    {"count": 15, "days": (11, 20), "floors": (2, 3)},
    "late":   {"count": 10, "days": (21, 30), "floors": (3, 4)},
}
BOUNTY_ACTIVE_MAX = 2             # 1-2 active at a time
BOUNTY_REGEN_RATE = 0.05          # 5% max HP per 8 hours
BOUNTY_REGEN_INTERVAL_HOURS = 8
BOUNTY_HTL_LIVES = 3             # 2 regenerations during Hold the Line
BOUNTY_REWARD_MODEL = "threshold" # Any contributor gets full reward

# =============================================================================
# SECRETS
# =============================================================================

SECRETS_PER_EPOCH = 20
SECRET_TYPES = {
    "observation": 6,   # Examine room features, gimmes, floors 1-2 heavy
    "puzzle":      4,   # Environmental interactions, multi-room possible
    "lore":        4,   # NPC hints + room exploration
    "stat_gated":  3,   # High DC checks (POW/SPD/DEF), days 15-25
    "breach":      3,   # Available only after day 15 Breach event
}

# Multi-room puzzles: 2-3 of the 4 puzzle secrets
MULTI_ROOM_PUZZLES_MIN = 2
MULTI_ROOM_PUZZLES_MAX = 3
# Archetypes: paired_mechanism, sequence_lock, cooperative_trigger

# Discovery buffs
DISCOVERY_BUFF_DURATION_HOURS = 24
DISCOVERY_BUFF_STACKABLE = True
DISCOVERY_BUFF_CAP = None     # No cap — async action budget is the limiter

# Milestone broadcasts at these thresholds
SECRET_MILESTONES = [5, 10, 15, 20]

# Barkeep hint tiers (pre-generated per secret at epoch start)
HINT_TIERS = {
    1: "vague",       # Floor-level pointer
    2: "directional", # Room cluster or feature type
    3: "targeted",    # Specific clue answering partial discovery
}

# =============================================================================
# HOLD THE LINE — REGEN RATES (Tuned for 30-day epoch)
# =============================================================================

HTL_REGEN_ROOMS_PER_DAY = {
    1: 3,    # Solo gains 1-3/day. Manageable.
    2: 5,    # Solo loses ground. Two players gain slowly.
    3: 7,    # Needs 2-3 consistent players.
    4: 9,    # Serious coordination required.
}

# Checkpoints per floor
HTL_CHECKPOINTS_PER_FLOOR = {
    1: 3,    # Hub, midpoint, stairway
    2: 3,
    3: 3,
    4: 1,    # The Warden — epoch win condition
}

# Checkpoint establishment: clear cluster within one regen window, then kill floor boss
# Floor 4: all rooms clear within regen windows, then fight the Warden

# =============================================================================
# FLOOR BOSS MECHANIC TABLES
# =============================================================================

FLOOR_BOSS_MECHANICS = {
    1: [  # Roll 1 — teaches chip-and-run
        "armored",      # Half damage until 50% HP
        "enraged",      # Double damage below 50%, takes 25% more
        "regenerator",  # 10% heal between sessions
        "stalwart",     # Immune to flee on first attempt
    ],
    2: [  # Roll 1 — introduces conditions
        "warded",       # Discovery secret disables defensive buff
        "phasing",      # Vulnerable every other day
        "draining",     # Steals HP on hit
        "splitting",    # Splits into two half-HP targets at 50%
    ],
    3: [  # Roll 1 — punishes solo play
        "rotating_resistance",  # Immune to highest stat used last session
        "retaliator",          # Reflects % damage back
        "summoner",            # Spawns add each session, must kill first
        "cursed",              # Debuffs top damage dealer next login
    ],
    4: 2,  # Roll 2 from ALL tables above combined
}

# Warden (floor 4 boss)
WARDEN_HP_MIN = 300
WARDEN_HP_MAX = 500
WARDEN_REGEN_RATE = 0.03         # 3% per 8 hours (same as raid boss)
WARDEN_REGEN_INTERVAL_HOURS = 8

# =============================================================================
# RAID BOSS
# =============================================================================

RAID_BOSS_HP_PER_PLAYER = 300    # Base HP = 300 × active players
RAID_BOSS_HP_CAP = 6000
RAID_BOSS_REGEN_RATE = 0.03      # 3% per 8 hours
RAID_BOSS_REGEN_INTERVAL_HOURS = 8
RAID_BOSS_PHASES = [0.66, 0.33]  # Phase transitions at 66% and 33% HP
RAID_BOSS_MECHANIC_ROLLS = (2, 3)  # Roll 2-3 mechanics

# Active player = entered dungeon at least once in first 3 days
ACTIVE_PLAYER_WINDOW_DAYS = 3

RAID_BOSS_MECHANIC_TABLE = {
    "offensive": [
        "windup_strike",    # Every 3rd round, dodge/defend or triple damage
        "flat_damage_boost", # Hits harder than level suggests
        "retribution",      # Burst damage at 75/50/25% thresholds
        "aura_damage",      # Unavoidable damage each round
    ],
    "defensive": [
        "extra_regen",       # 5%/8h instead of 3%/8h
        "armor_phase",       # Half damage until condition met
        "boss_flees",        # Relocates at HP thresholds
        "regen_burst",       # 15% heal once per day instead of spread
    ],
    "control": [
        "no_escape",         # Flee fails below 25% HP
        "summoner",          # 1-2 adds per engagement, kill first
        "lockout",           # Can't reengage for 24h after fighting
        "enrage_timer",      # Damage doubles after 5 rounds
    ],
}

# =============================================================================
# RETRIEVE AND ESCAPE
# =============================================================================

PURSUER_ADVANCE_RATE = 2      # Advances 1 room per N carrier actions
PURSUER_SPAWN_DISTANCE = 3    # Rooms behind carrier on objective claim
PURSUER_RELAY_RESET_DISTANCE = 5  # Rooms behind new carrier on relay
PURSUER_FLEE_BASE_CHANCE = 0.6    # Same as normal flee

# Warding
WARD_ACTION_COST = 1             # Extra action after clearing room
WARD_PURSUER_SLOWDOWN = 2       # Pursuer takes 2 ticks to pass warded room

# Lure
LURE_ACTION_COST = 2
LURE_DIVERT_TICKS = 3           # Pursuer chases lure for 3 ticks
LURE_TOTAL_DELAY_TICKS = 6      # 3 ticks wrong way + 3 ticks back

# Spawn rate modifier during active escape
ESCAPE_SPAWN_RATE_MULTIPLIER = 2.0  # Double monster spawns on all floors

# =============================================================================
# THE BREACH
# =============================================================================

BREACH_ROOMS_MIN = 5
BREACH_ROOMS_MAX = 8
BREACH_CONNECTS_FLOORS = (2, 3)  # Opens between floors 2 and 3
BREACH_SECRETS = 3               # Always 3 Breach-type secrets

BREACH_MINI_EVENTS = [
    "heist",       # Mini Retrieve & Escape
    "emergence",   # Mini Raid Boss (500-800 HP)
    "incursion",   # Mini Hold the Line (2 rooms revert/day, 48h hold)
    "resonance",   # Puzzle dungeon, no combat focus
]

EMERGENCE_HP_MIN = 500
EMERGENCE_HP_MAX = 800
INCURSION_REGEN_ROOMS_PER_DAY = 2
INCURSION_HOLD_HOURS = 48

# =============================================================================
# BROADCASTS
# =============================================================================

BROADCAST_TIERS = {
    1: "immediate",   # Server-wide, always delivered
    2: "batched",     # Server-wide, batched into barkeep recap if offline
    "targeted": "conditional",  # Only to players meeting a condition
}

# =============================================================================
# PLAYER MESSAGES
# =============================================================================

PLAYER_MSG_CHAR_LIMIT = 15    # Freeform, attributed, rated helpful
PLAYER_MSG_PER_ROOM = 1       # One message per room per player

# =============================================================================
# META-PROGRESSION (Survives Wipes)
# =============================================================================

# Persistent: player handle, cross-epoch stats, earned titles, hall of fame
# Everything else resets each epoch

# =============================================================================
# EPOCH GENERATION
# =============================================================================

# Endgame modes — voted on day 30
ENDGAME_MODES = ["retrieve_and_escape", "raid_boss", "hold_the_line"]

# Breach type — random, never voted
# 12 possible epoch configurations (4 breach × 3 endgame)

# Narrative skin theme lists — 20-30 per mode, no reuse within N epochs
THEME_REUSE_COOLDOWN_EPOCHS = 6

# LLM validation
LLM_OUTPUT_CHAR_LIMIT = 150   # Same as MSG_CHAR_LIMIT — all generated text must fit
HINT_FORBIDDEN_VERBS = [
    "examine", "push", "pull", "open", "move",
    "look behind", "try", "investigate", "check",
]

# =============================================================================
# DAILY TIPS
# =============================================================================

TIPS_PHASES = {
    "basic":        (1, 5),
    "intermediate": (6, 15),
    "advanced":     (16, 30),
}

# =============================================================================
# ONBOARDING
# =============================================================================

TUTORIAL_MESSAGES = 3  # Auto-tutorial: first login, first dungeon, first kill

# =============================================================================
# MESH NODES — 6-Node Architecture
# =============================================================================
# Each node connects to a meshtasticd SIM instance via TCP.
# Connection strings from env vars, format: "host:port"

import os

MESH_NODES = {
    "EMBR": {
        "connection": os.environ.get("MMUD_NODE_EMBR", ""),
        "role": "game",
        "description": "The Last Ember — game server",
    },
    "DCRG": {
        "connection": os.environ.get("MMUD_NODE_DCRG", ""),
        "role": "broadcast",
        "description": "The Darkcragg Depths — broadcast node",
    },
    "GRST": {
        "connection": os.environ.get("MMUD_NODE_GRST", ""),
        "role": "npc",
        "npc": "grist",
        "description": "Grist — barkeep",
    },
    "MRN": {
        "connection": os.environ.get("MMUD_NODE_MRN", ""),
        "role": "npc",
        "npc": "maren",
        "description": "Maren — healer",
    },
    "TRVL": {
        "connection": os.environ.get("MMUD_NODE_TRVL", ""),
        "role": "npc",
        "npc": "torval",
        "description": "Torval — merchant",
    },
    "WSPR": {
        "connection": os.environ.get("MMUD_NODE_WSPR", ""),
        "role": "npc",
        "npc": "whisper",
        "description": "Whisper — sage",
    },
}

# =============================================================================
# NPC CONVERSATION — LLM Chat Settings
# =============================================================================

NPC_LLM_MAX_TOKENS = 80           # Keep responses short → fits 150 chars
NPC_LLM_TIMEOUT = 10              # Seconds before fallback to pre-generated dialogue
NPC_SESSION_TTL = 300              # 5 minutes — session memory TTL in seconds

# Static rejection messages for DCRG inbound
DCRG_REJECTION = "The Darkcragg does not answer. It only speaks. DM EMBR to play."

# Static rejection messages per NPC — unknown player (not registered)
NPC_UNKNOWN_PLAYER = {
    "grist":  "Don't know you. DM EMBR to start. Then we'll talk.",
    "maren":  "I only patch up adventurers. DM EMBR to become one.",
    "torval": "No account, no credit, friend. DM EMBR to join up.",
    "whisper": "...not yet. EMBR. Begin there.",
}

# Static rejection messages per NPC — player not in town
NPC_NOT_IN_TOWN = {
    "grist":  "You're not here, {name}. Come back to the bar first.",
    "maren":  "I can hear you're still in the Darkcragg. Come back alive.",
    "torval": "I don't do deliveries. Get back to the Ember.",
    "whisper": "...too far. Return.",
}

# =============================================================================
# BROADCAST DRAIN — DCRG Outbound
# =============================================================================

BROADCAST_DRAIN_INTERVAL = 30      # Seconds between drain cycles
BROADCAST_DRAIN_BATCH_SIZE = 5     # Max broadcasts per drain cycle
BROADCAST_DRAIN_RATE_LIMIT = 3.0   # Minimum seconds between DCRG sends

# =============================================================================
# MESSAGE LOG — Traffic Visibility
# =============================================================================

MESSAGE_LOG_RETENTION_DAYS = 90    # Prune log entries older than this
