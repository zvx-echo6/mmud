"""
Admin service — write operations on the game database.
All writes are logged to admin_log.
"""
import logging
import sqlite3
from datetime import datetime

from src.web.services.gamedb import get_db, get_rw_db

logger = logging.getLogger(__name__)


def _log_action(db, admin, action, target=None, details=None):
    """Record an admin action."""
    db.execute(
        "INSERT INTO admin_log (admin, action, target, details) VALUES (?, ?, ?, ?)",
        (admin, action, target, details),
    )


def assign_node(admin, role, mesh_node_id):
    """Manually override a node ID for a mesh node role."""
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
        "UPDATE players SET state = 'town', floor = 0, room_id = NULL, combat_monster_id = NULL, town_location = NULL WHERE id = ?",
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
        "UPDATE players SET state = 'town', floor = 0, room_id = NULL, combat_monster_id = NULL, town_location = NULL WHERE id = ?",
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
           town_location = NULL, stat_points = 0, secrets_found = 0,
           resource = 5, resource_max = 5
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


# ═══ NODE CONFIGURATION ═══


def update_node_connection(admin, role, connection):
    """Update the TCP connection string for a mesh node role.

    Writes to node_config table. Requires container restart to take effect.
    """
    db = get_rw_db()
    db.execute(
        "UPDATE node_config SET connection = ? WHERE role = ?",
        (connection, role),
    )
    _log_action(db, admin, "node_connection", role, f"connection={connection}")
    db.commit()


# ═══ JOIN CONFIGURATION ═══


def get_join_config():
    """Read current join configuration from DB."""
    db = get_db()
    row = db.execute("SELECT * FROM join_config WHERE id = 1").fetchone()
    if not row:
        return {
            "channel_name": "", "channel_psk": "", "modem_preset": "LONG_FAST",
            "region": "US", "channel_num": 0, "game_node_name": "EMBR",
            "custom_instructions": "", "updated_at": "", "updated_by": "",
        }
    return dict(row)


def save_join_config(admin, channel_name, channel_psk, modem_preset, region,
                     channel_num, game_node_name, custom_instructions):
    """Save join configuration to DB."""
    db = get_rw_db()
    now = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO join_config (id, channel_name, channel_psk, modem_preset,
             region, channel_num, game_node_name, custom_instructions,
             updated_at, updated_by)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             channel_name = excluded.channel_name,
             channel_psk = excluded.channel_psk,
             modem_preset = excluded.modem_preset,
             region = excluded.region,
             channel_num = excluded.channel_num,
             game_node_name = excluded.game_node_name,
             custom_instructions = excluded.custom_instructions,
             updated_at = excluded.updated_at,
             updated_by = excluded.updated_by""",
        (channel_name, channel_psk, modem_preset, region,
         channel_num, game_node_name, custom_instructions, now, admin),
    )
    _log_action(db, admin, "join_config",
                details=f"preset={modem_preset}, region={region}, ch={channel_name}")
    db.commit()


# ═══ LLM CONFIGURATION ═══


def get_llm_config():
    """Read current LLM configuration from DB."""
    db = get_db()
    row = db.execute("SELECT * FROM llm_config WHERE id = 1").fetchone()
    if not row:
        return {
            "backend": "dummy", "api_key": "", "model": "",
            "base_url": "", "updated_at": "", "updated_by": "",
        }
    return dict(row)


def save_llm_config(admin, backend, api_key, model, base_url):
    """Save LLM configuration to DB and return the saved config.

    If api_key is empty or looks masked (ends with existing suffix),
    preserves the existing key from DB.
    """
    db = get_rw_db()

    # Preserve existing key if not provided or masked
    if not api_key or api_key.startswith("****"):
        existing = db.execute(
            "SELECT api_key FROM llm_config WHERE id = 1"
        ).fetchone()
        if existing:
            api_key = existing["api_key"]
        else:
            api_key = ""

    now = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO llm_config (id, backend, api_key, model, base_url, updated_at, updated_by)
           VALUES (1, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             backend = excluded.backend,
             api_key = excluded.api_key,
             model = excluded.model,
             base_url = excluded.base_url,
             updated_at = excluded.updated_at,
             updated_by = excluded.updated_by""",
        (backend, api_key, model, base_url, now, admin),
    )
    _log_action(db, admin, "llm_config", details=f"backend={backend}, model={model}")
    db.commit()

    return {
        "backend": backend, "api_key": api_key, "model": model,
        "base_url": base_url, "updated_at": now, "updated_by": admin,
    }


def test_llm_connection(backend, api_key, model, base_url):
    """Test an LLM backend connection without saving.

    If api_key is masked, reads the real key from DB.
    Returns (success: bool, message: str).
    """
    from src.generation.narrative import _backend_from_config

    # Resolve masked key
    if not api_key or api_key.startswith("****"):
        try:
            db = get_db()
            existing = db.execute(
                "SELECT api_key FROM llm_config WHERE id = 1"
            ).fetchone()
            if existing:
                api_key = existing["api_key"]
        except Exception:
            pass

    if backend == "dummy":
        return True, "Dummy backend always works."

    if not api_key:
        return False, "API key required for non-dummy backends."

    config = {
        "backend": backend, "api_key": api_key,
        "model": model, "base_url": base_url,
    }

    try:
        b = _backend_from_config(config)
        result = b.complete("Say 'ok' in one word.", max_tokens=10)
        if result and len(result.strip()) > 0:
            return True, f"Connected. Response: {result.strip()[:50]}"
        return False, "Backend returned empty response."
    except Exception as e:
        return False, f"Connection failed: {e}"


def apply_llm_config(app, config):
    """Hot-swap the live NPC handler backend if available.

    Args:
        app: Flask app with NPC_HANDLER in config.
        config: Dict with backend, api_key, model, base_url.
    """
    from src.generation.narrative import _backend_from_config

    npc_handler = app.config.get("NPC_HANDLER")
    if not npc_handler:
        logger.debug("No NPC_HANDLER in app.config — config saved to DB only")
        return

    try:
        new_backend = _backend_from_config(config)
        npc_handler.backend = new_backend
        logger.info(f"Live backend swapped to {config['backend']}")
    except Exception as e:
        logger.error(f"Failed to swap backend: {e}")
