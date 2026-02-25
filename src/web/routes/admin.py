"""
Admin routes — behind session-based auth.
"""
import functools
import os

from flask import (
    Blueprint, flash, redirect, render_template, request, session, url_for,
)

from src.web import config as web_config
from src.web.services import gamedb
from src.web.services import admin_service as admin_svc

bp = Blueprint("admin", __name__, template_folder="../templates/admin")


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == web_config.WEB_ADMIN_PASSWORD:
            session["admin_authenticated"] = True
            session["admin_user"] = request.form.get("callsign", "operator")
            return redirect(url_for("admin.dashboard"))
        return render_template("admin/login.html", error="Invalid credentials.")
    return render_template("admin/login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("public.index"))


@bp.route("/")
@login_required
def dashboard():
    epoch = gamedb.get_epoch_status()
    player_count = gamedb.get_player_count()
    log = gamedb.get_admin_log(limit=10)
    nodes = gamedb.get_node_config()
    return render_template(
        "admin/dashboard.html",
        epoch=epoch,
        player_count=player_count,
        log=log,
        nodes=nodes,
    )


@bp.route("/nodes")
@login_required
def nodes():
    nodes = gamedb.get_node_config()
    return render_template("admin/nodes.html", nodes=nodes)


@bp.route("/nodes/assign", methods=["POST"])
@login_required
def nodes_assign():
    role = request.form.get("role", "")
    mesh_id = request.form.get("mesh_node_id", "").strip()
    if not role or not mesh_id:
        flash("Role and node ID required.", "error")
        return redirect(url_for("admin.nodes"))
    admin_svc.assign_node(session["admin_user"], role, mesh_id)
    flash(f"Assigned {mesh_id} to {role.upper()}.", "success")
    return redirect(url_for("admin.nodes"))


@bp.route("/players")
@login_required
def players():
    players = gamedb.get_player_list()
    return render_template("admin/players.html", players=players)


@bp.route("/players/<int:player_id>/ban", methods=["POST"])
@login_required
def ban_player(player_id):
    reason = request.form.get("reason", "")
    admin_svc.ban_player(session["admin_user"], player_id, reason)
    flash("Player banned.", "success")
    return redirect(url_for("admin.players"))


@bp.route("/players/<int:player_id>/kick", methods=["POST"])
@login_required
def kick_player(player_id):
    admin_svc.kick_player(session["admin_user"], player_id)
    flash("Player kicked to town.", "success")
    return redirect(url_for("admin.players"))


@bp.route("/players/<int:player_id>/reset", methods=["POST"])
@login_required
def reset_player(player_id):
    admin_svc.reset_player(session["admin_user"], player_id)
    flash("Player reset to level 1.", "success")
    return redirect(url_for("admin.players"))


@bp.route("/epoch")
@login_required
def epoch():
    epoch = gamedb.get_epoch_status()
    breach = gamedb.get_breach_status()
    return render_template("admin/epoch.html", epoch=epoch, breach=breach)


@bp.route("/epoch/advance-day", methods=["POST"])
@login_required
def advance_day():
    new_day = admin_svc.advance_day(session["admin_user"])
    flash(f"Advanced to day {new_day}.", "success")
    return redirect(url_for("admin.epoch"))


@bp.route("/epoch/force-breach", methods=["POST"])
@login_required
def force_breach():
    admin_svc.force_breach(session["admin_user"])
    flash("Breach opened.", "success")
    return redirect(url_for("admin.epoch"))


@bp.route("/broadcast", methods=["POST"])
@login_required
def broadcast():
    message = request.form.get("message", "").strip()
    tier = int(request.form.get("tier", 1))
    if not message:
        flash("Message required.", "error")
        return redirect(url_for("admin.epoch"))
    admin_svc.send_broadcast(session["admin_user"], message, tier)
    flash("Broadcast sent.", "success")
    return redirect(url_for("admin.epoch"))


@bp.route("/system")
@login_required
def system():
    db_path = web_config.DB_PATH
    db_size = None
    try:
        db_size = os.path.getsize(db_path)
    except OSError:
        pass
    nodes = gamedb.get_node_config()
    log = gamedb.get_admin_log(limit=20)
    return render_template(
        "admin/system.html",
        db_path=db_path,
        db_size=db_size,
        nodes=nodes,
        log=log,
    )
