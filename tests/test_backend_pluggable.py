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

from config import LLM_OUTPUT_CHAR_LIMIT
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
]


class MinimalBackend(BackendInterface):
    """Backend that only implements complete(). Tests inheritance of all other methods."""

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        return "test output"


class BrokenBackend(BackendInterface):
    """Backend whose complete() always raises. Tests fallback behavior."""

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        raise RuntimeError("API down")


class EchoBackend(BackendInterface):
    """Backend that returns a fixed string. Tests parsing of valid LLM output."""

    def __init__(self, response: str = "Crystal Cavern"):
        self._response = response

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        return self._response


class VerboseBackend(BackendInterface):
    """Backend that returns overly long output. Tests character limit enforcement."""

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        return "A" * 300


# ── Tests ─────────────────────────────────────────────────────────────────


def test_interface_has_all_pipeline_methods():
    """All 12 pipeline methods exist on BackendInterface."""
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
