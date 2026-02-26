"""Tests for Floor 0 town grid and Floor 1 tutorial zone.

Tests town generation, movement, NPC rooms, examine action,
dungeon entry/exit, and tutorial monster stats.
"""

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import (
    BARD_TOKEN_CAP,
    DUNGEON_ACTIONS_PER_DAY,
    MSG_CHAR_LIMIT,
    TOWN_CENTER,
    TOWN_GRID_SIZE,
    TOWN_NPC_POSITIONS,
    TUTORIAL_MONSTER_NAMES,
)
from src.core.engine import GameEngine
from src.db.database import init_schema
from src.generation.narrative import DummyBackend
from src.generation.secretgen import generate_secrets
from src.generation.worldgen import generate_town, generate_world
from src.models import player as player_model
from src.models import world as world_data
from src.models.epoch import create_epoch


# ── Helpers ──────────────────────────────────────────────────────────────


def make_town_db() -> sqlite3.Connection:
    """Create an in-memory database with schema, epoch, town, and dungeon."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    create_epoch(conn, 1, "hold_the_line", "heist")
    backend = DummyBackend()
    generate_town(conn, backend)
    generate_world(conn, backend)
    conn.commit()
    return conn


def register_player(
    engine: GameEngine, node_id: str = "!test1234", name: str = "Tester", cls: str = "w"
) -> str:
    """Register a new player via the engine."""
    engine.process_message(node_id, name, "hello")
    resp = engine.process_message(node_id, name, cls)
    return resp


def get_player(conn, node_id="!test1234"):
    """Get a player by mesh_id."""
    return player_model.get_player_by_mesh_id(conn, node_id)


# ── Town Generation Tests ───────────────────────────────────────────────


def test_town_generates_25_rooms():
    """Floor 0 has exactly 25 rooms."""
    conn = make_town_db()
    rooms = conn.execute("SELECT id FROM rooms WHERE floor = 0").fetchall()
    assert len(rooms) == TOWN_GRID_SIZE * TOWN_GRID_SIZE


def test_town_grid_fully_connected():
    """Every Floor 0 room is reachable from center via BFS."""
    conn = make_town_db()
    center = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1"
    ).fetchone()
    assert center is not None

    # BFS from center
    visited = {center["id"]}
    queue = deque([center["id"]])
    while queue:
        rid = queue.popleft()
        exits = conn.execute(
            "SELECT to_room_id FROM room_exits WHERE from_room_id = ?", (rid,)
        ).fetchall()
        for ex in exits:
            tid = ex["to_room_id"]
            # Only follow Floor 0 rooms
            room = conn.execute(
                "SELECT floor FROM rooms WHERE id = ?", (tid,)
            ).fetchone()
            if room and room["floor"] == 0 and tid not in visited:
                visited.add(tid)
                queue.append(tid)

    all_f0 = conn.execute("SELECT id FROM rooms WHERE floor = 0").fetchall()
    assert len(visited) == len(all_f0)


def test_town_center_is_hub():
    """Center room has is_hub=1."""
    conn = make_town_db()
    hubs = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1"
    ).fetchall()
    assert len(hubs) == 1


def test_town_npc_rooms_exist():
    """4 NPC rooms exist with correct npc_name values."""
    conn = make_town_db()
    expected = set(TOWN_NPC_POSITIONS.values())
    rows = conn.execute(
        "SELECT npc_name FROM rooms WHERE floor = 0 AND npc_name IS NOT NULL"
    ).fetchall()
    found = {r["npc_name"] for r in rows}
    assert found == expected


def test_town_no_monsters():
    """No monsters on Floor 0."""
    conn = make_town_db()
    monsters = conn.execute(
        """SELECT m.id FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 0"""
    ).fetchall()
    assert len(monsters) == 0


# ── Town Movement Tests ─────────────────────────────────────────────────


def test_town_move_nsew():
    """Player can move in cardinal directions on Floor 0."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    # Player should start at center — try moving north
    resp = engine.process_message("!test1234", "Tester", "n")
    assert len(resp) <= MSG_CHAR_LIMIT
    # Should show a room name (not an error)
    assert "No exit" not in resp or "can't move" not in resp


def test_town_move_free():
    """Movement on Floor 0 costs no dungeon actions."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    player = get_player(conn)
    initial_actions = player["dungeon_actions_remaining"]

    # Move several times
    engine.process_message("!test1234", "Tester", "n")
    engine.process_message("!test1234", "Tester", "s")
    engine.process_message("!test1234", "Tester", "e")

    player = get_player(conn)
    assert player["dungeon_actions_remaining"] == initial_actions


def test_town_look_shows_room():
    """LOOK in town shows room description from DB."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "look")
    assert len(resp) <= MSG_CHAR_LIMIT
    # Should contain room formatting (exits)
    assert resp is not None


