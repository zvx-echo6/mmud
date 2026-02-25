"""
Secret placement for MMUD.
Places 20 secrets per epoch across 5 types with 3 hint tiers each.

Distribution:
  - Observation (6): 4 on floors 1-2, 2 on floors 3-4
  - Puzzle (4): 1-2 single-room, 2-3 multi-room
  - Lore (4): Tied to NPC dialogue / barkeep hints
  - Stat-gated (3): One each POW/SPD/DEF, floors 3-4
  - Breach (3): Inside Breach zone, not available until day 15
"""

import random
import sqlite3
from typing import Optional

from config import (
    HINT_FORBIDDEN_VERBS,
    LLM_OUTPUT_CHAR_LIMIT,
    MULTI_ROOM_PUZZLES_MAX,
    MULTI_ROOM_PUZZLES_MIN,
    NUM_FLOORS,
    SECRETS_PER_EPOCH,
)
from src.generation.narrative import DummyBackend


# Puzzle archetypes
_PUZZLE_ARCHETYPES = ["paired_mechanism", "sequence_lock", "cooperative_trigger"]

# Shared symbols for multi-room puzzles
_PUZZLE_SYMBOLS = [
    "serpent", "sun", "moon", "eye", "crown", "flame", "crystal", "rune",
    "key", "spiral", "arrow", "star", "wave", "leaf", "skull",
]

# Secret reward types
_REWARD_TYPES = ["lore_fragment", "stat_bump", "consumable", "shortcut"]

# Observation secret features (observable details in room descriptions)
_OBSERVATION_FEATURES = [
    "Scratches mark the south wall.",
    "A faint draft seeps from below.",
    "One stone sits slightly higher than others.",
    "Discolored mortar lines a section of wall.",
    "A thin crack runs along the base.",
    "Tool marks differ on one block.",
    "Dust patterns suggest recent movement.",
    "A loose tile wobbles underfoot.",
    "Faded paint outlines a shape on the wall.",
    "An iron ring is set into the floor.",
    "A hollow echo comes from one section.",
    "Moisture beads on one patch of stone.",
]

# Stat-gated descriptions
_STAT_GATED = {
    "pow": {
        "desc": "The cracked wall is thin here. Brute force could break through.",
        "hint_t3": "The thin wall yields to strength.",
    },
    "spd": {
        "desc": "The corridor narrows and the ceiling sags. Speed or nothing.",
        "hint_t3": "Only the swift survive the collapse.",
    },
    "def": {
        "desc": "Acrid fumes seep from vents. Only the hardy can endure.",
        "hint_t3": "Endurance conquers the toxic chamber.",
    },
}


