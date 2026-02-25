#!/usr/bin/env python3
"""
Seed a minimal test world for MMUD Phase 1 testing.

Creates:
- 2 floors (4 rooms on floor 1, 3 rooms on floor 2)
- Monsters in some rooms
- Room exits connecting everything
- An epoch record
- A few items in the items table

Run: python -m scripts.seed_test_world [db_path]
"""

import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database import get_db
from src.models.epoch import create_epoch


def seed(db_path: str = "mmud.db") -> None:
    """Seed the test world."""
    conn = get_db(db_path)

    # Create epoch
    create_epoch(
        conn,
        epoch_number=1,
        endgame_mode="hold_the_line",
        breach_type="emergence",
        narrative_theme="The Sunken Halls",
    )

    # === Floor 1: Sunken Halls ===
    # Room 1: Hub
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short,
           is_hub) VALUES (?, ?, ?, ?, ?, ?)""",
        (1, 1, "Sunken Hall",
         "Water drips from cracked stone. Passages lead in all directions. [n,s,e]",
         "Sunken Hall. Dripping water. [n,s,e]",
         1),
    )
    # Room 2: North branch
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short,
           is_hub) VALUES (?, ?, ?, ?, ?, ?)""",
        (2, 1, "Rat Warren",
         "Gnawed bones litter the floor. Chittering echoes from the dark. [s,e]",
         "Rat Warren. Bones and chittering. [s,e]",
         0),
    )
    # Room 3: East branch
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short,
           is_hub) VALUES (?, ?, ?, ?, ?, ?)""",
        (3, 1, "Flooded Passage",
         "Knee-deep water fills this corridor. Something moves beneath. [w,n]",
         "Flooded Passage. Dark water. [w,n]",
         0),
    )
    # Room 4: Stairway down
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short,
           is_hub, is_stairway) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (4, 1, "Crumbling Stair",
         "Worn steps descend into deeper darkness. Cold air rises. [s,d]",
         "Crumbling Stair. Steps going down. [s,d]",
         0, 1),
    )

    # === Floor 2: Fungal Depths ===
    # Room 5: Hub
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short,
           is_hub) VALUES (?, ?, ?, ?, ?, ?)""",
        (5, 2, "Mushroom Grotto",
         "Bioluminescent fungi cast pale blue light. Spores drift lazily. [n,e,u]",
         "Mushroom Grotto. Glowing fungi. [n,e,u]",
         1),
    )
    # Room 6: North
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short,
           is_hub) VALUES (?, ?, ?, ?, ?, ?)""",
        (6, 2, "Spore Chamber",
         "Thick clouds of spores choke the air. A large shape moves within. [s]",
         "Spore Chamber. Choking spores. [s]",
         0),
    )
    # Room 7: East
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short,
           is_hub) VALUES (?, ?, ?, ?, ?, ?)""",
        (7, 2, "Crystal Pool",
         "A still pool reflects crystalline formations. Peace here, for now. [w]",
         "Crystal Pool. Quiet reflections. [w]",
         0),
    )

    # === Room Exits (bidirectional) ===
    exits = [
        # Floor 1
        (1, 2, "n"), (2, 1, "s"),   # Hub <-> Rat Warren
        (1, 3, "e"), (3, 1, "w"),   # Hub <-> Flooded Passage
        (2, 4, "e"), (4, 2, "s"),   # Rat Warren <-> Stairway  (north exit from 4 leads nowhere, use s)
        (3, 4, "n"), (4, 3, "s"),   # Flooded Passage doesn't connect to stair — fix below
        # Floor 1 → Floor 2 stairway
        (4, 5, "d"), (5, 4, "u"),   # Stairway <-> Mushroom Grotto
        # Floor 2
        (5, 6, "n"), (6, 5, "s"),   # Grotto <-> Spore Chamber
        (5, 7, "e"), (7, 5, "w"),   # Grotto <-> Crystal Pool
    ]

    # Remove the duplicate exit — room 4 already has (4,2,"s") and (4,3,"s")
    # Fix: room 4 connects south to room 2, room 3 connects north to room 4
    # Let's clean up: Hub(1)-n->Rat(2), Hub(1)-e->Flooded(3), Hub(1)-s->nothing
    # Rat(2)-e->Stair(4), Flooded(3)-n->Stair(4) — but that's 2 rooms going to stair from different dirs
    # Simplify: remove (3,4,"n") and (4,3,"s") — just Hub-n-Rat-e-Stair, Hub-e-Flooded
    clean_exits = [
        (1, 2, "n"), (2, 1, "s"),
        (1, 3, "e"), (3, 1, "w"),
        (2, 4, "e"), (4, 2, "w"),
        (4, 5, "d"), (5, 4, "u"),
        (5, 6, "n"), (6, 5, "s"),
        (5, 7, "e"), (7, 5, "w"),
    ]

    for from_id, to_id, direction in clean_exits:
        conn.execute(
            "INSERT INTO room_exits (from_room_id, to_room_id, direction) VALUES (?, ?, ?)",
            (from_id, to_id, direction),
        )

    # === Monsters ===
    monsters = [
        # (room_id, name, hp, hp_max, pow, def, spd, xp, gold_min, gold_max, tier)
        (2, "Giant Rat", 15, 15, 3, 1, 2, 10, 2, 5, 1),
        (3, "Slime", 20, 20, 2, 2, 1, 15, 3, 8, 1),
        (4, "Skeleton Guard", 30, 30, 5, 3, 2, 25, 5, 12, 2),
        (6, "Spore Beast", 40, 40, 6, 4, 3, 35, 8, 15, 2),
        (7, "Crystal Golem", 50, 50, 7, 6, 1, 50, 12, 20, 3),
    ]

    for m in monsters:
        conn.execute(
            """INSERT INTO monsters
               (room_id, name, hp, hp_max, pow, def, spd,
                xp_reward, gold_reward_min, gold_reward_max, tier)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            m,
        )

    # === Items ===
    items = [
        # (name, slot, tier, pow_mod, def_mod, spd_mod, floor_source)
        ("Rusty Sword", "weapon", 1, 2, 0, 0, 1),
        ("Leather Cap", "armor", 1, 0, 2, 0, 1),
        ("Lucky Charm", "trinket", 1, 0, 0, 2, 1),
        ("Iron Blade", "weapon", 2, 4, 0, 0, 2),
        ("Chain Mail", "armor", 2, 0, 4, 0, 2),
    ]

    for item in items:
        conn.execute(
            """INSERT INTO items
               (name, slot, tier, pow_mod, def_mod, spd_mod, floor_source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            item,
        )

    conn.commit()
    print(f"Test world seeded in {db_path}")
    print(f"  Rooms: 7 (4 on floor 1, 3 on floor 2)")
    print(f"  Monsters: {len(monsters)}")
    print(f"  Items: {len(items)}")
    print(f"  Epoch: #1 (hold_the_line / emergence)")
    conn.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "mmud.db"
    seed(path)
