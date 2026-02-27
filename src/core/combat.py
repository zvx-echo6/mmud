"""
Combat resolution for MMUD.
Turn-based auto-resolution. One round per 'fight' command.
SPD-based initiative. POW vs DEF damage calculation.
"""

import math
import random
from dataclasses import dataclass
from typing import Optional


@dataclass
class CombatResult:
    """Result of one combat round."""
    player_damage_dealt: int     # Damage player dealt to monster
    monster_damage_dealt: int    # Damage monster dealt to player
    player_went_first: bool      # True if player had initiative
    player_hp: int               # Player HP after round
    monster_hp: int              # Monster HP after round
    monster_dead: bool           # True if monster HP <= 0
    player_dead: bool            # True if player HP <= 0
    narrative: str               # Combat narrative text


@dataclass
class FleeResult:
    """Result of a flee attempt."""
    success: bool
    damage_taken: int        # Damage taken on failed flee
    player_hp: int           # Player HP after attempt
    player_dead: bool        # True if player died during flee
    narrative: str


def calc_damage(attacker_pow: int, defender_def: int) -> int:
    """Calculate damage from one attack.

    Base damage = attacker POW - defender DEF/3, minimum 1.
    Random variance of +/- 20%.

    Args:
        attacker_pow: Attacker's POW stat.
        defender_def: Defender's DEF stat.

    Returns:
        Damage dealt (always >= 1).
    """
    base = max(1, attacker_pow - defender_def // 3)
    variance = random.uniform(0.8, 1.2)
    return max(1, math.floor(base * variance))


def check_initiative(player_spd: int, monster_spd: int) -> bool:
    """Determine if the player goes first.

    Higher SPD has better odds. Equal SPD = 50/50.

    Args:
        player_spd: Player's SPD stat.
        monster_spd: Monster's SPD stat.

    Returns:
        True if player has initiative.
    """
    total = player_spd + monster_spd
    if total == 0:
        return random.random() < 0.5
    return random.random() < (player_spd / total)


def resolve_round(
    player_pow: int, player_def: int, player_spd: int, player_hp: int,
    monster_pow: int, monster_def: int, monster_spd: int, monster_hp: int,
    monster_name: str,
) -> CombatResult:
    """Resolve one round of combat.

    Both sides attack once. Initiative determines who goes first.
    If the first attacker kills, the second doesn't get to attack.

    Args:
        player_*: Player stats and current HP.
        monster_*: Monster stats, current HP, and name.

    Returns:
        CombatResult with damage, HP changes, and narrative.
    """
    player_first = check_initiative(player_spd, monster_spd)

    player_dmg = calc_damage(player_pow, monster_def)
    monster_dmg = calc_damage(monster_pow, player_def)

    p_hp = player_hp
    m_hp = monster_hp

    if player_first:
        m_hp = max(0, m_hp - player_dmg)
        if m_hp > 0:
            p_hp = max(0, p_hp - monster_dmg)
        else:
            monster_dmg = 0
        narrative = (
            f"You strike {monster_name} for {player_dmg}!"
            + (f" It hits back for {monster_dmg}." if monster_dmg > 0 else " It falls!")
        )
    else:
        p_hp = max(0, p_hp - monster_dmg)
        if p_hp > 0:
            m_hp = max(0, m_hp - player_dmg)
        else:
            player_dmg = 0
        narrative = (
            f"{monster_name} strikes first for {monster_dmg}!"
            + (f" You hit back for {player_dmg}." if player_dmg > 0 else " You fall!")
        )

    return CombatResult(
        player_damage_dealt=player_dmg,
        monster_damage_dealt=monster_dmg,
        player_went_first=player_first,
        player_hp=p_hp,
        monster_hp=m_hp,
        monster_dead=m_hp <= 0,
        player_dead=p_hp <= 0,
        narrative=narrative,
    )


def attempt_flee(
    player_spd: int, player_hp: int,
    monster_pow: int, player_def: int,
    monster_name: str,
    base_chance: float = 0.6,
) -> FleeResult:
    """Attempt to flee from combat.

    Chance = base_chance + (player_spd - 3) * 0.05, clamped to [0.2, 0.95].
    On failure, the monster gets a free hit.

    Args:
        player_spd: Player's SPD stat.
        player_hp: Player's current HP.
        monster_pow: Monster's POW stat.
        player_def: Player's DEF stat.
        monster_name: Monster name for narrative.
        base_chance: Base flee probability.

    Returns:
        FleeResult with success/failure and any damage.
    """
    chance = base_chance + (player_spd - 3) * 0.05
    chance = max(0.2, min(0.95, chance))

    if random.random() < chance:
        return FleeResult(
            success=True,
            damage_taken=0,
            player_hp=player_hp,
            player_dead=False,
            narrative=f"You escape from {monster_name}!",
        )

    # Failed flee — take a hit (can't kill; player survives at 1 HP)
    damage = calc_damage(monster_pow, player_def)
    new_hp = max(1, player_hp - damage)

    return FleeResult(
        success=False,
        damage_taken=damage,
        player_hp=new_hp,
        player_dead=new_hp <= 0,
        narrative=f"Can't escape! {monster_name} hits you for {damage}!",
    )
