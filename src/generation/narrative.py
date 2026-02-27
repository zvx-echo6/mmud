"""
LLM Content Pipeline for MMUD.
This is the ONLY module that touches LLM APIs.
All text content is batch-generated at epoch start — zero runtime LLM calls.

Backend selection via environment variable MMUD_LLM_BACKEND:
  dummy    — template-based placeholder text (default, no API needed)
  anthropic — Claude API
  openai   — OpenAI-compatible API
  google   — Gemini API

API keys via MMUD_ANTHROPIC_API_KEY, MMUD_OPENAI_API_KEY, MMUD_GOOGLE_API_KEY
Model override via MMUD_LLM_MODEL
"""

import logging
import os
import random
import sqlite3
from abc import ABC, abstractmethod

from config import HINT_FORBIDDEN_VERBS, LLM_OUTPUT_CHAR_LIMIT

logger = logging.getLogger(__name__)


# ── Backend Interface ──────────────────────────────────────────────────────


class BackendInterface(ABC):
    """Base class all LLM provider backends implement."""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        """Generate text from a prompt.

        Args:
            prompt: The generation prompt.
            max_tokens: Maximum tokens to generate.

        Returns:
            Generated text string.
        """
        ...

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        """Multi-turn conversation. Override for real LLM backends.

        Args:
            system: System prompt (NPC personality + game state).
            messages: List of {"role": "user"|"assistant", "content": str}.
            max_tokens: Maximum tokens to generate.

        Returns:
            Assistant response string.
        """
        # Default: fall back to complete() with last user message
        last_user = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                last_user = msg["content"]
                break
        return self.complete(f"{system}\n\nPlayer says: {last_user}", max_tokens)

    def generate_spell_names(self, theme: str = "") -> list[str]:
        """Generate 3 spell names for the epoch (each <=20 chars).

        Default implementation calls self.complete() with a themed prompt.
        DummyBackend overrides this with a static pool.
        Falls back to static pool on invalid LLM results.
        """
        from config import DUMMY_SPELL_NAMES

        theme_desc = theme if theme else "a dark underground dungeon"
        prompt = (
            f"Generate 3 short spell names for a dungeon epoch themed around {theme_desc}. "
            f"Each name must be under 20 characters. "
            f"Return only the names, one per line."
        )
        try:
            raw = self.complete(prompt, max_tokens=100)
            names = [line.strip() for line in raw.strip().splitlines() if line.strip()]
            # Strip leading markers like "1.", "- ", "* "
            cleaned = []
            for n in names:
                n = n.lstrip("0123456789.-)*# ").strip()
                if n:
                    cleaned.append(n)
            names = cleaned

            # Validate: exactly 3 names, each <=20 chars
            if len(names) >= 3:
                names = names[:3]
            else:
                logger.warning(f"LLM returned {len(names)} spell names, need 3. Falling back.")
                return random.sample(DUMMY_SPELL_NAMES, 3)

            valid = all(len(n) <= 20 for n in names)
            if not valid:
                # Truncate long names rather than discarding
                names = [n[:20] for n in names]

            return names
        except Exception as e:
            logger.warning(f"Spell name generation failed: {e}. Falling back to static pool.")
            return random.sample(DUMMY_SPELL_NAMES, 3)

    def generate_floor_themes(self) -> dict[int, dict]:
        """Generate per-epoch floor sub-themes for narrative descent.

        Returns dict keyed by floor number, each value has:
        floor_name, atmosphere, narrative_beat, floor_transition.
        All text fields <=150 chars.

        Default implementation calls self.complete() with a narrative arc prompt.
        Falls back to DummyBackend static pool on failure.
        """
        import random as _rng
        from config import NUM_FLOORS

        # Random seed elements to ensure variety across epochs
        _envs = [
            "subterranean river networks", "petrified forest", "collapsed cathedral",
            "flooded mine shafts", "bone-walled catacombs", "volcanic fissures",
            "frozen underground lake", "bioluminescent caverns", "rusted machinery",
            "living coral tunnels", "obsidian galleries", "salt flats", "root systems",
            "sulfur vents", "sandstone tombs", "mercury pools", "chitin warrens",
            "glass spires", "tar pits", "fungal networks", "sunken aqueducts",
            "iron forges", "crystal geodes", "ash drifts", "tidal caves",
        ]
        _moods = [
            "dread", "claustrophobia", "vertigo", "wrongness", "hunger",
            "grief", "paranoia", "reverence", "decay", "silence",
            "weight", "abandonment", "watching", "erosion", "fever",
        ]
        seed_envs = _rng.sample(_envs, 3)
        seed_mood = _rng.choice(_moods)
        prompt = (
            f"Generate 8 unique floor identities for a dungeon descent. "
            f"This epoch's dungeon draws from: {', '.join(seed_envs)}. "
            f"The dominant mood is {seed_mood}. "
            f"Each floor needs: floor_name (2-3 word evocative name — be creative, "
            f"avoid generic fantasy cliches like 'depths', 'abyss', 'halls', 'caverns'), "
            f"atmosphere (one vivid sensory sentence), "
            f"narrative_beat (what the player discovers or realizes on this floor), "
            f"floor_transition (what the player sees/feels when entering). "
            f"Descent arc: Floor 1 = unsettling introduction, Floors 2-4 = escalating "
            f"wrongness, Floors 5-7 = hostile and alien, Floor 8 = the source. "
            f"Every name must be DISTINCT — no two floors should feel interchangeable. "
            f"Each field under 150 characters. "
            f"Return exactly {NUM_FLOORS} entries, one per line, format: "
            f"floor_name|atmosphere|narrative_beat|floor_transition"
        )
        try:
            raw = self.complete(prompt, max_tokens=600)
            lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
            # Strip leading markers
            cleaned = []
            for line in lines:
                line = line.lstrip("0123456789.-)*# ").strip()
                if "|" in line:
                    cleaned.append(line)
            if len(cleaned) < NUM_FLOORS:
                logger.warning(f"LLM returned {len(cleaned)} floor themes, need {NUM_FLOORS}. Falling back.")
                return DummyBackend().generate_floor_themes()

            result = {}
            for i, line in enumerate(cleaned[:NUM_FLOORS]):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 4:
                    logger.warning(f"Floor theme line has {len(parts)} parts, need 4. Falling back.")
                    return DummyBackend().generate_floor_themes()
                result[i + 1] = {
                    "floor_name": parts[0][:150],
                    "atmosphere": parts[1][:150],
                    "narrative_beat": parts[2][:150],
                    "floor_transition": parts[3][:150],
                }
            return result
        except Exception as e:
            logger.warning(f"Floor theme generation failed: {e}. Falling back to static pool.")
            return DummyBackend().generate_floor_themes()

    def generate_town_room_name(self, row: int, col: int, npc_name: str = None) -> str:
        """Generate a town room name for Floor 0.

        NPC rooms keep fixed names. Non-NPC rooms get LLM-generated names.
        Falls back to DummyBackend on failure.
        """
        if npc_name == "grist":
            return "The Last Ember"
        if npc_name == "maren":
            return "Maren's Clinic"
        if npc_name == "torval":
            return "Torval's Trading Post"
        if npc_name == "whisper":
            return "Whisper's Alcove"

        prompt = (
            "Generate one short name (2-4 words) for a location in a small "
            "frontier settlement built around a tavern at the edge of a dungeon. "
            "This is a worn, atmospheric town — crumbling walls, ash-dusted paths, "
            "old market stalls, lantern-lit alleys. Not a fantasy city. A last outpost. "
            "Return ONLY the name, nothing else."
        )
        try:
            raw = self.complete(prompt, max_tokens=30)
            name = raw.strip().strip('"\'').split('\n')[0].strip()
            if name and len(name) <= 40:
                return name
        except Exception as e:
            logger.warning(f"Town room name generation failed: {e}")
        return DummyBackend().generate_town_room_name(row, col, npc_name)

    def generate_town_description(self, name: str, npc_name: str = None) -> str:
        """Generate a town room description for Floor 0.

        Returns an atmospheric sensory description. Under 150 characters.
        Falls back to DummyBackend on failure.
        """
        if npc_name:
            npc_ctx = {
                "grist": "This is the bar. Smoke, amber light, a long wooden counter.",
                "maren": "This is the healer's workspace. Herbs, clean linens, sharp tools.",
                "torval": "This is the merchant's shop. Crates, weapons, armor on display.",
                "whisper": "This is the sage's alcove. Shadows, old books, strange markings.",
            }
            ctx = npc_ctx.get(npc_name, "An NPC lives here.")
            prompt = (
                f"Write a one-sentence description of '{name}' in a frontier tavern settlement. "
                f"Context: {ctx} Sensory details — what you see, smell, hear. "
                f"Under 150 characters. Return ONLY the description."
            )
        else:
            prompt = (
                f"Write a one-sentence description of '{name}' in a worn frontier settlement "
                f"at the edge of a dungeon. Ash-dusted, lantern-lit, crumbling but alive. "
                f"Sensory details. Under 150 characters. Return ONLY the description."
            )
        try:
            raw = self.complete(prompt, max_tokens=80)
            text = raw.strip().strip('"\'').split('\n')[0].strip()
            if text and text.lower() != name.lower():
                return text[:LLM_OUTPUT_CHAR_LIMIT]
        except Exception as e:
            logger.warning(f"Town description generation failed: {e}")
        return DummyBackend().generate_town_description(name, npc_name)

    def generate_lore_fragment(self, floor: int) -> str:
        """Generate a lore fragment for a room (<=80 chars).

        Default implementation calls self.complete() with a themed prompt.
        DummyBackend overrides this with a static pool.
        Falls back to static pool on invalid LLM results.
        """
        from config import DUMMY_LORE_FRAGMENTS, FLOOR_THEMES, REVEAL_LORE_MAX_CHARS

        theme = FLOOR_THEMES.get(floor, "The Depths")
        prompt = (
            f"Write one mysterious lore fragment for a dungeon room on floor {floor} "
            f"themed around '{theme}'. It should be a single cryptic sentence about "
            f"the dungeon's history. Must be under {REVEAL_LORE_MAX_CHARS} characters total. "
            f"Return only the fragment, no quotes."
        )
        try:
            raw = self.complete(prompt, max_tokens=60)
            text = raw.strip().strip('"\'')
            # Take only the first line if multi-line
            if "\n" in text:
                text = text.split("\n")[0].strip()
            if len(text) > REVEAL_LORE_MAX_CHARS:
                text = text[:REVEAL_LORE_MAX_CHARS]
            if not text:
                return random.choice(DUMMY_LORE_FRAGMENTS)[:REVEAL_LORE_MAX_CHARS]
            return text
        except Exception as e:
            logger.warning(f"Lore fragment generation failed: {e}. Falling back to static pool.")
            return random.choice(DUMMY_LORE_FRAGMENTS)[:REVEAL_LORE_MAX_CHARS]

    def generate_room_name(self, floor: int) -> str:
        """Generate a unique room name for a floor.

        Default implementation calls self.complete() with a themed prompt.
        DummyBackend overrides with template-based names (zero LLM calls).
        Falls back to DummyBackend on failure.
        """
        from config import FLOOR_THEMES
        theme = FLOOR_THEMES.get(floor, "The Depths")
        prompt = (
            f"Generate one short dungeon room name (2-4 words) for floor {floor} "
            f"themed around '{theme}'. Return ONLY the name, nothing else."
        )
        try:
            raw = self.complete(prompt, max_tokens=30)
            name = raw.strip().strip('"\'').split('\n')[0].strip()
            if name and len(name) <= 40:
                return name
        except Exception as e:
            logger.warning(f"Room name generation failed: {e}")
        return DummyBackend().generate_room_name(floor)

    def generate_room_description(self, floor: int, name: str, is_vault: bool = False,
                                  vault_type: str = "", floor_theme: dict = None) -> str:
        """Generate a full room description under 150 chars.

        Default implementation calls self.complete() with a themed prompt.
        DummyBackend overrides with template-based descriptions.
        Falls back to DummyBackend on failure.
        """
        from config import FLOOR_THEMES
        theme = FLOOR_THEMES.get(floor, "The Depths")
        vault_ctx = f" This is a {vault_type} vault." if is_vault and vault_type else ""
        prompt = (
            f"Write a one-sentence dungeon room description for '{name}' on floor {floor} "
            f"themed around '{theme}'.{vault_ctx} Use sensory details (sound, smell, temperature). "
            f"Under 150 characters. Return ONLY the description."
        )
        try:
            raw = self.complete(prompt, max_tokens=80)
            text = raw.strip().strip('"\'').split('\n')[0].strip()
            if text:
                return text[:LLM_OUTPUT_CHAR_LIMIT]
        except Exception as e:
            logger.warning(f"Room description generation failed: {e}")
        return DummyBackend().generate_room_description(floor, name, is_vault, vault_type, floor_theme)

    def generate_room_description_short(self, floor: int, name: str,
                                        floor_theme: dict = None) -> str:
        """Generate abbreviated room description for revisits.

        Default implementation calls self.complete() with a themed prompt.
        DummyBackend overrides with template-based sensory lines.
        Falls back to DummyBackend on failure.
        """
        from config import FLOOR_THEMES
        theme = FLOOR_THEMES.get(floor, "The Depths")
        prompt = (
            f"Write a very brief sensory impression of '{name}' (floor {floor}, '{theme}'). "
            f"One short sentence, under 100 characters. Return ONLY the text."
        )
        try:
            raw = self.complete(prompt, max_tokens=50)
            text = raw.strip().strip('"\'').split('\n')[0].strip()
            if text:
                return text[:LLM_OUTPUT_CHAR_LIMIT]
        except Exception as e:
            logger.warning(f"Short description generation failed: {e}")
        return DummyBackend().generate_room_description_short(floor, name, floor_theme)

    def generate_monster_name(self, tier: int, floor_theme: dict = None) -> str:
        """Generate a monster name for a tier.

        Default implementation calls self.complete() with a tier prompt.
        DummyBackend overrides with template-based names.
        Falls back to DummyBackend on failure.
        """
        prompt = (
            f"Generate one short monster name (2-3 words) for a tier {tier} dungeon creature. "
            f"Tier 1=weak, 5=legendary. Return ONLY the name."
        )
        try:
            raw = self.complete(prompt, max_tokens=20)
            name = raw.strip().strip('"\'').split('\n')[0].strip()
            if name and len(name) <= 30:
                return name
        except Exception as e:
            logger.warning(f"Monster name generation failed: {e}")
        return DummyBackend().generate_monster_name(tier, floor_theme)

    def generate_bounty_description(self, monster_name: str, floor: int, theme: str) -> str:
        """Generate a bounty briefing.

        Default implementation calls self.complete() with a themed prompt.
        DummyBackend overrides with template-based descriptions.
        Falls back to DummyBackend on failure.
        """
        prompt = (
            f"Write a one-sentence bounty briefing for hunting '{monster_name}' "
            f"on floor {floor} ({theme}). Under 150 characters. Return ONLY the text."
        )
        try:
            raw = self.complete(prompt, max_tokens=80)
            text = raw.strip().strip('"\'').split('\n')[0].strip()
            if text:
                return text[:LLM_OUTPUT_CHAR_LIMIT]
        except Exception as e:
            logger.warning(f"Bounty description generation failed: {e}")
        return DummyBackend().generate_bounty_description(monster_name, floor, theme)

    def generate_boss_name(self, floor: int, floor_theme: dict = None) -> str:
        """Generate a boss monster name for a floor.

        Default implementation calls self.complete() with a themed prompt.
        DummyBackend overrides with template-based names.
        Falls back to DummyBackend on failure.
        """
        from config import FLOOR_THEMES
        theme = FLOOR_THEMES.get(floor, "The Depths")
        prompt = (
            f"Generate one boss monster name (2-3 words) for the floor {floor} boss "
            f"of a dungeon themed around '{theme}'. Return ONLY the name."
        )
        try:
            raw = self.complete(prompt, max_tokens=20)
            name = raw.strip().strip('"\'').split('\n')[0].strip()
            if name and len(name) <= 30:
                return name
        except Exception as e:
            logger.warning(f"Boss name generation failed: {e}")
        return DummyBackend().generate_boss_name(floor, floor_theme)

    def generate_hint(self, tier: int, floor: int, room_name: str = "",
                      direction: str = "", theme: str = "") -> str:
        """Generate a secret hint at the specified tier.

        Default implementation calls self.complete() with a tier-appropriate prompt.
        DummyBackend overrides with template-based hints.
        Falls back to DummyBackend on failure.
        """
        if tier == 1:
            prompt = f"Write a vague hint about a secret on floor {floor} ({theme}). Under 150 characters."
        elif tier == 2:
            prompt = (
                f"Write a directional hint: something is hidden {direction} on floor {floor} "
                f"({theme}). Under 150 characters."
            )
        else:
            prompt = f"Write a specific hint pointing to {room_name} on floor {floor}. Under 150 characters."
        prompt += " Do NOT use action verbs like 'go', 'move', 'take', 'fight'. Return ONLY the hint."
        try:
            raw = self.complete(prompt, max_tokens=80)
            text = raw.strip().strip('"\'').split('\n')[0].strip()
            if text:
                return text[:LLM_OUTPUT_CHAR_LIMIT]
        except Exception as e:
            logger.warning(f"Hint generation failed: {e}")
        return DummyBackend().generate_hint(tier, floor, room_name, direction, theme)

    def generate_riddle(self) -> tuple[str, str]:
        """Generate a riddle and its one-word answer.

        Default implementation calls self.complete() with a prompt.
        DummyBackend overrides with template-based riddles.
        Falls back to DummyBackend on failure.
        """
        prompt = (
            "Generate a short riddle and its one-word answer for a dungeon gate. "
            "Format: RIDDLE|ANSWER. Riddle under 150 characters."
        )
        try:
            raw = self.complete(prompt, max_tokens=80)
            line = raw.strip().split('\n')[0].strip()
            if '|' in line:
                parts = line.split('|', 1)
                riddle = parts[0].strip().strip('"\'')
                answer = parts[1].strip().strip('"\'').lower()
                if riddle and answer:
                    return riddle[:LLM_OUTPUT_CHAR_LIMIT], answer
        except Exception as e:
            logger.warning(f"Riddle generation failed: {e}")
        return DummyBackend().generate_riddle()

    def generate_npc_dialogue(self, npc: str, context: str, **kwargs) -> str:
        """Generate NPC dialogue.

        Default implementation calls self.complete() with a character prompt.
        DummyBackend overrides with template-based dialogue.
        Falls back to DummyBackend on failure.
        """
        prompt = (
            f"Write one short line of dialogue for {npc.title()}, a dungeon NPC. "
            f"Context: {context}. Under 150 characters. Return ONLY the dialogue."
        )
        try:
            raw = self.complete(prompt, max_tokens=80)
            text = raw.strip().strip('"\'').split('\n')[0].strip()
            if text:
                return text[:LLM_OUTPUT_CHAR_LIMIT]
        except Exception as e:
            logger.warning(f"NPC dialogue generation failed: {e}")
        return DummyBackend().generate_npc_dialogue(npc, context, **kwargs)

    def generate_breach_name(self) -> str:
        """Generate a breach zone name.

        Default implementation calls self.complete() with a prompt.
        DummyBackend overrides with template-based names.
        Falls back to DummyBackend on failure.
        """
        prompt = (
            "Generate a short name (2-3 words, starting with 'The') for a "
            "dimensional breach zone in a dungeon. Return ONLY the name."
        )
        try:
            raw = self.complete(prompt, max_tokens=20)
            name = raw.strip().strip('"\'').split('\n')[0].strip()
            if name and len(name) <= 30:
                return name
        except Exception as e:
            logger.warning(f"Breach name generation failed: {e}")
        return DummyBackend().generate_breach_name()

    def generate_narrative_skin(self, mode: str, theme: str) -> dict:
        """Generate narrative skin for an endgame mode.

        Default implementation calls self.complete() with a structured prompt.
        DummyBackend overrides with template-based skins.
        Falls back to DummyBackend on failure.
        """
        prompt = (
            f"Generate narrative text for a dungeon {mode} event themed '{theme}'. "
            f"Provide 3 pipe-separated values: title (under 30 chars) | "
            f"description (under 150 chars) | broadcast message (under 150 chars). "
            f"Return one line: TITLE|DESCRIPTION|BROADCAST"
        )
        try:
            raw = self.complete(prompt, max_tokens=150)
            line = raw.strip().split('\n')[0].strip()
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 3:
                    return {
                        "title": parts[0][:30],
                        "description": parts[1][:LLM_OUTPUT_CHAR_LIMIT],
                        "broadcasts": [
                            parts[2][:LLM_OUTPUT_CHAR_LIMIT],
                            parts[2][:LLM_OUTPUT_CHAR_LIMIT],
                        ],
                    }
        except Exception as e:
            logger.warning(f"Narrative skin generation failed: {e}")
        return DummyBackend().generate_narrative_skin(mode, theme)

    def generate_atmospheric_broadcast(self, theme: str) -> str:
        """Generate an atmospheric broadcast message.

        Default implementation calls self.complete() with a themed prompt.
        DummyBackend overrides with template-based messages.
        Falls back to DummyBackend on failure.
        """
        prompt = (
            f"Write one short atmospheric dungeon broadcast message about '{theme}'. "
            f"Ominous tone. Under 150 characters. Return ONLY the message."
        )
        try:
            raw = self.complete(prompt, max_tokens=80)
            text = raw.strip().strip('"\'').split('\n')[0].strip()
            if text:
                return text[:LLM_OUTPUT_CHAR_LIMIT]
        except Exception as e:
            logger.warning(f"Atmospheric broadcast generation failed: {e}")
        return DummyBackend().generate_atmospheric_broadcast(theme)


    @staticmethod
    def _clean_preamble(raw: str) -> str:
        """Strip markdown artifacts and radio jargon from LLM preamble output."""
        text = raw.strip()
        # Radio jargon prefixes that some models add
        jargon_prefixes = (
            "*static", "warning:", "signal lost", "transmission",
            "---", "***", "///",
        )
        lines = []
        for line in text.split('\n'):
            stripped = line.strip()
            # Skip markdown headers
            if stripped.startswith('#'):
                continue
            # Skip bold-only lines (markdown section labels)
            if stripped.startswith('**') and stripped.endswith('**'):
                continue
            # Skip radio jargon lines
            if stripped.lower().startswith(jargon_prefixes):
                continue
            lines.append(line)
        return '\n'.join(lines).strip()

    def generate_epoch_preamble(self, endgame_mode: str, breach_type: str,
                               narrative_theme: str = "",
                               floor_themes: dict = None,
                               spell_names: list = None) -> str:
        """Generate a rich prose preamble for the web dashboard header.

        This is web-only content — no character limit. Returns 5-10 paragraphs
        of atmospheric prose describing the new epoch. Generated once at epoch
        start, stored in the epoch table, displayed for 30 days.

        Falls back to DummyBackend static text on failure.
        """
        from config import FLOOR_THEMES as BASE_THEMES

        # Build context for the prompt
        theme_ctx = f" themed around '{narrative_theme}'" if narrative_theme else ""
        mode_labels = {
            "hold_the_line": "Hold the Line — defend floor checkpoints",
            "raid_boss": "Raid Boss — a colossal creature waits at the bottom",
            "retrieve_and_escape": "Retrieve & Escape — find the objective and get out alive",
        }
        mode_desc = mode_labels.get(endgame_mode, endgame_mode)

        floor_desc = ""
        if floor_themes:
            parts = []
            for f in sorted(floor_themes):
                ft = floor_themes[f]
                name = ft.get("floor_name", BASE_THEMES.get(f, f"Floor {f}"))
                parts.append(f"Floor {f}: {name}")
            floor_desc = "\n".join(parts)

        spell_ctx = ""
        if spell_names:
            spell_ctx = f"This epoch's spells: {', '.join(spell_names)}."

        floor_block = f"Floor layout:\n{floor_desc}\n\n" if floor_desc else ""
        spell_block = f"{spell_ctx}\n\n" if spell_ctx else ""
        prompt = (
            f"You are writing the opening preamble for a new epoch of the Darkcragg "
            f"Depths — a living dungeon that periodically sheds its interior and "
            f"regenerates. This event is called the Shiver. Players experience it "
            f"through a mesh-radio text game (LoRa, 150-char messages).\n\n"
            f"The epoch{theme_ctx}. Endgame mode: {mode_desc}. Breach type: {breach_type}.\n\n"
            f"{floor_block}"
            f"{spell_block}"
            f"Write 5-7 paragraphs of prose for the tavern dashboard. "
            f"Cover these beats in order:\n"
            f"1. THE SHIVER — the dungeon has regenerated. This is a physical, "
            f"visceral event. An earthquake with intent. The stone moves. The air "
            f"pressure drops. Bottles rattle. Lanterns dim. Describe what the people "
            f"in the tavern FELT — not what they think it means.\n"
            f"2. NPC FEAR — how each NPC reacted during the Shiver. Grist stopped "
            f"pouring. Maren counted her supplies again. Torval checked the locks. "
            f"Whisper said something unsettling. These are people who have survived "
            f"this before. They are not panicking. They are preparing.\n"
            f"3. THE SETTLING — the tremor stops. The quiet that follows. The "
            f"particular quality of silence when stone has finished rearranging "
            f"itself. Dust hanging in the air. A crack in the wall that wasn't there "
            f"yesterday.\n"
            f"4. THE OPENING — the stairwell. Cold air rising from below. It smells "
            f"different now. Wetter. Colder. Something underneath the mineral smell "
            f"that nobody names. The first few steps visible in torchlight, worn "
            f"smooth by boots. Beyond the light, darkness.\n"
            f"5. THE INVITATION — one line. The stairs are open. A fact, not a "
            f"command.\n\n"
            f"RULES:\n"
            f"- Pure prose. No headers, no markdown, no bullet points, no labels.\n"
            f"- Horror-adjacent, not heroic. Dread, not adventure.\n"
            f"- Sensory and grounded — sound, smell, temperature, texture, pressure.\n"
            f"- Reference NPCs by name: Grist, Maren, Torval, Whisper.\n"
            f"- No exclamation marks. No questions directed at the reader.\n"
            f"- Do NOT mention floor names, spell names, or game mechanics.\n"
            f"- Do NOT describe the dungeon layout or what is below.\n"
            f"- Each paragraph is 2-4 sentences.\n"
            f"- Return ONLY the prose paragraphs separated by blank lines."
        )
        try:
            raw = self.complete(prompt, max_tokens=2000)
            text = self._clean_preamble(raw)
            if text and len(text) > 100:
                return text
        except Exception as e:
            logger.warning(f"Epoch preamble generation failed: {e}")
        return DummyBackend().generate_epoch_preamble(
            endgame_mode, breach_type, narrative_theme, floor_themes, spell_names,
        )

    def generate_epoch_announcements(self, endgame_mode: str,
                                     breach_type: str,
                                     narrative_theme: str = "",
                                     epoch_name: str = "") -> list[str]:
        """Generate 3 broadcast messages announcing a new epoch.

        Three beats:
          1. The Announcement — atmospheric reveal of the epoch name
          2. The Town — sensory description of the tavern
          3. The Invitation — short, direct, come in

        Args:
            endgame_mode: Endgame mode string.
            breach_type: Breach mini-event type.
            narrative_theme: Optional narrative theme.
            epoch_name: Epoch identity name for Message 1.

        Returns:
            List of exactly 3 broadcast strings (each <= 200 chars).
        """
        from config import BROADCAST_CHAR_LIMIT

        name_ctx = f" called '{epoch_name}'" if epoch_name else ""
        theme_ctx = f" themed around '{narrative_theme}'" if narrative_theme else ""
        prompt = (
            f"A living dungeon has regenerated{name_ctx}{theme_ctx}.\n\n"
            f"Write exactly 3 announcement broadcasts.\n\n"
            f"Message 1: The Announcement. Name the new epoch atmospherically. "
            f"What it is, what it feels like. Ominous reveal.\n"
            f"Message 2: The Town. What the tavern feels/smells/sounds like "
            f"right now. Sensory, grounded.\n"
            f"Message 3: The Invitation. Short. Direct. The stairs are open. "
            f"Come in.\n\n"
            f"Rules:\n"
            f"- Each message MUST be under 200 characters\n"
            f"- No quotes, labels, prefixes, or emoji\n"
            f"- Sensory, not informational\n\n"
            f"Return exactly 3 lines, one message per line, nothing else."
        )
        fallback = [
            "The ground shifts. Stone grinds against stone. Something ancient stirs below.",
            "Smoke curls from the chimney. The bar smells of char and old wood. Grist is pouring.",
            "The stairs are open.",
        ]
        try:
            raw = self.complete(prompt, max_tokens=400)
            lines = [ln.strip() for ln in raw.strip().split('\n') if ln.strip()]
            announcements = []
            for line in lines[:3]:
                cleaned = line.lstrip('0123456789.)- ').strip()
                if not cleaned:
                    cleaned = line.strip()
                if len(cleaned) > BROADCAST_CHAR_LIMIT:
                    cleaned = cleaned[:BROADCAST_CHAR_LIMIT - 3].rsplit(' ', 1)[0] + '...'
                announcements.append(cleaned)
            while len(announcements) < 3:
                announcements.append(fallback[len(announcements)])
            return announcements
        except Exception as e:
            logger.warning(f"Epoch announcement generation failed: {e}")
        return list(fallback)


