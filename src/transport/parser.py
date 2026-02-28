"""
Command parser for MMUD.
Parses 175-char Meshtastic messages into commands and arguments.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedCommand:
    """Result of parsing an inbound message."""
    command: str          # Normalized command name (lowercase)
    args: list[str]       # Remaining arguments
    raw: str              # Original message text


# Single-letter and short aliases → canonical command names
ALIASES = {
    # Movement
    "n": "move", "s": "move", "e": "move", "w": "move",
    "u": "move", "d": "move",
    "north": "move", "south": "move", "east": "move", "west": "move",
    "up": "move", "down": "move",
    "go": "move",
    # Combat
    "f": "fight", "a": "fight", "attack": "fight",
    # Look
    "l": "look",
    # Examine
    "x": "examine", "ex": "examine", "examine": "examine",
    # Stats
    "st": "stats",
    # Inventory
    "i": "inventory", "inv": "inventory",
    # Help
    "h": "help", "?": "help",
    # Flee
    "run": "flee", "fl": "flee",
    # Who
    "w": "who",  # Note: 'w' for west takes priority; 'who' must be typed out
    # Shorthand
    "eq": "equip",
    "uneq": "unequip",
    # Economy
    "dep": "deposit",
    "wd": "withdraw",
    # Social
    "bar": "barkeep",
    "tok": "token",
    "bounties": "bounty",
    "msg": "message",
    # Class abilities
    "charge": "charge", "ch": "charge", "sneak": "sneak", "sn": "sneak", "cast": "cast", "ca": "cast",
    "rest": "rest",
    # Character auth
    "join": "join", "register": "join", "create": "join", "new": "join",
    "login": "login", "auth": "login", "connect": "login",
    "logout": "logout", "disconnect": "logout", "quit": "logout",
    # Leave / Exit
    "exit": "leave", "go back": "leave", "back": "leave",
    # Floor transition aliases
    "descend": "enter", "ascend": "return",
    "warp": "enter", "ft": "enter",
    # Return to town
    "town": "return", "return": "return", "retreat": "return",
    # Town location keywords
    "tavern": "barkeep", "barkeep": "grist", "grist": "grist", "drink": "grist",
    "maren": "healer", "clinic": "healer", "infirmary": "healer",
    "torval": "merchant", "trader": "merchant",
    "whisper": "rumor", "rumor": "rumor", "hint": "rumor",
}

# Direction letters that are also movement commands
DIRECTION_ALIASES = {"n", "s", "e", "w", "u", "d",
                     "north", "south", "east", "west", "up", "down"}

# Map direction words to single-letter direction codes
DIRECTION_MAP = {
    "n": "n", "north": "n",
    "s": "s", "south": "s",
    "e": "e", "east": "e",
    "w": "w", "west": "w",
    "u": "u", "up": "u",
    "d": "d", "down": "d",
}


def parse(text: str) -> Optional[ParsedCommand]:
    """Parse a raw message into a command.

    Args:
        text: Raw message text (up to 175 chars).

    Returns:
        ParsedCommand or None if the message is empty/unparseable.
    """
    text = text.strip()
    if not text:
        return None

    parts = text.split()
    first = parts[0].lower()
    rest = parts[1:]

    # Check if it's a direction shortcut (n/s/e/w/u/d)
    if first in DIRECTION_ALIASES:
        direction = DIRECTION_MAP[first]
        return ParsedCommand(command="move", args=[direction], raw=text)

    # Check aliases
    command = ALIASES.get(first, first)

    # For 'move' via alias (like 'go north'), normalize the direction arg
    if command == "move" and rest:
        dir_word = rest[0].lower()
        if dir_word in DIRECTION_MAP:
            rest[0] = DIRECTION_MAP[dir_word]

    return ParsedCommand(command=command, args=rest, raw=text)
