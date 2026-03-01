"""
Epoch generation service — wraps the CLI pipeline with queue-based log streaming.
Runs generation in a background thread so Flask keeps serving requests.
"""
import json
import logging
import queue
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from config import (
    BREACH_MINI_EVENTS,
    BROADCAST_CHAR_LIMIT,
    ENDGAME_MODES,
    FLOOR_THEMES,
    LLM_OUTPUT_CHAR_LIMIT,
    NUM_FLOORS,
)

logger = logging.getLogger(__name__)

# ═══ MODULE-LEVEL STATE ═══

_generation_log: queue.Queue = queue.Queue()
_generation_running = threading.Event()
_generation_result: dict = {}
_generation_lock = threading.Lock()


def is_running() -> bool:
    """Check if epoch generation is currently in progress."""
    return _generation_running.is_set()


def get_result() -> dict:
    """Get the last generation result (empty if none)."""
    return dict(_generation_result)


def get_log_queue() -> queue.Queue:
    """Get the log queue for SSE streaming."""
    return _generation_log


def start_generation(db_path: str, epoch_number: int = 1,
                     endgame_mode: str = "", breach_type: str = "",
                     admin_user: str = "") -> bool:
    """Start epoch generation in a background thread.

    Returns True if generation started, False if already running.
    """
    if _generation_running.is_set():
        return False

    with _generation_lock:
        if _generation_running.is_set():
            return False
        _generation_running.set()

    # Clear previous state
    while not _generation_log.empty():
        try:
            _generation_log.get_nowait()
        except queue.Empty:
            break
    _generation_result.clear()

    thread = threading.Thread(
        target=_run_generation,
        args=(db_path, epoch_number, endgame_mode, breach_type, admin_user),
        daemon=True,
    )
    thread.start()
    return True


def start_soft_regen(db_path: str, admin_user: str = "") -> bool:
    """Regenerate world (rooms, monsters, items) while keeping characters.

    Preserves: accounts, players, node_sessions.
    Resets: rooms, exits, monsters, items, secrets, bounties, bosses, floor data.
    All players are returned to town with state='town'.
    """
    if _generation_running.is_set():
        return False

    with _generation_lock:
        if _generation_running.is_set():
            return False
        _generation_running.set()

    while not _generation_log.empty():
        try:
            _generation_log.get_nowait()
        except queue.Empty:
            break
    _generation_result.clear()

    thread = threading.Thread(
        target=_run_soft_regen,
        args=(db_path, admin_user),
        daemon=True,
    )
    thread.start()
    return True


def _log(msg: str) -> None:
    """Emit a log line to the queue."""
    _generation_log.put(msg)


