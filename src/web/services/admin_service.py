"""
Admin service — write operations on the game database.
All writes are logged to admin_log.
"""
import sqlite3
from datetime import datetime

from src.web.services.gamedb import get_rw_db


def _log_action(db, admin, action, target=None, details=None):
    """Record an admin action."""
    db.execute(
        "INSERT INTO admin_log (admin, action, target, details) VALUES (?, ?, ?, ?)",
        (admin, action, target, details),
    )


def assign_node(admin, role, mesh_node_id):
    """Assign a Meshtastic node ID to a sim node role."""
    db = get_rw_db()
    db.execute(
        "UPDATE node_config SET mesh_node_id = ? WHERE role = ?",
        (mesh_node_id, role),
    )
    _log_action(db, admin, "node_assign", role, f"node_id={mesh_node_id}")
    db.commit()


def ban_player(admin, player_id, reason=""):
    """Ban a player by adding to banned_players."""
    db = get_rw_db()
    player = db.execute(
        """SELECT p.name, a.mesh_id
           FROM players p JOIN accounts a ON p.account_id = a.id
           WHERE p.id = ?""",
        (player_id,),
    ).fetchone()
    if not player:
        return False
    db.execute(
        "INSERT OR IGNORE INTO banned_players (mesh_node_id, reason, banned_by) VALUES (?, ?, ?)",
        (player["mesh_id"], reason, admin),
    )
    # Reset to town, clear combat
    db.execute(
        "UPDATE players SET state = 'town', floor = 0, room_id = NULL, combat_monster_id = NULL WHERE id = ?",
        (player_id,),
    )
    _log_action(db, admin, "ban", player["name"], reason)
    db.commit()
    return True


def kick_player(admin, player_id):
    """Kick player to town, clear combat state."""
    db = get_rw_db()
    player = db.execute("SELECT name FROM players WHERE id = ?", (player_id,)).fetchone()
    if not player:
        return False
    db.execute(
        "UPDATE players SET state = 'town', floor = 0, room_id = NULL, combat_monster_id = NULL WHERE id = ?",
        (player_id,),
    )
    _log_action(db, admin, "kick", player["name"])
    db.commit()
    return True


def reset_player(admin, player_id):
    """Reset player to level 1 (testing tool)."""
    db = get_rw_db()
    player = db.execute("SELECT name FROM players WHERE id = ?", (player_id,)).fetchone()
    if not player:
        return False
    db.execute(
        """UPDATE players SET level = 1, xp = 0, gold_carried = 0, gold_banked = 0,
           state = 'town', floor = 0, room_id = NULL, combat_monster_id = NULL,
           stat_points = 0, secrets_found = 0
           WHERE id = ?""",
        (player_id,),
    )
    _log_action(db, admin, "reset", player["name"])
    db.commit()
    return True


def advance_day(admin):
    """Advance epoch day by 1."""
    db = get_rw_db()
    db.execute("UPDATE epoch SET day_number = day_number + 1 WHERE id = 1")
    epoch = db.execute("SELECT day_number FROM epoch WHERE id = 1").fetchone()
    _log_action(db, admin, "advance_day", details=f"now day {epoch['day_number']}")
    db.commit()
    return epoch["day_number"]


def force_breach(admin):
    """Open the Breach regardless of day."""
    db = get_rw_db()
    db.execute(
        "UPDATE epoch SET breach_open = 1 WHERE id = 1",
    )
    db.execute(
        "UPDATE breach SET active = 1, opened_at = ? WHERE id = 1",
        (datetime.utcnow().isoformat(),),
    )
    _log_action(db, admin, "force_breach")
    db.commit()
    return True


def send_broadcast(admin, message, tier=1):
    """Send a manual broadcast."""
    db = get_rw_db()
    if len(message) > 150:
        message = message[:150]
    db.execute(
        "INSERT INTO broadcasts (tier, message) VALUES (?, ?)",
        (tier, message),
    )
    _log_action(db, admin, "broadcast", details=f"tier={tier}: {message[:60]}")
    db.commit()
    return True