# ── Dummy Backend ──────────────────────────────────────────────────────────


# Floor-themed room name components
_FLOOR_NAMES = {
    1: {
        "prefix": [
            "Sunken", "Dripping", "Cracked", "Flooded", "Moss-Covered",
            "Rusted", "Broken", "Collapsed", "Eroded", "Ancient",
            "Forgotten", "Silent", "Shadowed", "Damp", "Echoing",
        ],
        "suffix": [
            "Hall", "Chamber", "Passage", "Gallery", "Vault",
            "Cistern", "Corridor", "Crypt", "Alcove", "Antechamber",
            "Well", "Den", "Cellar", "Landing", "Vestibule",
        ],
    },
    2: {
        "prefix": [
            "Fungal", "Glowing", "Spore-Filled", "Mycelial", "Luminous",
            "Blooming", "Rotting", "Pulsing", "Tangled", "Overgrown",
            "Twisted", "Festering", "Pale", "Dim", "Winding",
        ],
        "suffix": [
            "Grotto", "Cavern", "Hollow", "Burrow", "Thicket",
            "Basin", "Pit", "Garden", "Nest", "Pool",
            "Shelf", "Rift", "Channel", "Clearing", "Bower",
        ],
    },
    3: {
        "prefix": [
            "Ember", "Scorched", "Molten", "Ashen", "Burning",
            "Obsidian", "Smoldering", "Charred", "Sulfurous", "Blazing",
            "Blackened", "Heated", "Glassy", "Slag", "Cinder",
        ],
        "suffix": [
            "Forge", "Pit", "Furnace", "Vent", "Chamber",
            "Bridge", "Crucible", "Tunnel", "Caldera", "Shelf",
            "Chasm", "Platform", "Hearth", "Channel", "Terrace",
        ],
    },
    4: {
        "prefix": [
            "Iron", "Riveted", "Bolted", "Geared", "Grinding",
            "Mechanical", "Armored", "Plated", "Rusted", "Forged",
            "Hammered", "Welded", "Tempered", "Clockwork", "Brazen",
        ],
        "suffix": [
            "Labyrinth", "Works", "Foundry", "Mill", "Corridor",
            "Junction", "Conduit", "Press", "Chamber", "Valve",
            "Hub", "Shaft", "Gantry", "Bulkhead", "Cage",
        ],
    },
    5: {
        "prefix": [
            "Toxic", "Corroded", "Withered", "Blighted", "Rotting",
            "Caustic", "Scorched", "Barren", "Diseased", "Putrid",
            "Blackened", "Festering", "Drained", "Wasted", "Decayed",
        ],
        "suffix": [
            "Waste", "Mire", "Drain", "Hollow", "Bog",
            "Marsh", "Sinkhole", "Expanse", "Flats", "Basin",
            "Trench", "Slough", "Pit", "Stretch", "Barrens",
        ],
    },
    6: {
        "prefix": [
            "Crystalline", "Frozen", "Prismatic", "Glacial", "Refracting",
            "Mineral", "Faceted", "Glinting", "Vitreous", "Lucent",
            "Fissured", "Geode", "Quartzite", "Opaline", "Jagged",
        ],
        "suffix": [
            "Cavern", "Gallery", "Prism", "Grotto", "Vault",
            "Spire", "Crevasse", "Formation", "Hollow", "Shelf",
            "Cathedral", "Chamber", "Lattice", "Gorge", "Rift",
        ],
    },
    7: {
        "prefix": [
            "Shadow", "Dark", "Umbral", "Dim", "Murky",
            "Tenebrous", "Cloaked", "Veiled", "Obscured", "Twilight",
            "Shrouded", "Lightless", "Penumbral", "Dusky", "Eclipsed",
        ],
        "suffix": [
            "Gauntlet", "Passage", "Narrows", "Corridor", "Run",
            "Defile", "Bottleneck", "Strait", "Lane", "Choke",
            "Tunnel", "Approach", "Gallery", "Crawl", "Channel",
        ],
    },
    8: {
        "prefix": [
            "Void", "Null", "Abyssal", "Terminal", "Final",
            "Shattered", "Hollow", "Resonant", "Ethereal", "Absolute",
            "Entropic", "Ruined", "Silent", "Forgotten", "Last",
        ],
        "suffix": [
            "Sanctum", "Throne", "Terminus", "Core", "Apex",
            "Abyss", "Pinnacle", "Nexus", "Gate", "End",
            "Maw", "Seat", "Vault", "Crucible", "Heart",
        ],
    },
}

