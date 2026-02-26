"""
Floor sub-theme generation for MMUD.
Generates per-epoch floor identities with narrative descent arc.
"""

import logging
import sqlite3
from typing import Optional

from config import LLM_OUTPUT_CHAR_LIMIT, NUM_FLOORS
from src.generation.narrative import DummyBackend

logger = logging.getLogger(__name__)


def generate_floor_themes(
    conn: sqlite3.Connection, backend: Optional[DummyBackend] = None,
) -> dict:
    """Generate and store floor sub-themes for the current epoch.

    Args:
        conn: Database connection.
        backend: Narrative backend.

    Returns:
        Stats dict with count of themes generated.
    """
    if backend is None:
        backend = DummyBackend()

    themes = backend.generate_floor_themes()

    # Validate and insert
    for floor in range(1, NUM_FLOORS + 1):
        theme = themes.get(floor)
        if not theme:
            logger.warning(f"Missing floor theme for floor {floor}, using fallback")
            theme = DummyBackend().generate_floor_themes().get(floor, {})
            themes[floor] = theme

        # Enforce char limits
        for field in ("floor_name", "atmosphere", "narrative_beat", "floor_transition"):
            val = theme.get(field, "")
            if len(val) > LLM_OUTPUT_CHAR_LIMIT:
                theme[field] = val[:LLM_OUTPUT_CHAR_LIMIT]

        conn.execute(
            """INSERT INTO floor_themes (floor, floor_name, atmosphere, narrative_beat, floor_transition)
               VALUES (?, ?, ?, ?, ?)""",
            (floor, theme["floor_name"], theme["atmosphere"],
             theme["narrative_beat"], theme["floor_transition"]),
        )

    conn.commit()
    return {"floor_themes": len(themes)}


def get_floor_themes(conn: sqlite3.Connection) -> dict[int, dict]:
    """Load floor themes from DB as {floor_number: theme_dict}.

    Returns empty dict if no floor themes exist (backward compat).
    """
    rows = conn.execute(
        "SELECT floor, floor_name, atmosphere, narrative_beat, floor_transition FROM floor_themes"
    ).fetchall()

    result = {}
    for row in rows:
        result[row["floor"]] = {
            "floor_name": row["floor_name"],
            "atmosphere": row["atmosphere"],
            "narrative_beat": row["narrative_beat"],
            "floor_transition": row["floor_transition"],
        }
    return result
