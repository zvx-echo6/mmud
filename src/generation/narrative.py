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
            "Void", "Crystal", "Lightless", "Frozen", "Silent",
            "Shattered", "Hollow", "Pale", "Resonant", "Null",
            "Abyssal", "Prismatic", "Fractured", "Ethereal", "Dark",
        ],
        "suffix": [
            "Reach", "Spire", "Sanctum", "Throne", "Nexus",
            "Apex", "Core", "Dome", "Pinnacle", "Gate",
            "Abyss", "Vault", "Shard", "Lattice", "Terminus",
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
        "Absolute silence fills the dark.",
        "Crystals hum at frequencies felt, not heard.",
        "A cold beyond temperature seeps inward.",
        "Light bends strangely near the walls.",
        "Fractured prisms scatter pale rainbows.",
        "The dark seems to press inward.",
        "Crystal formations vibrate faintly.",
        "Sound dies within a few paces.",
        "The air feels thin and brittle.",
        "Shadows move without a source.",
        "A faint chime rings from nowhere.",
        "Ice crystals hang motionless in air.",
        "The walls absorb all warmth.",
        "Reality thins at the edges.",
        "Starlight leaks through crystal veins.",
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
        ],
        "hint": [
            "Grist: 'I heard something about Floor {floor}.'",
            "Grist: 'Explorers found something {direction} on Floor {floor}.'",
            "Grist: 'The {theme} holds secrets.'",
        ],
        "recap": [
            "Grist: 'While you were away: {summary}'",
            "Grist: 'Things happened. {summary}'",
        ],
    },
    "maren": {
        "greeting": [
            "Maren inspects your wounds.",
            "Maren: 'You look terrible. Sit down.'",
            "'Hold still.' Maren reaches for bandages.",
        ],
    },
    "torval": {
        "greeting": [
            "Torval arranges wares on the counter.",
            "Torval: 'Browse freely. Break it, buy it.'",
            "'Fresh stock today.' Torval gestures widely.",
        ],
    },
    "whisper": {
        "greeting": [
            "Whisper barely acknowledges your presence.",
            "Whisper turns a yellowed page slowly.",
            "'Hmm.' Whisper does not look up.",
        ],
        "hint": [
            "Whisper: 'The {theme} conceals much.'",
            "Whisper: '{direction} of Floor {floor}, something waits.'",
            "Whisper: 'Power lies dormant in the {theme}.'",
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
    4: ["The Warden", "Void Wyrm", "Crystal Archon", "Null Emperor"],
}

# Breach zone names
_BREACH_NAMES = [
    "The Fracture", "The Rift", "The Schism", "The Wound",
    "The Tear", "The Hollow", "The Split", "The Breach",
]


class DummyBackend(BackendInterface):
    """Template-based backend that produces valid, playable content without LLM."""

    def __init__(self):
        self._used_room_names: set[str] = set()

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
                                  vault_type: str = "") -> str:
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

    def generate_room_description_short(self, floor: int, name: str) -> str:
        """Generate abbreviated room description for revisits."""
        sensory = random.choice(_FLOOR_SENSORY.get(floor, _FLOOR_SENSORY[1]))
        return sensory[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_monster_name(self, tier: int) -> str:
        """Pick a monster name for a tier."""
        names = _MONSTER_NAMES.get(tier, _MONSTER_NAMES[1])
        return random.choice(names)

    def generate_bounty_description(self, monster_name: str, floor: int, theme: str) -> str:
        """Generate a bounty briefing."""
        template = random.choice(_BOUNTY_TEMPLATES)
        desc = template.format(monster=monster_name, floor=floor, theme=theme)
        return desc[:LLM_OUTPUT_CHAR_LIMIT]

    def generate_boss_name(self, floor: int) -> str:
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
        ]
        return random.choice(msgs)[:LLM_OUTPUT_CHAR_LIMIT]


# ── Real LLM Backends ─────────────────────────────────────────────────────


class AnthropicBackend(BackendInterface):
    """Claude API backend."""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("MMUD_ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("MMUD_LLM_MODEL", "claude-sonnet-4-5-20250929")
        if not self.api_key:
            raise ValueError("Anthropic API key required")

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
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

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        from openai import OpenAI
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        from openai import OpenAI
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = OpenAI(**kwargs)
        chat_messages = [{"role": "system", "content": system}] + messages
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=chat_messages,
        )
        return response.choices[0].message.content.strip()


class GoogleBackend(BackendInterface):
    """Gemini API backend."""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("MMUD_GOOGLE_API_KEY", "")
        self.model = model or os.environ.get("MMUD_LLM_MODEL", "gemini-2.0-flash")
        if not self.api_key:
            raise ValueError("Google API key required")

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(self.model)
        response = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max_tokens},
        )
        return response.text.strip()

    def chat(self, system: str, messages: list[dict], max_tokens: int = 80) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(self.model, system_instruction=system)
        history = []
        for msg in messages[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            history.append({"role": role, "parts": [msg["content"]]})
        chat = model.start_chat(history=history)
        last_msg = messages[-1]["content"] if messages else ""
        response = chat.send_message(
            last_msg,
            generation_config={"max_output_tokens": max_tokens},
        )
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
