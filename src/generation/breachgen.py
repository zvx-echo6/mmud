"""
Breach zone generation for MMUD.
Generates 5-8 rooms connecting two adjacent floors, with a mini-event and mini-boss.

Layout:
  - 5-8 rooms forming a mini-zone bridging two floors (randomized 4-6 range)
  - Entry point connected to the lower floor, exit to the upper floor
  - One mini-boss placed in the deepest room
  - 3 breach secrets placed by secretgen (after breach rooms exist)
  - Random mini-event selected from 4 types
  - All rooms sealed (is_breach=1) until day 15

Mini-events:
  - heist: Mini Retrieve & Escape
  - emergence: Mini Raid Boss (500-800 HP)
  - incursion: Mini Hold the Line (2 rooms revert/day, 48h hold)
  - resonance: Puzzle dungeon, no combat focus
"""

import json
import random
import sqlite3
from typing import Optional

from config import (
    BREACH_CONNECTS_FLOORS_RANGE,
    BREACH_MINI_EVENTS,
    BREACH_ROOMS_MAX,
    BREACH_ROOMS_MIN,
    EMERGENCE_HP_MAX,
    EMERGENCE_HP_MIN,
    INCURSION_HOLD_HOURS,
    INCURSION_REGEN_ROOMS_PER_DAY,
    LLM_OUTPUT_CHAR_LIMIT,
)
from src.generation.narrative import DummyBackend


def generate_breach(
    conn: sqlite3.Connection, backend: Optional[DummyBackend] = None,
) -> dict:
    """Generate the Breach zone and select a mini-event.

    Args:
        conn: Database connection (breach floor rooms must exist).
        backend: Narrative backend.

    Returns:
        Stats dict with room_count, mini_event, breach_room_ids, mini_boss_id.
    """
    if backend is None:
        backend = DummyBackend()

    # Use breach_type from epoch if set, otherwise random
    epoch = conn.execute("SELECT breach_type FROM epoch WHERE id = 1").fetchone()
    if epoch and epoch["breach_type"]:
        mini_event = epoch["breach_type"]
    else:
        mini_event = random.choice(BREACH_MINI_EVENTS)
    num_rooms = random.randint(BREACH_ROOMS_MIN, BREACH_ROOMS_MAX)

    stats = {
        "rooms": 0,
        "mini_event": mini_event,
        "breach_room_ids": [],
        "mini_boss_id": None,
    }

    # Randomize which floors the breach connects each epoch
    floor_entry = random.randint(BREACH_CONNECTS_FLOORS_RANGE[0], BREACH_CONNECTS_FLOORS_RANGE[1] - 1)
    floor_exit = floor_entry + 1

    # Find entry point on lower floor (non-hub room)
    entry_room = conn.execute(
        """SELECT id FROM rooms
           WHERE floor = ? AND is_hub = 0 AND is_vault = 0 AND is_breach = 0
           ORDER BY RANDOM() LIMIT 1""",
        (floor_entry,),
    ).fetchone()

    # Find exit point on upper floor (non-hub room)
    exit_room = conn.execute(
        """SELECT id FROM rooms
           WHERE floor = ? AND is_hub = 0 AND is_vault = 0 AND is_breach = 0
           ORDER BY RANDOM() LIMIT 1""",
        (floor_exit,),
    ).fetchone()

    if not entry_room or not exit_room:
        return stats

    # ── Generate breach rooms ──
    breach_room_ids = []
    prev_room_id = entry_room["id"]

    for i in range(num_rooms):
        # Breach rooms sit between the two connected floors
        floor = floor_entry if i < num_rooms // 2 else floor_exit
        name = backend.generate_breach_name()
        if i > 0:
            name = f"{name} {i + 1}"

        desc = _breach_room_desc(mini_event, i, num_rooms)
        short = desc[:80]  # shorter revisit

        cursor = conn.execute(
            """INSERT INTO rooms (floor, name, description, description_short,
               is_breach, is_hub)
               VALUES (?, ?, ?, ?, 1, 0)""",
            (floor, name[:80], desc[:LLM_OUTPUT_CHAR_LIMIT],
             short[:LLM_OUTPUT_CHAR_LIMIT]),
        )
        room_id = cursor.lastrowid
        breach_room_ids.append(room_id)
        stats["rooms"] += 1

        # Connect to previous room (linear chain through the breach)
        conn.execute(
            "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, ?)",
            (prev_room_id, room_id, "d"),  # descend into breach
        )
        conn.execute(
            "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, ?)",
            (room_id, prev_room_id, "u"),  # ascend back
        )
        prev_room_id = room_id

    # Connect last breach room to exit on upper floor
    if breach_room_ids:
        last_breach = breach_room_ids[-1]
        conn.execute(
            "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, ?)",
            (last_breach, exit_room["id"], "u"),
        )
        conn.execute(
            "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, ?)",
            (exit_room["id"], last_breach, "d"),
        )

    # ── Place mini-boss in deepest breach room ──
    if breach_room_ids:
        boss_room = breach_room_ids[-1]
        boss_id = _place_breach_boss(conn, boss_room, mini_event, backend)
        stats["mini_boss_id"] = boss_id

    # ── Set up breach table with mini-event state ──
    _setup_breach_state(conn, mini_event, breach_room_ids)

    stats["breach_room_ids"] = breach_room_ids
    conn.commit()
    return stats


