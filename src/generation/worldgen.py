"""
Dungeon world generation for MMUD.
Generates 4 floors of hub-spoke layout rooms with monsters, items, and traps.

Layout per floor:
- 1 central hub room (checkpoint)
- 3-4 branches of 3-5 rooms each radiating from hub
- 1-2 loops connecting branches (prevent dead-end-only layouts)
- One branch ends at a stairway connecting to next floor
- 3-5 vault rooms injected into layout
- 1-2 traps guarding vault rooms
"""

import math
import random
import sqlite3
from collections import deque
from typing import Optional

from config import (
    BRANCHES_PER_FLOOR,
    FLOOR_THEMES,
    ITEM_TIERS,
    LOOPS_PER_FLOOR,
    LLM_OUTPUT_CHAR_LIMIT,
    NUM_FLOORS,
    REVEAL_GOLD_CHANCE,
    REVEAL_GOLD_MAX,
    REVEAL_GOLD_MIN,
    REVEAL_LORE_CHANCE,
    ROOMS_PER_BRANCH_MAX,
    ROOMS_PER_BRANCH_MIN,
    ROOMS_PER_FLOOR_MAX,
    ROOMS_PER_FLOOR_MIN,
    TOWN_CENTER,
    TOWN_GRID_SIZE,
    TOWN_NPC_POSITIONS,
    TRAPS_PER_FLOOR,
    TUTORIAL_MONSTER_DMG_MULT,
    TUTORIAL_MONSTER_GOLD_MULT,
    TUTORIAL_MONSTER_HP_MULT,
    TUTORIAL_MONSTER_NAMES,
    TUTORIAL_ZONE_RATIO,
    VAULT_ROOMS_PER_FLOOR_MAX,
    VAULT_ROOMS_PER_FLOOR_MIN,
)
from src.generation.narrative import DummyBackend, _TRAP_DESCS, _TRAP_TYPES, _VAULT_TYPES


def generate_town(conn: sqlite3.Connection, backend: Optional[DummyBackend] = None) -> dict:
    """Generate Floor 0 town as a 5x5 grid with NPC rooms.

    Args:
        conn: Database connection (schema must already be initialized).
        backend: Narrative backend for text generation.

    Returns:
        Stats dict with rooms, npc_rooms, exits.
    """
    if backend is None:
        backend = DummyBackend()

    stats = {"rooms": 0, "npc_rooms": 0, "exits": 0}
    size = TOWN_GRID_SIZE
    center_r, center_c = TOWN_CENTER

    # Grid of room IDs: grid[row][col]
    grid: list[list[int]] = [[0] * size for _ in range(size)]

    # Create all 25 rooms
    for r in range(size):
        for c in range(size):
            npc = TOWN_NPC_POSITIONS.get((r, c))
            is_hub = 1 if (r, c) == (center_r, center_c) else 0

            name = backend.generate_town_room_name(r, c, npc_name=npc)
            desc = backend.generate_town_description(name, npc_name=npc)
            short = desc  # Town rooms use same desc for short

            room_id = _insert_room(
                conn, floor=0, name=name, desc=desc, desc_short=short,
                is_hub=is_hub, npc_name=npc,
            )
            grid[r][c] = room_id
            stats["rooms"] += 1
            if npc:
                stats["npc_rooms"] += 1

    # Connect adjacent rooms N/S/E/W
    for r in range(size):
        for c in range(size):
            room_id = grid[r][c]
            # North: r-1
            if r > 0:
                _insert_exit(conn, room_id, grid[r - 1][c], "n")
                stats["exits"] += 1
            # South: r+1
            if r < size - 1:
                _insert_exit(conn, room_id, grid[r + 1][c], "s")
                stats["exits"] += 1
            # West: c-1
            if c > 0:
                _insert_exit(conn, room_id, grid[r][c - 1], "w")
                stats["exits"] += 1
            # East: c+1
            if c < size - 1:
                _insert_exit(conn, room_id, grid[r][c + 1], "e")
                stats["exits"] += 1

    conn.commit()
    return stats


