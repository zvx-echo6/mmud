"""Tests for bounty system: shared HP, contributions, rewards, regen."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from datetime import datetime, timezone, timedelta

from config import BOUNTY_REGEN_INTERVAL_HOURS, BOUNTY_REGEN_RATE, MSG_CHAR_LIMIT
from src.db.database import init_schema
from src.systems import bounty as bounty_sys


def make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 5)"""
    )
    # Room
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Hub', 'Hub.', 'Hub.', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (2, 2, 'Arena', 'Arena.', 'Arena.', 0)"""
    )
    # Bounty monster
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier, is_bounty)
           VALUES (1, 2, 'Dragon', 100, 100, 8, 5, 3, 50, 20, 30, 3, 1)"""
    )
    # Active bounty
    conn.execute(
        """INSERT INTO bounties (id, type, description, target_monster_id, target_value,
           current_value, floor_min, floor_max, phase, available_from_day, active)
           VALUES (1, 'kill', 'Slay the Dragon', 1, 100, 0, 2, 2, 'early', 1, 1)"""
    )
    # Inactive bounty
    conn.execute(
        """INSERT INTO bounties (id, type, description, target_monster_id, target_value,
           current_value, floor_min, floor_max, phase, available_from_day, active)
           VALUES (2, 'kill', 'Kill the Hydra', NULL, 50, 0, 1, 2, 'mid', 10, 0)"""
    )
    # Players
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!abc', 'Hero')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, gold_carried, xp)
           VALUES (1, 1, 'Hero', 'warrior', 20, 20, 5, 3, 2, 'dungeon', 100, 0)"""
    )
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (2, '!def', 'Sidekick')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, gold_carried, xp)
           VALUES (2, 2, 'Sidekick', 'rogue', 20, 20, 3, 1, 4, 'dungeon', 50, 0)"""
    )
    conn.commit()


# ── Active Bounties ──


def test_get_active_bounties():
    conn = make_test_db()
    bounties = bounty_sys.get_active_bounties(conn)
    assert len(bounties) == 1
    assert bounties[0]["description"] == "Slay the Dragon"


def test_get_bounty_by_monster():
    conn = make_test_db()
    bounty = bounty_sys.get_bounty_by_monster(conn, 1)
    assert bounty is not None
    assert bounty["description"] == "Slay the Dragon"


def test_get_bounty_by_monster_none():
    conn = make_test_db()
    bounty = bounty_sys.get_bounty_by_monster(conn, 999)
    assert bounty is None


# ── Format ──


def test_format_bounty_list():
    conn = make_test_db()
    text = bounty_sys.format_bounty_list(conn)
    assert "Dragon" in text
    assert len(text) <= MSG_CHAR_LIMIT


def test_format_bounty_list_empty():
    conn = make_test_db()
    conn.execute("UPDATE bounties SET active = 0")
    conn.commit()
    text = bounty_sys.format_bounty_list(conn)
    assert "No active" in text


# ── Contribution Tracking ──


def test_record_contribution():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 10)
    row = conn.execute(
        "SELECT contribution FROM bounty_contributors WHERE bounty_id = 1 AND player_id = 1"
    ).fetchone()
    assert row["contribution"] == 10


def test_record_contribution_accumulates():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 10)
    bounty_sys.record_contribution(conn, 1, 1, 5)
    row = conn.execute(
        "SELECT contribution FROM bounty_contributors WHERE bounty_id = 1 AND player_id = 1"
    ).fetchone()
    assert row["contribution"] == 15


def test_multi_player_contributions():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 20)
    bounty_sys.record_contribution(conn, 1, 2, 15)
    rows = conn.execute(
        "SELECT player_id, contribution FROM bounty_contributors WHERE bounty_id = 1 ORDER BY contribution DESC"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["player_id"] == 1
    assert rows[1]["player_id"] == 2


# ── Bounty Completion ──


def test_completion_not_triggered_if_alive():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 10)
    result = bounty_sys.check_bounty_completion(conn, 1, 1)
    assert result is None  # Monster still has HP


def test_completion_on_monster_death():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 60)
    bounty_sys.record_contribution(conn, 1, 2, 40)
    # Kill the monster
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = 1")
    conn.commit()
    result = bounty_sys.check_bounty_completion(conn, 1, 1)
    assert result is not None
    assert "Bounty complete" in result
    assert len(result) <= MSG_CHAR_LIMIT


def test_completion_marks_bounty_done():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 100)
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = 1")
    conn.commit()
    bounty_sys.check_bounty_completion(conn, 1, 1)
    bounty = conn.execute("SELECT completed, active FROM bounties WHERE id = 1").fetchone()
    assert bounty["completed"] == 1
    assert bounty["active"] == 0


def test_completion_rewards_contributors():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 60)
    bounty_sys.record_contribution(conn, 1, 2, 40)
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = 1")
    conn.commit()
    bounty_sys.check_bounty_completion(conn, 1, 1)
    # Both players should have received XP and gold
    p1 = conn.execute("SELECT xp, gold_carried FROM players WHERE id = 1").fetchone()
    p2 = conn.execute("SELECT xp, gold_carried FROM players WHERE id = 2").fetchone()
    assert p1["xp"] > 0
    assert p2["xp"] > 0
    assert p1["gold_carried"] > 100  # Had 100, gained bounty reward
    assert p2["gold_carried"] > 50   # Had 50, gained bounty reward


def test_completion_killer_gets_bonus():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 80)
    bounty_sys.record_contribution(conn, 1, 2, 20)
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = 1")
    conn.commit()
    bounty_sys.check_bounty_completion(conn, 1, 1)  # Player 1 is the killer
    p1 = conn.execute("SELECT gold_carried FROM players WHERE id = 1").fetchone()
    p2 = conn.execute("SELECT gold_carried FROM players WHERE id = 2").fetchone()
    # Player 1 should have more gold than player 2 (killer bonus)
    assert p1["gold_carried"] > p2["gold_carried"]


# ── Halfway Broadcast ──


def test_halfway_broadcast():
    conn = make_test_db()
    # Reduce monster to below half HP
    conn.execute("UPDATE monsters SET hp = 40 WHERE id = 1")
    conn.commit()
    bounty_sys.check_halfway_broadcast(conn, 1, 1)
    # Should have created a broadcast
    row = conn.execute("SELECT message FROM broadcasts WHERE tier = 2").fetchone()
    assert row is not None
    assert "Dragon" in row["message"]


def test_halfway_broadcast_only_once():
    conn = make_test_db()
    conn.execute("UPDATE monsters SET hp = 40 WHERE id = 1")
    conn.commit()
    bounty_sys.check_halfway_broadcast(conn, 1, 1)
    count1 = conn.execute("SELECT COUNT(*) as cnt FROM broadcasts").fetchone()["cnt"]
    bounty_sys.check_halfway_broadcast(conn, 1, 1)
    count2 = conn.execute("SELECT COUNT(*) as cnt FROM broadcasts").fetchone()["cnt"]
    assert count2 == count1  # No duplicate


# ── Replacement Spawn ──


def test_completion_spawns_replacement():
    conn = make_test_db()
    bounty_sys.record_contribution(conn, 1, 1, 100)
    conn.execute("UPDATE monsters SET hp = 0 WHERE id = 1")
    conn.commit()
    bounty_sys.check_bounty_completion(conn, 1, 1)
    # Monster should be replaced with weaker version
    m = conn.execute("SELECT hp, hp_max, is_bounty FROM monsters WHERE id = 1").fetchone()
    assert m["is_bounty"] == 0
    assert m["hp_max"] == 50  # Half of 100
    assert m["hp"] == 50
