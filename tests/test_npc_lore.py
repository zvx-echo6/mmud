"""
Tests for NPC Deep Lore Integration.
Covers: per-NPC lore content, cross-reference inclusion/exclusion,
trigger word detection, Soren nuclear trigger, interaction count tracking,
layer depth guidance, system prompt injection.
"""

import os
import sys
import sqlite3
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db.database import init_schema
from src.systems.npc_lore import (
    get_npc_lore,
    get_layer_instructions,
    detect_triggers,
    build_trigger_hint,
    build_depth_guidance,
    TRIGGER_WORDS,
)
from src.systems.npc_conversation import (
    _build_system_prompt,
    _get_interaction_count,
)


# ── Test Database Setup ─────────────────────────────────────────────────────


def _create_test_db(day_number=5) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the MMUD schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', ?)""",
        (day_number,),
    )
    conn.execute(
        "INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!test01', 'LoreSeeker')"
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, level, gold_carried, floor, combat_monster_id, bard_tokens, gold_banked,
           last_login)
           VALUES (1, 1, 'LoreSeeker', 'caster', 35, 35, 3, 2, 4,
                   'town', 5, 100, 0, NULL, 0, 50,
                   '2026-01-05T00:00:00')"""
    )
    conn.execute(
        "INSERT INTO npc_dialogue (npc, context, dialogue) VALUES ('grist', 'greeting', 'Grist nods.')"
    )
    conn.commit()
    return conn


def _get_player(conn):
    row = conn.execute("SELECT * FROM players WHERE id = 1").fetchone()
    return dict(row)


# ── Test 1-4: Per-NPC Lore Content ──────────────────────────────────────────


class TestNPCLoreContent(unittest.TestCase):
    """Test that each NPC's lore payload contains required sections."""

    def test_grist_lore_contains_required_sections(self):
        """Grist's lore includes all structural sections."""
        lore = get_npc_lore("grist")
        self.assertIn("DEEP LORE", lore)
        self.assertIn("THE FOUNDATION", lore)
        self.assertIn("YOUR DEEP IDENTITY", lore)
        self.assertIn("LAYER 1", lore)
        self.assertIn("LAYER 5", lore)
        self.assertIn("WHAT OTHERS REVEAL ABOUT YOU", lore)
        self.assertIn("YOUR ROLE IN THE SHARED TRUTH", lore)
        self.assertIn("ABSOLUTE RULES", lore)

    def test_maren_lore_contains_required_sections(self):
        """Maren's lore includes all structural sections."""
        lore = get_npc_lore("maren")
        self.assertIn("DEEP LORE", lore)
        self.assertIn("THE FOUNDATION", lore)
        self.assertIn("field medic", lore)
        self.assertIn("LAYER 1", lore)
        self.assertIn("LAYER 5", lore)
        self.assertIn("WHAT OTHERS REVEAL ABOUT YOU", lore)
        self.assertIn("ABSOLUTE RULES", lore)

    def test_torval_lore_contains_required_sections(self):
        """Torval's lore includes all structural sections."""
        lore = get_npc_lore("torval")
        self.assertIn("DEEP LORE", lore)
        self.assertIn("The Vanguard", lore)
        self.assertIn("LAYER 1", lore)
        self.assertIn("LAYER 5", lore)
        self.assertIn("WHAT OTHERS REVEAL ABOUT YOU", lore)
        self.assertIn("ABSOLUTE RULES", lore)

    def test_whisper_lore_contains_required_sections(self):
        """Whisper's lore includes all structural sections."""
        lore = get_npc_lore("whisper")
        self.assertIn("DEEP LORE", lore)
        self.assertIn("Soren", lore)
        self.assertIn("LAYER 1", lore)
        self.assertIn("LAYER 5", lore)
        self.assertIn("WHAT OTHERS REVEAL ABOUT YOU", lore)
        self.assertIn("ABSOLUTE RULES", lore)


# ── Test 5-8: Cross-Reference Inclusion/Exclusion ───────────────────────────