# Sensory descriptions by floor (active verbs, sound/smell/temperature/light)
_FLOOR_SENSORY = {
    1: [
        "Water drips from cracked stone above.",
        "Echoes ripple through still air.",
        "Damp stone sweats in the cold.",
        "A low draft pushes through gaps.",
        "Moss clings to every surface.",
        "Rust flakes drift from iron brackets.",
        "Puddles reflect faint torchlight.",
        "The ceiling groans under its own weight.",
        "Mildew thickens the air.",
        "Condensation beads on old stone.",
        "Rats scratch somewhere behind the walls.",
        "Lichen glows faintly along the seams.",
        "Watermarks stripe the lower walls.",
        "A distant rumble carries through rock.",
        "Wet footprints fade on cold tile.",
    ],
    2: [
        "Bioluminescent caps pulse with pale light.",
        "Spores drift lazily through the air.",
        "Mycelial threads web the ceiling.",
        "A sweet rot hangs in the air.",
        "Soft squelching sounds come from below.",
        "Phosphorescent veins trace the walls.",
        "Thick tendrils curl over old stone.",
        "The floor gives slightly underfoot.",
        "A low hum vibrates through fungal mass.",
        "Translucent stalks sway without wind.",
        "Caps open and close in slow rhythm.",
        "Damp warmth radiates from the growth.",
        "Tiny lights scatter as spores drift.",
        "The air tastes faintly of mushroom.",
        "Slime trails gleam on the rock.",
    ],
    3: [
        "Heat shimmers above cracked obsidian.",
        "Lava veins pulse with dull orange light.",
        "Sulfur stings the back of the throat.",
        "Basalt crunches underfoot.",
        "Embers drift upward from fissures.",
        "The air scorches with each breath.",
        "Obsidian shards glint in firelight.",
        "Steam hisses from a crack in the floor.",
        "Ash coats every horizontal surface.",
        "A distant roar carries through stone.",
        "Molten rock glows behind thin walls.",
        "Charred timber frames sag overhead.",
        "The ground radiates uncomfortable heat.",
        "Cinders crackle in cooling pools.",
        "Smoke curls from gaps between stones.",
    ],
    4: [
        "Gears grind somewhere behind the walls.",
        "Iron plates shift and lock into place.",
        "Oil drips from overhead mechanisms.",
        "Riveted panels line every surface.",
        "The floor vibrates with hidden machinery.",
        "Pistons hiss in distant corridors.",
        "Rust flakes fall from turning gears.",
        "A metallic tang coats every breath.",
        "Chains rattle in unseen shafts.",
        "The walls are warm from friction.",
        "Clockwork ticks in steady rhythm.",
        "Steam vents whistle at intervals.",
        "Bolts strain against warped metal plates.",
        "Iron dust hangs in the stale air.",
        "A low mechanical drone never stops.",
    ],
    5: [
        "Toxic vapor seeps from cracked earth.",
        "Dead roots claw from blackened soil.",
        "The air burns faintly in the throat.",
        "Puddles of dark liquid reflect nothing.",
        "Corrosion eats through old stone.",
        "A sour chemical tang stings the eyes.",
        "Withered growths crumble at a touch.",
        "The ground is spongy and unstable.",
        "Discolored streaks mark every surface.",
        "Nothing living grows here anymore.",
        "A thin green haze clings to the floor.",
        "Bones dissolve slowly in acid pools.",
        "The stench of decay is overpowering.",
        "Blistered stone peels in dry sheets.",
        "Stagnant water bubbles faintly.",
    ],
    6: [
        "Crystals hum at frequencies felt, not heard.",
        "Light refracts into impossible colors.",
        "Fractured prisms scatter pale rainbows.",
        "Crystal formations vibrate faintly.",
        "Ice-cold surfaces gleam in all directions.",
        "A faint chime rings from the walls.",
        "Mineral growths jut from every surface.",
        "The air crackles with static charge.",
        "Geode formations split open underfoot.",
        "Pale light pulses through crystal veins.",
        "Sound echoes strangely off faceted walls.",
        "Frozen condensation coats the crystals.",
        "The temperature drops sharply near the walls.",
        "Prismatic light dances without a source.",
        "Sharp formations force careful steps.",
    ],
    7: [
        "Darkness thickens like smoke ahead.",
        "Shadows move without a source.",
        "The passage narrows to a squeeze.",
        "Sound dies within a few paces.",
        "The dark seems to press inward.",
        "A cold draft pushes from unseen gaps.",
        "Walls close in from both sides.",
        "The ceiling drops lower with each step.",
        "Absolute stillness fills the narrows.",
        "Your torch barely reaches the next wall.",
        "Something scrapes in the dark behind you.",
        "The air grows thin and stale.",
        "Echoes return wrong, distorted.",
        "The floor tilts at unsettling angles.",
        "Every shadow could hide a threat.",
    ],
    8: [
        "Absolute silence fills the dark.",
        "A cold beyond temperature seeps inward.",
        "Light bends strangely near the walls.",
        "The air feels thin and brittle.",
        "Reality thins at the edges.",
        "Starlight leaks through cracks in nothing.",
        "The walls absorb all warmth and sound.",
        "Space folds wrong at the periphery.",
        "Nothing reflects. Nothing echoes.",
        "The ground feels uncertain underfoot.",
        "A hum below hearing vibrates the bones.",
        "Gravity shifts subtly, pulling sideways.",
        "The void breathes. Slowly.",
        "Distance has no meaning here.",
        "The end of everything waits ahead.",
    ],
}

