"""Tests for the command parser."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.transport.parser import parse


def test_empty_input():
    assert parse("") is None
    assert parse("   ") is None


def test_direction_shortcuts():
    result = parse("n")
    assert result.command == "move"
    assert result.args == ["n"]

    result = parse("S")
    assert result.command == "move"
    assert result.args == ["s"]

    result = parse("east")
    assert result.command == "move"
    assert result.args == ["e"]


def test_fight_aliases():
    for alias in ("f", "F", "fight", "a", "attack"):
        result = parse(alias)
        assert result.command == "fight", f"'{alias}' should parse as 'fight'"


def test_look_alias():
    result = parse("l")
    assert result.command == "look"

    result = parse("look")
    assert result.command == "look"


def test_stats_alias():
    result = parse("st")
    assert result.command == "stats"


def test_inventory_alias():
    result = parse("i")
    assert result.command == "inventory"

    result = parse("inv")
    assert result.command == "inventory"


def test_help_alias():
    result = parse("h")
    assert result.command == "help"

    result = parse("?")
    assert result.command == "help"


def test_flee():
    result = parse("flee")
    assert result.command == "flee"

    result = parse("run")
    assert result.command == "flee"

    result = parse("fl")
    assert result.command == "flee"

    result = parse("FL")
    assert result.command == "flee"


def test_go_direction():
    result = parse("go north")
    assert result.command == "move"
    assert result.args == ["n"]


def test_examine():
    result = parse("x wall")
    assert result.command == "examine"
    assert result.args == ["wall"]


def test_class_ability_aliases():
    result = parse("ch")
    assert result.command == "charge"

    result = parse("CH")
    assert result.command == "charge"

    result = parse("sn")
    assert result.command == "sneak"

    result = parse("SN")
    assert result.command == "sneak"

    result = parse("ca")
    assert result.command == "cast"

    result = parse("CA")
    assert result.command == "cast"


def test_unknown_command():
    result = parse("dance")
    assert result.command == "dance"
    assert result.args == []


def test_preserves_raw():
    result = parse("go North quickly")
    assert result.raw == "go North quickly"


def test_case_insensitive():
    result = parse("LOOK")
    assert result.command == "look"

    result = parse("Fight")
    assert result.command == "fight"


if __name__ == "__main__":
    test_empty_input()
    test_direction_shortcuts()
    test_fight_aliases()
    test_look_alias()
    test_stats_alias()
    test_inventory_alias()
    test_help_alias()
    test_flee()
    test_class_ability_aliases()
    test_go_direction()
    test_examine()
    test_unknown_command()
    test_preserves_raw()
    test_case_insensitive()
    print("All parser tests passed!")
