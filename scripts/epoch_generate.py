#!/usr/bin/env python3
"""
Epoch generation orchestrator for MMUD.
Generates a complete world with all content for a new 30-day epoch.

Pipeline order:
  1. Reset epoch tables
  2. Create epoch record
  3. World generation (rooms, monsters, items)
  4. Breach zone generation
  5. Secret placement (needs breach room IDs)
  6. Bounty pool generation
  7. Boss generation (floor bosses + raid boss pre-roll)
  8. Narrative content (NPC dialogue, narrative skins, atmospheric broadcasts)
  9. Validation pass

Run: python scripts/epoch_generate.py [db_path]
"""

import json
import random
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    BREACH_MINI_EVENTS,
    ENDGAME_MODES,
    FLOOR_THEMES,
    LLM_OUTPUT_CHAR_LIMIT,
    NUM_FLOORS,
)
from src.db.database import get_db, reset_epoch_tables
from src.generation.bossgen import generate_bosses
from src.generation.bountygen import generate_bounties
from src.generation.breachgen import generate_breach
from src.generation.narrative import DummyBackend, get_backend
from src.generation.secretgen import generate_secrets
from src.generation.themegen import generate_floor_themes, get_floor_themes
from src.generation.validation import validate_epoch
from src.generation.worldgen import generate_town, generate_world
from src.models.epoch import create_epoch


