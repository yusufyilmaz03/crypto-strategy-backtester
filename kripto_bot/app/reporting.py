# kripto_bot/app/reporting.py
"""Read-side helpers for the dashboard: results of an instance from its database."""
import json
import math
import os
import sqlite3
import time

import pandas as pd

from kripto_bot import config, paths
from kripto_bot.core import bankroll, data
from kripto_bot.core.metrics import max_drawdown_pct
from kripto_bot.trading import store

LOG_DIR = paths.LOG_DIR
STATUS_FILE = paths.runtime("status.json")


def _query(db, sql, params=()):
    if not os.path.exists(db):
        return pd.DataFrame()
    with sqlite3.connect(db, timeout=10) as conn:
        try:
            return pd.read_sql_query(sql, conn, params=params)
        except Exception:
            return pd.DataFrame()


def _db(inst, paper_db=None):
    return (paper_db or store.DB_FILE) if inst.mode == "paper" else inst.db_path


def equity_frame(inst, paper_db=None):
    """Long frame: time, symbol, equity, close."""
    if inst.mode == "paper":
        df = _query(paper_db or store.DB_FILE, "SELECT time, symbol, equity, close FROM equity WHERE strategy=? AND timeframe=?",
                    (inst.run_key, inst.timeframe))
    else:
        df = _query(inst.db_path, "SELECT time, symbol, equity, close FROM equity")
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df


def _bh(df):
    closes = df.pivot_table(index="time", columns="symbol", values="close").sort_index()
    if closes.empty or closes.isna().all().all():
        return pd.Series(dtype=float)
    # forward-fill explicitly: pandas < 3 padded by default (with a FutureWarning), pandas 3 doesn't
    return ((1 + closes.ffill().pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)).cumprod() - 1) * 100


def snapshots(inst, paper_db=None):
    """Bankroll instances: free, reserved, invested, value, contributed and the
    time-weighted return index after every candle."""
    if inst.sizing != "bankroll":
        return pd.DataFrame()
    df = _query(paper_db or store.DB_FILE, "SELECT * FROM bankroll_snapshots WHERE strategy=? AND timeframe=? ORDER BY time",
                (inst.run_key, inst.timeframe))
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df


def curves(inst, paper_db=None):
    """(strategy return %, equal-weight buy-and-hold return %) indexed by candle time.
    A bankroll instance's return is time-weighted: deposits and withdrawals aren't return."""
    df = equity_frame(inst, paper_db)
    if inst.sizing == "bankroll":
        snaps = snapshots(inst, paper_db)
        if snaps.empty:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        strat = pd.Series((snaps["twr"].values - 1) * 100, index=pd.Index(snaps["time"], name="time"))
        bh = _bh(df) if not df.empty else pd.Series(dtype=float)
        if len(bh):     # from the bankroll's start, which is the candle before the first recorded one
            bh = pd.concat([pd.Series([0.0], index=[strat.index[0]]), bh]).groupby(level=0).last()
        return strat, bh.reindex(strat.index)
    if df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    wide = df.pivot_table(index="time", columns="symbol", values="equity").sort_index().ffill()
    # symbols with records count too: one removed from the list later still holds its capital
    n = len(set(inst.symbol_list) | set(wide.columns))
    per_symbol = inst.equity if inst.mode == "paper" else inst.equity / max(n, 1)
    start_total = per_symbol * n
    idle = max(n - wide.shape[1], 0)
    total = wide.fillna(per_symbol).sum(axis=1) + idle * per_symbol
    strat = (total / start_total - 1) * 100
    bh = _bh(df)
    if bh.empty:
        bh = pd.Series(index=strat.index, dtype=float)
    return strat, bh.reindex(strat.index)


def _sharpe(ret_pct):
    eq = 1 + ret_pct / 100
    d = eq.resample("1D").last().dropna().pct_change().dropna()
    if len(d) < 3 or not d.std(ddof=1) > 0:
        return None
    return float(d.mean() / d.std(ddof=1) * math.sqrt(365))