def generate_world(conn: sqlite3.Connection, backend: Optional[DummyBackend] = None) -> dict:
    """Generate the full dungeon world.

    Args:
        conn: Database connection (schema must already be initialized).
        backend: Narrative backend for text generation.

    Returns:
        Stats dict with room_count, monster_count, item_count.
    """
    if backend is None:
        backend = DummyBackend()

    stats = {"rooms": 0, "monsters": 0, "items": 0, "exits": 0}

    for floor_num in range(1, NUM_FLOORS + 1):
        floor_stats = _generate_floor(conn, floor_num, backend)
        for k in stats:
            stats[k] += floor_stats.get(k, 0)

    # Generate items across all tiers
    item_count = _generate_items(conn, backend)
    stats["items"] = item_count

    conn.commit()
    return stats


def _generate_floor(
    conn: sqlite3.Connection, floor: int, backend: DummyBackend
) -> dict:
    """Generate a single floor with hub-spoke layout.

    Returns stats dict.
    """
    theme = FLOOR_THEMES.get(floor, "Unknown Depths")
    stats = {"rooms": 0, "monsters": 0, "exits": 0}

    # Determine layout params
    num_branches = random.randint(BRANCHES_PER_FLOOR, BRANCHES_PER_FLOOR + 1)
    stairway_branch = random.randint(0, num_branches - 1) if floor < NUM_FLOORS else -1

    # How many vault rooms to inject
    num_vaults = random.randint(VAULT_ROOMS_PER_FLOOR_MIN, VAULT_ROOMS_PER_FLOOR_MAX)
    num_traps = random.randint(TRAPS_PER_FLOOR, TRAPS_PER_FLOOR + 1)

    # ── Create hub room ──
    hub_name = backend.generate_room_name(floor)
    hub_desc = backend.generate_room_description(floor, hub_name)
    hub_short = backend.generate_room_description_short(floor, hub_name)

    hub_id = _insert_room(conn, floor, hub_name, hub_desc, hub_short,
                          is_hub=1, is_checkpoint=1)
    stats["rooms"] += 1

    # Track all room IDs for this floor for loops and monster/vault placement
    branch_tips: list[int] = []  # Last room of each branch
    all_room_ids: list[int] = [hub_id]
    branch_rooms: list[list[int]] = []  # Room IDs per branch

    # ── Create branches ──
    for b in range(num_branches):
        branch_len = random.randint(ROOMS_PER_BRANCH_MIN, ROOMS_PER_BRANCH_MAX)
        is_stairway_branch = (b == stairway_branch)

        prev_room_id = hub_id
        b_rooms = []

        for r in range(branch_len):
            is_last = (r == branch_len - 1)
            is_stairway = is_last and is_stairway_branch

            name = backend.generate_room_name(floor)
            desc = backend.generate_room_description(floor, name)
            short = backend.generate_room_description_short(floor, name)

            # Roll for reveal content
            rgold = random.randint(REVEAL_GOLD_MIN, REVEAL_GOLD_MAX) if random.random() < REVEAL_GOLD_CHANCE else 0
            rlore = backend.generate_lore_fragment(floor) if random.random() < REVEAL_LORE_CHANCE else ""

            room_id = _insert_room(
                conn, floor, name, desc, short,
                is_stairway=1 if is_stairway else 0,
                reveal_gold=rgold, reveal_lore=rlore,
            )
            stats["rooms"] += 1
            all_room_ids.append(room_id)
            b_rooms.append(room_id)

            # Connect to previous room (bidirectional)
            if prev_room_id == hub_id:
                # Hub to first room — pick from available directions
                fwd, bwd = _pick_directions(b, num_branches)
            else:
                # Linear chain within branch
                fwd, bwd = _branch_directions()

            _insert_exit(conn, prev_room_id, room_id, fwd)
            _insert_exit(conn, room_id, prev_room_id, bwd)
            stats["exits"] += 2

            prev_room_id = room_id

        if b_rooms:
            branch_tips.append(b_rooms[-1])
        branch_rooms.append(b_rooms)

    # ── Add loops between branches ──
    num_loops = random.randint(LOOPS_PER_FLOOR, LOOPS_PER_FLOOR + 1)
    for _ in range(num_loops):
        if len(branch_rooms) < 2:
            break
        # Pick two different branches
        b1, b2 = random.sample(range(len(branch_rooms)), 2)
        if not branch_rooms[b1] or not branch_rooms[b2]:
            continue
        # Connect a mid-point room from each branch
        r1 = random.choice(branch_rooms[b1])
        r2 = random.choice(branch_rooms[b2])
        if r1 != r2:
            # Check no existing exit between them
            existing = conn.execute(
                "SELECT id FROM room_exits WHERE from_room_id = ? AND to_room_id = ?",
                (r1, r2),
            ).fetchone()
            if not existing:
                _insert_exit(conn, r1, r2, "e")
                _insert_exit(conn, r2, r1, "w")
                stats["exits"] += 2

    # ── Inject vault rooms ──
    non_hub_rooms = [rid for rid in all_room_ids if rid != hub_id]
    vault_parents = random.sample(non_hub_rooms, min(num_vaults, len(non_hub_rooms)))
    vault_room_ids = []

    for i, parent_id in enumerate(vault_parents):
        vault_type_name, vault_desc_extra = _VAULT_TYPES[i % len(_VAULT_TYPES)]
        name = backend.generate_room_name(floor)
        desc = backend.generate_room_description(floor, name, is_vault=True,
                                                  vault_type=vault_type_name)
        short = backend.generate_room_description_short(floor, name)

        # Riddle gate on some vault rooms
        riddle_text, riddle_answer = None, None
        if vault_type_name in ("treasure", "puzzle") and random.random() < 0.4:
            riddle_text, riddle_answer = backend.generate_riddle()

        room_id = _insert_room(
            conn, floor, name, desc, short,
            is_vault=1, riddle_answer=riddle_answer,
        )
        stats["rooms"] += 1
        all_room_ids.append(room_id)
        vault_room_ids.append(room_id)

        # Connect vault to parent
        _insert_exit(conn, parent_id, room_id, _random_dir())
        _insert_exit(conn, room_id, parent_id, _opposite_dir(_random_dir()))
        stats["exits"] += 2

    # ── Place traps guarding vault rooms ──
    trap_targets = random.sample(vault_room_ids, min(num_traps, len(vault_room_ids)))
    for room_id in trap_targets:
        trap_type = random.choice(_TRAP_TYPES)
        conn.execute(
            "UPDATE rooms SET trap_type = ? WHERE id = ?",
            (trap_type, room_id),
        )

    # ── Place monsters ──
    # Not every room has a monster — hub and some vault rooms are empty
    monster_rooms = [rid for rid in all_room_ids
                     if rid != hub_id and rid not in vault_room_ids[:1]]
    # About 60-70% of eligible rooms get monsters
    num_monsters = int(len(monster_rooms) * random.uniform(0.6, 0.75))
    monster_rooms = random.sample(monster_rooms, min(num_monsters, len(monster_rooms)))

    # Determine tutorial rooms for floor 1
    tutorial_room_ids: set[int] = set()
    if floor == 1:
        distances = _bfs_distances(conn, hub_id)
        # Sort rooms by distance, take first 25% as tutorial
        sorted_rooms = sorted(all_room_ids, key=lambda rid: distances.get(rid, 999))
        num_tutorial = max(1, int(len(sorted_rooms) * TUTORIAL_ZONE_RATIO))
        tutorial_room_ids = set(sorted_rooms[:num_tutorial])

    for room_id in monster_rooms:
        if room_id in tutorial_room_ids:
            _insert_tutorial_monster(conn, room_id, backend)
        else:
            tier = _floor_to_tier(floor)
            _insert_monster(conn, room_id, tier, backend)
        stats["monsters"] += 1

    return stats


