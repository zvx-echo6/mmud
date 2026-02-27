"""Tests for LLM backend pluggability.

Verifies that:
- All pipeline methods exist on BackendInterface
- A minimal complete()-only backend inherits all methods
- DummyBackend regression (still works)
- Base class fallback on error
- Base class parses valid LLM output
- Output character limits enforced
- Factory functions return correct types
- ValidationLayer wraps any backend
- Full epoch generation with DummyBackend (integration)
"""

import sqlite3

from config import BROADCAST_CHAR_LIMIT, LLM_OUTPUT_CHAR_LIMIT
from src.db.database import init_schema
from src.generation.narrative import (
    BackendInterface,
    DummyBackend,
    GoogleBackend,
    ValidationLayer,
    _backend_from_config,
    get_backend,
)
from src.generation.themegen import generate_floor_themes, get_floor_themes
from src.generation.worldgen import generate_town, generate_world
from src.models.epoch import create_epoch


# ── Helpers ───────────────────────────────────────────────────────────────


PIPELINE_METHODS = [
    "generate_room_name",
    "generate_room_description",
    "generate_room_description_short",
    "generate_monster_name",
    "generate_bounty_description",
    "generate_boss_name",
    "generate_hint",
    "generate_riddle",
    "generate_npc_dialogue",
    "generate_breach_name",
    "generate_narrative_skin",
    "generate_atmospheric_broadcast",
    "generate_epoch_announcements",
    "generate_epoch_preamble",
]


class MinimalBackend(BackendInterface):
    """Backend that only implements complete(). Tests inheritance of all other methods."""

    def complete(self, prompt: str) -> str:
        return "test output"


class BrokenBackend(BackendInterface):
    """Backend whose complete() always raises. Tests fallback behavior."""

    def complete(self, prompt: str) -> str:
        raise RuntimeError("API down")


class EchoBackend(BackendInterface):
    """Backend that returns a fixed string. Tests parsing of valid LLM output."""

    def __init__(self, response: str = "Crystal Cavern"):
        self._response = response

    def complete(self, prompt: str) -> str:
        return self._response


class VerboseBackend(BackendInterface):
    """Backend that returns overly long output. Tests character limit enforcement."""

    def complete(self, prompt: str) -> str:
        return "A" * 300


# ── Tests ─────────────────────────────────────────────────────────────────


def test_interface_has_all_pipeline_methods():
    """All 13 pipeline methods exist on BackendInterface."""
    for method in PIPELINE_METHODS:
        assert hasattr(BackendInterface, method), f"Missing method: {method}"


def test_minimal_backend_inherits_all_methods():
    """A minimal complete()-only backend inherits all methods without error."""
    b = MinimalBackend()
    # Should not raise AttributeError
    assert b.generate_room_name(1)
    assert b.generate_room_description(1, "Test Room")
    assert b.generate_room_description_short(1, "Test Room")
    assert b.generate_monster_name(1)
    assert b.generate_bounty_description("Rat", 1, "Sunken Halls")
    assert b.generate_boss_name(1)
    assert b.generate_hint(1, 1, theme="Sunken Halls")
    assert b.generate_breach_name()
    assert b.generate_atmospheric_broadcast("test")
    assert b.generate_npc_dialogue("grist", "greeting")
    riddle, answer = b.generate_riddle()
    assert riddle and answer
    skin = b.generate_narrative_skin("hold_the_line", "dark")
    assert "title" in skin and "description" in skin
    announcements = b.generate_epoch_announcements("hold_the_line", "heist")
    assert len(announcements) == 3
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    assert preamble


def test_dummy_backend_all_methods():
    """DummyBackend still works (regression)."""
    b = DummyBackend()
    assert b.generate_room_name(1)
    assert b.generate_room_description(1, "Hall")
    assert b.generate_room_description_short(1, "Hall")
    assert b.generate_monster_name(1)
    assert b.generate_bounty_description("Rat", 1, "Halls")
    assert b.generate_boss_name(1)
    assert b.generate_hint(1, 1, theme="Halls")
    riddle, answer = b.generate_riddle()
    assert riddle and answer
    assert b.generate_npc_dialogue("grist", "greeting")
    assert b.generate_breach_name()
    skin = b.generate_narrative_skin("hold_the_line", "dark")
    assert "title" in skin and "description" in skin and "broadcasts" in skin
    assert b.generate_atmospheric_broadcast("dark")
    announcements = b.generate_epoch_announcements("hold_the_line", "heist")
    assert len(announcements) == 3
    for msg in announcements:
        assert len(msg) <= LLM_OUTPUT_CHAR_LIMIT
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    assert preamble
    assert len(preamble) > 100


