# MMUD — Mesh Multi-User Dungeon

## Project Overview

MMUD is a text-based multiplayer dungeon crawler designed for Meshtastic LoRa mesh networks. Think BBS door games (Legend of the Red Dragon, TradeWars 2002) adapted for modern mesh radio constraints: 175-character message limits (200 overflow max), async play, 5-30 players, 12 dungeon actions per day, 30-day wipe cycles (epochs).

**The complete design document is at `docs/planned.md`.** Read it before making architectural decisions. Every mechanic has been deliberately designed around the constraints of mesh radio play.

## Core Constraints (Non-Negotiable)

- **175 characters per message** — target limit for Meshtastic LoRa (200 overflow max)
- **Game engine never calls LLMs at runtime** — all text content is batch-generated at epoch start and served via template substitution. The one exception is NPC conversation DMs, which use runtime LLM calls through a transaction tag (TX) system.
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

## Classes & Resource System

| Class | Stats | HP | Resource | Special | Command |
|-------|-------|----|----------|---------|---------|
| Warrior | POW:3 DEF:2 SPD:1 | 50 | Focus (5) | Charge | `charge` |
| Rogue | POW:2 DEF:1 SPD:3 | 40 | Tricks (5) | Sneak | `sneak` |
| Caster | POW:1 DEF:1 SPD:2 | 35 | Mana (5) | Cast | `cast` |

### Class Abilities

- **Charge** (Warrior, 2 Focus): In combat — 1.5x damage, no counterattack. In dungeon — double-move through 2 rooms (player picks first direction, second is random). Stops on monster contact.
- **Sneak** (Rogue, 1 Trick): In combat — 85% backstab (2x damage + exit combat). In dungeon — 85% bypass monster in room.
- **Cast** (Caster, 1 Mana): In combat — 2x POW magic damage (ignores DEF, uses epoch spell name). In dungeon — reveals hidden room content (gold, lore fragments, secrets). Once per room per player per epoch.

### Resource Regen

- Day tick: +2 (capped at max 5)
- Return to town: +1
- Rest (town, special action): +1
- Death: resource set to max/2

## Architecture

```
src/
  core/           # Game engine — state machine, action processing, combat resolver
    engine.py     # Main game loop: receive message -> parse -> execute -> respond
    actions.py    # 35+ action handlers (move, fight, charge, sneak, cast, rest, etc.)
    combat.py     # Combat resolution, damage calc, flee mechanics
    world.py      # World state management, room transitions, enter/exit combat
  models/         # Database models and state
    player.py     # Player state, stats, inventory, progression, resource management
    world.py      # Rooms, exits, monsters, reveal tracking (player_reveals)
    epoch.py      # Epoch state, mode tracking, spell names
  generation/     # Epoch generation pipeline (runs once at epoch start)
    worldgen.py   # Dungeon layout, room placement, monster distribution, reveal content
    secretgen.py  # Secret placement, puzzle generation, hint tiers
    bossgen.py    # Floor boss and raid boss mechanic rolling
    breachgen.py  # Breach zone generation and mini-event selection
    narrative.py  # LLM backends (Dummy/Anthropic/OpenAI/Google), spell names, lore
    validation.py # 175-char enforcement, spell name validation, lore length checks
  systems/        # Game systems
    bounty.py     # Bounty board, shared HP pools, reward distribution
    broadcast.py  # Broadcast tiers, targeted broadcasts, message queuing
    economy.py    # Gold, shops, banking, effective stats (gear bonuses)
    barkeep.py    # Recap, bard tokens, hint system
    social.py     # Player messages, mail, who list
    daytick.py    # Day rollover: action reset, resource regen, bounty rotation
    npc_conversation.py  # NPC DM conversations via LLM with TX tag transaction system
    endgame_htl.py   # Hold the Line mode
    endgame_raid.py  # Raid Boss mode
    endgame_rne.py   # Retrieve and Escape mode
    breach.py     # Breach activation and state management
    breach_*.py   # Four breach mini-events (heist, emergence, incursion, resonance)
  transport/      # Message layer
    meshtastic.py # Meshtastic API wrapper, send/receive, DM vs broadcast
    parser.py     # Command parsing from 175-char messages
    formatter.py  # Response formatting under 175-char limit
    router.py     # 6-node message routing (EMBR, DCRG, NPC nodes)
    broadcast_drain.py  # DCRG broadcast delivery
    message_logger.py   # Non-blocking message log writes
  web/            # Last Ember — spectator dashboard & admin panel
    __init__.py   # Flask app factory (create_app)
    config.py     # Web-specific settings (host, port, secret, polling intervals)
    routes/
      public.py   # / /chronicle /howto
      api.py      # /api/status /api/broadcasts /api/bounties /api/mode /api/leaderboard
      admin.py    # /admin/* (session auth, LLM config page)
    services/
      gamedb.py       # SQLite queries (read-only + admin RW via WAL)
      dashboard.py    # Dashboard data aggregation
      chronicle.py    # Epoch history + journal queries
      admin_service.py  # Admin write operations (bans, broadcasts, LLM config)
    templates/        # Jinja2 templates (dark tavern aesthetic)
    static/
      css/ember.css   # Full design system
      js/embers.js    # Canvas particle animation
      js/app.js       # AJAX polling + client logic
    prototypes/       # Original HTML design references
  db/
    schema.sql    # Database schema
    migrations/   # Schema versioning (008 = reveal + spells)
tests/            # 900+ tests, mirror src/ structure
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
receive_message() -> parse_command() -> check_action_budget() -> execute_action() -> format_response() -> send_message()
                                                                       |
                                                              (maybe_queue_npc_dm)
```
Every inbound message follows this pipeline. If the action costs a dungeon action, decrement the budget before executing. After execution, the engine checks if the player entered an NPC's town location and queues a greeting DM if cooldown permits.