# Per-epoch floor sub-themes — 4 variants per floor for DummyBackend
_FLOOR_SUB_THEMES = {
    1: [
        {
            "floor_name": "Drowned Corridors",
            "atmosphere": "Black water pools in every doorway. The walls weep.",
            "narrative_beat": "The descent begins — something drove these halls underwater.",
            "floor_transition": "Water rises around your ankles. The air turns cold and damp.",
        },
        {
            "floor_name": "Collapsed Undercroft",
            "atmosphere": "Broken stone and dust. Every step echoes twice.",
            "narrative_beat": "Old foundations crumble — this place was buried on purpose.",
            "floor_transition": "Stone groans overhead. Dust sifts down as you descend.",
        },
        {
            "floor_name": "Rusted Waterworks",
            "atmosphere": "Iron pipes line the walls, weeping rust-red water.",
            "narrative_beat": "Ancient machinery failed here — the flood was no accident.",
            "floor_transition": "Rusted gears creak. Water drips from corroded pipes above.",
        },
        {
            "floor_name": "Silted Crypts",
            "atmosphere": "Sand and silt fill the lower passages. Bones jut from the mud.",
            "narrative_beat": "The dead were never meant to be found again.",
            "floor_transition": "The floor turns to wet silt. Old bones shift underfoot.",
        },
    ],
    2: [
        {
            "floor_name": "Luminous Rot",
            "atmosphere": "Everything glows. The light comes from decay.",
            "narrative_beat": "Life thrives here, twisted — the fungus feeds on something below.",
            "floor_transition": "Pale light blooms from the walls. The air thickens with spores.",
        },
        {
            "floor_name": "Mycelial Sprawl",
            "atmosphere": "White threads web every surface. The floor breathes.",
            "narrative_beat": "The network is alive and aware — it's been growing for centuries.",
            "floor_transition": "Tendrils of fungus reach across the threshold. Warmth radiates.",
        },
        {
            "floor_name": "Spore Hollows",
            "atmosphere": "Clouds of spores drift in slow currents. Colors shift.",
            "narrative_beat": "The spores carry memories — breathe too deep and see the past.",
            "floor_transition": "A curtain of spores parts as you enter. Colors swirl.",
        },
        {
            "floor_name": "Bioluminescent Maze",
            "atmosphere": "Pulsing caps light branching paths. The glow follows movement.",
            "narrative_beat": "The maze reshapes itself — the fungus learns from those who enter.",
            "floor_transition": "Mushroom caps flare bright, then dim. The path ahead glows.",
        },
    ],
    3: [
        {
            "floor_name": "Slag Furnaces",
            "atmosphere": "Heat hammers down. Molten channels cut the floor.",
            "narrative_beat": "Someone built forges here — the fires never stopped.",
            "floor_transition": "Heat hits like a wall. The stone beneath glows dull orange.",
        },
        {
            "floor_name": "Obsidian Crucible",
            "atmosphere": "Glass-black stone reflects firelight in every direction.",
            "narrative_beat": "The crucible was meant to contain something — it's cracking.",
            "floor_transition": "Obsidian crunches underfoot. Embers drift upward from below.",
        },
        {
            "floor_name": "Cinder Tunnels",
            "atmosphere": "Ash coats everything. Small fires burn in the walls.",
            "narrative_beat": "The tunnels are scars — something burned through solid rock.",
            "floor_transition": "Ash swirls around your feet. The temperature spikes.",
        },
        {
            "floor_name": "Magma Veins",
            "atmosphere": "Lava pulses through cracks like a heartbeat. The air shimmers.",
            "narrative_beat": "The veins lead deeper — the source of heat is alive.",
            "floor_transition": "Orange light seeps from every crack. The ground pulses with heat.",
        },
    ],
    4: [
        {
            "floor_name": "Grinding Works",
            "atmosphere": "Gears turn endlessly. The whole floor is a machine.",
            "narrative_beat": "Someone built this to keep running forever — but why?",
            "floor_transition": "Metal clangs underfoot. The walls vibrate with hidden machinery.",
        },
        {
            "floor_name": "Iron Maze",
            "atmosphere": "Riveted corridors shift and reconfigure. The path changes.",
            "narrative_beat": "The labyrinth was designed to trap, not to be solved.",
            "floor_transition": "Iron panels slam into place behind you. The maze reconfigures.",
        },
        {
            "floor_name": "Clockwork Depths",
            "atmosphere": "Ticking fills the air. Every surface moves on hidden tracks.",
            "narrative_beat": "The clockwork counts down to something. It always has been.",
            "floor_transition": "Gears mesh and separate overhead. The ticking grows louder.",
        },
        {
            "floor_name": "The Foundry",
            "atmosphere": "Molten metal flows through channels. Hammers fall on nothing.",
            "narrative_beat": "The foundry still forges — but the smiths are long dead.",
            "floor_transition": "Heat rises from below. The clang of phantom hammers echoes.",
        },
    ],
    5: [
        {
            "floor_name": "Acid Flats",
            "atmosphere": "Chemical burns mark every surface. The air itself corrodes.",
            "narrative_beat": "Something was dissolved here — deliberately, completely.",
            "floor_transition": "The air turns acrid. Your eyes water. Nothing grows here.",
        },
        {
            "floor_name": "Withered Expanse",
            "atmosphere": "Dead roots and bleached bone. Life tried here and lost.",
            "narrative_beat": "The blight spread from below — it's still spreading.",
            "floor_transition": "Color drains from everything. The ground crumbles underfoot.",
        },
        {
            "floor_name": "Toxic Sinkhole",
            "atmosphere": "Pools of dark liquid bubble. The fumes burn.",
            "narrative_beat": "The poison seeps upward — the source is deeper still.",
            "floor_transition": "A chemical stench hits like a wall. Visibility drops.",
        },
        {
            "floor_name": "Corroded Hollow",
            "atmosphere": "Metal and stone dissolve alike. Nothing lasts here.",
            "narrative_beat": "Corrosion is the point — something here unmakes things.",
            "floor_transition": "Rust and decay coat every surface. The walls weep acid.",
        },
    ],
    6: [
        {
            "floor_name": "Crystalline Void",
            "atmosphere": "Silence. Light fractures through impossible geometry.",
            "narrative_beat": "The crystals remember — touch one and see the past.",
            "floor_transition": "Sound dies. Crystal formations hum at the edge of hearing.",
        },
        {
            "floor_name": "Frozen Gallery",
            "atmosphere": "Ice coats every crystal. The cold is absolute.",
            "narrative_beat": "The gallery preserves what should have been forgotten.",
            "floor_transition": "Temperature plummets. Your breath crystallizes instantly.",
        },
        {
            "floor_name": "Prismatic Depths",
            "atmosphere": "Light splits into colors that shouldn't exist.",
            "narrative_beat": "The prisms show other places — or other times.",
            "floor_transition": "Rainbow light floods from below. The crystals sing.",
        },
        {
            "floor_name": "Geode Cathedral",
            "atmosphere": "Massive crystal formations arch overhead like ribs.",
            "narrative_beat": "The cathedral was grown, not built. It's still growing.",
            "floor_transition": "Crystal spires tower above. The air hums with resonance.",
        },
    ],
    7: [
        {
            "floor_name": "The Narrows",
            "atmosphere": "Walls press close. Every shadow hides something.",
            "narrative_beat": "The gauntlet is a test — only the worthy pass through.",
            "floor_transition": "The passage tightens. Darkness presses from all sides.",
        },
        {
            "floor_name": "Umbral Passage",
            "atmosphere": "Light cannot hold here. The dark is hungry.",
            "narrative_beat": "Shadows are alive here — and they remember who passes.",
            "floor_transition": "Your light dims to a flicker. The shadows lean closer.",
        },
        {
            "floor_name": "Shadow Run",
            "atmosphere": "The ceiling drops. The walls close. Forward is the only option.",
            "narrative_beat": "This was built as a gauntlet — something guards what lies below.",
            "floor_transition": "The corridor narrows sharply. No room to turn back.",
        },
        {
            "floor_name": "Twilight Choke",
            "atmosphere": "Dim light from no source. The air is thick and still.",
            "narrative_beat": "The choke filters the weak from the strong. Always has.",
            "floor_transition": "Half-light and silence. The space compresses around you.",
        },
    ],
    8: [
        {
            "floor_name": "Null Sanctum",
            "atmosphere": "Space folds wrong. Distances lie. The dark watches back.",
            "narrative_beat": "The sanctum exists outside the rules — reality is optional here.",
            "floor_transition": "The air grows thin and brittle. Shadows move without sources.",
        },
        {
            "floor_name": "Fracture Point",
            "atmosphere": "Reality splinters. Light and dark trade places without warning.",
            "narrative_beat": "This is where it broke — the fracture goes all the way down.",
            "floor_transition": "Light bends. Your shadow stretches in the wrong direction.",
        },
        {
            "floor_name": "Abyssal Threshold",
            "atmosphere": "Cold beyond temperature. The walls are not stone.",
            "narrative_beat": "The threshold leads nowhere and everywhere — the final test.",
            "floor_transition": "A cold beyond temperature seeps into your bones. The end waits.",
        },
        {
            "floor_name": "The Terminus",
            "atmosphere": "Nothing. Then everything. The bottom of all things.",
            "narrative_beat": "The Warden waits where the world ends.",
            "floor_transition": "Reality thins to nothing. You stand at the edge of the void.",
        },
    ],
}