def test_base_class_fallback_on_error():
    """Base class methods fall back to DummyBackend on complete() failure."""
    b = BrokenBackend()
    # Should NOT raise — should fall back to DummyBackend
    name = b.generate_room_name(1)
    assert name
    desc = b.generate_room_description(1, "Hall")
    assert desc
    monster = b.generate_monster_name(1)
    assert monster
    boss = b.generate_boss_name(1)
    assert boss
    breach = b.generate_breach_name()
    assert breach
    hint = b.generate_hint(1, 1, theme="Halls")
    assert hint
    riddle, answer = b.generate_riddle()
    assert riddle and answer
    npc = b.generate_npc_dialogue("grist", "greeting")
    assert npc
    skin = b.generate_narrative_skin("hold_the_line", "dark")
    assert "title" in skin
    broadcast = b.generate_atmospheric_broadcast("dark")
    assert broadcast


def test_base_class_parses_valid_output():
    """Base class methods correctly parse valid LLM output."""
    b = EchoBackend("Crystal Cavern")
    name = b.generate_room_name(1)
    assert name == "Crystal Cavern"

    b2 = EchoBackend("Obsidian Wurm")
    boss = b2.generate_boss_name(1)
    assert boss == "Obsidian Wurm"

    b3 = EchoBackend("The Rift")
    breach = b3.generate_breach_name()
    assert breach == "The Rift"


def test_riddle_parses_pipe_format():
    """Base class riddle parser handles RIDDLE|ANSWER format."""
    b = EchoBackend("What burns without fire?|shadow")
    riddle, answer = b.generate_riddle()
    assert riddle == "What burns without fire?"
    assert answer == "shadow"


def test_output_char_limit():
    """All outputs respect the 150-char limit."""
    b = VerboseBackend()
    desc = b.generate_room_description(1, "Hall")
    assert len(desc) <= LLM_OUTPUT_CHAR_LIMIT
    short = b.generate_room_description_short(1, "Hall")
    assert len(short) <= LLM_OUTPUT_CHAR_LIMIT
    hint = b.generate_hint(1, 1, theme="Halls")
    assert len(hint) <= LLM_OUTPUT_CHAR_LIMIT
    bounty = b.generate_bounty_description("Rat", 1, "Halls")
    assert len(bounty) <= LLM_OUTPUT_CHAR_LIMIT
    npc = b.generate_npc_dialogue("grist", "greeting")
    assert len(npc) <= LLM_OUTPUT_CHAR_LIMIT
    broadcast = b.generate_atmospheric_broadcast("dark")
    assert len(broadcast) <= LLM_OUTPUT_CHAR_LIMIT


def test_get_backend_returns_dummy_by_default():
    """get_backend() returns DummyBackend when no DB or env config."""
    b = get_backend()
    assert isinstance(b, DummyBackend)


def test_backend_from_config():
    """_backend_from_config creates correct backend types."""
    b = _backend_from_config({"backend": "dummy"})
    assert isinstance(b, DummyBackend)

    b = _backend_from_config({"backend": "google", "api_key": "test-key"})
    assert isinstance(b, GoogleBackend)


def test_validation_layer_wraps_backend():
    """ValidationLayer wraps any backend and enforces limits."""
    b = DummyBackend()
    v = ValidationLayer(b)
    result = v.generate("test prompt")
    assert len(result) <= LLM_OUTPUT_CHAR_LIMIT


