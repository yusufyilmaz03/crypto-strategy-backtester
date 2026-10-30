# kripto_bot/app/manual_close.py
"""Close requests for paper positions, shared by the panel's Close button and the
Telegram bot. The instance's own process does the selling (trading/position_watch.py,
ManualClose); this only checks the request and files it in paper.db."""
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