def _generate_items(conn: sqlite3.Connection, backend: DummyBackend) -> int:
    """Generate the item pool for the epoch. Returns count."""
    items = []
    slots = ["weapon", "armor", "trinket"]

    for tier in range(1, 6):  # Tiers 1-5 for shops
        for slot in slots:
            name = _generate_item_name(tier, slot)
            pow_mod = _item_stat(tier, slot, "weapon")
            def_mod = _item_stat(tier, slot, "armor")
            spd_mod = _item_stat(tier, slot, "trinket")
            floor_source = min(tier, NUM_FLOORS)

            conn.execute(
                """INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod, floor_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, slot, tier, pow_mod, def_mod, spd_mod, floor_source),
            )
            items.append(name)

    # Tier 6 — loot-only endgame items
    for slot in slots:
        name = _generate_item_name(6, slot)
        pow_mod = _item_stat(6, slot, "weapon")
        def_mod = _item_stat(6, slot, "armor")
        spd_mod = _item_stat(6, slot, "trinket")

        conn.execute(
            """INSERT INTO items (name, slot, tier, pow_mod, def_mod, spd_mod, floor_source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, slot, 6, pow_mod, def_mod, spd_mod, NUM_FLOORS),
        )
        items.append(name)

    return len(items)


# ── Room helpers ───────────────────────────────────────────────────────────


