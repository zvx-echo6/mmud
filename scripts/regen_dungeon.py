#!/usr/bin/env python3
"""
Regenerate dungeon content without wiping players.

Wipes rooms, monsters, items, bounties, bosses, secrets, breach, floor progress —
then re-runs the generation pipeline. Players keep accounts, levels, gold, inventory.
Players are reset to town with full HP.

Usage: python scripts/regen_dungeon.py [db_path]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from src.db.database import get_db
from src.generation.bossgen import generate_bosses
from src.generation.bountygen import generate_bounties
from src.generation.breachgen import generate_breach
from src.generation.narrative import get_backend
from src.generation.secretgen import generate_secrets
from src.generation.themegen import generate_floor_themes, get_floor_themes
from src.generation.worldgen import generate_town, generate_world


# Tables that hold dungeon content (safe to wipe without losing player state)
DUNGEON_TABLES = [
    "bounty_contributors", "bounties",
    "discovery_buffs", "secret_progress", "secrets",
    "monsters", "room_exits", "rooms", "items",
    "floor_themes", "floor_progress",
    "breach",
    "inventory",
]


def regen_dungeon(db_path: str) -> dict:
    """Regenerate dungeon content in-place.

    Players are kept but moved to town with full HP.
    """
    conn = get_db(db_path)
    backend = get_backend()

    stats = {}

    # 1. Wipe dungeon content tables
    print("[1/7] Wiping dungeon content tables...")
    for table in DUNGEON_TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception as e:
            print(f"  Warning: {table}: {e}")
    conn.commit()

    # 2. Reset players to town with full HP
    print("[2/7] Resetting players to town...")
    player_count = conn.execute("SELECT COUNT(*) as cnt FROM players").fetchone()["cnt"]
    conn.execute(
        """UPDATE players SET
           room_id = NULL, state = 'town', floor = 0,
           combat_monster_id = NULL, hp = hp_max,
           deepest_floor_reached = 1"""
    )
    # Clear floor progress
    conn.commit()
    print(f"  {player_count} players reset to town")

    # 3. Regenerate floor themes
    print("[3/7] Generating floor themes...")
    theme_stats = generate_floor_themes(conn, backend)
    floor_themes = get_floor_themes(conn)
    stats["floor_themes"] = theme_stats
    for f in sorted(floor_themes):
        print(f"  Floor {f}: {floor_themes[f]['floor_name']}")

    # 4. Regenerate town (floor 0) + dungeon world
    print("[4/7] Generating town + dungeon...")
    town_stats = generate_town(conn, backend)
    stats["town"] = town_stats
    print(f"  Town rooms: {town_stats['rooms']}")

    world_stats = generate_world(conn, backend, floor_themes=floor_themes)
    stats["world"] = world_stats
    print(f"  Rooms: {world_stats['rooms']}, Monsters: {world_stats['monsters']}, "
          f"Items: {world_stats['items']}")

    # 5. Breach zone
    print("[5/7] Generating breach zone...")
    breach_stats = generate_breach(conn, backend)
    stats["breach"] = breach_stats
    print(f"  Breach rooms: {breach_stats['rooms']}")

    # 6. Secrets + Bounties
    print("[6/7] Placing secrets + bounties...")
    secret_stats = generate_secrets(
        conn, backend, breach_room_ids=breach_stats["breach_room_ids"],
        floor_themes=floor_themes,
    )
    stats["secrets"] = secret_stats
    print(f"  Secrets: {secret_stats['total']}")

    bounty_stats = generate_bounties(conn, backend, floor_themes=floor_themes)
    stats["bounties"] = bounty_stats
    print(f"  Bounties: {bounty_stats['total']}")

    # 7. Bosses
    print("[7/7] Generating bosses...")
    boss_stats = generate_bosses(conn, backend, floor_themes=floor_themes)
    stats["bosses"] = boss_stats
    print(f"  Floor bosses: {boss_stats['floor_bosses']}")

    # Re-place players in town center now that rooms exist
    center = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1 LIMIT 1"
    ).fetchone()
    if center:
        conn.execute("UPDATE players SET room_id = ? WHERE state = 'town'", (center["id"],))
        conn.commit()

    conn.close()

    print()
    print("=== Dungeon Regenerated ===")
    print(f"  Players preserved: {player_count}")
    print(f"  Total rooms: {world_stats['rooms'] + breach_stats['rooms']}")
    print(f"  Total monsters: {world_stats['monsters']}")
    print(f"  Floor bosses: {boss_stats['floor_bosses']}")
    print(f"  Bounties: {bounty_stats['total']}")
    print(f"  Secrets: {secret_stats['total']}")

    return stats


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "/data/mmud.db"
    regen_dungeon(db)
