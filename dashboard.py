# dashboard.py
"""Flask dashboard: paper trading results (from paper.db) and backtest comparisons."""
import json
import os
import sqlite3

import pandas as pd
from flask import Flask, jsonify, render_template, request

import config
from metrics import max_drawdown_pct
from store import DB_FILE

app = Flask(__name__)

BACKTEST_FILE = "multi_backtest_strategies.csv"

TRADE_COLUMNS = {
    "trade_id": "TradeID", "entry_time": "Timestamp Entry", "exit_time": "Timestamp Exit",
    "symbol": "Symbol", "side": "Position", "entry_price": "Entry Price", "exit_price": "Exit Price",
    "stop": "Stop", "entry_rsi": "Entry RSI", "pnl": "PnL", "return_pct": "Return%",
    "fees": "Fees", "reason": "Close Reason",
}
SIGNAL_COLUMNS = {
    "candle_time": "Candle Time", "symbol": "Symbol", "signal": "Signal", "rsi": "RSI",
    "close": "Close", "position": "Position", "action": "Action",
}


def current_run():
    """Strategy and timeframe shown by default: the ones paper trading is configured for."""
    return (request.args.get("strategy", config.STRATEGY),
            request.args.get("timeframe", config.TIMEFRAME))


def load_table(table, order=None):
    """Rows of `table` for the current run; empty frame if the database doesn't exist yet."""
    if not os.path.exists(DB_FILE):
        return pd.DataFrame()
    strat, tf = current_run()
    with sqlite3.connect(DB_FILE) as conn:
        try:
            query = f"SELECT * FROM {table} WHERE strategy=? AND timeframe=?"
            if order:
                query += f" ORDER BY {order}"
            return pd.read_sql_query(query, conn, params=(strat, tf))
        except Exception:
            return pd.DataFrame()


def load_trades():
    df = load_table("trades", "entry_time")
    if df.empty:
        return df
    return df.rename(columns=TRADE_COLUMNS)[list(TRADE_COLUMNS.values())]


def portfolio_equity():
    """Sum of per-symbol equity over time (symbols without data count at initial equity)."""
    eq = load_table("equity", "time")
    if eq.empty:
        return pd.Series(dtype=float)
    wide = eq.pivot_table(index="time", columns="symbol", values="equity").sort_index().ffill()
    idle = len(config.SYMBOLS) - wide.shape[1]
    total = wide.fillna(config.INITIAL_EQUITY).sum(axis=1) + max(idle, 0) * config.INITIAL_EQUITY
    return total


@app.route("/")
def index():
    trades = load_trades()
    closed = trades[trades["Timestamp Exit"].notna()] if not trades.empty else trades
    equity = portfolio_equity()
    start_equity = config.INITIAL_EQUITY * len(config.SYMBOLS)
    summary = {
        "strategy": current_run()[0], "timeframe": current_run()[1],
        "total_pnl": float(closed["PnL"].sum()) if len(closed) else 0.0,
        "return_pct": (equity.iloc[-1] / start_equity - 1) * 100 if len(equity) else 0.0,
        "win_rate": float((closed["PnL"] > 0).mean() * 100) if len(closed) else None,
        "max_dd": max_drawdown_pct(pd.concat([pd.Series([start_equity]), equity.reset_index(drop=True)]))
                  if len(equity) else 0.0,
        "open_trades": int(trades["Timestamp Exit"].isna().sum()) if len(trades) else 0,
        "closed_trades": int(len(closed)),
    }
    return render_template("index.html", **summary)


@app.route("/signals")
def signals():
    df = load_table("signals", "id DESC")
    if not df.empty:
        df = df.rename(columns=SIGNAL_COLUMNS)[list(SIGNAL_COLUMNS.values())]
    return render_template("table.html", title="Signals Log",
                           tables=[df.to_html(classes="table table-striped", index=False)])


@app.route("/trades")
def trades():
    df = load_trades()
    if not df.empty:
        df = df.iloc[::-1]
    return render_template("table.html", title="Trades Log",
                           tables=[df.to_html(classes="table table-striped", index=False)])


@app.route("/pnl_data")
def pnl_data():
    df = load_trades()
    df = df[df["Timestamp Exit"].notna()].sort_values("Timestamp Exit") if not df.empty else df
    if df.empty:
        return jsonify({"labels": [], "cum_pnl": [], "trade_pnl": [], "win": 0, "loss": 0})
    return jsonify({
        "labels": df["Timestamp Exit"].tolist(),
        "cum_pnl": df["PnL"].cumsum().tolist(),
        "trade_pnl": df["PnL"].tolist(),
        "win": int((df["PnL"] > 0).sum()),
        "loss": int((df["PnL"] <= 0).sum()),
    })


@app.route("/equity_data")
def equity_data():
    eq = portfolio_equity()
    start_equity = config.INITIAL_EQUITY * len(config.SYMBOLS)
    return jsonify({"labels": eq.index.tolist(),
                    "return_pct": ((eq / start_equity - 1) * 100).round(4).tolist()})


@app.route("/last_trade")
def last_trade():
    df = load_trades()
    if df.empty:
        return jsonify({"trade": None})
    last = df.iloc[-1].to_dict()
    # NaN -> None (for JSON)
    return jsonify({"trade": {k: (None if pd.isna(v) else v) for k, v in last.items()}})


@app.route("/dist_data")
def dist_data():
    """
    metric:
      - winloss            -> [Win, Loss] counts
      - pnl_by_symbol      -> total PnL per symbol
      - count_by_symbol    -> trade count per symbol
    """
    df = load_trades()
    df = df[df["Timestamp Exit"].notna()] if not df.empty else df
    if df.empty:
        return jsonify({"labels": [], "values": []})

    metric = request.args.get("metric", "winloss")
    if metric == "winloss":
        return jsonify({"labels": ["Win", "Loss"],
                        "values": [int((df["PnL"] > 0).sum()), int((df["PnL"] <= 0).sum())]})

    if metric in ("pnl_by_symbol", "count_by_symbol"):
        g = df.groupby("Symbol")["PnL"]
        g = (g.sum() if metric == "pnl_by_symbol" else g.count()).sort_values(ascending=False)
        # top 12 for readability, the rest as "Others"
        if len(g) > 12:
            g = pd.concat([g.iloc[:12], pd.Series({"Others": g.iloc[12:].sum()})])
        return jsonify({"labels": g.index.tolist(), "values": [float(x) for x in g.values]})

    return jsonify({"labels": [], "values": []})


# ============================================================
# Backtest comparison page + API
# ============================================================

@app.route("/backtest")
def backtest():
    # renders templates/backtest.html
    return render_template("backtest.html")


@app.route("/api/backtest")
def api_backtest():
    """
    Return backtest results (multi_backtest_strategies.csv) as JSON.
    One row per symbol / timeframe / strategy / segment (ALL, IS, OOS, WF1..).
    """
    if not os.path.exists(BACKTEST_FILE):
        return jsonify({"results": []})
    df = pd.read_csv(BACKTEST_FILE)
    if df.empty or "Segment" not in df.columns:
        return jsonify({"results": []})

    # inf (e.g. profit factor without losing trades) and NaN are not valid JSON
    df = df.replace([float("inf"), float("-inf")], None)
    data = json.loads(df.to_json(orient="records"))
    return jsonify({"results": data})

# ============================================================

@app.route("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    print("Starting dashboard on http://127.0.0.1:5002 ...")
    # use_reloader=True: reload automatically on code changes
    app.run(host="127.0.0.1", port=5002, debug=True, use_reloader=True)