# Objects of interest (things to notice in rooms)
_ROOM_OBJECTS = [
    "Broken chains hang from one wall.",
    "An old sconce holds no torch.",
    "Scratches mark the stone floor.",
    "A collapsed pillar blocks one corner.",
    "Tool marks score the ceiling.",
    "Faded carvings line the doorway.",
    "A dry fountain sits in the center.",
    "Iron rings are set into the wall.",
    "Rubble fills one side of the room.",
    "A stone bench sits against the wall.",
    "An empty weapon rack stands here.",
    "Debris covers most of the floor.",
    "A narrow ledge runs along one wall.",
    "The walls bear old torch marks.",
    "A crumbling arch frames the passage.",
]

# Monster names by tier
_MONSTER_NAMES = {
    1: [
        "Giant Rat", "Cave Spider", "Goblin Scout", "Mud Crawler",
        "Bone Rat", "Tunnel Snake", "Rot Grub", "Shadow Imp",
    ],
    2: [
        "Skeleton Guard", "Slime Horror", "Feral Ghoul", "Stone Beetle",
        "Fungal Shambler", "Tomb Wight", "Poison Drake", "Crypt Lurker",
    ],
    3: [
        "Fire Drake", "Obsidian Golem", "Magma Worm", "Ember Knight",
        "Lava Serpent", "Ash Wraith", "Forge Guardian", "Cinder Beast",
    ],
    4: [
        "Void Walker", "Crystal Lich", "Shadow Sentinel", "Null Warden",
        "Prismatic Shade", "Abyssal Horror", "Dark Construct", "Frost Revenant",
    ],
    5: [
        "Ancient Wyrm", "Void Titan", "Crystal Colossus", "Shadow Overlord",
        "Null Devourer", "Abyssal Lord", "Dark Archon", "Frost Monarch",
    ],
}