class TestCrossReferences(unittest.TestCase):
    """Test that NPCs get other NPCs' comments about THEM, not others' full lore."""

    def test_grist_gets_maren_reference_about_grist(self):
        """Grist's lore contains Maren's observations about Grist."""
        lore = get_npc_lore("grist")
        self.assertIn("Griston", lore)  # Maren references "Griston"
        self.assertIn("patient who refused treatment", lore)

    def test_grist_does_not_get_maren_full_layers(self):
        """Grist's lore does NOT contain Maren's own Layer 4-5 content."""
        lore = get_npc_lore("grist")
        # Maren's Layer 4 specifics about herself
        self.assertNotIn("stanch the bleed", lore)
        self.assertNotIn("living blueprint", lore)

    def test_torval_gets_whisper_reference_about_torval(self):
        """Torval's lore contains Whisper's comments about Torval."""
        lore = get_npc_lore("torval")
        self.assertIn("terrified deference", lore)

    def test_whisper_gets_cross_refs_from_all_three(self):
        """Whisper's lore contains references from Grist, Maren, and Torval."""
        lore = get_npc_lore("whisper")
        self.assertIn("person balanced on a ledge", lore)  # Grist about Whisper
        self.assertIn("have you eaten", lore)               # Maren about Whisper
        self.assertIn("broken sword hilt", lore)             # Torval about Whisper


# ── Test 9-11: Trigger Word Detection ───────────────────────────────────────


class TestTriggerDetection(unittest.TestCase):
    """Test trigger word detection across NPCs."""

    def test_grist_ledger_trigger(self):
        """'ledger' triggers Layer 3 for Grist."""
        matches = detect_triggers("grist", "tell me about the ledger")
        self.assertTrue(any(m[0] == "ledger" and m[1] == 3 for m in matches))

    def test_maren_scars_trigger(self):
        """'your scars' triggers Layer 4 for Maren."""
        matches = detect_triggers("maren", "what do your scars mean?")
        self.assertTrue(any(m[0] == "your scars" and m[1] == 4 for m in matches))

    def test_torval_shield_trigger(self):
        """'the shield' triggers Layer 4 for Torval."""
        matches = detect_triggers("torval", "where is the shield?")
        self.assertTrue(any(m[0] == "the shield" and m[1] == 4 for m in matches))

    def test_whisper_what_are_you_trigger(self):
        """'what are you' triggers Layer 4 for Whisper."""
        matches = detect_triggers("whisper", "what are you exactly?")
        self.assertTrue(any(m[0] == "what are you" and m[1] == 4 for m in matches))

    def test_no_triggers_on_casual_message(self):
        """Casual message returns no triggers."""
        matches = detect_triggers("grist", "hello how are you")
        self.assertEqual(matches, [])

    def test_deepest_trigger_first(self):
        """Multiple triggers returned with deepest layer first."""
        # "soren" (L5) + "ledger" (L3)
        matches = detect_triggers("grist", "does soren's name appear in the ledger?")
        self.assertGreater(len(matches), 1)
        self.assertEqual(matches[0][1], 5)  # Deepest first


# ── Test 12: Soren Nuclear Trigger ──────────────────────────────────────────


class TestSorenTrigger(unittest.TestCase):
    """Test that 'soren' is a Layer 5 trigger for all four NPCs."""

    def test_soren_triggers_layer5_all_npcs(self):
        """'soren' is Layer 5 trigger for grist, maren, torval, whisper."""
        for npc in ["grist", "maren", "torval", "whisper"]:
            with self.subTest(npc=npc):
                matches = detect_triggers(npc, "who is soren?")
                self.assertTrue(
                    any(m[0] == "soren" and m[1] == 5 for m in matches),
                    f"'soren' not a Layer 5 trigger for {npc}",
                )


# ── Test 13-14: Trigger Hint Builder ────────────────────────────────────────


class TestTriggerHintBuilder(unittest.TestCase):
    """Test the trigger hint string builder."""

    def test_hint_built_on_trigger(self):
        """build_trigger_hint returns hint text when triggers match."""
        hint = build_trigger_hint("grist", "tell me about soren")
        self.assertIn("LORE TRIGGER DETECTED", hint)
        self.assertIn("Layer 5", hint)
        self.assertIn("soren", hint)

    def test_no_hint_on_no_trigger(self):
        """build_trigger_hint returns empty string when no triggers match."""
        hint = build_trigger_hint("grist", "nice weather today")
        self.assertEqual(hint, "")


# ── Test 15-16: Interaction Count ───────────────────────────────────────────