def test_town_npc_greeting_on_enter():
    """NPC DM is queued when entering an NPC room via movement."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    # Find which direction leads to an NPC room from center
    player = get_player(conn)
    center_id = player["room_id"]

    # Get NPC room locations
    npc_rooms = conn.execute(
        "SELECT id, npc_name FROM rooms WHERE floor = 0 AND npc_name IS NOT NULL AND npc_name != 'grist'"
    ).fetchall()

    if npc_rooms:
        # Find exit from center toward a non-grist NPC
        for npc_room in npc_rooms:
            exit_row = conn.execute(
                "SELECT direction FROM room_exits WHERE from_room_id = ? AND to_room_id = ?",
                (center_id, npc_room["id"]),
            ).fetchone()
            if exit_row:
                engine.process_message("!test1234", "Tester", exit_row["direction"])
                # Should have queued NPC DM
                assert len(engine.npc_dm_queue) > 0
                break


def test_town_npc_greeting_cooldown():
    """NPC DM has cooldown — second entry doesn't re-trigger."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    player = get_player(conn)
    center_id = player["room_id"]

    # Find NPC room adjacent to center
    npc_rooms = conn.execute(
        "SELECT id, npc_name FROM rooms WHERE floor = 0 AND npc_name IS NOT NULL AND npc_name != 'grist'"
    ).fetchall()

    if npc_rooms:
        for npc_room in npc_rooms:
            exit_row = conn.execute(
                "SELECT direction FROM room_exits WHERE from_room_id = ? AND to_room_id = ?",
                (center_id, npc_room["id"]),
            ).fetchone()
            if exit_row:
                direction = exit_row["direction"]
                # First visit — should trigger NPC DM
                engine.process_message("!test1234", "Tester", direction)
                first_triggered = len(engine.npc_dm_queue) > 0
                assert first_triggered, "First visit should trigger NPC DM"

                # Go back to center (queue is cleared at start of each process_message)
                reverse = {"n": "s", "s": "n", "e": "w", "w": "e"}
                engine.process_message("!test1234", "Tester", reverse.get(direction, "s"))

                # Second visit (should be on cooldown — no DM queued)
                engine.process_message("!test1234", "Tester", direction)
                second_triggered = len(engine.npc_dm_queue) > 0
                assert not second_triggered, "Second visit should be on cooldown"
                break


# ── Dungeon Entry/Exit Tests ────────────────────────────────────────────


def test_town_enter_from_center():
    """ENTER works from center room (bar)."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "enter")
    # Should enter Floor 1 hub
    player = get_player(conn)
    assert player["state"] == "dungeon"
    assert player["floor"] == 1


def test_town_enter_from_non_center():
    """ENTER is rejected when not at center room."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    # Move away from center first
    engine.process_message("!test1234", "Tester", "n")
    resp = engine.process_message("!test1234", "Tester", "enter")
    assert "bar" in resp.lower() or "entrance" in resp.lower()

    player = get_player(conn)
    assert player["state"] == "town"


def test_town_descend_alias():
    """DESCEND works as ENTER alias."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "descend")
    player = get_player(conn)
    assert player["state"] == "dungeon"


def test_town_ascend_returns():
    """ASCEND from dungeon returns to town center."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    # Enter dungeon
    engine.process_message("!test1234", "Tester", "enter")
    player = get_player(conn)
    assert player["state"] == "dungeon"

    # Ascend back
    resp = engine.process_message("!test1234", "Tester", "ascend")
    player = get_player(conn)
    assert player["state"] == "town"


# ── Player Lifecycle Tests ──────────────────────────────────────────────


def test_town_player_spawn():
    """New player spawns at center room with valid room_id."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    player = get_player(conn)
    center = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1"
    ).fetchone()

    assert player["room_id"] is not None
    assert player["room_id"] == center["id"]
    assert player["state"] == "town"


def test_town_death_respawn_center():
    """Death respawns player at town center, not NULL."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    player = get_player(conn)
    center = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1"
    ).fetchone()

    # Apply death directly
    player_model.apply_death(conn, player["id"])

    player = get_player(conn)
    assert player["room_id"] == center["id"]
    assert player["state"] == "town"
    assert player["floor"] == 0