def trades(inst, paper_db=None):
    """Paper: trades table. Testnet: orders (each row one market order)."""
    if inst.mode == "paper":
        return _query(paper_db or store.DB_FILE, "SELECT * FROM trades WHERE strategy=? AND timeframe=? ORDER BY entry_time",
                      (inst.run_key, inst.timeframe))
    return _query(inst.db_path, "SELECT * FROM orders ORDER BY created_at")


POSITION_COLUMNS = ["symbol", "side", "entry_time", "entry_price", "stop", "qty", "score", "grade"]


def positions(inst, paper_db=None):
    if inst.mode == "paper":
        db = paper_db or store.DB_FILE
        # SELECT *: databases from before the score columns existed must still show positions
        pos = _query(db, "SELECT * FROM trades WHERE strategy=? AND timeframe=? AND exit_time IS NULL ORDER BY symbol",
                     (inst.run_key, inst.timeframe))
        pos = pos[[c for c in POSITION_COLUMNS if c in pos.columns]] if not pos.empty else pos
        if not pos.empty and inst.sizing == "bankroll":
            now = (bankroll_state(inst, paper_db) or {}).get("scores", {})
            pos["score_now"] = pos["symbol"].map(now)
        # separate query: databases from before close_requests existed must still show positions
        asked = _query(db, "SELECT symbol FROM close_requests WHERE strategy=? AND timeframe=? AND done_at IS NULL",
                       (inst.run_key, inst.timeframe))
        if not pos.empty:
            pos["close_requested"] = pos["symbol"].isin(set(asked["symbol"]) if not asked.empty else set())
        return pos
    return _query(inst.db_path, "SELECT * FROM positions ORDER BY symbol")


def stop_events(inst, paper_db=None):
    """Paper: shadow stop hits (trading/position_watch.py) with how the engine closed the
    trade. Testnet: none."""
    if inst.mode != "paper":
        return pd.DataFrame()
    return _query(paper_db or store.DB_FILE,
                  "SELECT s.symbol, s.entry_time, s.stop, s.minute_time, s.minute_open, s.minute_low, s.est_fill,"
                  " s.detected_at, t.exit_time, t.exit_price, t.reason FROM stop_events s LEFT JOIN trades t"
                  " ON t.strategy = s.strategy AND t.timeframe = s.timeframe AND t.symbol = s.symbol"
                  " AND t.entry_time = s.entry_time WHERE s.strategy=? AND s.timeframe=? ORDER BY s.minute_time",
                  (inst.run_key, inst.timeframe))


SIGNAL_COLUMNS = ["candle_time", "symbol", "signal", "rsi", "close", "position", "action", "score", "grade"]


def signals(inst, limit=500, paper_db=None):
    if inst.mode == "paper":
        df = _query(paper_db or store.DB_FILE, "SELECT * FROM signals WHERE strategy=? AND timeframe=? ORDER BY id DESC LIMIT ?",
                    (inst.run_key, inst.timeframe, limit))
        return df[[c for c in SIGNAL_COLUMNS if c in df.columns]] if not df.empty else df
    return _query(inst.db_path, "SELECT time, level, symbol, message FROM events ORDER BY id DESC LIMIT ?", (limit,))


def bankroll_state(inst, paper_db=None):
    """The bankroll's saved state (free cash, contributed capital, scores...), or None."""
    if inst.sizing != "bankroll":
        return None
    df = _query(paper_db or store.DB_FILE, "SELECT state FROM bankroll_state WHERE strategy=? AND timeframe=?",
                (inst.run_key, inst.timeframe))
    return json.loads(df["state"].iloc[0]) if not df.empty else None


def ledger(inst, limit=1000, paper_db=None):
    if inst.sizing != "bankroll":
        return pd.DataFrame()
    return _query(paper_db or store.DB_FILE, "SELECT * FROM bankroll_ledger WHERE strategy=? AND timeframe=? "
                  "ORDER BY id DESC LIMIT ?", (inst.run_key, inst.timeframe, limit))