def _insert_room(conn: sqlite3.Connection, floor: int, name: str, desc: str,
                 desc_short: str, is_hub: int = 0, is_checkpoint: int = 0,
                 is_stairway: int = 0, is_vault: int = 0,
                 riddle_answer: Optional[str] = None,
                 reveal_gold: int = 0, reveal_lore: str = "",
                 npc_name: Optional[str] = None) -> int:
    """Insert a room and return its ID."""
    # Enforce 150-char limit
    desc = desc[:LLM_OUTPUT_CHAR_LIMIT]
    desc_short = desc_short[:LLM_OUTPUT_CHAR_LIMIT]

    cursor = conn.execute(
        """INSERT INTO rooms (floor, name, description, description_short,
           is_hub, is_checkpoint, is_stairway, is_vault, riddle_answer,
           reveal_gold, reveal_lore, npc_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (floor, name, desc, desc_short, is_hub, is_checkpoint, is_stairway,
         is_vault, riddle_answer, reveal_gold, reveal_lore, npc_name),
    )
    return cursor.lastrowid


def _insert_exit(conn: sqlite3.Connection, from_id: int, to_id: int, direction: str) -> None:
    """Insert a room exit."""
    conn.execute(
        "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, ?)",
        (from_id, to_id, direction),
    )


def _insert_monster(conn: sqlite3.Connection, room_id: int, tier: int,
                    backend: DummyBackend) -> int:
    """Insert a monster and return its ID."""
    name = backend.generate_monster_name(tier)
    hp_max = _monster_hp(tier)
    pow_ = _monster_stat(tier, "pow")
    def_ = _monster_stat(tier, "def")
    spd = _monster_stat(tier, "spd")
    xp = _monster_xp(tier)
    gold_min, gold_max = _monster_gold(tier)

    cursor = conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (room_id, name, hp_max, hp_max, pow_, def_, spd, xp, gold_min, gold_max, tier),
    )
    return cursor.lastrowid


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


def _insert_tutorial_monster(conn: sqlite3.Connection, room_id: int,
                             backend: DummyBackend) -> int:
    """Insert a tutorial-zone monster with reduced stats."""
    tier = 1
    name = random.choice(TUTORIAL_MONSTER_NAMES)
    hp_max = max(1, int(_monster_hp(tier) * TUTORIAL_MONSTER_HP_MULT))
    pow_ = max(1, int(_monster_stat(tier, "pow") * TUTORIAL_MONSTER_DMG_MULT))
    def_ = _monster_stat(tier, "def")
    spd = _monster_stat(tier, "spd")
    xp = _monster_xp(tier)
    gold_min, gold_max = _monster_gold(tier)
    gold_min = int(gold_min * TUTORIAL_MONSTER_GOLD_MULT)
    gold_max = int(gold_max * TUTORIAL_MONSTER_GOLD_MULT)

    cursor = conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (room_id, name, hp_max, hp_max, pow_, def_, spd, xp, gold_min, gold_max, tier),
    )
    return cursor.lastrowid


# ── Direction helpers ──────────────────────────────────────────────────────


_DIRECTIONS = ["n", "s", "e", "w"]
_OPPOSITES = {"n": "s", "s": "n", "e": "w", "w": "e", "u": "d", "d": "u"}


def _pick_directions(branch_index: int, total_branches: int) -> tuple[str, str]:
    """Pick forward/backward directions for hub→branch connection."""
    dirs = ["n", "e", "s", "w"]
    fwd = dirs[branch_index % len(dirs)]
    bwd = _OPPOSITES[fwd]
    return fwd, bwd


def _branch_directions() -> tuple[str, str]:
    """Pick directions for within-branch connections."""
    # Branches extend in a consistent direction
    pairs = [("n", "s"), ("e", "w"), ("s", "n"), ("w", "e")]
    fwd, bwd = random.choice(pairs)
    return fwd, bwd


def _random_dir() -> str:
    return random.choice(_DIRECTIONS)


def _opposite_dir(d: str) -> str:
    return _OPPOSITES.get(d, "s")


# ── Monster stat scaling ──────────────────────────────────────────────────


def _floor_to_tier(floor: int) -> int:
    """Map floor number to primary monster tier."""
    # Floor 1: tier 1-2, Floor 2: tier 2-3, Floor 3: tier 3-4, Floor 4: tier 4-5
    base = floor
    if random.random() < 0.4:
        base = min(base + 1, 5)
    return base


def _monster_hp(tier: int) -> int:
    """Calculate monster HP based on tier."""
    base = {1: 15, 2: 30, 3: 50, 4: 75, 5: 100}
    hp = base.get(tier, 15)
    return hp + random.randint(-3, 5)


def _monster_stat(tier: int, stat: str) -> int:
    """Calculate a monster stat based on tier."""
    base = {1: 3, 2: 5, 3: 7, 4: 9, 5: 12}
    val = base.get(tier, 3)
    return max(1, val + random.randint(-1, 2))


def _monster_xp(tier: int) -> int:
    """Calculate monster XP reward."""
    base = {1: 10, 2: 25, 3: 45, 4: 70, 5: 100}
    return base.get(tier, 10) + random.randint(0, 5)


def _monster_gold(tier: int) -> tuple[int, int]:
    """Calculate monster gold drop range."""
    base_min = {1: 2, 2: 5, 3: 10, 4: 18, 5: 30}
    base_max = {1: 8, 2: 15, 3: 25, 4: 40, 5: 60}
    return base_min.get(tier, 2), base_max.get(tier, 8)


# ── Item generation ────────────────────────────────────────────────────────

_ITEM_NAMES = {
    "weapon": {
        1: "Rusty Sword", 2: "Iron Blade", 3: "Steel Falchion",
        4: "Mithril Edge", 5: "Adamant Cleaver", 6: "Void Reaper",
    },
    "armor": {
        1: "Leather Cap", 2: "Chain Mail", 3: "Steel Plate",
        4: "Mithril Guard", 5: "Adamant Shell", 6: "Void Aegis",
    },
    "trinket": {
        1: "Lucky Charm", 2: "Silver Ring", 3: "Crystal Amulet",
        4: "Mithril Band", 5: "Adamant Sigil", 6: "Void Shard",
    },
}


def _generate_item_name(tier: int, slot: str) -> str:
    """Get item name for tier and slot."""
    return _ITEM_NAMES.get(slot, {}).get(tier, f"T{tier} {slot.title()}")


def _item_stat(tier: int, slot: str, primary_slot: str) -> int:
    """Calculate item stat modifier. Primary stat scales with tier."""
    if slot == primary_slot:
        return tier * 2
    # Non-primary slots get minor stats at higher tiers
    if tier >= 3 and random.random() < 0.5:
        return 1
    return 0