def _breach_room_desc(mini_event: str, index: int, total: int) -> str:
    """Generate a breach room description based on mini-event type."""
    descs = {
        "heist": [
            "Rift energy crackles along twisted metal.",
            "Unstable ground shifts beneath your feet.",
            "Reality warps in the breach corridor.",
            "Stolen light flickers between dimensions.",
            "The breach hums with contained power.",
            "Air shimmers where planes intersect.",
            "Fractured stone floats in the rift.",
            "Energy arcs between broken walls.",
        ],
        "emergence": [
            "Something massive stirs in the rift.",
            "The breach pulses with a heartbeat.",
            "Organic matter fuses with stone here.",
            "Growths cover the breach walls.",
            "A wet heat radiates from deeper in.",
            "The rift births something terrible.",
            "Flesh-like tendrils line the ceiling.",
            "The air tastes of iron and bile.",
        ],
        "incursion": [
            "The rift pushes outward relentlessly.",
            "Breach matter encroaches on stone.",
            "Walls crack under rift pressure.",
            "The boundary between planes erodes.",
            "Rift spawn skitter in the shadows.",
            "The breach expands inch by inch.",
            "Reality fractures along fault lines.",
            "The rift fights to widen itself.",
        ],
        "resonance": [
            "Crystal formations hum in the breach.",
            "Harmonic tones echo from the rift.",
            "Symbols glow on breach surfaces.",
            "The rift reveals hidden patterns.",
            "Resonant energy maps the void beyond.",
            "Crystal nodes pulse in sequence.",
            "The breach sings at the edge of hearing.",
            "Patterns shift as you approach.",
        ],
    }
    lines = descs.get(mini_event, descs["heist"])
    return lines[index % len(lines)]


def _place_breach_boss(
    conn: sqlite3.Connection, room_id: int, mini_event: str,
    backend: DummyBackend,
) -> int:
    """Place a mini-boss in the breach zone. Returns monster ID."""
    name = f"Breach {backend.generate_boss_name(3)}"

    # Mini-boss stats — roughly floor 3-4 difficulty
    hp_max = random.randint(120, 200)
    pow_ = random.randint(10, 14)
    def_ = random.randint(8, 12)
    spd = random.randint(6, 10)
    xp = random.randint(80, 120)
    gold_min = random.randint(30, 50)
    gold_max = random.randint(60, 100)

    # Emergence mini-boss is beefier
    if mini_event == "emergence":
        hp_max = random.randint(EMERGENCE_HP_MIN, EMERGENCE_HP_MAX)
        pow_ = random.randint(12, 16)
        def_ = random.randint(10, 14)

    cursor = conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier,
           is_breach_boss, mechanic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 4, 1, ?)""",
        (room_id, name[:80], hp_max, hp_max, pow_, def_, spd,
         xp, gold_min, gold_max, mini_event),
    )
    return cursor.lastrowid


def _setup_breach_state(
    conn: sqlite3.Connection, mini_event: str, breach_room_ids: list[int],
) -> None:
    """Initialize the breach table with event-specific state."""
    emergence_hp = None
    emergence_hp_max = None
    heist_artifact_room = None

    if mini_event == "emergence":
        emergence_hp = random.randint(EMERGENCE_HP_MIN, EMERGENCE_HP_MAX)
        emergence_hp_max = emergence_hp

    if mini_event == "heist" and breach_room_ids:
        # Artifact placed in the deepest breach room
        heist_artifact_room = breach_room_ids[-1]

    conn.execute(
        """INSERT INTO breach (id, mini_event, active,
           emergence_hp, emergence_hp_max,
           heist_artifact_room_id)
           VALUES (1, ?, 0, ?, ?, ?)""",
        (mini_event, emergence_hp, emergence_hp_max, heist_artifact_room),
    )