def bankroll_requests(inst, paper_db=None):
    """Deposits and withdrawals the instance hasn't applied yet."""
    if inst.sizing != "bankroll":
        return pd.DataFrame()
    return _query(paper_db or store.DB_FILE, "SELECT id, kind, amount, note, requested_at FROM bankroll_requests "
                  "WHERE strategy=? AND timeframe=? AND done_at IS NULL ORDER BY id", (inst.run_key, inst.timeframe))


def bankroll_view(inst, paper_db=None, was_low=False):
    """Current balances of a bankroll instance and its low-bankroll state, or None.
    The balances are the later of the last ledger row and the last candle snapshot
    (a close inside a candle or a deposit changes them between candles)."""
    if inst.sizing != "bankroll":
        return None
    db = paper_db or store.DB_FILE
    out = {"started": False, "start": float(inst.equity), "low_basis": inst.low_basis, "low_pct": inst.low_pct,
           "pending_requests": int(len(bankroll_requests(inst, paper_db)))}
    snap = _query(db, "SELECT time, free, reserved, invested, value, contributed FROM bankroll_snapshots "
                      "WHERE strategy=? AND timeframe=? ORDER BY time DESC LIMIT 1", (inst.run_key, inst.timeframe))
    led = _query(db, "SELECT time, free_after AS free, reserved_after AS reserved, invested_after AS invested,"
                     " value_after AS value, contributed_after AS contributed FROM bankroll_ledger"
                     " WHERE strategy=? AND timeframe=? ORDER BY id DESC LIMIT 1", (inst.run_key, inst.timeframe))
    rows = [d.iloc[0] for d in (snap, led) if not d.empty]
    if not rows:
        return out
    last = max(rows, key=lambda r: pd.Timestamp(r["time"]))      # ties: the snapshot (written last)
    b = {k: float(last[k]) for k in ("free", "reserved", "invested", "value", "contributed")}
    out.update(b, started=True, time=pd.Timestamp(last["time"]).isoformat(), pnl=b["value"] - b["contributed"])
    out["level_pct"] = 100 * (b["value"] if inst.low_basis == "value" else b["free"]) / b["contributed"] \
        if b["contributed"] > 0 else None
    out["low"] = bankroll.low_state(inst.low_basis, inst.low_pct, b["value"], b["free"], b["contributed"], was_low)
    out["cant_trade"] = b["free"] < max(inst.min_entry or 0.0, bankroll.MIN_ORDER)
    return out


def summary(inst, paper_db=None):
    strat, bh = curves(inst, paper_db)
    tr = trades(inst, paper_db)
    out = {"days": 0, "return_pct": None, "buy_hold_pct": None, "sharpe": None, "buy_hold_sharpe": None,
           "max_dd_pct": None, "buy_hold_max_dd_pct": None, "closed_trades": 0, "win_rate_pct": None,
           "open_positions": int(len(positions(inst, paper_db))), "last_candle": None, "realized_pnl": 0.0}
    if len(strat):
        out.update(days=int((strat.index[-1] - strat.index[0]).days), return_pct=float(strat.iloc[-1]),
                   sharpe=_sharpe(strat), max_dd_pct=max_drawdown_pct(1 + strat / 100),
                   last_candle=strat.index[-1].isoformat())
        if bh.notna().any():
            b = bh.dropna()
            out.update(buy_hold_pct=float(b.iloc[-1]), buy_hold_sharpe=_sharpe(b),
                       buy_hold_max_dd_pct=max_drawdown_pct(1 + b / 100))
    if not tr.empty:
        if inst.mode == "paper":
            closed = tr[tr["exit_time"].notna()]
        else:
            closed = tr[(tr["side"] == "sell") & tr["pnl"].notna()]
        out["closed_trades"] = int(len(closed))
        if len(closed):
            out["win_rate_pct"] = float((closed["pnl"] > 0).mean() * 100)
            out["realized_pnl"] = float(closed["pnl"].sum())
    out["bankroll"] = bankroll_view(inst, paper_db)
    return out


