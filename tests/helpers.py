"""Shared test helpers for MMUD."""

import sqlite3

from config import HTL_CHECKPOINTS_PER_FLOOR, NUM_FLOORS
from src.db.database import get_db, init_schema
from src.generation.bossgen import generate_bosses
from src.generation.breachgen import generate_breach
from src.generation.narrative import DummyBackend
from src.generation.secretgen import generate_secrets
from src.generation.worldgen import generate_town, generate_world
from src.models.epoch import create_epoch
from src.systems.endgame_rne import init_escape_run


def generate_test_epoch(
    conn: sqlite3.Connection,
    endgame_mode: str = "hold_the_line",
    breach_type: str = "heist",
) -> dict:
    """Generate a full test epoch with world, bosses, secrets, and breach.

    Creates everything needed for endgame mode testing.

    Args:
        conn: In-memory DB with schema initialized.
        endgame_mode: Endgame mode to configure.
        breach_type: Breach mini-event type.

    Returns:
        Stats dict.
    """
    backend = DummyBackend()

    # Create epoch
    create_epoch(conn, 1, endgame_mode, breach_type)

    # Generate town (Floor 0)
    town_stats = generate_town(conn, backend)

    # Generate world
    world_stats = generate_world(conn, backend)

    # Generate bosses (floor bosses + raid boss pre-roll)
    boss_stats = generate_bosses(conn, backend)

    # Generate breach zone
    breach_stats = generate_breach(conn, backend)

    # Generate secrets (including breach secrets)
    secret_stats = generate_secrets(
        conn, backend, breach_room_ids=breach_stats.get("breach_room_ids", [])
    )

    # Set up HtL checkpoints if mode is hold_the_line
    if endgame_mode == "hold_the_line":
        _setup_htl_checkpoints(conn)

    # Set up escape run if mode is retrieve_and_escape
    if endgame_mode == "retrieve_and_escape":
        init_escape_run(conn)

    conn.commit()

    return {
        "world": world_stats,
        "bosses": boss_stats,
        "breach": breach_stats,
        "secrets": secret_stats,
    }


def _setup_htl_checkpoints(conn: sqlite3.Connection) -> None:
    """Set up HtL checkpoints for each floor."""
    for floor in range(1, NUM_FLOORS + 1):
        num_checkpoints = HTL_CHECKPOINTS_PER_FLOOR.get(floor, 1)

        # Hub checkpoint
        hub = conn.execute(
            "SELECT id FROM rooms WHERE floor = ? AND is_hub = 1 LIMIT 1",
            (floor,),
        ).fetchone()
        if hub:
            conn.execute(
                """INSERT INTO htl_checkpoints (floor, room_id, position)
                   VALUES (?, ?, 'hub')""",
                (floor, hub["id"]),
            )
            # Mark hub as checkpoint
            conn.execute(
                "UPDATE rooms SET is_checkpoint = 1 WHERE id = ?", (hub["id"],)
            )

        if num_checkpoints <= 1:
            continue

        # Stairway checkpoint
        stairway = conn.execute(
            "SELECT id FROM rooms WHERE floor = ? AND is_stairway = 1 LIMIT 1",
            (floor,),
        ).fetchone()
        if stairway:
            conn.execute(
                """INSERT INTO htl_checkpoints (floor, room_id, position)
                   VALUES (?, ?, 'stairway')""",
                (floor, stairway["id"]),
            )
            conn.execute(
                "UPDATE rooms SET is_checkpoint = 1 WHERE id = ?",
                (stairway["id"],),
            )

        if num_checkpoints <= 2:
            continue

        # Midpoint checkpoint — pick a non-hub, non-stairway room
        mid = conn.execute(
            """SELECT id FROM rooms
               WHERE floor = ? AND is_hub = 0 AND is_stairway = 0
               AND is_vault = 0 AND is_breach = 0
               ORDER BY RANDOM() LIMIT 1""",
            (floor,),
        ).fetchone()
        if mid:
            conn.execute(
                """INSERT INTO htl_checkpoints (floor, room_id, position)
                   VALUES (?, ?, 'midpoint')""",
                (floor, mid["id"]),
            )
            conn.execute(
                "UPDATE rooms SET is_checkpoint = 1 WHERE id = ?",
                (mid["id"],),
            )

    conn.commit()
