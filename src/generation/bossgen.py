"""
Boss generation for MMUD.
Generates floor bosses (1 per floor) and pre-rolls raid boss mechanics.

Floor bosses:
  - One per floor, rolls 1 mechanic from that floor's table
  - Floor 8 "Warden" rolls 2 mechanics from all tables combined
  - Warden HP 500-800, 3%/8h regen
  - Other floor bosses scale HP/stats by formula

Raid boss:
  - Not placed at generation time — HP scales to active player count
  - Pre-roll 2-3 mechanics from RAID_BOSS_MECHANIC_TABLE
  - Store in raid_boss table with hp=0
"""

import json
import random
import sqlite3
from typing import Optional

from collections import deque

from config import (
    FLOOR_BOSS_MECHANICS,
    LLM_OUTPUT_CHAR_LIMIT,
    NUM_FLOORS,
    RAID_BOSS_MECHANIC_ROLLS,
    RAID_BOSS_MECHANIC_TABLE,
    RAID_BOSS_REGEN_RATE,
    WARDEN_HP_MAX,
    WARDEN_HP_MIN,
    WARDEN_REGEN_RATE,
)
from src.generation.narrative import DummyBackend


def generate_bosses(
    conn: sqlite3.Connection, backend: Optional[DummyBackend] = None,
    floor_themes: dict = None,
) -> dict:
    """Generate floor bosses and pre-roll raid boss mechanics.

    Args:
        conn: Database connection (rooms must exist).
        backend: Narrative backend.
        floor_themes: Per-epoch floor sub-themes dict (optional).

    Returns:
        Stats dict with floor_bosses, raid_boss_mechanics.
    """
    if backend is None:
        backend = DummyBackend()

    stats = {"floor_bosses": 0, "raid_boss_mechanics": []}

    # ── Floor bosses ──
    for floor in range(1, NUM_FLOORS + 1):
        floor_theme = floor_themes.get(floor) if floor_themes else None
        boss_id = _generate_floor_boss(conn, floor, backend, floor_theme=floor_theme)
        if boss_id:
            stats["floor_bosses"] += 1

    # ── Raid boss pre-generation ──
    raid_mechanics = _pre_generate_raid_boss(conn, backend)
    stats["raid_boss_mechanics"] = raid_mechanics

    conn.commit()
    return stats


def _generate_floor_boss(
    conn: sqlite3.Connection, floor: int, backend: DummyBackend,
    floor_theme: dict = None,
) -> Optional[int]:
    """Generate a floor boss and place it in a room.

    Returns monster ID or None.
    """
    # Roll mechanic(s)
    mechanics = _roll_floor_boss_mechanics(floor)

    # Pick a non-hub, non-vault room on this floor for the boss
    # On floors 1-2, exclude rooms within 3 steps of the hub
    if floor <= 2:
        room = _pick_distant_room(conn, floor, min_distance=3)
    else:
        room = conn.execute(
            """SELECT id, floor, name FROM rooms
               WHERE floor = ? AND is_hub = 0 AND is_vault = 0 AND is_breach = 0
               ORDER BY RANDOM() LIMIT 1""",
            (floor,),
        ).fetchone()

    if not room:
        return None

    room_id = room["id"]

    # Boss name and stats
    name = backend.generate_boss_name(floor, floor_theme=floor_theme)
    pow_ = _boss_stat(floor, "pow")
    def_ = _boss_stat(floor, "def")
    spd = _boss_stat(floor, "spd")
    xp = _boss_xp(floor)
    gold_min, gold_max = _boss_gold(floor)

    # Floor bosses (except Warden) generate with hp=0 — scaled on first encounter
    if floor == NUM_FLOORS:
        hp_max = random.randint(WARDEN_HP_MIN, WARDEN_HP_MAX)
    else:
        hp_max = 0

    # Store mechanic as first rolled (monsters.mechanic is a single string)
    # Additional mechanics stored as JSON in mechanic field for floor 4
    if len(mechanics) == 1:
        mechanic_str = mechanics[0]
    else:
        mechanic_str = json.dumps(mechanics)

    cursor = conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier,
           is_floor_boss, mechanic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (room_id, name, hp_max, hp_max, pow_, def_, spd,
         xp, gold_min, gold_max, min(floor + 1, 5), mechanic_str),
    )
    return cursor.lastrowid


