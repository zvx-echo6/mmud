"""Tests for barkeep system: bard tokens, recap, token spending."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from datetime import datetime, timezone, timedelta

from config import BARD_TOKEN_CAP, BARD_TOKEN_RATE, MSG_CHAR_LIMIT
from src.db.database import init_schema
from src.systems import barkeep as barkeep_sys
from src.systems import broadcast as broadcast_sys
from src.models import player as player_model


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
    conn.execute(
        """INSERT INTO accounts (id, mesh_id, handle) VALUES (1, '!abc', 'Tester')"""
    )
    conn.execute(
        """INSERT INTO players (id, account_id, name, class, hp, hp_max, pow, def, spd,
           state, bard_tokens, dungeon_actions_remaining, floor, last_login)
           VALUES (1, 1, 'Tester', 'warrior', 20, 20, 3, 2, 1, 'town', 2, 12, 1,
                   '2026-01-01T00:00:00')"""
    )
    # Rooms for hint/reveal tests
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 1, 'Hub', 'Central hub.', 'Hub.', 1)"""
    )
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub, trap_type)
           VALUES (2, 1, 'Trap Room', 'A trapped room.', 'Traps.', 0, 'physical')"""
    )
    # Secret for hint test
    conn.execute(
        """INSERT INTO secrets (id, type, floor, room_id, name, description, reward_type,
           hint_tier1) VALUES
           (1, 'observation', 1, 2, 'Hidden Cache', 'Gold!', 'lore_fragment',
            'Something gleams in the dark.')"""
    )
    # Items for consumable test
    conn.execute(
        """INSERT INTO items (id, name, slot, tier, pow_mod, def_mod, spd_mod)
           VALUES (1, 'Rusty Sword', 'weapon', 1, 2, 0, 0)"""
    )
    conn.commit()


# ── Token Accrual ──


def test_accrue_first_login():
    conn = make_test_db()
    # Clear last_login to simulate first login
    conn.execute("UPDATE players SET last_login = NULL, bard_tokens = 0 WHERE id = 1")
    conn.commit()
    earned = barkeep_sys.accrue_tokens(conn, 1)
    assert earned == 1
    p = player_model.get_player(conn, 1)
    assert p["bard_tokens"] == 1


def test_accrue_same_day():
    conn = make_test_db()
    # Set last_login to now — same day, no tokens
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE players SET last_login = ?, bard_tokens = 0 WHERE id = 1", (now,))
    conn.commit()
    earned = barkeep_sys.accrue_tokens(conn, 1)
    assert earned == 0


def test_accrue_multi_day():
    conn = make_test_db()
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    conn.execute(
        "UPDATE players SET last_login = ?, bard_tokens = 0 WHERE id = 1",
        (three_days_ago,),
    )
    conn.commit()
    earned = barkeep_sys.accrue_tokens(conn, 1)
    assert earned == 3 * BARD_TOKEN_RATE


def test_accrue_respects_cap():
    conn = make_test_db()
    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn.execute(
        "UPDATE players SET last_login = ?, bard_tokens = 0 WHERE id = 1",
        (ten_days_ago,),
    )
    conn.commit()
    earned = barkeep_sys.accrue_tokens(conn, 1)
    p = player_model.get_player(conn, 1)
    assert p["bard_tokens"] <= BARD_TOKEN_CAP


# ── Recap ──


def test_recap_no_broadcasts():
    conn = make_test_db()
    recap = barkeep_sys.get_recap(conn, 1)
    assert len(recap) >= 1
    for msg in recap:
        assert len(msg) <= MSG_CHAR_LIMIT


def test_recap_with_broadcasts():
    conn = make_test_db()
    broadcast_sys.create_broadcast(conn, 1, "X Hero fell on Floor 1.")
    broadcast_sys.create_broadcast(conn, 2, "^ Sidekick reached level 4!")
    recap = barkeep_sys.get_recap(conn, 1)
    assert len(recap) >= 1
    for msg in recap:
        assert len(msg) <= MSG_CHAR_LIMIT


# ── Token Info ──


def test_token_info_shows_balance():
    conn = make_test_db()
    p = player_model.get_player(conn, 1)
    info = barkeep_sys.get_token_info(p)
    assert "2" in info  # 2 tokens
    assert len(info) <= MSG_CHAR_LIMIT


def test_token_info_zero_tokens():
    conn = make_test_db()
    conn.execute("UPDATE players SET bard_tokens = 0 WHERE id = 1")
    conn.commit()
    p = player_model.get_player(conn, 1)
    info = barkeep_sys.get_token_info(p)
    assert "Nothing" in info or "0" in info


# ── Token Spending ──


def test_spend_invalid_cost():
    conn = make_test_db()
    ok, msg = barkeep_sys.spend_tokens(conn, 1, "abc", "hint")
    assert not ok
    assert len(msg) <= MSG_CHAR_LIMIT


def test_spend_not_enough_tokens():
    conn = make_test_db()
    conn.execute("UPDATE players SET bard_tokens = 0 WHERE id = 1")
    conn.commit()
    ok, msg = barkeep_sys.spend_tokens(conn, 1, "1", "hint")
    assert not ok
    assert "Need" in msg


def test_spend_invalid_choice():
    conn = make_test_db()
    ok, msg = barkeep_sys.spend_tokens(conn, 1, "1", "invalid")
    assert not ok


def test_spend_hint():
    conn = make_test_db()
    conn.execute("UPDATE players SET bard_tokens = 3, floor = 1 WHERE id = 1")
    conn.commit()
    ok, msg = barkeep_sys.spend_tokens(conn, 1, "1", "hint")
    assert ok
    assert "Grist" in msg
    assert len(msg) <= MSG_CHAR_LIMIT
    p = player_model.get_player(conn, 1)
    assert p["bard_tokens"] == 2


def test_spend_buff():
    conn = make_test_db()
    conn.execute("UPDATE players SET bard_tokens = 3 WHERE id = 1")
    conn.commit()
    ok, msg = barkeep_sys.spend_tokens(conn, 1, "1", "buff")
    assert ok
    assert "+" in msg
    assert len(msg) <= MSG_CHAR_LIMIT


def test_spend_reveal():
    conn = make_test_db()
    conn.execute("UPDATE players SET bard_tokens = 3, floor = 1 WHERE id = 1")
    conn.commit()
    ok, msg = barkeep_sys.spend_tokens(conn, 1, "2", "reveal")
    assert ok
    assert "Grist" in msg or "trap" in msg.lower() or "clear" in msg.lower()
    assert len(msg) <= MSG_CHAR_LIMIT


def test_spend_bonus_action():
    conn = make_test_db()
    conn.execute("UPDATE players SET bard_tokens = 3, dungeon_actions_remaining = 5 WHERE id = 1")
    conn.commit()
    ok, msg = barkeep_sys.spend_tokens(conn, 1, "2", "bonus")
    assert ok
    assert "+1" in msg
    p = player_model.get_player(conn, 1)
    assert p["dungeon_actions_remaining"] == 6


def test_spend_deducts_tokens():
    conn = make_test_db()
    conn.execute("UPDATE players SET bard_tokens = 5 WHERE id = 1")
    conn.commit()
    barkeep_sys.spend_tokens(conn, 1, "1", "hint")
    p = player_model.get_player(conn, 1)
    assert p["bard_tokens"] == 4
