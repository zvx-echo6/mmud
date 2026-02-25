#!/usr/bin/env python3
"""
Clean epoch reset for MMUD.
Wipes all epoch-scoped data while preserving persistent records.

Preserved:
  - accounts (player identities)
  - titles (earned titles)
  - hall_of_fame + participants (historical records)

Reset:
  - Everything else (players, rooms, monsters, items, secrets, bounties, etc.)

Run: python scripts/epoch_reset.py [db_path]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database import get_db, reset_epoch_tables


def reset_epoch(db_path: str = "mmud.db") -> None:
    """Perform a clean epoch reset."""
    conn = get_db(db_path)

    # Count what we're about to wipe
    counts = {}
    for table in ["rooms", "monsters", "items", "secrets", "bounties", "players"]:
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        counts[table] = row["cnt"]

    # Preserve counts
    acct = conn.execute("SELECT COUNT(*) as cnt FROM accounts").fetchone()
    titles = conn.execute("SELECT COUNT(*) as cnt FROM titles").fetchone()
    hof = conn.execute("SELECT COUNT(*) as cnt FROM hall_of_fame").fetchone()

    print("=== MMUD Epoch Reset ===")
    print(f"  Database: {db_path}")
    print()
    print("Wiping:")
    for table, count in counts.items():
        print(f"  {table}: {count} rows")
    print()
    print("Preserving:")
    print(f"  accounts: {acct['cnt']}")
    print(f"  titles: {titles['cnt']}")
    print(f"  hall_of_fame: {hof['cnt']}")
    print()

    reset_epoch_tables(conn)

    print("Reset complete. Run epoch_generate.py to create a new epoch.")
    conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "mmud.db"
    reset_epoch(db)
