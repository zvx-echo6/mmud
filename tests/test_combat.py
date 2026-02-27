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
    print("All combat tests passed!")
