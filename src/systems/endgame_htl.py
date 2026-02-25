"""
Hold the Line — Endgame mode runtime logic.

Territory control: clear rooms, establish checkpoints, kill floor bosses.
Dungeon regen pushes back. Checkpoints lock progress permanently.

Floor 4 Warden kill = epoch win.
"""

import json
import random
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import (
    HTL_CHECKPOINTS_PER_FLOOR,
    HTL_REGEN_ROOMS_PER_DAY,
    MSG_CHAR_LIMIT,
    NUM_FLOORS,
    WARDEN_REGEN_INTERVAL_HOURS,
    WARDEN_REGEN_RATE,
)
from src.systems import broadcast as broadcast_sys


# ── Room Clearing ──────────────────────────────────────────────────────────


def clear_room(conn: sqlite3.Connection, room_id: int) -> dict:
    """Mark a room as cleared after all monsters killed.

    Returns:
        Status dict with cleared, checkpoint_ready, boss_spawned keys.
    """
    result = {"cleared": False, "checkpoint_ready": False, "boss_spawned": False}

    conn.execute(
        "UPDATE rooms SET htl_cleared = 1, htl_cleared_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), room_id),
    )
    result["cleared"] = True

    # Check if this completes a checkpoint cluster
    room = conn.execute(
        "SELECT floor, is_checkpoint FROM rooms WHERE id = ?", (room_id,)
    ).fetchone()
    if not room:
        return result

    floor = room["floor"]
    checkpoint_info = check_checkpoint_cluster(conn, floor)
    if checkpoint_info:
        result["checkpoint_ready"] = True
        result["checkpoint_room_id"] = checkpoint_info["room_id"]
        result["checkpoint_position"] = checkpoint_info["position"]

    conn.commit()
    return result


