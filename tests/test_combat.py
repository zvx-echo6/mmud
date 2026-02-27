"""Tests for combat resolution."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.combat import attempt_flee, calc_damage, check_initiative, resolve_round


def test_calc_damage_minimum():
    """Damage is always at least 1."""
    random.seed(42)
    # Very low POW vs very high DEF
    for _ in range(100):
        dmg = calc_damage(attacker_pow=1, defender_def=20)
        assert dmg >= 1


def test_calc_damage_scaling():
    """Higher POW should deal more damage on average."""
    random.seed(42)
    low_pow_total = sum(calc_damage(3, 2) for _ in range(1000))
    random.seed(42)
    high_pow_total = sum(calc_damage(10, 2) for _ in range(1000))
    assert high_pow_total > low_pow_total


def test_initiative_faster_wins_more():
    """Higher SPD should win initiative more often."""
    random.seed(42)
    fast_wins = sum(check_initiative(10, 2) for _ in range(10000))
    assert fast_wins > 7000  # Should win ~83% of the time


def test_initiative_equal_is_fair():
    """Equal SPD should be roughly 50/50."""
    random.seed(42)
    wins = sum(check_initiative(5, 5) for _ in range(10000))
    assert 4000 < wins < 6000


def test_resolve_round_monster_dies():
    """When monster HP is very low, it should die."""
    random.seed(42)
    result = resolve_round(
        player_pow=10, player_def=5, player_spd=5, player_hp=50,
        monster_pow=3, monster_def=1, monster_spd=1, monster_hp=1,
        monster_name="Rat",
    )
    assert result.monster_dead
    assert result.monster_hp == 0


def test_resolve_round_player_dies():
    """When player HP is very low against a strong monster."""
    random.seed(42)
    result = resolve_round(
        player_pow=2, player_def=1, player_spd=1, player_hp=1,
        monster_pow=20, monster_def=10, monster_spd=10, monster_hp=100,
        monster_name="Dragon",
    )
    assert result.player_dead
    assert result.player_hp == 0


def test_resolve_round_both_survive():
    """Both sides should survive with enough HP."""
    random.seed(42)
    result = resolve_round(
        player_pow=5, player_def=3, player_spd=3, player_hp=100,
        monster_pow=5, monster_def=3, monster_spd=3, monster_hp=100,
        monster_name="Goblin",
    )
    assert not result.monster_dead
    assert not result.player_dead
    assert result.player_hp < 100
    assert result.monster_hp < 100


def test_resolve_round_has_narrative():
    """Combat result should have narrative text."""
    random.seed(42)
    result = resolve_round(
        player_pow=5, player_def=3, player_spd=3, player_hp=50,
        monster_pow=3, monster_def=2, monster_spd=2, monster_hp=30,
        monster_name="Orc",
    )
    assert result.narrative
    assert len(result.narrative) > 0


def test_flee_success():
    """High SPD should flee easily."""
    random.seed(42)
    successes = 0
    for _ in range(100):
        result = attempt_flee(
            player_spd=10, player_hp=50,
            monster_pow=3, player_def=3,
            monster_name="Rat",
        )
        if result.success:
            successes += 1
            assert result.damage_taken == 0
            assert result.player_hp == 50
    assert successes > 70  # High SPD should flee most of the time


def test_flee_failure_takes_damage():
    """Failed flee should result in damage."""
    # Force failure by seeding
    random.seed(0)
    found_failure = False
    for _ in range(100):
        result = attempt_flee(
            player_spd=1, player_hp=50,
            monster_pow=5, player_def=2,
            monster_name="Troll",
        )
        if not result.success:
            found_failure = True
            assert result.damage_taken > 0
            assert result.player_hp < 50
            break
    assert found_failure, "Should have found at least one flee failure"


def test_flee_cannot_kill():
    """Failed flee at 1 HP should leave player alive at 1 HP (flee can't kill)."""
    random.seed(0)
    found_fail = False
    for _ in range(200):
        result = attempt_flee(
            player_spd=1, player_hp=1,
            monster_pow=10, player_def=1,
            monster_name="Dragon",
        )
        if not result.success:
            found_fail = True
            assert not result.player_dead
            assert result.player_hp == 1
            break
    assert found_fail


def test_calc_damage_minimum_is_2_at_level_1():
    """Level 1 player should always deal at least 2 damage."""
    random.seed(42)
    for _ in range(100):
        dmg = calc_damage(attacker_pow=1, defender_def=20, attacker_level=1)
        assert dmg >= 2, f"Level 1 damage {dmg} < 2"


def test_calc_damage_minimum_scales_with_level():
    """Minimum damage scales: level 1-2: 2, level 6-8: 3, level 9+: 4."""
    random.seed(42)
    for _ in range(100):
        # Level 1: min 2
        assert calc_damage(1, 20, attacker_level=1) >= 2
        # Level 6: min 3 (1 + 6//3 = 3)
        assert calc_damage(1, 20, attacker_level=6) >= 3
        # Level 9: min 4 (1 + 9//3 = 4)
        assert calc_damage(1, 20, attacker_level=9) >= 4


def test_calc_damage_monster_min_stays_at_base():
    """Monster attacks (level 0) should have minimum 2 damage."""
    random.seed(42)
    for _ in range(100):
        dmg = calc_damage(attacker_pow=1, defender_def=20, attacker_level=0)
        assert dmg >= 2


def test_rogue_vs_tier1_always_2_plus():
    """Rogue (POW 2) vs tier 1 monster (DEF 1-3) should deal >= 2 damage."""
    random.seed(42)
    for def_val in [1, 2, 3]:
        for _ in range(100):
            dmg = calc_damage(attacker_pow=2, defender_def=def_val, attacker_level=1)
            assert dmg >= 2, f"Rogue vs DEF {def_val}: {dmg} < 2"


def test_warrior_vs_tier1_always_2_plus():
    """Warrior (POW 3) vs tier 1 monster (DEF 1-3) should deal >= 2 damage."""
    random.seed(42)
    for def_val in [1, 2, 3]:
        for _ in range(100):
            dmg = calc_damage(attacker_pow=3, defender_def=def_val, attacker_level=1)
            assert dmg >= 2, f"Warrior vs DEF {def_val}: {dmg} < 2"


def test_caster_vs_tier1_always_2_plus():
    """Caster (POW 1) vs tier 1 monster (DEF 1-3) should deal >= 2 damage."""
    random.seed(42)
    for def_val in [1, 2, 3]:
        for _ in range(100):
            dmg = calc_damage(attacker_pow=1, defender_def=def_val, attacker_level=1)
            assert dmg >= 2, f"Caster vs DEF {def_val}: {dmg} < 2"


def test_rogue_survives_tier1_fight():
    """Level 1 Rogue should survive most tier 1 fights (>80% survival over 100 sims)."""
    random.seed(42)
    survivals = 0
    for _ in range(100):
        p_hp = 40   # Rogue starting HP
        m_hp = 8    # Tier 1 monster HP (new base)
        for _round in range(30):  # Cap at 30 rounds
            result = resolve_round(
                player_pow=2, player_def=1, player_spd=3, player_hp=p_hp,
                monster_pow=2, monster_def=2, monster_spd=2, monster_hp=m_hp,
                monster_name="Rat", player_level=1,
            )
            p_hp = result.player_hp
            m_hp = result.monster_hp
            if result.monster_dead:
                survivals += 1
                break
            if result.player_dead:
                break
    assert survivals > 80, f"Rogue survived {survivals}/100 fights (need >80)"


if __name__ == "__main__":
    test_calc_damage_minimum()
    test_calc_damage_scaling()
    test_initiative_faster_wins_more()
    test_initiative_equal_is_fair()
    test_resolve_round_monster_dies()
    test_resolve_round_player_dies()
    test_resolve_round_both_survive()
    test_resolve_round_has_narrative()
    test_flee_success()
    test_flee_failure_takes_damage()
    test_flee_cannot_kill()
    test_calc_damage_minimum_is_2_at_level_1()
    test_calc_damage_minimum_scales_with_level()
    test_calc_damage_monster_min_stays_at_base()
    test_rogue_vs_tier1_always_2_plus()
    test_warrior_vs_tier1_always_2_plus()
    test_caster_vs_tier1_always_2_plus()
    test_rogue_survives_tier1_fight()
    print("All combat tests passed!")