# Vault room templates
_VAULT_TYPES = [
    ("treasure", "A locked chest gleams behind a grate."),
    ("puzzle", "Strange symbols cover every surface."),
    ("shrine", "An altar hums with residual power."),
    ("cache", "Supplies are stacked in a hidden nook."),
    ("boss_chamber", "The air thrums with menace."),
]

# Trap types
_TRAP_TYPES = ["physical", "status", "environmental"]
_TRAP_DESCS = {
    "physical": "A spike pit spans the corridor ahead.",
    "status": "Green gas seeps from vents in the wall.",
    "environmental": "The ceiling sags dangerously here.",
}

# NPC names and context
_NPC_DIALOGUE = {
    "grist": {
        "greeting": [
            "Grist slides a drink across the bar.",
            "Grist nods. 'What'll it be?'",
            "Grist wipes the counter. 'Still alive, eh?'",
            "'Back already?' Grist grins.",
            "Grist: 'Another day. Walls shifted last night.'",
            "'Epoch's turning. I can feel it.' Grist pours.",
        ],
        "hint": [
            "Grist: 'I heard something about Floor {floor}.'",
            "Grist: 'Explorers found something {direction} on Floor {floor}.'",
            "Grist: 'The {theme} holds secrets.'",
            "Grist: 'The builder left something on Floor {floor}.'",
        ],
        "recap": [
            "Grist: 'While you were away: {summary}'",
            "Grist: 'Things happened. {summary}'",
            "Grist: 'The Darkcragg remembers. {summary}'",
        ],
    },
    "maren": {
        "greeting": [
            "Maren inspects your wounds.",
            "Maren: 'You look terrible. Sit down.'",
            "'Hold still.' Maren reaches for bandages.",
            "Maren checks her supplies. 'Who's next?'",
            "'The Depths sent you back early.' Maren frowns.",
        ],
    },
    "torval": {
        "greeting": [
            "Torval arranges wares on the counter.",
            "Torval: 'Browse freely. Break it, buy it.'",
            "'Fresh stock today.' Torval gestures widely.",
            "'Void-grade stock today.' Torval beams.",
            "Torval: 'The Caverns melted my last shipment.'",
        ],
    },
    "whisper": {
        "greeting": [
            "Whisper barely acknowledges your presence.",
            "Whisper turns a yellowed page slowly.",
            "'Hmm.' Whisper does not look up.",
            "Whisper traces a symbol in the dust.",
            "'...the pattern shifts.' Whisper goes quiet.",
        ],
        "hint": [
            "Whisper: 'The {theme} conceals much.'",
            "Whisper: '{direction} of Floor {floor}, something waits.'",
            "Whisper: 'Power lies dormant in the {theme}.'",
            "Whisper: 'The builder's mark. Floor {floor}. {direction}.'",
        ],
    },
}

# Riddle templates (answer word → riddle text)
_RIDDLE_TEMPLATES = [
    ("fire", "What consumes all it touches yet has no hands?"),
    ("shadow", "What follows you always but cannot be held?"),
    ("echo", "What speaks without a mouth and dies in silence?"),
    ("time", "What moves without legs and waits for no one?"),
    ("darkness", "What fills a room without weight or shape?"),
    ("wind", "What howls without a throat?"),
    ("stone", "What endures all blows yet never bleeds?"),
    ("water", "What carves mountains yet slips through fingers?"),
    ("silence", "What grows louder the less there is?"),
    ("ice", "What holds firm yet melts at a touch?"),
]

# Bounty description templates
_BOUNTY_TEMPLATES = [
    "Slay the {monster} on Floor {floor}.",
    "Hunt the {monster} in the {theme}.",
    "Destroy the {monster} lurking below.",
    "End the {monster} threat on Floor {floor}.",
    "Eliminate the {monster} from Floor {floor}.",
]

# Boss names by floor
_BOSS_NAMES = {
    1: ["Stone Warden", "Rat King", "Flood Guardian", "Crypt Sentinel"],
    2: ["Spore Tyrant", "Mycelial Overmind", "Fungal Colossus", "Rot Monarch"],
    3: ["Forge Master", "Magma Lord", "Ember Titan", "Obsidian Wyrm"],
    4: ["Iron Juggernaut", "Gear Tyrant", "Clockwork Abom.", "Steel Sentinel"],
    5: ["Blight Lord", "Acid Hydra", "Toxic Colossus", "Corrosion King"],
    6: ["Crystal Archon", "Frost Monarch", "Prismatic Wyrm", "Geode Titan"],
    7: ["Shadow Overlord", "Umbral Stalker", "Dark Watcher", "Night Tyrant"],
    8: ["The Warden", "Void Wyrm", "Null Emperor", "The End"],
}

# Breach zone names
_BREACH_NAMES = [
    "The Fracture", "The Rift", "The Schism", "The Wound",
    "The Tear", "The Hollow", "The Split", "The Breach",
]


_TOWN_ROOM_NAMES = [
    "The Last Ember", "Maren's Clinic", "Torval's Trading Post", "Whisper's Alcove",
    "Dusty Alley", "Cobblestone Path", "Market Square", "Old Well",
    "Crumbling Wall", "Iron Gate", "Lantern Row", "Ash Garden",
    "Broken Fountain", "Stone Bench", "Ember Street", "Charcoal Lane",
    "Collapsed Archway", "Mossy Corner", "Watchtower Base", "Root Cellar",
    "Dried Canal", "Scaffold Walk", "Rubble Pile", "Merchant Row", "Quiet Nook",
]