def _run_generation(db_path: str, epoch_number: int,
                    endgame_mode: str, breach_type: str,
                    admin_user: str) -> None:
    """Run the full epoch generation pipeline with log emission.

    Wraps the existing pipeline functions — does NOT rewrite them.
    """
    # Late imports to avoid circular deps at module load
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

    start_time = time.time()

    try:
        conn = get_db(db_path)
        # Generation does many sequential writes; give it a long timeout
        # so the game engine's brief write locks don't cause "database is locked"
        conn.execute("PRAGMA busy_timeout=30000")
        backend = get_backend(db_path=db_path)
        backend_name = type(backend).__name__

        # Select modes
        if not endgame_mode:
            endgame_mode = random.choice(ENDGAME_MODES)
        if not breach_type:
            breach_type = random.choice(BREACH_MINI_EVENTS)

        theme = FLOOR_THEMES.get(1, "The Depths")

        _log("=== EPOCH GENERATION STARTED ===")
        _log(f"Backend: {backend_name}")
        _log(f"Endgame mode: {endgame_mode}")
        _log(f"Breach type: {breach_type}")

        # Step 1: Reset
        step_start = time.time()
        _log("[1/9] Resetting epoch tables...")
        reset_epoch_tables(conn)
        _log(f"[1/9] Done ({_elapsed(step_start)})")

        # Step 2: Create epoch record
        step_start = time.time()
        _log("[2/9] Creating epoch record...")
        create_epoch(conn, epoch_number, endgame_mode, breach_type, theme)
        _log(f"[2/9] Epoch #{epoch_number} initialized ({_elapsed(step_start)})")

        # Step 2a: Floor sub-themes
        step_start = time.time()
        _log("[2a/9] Generating floor sub-themes...")
        theme_stats = generate_floor_themes(conn, backend)
        floor_themes = get_floor_themes(conn)
        for f in sorted(floor_themes):
            _log(f"  Floor {f}: {floor_themes[f]['floor_name']}")
        _log(f"[2a/9] Floor themes: {theme_stats['floor_themes']} ({_elapsed(step_start)})")

        # Step 2b: Town generation
        step_start = time.time()
        _log("[2b/9] Generating town (Floor 0)...")
        town_stats = generate_town(conn, backend)
        _log(f"[2b/9] Town: {town_stats['rooms']} rooms, {town_stats['npc_rooms']} NPCs ({_elapsed(step_start)})")

        # Step 3: World generation
        step_start = time.time()
        _log("[3/9] Generating dungeon world...")
        world_stats = generate_world(conn, backend, floor_themes=floor_themes)
        _log(f"[3/9] World: {world_stats['rooms']} rooms, {world_stats['monsters']} monsters, "
             f"{world_stats['items']} items, {world_stats['exits']} exits ({_elapsed(step_start)})")

        # Step 4: Breach zone
        step_start = time.time()
        _log("[4/9] Generating breach zone...")
        breach_stats = generate_breach(conn, backend)
        _log(f"[4/9] Breach: {breach_stats['rooms']} rooms, mini-event: {breach_stats['mini_event']} ({_elapsed(step_start)})")

        # Step 5: Secrets
        step_start = time.time()
        _log("[5/9] Placing secrets...")
        secret_stats = generate_secrets(
            conn, backend, breach_room_ids=breach_stats["breach_room_ids"],
            floor_themes=floor_themes,
        )
        _log(f"[5/9] Secrets: {secret_stats['total']} — "
             f"obs:{secret_stats['observation']} puz:{secret_stats['puzzle']} "
             f"lore:{secret_stats['lore']} stat:{secret_stats['stat_gated']} "
             f"breach:{secret_stats['breach']} ({_elapsed(step_start)})")

        # Step 6: Bounties
        step_start = time.time()
        _log("[6/9] Generating bounty pool...")
        bounty_stats = generate_bounties(conn, backend, floor_themes=floor_themes)
        _log(f"[6/9] Bounties: {bounty_stats['total']} — "
             f"early:{bounty_stats['early']} mid:{bounty_stats['mid']} "
             f"late:{bounty_stats['late']} ({_elapsed(step_start)})")

        # Step 7: Bosses
        step_start = time.time()
        _log("[7/9] Generating bosses...")
        boss_stats = generate_bosses(conn, backend, floor_themes=floor_themes)
        _log(f"[7/9] Bosses: {boss_stats['floor_bosses']} floor bosses, "
             f"raid mechanics: {boss_stats['raid_boss_mechanics']} ({_elapsed(step_start)})")

        # Step 8: Narrative content
        step_start = time.time()
        _log("[8/9] Generating narrative content...")
        narrative_count = _generate_narrative_content(conn, backend, endgame_mode, breach_type, floor_themes)
        _log(f"[8/9] Narrative: {narrative_count['dialogue']} dialogue, "
             f"{narrative_count['skins']} skins, {narrative_count['broadcasts']} broadcasts ({_elapsed(step_start)})")

        # Step 8b: NPC journals
        journal_count = _seed_npc_journals(conn, epoch_number)
        _log(f"[8b/9] NPC journals seeded: {journal_count}")

        # Step 9: Validation
        step_start = time.time()
        _log("[9/9] Running validation...")
        validation = validate_epoch(conn)
        if validation["errors"]:
            for err in validation["errors"]:
                _log(f"  ERROR: {err}")
        if validation["warnings"]:
            for w in validation["warnings"][:5]:
                _log(f"  WARNING: {w}")
            if len(validation["warnings"]) > 5:
                _log(f"  ... and {len(validation['warnings']) - 5} more warnings")
        _log(f"[9/9] Validation: {len(validation['errors'])} errors, "
             f"{len(validation['warnings'])} warnings ({_elapsed(step_start)})")

        # Announce the new epoch (non-fatal)
        announcement_count = 0
        try:
            epoch_row = conn.execute("SELECT * FROM epoch WHERE id = 1").fetchone()
            narrative_theme = epoch_row["narrative_theme"] if epoch_row else ""
            epoch_name = ""  # No epoch_name column in schema; param kept for API compat
            announcements = backend.generate_epoch_announcements(
                endgame_mode, breach_type, narrative_theme or "", epoch_name or "",
            )
            # Store in epoch record as JSON
            conn.execute(
                "UPDATE epoch SET announcements = ? WHERE id = 1",
                (json.dumps(announcements),),
            )
            # Broadcast as tier 1 (server-wide immediate)
            for msg in announcements:
                conn.execute(
                    "INSERT INTO broadcasts (tier, message) VALUES (1, ?)",
                    (msg[:BROADCAST_CHAR_LIMIT],),
                )
            conn.commit()
            announcement_count = len(announcements)
            _log(f"Epoch announced: {announcement_count} broadcasts queued")
        except Exception as e:
            logger.warning(f"Epoch announcement failed (non-fatal): {e}")
            _log(f"WARNING: Epoch announcement failed: {e}")

        # Generate dashboard preamble (non-fatal)
        try:
            spell_csv = conn.execute("SELECT spell_names FROM epoch WHERE id = 1").fetchone()
            spell_list = spell_csv["spell_names"].split(",") if spell_csv and spell_csv["spell_names"] else []
            preamble = backend.generate_epoch_preamble(
                endgame_mode, breach_type,
                narrative_theme=narrative_theme or "",
                floor_themes=floor_themes,
                spell_names=spell_list,
            )
            conn.execute("UPDATE epoch SET preamble = ? WHERE id = 1", (preamble,))
            conn.commit()
            _log(f"Dashboard preamble: {len(preamble)} chars")
        except Exception as e:
            logger.warning(f"Preamble generation failed (non-fatal): {e}")
            _log(f"WARNING: Preamble generation failed: {e}")

        conn.close()

        total_time = time.time() - start_time
        total_rooms = town_stats["rooms"] + world_stats["rooms"] + breach_stats["rooms"]

        _log("")
        _log("=== EPOCH GENERATION COMPLETE ===")
        _log(f"Epoch #{epoch_number} — {endgame_mode}")
        _log(f"Total rooms: {total_rooms}")
        _log(f"Total monsters: {world_stats['monsters']}")
        _log(f"Total items: {world_stats['items']}")
        _log(f"Total secrets: {secret_stats['total']}")
        _log(f"Total bounties: {bounty_stats['total']}")
        _log(f"Floor bosses: {boss_stats['floor_bosses']}")
        _log(f"Validation errors: {len(validation['errors'])}")
        _log(f"Time: {total_time:.1f}s")

        # Store result for the summary panel
        _generation_result.update({
            "success": True,
            "epoch_number": epoch_number,
            "endgame_mode": endgame_mode,
            "breach_type": breach_type,
            "backend": backend_name,
            "rooms": total_rooms,
            "town_rooms": town_stats["rooms"],
            "dungeon_rooms": world_stats["rooms"],
            "breach_rooms": breach_stats["rooms"],
            "monsters": world_stats["monsters"],
            "items": world_stats["items"],
            "exits": world_stats["exits"],
            "secrets": secret_stats["total"],
            "bounties": bounty_stats["total"],
            "floor_bosses": boss_stats["floor_bosses"],
            "raid_mechanics": boss_stats["raid_boss_mechanics"],
            "dialogue": narrative_count["dialogue"],
            "skins": narrative_count["skins"],
            "broadcasts": narrative_count["broadcasts"],
            "journals": journal_count,
            "spells": narrative_count.get("spells", 0),
            "validation_errors": len(validation["errors"]),
            "validation_warnings": len(validation["warnings"]),
            "elapsed": round(total_time, 1),
        })

        # Log admin action
        try:
            aconn = sqlite3.connect(db_path)
            aconn.execute(
                "INSERT INTO admin_log (admin, action, details) VALUES (?, ?, ?)",
                (admin_user or "system", "epoch_generate",
                 f"epoch={epoch_number} mode={endgame_mode} breach={breach_type} "
                 f"backend={backend_name} rooms={total_rooms} time={total_time:.1f}s"),
            )
            aconn.commit()
            aconn.close()
        except Exception:
            pass

    except Exception as e:
        logger.exception("Epoch generation failed")
        _log(f"")
        _log(f"FATAL ERROR: {e}")
        _generation_result.update({
            "success": False,
            "error": str(e),
        })

    finally:
        _generation_running.clear()
        _generation_log.put(None)  # Sentinel to signal stream end


