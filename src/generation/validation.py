"""
Post-generation validation pass for MMUD.
Checks all generated content meets the game's constraints.

Validates:
  - All text fields under 150 characters
  - No forbidden verbs in hint text
  - No unresolved template variables ({variable})
  - Room descriptions exist for all rooms
  - All 3 hint tiers exist for each secret
  - Multi-room puzzle symbols match across paired rooms
  - Exit symmetry (bidirectional)
  - Floor boss exists per floor
  - Room connectivity (no orphans)
"""

import re
import sqlite3

from config import (
    HINT_FORBIDDEN_VERBS,
    LLM_OUTPUT_CHAR_LIMIT,
    NUM_FLOORS,
)


def validate_epoch(conn: sqlite3.Connection) -> dict:
    """Run all validation checks on the generated epoch.

    Args:
        conn: Database connection with generated content.

    Returns:
        Dict with 'errors' (list of strings) and 'warnings' (list of strings).
        An epoch is valid when errors is empty.
    """
    errors: list[str] = []
    warnings: list[str] = []

    _validate_town(conn, errors, warnings)
    _validate_room_descriptions(conn, errors, warnings)
    _validate_room_exits(conn, errors, warnings)
    _validate_monsters(conn, errors, warnings)
    _validate_secrets(conn, errors, warnings)
    _validate_bounties(conn, errors, warnings)
    _validate_floor_bosses(conn, errors, warnings)
    _validate_narrative_skins(conn, errors, warnings)
    _validate_npc_dialogue(conn, errors, warnings)
    _validate_template_variables(conn, errors, warnings)
    _validate_spell_names(conn, errors, warnings)
    _validate_lore_fragments(conn, errors, warnings)
    _validate_floor_themes(conn, errors, warnings)

    return {"errors": errors, "warnings": warnings}


# ── Individual validators ─────────────────────────────────────────────────


def _validate_room_descriptions(conn: sqlite3.Connection,
                                 errors: list, warnings: list) -> None:
    """Check all room descriptions exist and are under limit."""
    rooms = conn.execute(
        "SELECT id, name, description, description_short FROM rooms"
    ).fetchall()

    if not rooms:
        errors.append("No rooms found in database")
        return

    for room in rooms:
        rid = room["id"]
        if not room["description"]:
            errors.append(f"Room {rid} ({room['name']}) has no description")
        elif len(room["description"]) > LLM_OUTPUT_CHAR_LIMIT:
            errors.append(
                f"Room {rid} description exceeds {LLM_OUTPUT_CHAR_LIMIT} chars: "
                f"{len(room['description'])}"
            )
        if not room["description_short"]:
            errors.append(f"Room {rid} ({room['name']}) has no short description")
        elif len(room["description_short"]) > LLM_OUTPUT_CHAR_LIMIT:
            errors.append(
                f"Room {rid} short description exceeds {LLM_OUTPUT_CHAR_LIMIT} chars: "
                f"{len(room['description_short'])}"
            )


def _validate_room_exits(conn: sqlite3.Connection,
                          errors: list, warnings: list) -> None:
    """Check exit symmetry and no orphan rooms."""
    rooms = conn.execute("SELECT id, is_breach FROM rooms").fetchall()
    room_ids = {r["id"] for r in rooms}

    # Check for orphan rooms (no exits at all)
    for room in rooms:
        rid = room["id"]
        exits = conn.execute(
            "SELECT COUNT(*) as cnt FROM room_exits WHERE from_room_id = ? OR to_room_id = ?",
            (rid, rid),
        ).fetchone()
        if exits["cnt"] == 0:
            errors.append(f"Room {rid} is an orphan (no exits)")

    # Check exit symmetry: if A→B exists, B→A should exist
    all_exits = conn.execute(
        "SELECT from_room_id, to_room_id FROM room_exits"
    ).fetchall()

    for ex in all_exits:
        reverse = conn.execute(
            "SELECT id FROM room_exits WHERE from_room_id = ? AND to_room_id = ?",
            (ex["to_room_id"], ex["from_room_id"]),
        ).fetchone()
        if not reverse:
            warnings.append(
                f"One-way exit: {ex['from_room_id']} → {ex['to_room_id']} "
                f"has no reverse"
            )


def _validate_monsters(conn: sqlite3.Connection,
                        errors: list, warnings: list) -> None:
    """Check monster stats are reasonable."""
    monsters = conn.execute(
        "SELECT id, name, hp, hp_max, room_id FROM monsters"
    ).fetchall()

    for m in monsters:
        if m["hp_max"] <= 0:
            # Skip raid boss placeholder (hp=0 is intentional)
            if m["hp"] == 0 and m["hp_max"] == 0:
                continue
            errors.append(f"Monster {m['id']} ({m['name']}) has invalid hp_max: {m['hp_max']}")
        if m["room_id"] not in {r["id"] for r in conn.execute("SELECT id FROM rooms").fetchall()}:
            errors.append(f"Monster {m['id']} references non-existent room {m['room_id']}")


