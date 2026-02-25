"""
Public page routes — no auth required.
"""
from flask import Blueprint, render_template

from src.web.services.dashboard import get_dashboard_data
from src.web.services.chronicle import get_chronicle_data, get_journal_data
from src.web.services import gamedb

bp = Blueprint("public", __name__)


@bp.route("/")
def index():
    data = get_dashboard_data()
    return render_template("index.html", **data)


@bp.route("/chronicle")
def chronicle():
    data = get_chronicle_data()
    journals = {}
    epoch = data.get("epoch")
    ep_num = epoch["epoch_number"] if epoch else None
    for npc in ("grist", "maren", "torval", "whisper"):
        journals[npc] = gamedb.get_npc_journals(npc=npc, epoch_number=ep_num, limit=10)
    return render_template("chronicle.html", **data, journals=journals)


@bp.route("/howto")
def howto():
    return render_template("howto.html")


@bp.route("/join")
def join():
    config = gamedb.get_join_config()
    return render_template("join.html", config=config)