def _pre_generate_raid_boss(
    conn: sqlite3.Connection, backend: DummyBackend,
) -> list[str]:
    """Pre-generate raid boss entry with hp=0 (scaled at epoch start).

    Returns list of rolled mechanics.
    """
    num_mechanics = random.randint(*RAID_BOSS_MECHANIC_ROLLS)

    # Roll from different categories to avoid duplicates within a category
    all_mechanics = []
    categories = list(RAID_BOSS_MECHANIC_TABLE.keys())
    random.shuffle(categories)

    used_mechanics: set[str] = set()
    for cat in categories * 2:  # cycle through categories
        if len(all_mechanics) >= num_mechanics:
            break
        options = [m for m in RAID_BOSS_MECHANIC_TABLE[cat] if m not in used_mechanics]
        if options:
            pick = random.choice(options)
            all_mechanics.append(pick)
            used_mechanics.add(pick)

    mechanics = all_mechanics[:num_mechanics]

    # Pick a room on the deepest floor for raid boss placement
    room = conn.execute(
        """SELECT id FROM rooms
           WHERE floor = ? AND is_hub = 0 AND is_vault = 0 AND is_breach = 0
           ORDER BY RANDOM() LIMIT 1""",
        (NUM_FLOORS,),
    ).fetchone()

    if not room:
        return mechanics

    name = backend.generate_boss_name(NUM_FLOORS)

    conn.execute(
        """INSERT INTO raid_boss (id, name, hp, hp_max, floor, room_id,
           regen_rate, mechanics, phase)
           VALUES (1, ?, 0, 0, ?, ?, ?, ?, 1)""",
        (name, NUM_FLOORS, room["id"], RAID_BOSS_REGEN_RATE,
         json.dumps(mechanics)),
    )

    return mechanics


# ── Floor boss stat scaling ──────────────────────────────────────────────


def _roll_floor_boss_mechanics(floor: int) -> list[str]:
    """Roll mechanic(s) for a floor boss.

    Floors 1-7: roll 1 from that floor's table.
    Floor 8 (Warden): roll 2 from all floor tables combined.
    """
    mechanic_config = FLOOR_BOSS_MECHANICS.get(floor)

    if isinstance(mechanic_config, int):
        # Warden: roll N from all tables combined
        num_rolls = mechanic_config
        all_mechanics = []
        for f in range(1, NUM_FLOORS):  # floors 1 through NUM_FLOORS-1
            table = FLOOR_BOSS_MECHANICS.get(f)
            if isinstance(table, list):
                all_mechanics.extend(table)
        return random.sample(all_mechanics, min(num_rolls, len(all_mechanics)))
    elif isinstance(mechanic_config, list):
        return [random.choice(mechanic_config)]
    else:
        return ["armored"]


def _boss_hp(floor: int) -> int:
    """Calculate floor boss HP via formula.

    F1≈105, F2≈160, F3≈225, F4≈300, F5≈385, F6≈480, F7≈585, F8=Warden.
    """
    if floor == NUM_FLOORS:
        return random.randint(WARDEN_HP_MIN, WARDEN_HP_MAX)
    hp = 60 + 40 * floor + 5 * floor * floor
    return hp + random.randint(-10, 20)


def _boss_stat(floor: int, stat: str) -> int:
    """Calculate floor boss stat via formula (stronger than regular monsters)."""
    base = min(2 + floor, 20)
    return base + random.randint(0, 2)


def _boss_xp(floor: int) -> int:
    """Floor bosses give substantial XP, scaling by formula."""
    xp = 30 + 20 * floor + 5 * floor * floor
    return xp + random.randint(0, 20)


def _boss_gold(floor: int) -> tuple[int, int]:
    """Floor bosses drop more gold than regular monsters, scaling by formula."""
    return (10 + 15 * floor, 30 + 25 * floor)


def _bfs_distances(conn: sqlite3.Connection, hub_id: int) -> dict[int, int]:
    """BFS from hub, returns {room_id: distance}."""
    distances = {hub_id: 0}
    queue = deque([hub_id])
    while queue:
        rid = queue.popleft()
        exits = conn.execute(
            "SELECT to_room_id FROM room_exits WHERE from_room_id = ?", (rid,)
        ).fetchall()
        for ex in exits:
            tid = ex["to_room_id"]
            if tid not in distances:
                distances[tid] = distances[rid] + 1
                queue.append(tid)
    return distances


def _pick_distant_room(
    conn: sqlite3.Connection, floor: int, min_distance: int = 3,
) -> Optional[sqlite3.Row]:
    """Pick a non-hub, non-vault room at least min_distance from the hub.

    Falls back to any eligible room if none are far enough.
    """
    hub = conn.execute(
        "SELECT id FROM rooms WHERE floor = ? AND is_hub = 1 LIMIT 1", (floor,),
    ).fetchone()
    if not hub:
        return None

    distances = _bfs_distances(conn, hub["id"])

    eligible = conn.execute(
        """SELECT id, floor, name FROM rooms
           WHERE floor = ? AND is_hub = 0 AND is_vault = 0 AND is_breach = 0""",
        (floor,),
    ).fetchall()

    far_rooms = [r for r in eligible if distances.get(r["id"], 0) >= min_distance]
    if far_rooms:
        return random.choice(far_rooms)

    # Fallback: any eligible room
    if eligible:
        return random.choice(eligible)
    return None