_TOWN_DESCRIPTIONS = {
    "grist": "Smoke and amber light. The bar stretches across the back wall. A trapdoor leads down.",
    "maren": "Clean linens and the smell of herbs. Maren's tools line the shelves.",
    "torval": "Crates and barrels stacked high. Torval's wares gleam in lamplight.",
    "whisper": "Shadows pool in the corners. Old books and strange symbols cover the walls.",
    None: [
        "Worn cobblestones underfoot. The buildings lean close overhead.",
        "A narrow lane between soot-stained walls. Quiet here.",
        "Cracked flagstones and dry weeds. The town feels old.",
        "Faded shop fronts line the path. Most are boarded up.",
        "A crossroads of packed earth. Boot prints everywhere.",
        "The street widens here. A dry fountain marks the center.",
        "Rubble fills one side. The other stands weathered but whole.",
        "Lamplight spills from a high window. Otherwise, shadow.",
        "A low wall separates the path from overgrown gardens.",
        "Stone steps lead nowhere. The upper floor collapsed long ago.",
        "Wind whistles through gaps in the stonework.",
        "A quiet corner where the noise of the bar barely reaches.",
        "Ash dusts every surface. The air is still and warm.",
        "Iron rings are bolted into the wall. Old hitching posts.",
        "The smell of earth and old stone. Nothing stirs.",
        "A sheltered nook between two leaning walls.",
        "Broken tiles crunch underfoot. The ceiling is open sky.",
        "A narrow passage barely wide enough for two.",
        "Scaffolding props up a sagging wall. It looks recent.",
        "The remains of a market stall. Empty crates and rope.",
        "A patch of stubborn moss brightens the grey stone.",
    ],
}