def generate_secrets(
    conn: sqlite3.Connection, backend: Optional[DummyBackend] = None,
    breach_room_ids: Optional[list[int]] = None,
) -> dict:
    """Place all 20 secrets for the epoch.

    Args:
        conn: Database connection.
        backend: Narrative backend.
        breach_room_ids: Room IDs in the Breach zone for Breach secrets.

    Returns:
        Stats dict with counts per type.
    """
    if backend is None:
        backend = DummyBackend()
    if breach_room_ids is None:
        breach_room_ids = []

    stats = {
        "observation": 0, "puzzle": 0, "lore": 0,
        "stat_gated": 0, "breach": 0, "total": 0,
    }

    # Get available rooms by floor
    rooms_by_floor = _get_rooms_by_floor(conn)

    # ── Observation (6) — 4 on floors 1-2, 2 on floors 3-4 ──
    obs_rooms_low = _pick_rooms(rooms_by_floor, [1, 2], 4)
    obs_rooms_high = _pick_rooms(rooms_by_floor, [3, 4], 2)
    for room in obs_rooms_low + obs_rooms_high:
        _place_observation_secret(conn, room, backend)
        stats["observation"] += 1

    # ── Puzzle (4) — 1-2 single, 2-3 multi-room ──
    num_multi = random.randint(MULTI_ROOM_PUZZLES_MIN, MULTI_ROOM_PUZZLES_MAX)
    num_single = 4 - num_multi

    for _ in range(num_single):
        room = _pick_rooms(rooms_by_floor, [1, 2, 3], 1)
        if room:
            _place_single_puzzle(conn, room[0], backend)
            stats["puzzle"] += 1

    for i in range(num_multi):
        archetype = _PUZZLE_ARCHETYPES[i % len(_PUZZLE_ARCHETYPES)]
        symbol = random.choice(_PUZZLE_SYMBOLS)
        # Pick 2 rooms on same floor for multi-room puzzles
        floor = random.choice([1, 2, 3])
        pair = _pick_rooms(rooms_by_floor, [floor], 2)
        if len(pair) >= 2:
            _place_multi_puzzle(conn, pair, archetype, symbol, backend)
            stats["puzzle"] += 1

    # ── Lore (4) ──
    for _ in range(4):
        room = _pick_rooms(rooms_by_floor, [1, 2, 3, 4], 1)
        if room:
            _place_lore_secret(conn, room[0], backend)
            stats["lore"] += 1

    # ── Stat-gated (3) — one POW, one SPD, one DEF ──
    for stat in ["pow", "spd", "def"]:
        room = _pick_rooms(rooms_by_floor, [3, 4], 1)
        if room:
            _place_stat_gated_secret(conn, room[0], stat, backend)
            stats["stat_gated"] += 1

    # ── Breach (3) — inside Breach zone ──
    if breach_room_ids:
        breach_targets = random.sample(
            breach_room_ids, min(3, len(breach_room_ids))
        )
        for room_id in breach_targets:
            room = conn.execute(
                "SELECT id, floor, name FROM rooms WHERE id = ?", (room_id,)
            ).fetchone()
            if room:
                _place_breach_secret(conn, dict(room), backend)
                stats["breach"] += 1

    stats["total"] = sum(v for k, v in stats.items() if k != "total")
    conn.commit()
    return stats


# ── Secret placement functions ─────────────────────────────────────────────


def _place_observation_secret(conn: sqlite3.Connection, room: dict,
                              backend: DummyBackend) -> None:
    """Place an observation secret — tied to a room feature."""
    feature = random.choice(_OBSERVATION_FEATURES)
    floor = room["floor"]
    name = f"Hidden Detail in {room['name']}"
    theme = _floor_theme(floor)
    direction = random.choice(["eastern", "western", "northern", "southern"])

    hint1 = backend.generate_hint(1, floor, theme=theme)
    hint2 = backend.generate_hint(2, floor, direction=direction, theme=theme)
    hint3 = backend.generate_hint(3, floor, room_name=room["name"], theme=theme)

    # Validate hints — no forbidden verbs
    hint1 = _sanitize_hint(hint1)
    hint2 = _sanitize_hint(hint2)
    hint3 = _sanitize_hint(hint3)

    reward = random.choice(_REWARD_TYPES)

    conn.execute(
        """INSERT INTO secrets (type, floor, room_id, name, description, reward_type,
           hint_tier1, hint_tier2, hint_tier3)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("observation", floor, room["id"], name[:80], feature[:LLM_OUTPUT_CHAR_LIMIT],
         reward, hint1, hint2, hint3),
    )

    # Inject feature into room description (two-pass system)
    _inject_feature_into_room(conn, room["id"], feature)


def _place_single_puzzle(conn: sqlite3.Connection, room: dict,
                         backend: DummyBackend) -> None:
    """Place a single-room puzzle secret."""
    floor = room["floor"]
    theme = _floor_theme(floor)
    name = f"Puzzle in {room['name']}"
    desc = "Symbols cover the walls. A pattern emerges."

    hint1 = backend.generate_hint(1, floor, theme=theme)
    hint2 = backend.generate_hint(2, floor, direction="deeper", theme=theme)
    hint3 = f"The symbols in {room['name']} form a sequence."

    hint1 = _sanitize_hint(hint1)
    hint2 = _sanitize_hint(hint2)
    hint3 = _sanitize_hint(hint3)

    reward = random.choice(_REWARD_TYPES)

    conn.execute(
        """INSERT INTO secrets (type, floor, room_id, name, description, reward_type,
           hint_tier1, hint_tier2, hint_tier3)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("puzzle", floor, room["id"], name[:80], desc[:LLM_OUTPUT_CHAR_LIMIT],
         reward, hint1, hint2, hint3),
    )