PRICE_TTL_S = 15
_prices = {"at": 0.0, "symbols": frozenset(), "prices": {}}


def _fetch_tickers(symbols):
    return data.get_exchange().fetch_tickers(symbols)


def current_prices(symbols, fetch=None, clock=time.time):
    """Last traded price per symbol from Binance (one request for all, cached for
    PRICE_TTL_S seconds). {} if Binance can't be reached: prices are a convenience."""
    symbols = frozenset(symbols)
    if not symbols:
        return {}
    if clock() - _prices["at"] < PRICE_TTL_S and symbols <= _prices["symbols"]:
        return {s: _prices["prices"].get(s) for s in symbols}
    try:
        tickers = (fetch or _fetch_tickers)(sorted(symbols))
    except Exception:
        return {}
    prices = {s: (tickers.get(s) or {}).get("last") for s in symbols}
    _prices.update(at=clock(), symbols=symbols, prices=prices)
    return prices


def service_status(name, status_file=None, max_age_s=15):
    """State of an instance's process as reported by run_all.py."""
    status_file = status_file or STATUS_FILE
    if not os.path.exists(status_file):
        return {"state": "supervisor not running"}
    try:
        with open(status_file) as f:
            st = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"state": "unknown"}
    age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(st["updated_at"])).total_seconds()
    if age > max_age_s:
        return {"state": "supervisor not running"}
    if name in st.get("blocked", {}):
        return {"state": "blocked", "note": st["blocked"][name]}
    return st["services"].get(name, {"state": "stopped"})


def log_tail(name, lines=200, log_dir=None, block=65536):
    """Last `lines` lines of a service's log, read from the end of the file."""
    log_dir = log_dir or LOG_DIR
    path = os.path.join(log_dir, f"{name}.log")
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        end = f.seek(0, os.SEEK_END)
        data = b""
        while end > 0 and data.count(b"\n") <= lines:
            step = min(block, end)
            end -= step
            f.seek(end)
            data = f.read(step) + data
    return data.decode(errors="replace").splitlines()[-lines:]


# ---------------------------------------------------------------- v11 AI analyst
def _plan_fields(plan_json):
    """The columns the panel shows from a stored plan."""
    p = json.loads(plan_json or "{}")
    e = p.get("entry") or {}
    return {"entry_type": e.get("type"), "entry_price": e.get("price"), "expires_candles": e.get("expires_candles"),
            "stop": p.get("stop"), "take_profit": p.get("take_profit"), "p_tp": p.get("p_tp"), "grade": p.get("grade"),
            "review_if": ", ".join(f"{x.get('side')} {x.get('price')}" for x in p.get("review_if") or []),
            "plan_reasons": " ".join(p.get("reasons") or []), "memo": p.get("memo") or "",
            "news_ids": p.get("news_ids") or []}


def llm_plans(inst, limit=500, paper_db=None):
    """Every plan of a v11 instance, newest first, with the call's kind and the validator's reasons."""
    df = _query(_db(inst, paper_db),
                "SELECT p.id, p.time, p.symbol, p.action, p.accepted, p.reasons, p.plan, c.kind "
                "FROM llm_plans p LEFT JOIN llm_calls c ON c.id = p.call_id WHERE p.instance=? ORDER BY p.id DESC LIMIT ?",
                (inst.name, limit))
    if df.empty:
        return df
    fields = pd.DataFrame([_plan_fields(x) for x in df["plan"]], index=df.index)
    df["rejected_because"] = [" · ".join(json.loads(r or "[]")) for r in df["reasons"]]
    return pd.concat([df.drop(columns=["plan", "reasons"]), fields], axis=1)


def llm_current(inst, paper_db=None):
    """The latest accepted plan of each symbol: what the analyst wants now."""
    df = llm_plans(inst, limit=5000, paper_db=paper_db)
    if df.empty:
        return df
    return df[df["accepted"] == 1].drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)