def generate_epoch(
    db_path: str = "mmud.db",
    epoch_number: int = 1,
    endgame_mode: str = "",
    breach_type: str = "",
) -> dict:
    """Generate a complete epoch.

    Args:
        db_path: Path to the database file.
        epoch_number: Sequential epoch number.
        endgame_mode: Override endgame mode (random if empty).
        breach_type: Override breach type (random if empty).

    Returns:
        Stats dict with all generation results.
    """
    conn = get_db(db_path)
    backend = get_backend()

    # Select modes
    if not endgame_mode:
        endgame_mode = random.choice(ENDGAME_MODES)
    if not breach_type:
        breach_type = random.choice(BREACH_MINI_EVENTS)

    theme = FLOOR_THEMES.get(1, "The Depths")

    print(f"=== MMUD Epoch #{epoch_number} Generation ===")
    print(f"  Endgame: {endgame_mode}")
    print(f"  Breach: {breach_type}")
    print(f"  Backend: {type(backend).__name__}")
    print()

    stats = {}

    # 1. Reset
    print("[1/9] Resetting epoch tables...")
    reset_epoch_tables(conn)

    # 2. Create epoch
    print("[2/9] Creating epoch record...")
    create_epoch(conn, epoch_number, endgame_mode, breach_type, theme)

    # 2a. Floor sub-themes
    print("[2a/9] Generating floor sub-themes...")
    theme_stats = generate_floor_themes(conn, backend)
    floor_themes = get_floor_themes(conn)
    stats["floor_themes"] = theme_stats
    for f in sorted(floor_themes):
        print(f"  Floor {f}: {floor_themes[f]['floor_name']}")

    # 2b. Town generation (Floor 0)
    print("[2b/9] Generating town (Floor 0)...")
    town_stats = generate_town(conn, backend)
    stats["town"] = town_stats
    print(f"  Town rooms: {town_stats['rooms']}, NPCs: {town_stats['npc_rooms']}")

    # 3. World generation
    print("[3/9] Generating dungeon world...")
    world_stats = generate_world(conn, backend, floor_themes=floor_themes)
    stats["world"] = world_stats
    print(f"  Rooms: {world_stats['rooms']}, Monsters: {world_stats['monsters']}, "
          f"Items: {world_stats['items']}, Exits: {world_stats['exits']}")

    # 4. Breach zone
    print("[4/9] Generating breach zone...")
    breach_stats = generate_breach(conn, backend)
    stats["breach"] = breach_stats
    print(f"  Breach rooms: {breach_stats['rooms']}, "
          f"Mini-event: {breach_stats['mini_event']}")

    # 5. Secrets
    print("[5/9] Placing secrets...")
    secret_stats = generate_secrets(
        conn, backend, breach_room_ids=breach_stats["breach_room_ids"],
        floor_themes=floor_themes,
    )
    stats["secrets"] = secret_stats
    print(f"  Total: {secret_stats['total']} — "
          f"obs:{secret_stats['observation']} puz:{secret_stats['puzzle']} "
          f"lore:{secret_stats['lore']} stat:{secret_stats['stat_gated']} "
          f"breach:{secret_stats['breach']}")

    # 6. Bounties
    print("[6/9] Generating bounty pool...")
    bounty_stats = generate_bounties(conn, backend, floor_themes=floor_themes)
    stats["bounties"] = bounty_stats
    print(f"  Total: {bounty_stats['total']} — "
          f"early:{bounty_stats['early']} mid:{bounty_stats['mid']} "
          f"late:{bounty_stats['late']}")

    # 7. Bosses
    print("[7/9] Generating bosses...")
    boss_stats = generate_bosses(conn, backend, floor_themes=floor_themes)
    stats["bosses"] = boss_stats
    print(f"  Floor bosses: {boss_stats['floor_bosses']}, "
          f"Raid mechanics: {boss_stats['raid_boss_mechanics']}")

    # 8. Narrative content
    print("[8/9] Generating narrative content...")
    narrative_count = _generate_narrative_content(conn, backend, endgame_mode, breach_type, floor_themes)
    stats["narrative"] = narrative_count
    print(f"  NPC dialogue: {narrative_count['dialogue']}, "
          f"Skins: {narrative_count['skins']}, "
          f"Broadcasts: {narrative_count['broadcasts']}")

    # 8b. Seed NPC journals
    journal_count = _seed_npc_journals(conn, epoch_number)
    stats["journals"] = journal_count
    print(f"  NPC journals seeded: {journal_count}")

    # 9. Validation
    print("[9/9] Running validation...")
    validation = validate_epoch(conn)
    stats["validation"] = validation
    if validation["errors"]:
        print(f"  ERRORS: {len(validation['errors'])}")
        for err in validation["errors"]:
            print(f"    ! {err}")
    else:
        print("  No errors.")
    if validation["warnings"]:
        print(f"  WARNINGS: {len(validation['warnings'])}")
        for w in validation["warnings"][:10]:
            print(f"    ? {w}")
        if len(validation["warnings"]) > 10:
            print(f"    ... and {len(validation['warnings']) - 10} more")

    conn.close()

    print()
    print("=== Generation Complete ===")
    total_rooms = world_stats["rooms"] + breach_stats["rooms"]
    print(f"  Total rooms: {total_rooms}")
    print(f"  Total monsters: {world_stats['monsters']}")
    print(f"  Total items: {world_stats['items']}")
    print(f"  Total secrets: {secret_stats['total']}")
    print(f"  Total bounties: {bounty_stats['total']}")
    print(f"  Floor bosses: {boss_stats['floor_bosses']}")
    print(f"  Validation errors: {len(validation['errors'])}")

    return stats