def _elapsed(start: float) -> str:
    """Format elapsed time since start."""
    elapsed = time.time() - start
    if elapsed < 1:
        return f"{elapsed * 1000:.0f}ms"
    return f"{elapsed:.1f}s"


# ═══ Narrative content generation (copied from scripts/epoch_generate.py) ═══
# We duplicate these small orchestration functions rather than importing from
# scripts/ to avoid path issues. They just call the pipeline functions.


def _generate_narrative_content(conn, backend, endgame_mode, breach_type, floor_themes=None):
    """Generate NPC dialogue, narrative skins, and atmospheric broadcasts."""
    counts = {"dialogue": 0, "skins": 0, "broadcasts": 0}

    npcs = ["grist", "maren", "torval", "whisper"]
    contexts = {
        "grist": ["greeting", "hint", "recap"],
        "maren": ["greeting"],
        "torval": ["greeting"],
        "whisper": ["greeting", "hint"],
    }

    for npc in npcs:
        for context in contexts.get(npc, ["greeting"]):
            for _ in range(3):
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

    skin = backend.generate_narrative_skin(endgame_mode, endgame_mode)
    conn.execute(
        "INSERT INTO narrative_skins (target, skin_type, content) VALUES (?, ?, ?)",
        ("endgame", "title", skin["title"][:LLM_OUTPUT_CHAR_LIMIT]),
    )
    counts["skins"] += 1

    skin = backend.generate_narrative_skin("breach", breach_type)
    conn.execute(
        "INSERT INTO narrative_skins (target, skin_type, content) VALUES (?, ?, ?)",
        ("breach", "title", skin["title"][:LLM_OUTPUT_CHAR_LIMIT]),
    )
    counts["skins"] += 1

    theme = FLOOR_THEMES.get(1, "")
    spell_names = backend.generate_spell_names(theme)
    spell_names = [s[:20] for s in spell_names]
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