### State Machine
Player state is simple: `town`, `dungeon`, `combat`, `dead`. Actions are validated against current state — you can't `fight` in town, you can't `shop` in the dungeon.

### NPC Transaction System (TX Tags)
NPC conversations use runtime LLM calls via `npc_conversation.py`. The LLM response must be prefixed with a transaction tag: `[TX:action:detail]`. The system parses the tag, validates it against allowed actions per NPC, executes the game mechanic, then delivers the narrative text as a DM.

| NPC | Allowed TX Actions |
|-----|--------------------|
| Maren (healer) | `heal` |
| Torval (merchant) | `buy`, `sell`, `browse` |
| Grist (barkeep) | `recap`, `hint` |
| Whisper (sage) | `hint` |

### NPC Greeting DMs
When a player moves to a town location where an NPC lives, the engine queues a greeting DM (one per NPC per cooldown period). The NPC greeting is generated by the LLM backend and delivered via the router as a separate message after the main action response.

### Shared HP Pools
Bounties, raid boss, floor bosses, and the Warden all use the same shared HP pool pattern. Store current HP in DB, apply damage on hit, check regen on tick.

### Broadcast Tiers
- **Tier 1**: Server-wide, always delivered. Deaths, discoveries, mode progress.
- **Tier 2**: Server-wide, batched into barkeep recap if player is offline.
- **Targeted**: Only delivered to players who meet a condition.

### 175-Character Formatting
Every outbound message should target 175 characters (200 overflow max). The formatter is the last gate before send. If a message exceeds MSG_CHAR_LIMIT (175), it gets truncated with `...`. Test every response template against this limit.

### Reveal System (Caster)
Rooms are populated with hidden content during epoch generation:
- **reveal_gold** (30-40% of rooms): Gold award on cast reveal
- **reveal_lore** (10-15% of rooms): Lore fragment (<=80 chars) + bard token
- **Secrets**: Auto-detected if undiscovered secret exists in room
- Tracking: `player_reveals` table enforces once-per-room-per-player-per-epoch

### Epoch Spell Names
Three themed spell names are generated per epoch (<=20 chars each) and stored comma-separated in `epoch.spell_names`. In-combat cast uses a random spell name instead of hardcoded text. DummyBackend picks from a pool of 9 names; real LLM backends prompt for themed names.

## Database

- SQLite single file at `/data/mmud.db`. No ORM — raw SQL with parameterized queries.
- Player state is one row per player. No joins for the hot path.
- Shared state uses atomic UPDATE statements for concurrent access.
- Epoch generation writes all content at epoch start. Runtime is reads + state updates.
- Schema migrations via `src/db/migrations/` (currently 008). Engine applies idempotent `ALTER TABLE` on startup.

## Config Constants

All tunable game values live in `config.py`. See that file for the complete list with rationale. Key sections:
- Classes, stats, HP, resource system
- Dungeon layout (floors, rooms per floor, branches)
- Combat math, XP/gold curves
- Ability costs and multipliers
- Reveal chances and ranges
- Spell name / lore fragment pools (DummyBackend)
- Endgame mode constants
- Social action limits, bard token caps

## Web Dashboard (Last Ember)

The Last Ember is a Flask web dashboard in `src/web/`. It runs in-process with the mesh daemon as a daemon thread. Reads from the same SQLite database (WAL mode).

### Key Constraints
- **Read-only** access to game DB on all public routes (`file:{path}?mode=ro`)
- **Read-write** only for admin operations (bans, broadcasts, LLM config)
- **No game logic** — display and admin only, never processes game turns
- **use_reloader=False** — prevents forking that would break mesh connections

### Design System
- Dark tavern aesthetic — `static/css/ember.css`
- Fonts: Cinzel (headings), Crimson Text (body), JetBrains Mono (data)
- Canvas ember particle animation — `static/js/embers.js`
- AJAX polling: status every 30s, broadcasts every 15s
- Class badges: WAR (warrior), CST (caster), ROG (rogue)

### Routes
- **Public:** `/` (dashboard), `/chronicle` (epoch history), `/howto` (class guide)
- **API:** `/api/status`, `/api/broadcasts`, `/api/bounties`, `/api/mode`, `/api/leaderboard`
- **Admin:** `/admin/*` (session auth), `/admin/llm` (LLM backend config)

## Testing

- **900+ tests** — unit tests for combat math, integration tests for full action pipelines, epoch generation validation
- No mocking the DB — use in-memory SQLite
- `python3 -m pytest tests/ -x -v` to run all
- Tests use `helpers.py:generate_test_epoch()` for full epoch setup

## Important Gotchas

- **The game engine never calls an LLM at runtime.** All game text is pre-generated. NPC conversations DO use runtime LLM calls, but that's the `npc_conversation.py` system, not the game engine.
- **175 chars is the target limit** (200 overflow max). Test every message template.
- **Spell names must be <= 20 chars**, lore fragments <= 80 chars.
- **Actions are atomic.** One command in, one response out. No multi-step confirmations.
- **Town is free.** Never charge a dungeon action for anything in town.
- **Class abilities refund resource if dungeon action fails.** Check resource first, then dungeon action. If dungeon action fails, restore the resource.
- **Reveal is once per room per player.** The `player_reveals` table tracks this with a UNIQUE constraint.
- **No max_tokens on LLM calls.** Character limits in prompts and MSG_CHAR_LIMIT enforce output length. Never pass max_tokens — thinking models (Gemini 2.5 Flash) count thinking tokens against the limit, causing truncated responses.
- **The design doc is the source of truth.** If code contradicts `docs/planned.md`, the code is wrong.