def _place_multi_puzzle(conn: sqlite3.Connection, rooms: list[dict],
                        archetype: str, symbol: str,
                        backend: DummyBackend) -> None:
    """Place a multi-room puzzle across 2+ rooms."""
    floor = rooms[0]["floor"]
    theme = _floor_theme(floor)
    group_id = f"puzzle_{floor}_{symbol}"

    for i, room in enumerate(rooms):
        name = f"{symbol.title()} Puzzle ({i + 1}/{len(rooms)})"
        desc = f"A {symbol} motif marks the stonework here."

        hint1 = backend.generate_hint(1, floor, theme=theme)
        hint2 = f"The {symbol} appears in multiple rooms on Floor {floor}."
        hint3 = f"Connect the {symbol} marks between rooms."

        hint1 = _sanitize_hint(hint1)
        hint2 = _sanitize_hint(hint2)[:LLM_OUTPUT_CHAR_LIMIT]
        hint3 = _sanitize_hint(hint3)[:LLM_OUTPUT_CHAR_LIMIT]

        reward = "shortcut" if i == 0 else random.choice(_REWARD_TYPES)

        conn.execute(
            """INSERT INTO secrets (type, floor, room_id, name, description, reward_type,
               hint_tier1, hint_tier2, hint_tier3,
               puzzle_group, puzzle_archetype, puzzle_order, puzzle_symbol)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("puzzle", floor, room["id"], name[:80], desc[:LLM_OUTPUT_CHAR_LIMIT],
             reward, hint1, hint2, hint3,
             group_id, archetype, i + 1, symbol),
        )


def _place_lore_secret(conn: sqlite3.Connection, room: dict,
                       backend: DummyBackend) -> None:
    """Place a lore secret — tied to NPC dialogue."""
    floor = room["floor"]
    theme = _floor_theme(floor)
    name = f"Lore of {room['name']}"
    desc = "Ancient knowledge rewards the attentive."

    hint1 = f"The sage speaks of {theme} lore."
    hint2 = f"Lore hides near {room['name']} on Floor {floor}."
    hint3 = f"The inscription in {room['name']} holds meaning."

    hint1 = _sanitize_hint(hint1)[:LLM_OUTPUT_CHAR_LIMIT]
    hint2 = _sanitize_hint(hint2)[:LLM_OUTPUT_CHAR_LIMIT]
    hint3 = _sanitize_hint(hint3)[:LLM_OUTPUT_CHAR_LIMIT]

    reward = random.choice(["lore_fragment", "stat_bump"])

    conn.execute(
        """INSERT INTO secrets (type, floor, room_id, name, description, reward_type,
           hint_tier1, hint_tier2, hint_tier3)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("lore", floor, room["id"], name[:80], desc[:LLM_OUTPUT_CHAR_LIMIT],
         reward, hint1, hint2, hint3),
    )


