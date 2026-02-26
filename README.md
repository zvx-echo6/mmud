# MMUD — Mesh Multi-User Dungeon

A text-based multiplayer dungeon crawler for [Meshtastic](https://meshtastic.org/) LoRa mesh networks.

BBS door games (Legend of the Red Dragon, TradeWars 2002) reborn for off-grid mesh radio. 150-character messages, async play, 30-day wipe cycles, 5-30 players on a mesh network.

## What Is This?

MMUD is a MUD that runs over Meshtastic mesh radio. Players send short text commands via their Meshtastic device, and the server responds with game state — room descriptions, combat results, broadcasts of what other players are doing. Sessions are 10-20 minutes per day. Everything is async — no two players need to be online at the same time.

Every 30 days the world wipes and regenerates. New dungeon layout, new narrative skin, new endgame mode (voted by players), new mid-epoch surprise event. Player accounts and titles persist across wipes.

## Key Features

- **150-char message limit** — every response fits in one Meshtastic LoRa packet
- **30-day epochs** — full wipe cycle with progression arc from newbie to endgame
- **3 endgame modes** — Retrieve & Escape (cooperative relay), Raid Boss (shared HP pool), Hold the Line (territory control). Voted each epoch.
- **The Breach** — random mid-epoch event on day 15 with 4 possible mini-events
- **20 secrets per epoch** — 5 discovery types rewarding exploration, puzzle-solving, and lore
- **Async multiplayer** — bounties, shared HP pools, player messages, mail, broadcast channel
- **Zero runtime LLM** — all narrative content batch-generated at epoch start
- **Randomly rolled boss mechanics** — floor bosses and raid bosses roll fresh mechanics each epoch

## Requirements

- Python 3.11+
- Meshtastic device connected via USB/serial or TCP
- SQLite (included with Python)

## Quick Start

```bash
git clone https://github.com/zvx-echo6/mmud.git
cd mmud
pip install -r requirements.txt
python scripts/epoch_generate.py   # Generate first epoch
python src/main.py                 # Start the game server
```

## Documentation

- **[Design Document](docs/planned.md)** — complete game design, every mechanic, every decision
- **[CLAUDE.md](CLAUDE.md)** — architecture guide and development priorities for Claude Code

## Project Status

Early development. Design is complete. Implementation starting with Phase 1 (core game loop).

## License

TBD