def _generate_narrative_content(
    conn: sqlite3.Connection, backend, endgame_mode: str, breach_type: str,
    floor_themes: dict = None,
) -> dict:
    """Generate NPC dialogue, narrative skins, and atmospheric broadcasts."""
    counts = {"dialogue": 0, "skins": 0, "broadcasts": 0}

    # NPC dialogue for all NPCs and contexts
    npcs = ["grist", "maren", "torval", "whisper"]
    contexts = {
        "grist": ["greeting", "hint", "recap"],
        "maren": ["greeting"],
        "torval": ["greeting"],
        "whisper": ["greeting", "hint"],
    }

    for npc in npcs:
        for context in contexts.get(npc, ["greeting"]):
            for _ in range(3):  # 3 variations each
                f = random.randint(1, NUM_FLOORS)
                if floor_themes and f in floor_themes:
                    theme_name = floor_themes[f]["floor_name"]
                else:
                    theme_name = FLOOR_THEMES.get(f, "")
                dialogue = backend.generate_npc_dialogue(
                    npc, context,
                    floor=f,
                    direction=random.choice(["north", "south", "east", "west"]),
                    theme=theme_name,
                    summary="things happened",
                )
                conn.execute(
                    "INSERT INTO npc_dialogue (npc, context, dialogue) VALUES (?, ?, ?)",
                    (npc, context, dialogue[:LLM_OUTPUT_CHAR_LIMIT]),
                )
                counts["dialogue"] += 1

    # Narrative skins for endgame mode
    for floor in range(1, NUM_FLOORS + 1):
        if floor_themes and floor in floor_themes:
            theme = floor_themes[floor]["floor_name"]
        else:
            theme = FLOOR_THEMES.get(floor, "Unknown")
        skin = backend.generate_narrative_skin(endgame_mode, theme)

        conn.execute(
            """INSERT INTO narrative_skins (target, skin_type, content)
               VALUES (?, ?, ?)""",
            (f"floor_{floor}", "description",
             skin["description"][:LLM_OUTPUT_CHAR_LIMIT]),
        )
        counts["skins"] += 1

    # Endgame mode skin
    skin = backend.generate_narrative_skin(endgame_mode, endgame_mode)
    conn.execute(
        "INSERT INTO narrative_skins (target, skin_type, content) VALUES (?, ?, ?)",
        ("endgame", "title", skin["title"][:LLM_OUTPUT_CHAR_LIMIT]),
    )
    counts["skins"] += 1

    # Breach skin
    skin = backend.generate_narrative_skin("breach", breach_type)
    conn.execute(
        "INSERT INTO narrative_skins (target, skin_type, content) VALUES (?, ?, ?)",
        ("breach", "title", skin["title"][:LLM_OUTPUT_CHAR_LIMIT]),
    )
    counts["skins"] += 1

    # Atmospheric broadcasts
    for floor in range(1, NUM_FLOORS + 1):
        if floor_themes and floor in floor_themes:
            theme = floor_themes[floor]["floor_name"]
        else:
            theme = FLOOR_THEMES.get(floor, "")
        for _ in range(2):
            msg = backend.generate_atmospheric_broadcast(theme)
            conn.execute(
                "INSERT INTO broadcasts (tier, message) VALUES (2, ?)",
                (msg[:LLM_OUTPUT_CHAR_LIMIT],),
            )
            counts["broadcasts"] += 1

    # Spell names (3 per epoch, ≤20 chars each)
    theme = FLOOR_THEMES.get(1, "")
    spell_names = backend.generate_spell_names(theme)
    spell_names = [s[:20] for s in spell_names]  # Enforce limit
    spell_csv = ",".join(spell_names)
    conn.execute(
        "UPDATE epoch SET spell_names = ? WHERE id = 1",
        (spell_csv,),
    )
    counts["spells"] = len(spell_names)

    conn.commit()
    return counts


_JOURNAL_SEEDS = {
    "grist": (
        "New epoch. Walls shifted overnight. Same bar, different dungeon. "
        "Three regulars already. The usual."
    ),
    "maren": (
        "Stocks restocked. New epoch brings new injuries. "
        "Floor 2 fungal burns incoming, I can tell already."
    ),
    "torval": (
        "Fresh inventory. Priced the fire-rated gear higher — "
        "Floor 3 demand always spikes early epoch."
    ),
    "whisper": (
        "...the cycle begins again. The marks have changed. "
        "Something in the pattern is different this time."
    ),
}


def _seed_npc_journals(conn: sqlite3.Connection, epoch_number: int) -> int:
    """Insert Day 1 journal entries for each NPC at epoch start."""
    count = 0
    for npc, content in _JOURNAL_SEEDS.items():
        conn.execute(
            """INSERT OR IGNORE INTO npc_journals (npc, epoch_number, day_number, content)
               VALUES (?, ?, 1, ?)""",
            (npc, epoch_number, content),
        )
        count += 1
    conn.commit()
    return count


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "mmud.db"
    epoch = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    generate_epoch(db, epoch)
