"""Tests for character-based authentication system.

Covers: registration (JOIN), login (LOGIN), logout, auto-resume,
session clearing on epoch reset, multi-node login, validation,
and 150-char message limits.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from config import MSG_CHAR_LIMIT
from src.core.engine import GameEngine
from src.db.database import init_schema, reset_epoch_tables
from src.models import player as player_model


def make_test_db() -> sqlite3.Connection:
    """Create an in-memory database with schema and test world."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed_test_world(conn)
    return conn


def _seed_test_world(conn: sqlite3.Connection) -> None:
    """Minimal test world for auth tests."""
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 1, '2026-01-01', '2026-01-31', 'hold_the_line', 'emergence', 1)"""
    )
    # Town center room
    conn.execute(
        """INSERT INTO rooms (id, floor, name, description, description_short, is_hub)
           VALUES (1, 0, 'Town Center', 'The Last Ember tavern.', 'Town center.', 1)"""
    )
    conn.commit()


def register_player(engine, node_id="!test1234", name="Tester", cls="w"):
    """Full registration flow: JOIN → name → password → class."""
    engine.process_message(node_id, name, "join")
    engine.process_message(node_id, name, name)
    engine.process_message(node_id, name, "testpass")
    resp = engine.process_message(node_id, name, cls)
    return resp


# ── Registration Tests ──


def test_join_command_starts_registration():
    """JOIN from unknown node prompts for character name."""
    conn = make_test_db()
    engine = GameEngine(conn)
    resp = engine.process_message("!new1", "NewPlayer", "join")
    assert "name" in resp.lower() or "Name" in resp
    assert len(resp) <= MSG_CHAR_LIMIT


def test_registration_full_flow():
    """Full registration: JOIN → name → password → class → Welcome."""
    conn = make_test_db()
    engine = GameEngine(conn)

    resp = engine.process_message("!reg1", "Player1", "join")
    assert "name" in resp.lower() or "Name" in resp

    resp = engine.process_message("!reg1", "Player1", "Gandalf")
    assert "password" in resp.lower() or "Password" in resp

    resp = engine.process_message("!reg1", "Player1", "mypass123")
    assert "class" in resp.lower() or "Pick" in resp

    resp = engine.process_message("!reg1", "Player1", "c")
    assert "Welcome" in resp
    assert "Caster" in resp


def test_registration_name_too_short():
    """Name < 2 chars is rejected."""
    conn = make_test_db()
    engine = GameEngine(conn)
    engine.process_message("!reg2", "P", "join")
    resp = engine.process_message("!reg2", "P", "A")
    assert "2-16" in resp


def test_registration_name_too_long():
    """Name > 16 chars is rejected."""
    conn = make_test_db()
    engine = GameEngine(conn)
    engine.process_message("!reg3", "P", "join")
    resp = engine.process_message("!reg3", "P", "A" * 17)
    assert "2-16" in resp


def test_registration_name_taken():
    """Duplicate name is rejected."""
    conn = make_test_db()
    engine = GameEngine(conn)

    # Register first player
    register_player(engine, "!first", "Hero")

    # Try to register second player with same name
    engine.process_message("!second", "P2", "join")
    resp = engine.process_message("!second", "P2", "Hero")
    assert "taken" in resp.lower() or "Name taken" in resp


def test_registration_password_too_short():
    """Password < 4 chars is rejected."""
    conn = make_test_db()
    engine = GameEngine(conn)
    engine.process_message("!reg4", "P", "join")
    engine.process_message("!reg4", "P", "ValidName")
    resp = engine.process_message("!reg4", "P", "abc")
    assert "4+" in resp


def test_registration_bad_class():
    """Invalid class choice re-prompts."""
    conn = make_test_db()
    engine = GameEngine(conn)
    engine.process_message("!reg5", "P", "join")
    engine.process_message("!reg5", "P", "TestChar")
    engine.process_message("!reg5", "P", "goodpass")
    resp = engine.process_message("!reg5", "P", "z")
    assert "Pick" in resp or "class" in resp.lower()


# ── Login Tests ──


def test_login_full_flow():
    """LOGIN → name → password → Welcome back."""
    conn = make_test_db()
    engine = GameEngine(conn)

    # Register first
    register_player(engine, "!login1", "LoginTest")

    # Logout
    engine.process_message("!login1", "P", "logout")

    # Login from same node
    resp = engine.process_message("!login1", "P", "login")
    assert "name" in resp.lower() or "Character" in resp

    resp = engine.process_message("!login1", "P", "LoginTest")
    assert "password" in resp.lower() or "Password" in resp

    resp = engine.process_message("!login1", "P", "testpass")
    assert "Welcome back" in resp


def test_login_wrong_password():
    """Wrong password rejects login."""
    conn = make_test_db()
    engine = GameEngine(conn)

    register_player(engine, "!login2", "WrongPW")
    engine.process_message("!login2", "P", "logout")

    engine.process_message("!login2", "P", "login")
    engine.process_message("!login2", "P", "WrongPW")
    resp = engine.process_message("!login2", "P", "badpassword")
    assert "Wrong" in resp or "wrong" in resp


def test_login_unknown_character():
    """Unknown character name rejects login."""
    conn = make_test_db()
    engine = GameEngine(conn)

    engine.process_message("!login3", "P", "login")
    resp = engine.process_message("!login3", "P", "NonExistent")
    assert "Unknown" in resp or "unknown" in resp


# ── Session Tests ──


def test_auto_resume():
    """After registration, same node can send commands without re-login."""
    conn = make_test_db()
    engine = GameEngine(conn)

    register_player(engine, "!resume1", "Resumer")

    # Should work immediately without login
    resp = engine.process_message("!resume1", "Resumer", "look")
    assert resp is not None
    assert "JOIN" not in resp  # Should not prompt for join/login


def test_logout():
    """LOGOUT clears session; next command gets JOIN/LOGIN prompt."""
    conn = make_test_db()
    engine = GameEngine(conn)

    register_player(engine, "!logout1", "LogoutTest")

    resp = engine.process_message("!logout1", "P", "logout")
    assert "Logged out" in resp or "logged out" in resp.lower()

    # Next command should prompt for join/login
    resp = engine.process_message("!logout1", "P", "look")
    assert "JOIN" in resp or "LOGIN" in resp


def test_multi_node_login():
    """Same character can log in from a different node."""
    conn = make_test_db()
    engine = GameEngine(conn)

    register_player(engine, "!node1", "MultiNode")

    # Login from a different node
    engine.process_message("!node2", "P", "login")
    engine.process_message("!node2", "P", "MultiNode")
    resp = engine.process_message("!node2", "P", "testpass")
    assert "Welcome back" in resp


def test_unknown_message_no_session():
    """Non-JOIN/LOGIN from unknown node gets prompt."""
    conn = make_test_db()
    engine = GameEngine(conn)

    resp = engine.process_message("!stranger", "Stranger", "look")
    assert "JOIN" in resp or "LOGIN" in resp


# ── Limit Tests ──


def test_150_char_limit():
    """All auth responses fit in 150 characters."""
    conn = make_test_db()
    engine = GameEngine(conn)

    # Join prompt
    resp = engine.process_message("!lim1", "P", "join")
    assert len(resp) <= MSG_CHAR_LIMIT

    # Name prompt response
    resp = engine.process_message("!lim1", "P", "TestName")
    assert len(resp) <= MSG_CHAR_LIMIT

    # Password prompt
    resp = engine.process_message("!lim1", "P", "goodpass")
    assert len(resp) <= MSG_CHAR_LIMIT

    # Welcome
    resp = engine.process_message("!lim1", "P", "w")
    assert len(resp) <= MSG_CHAR_LIMIT

    # Logout
    resp = engine.process_message("!lim1", "P", "logout")
    assert len(resp) <= MSG_CHAR_LIMIT

    # Login prompt
    resp = engine.process_message("!lim1", "P", "login")
    assert len(resp) <= MSG_CHAR_LIMIT

    # Login name
    resp = engine.process_message("!lim1", "P", "TestName")
    assert len(resp) <= MSG_CHAR_LIMIT

    # Login password
    resp = engine.process_message("!lim1", "P", "goodpass")
    assert len(resp) <= MSG_CHAR_LIMIT

    # Unknown node prompt
    resp = engine.process_message("!unknown", "P", "hello")
    assert len(resp) <= MSG_CHAR_LIMIT


# ── Epoch Reset ──


def test_epoch_reset_clears_sessions():
    """After reset_epoch_tables, sessions are gone."""
    conn = make_test_db()
    engine = GameEngine(conn)

    register_player(engine, "!epoch1", "EpochTest")

    # Verify session exists
    player = player_model.get_player_by_session(conn, "!epoch1")
    assert player is not None

    # Reset epoch
    reset_epoch_tables(conn)

    # Session should be gone
    player = player_model.get_player_by_session(conn, "!epoch1")
    assert player is None

    # Re-seed epoch for clean state
    conn.execute(
        """INSERT INTO epoch (id, epoch_number, start_date, end_date,
           endgame_mode, breach_type, day_number)
           VALUES (1, 2, '2026-02-01', '2026-02-28', 'hold_the_line', 'emergence', 1)"""
    )
    conn.commit()

    # Should prompt for join/login
    resp = engine.process_message("!epoch1", "P", "look")
    assert "JOIN" in resp or "LOGIN" in resp