def _seed_npc_journals(conn, epoch_number):
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


def _run_soft_regen(db_path: str, admin_user: str) -> None:
    """Regenerate world while keeping characters intact."""
    from src.db.database import get_db
    from src.generation.bossgen import generate_bosses
    from src.generation.bountygen import generate_bounties
    from src.generation.breachgen import generate_breach
    from src.generation.narrative import get_backend
    from src.generation.secretgen import generate_secrets
    from src.generation.themegen import generate_floor_themes, get_floor_themes
    from src.generation.validation import validate_epoch
    from src.generation.worldgen import generate_town, generate_world

    start_time = time.time()

    try:
        conn = get_db(db_path)
        conn.execute("PRAGMA busy_timeout=30000")
        backend = get_backend(db_path=db_path)
        backend_name = type(backend).__name__

        # Get current epoch info
        epoch_row = conn.execute("SELECT * FROM epoch WHERE id = 1").fetchone()
        if not epoch_row:
            _log("ERROR: No epoch found. Run a full generation first.")
            _generation_result.update({"success": False, "error": "No epoch found"})
            return
        epoch_number = epoch_row["epoch_number"]
        endgame_mode = epoch_row["endgame_mode"]
        breach_type = epoch_row["breach_type"]

        _log("=== SOFT REGEN — KEEP CHARACTERS ===")
        _log(f"Backend: {backend_name}")
        _log(f"Epoch #{epoch_number} (preserving)")

        # Step 1: Reset world tables (NOT players, accounts, sessions)
        step_start = time.time()
        _log("[1/8] Clearing world data (keeping characters)...")
        world_tables = [
            "broadcast_seen", "broadcasts", "player_messages", "mail", "town_board",
            "npc_journals", "npc_dialogue", "narrative_skins",
            "breach", "htl_checkpoints",
            "escape_participants", "escape_run",
            "raid_boss_contributors", "raid_boss",
            "bounty_contributors", "bounties",
            "discovery_buffs", "secret_progress", "secrets",
            "inventory", "monsters", "room_exits", "rooms", "items",
            "floor_themes", "floor_progress",
        ]
        for table in world_tables:
            conn.execute(f"DELETE FROM {table}")

        # Reset all players to town
        conn.execute(
            "UPDATE players SET state = 'town', room_id = NULL, floor = 0, "
            "combat_monster_id = NULL, town_location = 'tavern'"
        )
        # Reset day counter
        conn.execute("UPDATE epoch SET day_number = 1")
        conn.commit()
        _log(f"[1/8] World cleared, players sent to town ({_elapsed(step_start)})")

        # Step 2: Floor sub-themes
        step_start = time.time()
        _log("[2/8] Generating floor sub-themes...")
        theme_stats = generate_floor_themes(conn, backend)
        floor_themes = get_floor_themes(conn)
        for f in sorted(floor_themes):
            _log(f"  Floor {f}: {floor_themes[f]['floor_name']}")
        _log(f"[2/8] Floor themes: {theme_stats['floor_themes']} ({_elapsed(step_start)})")

        # Step 3: Town generation
        step_start = time.time()
        _log("[3/8] Generating town (Floor 0)...")
        town_stats = generate_town(conn, backend)
        _log(f"[3/8] Town: {town_stats['rooms']} rooms ({_elapsed(step_start)})")

        # Step 4: World generation
        step_start = time.time()
        _log("[4/8] Generating dungeon world...")
        world_stats = generate_world(conn, backend, floor_themes=floor_themes)
        _log(f"[4/8] World: {world_stats['rooms']} rooms, {world_stats['monsters']} monsters ({_elapsed(step_start)})")

        # Step 5: Breach zone
        step_start = time.time()
        _log("[5/8] Generating breach zone...")
        breach_stats = generate_breach(conn, backend)
        _log(f"[5/8] Breach: {breach_stats['rooms']} rooms ({_elapsed(step_start)})")

        # Step 6: Secrets
        step_start = time.time()
        _log("[6/8] Placing secrets...")
        secret_stats = generate_secrets(
            conn, backend, breach_room_ids=breach_stats["breach_room_ids"],
            floor_themes=floor_themes,
        )
        _log(f"[6/8] Secrets: {secret_stats['total']} ({_elapsed(step_start)})")

        # Step 7: Bounties + Bosses
        step_start = time.time()
        _log("[7/8] Generating bounties and bosses...")
        bounty_stats = generate_bounties(conn, backend, floor_themes=floor_themes)
        boss_stats = generate_bosses(conn, backend, floor_themes=floor_themes)
        _log(f"[7/8] Bounties: {bounty_stats['total']}, Bosses: {boss_stats['floor_bosses']} ({_elapsed(step_start)})")

        # Step 8: Narrative + journals
        step_start = time.time()
        _log("[8/8] Generating narrative content...")
        narrative_count = _generate_narrative_content(conn, backend, endgame_mode, breach_type, floor_themes)
        journal_count = _seed_npc_journals(conn, epoch_number)
        _log(f"[8/8] Narrative: {narrative_count['dialogue']} dialogue, journals: {journal_count} ({_elapsed(step_start)})")

        # Set player room_ids to the town center
        hub = conn.execute("SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1 LIMIT 1").fetchone()
        if hub:
            conn.execute("UPDATE players SET room_id = ? WHERE state = 'town'", (hub["id"],))
            conn.commit()

        conn.close()

        total_time = time.time() - start_time
        total_rooms = town_stats["rooms"] + world_stats["rooms"] + breach_stats["rooms"]

        _log("")
        _log("=== SOFT REGEN COMPLETE ===")
        _log(f"Rooms: {total_rooms}, Monsters: {world_stats['monsters']}")
        _log(f"Characters preserved. All players in town.")
        _log(f"Time: {total_time:.1f}s")

        _generation_result.update({
            "success": True,
            "soft_regen": True,
            "epoch_number": epoch_number,
            "rooms": total_rooms,
            "monsters": world_stats["monsters"],
            "secrets": secret_stats["total"],
            "bounties": bounty_stats["total"],
            "floor_bosses": boss_stats["floor_bosses"],
            "elapsed": round(total_time, 1),
        })

        try:
            aconn = sqlite3.connect(db_path)
            aconn.execute(
                "INSERT INTO admin_log (admin, action, details) VALUES (?, ?, ?)",
                (admin_user or "system", "soft_regen",
                 f"epoch={epoch_number} rooms={total_rooms} time={total_time:.1f}s"),
            )
            aconn.commit()
            aconn.close()
        except Exception:
            pass

    except Exception as e:
        logger.exception("Soft regen failed")
        _log(f"")
        _log(f"FATAL ERROR: {e}")
        _generation_result.update({"success": False, "error": str(e)})
    finally:
        _generation_running.clear()
        _generation_log.put(None)
