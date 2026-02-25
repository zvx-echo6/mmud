# MMUD — Mesh Multi-User Dungeon

## Project Overview

MMUD is a text-based multiplayer dungeon crawler designed for Meshtastic LoRa mesh networks. Think BBS door games (Legend of the Red Dragon, TradeWars 2002) adapted for modern mesh radio constraints: 150-character message limits, async play, 5-30 players, 12 dungeon actions per day, 30-day wipe cycles (epochs).

**The complete design document is at `docs/planned.md`.** Read it before making architectural decisions. Every mechanic has been deliberately designed around the constraints of mesh radio play.

## Core Constraints (Non-Negotiable)

- **150 characters per message** — hard ceiling from Meshtastic LoRa
- **Zero runtime LLM calls** — all text content is batch-generated at epoch start and served via template substitution
- **Async-first** — no guarantee two players are ever online simultaneously. Every multiplayer mechanic works through shared state in the database.
- **12 dungeon actions per day** — the primary scarcity mechanic. Town actions are always free.
- **30-day epochs** — everything except player accounts and meta-progression resets

## Tech Stack

- **Python 3.11+** — primary language
- **SQLite** — database (single file, no server, fits the deployment model)
- **Meshtastic Python API** — mesh radio interface for message send/receive
- **Flask 3.x** — web dashboard (Last Ember), runs in-process as daemon thread
- **Jinja2** — server-side templates, no React, no build step
- **Docker** — containerized deployment (python:3.11-slim)

## Architecture

```
src/
  core/           # Game engine — state machine, action processing, combat resolver
    engine.py     # Main game loop: receive message → parse → execute → respond
    actions.py    # Action handlers (move, fight, flee, examine, etc.)
    combat.py     # Combat resolution, damage calc, flee mechanics
    world.py      # World state management, room transitions
  models/         # Database models and state
    player.py     # Player state, stats, inventory, progression
    world.py      # Rooms, floors, monsters, items, secrets
    epoch.py      # Epoch state, mode tracking, breach state, boss state
  generation/     # Epoch generation pipeline (runs once at epoch start)
    worldgen.py   # Dungeon layout, room placement, monster distribution
    secretgen.py  # Secret placement, puzzle generation, hint tiers
    bossgen.py    # Floor boss and raid boss mechanic rolling
    breachgen.py  # Breach zone generation and mini-event selection
    narrative.py  # LLM batch calls for narrative skins, descriptions, hints
    validation.py # 150-char enforcement, hint verb checking, template validation
  systems/        # Game systems
    bounty.py     # Bounty board, shared HP pools, reward distribution
    discovery.py  # Secret tracking, puzzle state, buff management
    broadcast.py  # Broadcast tiers, targeted broadcasts, message queuing
    economy.py    # Gold, shops, banking, death penalties
    barkeep.py    # Recap, bard tokens, hint system
    endgame.py    # Mode-specific logic (R&E, Raid Boss, HtL)
    breach.py     # Breach mini-event logic (Heist, Emergence, Incursion, Resonance)
  transport/      # Message layer
    meshtastic.py # Meshtastic API wrapper, send/receive, DM vs broadcast
    parser.py     # Command parsing from 150-char messages
    formatter.py  # Response formatting under 150-char limit
    router.py     # 6-node message routing (EMBR, DCRG, NPC nodes)
    broadcast_drain.py  # DCRG broadcast delivery
    message_logger.py   # Non-blocking message log writes
  web/            # Last Ember — spectator dashboard & admin panel
    __init__.py   # Flask app factory (create_app)
    config.py     # Web-specific settings (host, port, secret, polling intervals)
    routes/
      public.py   # / /chronicle /howto
      api.py      # /api/status /api/broadcasts /api/bounties /api/mode /api/leaderboard
      admin.py    # /admin/* (session auth)
    services/
      gamedb.py       # SQLite queries (read-only + admin RW via WAL)
      dashboard.py    # Dashboard data aggregation
      chronicle.py    # Epoch history + journal queries
      admin_service.py  # Admin write operations
    templates/        # Jinja2 templates (dark tavern aesthetic)
    static/
      css/ember.css   # Full design system
      js/embers.js    # Canvas particle animation
      js/app.js       # AJAX polling + client logic
    prototypes/       # Original HTML design references
  db/
    schema.sql    # Database schema
    migrations/   # Schema versioning (004 = web tables)
tests/            # Mirror src/ structure
scripts/
  epoch_generate.py  # Run epoch generation pipeline
  epoch_reset.py     # Wipe and start new epoch
docs/
  planned.md         # Complete design document — the source of truth
config.py            # All game constants (see below)
Dockerfile           # python:3.11-slim, /data volume
docker-compose.yml   # Container orchestration
```

## Key Design Patterns

### Message Processing Loop
```
receive_message() → parse_command() → check_action_budget() → execute_action() → format_response() → send_message()
```
Every inbound message follows this pipeline. No exceptions. If the action costs a dungeon action, decrement the budget before executing.

### State Machine
Player state is simple: `town`, `dungeon:floor:room`, `combat:monster_id`, `dead`. Actions are validated against current state — you can't `fight` in town, you can't `shop` in the dungeon.

