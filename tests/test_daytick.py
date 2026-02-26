"""Tests for the day tick system."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import (
    BARD_TOKEN_CAP,
    BOUNTY_ACTIVE_MAX,
    BREACH_DAY,
    DUNGEON_ACTIONS_PER_DAY,
    EPOCH_DAYS,
    SOCIAL_ACTIONS_PER_DAY,
    SPECIAL_ACTIONS_PER_DAY,
)
from src.db.database import init_schema
from src.models.epoch import get_epoch
from src.systems.daytick import run_day_tick


def _make_db(day: int = 1) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', ?)""",
        (day,),
    )
    # Players
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!abc', 'Hero')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, floor, dungeon_actions_remaining, social_actions_remaining,
           special_actions_remaining, bard_tokens)
           VALUES (1, 1, 'Hero', 'warrior', 20, 20, 3, 2, 1, 'dungeon', 1, 3, 0, 0, 2)"""
    )
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (2, '!def', 'Rogue')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, floor, dungeon_actions_remaining, social_actions_remaining,
           special_actions_remaining, bard_tokens)
           VALUES (2, 2, 'Rogue', 'rogue', 18, 20, 2, 1, 3, 'town', 0, 8, 1, 1, 4)"""
    )
    conn.commit()
    return conn


# ── Day advancement ──


def test_day_increments():
    conn = _make_db(day=5)
    stats = run_day_tick(conn)
    assert stats["old_day"] == 5
    assert stats["new_day"] == 6
    epoch = get_epoch(conn)
    assert epoch["day_number"] == 6


# ── Action budget reset ──


def test_action_budget_reset():
    conn = _make_db()
    run_day_tick(conn)
    p1 = conn.execute(
        "SELECT dungeon_actions_remaining, social_actions_remaining, "
        "special_actions_remaining FROM players WHERE id = 1"
    ).fetchone()
    assert p1["dungeon_actions_remaining"] == DUNGEON_ACTIONS_PER_DAY
    assert p1["social_actions_remaining"] == SOCIAL_ACTIONS_PER_DAY
    assert p1["special_actions_remaining"] == SPECIAL_ACTIONS_PER_DAY


def test_all_players_reset():
    conn = _make_db()
    stats = run_day_tick(conn)
    assert stats["actions_reset"] == 2


# ── Bard token grant ──


def test_bard_tokens_granted():
    conn = _make_db()
    run_day_tick(conn)
    p1 = conn.execute(
        "SELECT bard_tokens FROM players WHERE id = 1"
    ).fetchone()
    assert p1["bard_tokens"] == 3  # Was 2, +1 = 3


def test_bard_tokens_capped():
    conn = _make_db()
    # Set a player to near cap
    conn.execute("UPDATE players SET bard_tokens = ? WHERE id = 2", (BARD_TOKEN_CAP,))
    conn.commit()
    run_day_tick(conn)
    p2 = conn.execute(
        "SELECT bard_tokens FROM players WHERE id = 2"
    ).fetchone()
    assert p2["bard_tokens"] == BARD_TOKEN_CAP  # Should not exceed cap


# ── Bounty regen ──


