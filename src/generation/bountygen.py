"""
Bounty pool generation for MMUD.
Generates ~40 bounties phased across 30 days.

Distribution:
  - Days 1-10 (early): 15 bounties, floors 1-2, HP 100-250, soloable
  - Days 11-20 (mid): 15 bounties, floors 2-3, HP 200-500, coordination rewarded
  - Days 21-30 (late): 10 bounties, floors 3-4, HP 400-800, group effort
"""

import random
import sqlite3
from typing import Optional

from config import (
    BOUNTIES_PER_EPOCH,
    BOUNTY_ACTIVE_MAX,
    BOUNTY_PHASE_DISTRIBUTION,
    LLM_OUTPUT_CHAR_LIMIT,
    NUM_FLOORS,
)
from src.generation.narrative import DummyBackend


def generate_bounties(
    conn: sqlite3.Connection, backend: Optional[DummyBackend] = None,
) -> dict:
    """Generate the full bounty pool for the epoch.

    Args:
        conn: Database connection (rooms and some monsters must exist).
        backend: Narrative backend.

    Returns:
        Stats dict with total and per-phase counts.
    """
    if backend is None:
        backend = DummyBackend()

    stats = {"total": 0, "early": 0, "mid": 0, "late": 0}

    for phase_name, phase_config in BOUNTY_PHASE_DISTRIBUTION.items():
        count = phase_config["count"]
        day_min, day_max = phase_config["days"]
        floor_min, floor_max = phase_config["floors"]

        for i in range(count):
            # Spread available_from_day across the phase window
            day = day_min + (i * (day_max - day_min)) // max(count - 1, 1)

            # Get eligible rooms on target floors
            rooms = conn.execute(
                """SELECT r.id, r.floor, r.name FROM rooms r
                   WHERE r.floor >= ? AND r.floor <= ?
                   AND r.is_hub = 0 AND r.is_breach = 0
                   ORDER BY RANDOM() LIMIT 1""",
                (floor_min, floor_max),
            ).fetchone()

            if not rooms:
                continue

            room_id = rooms["id"]
            floor = rooms["floor"]
            theme = _floor_theme(floor)

            # Generate bounty monster
            hp_range = _hp_range_for_phase(phase_name)
            hp_max = random.randint(*hp_range)
            tier = min(floor + random.randint(0, 1), 5)

            monster_name = backend.generate_monster_name(tier)
            pow_ = _bounty_stat(tier, "pow")
            def_ = _bounty_stat(tier, "def")
            spd = _bounty_stat(tier, "spd")
            xp = _bounty_xp(tier)
            gold_min, gold_max = _bounty_gold(tier)

            # Insert bounty monster
            cursor = conn.execute(
                """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
                   xp_reward, gold_reward_min, gold_reward_max, tier, is_bounty)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (room_id, monster_name, hp_max, hp_max, pow_, def_, spd,
                 xp, gold_min, gold_max, tier),
            )
            monster_id = cursor.lastrowid

            # Generate bounty description
            desc = backend.generate_bounty_description(monster_name, floor, theme)

            # First BOUNTY_ACTIVE_MAX bounties start active
            is_active = 1 if stats["total"] < BOUNTY_ACTIVE_MAX else 0

            conn.execute(
                """INSERT INTO bounties (type, description, target_monster_id,
                   target_value, current_value, floor_min, floor_max,
                   phase, available_from_day, active)
                   VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
                ("kill", desc[:LLM_OUTPUT_CHAR_LIMIT], monster_id,
                 hp_max, floor_min, floor_max, phase_name, day, is_active),
            )

            stats["total"] += 1
            stats[phase_name] += 1

    conn.commit()
    return stats


# ── Phase-specific scaling ─────────────────────────────────────────────────


def _hp_range_for_phase(phase: str) -> tuple[int, int]:
    """Get HP range for bounty monsters in a phase."""
    ranges = {
        "early": (100, 250),
        "mid": (200, 500),
        "late": (400, 800),
    }
    return ranges.get(phase, (100, 250))


def _bounty_stat(tier: int, stat: str) -> int:
    """Calculate bounty monster stat (slightly stronger than regular)."""
    base = {1: 4, 2: 6, 3: 9, 4: 12, 5: 15}
    return base.get(tier, 4) + random.randint(0, 2)


def _bounty_xp(tier: int) -> int:
    """Bounty monsters give more XP than regular."""
    base = {1: 20, 2: 40, 3: 65, 4: 95, 5: 130}
    return base.get(tier, 20) + random.randint(0, 10)


def _bounty_gold(tier: int) -> tuple[int, int]:
    """Bounty monsters drop more gold."""
    base_min = {1: 5, 2: 12, 3: 22, 4: 35, 5: 55}
    base_max = {1: 15, 2: 30, 3: 45, 4: 70, 5: 100}
    return base_min.get(tier, 5), base_max.get(tier, 15)


def _floor_theme(floor: int) -> str:
    from config import FLOOR_THEMES
    return FLOOR_THEMES.get(floor, "Unknown Depths")
