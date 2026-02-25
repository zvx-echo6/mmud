"""
JSON API endpoints for AJAX polling.
"""
from flask import Blueprint, jsonify, request

from src.web.services import gamedb

bp = Blueprint("api", __name__)


@bp.route("/status")
def status():
    """Epoch status, mode status, breach status. Polled every 30s."""
    epoch = gamedb.get_epoch_status()
    breach = gamedb.get_breach_status()
    secrets = gamedb.get_secrets_status()
    player_count = gamedb.get_player_count()

    mode = epoch["endgame_mode"] if epoch else None
    mode_status = None
    if mode == "hold_the_line":
        mode_status = gamedb.get_htl_status()
    elif mode == "raid_boss":
        mode_status = gamedb.get_raid_status()
    elif mode == "retrieve_and_escape":
        mode_status = gamedb.get_rne_status()

    return jsonify({
        "epoch": epoch,
        "breach": breach,
        "secrets": secrets,
        "player_count": player_count,
        "mode": mode,
        "mode_status": mode_status,
    })


@bp.route("/broadcasts")
def broadcasts():
    """New broadcasts since timestamp. Polled every 15s."""
    since = request.args.get("since")
    limit = min(int(request.args.get("limit", 20)), 50)
    data = gamedb.get_broadcasts(limit=limit, since=since)
    return jsonify(data)


@bp.route("/bounties")
def bounties():
    """Current bounty state with HP bars."""
    data = gamedb.get_bounties()
    return jsonify(data)


@bp.route("/mode")
def mode():
    """Mode-specific status."""
    epoch = gamedb.get_epoch_status()
    if not epoch:
        return jsonify(None)
    m = epoch["endgame_mode"]
    if m == "hold_the_line":
        return jsonify({"mode": m, "data": gamedb.get_htl_status()})
    elif m == "raid_boss":
        return jsonify({"mode": m, "data": gamedb.get_raid_status()})
    elif m == "retrieve_and_escape":
        return jsonify({"mode": m, "data": gamedb.get_rne_status()})
    return jsonify({"mode": m, "data": None})


@bp.route("/leaderboard")
def leaderboard():
    """Current leaderboard data."""
    limit = min(int(request.args.get("limit", 10)), 30)
    data = gamedb.get_leaderboard(limit=limit)
    return jsonify(data)