def llm_calls(inst, limit=300, paper_db=None):
    df = _query(_db(inst, paper_db),
                "SELECT id, time, kind, mode, symbols, retry, outcome, model, effort, input_tokens, output_tokens, "
                "reasoning_tokens, latency_sec, exit_code, substr(stderr, -300) AS stderr "
                "FROM llm_calls WHERE instance=? ORDER BY id DESC LIMIT ?", (inst.name, limit))
    if not df.empty:
        df["symbols"] = [", ".join(json.loads(s or "[]")) for s in df["symbols"]]
    return df


def llm_events(inst, limit=500, paper_db=None):
    """Fills, expiries, cancels, review triggers and skipped reviews, newest first."""
    df = _query(_db(inst, paper_db), "SELECT id, time, symbol, kind, plan_id, data FROM llm_events "
                                      "WHERE instance=? ORDER BY id DESC LIMIT ?", (inst.name, limit))
    if not df.empty:
        df["detail"] = [_event_detail(k, d) for k, d in zip(df["kind"], df["data"])]
        df = df.drop(columns=["data"])
    return df


def _event_detail(kind, data_json):
    d = json.loads(data_json or "{}")
    if "reason" in d:
        return d["reason"]
    if kind == "review":
        return f"{d.get('side')} {d.get('price')}"
    if "price" in d and d["price"] is not None:
        out = f"{d['price']:.8g}"
        return out + (f" · {d['return_pct']:+.2f}%" if "return_pct" in d else "")
    return ""


def llm_orders(inst, paper_db=None):
    """Pending entries and review levels from the analyst's books (open positions are in trades)."""
    df = _query(_db(inst, paper_db), "SELECT symbol, book FROM llm_books WHERE instance=? ORDER BY symbol", (inst.name,))
    rows = []
    for symbol, book in zip(df.get("symbol", []), df.get("book", [])):
        b = json.loads(book)
        e = b.get("entry")
        levels = ", ".join(f"{x['side']} {x['price']}" for x in b.get("review") or [])
        if e or levels:
            rows.append({"symbol": symbol, "entry_type": e and e["kind"], "entry_price": e and e["price"],
                         "stop": e and e["stop"], "take_profit": e and e["take_profit"],
                         "expires_at": e and e["expires_at"], "review_if": levels})
    return pd.DataFrame(rows)


def llm_today(inst, paper_db=None, now=None):
    """Today's (UTC) calls, failures and tokens, and whether the calls are stopped for the day."""
    day = (now or pd.Timestamp.now(tz="UTC")).floor("D").isoformat()
    df = _query(_db(inst, paper_db), "SELECT kind, outcome, retry, input_tokens, output_tokens, reasoning_tokens, time "
                                      "FROM llm_calls WHERE instance=? AND time >= ?", (inst.name, day))
    last = _query(_db(inst, paper_db), "SELECT MAX(time) AS t FROM llm_calls WHERE instance=? AND kind='scheduled'",
                  (inst.name,))
    if df.empty:
        return {"calls": 0, "failed": 0, "triggered": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                "stopped": None, "last_scheduled": last["t"].iloc[0] if not last.empty else None}
    stopped = next((o for o in ("usage_limit", "version_mismatch") if (df["outcome"] == o).any()), None)
    return {"calls": int(len(df)), "failed": int((df["outcome"] != "ok").sum()),
            "triggered": int(((df["kind"] == "triggered") & (df["retry"] == 0)).sum()),
            "input_tokens": int(df["input_tokens"].fillna(0).sum()), "output_tokens": int(df["output_tokens"].fillna(0).sum()),
            "reasoning_tokens": int(df["reasoning_tokens"].fillna(0).sum()), "stopped": stopped,
            "last_scheduled": last["t"].iloc[0] if not last.empty else None}


