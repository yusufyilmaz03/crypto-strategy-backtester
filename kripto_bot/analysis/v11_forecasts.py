# kripto_bot/analysis/v11_forecasts.py
"""Scored forecasts of the v11 analyst (docs/v11-ai-analyst.md, "Scored forecasts").

Every filled enter plan with a take-profit left a forecast in llm_forecasts: p_tp, the
probability that the take-profit comes before the stop within 30 days of the fill. Here the
outcome is read from closed 1m candles with the execution rules (in the fill candle only the
stop counts; stop first when both are hit in one candle; neither within 30 days is 0) and
compared with two no-skill references:

- P0 = (fill - stop) / (take_profit - stop), a driftless price without a time limit;
- P_placebo, the main one: the plan's stop and take-profit as daily ATR multiples, applied
  from every daily close of the same symbol in the test window that has 30 days after it.

Only fills at least 30 days before the evaluation date are scored, whatever their outcome.
Reported: Brier scores, skill scores, a calibration table and a bootstrap interval over plan
dates (plans from one date are not independent). Below 30 scored plans only counts are shown.

    python -m kripto_bot v11-forecasts --instance v11-astra-high [--as-of 2027-06-01]
"""
import argparse
import json

import numpy as np
import pandas as pd

from kripto_bot.core import data, indicators
from kripto_bot.llm.store import LLMStore
from kripto_bot.trading import store

HORIZON = pd.Timedelta(days=30)
MIN_SCORED = 30
BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001)]
BOOTSTRAP = 2000


def resolve(minutes, fill_time, stop, take_profit, horizon=HORIZON):
    """(outcome 1/0, how: "take_profit" | "stop" | "neither", time) on 1m candles.

    `minutes`: DataFrame with time (open, UTC), high, low, sorted. The fill candle is the one
    opening at fill_time: there only the stop counts."""
    fill_time = pd.Timestamp(fill_time)
    window = minutes[(minutes["time"] >= fill_time) & (minutes["time"] < fill_time + horizon)]
    if window.empty:
        return 0, "neither", None
    low, high = window["low"].to_numpy(), window["high"].to_numpy()
    stop_hit = low <= stop
    tp_hit = high > take_profit
    tp_hit[0] = False                                  # the fill candle: only the stop
    first_stop = int(np.argmax(stop_hit)) if stop_hit.any() else None
    first_tp = int(np.argmax(tp_hit)) if tp_hit.any() else None
    if first_stop is not None and (first_tp is None or first_stop <= first_tp):
        return 0, "stop", window["time"].iloc[first_stop]
    if first_tp is not None:
        return 1, "take_profit", window["time"].iloc[first_tp]
    return 0, "neither", None


def p0(fill, stop, take_profit):
    return (fill - stop) / (take_profit - stop)


def placebo_rate(daily, minutes, k_stop, k_tp, start, end, horizon=HORIZON):
    """Share of daily closes in [start, end - horizon] from which a long with its stop k_stop
    ATRs below and its take-profit k_tp ATRs above the close reaches the take-profit first.

    `daily`: DataFrame time (open), high, low, close; a close is at time + 1 day."""
    daily = daily.reset_index(drop=True)
    atr = indicators.atr(daily)
    times = minutes["time"].values               # datetime64 in UTC (to_numpy() would give objects)
    low, high = minutes["low"].to_numpy(), minutes["high"].to_numpy()
    hits = []
    for i, row in daily.iterrows():
        close_at = row["time"] + pd.Timedelta(days=1)
        if close_at < start or close_at + horizon > end or not np.isfinite(atr.iloc[i]) or i < 14:
            continue
        a, b = np.searchsorted(times, close_at.to_datetime64()), np.searchsorted(times, (close_at + horizon).to_datetime64())
        if b <= a:
            continue
        stop, tp = row["close"] - k_stop * atr.iloc[i], row["close"] + k_tp * atr.iloc[i]
        s, t = low[a:b] <= stop, high[a:b] > tp
        first_s = int(np.argmax(s)) if s.any() else None
        first_t = int(np.argmax(t)) if t.any() else None
        hits.append(int(first_t is not None and (first_s is None or first_t < first_s)))
    return float(np.mean(hits)) if hits else None, len(hits)


def brier(p, o):
    p, o = np.asarray(p, float), np.asarray(o, float)
    return float(np.mean((p - o) ** 2))


def skill(p, ref, o):
    b_ref = brier(ref, o)
    return 1 - brier(p, o) / b_ref if b_ref > 0 else None


def calibration(p, o):
    p, o = np.asarray(p, float), np.asarray(o, float)
    rows = []
    for lo, hi in BINS:
        m = (p >= lo) & (p < hi)
        rows.append({"bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": int(m.sum()),
                     "mean_p": float(p[m].mean()) if m.any() else None,
                     "observed": float(o[m].mean()) if m.any() else None})
    return rows


