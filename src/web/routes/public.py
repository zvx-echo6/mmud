"""
Public page routes — no auth required.
"""
from flask import Blueprint, render_template

from src.web.services.dashboard import get_dashboard_data
from src.web.services.chronicle import get_chronicle_data, get_journal_data
from src.web.services import gamedb

bp = Blueprint("public", __name__)

# NPC blurb data — embedded as JSON in every page for the modal overlay.
NPC_BLURBS = {
    "grist": {
        "sigil": "\U0001f37a",
        "name": "Grist",
        "title": "The Barkeep",
        "blurb": (
            "A mountain of a man behind a bar that\u2019s older than it should be. "
            "Grist pours drinks, recites the names of the dead, and writes everything "
            "down in a ledger that never loses a page. He calls you \u201clad\u201d or "
            "\u201class\u201d whether it fits or not. He knows who went down today. He "
            "knows who\u2019s coming back. He\u2019s been wrong about the second part "
            "exactly zero times. Buy him a story and he might tell you one \u2014 but "
            "his are always longer, and they always end the same way."
        ),
    },
    "maren": {
        "sigil": "\U0001fa78",
        "name": "Maren",
        "title": "The Healer",
        "blurb": (
            "She smells of lye and something copper. Her hands move over wounds without "
            "looking \u2014 stitches too precise, too geometric, as if she learned surgery "
            "from studying architecture. She doesn\u2019t ask your name on the first visit. "
            "She charges for healing, but not in gold \u2014 she wants the story of the "
            "wound. Where you got it. What the room looked like. She collects these the way "
            "other people collect coins. Don\u2019t thank her. She doesn\u2019t like it."
        ),
    },
    "torval": {
        "sigil": "\u2696",
        "name": "Torval",
        "title": "The Merchant",
        "blurb": (
            "Coins and heavy furs and a counter piled with gear that changes every epoch. "
            "Torval doesn\u2019t greet you. He waits. \"Buying or selling. Pick one.\" His "
            "prices are steep for newcomers and fair for veterans \u2014 he calls this "
            "\"adjusted for risk.\" He polishes every blade with a tenderness he never shows "
            "a customer. He stocks everything an adventurer could need, except shields. "
            "Don\u2019t ask about the shields."
        ),
    },
    "whisper": {
        "sigil": "\U0001f441",
        "name": "Whisper",
        "title": "The Sage",
        "blurb": (
            "A voice from the corner that\u2019s never quite where you expect it. Whisper "
            "speaks in fragments \u2014 scout reports from a war that hasn\u2019t happened "
            "yet. \"South wall. Brittle. Watch the eyes.\" It sounds like nonsense until it "
            "saves your life on the fourth floor. They call you \"echo\" or \"rehearsal.\" "
            "They describe the dungeon as a stomach. The other three NPCs don\u2019t talk "
            "about Whisper. If you ask why, they change the subject."
        ),
    },
}


@bp.route("/")
def index():
    data = get_dashboard_data()
    return render_template("index.html", **data, npc_blurbs=NPC_BLURBS)


@bp.route("/chronicle")
def chronicle():
    data = get_chronicle_data()
    journals = {}
    epoch = data.get("epoch")
    ep_num = epoch["epoch_number"] if epoch else None
    for npc in ("grist", "maren", "torval", "whisper"):
        journals[npc] = gamedb.get_npc_journals(npc=npc, epoch_number=ep_num, limit=10)
    return render_template("chronicle.html", **data, journals=journals, npc_blurbs=NPC_BLURBS)


@bp.route("/howto")
def howto():
    return render_template("howto.html", npc_blurbs=NPC_BLURBS)


@bp.route("/join")
def join():
    config = gamedb.get_join_config()
    return render_template("join.html", config=config, npc_blurbs=NPC_BLURBS)
