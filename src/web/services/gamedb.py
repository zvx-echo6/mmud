"""
Read-only SQLite connection to the MMUD game database.
WAL mode allows concurrent reads while the game daemon writes.
"""
import json
import sqlite3
from datetime import datetime

from flask import current_app, g


def get_db():
    """Get a read-only database connection for the current request."""
    if "db" not in g:
        db_path = current_app.config["MMUD_DB_PATH"]
        g.db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


def get_rw_db():
    """Get a read-write connection (admin operations only)."""
    if "rw_db" not in g:
        db_path = current_app.config["MMUD_DB_PATH"]
        g.rw_db = sqlite3.connect(db_path)
        g.rw_db.row_factory = sqlite3.Row
        g.rw_db.execute("PRAGMA journal_mode=WAL")
    return g.rw_db


def close_db(e=None):
    """Close database connections at end of request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()
    rw_db = g.pop("rw_db", None)
    if rw_db is not None:
        rw_db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


# ═══ EPOCH STATUS ═══

def get_epoch_status():
    """Current epoch state: day, mode, breach, dates."""
    db = get_db()
    row = db.execute("SELECT * FROM epoch WHERE id = 1").fetchone()
    if not row:
        return None
    return dict(row)


# ═══ LEADERBOARD ═══

def get_leaderboard(limit=10):
    """Players sorted by level desc, xp desc."""
    db = get_db()
    rows = db.execute(
        """SELECT p.id, p.name, p.class, p.level, p.xp, p.floor, p.state,
                  p.hp, p.hp_max, p.hardcore, p.secrets_found,
                  a.handle, a.lifetime_kills
           FROM players p
           JOIN accounts a ON p.account_id = a.id
           ORDER BY p.level DESC, p.xp DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ═══ BROADCASTS ═══