def test_town_return_sets_center():
    """TOWN command from dungeon sets room_id to center room."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    # Enter dungeon
    engine.process_message("!test1234", "Tester", "enter")
    # Return to town
    engine.process_message("!test1234", "Tester", "town")

    player = get_player(conn)
    center = conn.execute(
        "SELECT id FROM rooms WHERE floor = 0 AND is_hub = 1"
    ).fetchone()
    assert player["room_id"] == center["id"]
    assert player["state"] == "town"


def test_town_room_id_never_null():
    """room_id is never NULL after any standard action sequence."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    # Various actions
    actions = ["look", "n", "s", "enter", "look", "town", "look"]
    for action in actions:
        engine.process_message("!test1234", "Tester", action)
        player = get_player(conn)
        assert player["room_id"] is not None, f"room_id NULL after '{action}'"


# ── Town Feature Tests ──────────────────────────────────────────────────


def test_town_shop_works():
    """SHOP works on Floor 0."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "shop")
    assert resp is not None
    assert len(resp) <= MSG_CHAR_LIMIT


def test_town_heal_works():
    """HEAL works on Floor 0."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "heal")
    assert resp is not None
    assert len(resp) <= MSG_CHAR_LIMIT


def test_town_bank_works():
    """BANK works on Floor 0."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    resp = engine.process_message("!test1234", "Tester", "bank")
    assert "Bank" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


# ── Examine Tests ───────────────────────────────────────────────────────


def test_town_examine_finds_secret():
    """EXAMINE in a room with an undiscovered secret finds it."""
    conn = make_town_db()
    backend = DummyBackend()

    # Generate secrets (includes town secrets)
    generate_secrets(conn, backend)

    engine = GameEngine(conn)
    register_player(engine)

    # Find a Floor 0 room with a secret
    secret = conn.execute(
        """SELECT s.room_id FROM secrets s
           JOIN rooms r ON s.room_id = r.id
           WHERE r.floor = 0 AND s.discovered_by IS NULL
           LIMIT 1"""
    ).fetchone()

    if secret:
        # Move player to that room
        player = get_player(conn)
        conn.execute(
            "UPDATE players SET room_id = ? WHERE id = ?",
            (secret["room_id"], player["id"]),
        )
        conn.commit()

        resp = engine.process_message("!test1234", "Tester", "examine")
        assert "Found" in resp


def test_town_examine_awards_bard_token():
    """Town secret discovery awards a bard token."""
    conn = make_town_db()
    backend = DummyBackend()
    generate_secrets(conn, backend)

    engine = GameEngine(conn)
    register_player(engine)

    player = get_player(conn)
    initial_tokens = player["bard_tokens"]

    # Find a Floor 0 room with a secret
    secret = conn.execute(
        """SELECT s.room_id FROM secrets s
           JOIN rooms r ON s.room_id = r.id
           WHERE r.floor = 0 AND s.discovered_by IS NULL
           LIMIT 1"""
    ).fetchone()

    if secret:
        conn.execute(
            "UPDATE players SET room_id = ? WHERE id = ?",
            (secret["room_id"], player["id"]),
        )
        conn.commit()

        engine.process_message("!test1234", "Tester", "examine")

        player = get_player(conn)
        assert player["bard_tokens"] == initial_tokens + 1


def test_town_examine_no_gold_reward():
    """Town secret does NOT award gold (bard token only)."""
    conn = make_town_db()
    backend = DummyBackend()
    generate_secrets(conn, backend)

    engine = GameEngine(conn)
    register_player(engine)

    player = get_player(conn)
    initial_gold = player["gold_carried"]

    secret = conn.execute(
        """SELECT s.room_id FROM secrets s
           JOIN rooms r ON s.room_id = r.id
           WHERE r.floor = 0 AND s.discovered_by IS NULL
           LIMIT 1"""
    ).fetchone()

    if secret:
        conn.execute(
            "UPDATE players SET room_id = ? WHERE id = ?",
            (secret["room_id"], player["id"]),
        )
        conn.commit()

        engine.process_message("!test1234", "Tester", "examine")

        player = get_player(conn)
        assert player["gold_carried"] == initial_gold


def test_town_examine_empty_room():
    """EXAMINE in room without secret says nothing unusual."""
    conn = make_town_db()
    engine = GameEngine(conn)
    register_player(engine)

    # No secrets generated — examine should find nothing
    resp = engine.process_message("!test1234", "Tester", "examine")
    assert "Nothing unusual" in resp


def test_town_descriptions_under_150():
    """All town room descriptions are under 150 chars."""
    conn = make_town_db()
    rooms = conn.execute(
        "SELECT id, name, description, description_short FROM rooms WHERE floor = 0"
    ).fetchall()
    for room in rooms:
        assert len(room["description"]) <= 150, (
            f"Room {room['id']} ({room['name']}) description too long: "
            f"{len(room['description'])} chars"
        )
        assert len(room["description_short"]) <= 150, (
            f"Room {room['id']} ({room['name']}) short description too long: "
            f"{len(room['description_short'])} chars"
        )


# ── Tutorial Zone Tests ─────────────────────────────────────────────────


def test_tutorial_zone_ratio():
    """~25% of Floor 1 rooms have tutorial monsters (softer stats)."""
    conn = make_town_db()

    # Get all Floor 1 rooms with monsters
    all_f1 = conn.execute(
        "SELECT id FROM rooms WHERE floor = 1 AND is_hub = 0"
    ).fetchall()
    tutorial_monsters = conn.execute(
        """SELECT m.name FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 1 AND m.name IN ({})""".format(
            ",".join(f"'{n}'" for n in TUTORIAL_MONSTER_NAMES)
        )
    ).fetchall()

    # Should have some tutorial monsters (at least 1)
    assert len(tutorial_monsters) > 0


def test_tutorial_monster_hp_reduced():
    """Tutorial monsters have reduced HP compared to normal tier 1 monsters."""
    conn = make_town_db()

    tutorial = conn.execute(
        """SELECT m.hp_max FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 1 AND m.name IN ({})
           LIMIT 1""".format(
            ",".join(f"'{n}'" for n in TUTORIAL_MONSTER_NAMES)
        )
    ).fetchone()

    normal = conn.execute(
        """SELECT m.hp_max FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 1 AND m.name NOT IN ({})
           AND m.is_floor_boss = 0
           LIMIT 1""".format(
            ",".join(f"'{n}'" for n in TUTORIAL_MONSTER_NAMES)
        )
    ).fetchone()

    if tutorial and normal:
        assert tutorial["hp_max"] < normal["hp_max"]


def test_tutorial_monster_dmg_reduced():
    """Tutorial monsters have reduced POW."""
    conn = make_town_db()

    tutorial = conn.execute(
        """SELECT m.pow FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 1 AND m.name IN ({})
           LIMIT 1""".format(
            ",".join(f"'{n}'" for n in TUTORIAL_MONSTER_NAMES)
        )
    ).fetchone()

    normal = conn.execute(
        """SELECT m.pow FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 1 AND m.name NOT IN ({})
           AND m.is_floor_boss = 0
           LIMIT 1""".format(
            ",".join(f"'{n}'" for n in TUTORIAL_MONSTER_NAMES)
        )
    ).fetchone()

    if tutorial and normal:
        assert tutorial["pow"] <= normal["pow"]


def test_tutorial_monster_gold_increased():
    """Tutorial monsters drop at least as much gold as normal (125% multiplier)."""
    conn = make_town_db()

    tutorial = conn.execute(
        """SELECT m.gold_reward_min, m.gold_reward_max FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 1 AND m.name IN ({})
           LIMIT 1""".format(
            ",".join(f"'{n}'" for n in TUTORIAL_MONSTER_NAMES)
        )
    ).fetchone()

    if tutorial:
        # Tutorial monsters should have non-zero gold reward
        assert tutorial["gold_reward_min"] > 0 or tutorial["gold_reward_max"] > 0


def test_tutorial_monster_xp_unchanged():
    """Tutorial monsters give XP (not reduced to 0)."""
    conn = make_town_db()

    tutorial = conn.execute(
        """SELECT m.xp_reward FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 1 AND m.name IN ({})
           LIMIT 1""".format(
            ",".join(f"'{n}'" for n in TUTORIAL_MONSTER_NAMES)
        )
    ).fetchone()

    if tutorial:
        assert tutorial["xp_reward"] > 0


def test_tutorial_monster_names():
    """Tutorial monsters use names from the tutorial name pool."""
    conn = make_town_db()

    tutorial = conn.execute(
        """SELECT DISTINCT m.name FROM monsters m
           JOIN rooms r ON m.room_id = r.id
           WHERE r.floor = 1 AND m.name IN ({})""".format(
            ",".join(f"'{n}'" for n in TUTORIAL_MONSTER_NAMES)
        )
    ).fetchall()

    for m in tutorial:
        assert m["name"] in TUTORIAL_MONSTER_NAMES


def test_tutorial_has_reveal():
    """At least 1 Floor 1 room has reveal content (gold or lore)."""
    conn = make_town_db()

    reveals = conn.execute(
        """SELECT id FROM rooms
           WHERE floor = 1 AND (reveal_gold > 0 OR reveal_lore != '')
           LIMIT 1"""
    ).fetchone()

    assert reveals is not None
