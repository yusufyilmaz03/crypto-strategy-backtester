# kripto_bot/app/manual_close.py
"""Close requests for paper positions, shared by the panel's Close button and the
Telegram bot. The instance's own process does the selling (trading/position_watch.py,
ManualClose); this only checks the request and files it in paper.db."""
import json

from kripto_bot.app import reporting
from kripto_bot.trading import store


class CloseRefused(ValueError):
    pass


def request(inst, symbol, paper_db=None):
    """Ask `inst`'s process to sell `symbol` at the market. Raises CloseRefused with the
    reason when the request can't be filed."""
    if not inst.manual_close:
        raise CloseRefused(f"{inst.name} was created without manual close")
    if not inst.enabled:
        raise CloseRefused("start the instance first: its process does the closing")
    if symbol not in set(reporting.positions(inst, paper_db).get("symbol", [])):
        raise CloseRefused(f"{inst.name} has no open {symbol} position")
    paper = store.PaperStore(paper_db)
    try:
        if not paper.request_close(inst.run_key, inst.timeframe, symbol):
            raise CloseRefused(f"closing {symbol} was already requested")
    finally:
        paper.close()


def _pending_entries(inst, paper_db=None):
    """Symbols with a long entry decided at the last close and filled at the open since."""
    states = reporting._query(paper_db or store.DB_FILE,
                              "SELECT symbol, state FROM engine_state WHERE strategy=? AND timeframe=?",
                              (inst.run_key, inst.timeframe))
    out = set()
    for symbol, state in zip(states.get("symbol", []), states.get("state", [])):
        pending = json.loads(state).get("pending")
        if pending and pending[0] == "ENTER" and pending[1] == "LONG":
            out.add(symbol)
    return out


def flatten(inst, paper_db=None):
    """Ask `inst`'s process to sell every open position (and every entry filled at the open
    since the last close) at the market, whatever the instance's manual close setting.
    Returns the symbols asked for; ones already asked are left as they are."""
    if inst.mode != "paper":
        raise CloseRefused("flatten is for paper instances (for the testnet it would send orders)")
    if not inst.enabled:
        raise CloseRefused("start the instance first: its process does the closing")
    symbols = set(reporting.positions(inst, paper_db).get("symbol", [])) | _pending_entries(inst, paper_db)
    paper = store.PaperStore(paper_db)
    try:
        return sorted(s for s in symbols if paper.request_close(inst.run_key, inst.timeframe, s))
    finally:
        paper.close()