def get_broadcasts(limit=20, since=None):
    """Recent broadcasts, newest first. Optionally filter by timestamp."""
    db = get_db()
    if since:
        rows = db.execute(
            """SELECT id, tier, message, created_at
               FROM broadcasts
               WHERE created_at > ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (since, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT id, tier, message, created_at
               FROM broadcasts
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ═══ BOUNTIES ═══

def get_bounties():
    """Active and recently completed bounties with contributors."""
    db = get_db()
    rows = db.execute(
        """SELECT b.*
           FROM bounties b
           WHERE b.active = 1 OR b.completed = 1
           ORDER BY b.active DESC, b.completed_at DESC
           LIMIT 10""",
    ).fetchall()
    result = []
    for b in rows:
        bounty = dict(b)
        contribs = db.execute(
            """SELECT p.name, bc.contribution
               FROM bounty_contributors bc
               JOIN players p ON bc.player_id = p.id
               WHERE bc.bounty_id = ?
               ORDER BY bc.contribution DESC""",
            (bounty["id"],),
        ).fetchall()
        bounty["contributors"] = [dict(c) for c in contribs]
        result.append(bounty)
    return result


# ═══ HOLD THE LINE ═══

def get_htl_status():
    """Floor clear percentages, checkpoint status."""
    db = get_db()
    floors = {}
    for floor_num in range(1, 5):
        total = db.execute(
            "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ? AND is_breach = 0",
            (floor_num,),
        ).fetchone()["cnt"]
        cleared = db.execute(
            "SELECT COUNT(*) as cnt FROM rooms WHERE floor = ? AND htl_cleared = 1 AND is_breach = 0",
            (floor_num,),
        ).fetchone()["cnt"]
        checkpoints = db.execute(
            """SELECT position, established, established_at
               FROM htl_checkpoints WHERE floor = ?
               ORDER BY id""",
            (floor_num,),
        ).fetchall()
        floors[floor_num] = {
            "total": total,
            "cleared": cleared,
            "pct": round(cleared / total * 100) if total > 0 else 0,
            "checkpoints": [dict(c) for c in checkpoints],
        }
    return floors


# ═══ RAID BOSS ═══

def get_raid_status():
    """Boss HP, phase, mechanics, contributors."""
    db = get_db()
    boss = db.execute("SELECT * FROM raid_boss WHERE id = 1").fetchone()
    if not boss:
        return None
    result = dict(boss)
    result["mechanics_list"] = json.loads(boss["mechanics"]) if boss["mechanics"] else []
    contribs = db.execute(
        """SELECT p.name, rc.total_damage
           FROM raid_boss_contributors rc
           JOIN players p ON rc.player_id = p.id
           ORDER BY rc.total_damage DESC
           LIMIT 10""",
    ).fetchall()
    result["contributors"] = [dict(c) for c in contribs]
    return result


# ═══ RETRIEVE AND ESCAPE ═══

def get_rne_status():
    """Carrier, pursuer, objective state, participants."""
    db = get_db()
    run = db.execute("SELECT * FROM escape_run WHERE id = 1").fetchone()
    if not run:
        return None
    result = dict(run)
    # Carrier name
    if run["carrier_player_id"]:
        carrier = db.execute(
            "SELECT name FROM players WHERE id = ?",
            (run["carrier_player_id"],),
        ).fetchone()
        result["carrier_name"] = carrier["name"] if carrier else None
    else:
        result["carrier_name"] = None
    # Participants
    parts = db.execute(
        """SELECT p.name, ep.role
           FROM escape_participants ep
           JOIN players p ON ep.player_id = p.id""",
    ).fetchall()
    result["participants"] = [dict(p) for p in parts]
    return result


# ═══ SECRETS ═══

def get_secrets_status():
    """Server-wide secrets found count and milestones."""
    db = get_db()
    total = db.execute("SELECT COUNT(*) as cnt FROM secrets").fetchone()["cnt"]
    found = db.execute(
        "SELECT COUNT(*) as cnt FROM secrets WHERE discovered_by IS NOT NULL"
    ).fetchone()["cnt"]
    return {"total": total, "found": found}


# ═══ BREACH ═══

def get_breach_status():
    """Breach open/sealed, mini-event type, completion."""
    db = get_db()
    row = db.execute("SELECT * FROM breach WHERE id = 1").fetchone()
    if not row:
        return None
    return dict(row)


# ═══ EPOCH HISTORY ═══

def get_epoch_history():
    """Hall of fame entries for chronicle page."""
    db = get_db()
    epochs = db.execute(
        """SELECT * FROM hall_of_fame ORDER BY epoch_number DESC"""
    ).fetchall()
    result = []
    for e in epochs:
        entry = dict(e)
        parts = db.execute(
            """SELECT a.handle, hp.role, hp.score
               FROM hall_of_fame_participants hp
               JOIN accounts a ON hp.account_id = a.id
               WHERE hp.hall_id = ?
               ORDER BY hp.score DESC""",
            (e["id"],),
        ).fetchall()
        entry["participants"] = [dict(p) for p in parts]
        result.append(entry)
    return result


# ═══ PLAYER LIST (Admin) ═══

def get_player_list():
    """All players with stats for admin panel."""
    db = get_db()
    rows = db.execute(
        """SELECT p.*, a.mesh_id, a.handle,
                  (SELECT COUNT(*) FROM banned_players bp
                   WHERE bp.mesh_node_id = a.mesh_id) as is_banned
           FROM players p
           JOIN accounts a ON p.account_id = a.id
           ORDER BY p.level DESC, p.xp DESC""",
    ).fetchall()
    return [dict(r) for r in rows]


# ═══ NODE CONFIG ═══

def get_node_config():
    """Mesh node connection info."""
    db = get_db()
    rows = db.execute("SELECT * FROM node_config ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# ═══ NPC JOURNALS ═══

def get_npc_journals(npc=None, epoch_number=None, limit=10):
    """Journal entries, optionally filtered by NPC and epoch."""
    db = get_db()
    query = "SELECT * FROM npc_journals WHERE 1=1"
    params = []
    if npc:
        query += " AND npc = ?"
        params.append(npc)
    if epoch_number:
        query += " AND epoch_number = ?"
        params.append(epoch_number)
    query += " ORDER BY day_number DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ═══ JOIN CONFIG ═══

def get_join_config():
    """Mesh join configuration for public join page."""
    db = get_db()
    row = db.execute("SELECT * FROM join_config WHERE id = 1").fetchone()
    if not row:
        return None
    return dict(row)


# ═══ MESSAGE LOG ═══

def get_node_messages(node):
    """All messages for a specific node, oldest first."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM message_log WHERE node = ? ORDER BY id ASC",
        (node,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_node_messages_after(node, after_id):
    """Messages for a node with id > after_id, oldest first."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM message_log WHERE node = ? AND id > ? ORDER BY id ASC",
        (node, after_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_node_message_count(node):
    """Count of messages for a specific node."""
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM message_log WHERE node = ?",
        (node,),
    ).fetchone()
    return row["cnt"] if row else 0


def get_all_messages():
    """All messages across all nodes, oldest first."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM message_log ORDER BY id ASC",
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_messages_after(after_id):
    """Messages across all nodes with id > after_id, oldest first."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM message_log WHERE id > ? ORDER BY id ASC",
        (after_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_message_count():
    """Total message count across all nodes."""
    db = get_db()
    row = db.execute("SELECT COUNT(*) as cnt FROM message_log").fetchone()
    return row["cnt"] if row else 0


# ═══ ADMIN LOG ═══

def get_admin_log(limit=20):
    """Recent admin actions."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM admin_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ═══ PLAYER COUNT ═══

def get_player_count():
    """Count of active players this epoch."""
    db = get_db()
    row = db.execute("SELECT COUNT(*) as cnt FROM players").fetchone()
    return row["cnt"] if row else 0