def bootstrap_skill(df, ref_col, n=BOOTSTRAP, seed=0):
    """90% interval of the skill score vs `ref_col`, resampling plan dates."""
    rng = np.random.default_rng(seed)
    dates = df["plan_date"].unique()
    groups = {d: df[df["plan_date"] == d] for d in dates}
    out = []
    for _ in range(n):
        sample = pd.concat([groups[d] for d in rng.choice(dates, size=len(dates), replace=True)])
        s = skill(sample["p_tp"], sample[ref_col], sample["outcome"])
        if s is not None:
            out.append(s)
    return (float(np.percentile(out, 5)), float(np.percentile(out, 95))) if out else (None, None)


def score(df):
    """Metrics from scored forecasts: columns p_tp, outcome, how, p0, p_placebo, plan_date."""
    out = {"scored": len(df)}
    if df.empty:
        return out
    out.update({f"share {h}": float((df["how"] == h).mean()) for h in ("take_profit", "stop", "neither")})
    if len(df) < MIN_SCORED:
        return out
    out["brier p_tp"] = brier(df["p_tp"], df["outcome"])
    for ref in ("p_placebo", "p0"):
        valid = df[df[ref].notna()]
        out[f"brier {ref}"] = brier(valid[ref], valid["outcome"]) if not valid.empty else None
        out[f"skill vs {ref}"] = skill(valid["p_tp"], valid[ref], valid["outcome"]) if not valid.empty else None
        out[f"skill vs {ref} 90%"] = bootstrap_skill(valid, ref) if not valid.empty else (None, None)
    out["calibration"] = calibration(df["p_tp"], df["outcome"])
    return out


# ---------- from the database ----------

def load(instance, db_path=None):
    st = LLMStore(db_path)
    try:
        f = pd.read_sql_query(
            "SELECT f.*, p.time AS plan_time, p.context FROM llm_forecasts f JOIN llm_plans p ON p.id = f.plan_id "
            "WHERE f.instance=?", st.conn, params=(instance,))
        enters = pd.read_sql_query("SELECT plan FROM llm_plans WHERE instance=? AND accepted=1 AND action='enter'",
                                   st.conn, params=(instance,))
    finally:
        st.close()
    without_tp = sum(json.loads(x).get("take_profit") is None for x in enters["plan"])
    return f, without_tp, len(enters)


def _minutes(symbol, start, end):
    days = (pd.Timestamp.now(tz="UTC") - start).total_seconds() / 86400 + 1
    df = data.load_ohlcv(symbol, "1m", days)
    df = df.rename(columns={"timestamp": "time"})
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize("UTC")
    return df[(df["time"] >= start) & (df["time"] < end)].reset_index(drop=True)


def _daily(symbol, start, end):
    days = (pd.Timestamp.now(tz="UTC") - start).total_seconds() / 86400 + 40
    df = data.load_ohlcv(symbol, "1d", days).rename(columns={"timestamp": "time"})
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize("UTC")
    return df[df["time"] < end].reset_index(drop=True)


def evaluate(instance, as_of, db_path=None, minutes=_minutes, daily=_daily, log=print):
    as_of = pd.Timestamp(as_of)
    as_of = as_of.tz_localize("UTC") if as_of.tzinfo is None else as_of.tz_convert("UTC")
    f, without_tp, enters = load(instance, db_path)
    log(f"{instance}: {len(f)} forecasts; {without_tp} of {enters} accepted enter plans had no take-profit")
    if f.empty:
        return score(pd.DataFrame())
    f["fill_time"] = pd.to_datetime(f["fill_time"], utc=True)
    f = f[f["fill_time"] + HORIZON <= as_of].copy()
    window_start = pd.to_datetime(f["plan_time"], utc=True).min() if not f.empty else as_of
    rows = []
    for sym, group in f.groupby("symbol"):
        mins = minutes(sym, window_start, as_of)
        days = daily(sym, window_start, as_of)
        for r in group.itertuples():
            outcome, how, _ = resolve(mins, r.fill_time, r.stop, r.take_profit)
            atr = json.loads(r.context)["atr"]
            rate, _n = placebo_rate(days, mins, (r.fill_price - r.stop) / atr, (r.take_profit - r.fill_price) / atr,
                                    window_start, as_of)
            rows.append({"symbol": sym, "plan_date": pd.Timestamp(r.plan_time).strftime("%Y-%m-%d"),
                         "p_tp": r.p_tp, "outcome": outcome, "how": how, "p0": p0(r.fill_price, r.stop, r.take_profit),
                         "p_placebo": rate})
    return score(pd.DataFrame(rows))


def main():
    ap = argparse.ArgumentParser(description="Score the v11 p_tp forecasts against candles.")
    ap.add_argument("--instance", required=True)
    ap.add_argument("--as-of", default=None, help="evaluation date (UTC), default now")
    a = ap.parse_args()
    out = evaluate(a.instance, a.as_of or pd.Timestamp.now(tz="UTC"), store.DB_FILE)
    for k, v in out.items():
        if k == "calibration":
            print("calibration:")
            for row in v:
                print(f"  {row['bin']}: n={row['n']}, mean p {row['mean_p']}, observed {row['observed']}")
        else:
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