### Shared HP Pools
Bounties, raid boss, floor bosses, and the Warden all use the same shared HP pool pattern. Store current HP in DB, apply damage on hit, check regen on tick. The only differences are regen rate and mechanic overlays.

### Broadcast Tiers
- **Tier 1**: Server-wide, always delivered. Deaths, discoveries, mode progress.
- **Tier 2**: Server-wide, batched into barkeep recap if player is offline. Bounty progress, combat milestones.
- **Targeted**: Only delivered to players who meet a condition (e.g., have visited a specific room). Used for multi-room puzzle feedback.

### 150-Character Formatting
Every outbound message MUST fit in 150 characters. The formatter is the last gate before send. If a message exceeds 150 chars, it gets truncated with `...` or split into multiple messages. Test every response template against this limit.

## Database Principles

- SQLite single file. No ORM — raw SQL with parameterized queries.
- Player state is one row per player. No joins for the hot path (action processing).
- Shared state (bounty HP, room clear status, boss HP) uses atomic UPDATE statements to handle concurrent access from multiple player sessions.
- Epoch generation writes all content to DB at epoch start. Runtime is pure reads + state updates.

## Config Constants

All tunable game values live in `config.py`. See that file for the complete list with rationale. When in doubt about a number, check config.py first — every value has been deliberately chosen during design.

## Development Priorities

### Phase 1: Core Loop
1. Message receive/send via Meshtastic API
2. Command parser
3. Player creation (name + class)
4. Room navigation (move, look)
5. Basic combat (fight, flee)
6. Death and respawn
7. Action budget enforcement

### Phase 2: Economy & Progression
1. XP and leveling
2. Gold and shops
3. Gear system (weapon, armor, trinket)
4. Bank
5. Healer

### Phase 3: Social Systems
1. Broadcast system (tier 1, tier 2)
2. Barkeep (recap, tokens, hints)
3. Bounty board and shared HP pools
4. Player messages (15-char freeform)
5. Mail system

### Phase 4: Epoch Generation
1. World generation (dungeon layout, rooms, monsters)
2. LLM narrative pipeline (batch generation, validation)
3. Secret placement and hint generation
4. Bounty pool generation

### Phase 5: Endgame Modes
1. Hold the Line (regen, checkpoints, floor bosses)
2. Raid Boss (HP scaling, mechanic tables, phases)
3. Retrieve and Escape (Pursuer, support roles)
4. Epoch vote system

### Phase 6: The Breach
1. Breach zone generation
2. Four mini-event types
3. Breach secret integration
4. Day 15 trigger and barkeep foreshadowing

## Web Dashboard (Last Ember)

The Last Ember is a Flask web dashboard consolidated into `src/web/`. It runs in-process with the mesh daemon as a background daemon thread. It reads from the same SQLite database (WAL mode for concurrent access).

### Key Constraints
- **Read-only** access to game DB on all public routes (uses `file:{path}?mode=ro` URI)
- **Read-write** only for admin operations (node assignment, bans, broadcast)
- **150-character** message limit enforced on admin broadcast
- **No game logic** — display and admin only, never processes game turns
- **use_reloader=False** — critical to prevent forking that would break mesh connections

### Design System
- Dark tavern aesthetic — `static/css/ember.css` (1500+ lines)
- Fonts: Cinzel (headings), Crimson Text (body), JetBrains Mono (data)
- Canvas particle animation (ember sparks) — `static/js/embers.js`
- AJAX polling: status every 30s, broadcasts every 15s — `static/js/app.js`
- Prototypes in `prototypes/` are the design truth

### CLI Integration
- `--web-port PORT` — override web dashboard port (default: 5000)
- `--no-web` — disable the web dashboard entirely
- `MMUD_WEB_PORT`, `MMUD_WEB_HOST`, `MMUD_WEB_SECRET`, `MMUD_ADMIN_PASSWORD` env vars

### Routes
- **Public:** `/` (dashboard), `/chronicle` (epoch history), `/howto` (guide)
- **API:** `/api/status`, `/api/broadcasts`, `/api/bounties`, `/api/mode`, `/api/leaderboard`
- **Admin:** `/admin/*` (session auth, password from `MMUD_ADMIN_PASSWORD`)

### Web Tables (migration 004)
- `node_config` — Meshtastic sim node assignments
- `admin_log` — Admin action audit trail
- `banned_players` — Ban list with reasons
- `npc_journals` — Journal entries for Grist, Maren, Torval, Whisper

## Testing Strategy

- Unit tests for combat math, action budget, damage calculations
- Integration tests for full action pipelines (message in → state change → message out)
- Epoch generation tests (validate all output under 150 chars, check secret placement, verify boss mechanic rolls)
- No mocking the DB — use an in-memory SQLite for tests

## Important Gotchas

- **Never call an LLM at runtime.** All text is pre-generated. If you're tempted to call an LLM during action processing, you're doing it wrong.
- **150 chars is a hard limit**, not a guideline. Test every message template.
- **Actions are atomic.** A player sends one command, gets one response. No multi-step wizards or "are you sure?" confirmations — the radio is too slow for that.
- **Town is free.** Never charge an action for anything that happens in town. The scarcity is dungeon actions only.
- **The design doc is the source of truth.** If code contradicts `docs/planned.md`, the code is wrong.
