"""Tests for the day tick system."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from config import (
    BARD_TOKEN_CAP,
    BOUNTY_ACTIVE_MAX,
    BREACH_DAY,
    DAYTICK_TIMEZONE,
    DUNGEON_ACTIONS_PER_DAY,
    EPOCH_DAYS,
    SOCIAL_ACTIONS_PER_DAY,
    SPECIAL_ACTIONS_PER_DAY,
)
from src.db.database import init_schema
from src.main import _check_day_tick
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


# ── Wall-clock day tick (_check_day_tick) ──


def _mock_now(year, month, day, hour, minute=0):
    """Create a timezone-aware datetime for mocking."""
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(DAYTICK_TIMEZONE))


def test_wallclock_tick_fires_after_10am():
    """Tick fires when date is past last_tick_date and hour >= 10."""
    conn = _make_db(day=5)
    # Simulate: last tick was yesterday, it's now 10:30 AM today
    with patch("src.main.datetime") as mock_dt:
        mock_dt.now.return_value = _mock_now(2026, 3, 2, 10, 30)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = _check_day_tick(conn, "2026-03-01")

    assert result == "2026-03-02"
    epoch = get_epoch(conn)
    assert epoch["day_number"] == 6  # Advanced from 5 to 6


def test_wallclock_tick_does_not_fire_before_10am():
    """Tick does NOT fire when hour < 10, even if date advanced."""
    conn = _make_db(day=5)
    # Simulate: last tick was yesterday, it's now 9:00 AM today
    with patch("src.main.datetime") as mock_dt:
        mock_dt.now.return_value = _mock_now(2026, 3, 2, 9, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = _check_day_tick(conn, "2026-03-01")

    assert result == "2026-03-01"  # Unchanged — didn't tick
    epoch = get_epoch(conn)
    assert epoch["day_number"] == 5  # Still day 5


def test_wallclock_tick_idempotent_same_day():
    """Tick does NOT double-fire on same day (idempotent on restart)."""
    conn = _make_db(day=5)
    # First tick: advances to day 6
    with patch("src.main.datetime") as mock_dt:
        mock_dt.now.return_value = _mock_now(2026, 3, 2, 11, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = _check_day_tick(conn, "2026-03-01")
    assert result == "2026-03-02"
    assert get_epoch(conn)["day_number"] == 6

    # Second check same day: should NOT fire again
    with patch("src.main.datetime") as mock_dt:
        mock_dt.now.return_value = _mock_now(2026, 3, 2, 14, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result2 = _check_day_tick(conn, "2026-03-02")
    assert result2 == "2026-03-02"  # Unchanged
    assert get_epoch(conn)["day_number"] == 6  # Still day 6


def test_wallclock_multi_day_catchup():
    """Server down 3 days: fires one tick per check cycle until caught up."""
    conn = _make_db(day=5)
    # Simulate: last tick was 3 days ago, it's now 11 AM
    with patch("src.main.datetime") as mock_dt:
        mock_dt.now.return_value = _mock_now(2026, 3, 5, 11, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        # First check: catches up one day (March 2 → March 3)
        result1 = _check_day_tick(conn, "2026-03-02")
        assert result1 == "2026-03-03"
        assert get_epoch(conn)["day_number"] == 6

        # Second check: catches up another day (March 3 → March 4)
        result2 = _check_day_tick(conn, result1)
        assert result2 == "2026-03-04"
        assert get_epoch(conn)["day_number"] == 7

        # Third check: catches up to today (March 4 → March 5)
        result3 = _check_day_tick(conn, result2)
        assert result3 == "2026-03-05"
        assert get_epoch(conn)["day_number"] == 8

        # Fourth check: already caught up, no tick
        result4 = _check_day_tick(conn, result3)
        assert result4 == "2026-03-05"
        assert get_epoch(conn)["day_number"] == 8


def test_wallclock_no_epoch_returns_unchanged():
    """_check_day_tick returns last_tick_date unchanged when no epoch exists."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    result = _check_day_tick(conn, "2026-03-01")
    assert result == "2026-03-01"


def test_wallclock_catchup_before_10am():
    """Multi-day catch-up before 10 AM: ticks up to yesterday, waits for 10 AM today."""
    conn = _make_db(day=5)
    # Last tick 3 days ago, it's 8 AM now (March 5)
    # So last tick was March 2, today is March 5
    with patch("src.main.datetime") as mock_dt:
        mock_dt.now.return_value = _mock_now(2026, 3, 5, 8, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        # First check: catch up March 2 → March 3
        result1 = _check_day_tick(conn, "2026-03-02")
        assert result1 == "2026-03-03"
        assert get_epoch(conn)["day_number"] == 6

        # Second check: catch up March 3 → March 4 (yesterday)
        result2 = _check_day_tick(conn, result1)
        assert result2 == "2026-03-04"
        assert get_epoch(conn)["day_number"] == 7

        # Third check: last tick is March 4 (yesterday), before 10 AM — wait
        result3 = _check_day_tick(conn, result2)
        assert result3 == "2026-03-04"  # Still yesterday — waiting for 10 AM
        assert get_epoch(conn)["day_number"] == 7  # No additional tick


def test_advance_day_sets_last_tick_date():
    """advance_day() records the current date in last_tick_date."""
    conn = _make_db(day=5)
    from src.models.epoch import advance_day
    advance_day(conn)
    epoch = get_epoch(conn)
    assert epoch["last_tick_date"] is not None
    # Should be a valid date string
    datetime.strptime(epoch["last_tick_date"], "%Y-%m-%d")
