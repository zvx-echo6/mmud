"""
Raid Boss — Endgame mode runtime logic.

Massive shared HP pool boss on floor 3-4. Entire server chips away.
HP scales to active player count. 2-3 rolled mechanics with phase scaling.
3 phases always present (100-66%, 66-33%, 33-0%).
"""

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import (
    ACTIVE_PLAYER_WINDOW_DAYS,
    MSG_CHAR_LIMIT,
    NUM_FLOORS,
    RAID_BOSS_HP_CAP,
    RAID_BOSS_HP_PER_PLAYER,
    RAID_BOSS_PHASES,
    RAID_BOSS_REGEN_INTERVAL_HOURS,
    RAID_BOSS_REGEN_RATE,
)
from src.systems import broadcast as broadcast_sys


# ── Activation ─────────────────────────────────────────────────────────────


def activate_raid_boss(conn: sqlite3.Connection) -> dict:
    """Activate the raid boss with HP scaled to active players.

    Called on epoch start when mode is raid_boss.
    Active player = entered dungeon in first 3 days.

    Returns:
        Activation stats.
    """
    # Count active players
    active = conn.execute(
        """SELECT COUNT(DISTINCT p.id) as cnt FROM players p
           WHERE p.floor > 0 OR p.state = 'dungeon'"""
    ).fetchone()["cnt"]

    # Fallback: if called early, use all players
    if active == 0:
        active = conn.execute(
            "SELECT COUNT(*) as cnt FROM players"
        ).fetchone()["cnt"]
    active = max(1, active)

    hp = min(RAID_BOSS_HP_PER_PLAYER * active, RAID_BOSS_HP_CAP)

    # Update the pre-generated raid boss row
    conn.execute(
        """UPDATE raid_boss SET
           hp = ?, hp_max = ?,
           last_regen_at = ?,
           phase = 1
           WHERE id = 1""",
        (hp, hp, datetime.now(timezone.utc).isoformat()),
    )

    boss = get_raid_boss(conn)
    if boss:
        name = boss["name"]
        msg = f"The {name} stirs on Floor {boss['floor']}. HP: {hp}. The hunt begins."
        broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])

    conn.commit()
    return {"hp": hp, "active_players": active}


def get_raid_boss(conn: sqlite3.Connection) -> Optional[dict]:
    """Get current raid boss state."""
    row = conn.execute("SELECT * FROM raid_boss WHERE id = 1").fetchone()
    if not row:
        return None
    result = dict(row)
    # Parse mechanics
    try:
        result["mechanics_list"] = json.loads(result["mechanics"])
    except (json.JSONDecodeError, TypeError):
        result["mechanics_list"] = []
    return result


# ── Regen ──────────────────────────────────────────────────────────────────


def apply_raid_regen(conn: sqlite3.Connection) -> int:
    """Apply lazy-evaluated regen to raid boss.

    Called on engagement or periodically. Returns HP healed.
    """
    boss = get_raid_boss(conn)
    if not boss or boss["hp"] >= boss["hp_max"] or boss["hp"] <= 0:
        return 0

    regen_rate = boss["regen_rate"]

    # Check for extra_regen mechanic
    if "extra_regen" in boss.get("mechanics_list", []):
        regen_rate = 0.05  # 5%/8h instead of 3%

    last_regen = boss.get("last_regen_at")
    now = datetime.now(timezone.utc)

    if not last_regen:
        conn.execute(
            "UPDATE raid_boss SET last_regen_at = ? WHERE id = 1",
            (now.isoformat(),),
        )
        conn.commit()
        return 0

    try:
        if isinstance(last_regen, str):
            dt = datetime.fromisoformat(last_regen)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = last_regen
    except (ValueError, TypeError):
        conn.execute(
            "UPDATE raid_boss SET last_regen_at = ? WHERE id = 1",
            (now.isoformat(),),
        )
        conn.commit()
        return 0

    hours = (now - dt).total_seconds() / 3600
    intervals = int(hours / RAID_BOSS_REGEN_INTERVAL_HOURS)

    if intervals <= 0:
        return 0

    regen_per = math.ceil(boss["hp_max"] * regen_rate)
    total = regen_per * intervals
    new_hp = min(boss["hp_max"], boss["hp"] + total)

    conn.execute(
        "UPDATE raid_boss SET hp = ?, last_regen_at = ? WHERE id = 1",
        (new_hp, now.isoformat()),
    )
    conn.commit()
    return new_hp - boss["hp"]


