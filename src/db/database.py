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
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")

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
    # Ensure floor_themes table exists (migration for pre-existing DBs)
    conn.execute("""CREATE TABLE IF NOT EXISTS floor_themes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        floor INTEGER NOT NULL,
        floor_name TEXT NOT NULL,
        atmosphere TEXT NOT NULL,
        narrative_beat TEXT NOT NULL,
        floor_transition TEXT NOT NULL
    )""")

    # Ensure floor_progress table exists (migration for pre-existing DBs)
    conn.execute("""CREATE TABLE IF NOT EXISTS floor_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL REFERENCES players(id),
        floor INTEGER NOT NULL,
        boss_killed INTEGER DEFAULT 0,
        boss_killed_at DATETIME,
        UNIQUE(player_id, floor)
    )""")

    # Ensure deepest_floor_reached column exists
    try:
        conn.execute("SELECT deepest_floor_reached FROM players LIMIT 0")
    except Exception:
        conn.execute("ALTER TABLE players ADD COLUMN deepest_floor_reached INTEGER DEFAULT 1")

    # Ensure node_sessions table exists (character auth migration)
    conn.execute("""CREATE TABLE IF NOT EXISTS node_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mesh_id TEXT UNIQUE NOT NULL,
        player_id INTEGER NOT NULL REFERENCES players(id),
        logged_in_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_active DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # Ensure accounts has character_name and password_hash columns
    try:
        conn.execute("SELECT character_name FROM accounts LIMIT 0")
    except Exception:
        conn.execute("ALTER TABLE accounts ADD COLUMN character_name TEXT DEFAULT ''")
    try:
        conn.execute("SELECT password_hash FROM accounts LIMIT 0")
    except Exception:
        conn.execute("ALTER TABLE accounts ADD COLUMN password_hash TEXT DEFAULT ''")

    epoch_tables = [
        "broadcast_seen", "broadcasts", "player_messages", "mail",
        "epoch_votes", "npc_journals", "npc_dialogue", "narrative_skins",
        "breach", "htl_checkpoints",
        "escape_participants", "escape_run",
        "raid_boss_contributors", "raid_boss",
        "bounty_contributors", "bounties",
        "discovery_buffs", "secret_progress", "secrets",
        "inventory", "monsters", "room_exits", "rooms", "items",
        "floor_themes", "floor_progress",
        "node_sessions", "players", "epoch",
    ]
    for table in epoch_tables:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
