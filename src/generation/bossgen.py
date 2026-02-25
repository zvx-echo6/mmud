"""
Boss generation for MMUD.
Generates floor bosses (1 per floor) and pre-rolls raid boss mechanics.

Floor bosses:
  - One per floor, rolls 1 mechanic from that floor's table
  - Floor 4 "Warden" rolls 2 mechanics from all tables combined
  - Warden HP 300-500, 3%/8h regen
  - Other floor bosses scale HP/stats by floor

Raid boss:
  - Not placed at generation time — HP scales to active player count
  - Pre-roll 2-3 mechanics from RAID_BOSS_MECHANIC_TABLE
  - Store in raid_boss table with hp=0
"""

import json
import random
import sqlite3
from typing import Optional

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
) -> dict:
    """Generate floor bosses and pre-roll raid boss mechanics.

    Args:
        conn: Database connection (rooms must exist).
        backend: Narrative backend.

    Returns:
        Stats dict with floor_bosses, raid_boss_mechanics.
    """
    if backend is None:
        backend = DummyBackend()

    stats = {"floor_bosses": 0, "raid_boss_mechanics": []}

    # ── Floor bosses ──
    for floor in range(1, NUM_FLOORS + 1):
        boss_id = _generate_floor_boss(conn, floor, backend)
        if boss_id:
            stats["floor_bosses"] += 1

    # ── Raid boss pre-generation ──
    raid_mechanics = _pre_generate_raid_boss(conn, backend)
    stats["raid_boss_mechanics"] = raid_mechanics

    conn.commit()
    return stats


def _generate_floor_boss(
    conn: sqlite3.Connection, floor: int, backend: DummyBackend,
) -> Optional[int]:
    """Generate a floor boss and place it in a room.

    Returns monster ID or None.
    """
    # Roll mechanic(s)
    mechanics = _roll_floor_boss_mechanics(floor)

    # Pick a non-hub, non-vault room on this floor for the boss
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
    name = backend.generate_boss_name(floor)
    hp_max = _boss_hp(floor)
    pow_ = _boss_stat(floor, "pow")
    def_ = _boss_stat(floor, "def")
    spd = _boss_stat(floor, "spd")
    xp = _boss_xp(floor)
    gold_min, gold_max = _boss_gold(floor)

    # Floor 4 Warden gets special HP
    if floor == NUM_FLOORS:
        hp_max = random.randint(WARDEN_HP_MIN, WARDEN_HP_MAX)

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

    Floor 1-3: roll 1 from that floor's table.
    Floor 4: roll 2 from all floor tables combined.
    """
    mechanic_config = FLOOR_BOSS_MECHANICS.get(floor)

    if isinstance(mechanic_config, int):
        # Floor 4: roll N from all tables combined
        num_rolls = mechanic_config
        all_mechanics = []
        for f in range(1, NUM_FLOORS):  # floors 1-3
            all_mechanics.extend(FLOOR_BOSS_MECHANICS[f])
        return random.sample(all_mechanics, min(num_rolls, len(all_mechanics)))
    elif isinstance(mechanic_config, list):
        return [random.choice(mechanic_config)]
    else:
        return ["armored"]


def _boss_hp(floor: int) -> int:
    """Calculate floor boss HP. Scales significantly with floor."""
    base = {1: 80, 2: 150, 3: 250, 4: 400}
    hp = base.get(floor, 80)
    return hp + random.randint(-10, 20)


def _boss_stat(floor: int, stat: str) -> int:
    """Calculate floor boss stat (stronger than regular monsters)."""
    base = {1: 6, 2: 9, 3: 13, 4: 17}
    return base.get(floor, 6) + random.randint(0, 3)


def _boss_xp(floor: int) -> int:
    """Floor bosses give substantial XP."""
    base = {1: 50, 2: 100, 3: 180, 4: 300}
    return base.get(floor, 50) + random.randint(0, 20)


def _boss_gold(floor: int) -> tuple[int, int]:
    """Floor bosses drop more gold than regular monsters."""
    base_min = {1: 20, 2: 40, 3: 70, 4: 120}
    base_max = {1: 50, 2: 80, 3: 130, 4: 200}
    return base_min.get(floor, 20), base_max.get(floor, 50)