def apply_regen_burst(conn: sqlite3.Connection) -> int:
    """Apply regen_burst mechanic: 15% max HP heal once per day.

    Returns HP healed (0 if not applicable or already triggered today).
    """
    boss = get_raid_boss(conn)
    if not boss or "regen_burst" not in boss.get("mechanics_list", []):
        return 0

    if boss["hp"] <= 0 or boss["hp"] >= boss["hp_max"]:
        return 0

    now = datetime.now(timezone.utc)
    last_burst = boss.get("last_burst_at")

    if last_burst:
        try:
            dt = datetime.fromisoformat(last_burst) if isinstance(last_burst, str) else last_burst
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if (now - dt).total_seconds() < 86400:  # Less than 24h
                return 0
        except (ValueError, TypeError):
            pass

    heal = math.ceil(boss["hp_max"] * 0.15)
    new_hp = min(boss["hp_max"], boss["hp"] + heal)

    conn.execute(
        "UPDATE raid_boss SET hp = ?, last_burst_at = ? WHERE id = 1",
        (new_hp, now.isoformat()),
    )

    msg = f"The {boss['name']} surges with energy! It heals significantly."
    broadcast_sys.create_broadcast(conn, 2, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return new_hp - boss["hp"]


# ── Combat ─────────────────────────────────────────────────────────────────


def engage_raid_boss(
    conn: sqlite3.Connection, player_id: int
) -> tuple[bool, str]:
    """Check if a player can engage the raid boss.

    Returns:
        (can_engage, reason)
    """
    boss = get_raid_boss(conn)
    if not boss or boss["hp"] <= 0:
        return False, "The raid boss has been defeated."

    # Check lockout mechanic
    if "lockout" in boss.get("mechanics_list", []):
        contrib = conn.execute(
            "SELECT lockout_until FROM raid_boss_contributors WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        if contrib and contrib["lockout_until"]:
            try:
                lockout = datetime.fromisoformat(contrib["lockout_until"])
                if lockout.tzinfo is None:
                    lockout = lockout.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < lockout:
                    return False, "You're locked out. Return tomorrow."
            except (ValueError, TypeError):
                pass

    return True, "The raid boss awaits."


def apply_raid_mechanic(
    conn: sqlite3.Connection,
    boss: dict,
    player: dict,
    damage: int,
    combat_round: int = 1,
) -> dict:
    """Apply raid boss mechanic modifications to combat.

    Returns:
        Dict with modified values.
    """
    result = {
        "damage": damage,
        "extra_damage_to_player": 0,
        "messages": [],
        "flee_blocked": False,
        "boss_immune": False,
    }

    mechanics = boss.get("mechanics_list", [])
    phase = boss.get("phase", 1)

    for mechanic in mechanics:
        _apply_raid_mechanic(conn, mechanic, boss, player, result, combat_round, phase)

    return result


def _apply_raid_mechanic(
    conn: sqlite3.Connection,
    mechanic: str,
    boss: dict,
    player: dict,
    result: dict,
    combat_round: int,
    phase: int,
) -> None:
    """Apply a single raid boss mechanic."""
    hp_ratio = boss["hp"] / boss["hp_max"] if boss["hp_max"] > 0 else 0

    if mechanic == "windup_strike":
        # Every Nth round: triple damage next round unless defend/dodge
        interval = max(2, 4 - phase)  # Phase 1: every 3rd, Phase 2: every 2nd, Phase 3: every 2nd
        if combat_round % interval == 0:
            result["messages"].append("WIND-UP! Use DEFEND or DODGE next round!")
        if (combat_round - 1) % interval == 0 and combat_round > 1:
            result["extra_damage_to_player"] += player.get("hp_max", 50) // 3
            result["messages"].append("The wind-up connects!")

    elif mechanic == "flat_damage_boost":
        mult = 1.5 + (phase - 1) * 0.25  # 1.5x, 1.75x, 2.0x
        result["extra_damage_to_player"] += int(player.get("hp_max", 50) * 0.05 * mult)

    elif mechanic == "retribution":
        # Burst at HP thresholds
        thresholds = [0.75, 0.50, 0.25]
        for t in thresholds:
            threshold_hp = int(boss["hp_max"] * t)
            if boss["hp"] <= threshold_hp and boss["hp"] + result["damage"] > threshold_hp:
                burst = int(player.get("hp_max", 50) * 0.3 * phase)
                result["extra_damage_to_player"] += burst
                result["messages"].append(f"RETRIBUTION! It unleashes {burst} damage!")
                break

    elif mechanic == "aura_damage":
        aura = max(1, int(player.get("hp_max", 50) * 0.05 * phase))
        result["extra_damage_to_player"] += aura
        result["messages"].append(f"Its aura burns for {aura}.")

    elif mechanic == "armor_phase":
        # Half damage until condition met
        contributors = conn.execute(
            "SELECT COUNT(*) as cnt FROM raid_boss_contributors WHERE total_damage > 0"
        ).fetchone()["cnt"]
        secrets = conn.execute(
            """SELECT COUNT(*) as cnt FROM secrets
               WHERE floor = ? AND discovered_by IS NOT NULL""",
            (boss.get("floor", NUM_FLOORS),),
        ).fetchone()["cnt"]
        if contributors < 5 and secrets == 0:
            result["damage"] = max(1, result["damage"] // 2)
            result["messages"].append("Armor holds! Need 5 fighters or a floor secret.")

    elif mechanic == "boss_flees":
        thresholds = [0.75, 0.50, 0.25]
        for t in thresholds:
            threshold_hp = int(boss["hp_max"] * t)
            if boss["hp"] <= threshold_hp and boss["hp"] + result["damage"] > threshold_hp:
                result["messages"].append("BOSS FLEES! It relocates on this floor!")
                break

    elif mechanic == "no_escape":
        if hp_ratio <= 0.25:
            result["flee_blocked"] = True
            result["messages"].append("NO ESCAPE! Fight to the death!")

    elif mechanic == "summoner":
        adds = phase  # 1 add phase 1, 2 phase 2, 3 phase 3
        existing = conn.execute(
            """SELECT COUNT(*) as cnt FROM monsters
               WHERE room_id = ? AND is_floor_boss = 0 AND hp > 0
               AND name LIKE '%Raid Add%'""",
            (boss.get("room_id", 0),),
        ).fetchone()["cnt"]
        if existing < adds:
            result["messages"].append(f"It summons reinforcements! ({adds - existing} adds)")

    elif mechanic == "lockout":
        # Applied post-combat via record_contribution
        pass

    elif mechanic == "enrage_timer":
        threshold = max(3, 6 - phase)  # Phase 1: 5 rounds, Phase 2: 4, Phase 3: 3
        if combat_round > threshold:
            mult = 2 ** (combat_round - threshold)
            result["extra_damage_to_player"] += int(player.get("hp_max", 50) * 0.1 * mult)
            result["messages"].append(f"ENRAGED! Damage x{mult}!")


# ── Contribution Tracking ──────────────────────────────────────────────────


def record_raid_contribution(
    conn: sqlite3.Connection, player_id: int, damage: int
) -> None:
    """Record a player's damage against the raid boss."""
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO raid_boss_contributors (player_id, total_damage, last_engaged_at)
           VALUES (?, ?, ?)
           ON CONFLICT(player_id)
           DO UPDATE SET total_damage = total_damage + ?,
                         last_engaged_at = ?""",
        (player_id, damage, now, damage, now),
    )

    # Apply lockout if mechanic active
    boss = get_raid_boss(conn)
    if boss and "lockout" in boss.get("mechanics_list", []):
        lockout_until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        conn.execute(
            """UPDATE raid_boss_contributors SET lockout_until = ?
               WHERE player_id = ?""",
            (lockout_until, player_id),
        )

    conn.commit()


# ── Phase Management ───────────────────────────────────────────────────────


def check_phase_transition(conn: sqlite3.Connection) -> Optional[int]:
    """Check if the raid boss should transition to a new phase.

    Returns new phase number or None.
    """
    boss = get_raid_boss(conn)
    if not boss or boss["hp"] <= 0:
        return None

    hp_ratio = boss["hp"] / boss["hp_max"] if boss["hp_max"] > 0 else 0
    current_phase = boss["phase"]

    new_phase = current_phase
    for i, threshold in enumerate(RAID_BOSS_PHASES):
        if hp_ratio <= threshold and current_phase <= i + 1:
            new_phase = i + 2  # Phase 2 at first threshold, phase 3 at second

    if new_phase > current_phase:
        conn.execute(
            "UPDATE raid_boss SET phase = ? WHERE id = 1", (new_phase,)
        )

        msg = f"The {boss['name']} enters phase {new_phase}! It grows stronger."
        broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
        conn.commit()
        return new_phase

    return None


def handle_boss_flees(conn: sqlite3.Connection) -> Optional[str]:
    """Relocate the raid boss to a random room on the same floor.

    Returns broadcast message or None.
    """
    boss = get_raid_boss(conn)
    if not boss:
        return None

    floor = boss["floor"]
    new_room = conn.execute(
        """SELECT id FROM rooms
           WHERE floor = ? AND id != ? AND is_hub = 0 AND is_breach = 0
           ORDER BY RANDOM() LIMIT 1""",
        (floor, boss["room_id"]),
    ).fetchone()

    if not new_room:
        return None

    conn.execute(
        "UPDATE raid_boss SET room_id = ? WHERE id = 1",
        (new_room["id"],),
    )

    msg = f"The {boss['name']} fled to somewhere on Floor {floor}!"
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return msg


# ── Completion ─────────────────────────────────────────────────────────────


def check_raid_boss_dead(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check if the raid boss has been killed.

    Returns:
        (dead, message)
    """
    boss = get_raid_boss(conn)
    if not boss or boss["hp"] > 0:
        return False, ""

    msg = f"The {boss['name']} has been slain! Victory belongs to the Darkcragg!"
    broadcast_sys.create_broadcast(conn, 1, msg[:MSG_CHAR_LIMIT])
    conn.commit()
    return True, msg


def deal_damage_to_boss(
    conn: sqlite3.Connection, damage: int
) -> int:
    """Apply damage to the raid boss. Returns new HP."""
    boss = get_raid_boss(conn)
    if not boss:
        return 0

    new_hp = max(0, boss["hp"] - damage)
    conn.execute("UPDATE raid_boss SET hp = ? WHERE id = 1", (new_hp,))
    conn.commit()
    return new_hp


def format_raid_status(conn: sqlite3.Connection) -> str:
    """Format raid boss status for display."""
    boss = get_raid_boss(conn)
    if not boss:
        return "No raid boss active."

    if boss["hp"] <= 0:
        return f"The {boss['name']} has been defeated!"

    pct = boss["hp"] / boss["hp_max"] * 100 if boss["hp_max"] > 0 else 0
    mechanics_known = len(boss.get("mechanics_list", []))

    contrib = conn.execute(
        "SELECT COUNT(*) as cnt FROM raid_boss_contributors WHERE total_damage > 0"
    ).fetchone()["cnt"]

    return (
        f"{boss['name']} HP:{boss['hp']}/{boss['hp_max']} "
        f"P{boss['phase']} {pct:.0f}% {contrib} fighters"
    )