class TestInteractionCount(unittest.TestCase):
    """Test _get_interaction_count from npc_memory table."""

    def test_interaction_count_zero_for_new_player(self):
        """New player with no npc_memory returns 0."""
        conn = _create_test_db()
        count = _get_interaction_count(conn, 1, "grist")
        self.assertEqual(count, 0)

    def test_interaction_count_from_npc_memory(self):
        """Player with npc_memory returns turn_count."""
        conn = _create_test_db()
        conn.execute(
            """INSERT INTO npc_memory (player_id, npc, turn_count, summary, updated_at)
               VALUES (1, 'grist', 15, 'test summary', '2026-01-05')"""
        )
        conn.commit()
        count = _get_interaction_count(conn, 1, "grist")
        self.assertEqual(count, 15)


# ── Test 17-20: Layer Depth Guidance ────────────────────────────────────────


class TestDepthGuidance(unittest.TestCase):
    """Test layer-depth guidance strings based on interaction count."""

    def test_depth_layer1_only(self):
        """0-3 interactions → Layer 1 only."""
        guidance = build_depth_guidance(2)
        self.assertIn("Layer 1 only", guidance)
        self.assertIn("2 times", guidance)

    def test_depth_layer1_2(self):
        """4-10 interactions → Layer 1-2."""
        guidance = build_depth_guidance(7)
        self.assertIn("Layer 1-2", guidance)

    def test_depth_layer1_3(self):
        """11-25 interactions → Layer 1-3."""
        guidance = build_depth_guidance(20)
        self.assertIn("Layer 1-3", guidance)

    def test_depth_layer1_4(self):
        """26+ interactions → Layer 1-4."""
        guidance = build_depth_guidance(30)
        self.assertIn("Layer 1-4", guidance)


# ── Test 21: System Prompt Injection ────────────────────────────────────────


class TestSystemPromptInjection(unittest.TestCase):
    """Test that lore is injected into _build_system_prompt output."""

    def test_lore_in_system_prompt(self):
        """_build_system_prompt includes lore block when interaction_count > 0."""
        conn = _create_test_db()
        player = _get_player(conn)
        prompt = _build_system_prompt(
            conn, "grist", "Epoch 1, Day 5.",
            player=player,
            interaction_count=5,
        )
        self.assertIn("DEEP LORE", prompt)
        self.assertIn("THE FOUNDATION", prompt)
        self.assertIn("Hearth-Sworn", prompt)
        self.assertIn("CONVERSATION DEPTH", prompt)
        self.assertIn("Layer 1-2", prompt)

    def test_lore_in_prompt_for_new_player(self):
        """Even at interaction_count=0, lore block is injected (Layer 1 only)."""
        conn = _create_test_db()
        player = _get_player(conn)
        prompt = _build_system_prompt(
            conn, "grist", "Epoch 1, Day 5.",
            player=player,
            interaction_count=0,
        )
        self.assertIn("DEEP LORE", prompt)
        self.assertIn("Layer 1 only", prompt)

    def test_trigger_hint_in_prompt(self):
        """_build_system_prompt includes trigger hint when provided."""
        conn = _create_test_db()
        player = _get_player(conn)
        trigger = build_trigger_hint("grist", "who is soren?")
        prompt = _build_system_prompt(
            conn, "grist", "Epoch 1, Day 5.",
            player=player,
            interaction_count=10,
            trigger_hint=trigger,
        )
        self.assertIn("LORE TRIGGER DETECTED", prompt)
        self.assertIn("Layer 5", prompt)


# ── Test 22: Unknown NPC Returns Empty Lore ─────────────────────────────────


class TestUnknownNPC(unittest.TestCase):
    """Test graceful handling of unknown NPC names."""

    def test_unknown_npc_returns_empty(self):
        """get_npc_lore returns empty string for unknown NPC."""
        lore = get_npc_lore("bogus_npc")
        self.assertEqual(lore, "")

    def test_unknown_npc_no_triggers(self):
        """detect_triggers returns empty list for unknown NPC."""
        matches = detect_triggers("bogus_npc", "soren hearth-sworn ledger")
        self.assertEqual(matches, [])


# ── Test 23: Layer Instructions ─────────────────────────────────────────────


class TestLayerInstructions(unittest.TestCase):
    """Test the layer instruction text."""

    def test_layer_instructions_content(self):
        """get_layer_instructions returns the layer system description."""
        instructions = get_layer_instructions()
        self.assertIn("LAYER SYSTEM", instructions)
        self.assertIn("five layers", instructions)
        self.assertIn("mask slips", instructions)


if __name__ == "__main__":
    unittest.main()