def _validate_secrets(conn: sqlite3.Connection,
                       errors: list, warnings: list) -> None:
    """Check secrets have all required fields and valid hints."""
    secrets = conn.execute(
        "SELECT id, type, name, hint_tier1, hint_tier2, hint_tier3, "
        "puzzle_group, puzzle_symbol FROM secrets"
    ).fetchall()

    if not secrets:
        warnings.append("No secrets found in database")
        return

    for s in secrets:
        sid = s["id"]
        # Check all 3 hint tiers exist
        for tier in ["hint_tier1", "hint_tier2", "hint_tier3"]:
            hint = s[tier]
            if not hint:
                errors.append(f"Secret {sid} ({s['name']}) missing {tier}")
                continue
            if len(hint) > LLM_OUTPUT_CHAR_LIMIT:
                errors.append(
                    f"Secret {sid} {tier} exceeds {LLM_OUTPUT_CHAR_LIMIT} chars: "
                    f"{len(hint)}"
                )
            # Check forbidden verbs in hints
            hint_lower = hint.lower()
            for verb in HINT_FORBIDDEN_VERBS:
                if verb in hint_lower:
                    errors.append(
                        f"Secret {sid} {tier} contains forbidden verb '{verb}': "
                        f"{hint[:50]}"
                    )

    # Check multi-room puzzle symbol consistency
    puzzle_groups: dict[str, list] = {}
    for s in secrets:
        if s["puzzle_group"]:
            group = s["puzzle_group"]
            if group not in puzzle_groups:
                puzzle_groups[group] = []
            puzzle_groups[group].append(s)

    for group, members in puzzle_groups.items():
        symbols = {m["puzzle_symbol"] for m in members if m["puzzle_symbol"]}
        if len(symbols) > 1:
            errors.append(
                f"Puzzle group '{group}' has inconsistent symbols: {symbols}"
            )
        if len(members) < 2:
            warnings.append(
                f"Puzzle group '{group}' has only {len(members)} member(s)"
            )


def _validate_bounties(conn: sqlite3.Connection,
                        errors: list, warnings: list) -> None:
    """Check bounty descriptions are under limit."""
    bounties = conn.execute(
        "SELECT id, description, target_monster_id FROM bounties"
    ).fetchall()

    for b in bounties:
        if len(b["description"]) > LLM_OUTPUT_CHAR_LIMIT:
            errors.append(
                f"Bounty {b['id']} description exceeds {LLM_OUTPUT_CHAR_LIMIT} chars: "
                f"{len(b['description'])}"
            )


def _validate_floor_bosses(conn: sqlite3.Connection,
                             errors: list, warnings: list) -> None:
    """Check one floor boss exists per floor."""
    for floor in range(1, NUM_FLOORS + 1):
        boss = conn.execute(
            """SELECT m.id, m.name, m.mechanic FROM monsters m
               JOIN rooms r ON m.room_id = r.id
               WHERE m.is_floor_boss = 1 AND r.floor = ?""",
            (floor,),
        ).fetchone()
        if not boss:
            errors.append(f"No floor boss found on floor {floor}")
        elif not boss["mechanic"]:
            errors.append(f"Floor {floor} boss ({boss['name']}) has no mechanic")


def _validate_narrative_skins(conn: sqlite3.Connection,
                                errors: list, warnings: list) -> None:
    """Check narrative skin content lengths."""
    skins = conn.execute(
        "SELECT id, target, content FROM narrative_skins"
    ).fetchall()

    for skin in skins:
        if len(skin["content"]) > LLM_OUTPUT_CHAR_LIMIT:
            errors.append(
                f"Narrative skin {skin['id']} ({skin['target']}) exceeds "
                f"{LLM_OUTPUT_CHAR_LIMIT} chars: {len(skin['content'])}"
            )


def _validate_npc_dialogue(conn: sqlite3.Connection,
                             errors: list, warnings: list) -> None:
    """Check NPC dialogue lengths."""
    dialogues = conn.execute(
        "SELECT id, npc, dialogue FROM npc_dialogue"
    ).fetchall()

    for d in dialogues:
        if len(d["dialogue"]) > LLM_OUTPUT_CHAR_LIMIT:
            errors.append(
                f"NPC dialogue {d['id']} ({d['npc']}) exceeds "
                f"{LLM_OUTPUT_CHAR_LIMIT} chars: {len(d['dialogue'])}"
            )


def _validate_template_variables(conn: sqlite3.Connection,
                                   errors: list, warnings: list) -> None:
    """Check for unresolved {variable} template markers in text fields."""
    template_pattern = re.compile(r'\{[a-zA-Z_]+\}')

    # Check room descriptions
    rooms = conn.execute("SELECT id, description FROM rooms").fetchall()
    for room in rooms:
        if room["description"] and template_pattern.search(room["description"]):
            warnings.append(
                f"Room {room['id']} description has unresolved template: "
                f"{room['description'][:50]}"
            )

    # Check bounty descriptions
    bounties = conn.execute("SELECT id, description FROM bounties").fetchall()
    for b in bounties:
        if template_pattern.search(b["description"]):
            warnings.append(
                f"Bounty {b['id']} description has unresolved template: "
                f"{b['description'][:50]}"
            )

    # Check hints
    secrets = conn.execute(
        "SELECT id, hint_tier1, hint_tier2, hint_tier3 FROM secrets"
    ).fetchall()
    for s in secrets:
        for tier in ["hint_tier1", "hint_tier2", "hint_tier3"]:
            hint = s[tier]
            if hint and template_pattern.search(hint):
                warnings.append(
                    f"Secret {s['id']} {tier} has unresolved template: "
                    f"{hint[:50]}"
                )


