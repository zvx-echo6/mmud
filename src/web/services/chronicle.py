"""
Chronicle — epoch history and NPC journal queries.
"""
from src.web.services import gamedb


def get_chronicle_data():
    """Data for the chronicle page."""
    return {
        "epoch": gamedb.get_epoch_status(),
        "history": gamedb.get_epoch_history(),
    }


def get_journal_data(npc=None, epoch_number=None):
    """Data for NPC journals."""
    epoch = gamedb.get_epoch_status()
    ep_num = epoch_number or (epoch["epoch_number"] if epoch else None)
    return {
        "epoch": epoch,
        "journals": gamedb.get_npc_journals(npc=npc, epoch_number=ep_num, limit=30),
    }
