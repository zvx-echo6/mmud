"""
Dashboard data aggregation — combines multiple queries for the main page.
"""
from src.web.services import gamedb


def get_dashboard_data():
    """Aggregate all data needed for the main dashboard."""
    epoch = gamedb.get_epoch_status()
    breach = gamedb.get_breach_status()
    mode = epoch["endgame_mode"] if epoch else None

    # Extract preamble from epoch data (split into paragraphs for template)
    preamble_raw = epoch.get("preamble", "") if epoch else ""
    preamble_paragraphs = [
        p.strip() for p in preamble_raw.split("\n\n") if p.strip()
    ] if preamble_raw else []

    data = {
        "epoch": epoch,
        "breach": breach,
        "leaderboard": gamedb.get_leaderboard(limit=10),
        "broadcasts": gamedb.get_broadcasts(limit=20),
        "bounties": gamedb.get_bounties(),
        "secrets": gamedb.get_secrets_status(),
        "player_count": gamedb.get_player_count(),
        "floor_themes": gamedb.get_floor_themes_public(),
        "mode": mode,
        "mode_status": None,
        "preamble": preamble_paragraphs,
    }

    # Mode-specific status
    if mode == "hold_the_line":
        data["mode_status"] = gamedb.get_htl_status()
    elif mode == "raid_boss":
        data["mode_status"] = gamedb.get_raid_status()
    elif mode == "retrieve_and_escape":
        data["mode_status"] = gamedb.get_rne_status()

    return data
