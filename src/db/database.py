"""
MMUD Database — SQLite connection management and schema initialization.
Single file, WAL mode, parameterized queries only.
"""

import os
import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_db(db_path: str = "mmud.db") -> sqlite3.Connection:
    """Open a connection to the MMUD database.

    Creates the database and runs schema.sql if it doesn't exist.
    Uses WAL mode for concurrent read access.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    exists = os.path.exists(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    if not exists:
        init_schema(conn)

    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Run schema.sql to create all tables and indexes."""
    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)
    conn.commit()


def reset_epoch_tables(conn: sqlite3.Connection) -> None:
    """Drop and recreate epoch-scoped tables for a new wipe cycle.

    Preserves: accounts, titles, hall_of_fame, hall_of_fame_participants.
    Resets everything else.
    """
    epoch_tables = [
        "broadcast_seen", "broadcasts", "player_messages", "mail",
        "epoch_votes", "npc_dialogue", "narrative_skins",
        "breach", "htl_checkpoints",
        "escape_participants", "escape_run",
        "raid_boss_contributors", "raid_boss",
        "bounty_contributors", "bounties",
        "discovery_buffs", "secret_progress", "secrets",
        "inventory", "monsters", "room_exits", "rooms", "items",
        "players", "epoch",
    ]
    for table in epoch_tables:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