def _validate_spell_names(conn: sqlite3.Connection,
                           errors: list, warnings: list) -> None:
    """Check epoch spell names exist and are under 20 chars."""
    epoch = conn.execute("SELECT spell_names FROM epoch WHERE id = 1").fetchone()
    if not epoch:
        return
    spell_csv = epoch["spell_names"]
    if not spell_csv:
        warnings.append("No spell names generated for epoch")
        return
    names = [s.strip() for s in spell_csv.split(",")]
    if len(names) != 3:
        errors.append(f"Expected 3 spell names, got {len(names)}")
    for name in names:
        if len(name) > 20:
            errors.append(f"Spell name exceeds 20 chars: '{name}' ({len(name)})")


def _validate_lore_fragments(conn: sqlite3.Connection,
                              errors: list, warnings: list) -> None:
    """Check lore fragments are under 80 chars."""
    from config import REVEAL_LORE_MAX_CHARS
    rooms = conn.execute(
        "SELECT id, name, reveal_lore FROM rooms WHERE reveal_lore != ''"
    ).fetchall()
    for room in rooms:
        if len(room["reveal_lore"]) > REVEAL_LORE_MAX_CHARS:
            errors.append(
                f"Room {room['id']} ({room['name']}) lore exceeds {REVEAL_LORE_MAX_CHARS} chars: "
                f"{len(room['reveal_lore'])}"
            )


def _validate_town(conn: sqlite3.Connection,
                    errors: list, warnings: list) -> None:
    """Validate Floor 0 town grid."""
    from config import TOWN_GRID_SIZE, TOWN_NPC_POSITIONS

    rooms = conn.execute(
        "SELECT id, name, is_hub, npc_name FROM rooms WHERE floor = 0"
    ).fetchall()

    if not rooms:
        warnings.append("No Floor 0 (town) rooms found")
        return

    expected = TOWN_GRID_SIZE * TOWN_GRID_SIZE
    if len(rooms) != expected:
        errors.append(f"Floor 0 has {len(rooms)} rooms, expected {expected}")

    # Center room is hub
    hubs = [r for r in rooms if r["is_hub"]]
    if not hubs:
        errors.append("Floor 0 has no hub room")
    elif len(hubs) > 1:
        errors.append(f"Floor 0 has {len(hubs)} hub rooms, expected 1")

    # NPC rooms exist
    expected_npcs = set(TOWN_NPC_POSITIONS.values())
    found_npcs = {r["npc_name"] for r in rooms if r["npc_name"]}
    missing = expected_npcs - found_npcs
    if missing:
        errors.append(f"Floor 0 missing NPC rooms: {missing}")

    # All rooms connected (no orphans)
    for room in rooms:
        exits = conn.execute(
            "SELECT COUNT(*) as cnt FROM room_exits WHERE from_room_id = ? OR to_room_id = ?",
            (room["id"], room["id"]),
        ).fetchone()
        if exits["cnt"] == 0:
            errors.append(f"Floor 0 room {room['id']} ({room['name']}) is an orphan")

    # No monsters on Floor 0
    monsters = conn.execute(
        """SELECT m.id, m.name FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 0"""
    ).fetchall()
    if monsters:
        errors.append(f"Floor 0 has {len(monsters)} monsters (should be 0)")


def _validate_floor_themes(conn: sqlite3.Connection,
                            errors: list, warnings: list) -> None:
    """Check floor themes table has correct entries."""
    rows = conn.execute(
        "SELECT floor, floor_name, atmosphere, narrative_beat, floor_transition FROM floor_themes"
    ).fetchall()

    if not rows:
        warnings.append("No floor themes found in database")
        return

    if len(rows) != NUM_FLOORS:
        errors.append(f"floor_themes has {len(rows)} rows, expected {NUM_FLOORS}")

    found_floors = set()
    for row in rows:
        floor = row["floor"]
        found_floors.add(floor)

        for field in ("floor_name", "atmosphere", "narrative_beat", "floor_transition"):
            val = row[field]
            if not val:
                errors.append(f"Floor {floor} theme missing {field}")
            elif len(val) > LLM_OUTPUT_CHAR_LIMIT:
                errors.append(
                    f"Floor {floor} theme {field} exceeds {LLM_OUTPUT_CHAR_LIMIT} chars: "
                    f"{len(val)}"
                )

    expected_floors = set(range(1, NUM_FLOORS + 1))
    missing = expected_floors - found_floors
    if missing:
        errors.append(f"Floor themes missing for floors: {sorted(missing)}")