def _place_stat_gated_secret(conn: sqlite3.Connection, room: dict,
                              stat: str, backend: DummyBackend) -> None:
    """Place a stat-gated secret (POW/SPD/DEF)."""
    floor = room["floor"]
    theme = _floor_theme(floor)
    info = _STAT_GATED[stat]
    name = f"{stat.upper()} Challenge in {room['name']}"

    hint1 = f"Strength is tested in the {theme}."
    hint2 = f"A {stat.upper()} challenge waits on Floor {floor}."
    hint3 = info["hint_t3"]

    hint1 = _sanitize_hint(hint1)[:LLM_OUTPUT_CHAR_LIMIT]
    hint2 = _sanitize_hint(hint2)[:LLM_OUTPUT_CHAR_LIMIT]
    hint3 = _sanitize_hint(hint3)[:LLM_OUTPUT_CHAR_LIMIT]

    conn.execute(
        """INSERT INTO secrets (type, floor, room_id, name, description, reward_type,
           hint_tier1, hint_tier2, hint_tier3)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("stat_gated", floor, room["id"], name[:80],
         info["desc"][:LLM_OUTPUT_CHAR_LIMIT], "stat_bump",
         hint1, hint2, hint3),
    )


def _place_breach_secret(conn: sqlite3.Connection, room: dict,
                          backend: DummyBackend) -> None:
    """Place a Breach secret in the Breach zone."""
    floor = room["floor"]
    name = f"Breach Secret in {room['name']}"
    desc = "The rift reveals something hidden."

    hint1 = "The Breach holds unseen treasures."
    hint2 = f"Deep in the Breach near {room['name']}."
    hint3 = f"The rift energy in {room['name']} conceals a reward."

    hint1 = _sanitize_hint(hint1)[:LLM_OUTPUT_CHAR_LIMIT]
    hint2 = _sanitize_hint(hint2)[:LLM_OUTPUT_CHAR_LIMIT]
    hint3 = _sanitize_hint(hint3)[:LLM_OUTPUT_CHAR_LIMIT]

    reward = random.choice(_REWARD_TYPES)

    conn.execute(
        """INSERT INTO secrets (type, floor, room_id, name, description, reward_type,
           hint_tier1, hint_tier2, hint_tier3)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("breach", floor, room["id"], name[:80], desc[:LLM_OUTPUT_CHAR_LIMIT],
         reward, hint1, hint2, hint3),
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_rooms_by_floor(conn: sqlite3.Connection) -> dict[int, list[dict]]:
    """Get all non-hub, non-breach rooms grouped by floor."""
    rows = conn.execute(
        """SELECT id, floor, name FROM rooms
           WHERE is_hub = 0 AND is_breach = 0
           ORDER BY floor, id"""
    ).fetchall()
    result: dict[int, list[dict]] = {}
    for r in rows:
        f = r["floor"]
        if f not in result:
            result[f] = []
        result[f].append(dict(r))
    return result


def _pick_rooms(rooms_by_floor: dict, floors: list[int], count: int) -> list[dict]:
    """Pick count rooms from the specified floors. Removes them from pool."""
    candidates = []
    for f in floors:
        candidates.extend(rooms_by_floor.get(f, []))
    if not candidates:
        return []
    chosen = random.sample(candidates, min(count, len(candidates)))
    # Remove chosen from pool
    for room in chosen:
        f = room["floor"]
        if f in rooms_by_floor and room in rooms_by_floor[f]:
            rooms_by_floor[f].remove(room)
    return chosen


def _floor_theme(floor: int) -> str:
    """Get floor theme name."""
    from config import FLOOR_THEMES
    return FLOOR_THEMES.get(floor, "Unknown Depths")


def _sanitize_hint(hint: str) -> str:
    """Remove forbidden action verbs from hint text."""
    hint_lower = hint.lower()
    for verb in HINT_FORBIDDEN_VERBS:
        if verb in hint_lower:
            # Replace the verb with a neutral alternative
            hint = hint.replace(verb, "notice")
            hint = hint.replace(verb.title(), "Notice")
    return hint[:LLM_OUTPUT_CHAR_LIMIT]


def _inject_feature_into_room(conn: sqlite3.Connection, room_id: int,
                               feature: str) -> None:
    """Inject an observable feature into a room's description (two-pass)."""
    row = conn.execute(
        "SELECT description FROM rooms WHERE id = ?", (room_id,)
    ).fetchone()
    if not row:
        return

    existing = row["description"]
    # Append feature if room has space
    combined = f"{existing} {feature}"
    if len(combined) > LLM_OUTPUT_CHAR_LIMIT:
        # Replace last sentence with feature
        combined = feature
    conn.execute(
        "UPDATE rooms SET description = ? WHERE id = ?",
        (combined[:LLM_OUTPUT_CHAR_LIMIT], room_id),
    )