class DummyBackend(BackendInterface):
    """Template-based backend that produces valid, playable content without LLM."""

    def __init__(self):
        self._used_room_names: set[str] = set()
        self._town_name_index: int = 0

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        """Return template-based text. Ignores the prompt, uses context hints."""
        # Return a generic sensory line — callers use specialized methods instead
        return random.choice(_FLOOR_SENSORY.get(1, _FLOOR_SENSORY[1]))

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        """Return a random NPC dialogue snippet. No LLM call."""
        # Try to detect which NPC from system prompt
        for npc in ("grist", "maren", "torval", "whisper"):
            if npc in system.lower():
                lines = _NPC_DIALOGUE.get(npc, {}).get("greeting", [])
                if lines:
                    return random.choice(lines)[:LLM_OUTPUT_CHAR_LIMIT]
        return "The NPC nods thoughtfully."[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_room_name(self, floor: int) -> str:
        """Generate a unique room name for a floor."""
        names = _FLOOR_NAMES.get(floor, _FLOOR_NAMES[1])
        for _ in range(50):
            name = f"{random.choice(names['prefix'])} {random.choice(names['suffix'])}"
            if name not in self._used_room_names:
                self._used_room_names.add(name)
                return name
        # Fallback with numeric suffix
        base = f"{random.choice(names['prefix'])} {random.choice(names['suffix'])}"
        name = f"{base} {len(self._used_room_names)}"
        self._used_room_names.add(name)
        return name

    def generate_room_description(self, floor: int, name: str, is_vault: bool = False,
                                  vault_type: str = "", floor_theme: dict = None) -> str:
        """Generate a full room description under 150 chars."""
        sensory = random.choice(_FLOOR_SENSORY.get(floor, _FLOOR_SENSORY[1]))
        if is_vault and vault_type:
            for vt, vdesc in _VAULT_TYPES:
                if vt == vault_type:
                    desc = f"{sensory} {vdesc}"
                    return desc[:LLM_OUTPUT_CHAR_LIMIT]
        obj = random.choice(_ROOM_OBJECTS)
        desc = f"{sensory} {obj}"
        return desc[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_room_description_short(self, floor: int, name: str,
                                       floor_theme: dict = None) -> str:
        """Generate abbreviated room description for revisits."""
        sensory = random.choice(_FLOOR_SENSORY.get(floor, _FLOOR_SENSORY[1]))
        return sensory[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_town_room_name(self, row: int, col: int, npc_name: str = None) -> str:
        """Generate a town room name for Floor 0."""
        if npc_name == "grist":
            return "The Last Ember"
        if npc_name == "maren":
            return "Maren's Clinic"
        if npc_name == "torval":
            return "Torval's Trading Post"
        if npc_name == "whisper":
            return "Whisper's Alcove"
        # Use sequential names from pool for variety
        idx = self._town_name_index % len(_TOWN_ROOM_NAMES)
        name = _TOWN_ROOM_NAMES[idx]
        # Skip NPC room names already assigned
        while name in ("The Last Ember", "Maren's Clinic", "Torval's Trading Post", "Whisper's Alcove"):
            self._town_name_index += 1
            idx = self._town_name_index % len(_TOWN_ROOM_NAMES)
            name = _TOWN_ROOM_NAMES[idx]
        self._town_name_index += 1
        return name

    def generate_town_description(self, name: str, npc_name: str = None) -> str:
        """Generate a town room description for Floor 0."""
        if npc_name and npc_name in _TOWN_DESCRIPTIONS:
            return _TOWN_DESCRIPTIONS[npc_name][:LLM_OUTPUT_CHAR_LIMIT]
        generic = _TOWN_DESCRIPTIONS[None]
        return random.choice(generic)[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_monster_name(self, tier: int, floor_theme: dict = None) -> str:
        """Pick a monster name for a tier."""
        names = _MONSTER_NAMES.get(tier, _MONSTER_NAMES[1])
        return random.choice(names)

    def generate_bounty_description(self, monster_name: str, floor: int, theme: str) -> str:
        """Generate a bounty briefing."""
        template = random.choice(_BOUNTY_TEMPLATES)
        desc = template.format(monster=monster_name, floor=floor, theme=theme)
        return desc[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_boss_name(self, floor: int, floor_theme: dict = None) -> str:
        """Pick a boss name for a floor."""
        names = _BOSS_NAMES.get(floor, _BOSS_NAMES[1])
        return random.choice(names)

    def generate_hint(self, tier: int, floor: int, room_name: str = "",
                      direction: str = "", theme: str = "") -> str:
        """Generate a secret hint at the specified tier."""
        if tier == 1:
            hints = [
                f"Secrets hide in the {theme}.",
                f"Floor {floor} holds something unseen.",
                f"The {theme} conceals more than it shows.",
            ]
        elif tier == 2:
            hints = [
                f"The {direction} branch of Floor {floor} hides something.",
                f"Look carefully in the {theme}, {direction} side.",
                f"Something waits {direction} on Floor {floor}.",
            ]
        else:  # tier 3
            hints = [
                f"Search near {room_name} closely.",
                f"The marks near {room_name} point the way.",
                f"What's hidden in {room_name} rewards attention.",
            ]
        return random.choice(hints)[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_riddle(self) -> tuple[str, str]:
        """Generate a riddle and its answer."""
        answer, text = random.choice(_RIDDLE_TEMPLATES)
        return text[:LLM_OUTPUT_CHAR_LIMIT], answer

    def generate_npc_dialogue(self, npc: str, context: str, **kwargs) -> str:
        """Generate NPC dialogue."""
        lines = _NPC_DIALOGUE.get(npc, {}).get(context, [])
        if not lines:
            return f"{npc.title()} says nothing."
        template = random.choice(lines)
        try:
            return template.format(**kwargs)[:LLM_OUTPUT_CHAR_LIMIT]
        except KeyError:
            return template[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_breach_name(self) -> str:
        """Generate a breach zone name."""
        return random.choice(_BREACH_NAMES)

    def generate_narrative_skin(self, mode: str, theme: str) -> dict:
        """Generate narrative skin for an endgame mode."""
        return {
            "title": f"The {theme}"[:30],
            "description": f"The {theme} awaits. Steel yourself."[:LLM_OUTPUT_CHAR_LIMIT],
            "broadcasts": [
                f"Progress in the {theme}." [:LLM_OUTPUT_CHAR_LIMIT],
                f"The {theme} shudders."[:LLM_OUTPUT_CHAR_LIMIT],
            ],
        }

    def generate_spell_names(self, theme: str = "") -> list[str]:
        """Generate 3 spell names for the epoch (each ≤20 chars)."""
        from config import DUMMY_SPELL_NAMES
        return random.sample(DUMMY_SPELL_NAMES, 3)

    def generate_floor_themes(self) -> dict[int, dict]:
        """Pick one random sub-theme variant per floor from static pool."""
        from config import NUM_FLOORS
        result = {}
        for floor in range(1, NUM_FLOORS + 1):
            variants = _FLOOR_SUB_THEMES.get(floor, _FLOOR_SUB_THEMES[1])
            result[floor] = dict(random.choice(variants))
        return result

    def generate_lore_fragment(self, floor: int) -> str:
        """Generate a lore fragment for a room (≤80 chars)."""
        from config import DUMMY_LORE_FRAGMENTS
        return random.choice(DUMMY_LORE_FRAGMENTS)[:80]

    def generate_atmospheric_broadcast(self, theme: str) -> str:
        """Generate a generic atmospheric broadcast message."""
        msgs = [
            f"The dungeon groans. The {theme} shifts.",
            "Dust falls from the ceiling. Something moved.",
            "A distant scream echoes and fades.",
            "The torches flicker in unison.",
            "Cold air rushes from below.",
            "The ground trembles briefly.",
            "An eerie silence falls.",
            "Shadows lengthen without cause.",
            "The Darkcragg groans. Something below stirs.",
            "Ancient symbols flicker in the walls.",
            "The air tastes of old stone and older memory.",
            "A low hum rises from the deep floors.",
            "The Breach pulses faintly. Waiting.",
        ]
        return random.choice(msgs)[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_epoch_preamble(self, endgame_mode: str, breach_type: str,
                               narrative_theme: str = "",
                               floor_themes: dict = None,
                               spell_names: list = None) -> str:
        """Static preamble for DummyBackend."""
        return (
            "It started below the cellars. A sound too low to hear — felt instead "
            "in the teeth, in the joints of the fingers, in the place behind the "
            "eyes where headaches live. The bottles behind the bar clinked against "
            "each other once. The fire flattened. The air pressure dropped so fast "
            "that ears popped across the room. Then the floor moved.\n\n"
            "Not an earthquake. Earthquakes don't choose a direction. This rolled "
            "from below, a single wave through solid stone, and when it passed the "
            "building groaned in a voice that buildings should not have. Dust sifted "
            "from between the ceiling beams. A crack opened in the wall behind the "
            "bar — thin as a hair, running floor to ceiling. It hadn't been there "
            "a moment ago.\n\n"
            "Grist stopped pouring mid-glass. Maren put down the bandage she was "
            "rolling and counted the jars on her shelf — twice. Torval checked the "
            "iron bolt on his storeroom door, then checked it again. In the corner "
            "where the lamplight doesn't reach, Whisper pressed one palm flat against "
            "the wall and held it there for a long time. \"It's rearranging,\" the "
            "voice said. \"The bones of it are moving.\"\n\n"
            "Then it stopped. The silence after was worse than the tremor — thick "
            "and pressurized, the silence of stone settling into a shape it has "
            "chosen. Dust hung motionless in the air. The crack in the wall did not "
            "close.\n\n"
            "The stairwell door is open. Cold air drifts up from below, carrying "
            "the smell of wet rock and something else — something organic, faintly "
            "sweet, like fruit left too long in a closed room. The first few steps "
            "are visible in the torchlight, worn smooth. Beyond the light, the stairs "
            "curve into a darkness that feels deeper than it did thirty days ago.\n\n"
            "The stairs are open."
        )

    def generate_epoch_announcements(self, endgame_mode: str,
                                     breach_type: str,
                                     narrative_theme: str = "",
                                     epoch_name: str = "") -> list[str]:
        """Static epoch announcements for DummyBackend."""
        return [
            "The ground trembles. The air shifts. Something ancient stirs below.",
            "Smoke curls from the chimney. The bar smells of char and old wood. Grist is pouring.",
            "The stairs are open.",
        ]


# ── Real LLM Backends ─────────────────────────────────────────────────────


class AnthropicBackend(BackendInterface):
    """Claude API backend."""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("MMUD_ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("MMUD_LLM_MODEL", "claude-sonnet-4-5-20250929")
        if not self.api_key:
            raise ValueError("Anthropic API key required")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError("Install 'anthropic' package: pip install anthropic")
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        return response.content[0].text.strip()


class OpenAIBackend(BackendInterface):
    """OpenAI-compatible API backend."""

    def __init__(self, api_key=None, model=None, base_url=None):
        self.api_key = api_key or os.environ.get("MMUD_OPENAI_API_KEY", "")
        self.model = model or os.environ.get("MMUD_LLM_MODEL", "gpt-4o-mini")
        self.base_url = base_url or os.environ.get("MMUD_OPENAI_BASE_URL")
        if not self.api_key:
            raise ValueError("OpenAI API key required")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError("Install 'openai' package: pip install openai")
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        chat_messages = [{"role": "system", "content": system}] + messages
        response = self.client.chat.completions.create(
            model=self.model,
            messages=chat_messages,
        )
        return response.choices[0].message.content.strip()


class GoogleBackend(BackendInterface):
    """Gemini API backend using google-genai SDK."""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("MMUD_GOOGLE_API_KEY", "")
        self.model = model or os.environ.get("MMUD_LLM_MODEL", "gemini-2.0-flash")
        if not self.api_key:
            raise ValueError("Google API key required")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return response.text.strip()

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        from google.genai import types
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part(text=msg["content"])],
            ))
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
            ),
        )
        # Debug: log full response structure to diagnose truncation
        candidate = response.candidates[0]
        parts = candidate.content.parts
        logger.info(
            f"Gemini response: finish_reason={candidate.finish_reason}, "
            f"parts={len(parts)}, "
            f"usage={{cand={response.usage_metadata.candidates_token_count}, "
            f"think={getattr(response.usage_metadata, 'thoughts_token_count', None)}}}"
        )
        for i, part in enumerate(parts):
            has_thought = getattr(part, 'thought', False)
            logger.info(f"  part[{i}]: thought={has_thought}, text={repr(part.text[:100]) if part.text else 'None'}")
        return response.text.strip()


# ── Validation Layer ───────────────────────────────────────────────────────


class ValidationLayer:
    """Wraps any backend and enforces content rules.

    - 150-char limit
    - No forbidden verbs in hint text
    - Retries on failure up to max_retries before falling back to DummyBackend
    """

    def __init__(self, backend: BackendInterface, max_retries: int = 3):
        self.backend = backend
        self.fallback = DummyBackend()
        self.max_retries = max_retries

    def generate(self, prompt: str, max_chars: int = LLM_OUTPUT_CHAR_LIMIT,
                 is_hint: bool = False) -> str:
        """Generate text with validation.

        Args:
            prompt: Generation prompt.
            max_chars: Maximum character count.
            is_hint: If True, validate against forbidden action verbs.

        Returns:
            Validated text string.
        """
        for attempt in range(self.max_retries):
            try:
                text = self.backend.complete(prompt)
                text = text.strip()

                # Length check
                if len(text) > max_chars:
                    text = text[:max_chars - 3] + "..."

                # Forbidden verb check for hints
                if is_hint and self._has_forbidden_verbs(text):
                    logger.warning(
                        f"Hint contains forbidden verb (attempt {attempt + 1}): {text}"
                    )
                    continue

                return text

            except Exception as e:
                logger.warning(f"Backend error (attempt {attempt + 1}): {e}")

        # Fallback to dummy
        logger.warning("Falling back to DummyBackend after validation failures")
        return self.fallback.complete(prompt)[:max_chars]

    @staticmethod
    def _has_forbidden_verbs(text: str) -> bool:
        """Check if text contains forbidden action verbs."""
        text_lower = text.lower()
        for verb in HINT_FORBIDDEN_VERBS:
            if verb in text_lower:
                return True
        return False

    @staticmethod
    def validate_text(text: str, max_chars: int = LLM_OUTPUT_CHAR_LIMIT,
                      is_hint: bool = False) -> tuple[bool, str]:
        """Validate a text string against rules.

        Returns:
            (valid, error_message)
        """
        if len(text) > max_chars:
            return False, f"Text exceeds {max_chars} chars: {len(text)}"
        if is_hint:
            text_lower = text.lower()
            for verb in HINT_FORBIDDEN_VERBS:
                if verb in text_lower:
                    return False, f"Hint contains forbidden verb: '{verb}'"
        return True, ""


# ── Backend Factory ────────────────────────────────────────────────────────


def _backend_from_config(config: dict) -> BackendInterface:
    """Create a backend instance from a config dict (DB row or test params).

    Args:
        config: Dict with keys: backend, api_key, model, base_url.

    Returns:
        A BackendInterface instance.

    Raises:
        ValueError: If a real backend is requested but api_key is missing.
    """
    name = config.get("backend", "dummy").lower()
    api_key = config.get("api_key", "")
    model = config.get("model", "")
    base_url = config.get("base_url", "")

    if name == "anthropic":
        return AnthropicBackend(api_key=api_key or None, model=model or None)
    elif name == "openai":
        return OpenAIBackend(
            api_key=api_key or None, model=model or None, base_url=base_url or None,
        )
    elif name == "google":
        return GoogleBackend(api_key=api_key or None, model=model or None)
    else:
        return DummyBackend()


def get_backend(db_path: str | None = None) -> BackendInterface:
    """Get the configured LLM backend.

    Checks DB config first (if db_path provided), falls back to env vars.
    """
    if db_path:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM llm_config WHERE id = 1").fetchone()
            conn.close()
            if row and row["backend"] != "dummy":
                return _backend_from_config(dict(row))
            elif row and row["backend"] == "dummy":
                # DB explicitly says dummy — use it, but still allow env override
                env_backend = os.environ.get("MMUD_LLM_BACKEND", "dummy").lower()
                if env_backend != "dummy":
                    return _backend_from_env(env_backend)
                return DummyBackend()
        except Exception as e:
            logger.debug(f"Could not read llm_config from DB: {e}")

    # Env var fallback
    backend_name = os.environ.get("MMUD_LLM_BACKEND", "dummy").lower()
    return _backend_from_env(backend_name)


def _backend_from_env(backend_name: str) -> BackendInterface:
    """Create backend from environment variables."""
    if backend_name == "anthropic":
        return AnthropicBackend()
    elif backend_name == "openai":
        return OpenAIBackend()
    elif backend_name == "google":
        return GoogleBackend()
    else:
        return DummyBackend()


def get_validated_backend(db_path: str | None = None) -> ValidationLayer:
    """Get a validation-wrapped backend."""
    return ValidationLayer(get_backend(db_path=db_path))
