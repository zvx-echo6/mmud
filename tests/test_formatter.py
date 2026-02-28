"""Tests for the response formatter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import MSG_CHAR_LIMIT
from src.transport.formatter import (
    fmt,
    fmt_combat_status,
    fmt_death,
    fmt_multi,
    fmt_room,
    fmt_stats,
)


def test_short_message_unchanged():
    msg = "Hello world"
    assert fmt(msg) == msg
    assert len(fmt(msg)) <= MSG_CHAR_LIMIT


def test_truncation():
    msg = "A" * 200
    result = fmt(msg)
    assert len(result) == MSG_CHAR_LIMIT
    assert result.endswith("...")


def test_exact_limit():
    msg = "A" * MSG_CHAR_LIMIT
    assert fmt(msg) == msg
    assert len(fmt(msg)) == MSG_CHAR_LIMIT


def test_one_over_limit():
    msg = "A" * (MSG_CHAR_LIMIT + 1)
    result = fmt(msg)
    assert len(result) == MSG_CHAR_LIMIT
    assert result.endswith("...")


def test_fmt_multi_short():
    msg = "Short message"
    result = fmt_multi(msg)
    assert result == [msg]


def test_fmt_multi_split():
    # Build a message that needs splitting
    msg = " ".join(["word"] * 50)  # ~250 chars
    result = fmt_multi(msg)
    assert len(result) > 1
    for part in result:
        assert len(part) <= MSG_CHAR_LIMIT


def test_fmt_room():
    result = fmt_room("Sunken Hall", "Water drips from stone.", ["n", "s", "e"])
    assert "Sunken Hall" in result
    assert "[n,s,e]" in result
    assert len(result) <= MSG_CHAR_LIMIT


def test_fmt_room_truncation():
    long_desc = "A" * 200
    result = fmt_room("Room", long_desc, ["n"])
    assert len(result) <= MSG_CHAR_LIMIT
    assert result.endswith("...")


def test_fmt_combat_status():
    result = fmt_combat_status(38, 60, "Orc", 42, 50)
    assert "HP:38/60" in result
    assert "Orc" in result
    assert "F)ight" in result
    assert "FL)ee" in result
    assert len(result) <= MSG_CHAR_LIMIT


def test_fmt_combat_status_with_class():
    result = fmt_combat_status(38, 60, "Orc", 42, 50, player_class="warrior")
    assert "CH)rg" in result
    assert "F)ight" in result
    assert "FL)ee" in result
    assert len(result) <= MSG_CHAR_LIMIT

    result = fmt_combat_status(38, 60, "Orc", 42, 50, player_class="rogue")
    assert "SN)eak" in result
    assert len(result) <= MSG_CHAR_LIMIT

    result = fmt_combat_status(38, 60, "Orc", 42, 50, player_class="caster")
    assert "CA)st" in result
    assert len(result) <= MSG_CHAR_LIMIT


def test_fmt_death():
    result = fmt_death(100, 50)
    assert "100g" in result
    assert "50xp" in result
    assert len(result) <= MSG_CHAR_LIMIT


def test_fmt_stats():
    result = fmt_stats(
        name="Kael", cls="warrior", level=5,
        hp=38, hp_max=60,
        pow_=8, def_=6, spd=4,
        gold=150, xp=2500, actions=8,
    )
    assert "Kael" in result
    assert "Lv5" in result
    assert "warrior" in result
    assert len(result) <= MSG_CHAR_LIMIT


if __name__ == "__main__":
    test_short_message_unchanged()
    test_truncation()
    test_exact_limit()
    test_one_over_limit()
    test_fmt_multi_short()
    test_fmt_multi_split()
    test_fmt_room()
    test_fmt_room_truncation()
    test_fmt_combat_status()
    test_fmt_combat_status_with_class()
    test_fmt_death()
    test_fmt_stats()
    print("All formatter tests passed!")