def test_full_epoch_generation_with_dummy():
    """Full epoch generation works with DummyBackend (integration)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    create_epoch(conn, 1, "hold_the_line", "heist")

    b = DummyBackend()
    generate_floor_themes(conn, b)
    generate_town(conn, b)
    floor_themes = get_floor_themes(conn)
    stats = generate_world(conn, b, floor_themes=floor_themes)

    assert stats["rooms"] > 0
    assert stats["monsters"] > 0
    conn.close()


# ── Epoch Announcement Tests ─────────────────────────────────────────────


def test_dummy_backend_announcements_returns_three_strings():
    """DummyBackend.generate_epoch_announcements returns exactly 3 strings."""
    b = DummyBackend()
    msgs = b.generate_epoch_announcements("hold_the_line", "heist")
    assert isinstance(msgs, list)
    assert len(msgs) == 3
    for msg in msgs:
        assert isinstance(msg, str)
        assert len(msg) > 0
        assert len(msg) <= BROADCAST_CHAR_LIMIT


def test_base_class_announcements_returns_three_strings():
    """BackendInterface default generates 3 announcements via complete()."""
    b = EchoBackend("The walls shudder.\nA new epoch dawns.\nThe stairs are open.")
    msgs = b.generate_epoch_announcements("hold_the_line", "heist")
    assert isinstance(msgs, list)
    assert len(msgs) == 3
    assert msgs[0] == "The walls shudder."
    assert msgs[1] == "A new epoch dawns."
    assert msgs[2] == "The stairs are open."


def test_announcements_fallback_on_error():
    """Announcements fall back to static messages when complete() fails."""
    b = BrokenBackend()
    msgs = b.generate_epoch_announcements("hold_the_line", "heist")
    assert isinstance(msgs, list)
    assert len(msgs) == 3
    for msg in msgs:
        assert len(msg) > 0
        assert len(msg) <= BROADCAST_CHAR_LIMIT


def test_announcements_pads_if_fewer_than_three():
    """If LLM returns fewer than 3 lines, pad with fallbacks."""
    b = EchoBackend("Only one line here.")
    msgs = b.generate_epoch_announcements("hold_the_line", "heist")
    assert len(msgs) == 3
    assert msgs[0] == "Only one line here."


def test_announcements_truncates_long_messages():
    """Each announcement is truncated to BROADCAST_CHAR_LIMIT (200)."""
    long_line = "X" * 300
    b = EchoBackend(f"{long_line}\n{long_line}\n{long_line}")
    msgs = b.generate_epoch_announcements("hold_the_line", "heist")
    assert len(msgs) == 3
    for msg in msgs:
        assert len(msg) <= BROADCAST_CHAR_LIMIT


def test_announcements_strips_numbering():
    """Leading '1.' or '2)' numbering is stripped from LLM output."""
    b = EchoBackend("1. The ground shakes.\n2. A new shape emerges.\n3. Enter.")
    msgs = b.generate_epoch_announcements("hold_the_line", "heist")
    assert msgs[0] == "The ground shakes."
    assert msgs[1] == "A new shape emerges."
    assert msgs[2] == "Enter."


def test_minimal_backend_inherits_announcements():
    """A minimal complete()-only backend can call generate_epoch_announcements."""
    b = MinimalBackend()
    msgs = b.generate_epoch_announcements("hold_the_line", "heist")
    assert isinstance(msgs, list)
    assert len(msgs) == 3


def test_announcements_accept_epoch_name():
    """generate_epoch_announcements accepts epoch_name kwarg."""
    b = DummyBackend()
    msgs = b.generate_epoch_announcements(
        "hold_the_line", "heist", epoch_name="The Withering"
    )
    assert len(msgs) == 3


def test_announcements_allow_up_to_200_chars():
    """Announcements with 151-200 chars should NOT be truncated."""
    # 180 chars — fits within 200 limit
    line_180 = "A" * 180
    b = EchoBackend(f"{line_180}\n{line_180}\n{line_180}")
    msgs = b.generate_epoch_announcements("hold_the_line", "heist")
    assert len(msgs) == 3
    for msg in msgs:
        assert len(msg) == 180  # No truncation


# ── Epoch Preamble Tests ────────────────────────────────────────────────


def test_dummy_preamble_returns_nonempty():
    """DummyBackend.generate_epoch_preamble returns non-empty prose."""
    b = DummyBackend()
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    assert isinstance(preamble, str)
    assert len(preamble) > 100
    # Should contain multiple paragraphs (double newline separated)
    paragraphs = [p.strip() for p in preamble.split("\n\n") if p.strip()]
    assert len(paragraphs) >= 5


def test_dummy_preamble_mentions_npcs():
    """DummyBackend preamble references all four NPCs."""
    b = DummyBackend()
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    for npc in ("Grist", "Maren", "Torval", "Whisper"):
        assert npc in preamble, f"Preamble should mention {npc}"


def test_preamble_fallback_on_error():
    """Preamble falls back to DummyBackend when complete() fails."""
    b = BrokenBackend()
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    assert isinstance(preamble, str)
    assert len(preamble) > 100
    # Should be the DummyBackend static text
    assert "Grist" in preamble


def test_preamble_from_llm_output():
    """Base class parses multi-paragraph LLM output correctly."""
    text = (
        "The ground shuddered at dawn. Dust fell from the rafters.\n\n"
        "Below, the dungeon had changed. New corridors where walls used to be.\n\n"
        "Grist poured without being asked. Maren checked her supplies.\n\n"
        "Torval laid out fresh stock. Whisper traced a pattern on the table.\n\n"
        "The stairs are open. They always are."
    )
    b = EchoBackend(text)
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    assert preamble == text


def test_preamble_strips_markdown_headers():
    """Preamble generation strips markdown headers from LLM output."""
    text = (
        "# The Shiver\n"
        "The ground shuddered at dawn. Dust fell from the rafters and the bottles behind "
        "the bar rattled against each other. The lanterns dimmed for a moment.\n\n"
        "**The Town**\n"
        "Grist poured without being asked, his hand steady, his eyes on the door. The "
        "ledger is open to a fresh page.\n\n"
        "The stairs are open. Cold air drifts up from below, carrying the smell of wet stone."
    )
    b = EchoBackend(text)
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    assert "# The Shiver" not in preamble
    assert "**The Town**" not in preamble
    assert "The ground shuddered" in preamble
    assert "Grist poured" in preamble


def test_minimal_backend_inherits_preamble():
    """A minimal complete()-only backend can call generate_epoch_preamble."""
    b = MinimalBackend()
    # MinimalBackend returns "test output" which is <100 chars, so fallback
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    assert isinstance(preamble, str)
    assert len(preamble) > 100


def test_preamble_accepts_all_kwargs():
    """generate_epoch_preamble accepts floor_themes and spell_names."""
    b = DummyBackend()
    themes = {1: {"floor_name": "Drowned Corridors"}, 2: {"floor_name": "Luminous Rot"}}
    spells = ["Glacial Shatter", "Void Spike", "Ember Bolt"]
    preamble = b.generate_epoch_preamble(
        "raid_boss", "emergence",
        narrative_theme="The Withering",
        floor_themes=themes,
        spell_names=spells,
    )
    assert isinstance(preamble, str)
    assert len(preamble) > 100


def test_preamble_stored_in_epoch():
    """Preamble column is accessible in the epoch table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    create_epoch(conn, 1, "hold_the_line", "heist")

    # Verify column exists and is empty by default
    row = conn.execute("SELECT preamble FROM epoch WHERE id = 1").fetchone()
    assert row is not None
    assert row["preamble"] == ""

    # Store a preamble
    b = DummyBackend()
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    conn.execute("UPDATE epoch SET preamble = ? WHERE id = 1", (preamble,))
    conn.commit()

    row = conn.execute("SELECT preamble FROM epoch WHERE id = 1").fetchone()
    assert row["preamble"] == preamble
    assert len(row["preamble"]) > 100
    conn.close()


def test_preamble_strips_radio_jargon():
    """Preamble cleanup strips radio jargon lines from LLM output."""
    text = (
        "*static crackles*\n"
        "The ground shuddered at dawn. Dust fell from the rafters.\n\n"
        "Warning: transmission unstable\n"
        "Grist poured without being asked, his hand steady.\n\n"
        "Signal lost...\n"
        "The stairs are open."
    )
    b = EchoBackend(text)
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    assert "*static" not in preamble
    assert "Warning:" not in preamble
    assert "Signal lost" not in preamble
    assert "The ground shuddered" in preamble
    assert "Grist poured" in preamble
    assert "The stairs are open" in preamble


def test_dummy_preamble_no_floor_names():
    """DummyBackend preamble should not reveal any floor names."""
    from config import FLOOR_THEMES
    b = DummyBackend()
    preamble = b.generate_epoch_preamble("hold_the_line", "heist")
    for floor_name in FLOOR_THEMES.values():
        assert floor_name not in preamble, (
            f"Preamble should not contain floor name '{floor_name}'"
        )