def test_bounty_regen():
    conn = _make_db()
    # Create a room and bounty monster
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Arena', 'An arena.', 'Arena.', 0)"""
    )
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier, is_bounty)
           VALUES (1, 1, 'Bounty Beast', 80, 200, 5, 3, 2, 20, 5, 10, 2, 1)"""
    )
    conn.execute(
        """INSERT INTO bounties (id, type, description, target_monster_id, target_value,
           current_value, floor_min, floor_max, phase, available_from_day, active)
           VALUES (1, 'kill', 'Slay the beast', 1, 200, 0, 1, 2, 'early', 1, 1)"""
    )
    conn.commit()

    stats = run_day_tick(conn)
    assert stats["bounties_regened"] == 1

    monster = conn.execute("SELECT hp FROM monsters WHERE id = 1").fetchone()
    assert monster["hp"] > 80  # Should have regened from 80


# ── Bounty rotation ──


def test_bounty_rotation():
    conn = _make_db()
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Arena', 'An arena.', 'Arena.', 0)"""
    )
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier, is_bounty)
           VALUES (1, 1, 'Beast 1', 100, 100, 5, 3, 2, 20, 5, 10, 2, 1)"""
    )
    conn.execute(
        """INSERT INTO monsters (id, room_id, name, hp, hp_max, pow, def, spd,
           xp_reward, gold_reward_min, gold_reward_max, tier, is_bounty)
           VALUES (2, 1, 'Beast 2', 100, 100, 5, 3, 2, 20, 5, 10, 2, 1)"""
    )
    # No active bounties, 2 available
    conn.execute(
        """INSERT INTO bounties (type, description, target_monster_id, target_value,
           floor_min, floor_max, phase, available_from_day, active)
           VALUES ('kill', 'Slay beast 1', 1, 100, 1, 2, 'early', 1, 0)"""
    )
    conn.execute(
        """INSERT INTO bounties (type, description, target_monster_id, target_value,
           floor_min, floor_max, phase, available_from_day, active)
           VALUES ('kill', 'Slay beast 2', 2, 100, 1, 2, 'early', 1, 0)"""
    )
    conn.commit()

    stats = run_day_tick(conn)
    assert stats["bounties_activated"] == BOUNTY_ACTIVE_MAX

    active = conn.execute(
        "SELECT COUNT(*) as cnt FROM bounties WHERE active = 1"
    ).fetchone()
    assert active["cnt"] == BOUNTY_ACTIVE_MAX


# ── Breach foreshadowing ──


def test_breach_foreshadow_day_12():
    conn = _make_db(day=BREACH_DAY - 4)  # Start at 11, tick advances to 12
    run_day_tick(conn)
    broadcasts = conn.execute(
        "SELECT message FROM broadcasts WHERE tier = 1"
    ).fetchall()
    assert len(broadcasts) >= 1
    found = any("trembles" in b["message"] for b in broadcasts)
    assert found, "Should broadcast breach foreshadow on day 12"


def test_breach_foreshadow_day_13():
    conn = _make_db(day=BREACH_DAY - 3)  # Start at 12, tick advances to 13
    run_day_tick(conn)
    broadcasts = conn.execute(
        "SELECT message FROM broadcasts WHERE tier = 1"
    ).fetchall()
    found = any("Breach" in b["message"] for b in broadcasts)
    assert found, "Should broadcast breach foreshadow on day 13"


# ── Breach opening ──


def test_breach_opens_day_15():
    conn = _make_db(day=BREACH_DAY - 1)  # Day 14, tick advances to 15
    # Need breach table
    conn.execute(
        "INSERT INTO breach (id, mini_event, active) VALUES (1, 'emergence', 0)"
    )
    conn.commit()

    stats = run_day_tick(conn)
    assert stats["breach_opened"] is True

    epoch = get_epoch(conn)
    assert epoch["breach_open"] == 1

    breach = conn.execute("SELECT active FROM breach WHERE id = 1").fetchone()
    assert breach["active"] == 1


def test_breach_not_opened_early():
    conn = _make_db(day=10)
    conn.execute(
        "INSERT INTO breach (id, mini_event, active) VALUES (1, 'emergence', 0)"
    )
    conn.commit()

    stats = run_day_tick(conn)
    assert stats["breach_opened"] is False


# ── Epoch vote ──


def test_vote_triggered_day_30():
    conn = _make_db(day=EPOCH_DAYS - 1)  # Day 29, tick advances to 30
    stats = run_day_tick(conn)
    assert stats["vote_triggered"] is True

    broadcasts = conn.execute(
        "SELECT message FROM broadcasts WHERE tier = 1"
    ).fetchall()
    found = any("Vote" in b["message"] for b in broadcasts)
    assert found, "Should broadcast vote trigger on day 30"


def test_vote_not_triggered_early():
    conn = _make_db(day=10)
    stats = run_day_tick(conn)
    assert stats["vote_triggered"] is False


# ── No epoch ──


def test_no_epoch_returns_error():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    stats = run_day_tick(conn)
    assert "error" in stats