# ---------------------------------------------------------------- v11 dry run (the AI analyst page)
def dry_run_view(paper_db=None, now=None):
    """The v11 dry run from paper.db: usage per day and run, the grouping comparison, the
    days with plans, its universe and the next run. Reads only what is stored."""
    from kripto_bot.analysis import v11_dry_run as dr
    db = paper_db or store.DB_FILE
    like = (dr.PREFIX + "%",)
    calls = _query(db, "SELECT id, instance, time, mode, outcome, retry, input_tokens, output_tokens, reasoning_tokens, "
                       "latency_sec FROM llm_calls WHERE instance LIKE ? ORDER BY id", like)
    plans = _query(db, "SELECT id, instance, time, mode, symbol, action, accepted, plan, context FROM llm_plans "
                       "WHERE instance LIKE ? ORDER BY id", like)
    usage = []
    if not calls.empty:
        calls["day"] = calls["time"].str[:10]
        calls["setup"] = calls["instance"].str[len(dr.PREFIX):]
        g = calls.groupby(["day", "setup", "mode"], sort=False)
        usage = g.agg(calls=("id", "size"), failed=("outcome", lambda s: int((s != "ok").sum())),
                      retries=("retry", "sum"), input_tokens=("input_tokens", "sum"),
                      output_tokens=("output_tokens", "sum"), reasoning_tokens=("reasoning_tokens", "sum"),
                      seconds=("latency_sec", "mean")).reset_index()
        if not plans.empty:
            pl = plans.assign(day=plans["time"].str[:10], setup=plans["instance"].str[len(dr.PREFIX):])
            counts = pl.groupby(["day", "setup", "mode"]).agg(plans=("id", "size"), accepted=("accepted", "sum")).reset_index()
            usage = usage.merge(counts, on=["day", "setup", "mode"], how="left")
        order = {(s, m): i for i, (s, m, _, _) in enumerate(dr.RUNS)}
        usage["run"] = [order.get((s, m), 99) for s, m in zip(usage["setup"], usage["mode"])]
        usage = usage.sort_values(["day", "run"], ascending=[False, True]).drop(columns="run")
        usage = json.loads(usage.to_json(orient="records"))
    comparison = dr.comparison(plans) if not plans.empty else {}
    path = dr.universe_file()
    uni = None
    if path:
        with open(path) as f:
            u = json.load(f)
        uni = {"file": os.path.basename(path), "as_of": u.get("as_of"), "symbols": u["symbols"][:dr.SYMBOLS]}
    now = now or pd.Timestamp.now(tz="UTC")
    return {"enabled": bool(config.V11_DRY_RUN), "service": service_status("v11-dry-run"),
            "next_run": (now + pd.Timedelta(seconds=dr.seconds_to_next_run(now))).isoformat() if config.V11_DRY_RUN else None,
            "last_call": calls["time"].iloc[-1] if not calls.empty else None,
            "runs": [{"setup": s, "mode": m, "group": n, "news": news, "model": dr.SETUPS[s][0], "effort": dr.SETUPS[s][1]}
                     for s, m, n, news in dr.RUNS],
            "usage": usage, "comparison": comparison, "tolerance": dr.AGREE_TOLERANCE, "agree_atr": dr.AGREE_ATR,
            "days": sorted(plans["time"].str[:10].unique().tolist(), reverse=True) if not plans.empty else [],
            "universe": uni}


def dry_run_plans(day, paper_db=None):
    """Every dry-run plan of one UTC day, by symbol and run."""
    from kripto_bot.analysis import v11_dry_run as dr
    df = _query(paper_db or store.DB_FILE,
                "SELECT id, instance, time, mode, symbol, action, accepted, reasons, plan FROM llm_plans "
                "WHERE instance LIKE ? AND substr(time, 1, 10) = ? ORDER BY symbol, id", (dr.PREFIX + "%", day))
    if df.empty:
        return df
    fields = pd.DataFrame([_plan_fields(x) for x in df["plan"]], index=df.index)
    df["setup"] = df["instance"].str[len(dr.PREFIX):]
    df["rejected_because"] = [" · ".join(json.loads(r or "[]")) for r in df["reasons"]]
    return pd.concat([df.drop(columns=["plan", "reasons", "instance"]), fields], axis=1)