def check_checkpoint_cluster(
    conn: sqlite3.Connection, floor: int
) -> Optional[dict]:
    """Check if any checkpoint cluster on this floor is fully cleared.

    A checkpoint cluster = the checkpoint room + all adjacent rooms.
    All must be cleared (htl_cleared = 1) and the checkpoint not yet established.

    Returns:
        Dict with room_id and position if a cluster is ready, else None.
    """
    checkpoints = conn.execute(
        """SELECT hc.room_id, hc.position, hc.established
           FROM htl_checkpoints hc
           WHERE hc.floor = ? AND hc.established = 0
           ORDER BY hc.id""",
        (floor,),
    ).fetchall()

    for cp in checkpoints:
        # Get adjacent rooms
        adjacent_ids = conn.execute(
            """SELECT to_room_id FROM room_exits
               WHERE from_room_id = ?""",
            (cp["room_id"],),
        ).fetchall()

        cluster_ids = [cp["room_id"]] + [a["to_room_id"] for a in adjacent_ids]

        # Check if all are cleared
        cleared_count = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM rooms
                WHERE id IN ({','.join('?' * len(cluster_ids))})
                AND htl_cleared = 1""",
            cluster_ids,
        ).fetchone()

        if cleared_count["cnt"] >= len(cluster_ids):
            return {"room_id": cp["room_id"], "position": cp["position"]}

    return None


def establish_checkpoint(
    conn: sqlite3.Connection, room_id: int, player_id: int
) -> tuple[bool, str]:
    """Establish a checkpoint after floor boss is killed.

    Args:
        conn: Database connection.
        room_id: Checkpoint room ID.
        player_id: Player who triggered it.

    Returns:
        (success, message)
    """
    cp = conn.execute(
        "SELECT * FROM htl_checkpoints WHERE room_id = ? AND established = 0",
        (room_id,),
    ).fetchone()

    if not cp:
        return False, "No checkpoint here to establish."

    conn.execute(
        """UPDATE htl_checkpoints SET
           established = 1,
           established_at = ?,
           established_by = ?
           WHERE room_id = ?""",
        (datetime.now(timezone.utc).isoformat(), player_id, room_id),
    )

    floor = cp["floor"]
    position = cp["position"]

    # Broadcast
    msg = f"F{floor} Checkpoint {position} established! Darkness cannot pass."
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    # If stairway checkpoint, unlock next floor
    if position == "stairway" and floor < NUM_FLOORS:
        msg2 = f"Floor {floor + 1} unlocked! The descent continues."
        broadcast_sys.create_broadcast(conn, 1, msg2[:MSG_CHAR_LIMIT])

    conn.commit()
    return True, f"Checkpoint {position} on Floor {floor} established!"


# ── Regen Ticks ────────────────────────────────────────────────────────────


def apply_htl_regen(conn: sqlite3.Connection) -> dict:
    """Apply daily regen: revert cleared rooms back to hostile.

    Rooms behind established checkpoints are immune.
    Checkpoint rooms themselves are immune.
    Spread ticks across the day by reverting the per-floor count.

    Returns:
        Dict with per-floor revert counts.
    """
    stats = {}

    for floor in range(1, NUM_FLOORS + 1):
        rooms_to_revert = HTL_REGEN_ROOMS_PER_DAY.get(floor, 3)

        # Get immune room IDs (rooms behind established checkpoints + checkpoint rooms)
        immune_ids = _get_immune_room_ids(conn, floor)

        # Pick random cleared non-immune rooms to revert
        if immune_ids:
            placeholders = ",".join("?" * len(immune_ids))
            reverted = conn.execute(
                f"""SELECT id, name FROM rooms
                    WHERE floor = ? AND htl_cleared = 1
                    AND id NOT IN ({placeholders})
                    AND is_checkpoint = 0
                    ORDER BY RANDOM()
                    LIMIT ?""",
                [floor] + list(immune_ids) + [rooms_to_revert],
            ).fetchall()
        else:
            reverted = conn.execute(
                """SELECT id, name FROM rooms
                   WHERE floor = ? AND htl_cleared = 1
                   AND is_checkpoint = 0
                   ORDER BY RANDOM()
                   LIMIT ?""",
                (floor, rooms_to_revert),
            ).fetchall()

        for room in reverted:
            conn.execute(
                "UPDATE rooms SET htl_cleared = 0, htl_cleared_at = NULL WHERE id = ?",
                (room["id"],),
            )
            # Respawn a monster in the reverted room
            _respawn_monster(conn, room["id"], floor)

        stats[floor] = len(reverted)

    # Broadcast frontline status if rooms were lost
    total_lost = sum(stats.values())
    if total_lost > 0:
        _broadcast_frontline_status(conn, stats)

    conn.commit()
    return stats


def _get_immune_room_ids(conn: sqlite3.Connection, floor: int) -> list[int]:
    """Get room IDs that are immune to regen on this floor.

    Rooms are immune if they are behind (or are) an established checkpoint.
    """
    immune = set()

    # All established checkpoint rooms on this floor
    established = conn.execute(
        """SELECT room_id FROM htl_checkpoints
           WHERE floor = ? AND established = 1""",
        (floor,),
    ).fetchall()

    for cp in established:
        immune.add(cp["room_id"])
        # All adjacent rooms to established checkpoints are also immune
        adjacent = conn.execute(
            "SELECT to_room_id FROM room_exits WHERE from_room_id = ?",
            (cp["room_id"],),
        ).fetchall()
        for adj in adjacent:
            # Only immune if on same floor
            room = conn.execute(
                "SELECT floor FROM rooms WHERE id = ?", (adj["to_room_id"],)
            ).fetchone()
            if room and room["floor"] == floor:
                immune.add(adj["to_room_id"])

    return list(immune)


def _respawn_monster(conn: sqlite3.Connection, room_id: int, floor: int) -> None:
    """Respawn a basic monster in a reverted room."""
    tier = min(floor, 5)
    hp = 15 + floor * 10
    pow_ = 3 + floor * 2
    def_ = 2 + floor
    spd = 2 + floor

    names = {
        1: ["Shambling Corpse", "Cave Rat", "Feral Goblin"],
        2: ["Spore Walker", "Fungal Horror", "Mushroom Lurker"],
        3: ["Ember Drake", "Obsidian Golem", "Fire Imp"],
        4: ["Void Shade", "Crystal Lich", "Shadow Stalker"],
    }
    name = random.choice(names.get(floor, ["Dark Creature"]))

    conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (room_id, name, hp, hp, pow_, def_, spd,
         5 + floor * 3, floor * 2, floor * 5, tier),
    )


def _broadcast_frontline_status(conn: sqlite3.Connection, stats: dict) -> None:
    """Broadcast which floors lost rooms."""
    for floor, lost in stats.items():
        if lost > 0:
            # Count remaining cleared rooms on floor
            cleared = conn.execute(
                "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ? AND htl_cleared = 1",
                (floor,),
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ?",
                (floor,),
            ).fetchone()
            msg = f"F{floor} lost {lost} rooms. {cleared['cnt']}/{total['cnt']} held."
            broadcast_sys.create_broadcast(conn, 2, msg[:MSG_CHAR_LIMIT])


# ── Floor Control ──────────────────────────────────────────────────────────


def get_floor_control(conn: sqlite3.Connection) -> dict:
    """Get floor control percentages for all floors.

    Returns:
        Dict of floor -> {"cleared": N, "total": N, "percent": float,
                         "checkpoints_established": N, "checkpoints_total": N}
    """
    result = {}
    for floor in range(1, NUM_FLOORS + 1):
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ? AND is_breach = 0",
            (floor,),
        ).fetchone()["cnt"]

        cleared = conn.execute(
            "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ? AND htl_cleared = 1 AND is_breach = 0",
            (floor,),
        ).fetchone()["cnt"]

        cp_total = conn.execute(
            "SELECT COUNT(*) as cnt FROM htl_checkpoints WHERE floor = ?",
            (floor,),
        ).fetchone()["cnt"]

        cp_established = conn.execute(
            "SELECT COUNT(*) as cnt FROM htl_checkpoints WHERE floor = ? AND established = 1",
            (floor,),
        ).fetchone()["cnt"]

        pct = (cleared / total * 100) if total > 0 else 0

        result[floor] = {
            "cleared": cleared,
            "total": total,
            "percent": round(pct, 1),
            "checkpoints_established": cp_established,
            "checkpoints_total": cp_total,
        }

    return result


def format_htl_status(conn: sqlite3.Connection) -> str:
    """Format HtL status for barkeep recap or status command."""
    control = get_floor_control(conn)
    parts = []
    for floor, data in control.items():
        cp = f"{data['checkpoints_established']}/{data['checkpoints_total']}cp"
        parts.append(f"F{floor}:{data['percent']:.0f}%({cp})")
    return "HtL: " + " ".join(parts)


# ── Boss Mechanics ─────────────────────────────────────────────────────────


def apply_boss_mechanic(
    conn: sqlite3.Connection,
    boss: dict,
    player: dict,
    damage: int,
    combat_round: int = 1,
) -> dict:
    """Apply floor boss mechanic modifications to combat.

    Args:
        conn: Database connection.
        boss: Monster dict (with 'mechanic' field).
        player: Player dict.
        damage: Base damage player would deal.
        combat_round: Current round number in this engagement.

    Returns:
        Dict with modified damage, extra_damage_to_player, messages, flee_blocked.
    """
    result = {
        "damage": damage,
        "extra_damage_to_player": 0,
        "messages": [],
        "flee_blocked": False,
        "boss_immune": False,
    }

    mechanic_raw = boss.get("mechanic")
    if not mechanic_raw:
        return result

    # Parse mechanics (single string or JSON array)
    try:
        mechanics = json.loads(mechanic_raw)
        if isinstance(mechanics, str):
            mechanics = [mechanics]
    except (json.JSONDecodeError, TypeError):
        mechanics = [mechanic_raw]

    for mechanic in mechanics:
        _apply_single_mechanic(conn, mechanic, boss, player, result, combat_round)

    return result


def _apply_single_mechanic(
    conn: sqlite3.Connection,
    mechanic: str,
    boss: dict,
    player: dict,
    result: dict,
    combat_round: int,
) -> None:
    """Apply a single mechanic's effect."""
    hp_ratio = boss["hp"] / boss["hp_max"] if boss["hp_max"] > 0 else 0

    if mechanic == "armored":
        # Half damage until boss below 50% HP
        if hp_ratio > 0.5:
            result["damage"] = max(1, result["damage"] // 2)
            result["messages"].append("Armor absorbs half the blow.")

    elif mechanic == "enraged":
        # Double damage below 50% HP, takes 25% more
        if hp_ratio <= 0.5:
            result["extra_damage_to_player"] += player.get("hp_max", 50) // 10
            result["damage"] = int(result["damage"] * 1.25)
            result["messages"].append("It rages! Hits harder but more reckless.")

    elif mechanic == "regenerator":
        # 10% heal between sessions — lazy eval on engagement
        # Applied via apply_boss_regen() called before combat
        pass

    elif mechanic == "stalwart":
        # First flee attempt per engagement always fails
        if combat_round <= 1:
            result["flee_blocked"] = True
            result["messages"].append("It blocks the exit!")

    elif mechanic == "warded":
        # +50% DEF until a discovery secret on the same floor is found
        floor = boss.get("floor", conn.execute(
            "SELECT floor FROM rooms WHERE id = ?", (boss.get("room_id", 0),)
        ).fetchone()["floor"] if boss.get("room_id") else 1)
        secrets_found = conn.execute(
            """SELECT COUNT(*) as cnt FROM secrets
               WHERE floor = ? AND discovered_by IS NOT NULL""",
            (floor,),
        ).fetchone()["cnt"]
        if secrets_found == 0:
            result["damage"] = max(1, int(result["damage"] * 0.67))
            result["messages"].append("A ward shields it. Find secrets on this floor.")

    elif mechanic == "phasing":
        # Immune on even-numbered epoch days
        epoch = conn.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
        if epoch and epoch["day_number"] % 2 == 0:
            result["boss_immune"] = True
            result["damage"] = 0
            result["messages"].append("It phases out of reality. Try tomorrow.")

    elif mechanic == "draining":
        # Steals 10% of damage dealt as HP from attacker
        drain = max(1, result["damage"] // 10)
        result["extra_damage_to_player"] += drain
        result["messages"].append(f"It drains {drain}HP from you!")

    elif mechanic == "splitting":
        # At 50% HP, split into two — handled in post-combat
        if hp_ratio <= 0.5 and hp_ratio > 0:
            result["messages"].append("It shudders and begins to split!")

    elif mechanic == "rotating_resistance":
        # Immune to highest stat used last session
        # Track via a simple approach: check player's highest stat
        stats = {"pow": player.get("pow", 0), "def": player.get("def", 0), "spd": player.get("spd", 0)}
        highest = max(stats, key=stats.get)
        if highest == "pow":
            result["damage"] = max(1, result["damage"] // 3)
            result["messages"].append("It resists physical attacks!")

    elif mechanic == "retaliator":
        # Reflects 20% of damage back to attacker
        reflected = max(1, result["damage"] // 5)
        result["extra_damage_to_player"] += reflected
        result["messages"].append(f"It reflects {reflected} damage back!")

    elif mechanic == "summoner":
        # Spawns add — handled by checking if add exists before allowing boss damage
        # If add is alive, boss is immune
        add_alive = conn.execute(
            """SELECT COUNT(*) as cnt FROM monsters
               WHERE room_id = ? AND is_floor_boss = 0 AND hp > 0
               AND name LIKE '%Minion%'""",
            (boss.get("room_id", 0),),
        ).fetchone()["cnt"]
        if add_alive > 0:
            result["boss_immune"] = True
            result["damage"] = 0
            result["messages"].append("Kill the minion first!")

    elif mechanic == "cursed":
        # Player who dealt most damage last session gets debuff — tracked post-combat
        pass


def apply_boss_regen(conn: sqlite3.Connection, monster_id: int) -> int:
    """Apply lazy-evaluated regen to a floor boss.

    Called when engaging the boss. Returns HP healed.
    """
    boss = conn.execute(
        "SELECT * FROM monsters WHERE id = ? AND is_floor_boss = 1",
        (monster_id,),
    ).fetchone()

    if not boss or boss["hp"] >= boss["hp_max"] or boss["hp"] <= 0:
        return 0

    mechanic_raw = boss["mechanic"] if boss["mechanic"] else ""
    try:
        mechanics = json.loads(mechanic_raw)
        if isinstance(mechanics, str):
            mechanics = [mechanics]
    except (json.JSONDecodeError, TypeError):
        mechanics = [mechanic_raw] if mechanic_raw else []

    # Regenerator mechanic: 10% between sessions
    if "regenerator" in mechanics:
        regen = max(1, boss["hp_max"] // 10)
        new_hp = min(boss["hp_max"], boss["hp"] + regen)
        if new_hp != boss["hp"]:
            conn.execute(
                "UPDATE monsters SET hp = ? WHERE id = ?", (new_hp, monster_id)
            )
            conn.commit()
            return new_hp - boss["hp"]

    # Standard Warden regen (3%/8h) — lazy eval
    # For simplicity, apply a flat rate per engagement
    regen = max(1, int(boss["hp_max"] * WARDEN_REGEN_RATE))
    new_hp = min(boss["hp_max"], boss["hp"] + regen)
    if new_hp != boss["hp"]:
        conn.execute(
            "UPDATE monsters SET hp = ? WHERE id = ?", (new_hp, monster_id)
        )
        conn.commit()
        return new_hp - boss["hp"]

    return 0


def spawn_boss_add(conn: sqlite3.Connection, boss: dict) -> Optional[int]:
    """Spawn a summoner minion in the boss room.

    Returns monster ID or None.
    """
    room_id = boss.get("room_id", 0)
    floor = conn.execute(
        "SELECT floor FROM rooms WHERE id = ?", (room_id,)
    ).fetchone()
    f = floor["floor"] if floor else 1

    hp = 10 + f * 5
    cursor = conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (room_id, f"Boss Minion", hp, hp, 2 + f, 1 + f, 2,
         3 + f, f, f * 2, min(f, 5)),
    )
    conn.commit()
    return cursor.lastrowid


def check_warden_kill(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check if the Warden (floor 4 boss) has been killed.

    Returns:
        (killed, message)
    """
    warden = conn.execute(
        """SELECT * FROM monsters
           WHERE is_floor_boss = 1 AND hp <= 0
           AND room_id IN (SELECT id FROM rooms WHERE floor = ?)""",
        (NUM_FLOORS,),
    ).fetchone()

    if warden:
        msg = "The Warden has fallen! The Darkcragg Depths are conquered!"
        broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
        conn.commit()
        return True, msg

    return False, ""


def handle_splitting(conn: sqlite3.Connection, boss: dict) -> Optional[str]:
    """Handle the splitting mechanic when boss hits 50% HP.

    Creates a second half-HP monster in an adjacent room.

    Returns message or None.
    """
    if boss["hp"] > boss["hp_max"] // 2 or boss["hp"] <= 0:
        return None

    mechanic_raw = boss.get("mechanic", "")
    try:
        mechanics = json.loads(mechanic_raw)
        if isinstance(mechanics, str):
            mechanics = [mechanics]
    except (json.JSONDecodeError, TypeError):
        mechanics = [mechanic_raw] if mechanic_raw else []

    if "splitting" not in mechanics:
        return None

    # Check if already split (look for a split copy)
    existing_split = conn.execute(
        """SELECT COUNT(*) as cnt FROM monsters
           WHERE name LIKE ? AND is_floor_boss = 1 AND id != ?""",
        (f"%{boss['name']}%", boss["id"]),
    ).fetchone()
    if existing_split["cnt"] > 0:
        return None

    # Find adjacent room
    adj = conn.execute(
        "SELECT to_room_id FROM room_exits WHERE from_room_id = ? LIMIT 1",
        (boss["room_id"],),
    ).fetchone()
    if not adj:
        return None

    half_hp = boss["hp_max"] // 2
    conn.execute(
        """INSERT INTO monsters (room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier,
           is_floor_boss, mechanic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)""",
        (adj["to_room_id"], f"{boss['name']} (Split)",
         half_hp, half_hp,
         boss["pow"], boss["def"], boss["spd"],
         boss["xp_reward"] // 2,
         boss["gold_reward_min"] // 2, boss["gold_reward_max"] // 2,
         boss["tier"]),
    )
    conn.commit()
    return f"{boss['name']} splits! A second form appears nearby."
