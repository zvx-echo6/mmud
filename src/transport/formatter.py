"""
Response formatter for MMUD.
Every outbound message MUST fit in 150 characters.
This module is the last gate before send.
"""

from config import MSG_CHAR_LIMIT


def fmt(text: str) -> str:
    """Format a response to fit within the character limit.

    Truncates with '...' if the message exceeds MSG_CHAR_LIMIT.

    Args:
        text: Response text to format.

    Returns:
        Formatted string, guaranteed <= MSG_CHAR_LIMIT chars.
    """
    if len(text) <= MSG_CHAR_LIMIT:
        return text
    return text[:MSG_CHAR_LIMIT - 3] + "..."


def fmt_multi(text: str) -> list[str]:
    """Split a long response into multiple messages.

    Each message is <= MSG_CHAR_LIMIT chars. Splits on word boundaries
    when possible.

    Args:
        text: Response text that may exceed the limit.

    Returns:
        List of strings, each <= MSG_CHAR_LIMIT chars.
    """
    if len(text) <= MSG_CHAR_LIMIT:
        return [text]

    messages = []
    while text:
        if len(text) <= MSG_CHAR_LIMIT:
            messages.append(text)
            break

        # Find the last space within the limit
        cut = text.rfind(" ", 0, MSG_CHAR_LIMIT)
        if cut == -1:
            # No space found — hard cut
            cut = MSG_CHAR_LIMIT
        messages.append(text[:cut].rstrip())
        text = text[cut:].lstrip()

    return messages


def fmt_room(name: str, desc: str, exits: list[str]) -> str:
    """Format a room description.

    Template: {Name}. {Description}. [{Exits}]

    Args:
        name: Room name.
        desc: Room sensory description.
        exits: List of exit directions (e.g., ["n", "s", "e"]).

    Returns:
        Formatted room string, truncated if needed.
    """
    exit_str = ",".join(exits)
    full = f"{name}. {desc} [{exit_str}]"
    return fmt(full)


def fmt_combat_narrative(text: str) -> str:
    """Format a combat narrative line."""
    return fmt(text)


def fmt_combat_status(
    player_hp: int, player_hp_max: int,
    monster_name: str, monster_hp: int, monster_hp_max: int,
) -> str:
    """Format combat status line.

    Template: HP:{current}/{max} vs {Monster}({hp}/{max}) A)tk F)lee

    Args:
        player_hp: Player's current HP.
        player_hp_max: Player's max HP.
        monster_name: Monster name.
        monster_hp: Monster's current HP.
        monster_hp_max: Monster's max HP.

    Returns:
        Formatted status string.
    """
    status = (
        f"HP:{player_hp}/{player_hp_max} "
        f"vs {monster_name}({monster_hp}/{monster_hp_max}) "
        f"A)tk F)lee"
    )
    return fmt(status)


def fmt_death(gold_lost: int, xp_lost: int) -> str:
    """Format death message."""
    return fmt(f"You died! Lost {gold_lost}g and {xp_lost}xp. Respawning in town at 50% HP.")


def fmt_level_up(level: int, stat_points: int = 0) -> str:
    """Format level up message."""
    if stat_points > 0:
        return fmt(f"LEVEL UP! Lv{level}! +{stat_points} stat pts. TRAIN POW/DEF/SPD in town.")
    return fmt(f"LEVEL UP! You are now level {level}!")


def fmt_stats(
    name: str, cls: str, level: int,
    hp: int, hp_max: int,
    pow_: int, def_: int, spd: int,
    gold: int, xp: int, actions: int,
    banked: int = 0, stat_points: int = 0,
) -> str:
    """Format player stats display."""
    base = (
        f"{name} Lv{level} {cls} "
        f"HP:{hp}/{hp_max} "
        f"POW:{pow_} DEF:{def_} SPD:{spd} "
        f"G:{gold}"
    )
    if banked > 0:
        base += f"({banked}bank)"
    base += f" XP:{xp} Acts:{actions}"
    if stat_points > 0:
        base += f" SP:{stat_points}"
    return fmt(base)
