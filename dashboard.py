# dashboard.py
from flask import Flask, render_template, jsonify
import pandas as pd
import os
import json  # 🔹 backtest JSON çıkışı için

app = Flask(__name__)

SIGNALS_FILE = "signals_log.csv"
TRADES_FILE  = "trades_log.csv"
BACKTEST_FILE = "multi_backtest_strategies.csv"  # 🔹 eklendi

def load_csv(path):
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            # bozuk satır vb. durumlarda boş dön
            return pd.DataFrame()
    return pd.DataFrame()

@app.route("/")
def index():
    # Özet metrikler için trades okunması opsiyonel; grafikleri JS çekecek
    trades = load_csv(TRADES_FILE)
    total_pnl = 0
    open_trades = 0
    closed_trades = 0
    if not trades.empty:
        if "PnL" in trades.columns:
            trades["PnL"] = pd.to_numeric(trades["PnL"], errors="coerce").fillna(0)
            total_pnl = float(trades["PnL"].sum())
        # Exit Price NaN => açık işlem kabul edelim
        if "Exit Price" in trades.columns:
            open_trades  = trades["Exit Price"].isna().sum()
            closed_trades = trades["Exit Price"].notna().sum()

    return render_template("index.html",
                           total_pnl=total_pnl,
                           open_trades=int(open_trades),
                           closed_trades=int(closed_trades))

@app.route("/signals")
def signals():
    df = load_csv(SIGNALS_FILE)
    return render_template("table.html", title="Signals Log",
                           tables=[df.to_html(classes="table table-striped", index=False)])

@app.route("/trades")
def trades():
    df = load_csv(TRADES_FILE)
    return render_template("table.html", title="Trades Log",
                           tables=[df.to_html(classes="table table-striped", index=False)])

@app.route("/pnl_data")
def pnl_data():
    df = load_csv(TRADES_FILE)
    if df.empty:
        return jsonify({"labels": [], "cum_pnl": [], "trade_pnl": [], "win": 0, "loss": 0})

    # Güvenli sayısal dönüşümler
    df["PnL"] = pd.to_numeric(df.get("PnL", 0), errors="coerce").fillna(0)
    df["CumPnL"] = df["PnL"].cumsum()

    labels     = df.get("Timestamp Entry", pd.Series([""]*len(df))).fillna("").astype(str).tolist()
    trade_pnl  = df["PnL"].tolist()
    cum_pnl    = df["CumPnL"].tolist()
    win        = int((df["PnL"] > 0).sum())
    loss       = int((df["PnL"] <= 0).sum())

    return jsonify({
        "labels": labels,
        "cum_pnl": cum_pnl,
        "trade_pnl": trade_pnl,
        "win": win,
        "loss": loss
    })

@app.route("/last_trade")
def last_trade():
    df = load_csv(TRADES_FILE)
    if df.empty:
        return jsonify({"trade": None})
    last = df.iloc[-1].to_dict()
    # NaN -> None (JSON için)
    for k, v in list(last.items()):
        if pd.isna(v):
            last[k] = None
    return jsonify({"trade": last})

@app.route("/dist_data")
def dist_data():
    """
    metric:
      - winloss            -> [Win, Loss] adetleri
      - pnl_by_symbol      -> coin başına toplam PnL
      - count_by_symbol    -> coin başına işlem adedi
    """
    import math
    from flask import request

    df = load_csv(TRADES_FILE)
    if df.empty:
        return jsonify({"labels": [], "values": []})

    # sayısal güvenlik
    df["PnL"] = pd.to_numeric(df.get("PnL", 0), errors="coerce").fillna(0)
    df["Symbol"] = df.get("Symbol", "").fillna("UNKNOWN")

    metric = request.args.get("metric", "winloss")

    if metric == "winloss":
        win  = int((df["PnL"] > 0).sum())
        loss = int((df["PnL"] <= 0).sum())
        return jsonify({"labels": ["Win", "Loss"], "values": [win, loss]})

    elif metric == "pnl_by_symbol":
        g = df.groupby("Symbol")["PnL"].sum().sort_values(ascending=False)
        # okunabilirlik için ilk 12, kalanı "Others"
        if len(g) > 12:
            top = g.iloc[:12]
            others = g.iloc[12:].sum()
            g = pd.concat([top, pd.Series({"Others": others})])
        return jsonify({"labels": g.index.tolist(), "values": [float(x) for x in g.values]})

    elif metric == "count_by_symbol":
        g = df.groupby("Symbol")["PnL"].count().sort_values(ascending=False)
        if len(g) > 12:
            top = g.iloc[:12]
            others = g.iloc[12:].sum()
            g = pd.concat([top, pd.Series({"Others": others})])
        return jsonify({"labels": g.index.tolist(), "values": [int(x) for x in g.values]})

    else:
        return jsonify({"labels": [], "values": []})

# ============================================================
# 🔹 YENİ: Backtest karşılaştırma sayfası + API
# ============================================================

@app.route("/backtest")
def backtest():
    # templates/backtest.html (daha önce eklediğimiz) sayfayı render eder
    return render_template("backtest.html")

@app.route("/api/backtest")
def api_backtest():
    """
    Backtest sonuçlarını JSON döner.
    Beklenen kolonlar (esnek):
      Symbol, Timeframe, Strategy, TotalPnL, TradeCount, WinRate(%), MaxDD, AvgPnL
    """
    df = load_csv(BACKTEST_FILE)
    if df.empty:
        return jsonify({"results": []})

    # Kolon isimlerini olabildiğince normalize et
    rename_map = {
        "WinRate": "WinRate(%)",
        "WinRate(%)": "WinRate(%)",
        "MaxDrawdown": "MaxDD",
        "MaxDD(Abs)": "MaxDD",
        "Total PnL": "TotalPnL",
        "Trade Count": "TradeCount",
        "Avg PnL": "AvgPnL",
        "StrategyName": "Strategy"
    }
    for src, dst in rename_map.items():
        if src in df.columns and dst not in df.columns:
            df.rename(columns={src: dst}, inplace=True)

    # Sayısal kolonları temizle
    for col in ["TotalPnL", "TradeCount", "AvgPnL", "MaxDD", "WinRate(%)"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # JSON'a NaN düşmesin
    data = json.loads(df.to_json(orient="records"))
    return jsonify({"results": data})

# ============================================================

@app.route("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    print("Starting dashboard on http://127.0.0.1:5002 ...")
    # use_reloader=True: kod değişince otomatik reload
    app.run(host="127.0.0.1", port=5002, debug=True, use_reloader=True)
