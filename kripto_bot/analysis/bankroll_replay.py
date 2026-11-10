# kripto_bot/analysis/bankroll_replay.py
"""Replay a bankroll instance on cached candles through the live BankrollRunner.

Only the clock and the candle fetches are faked (from the OHLCV cache in data/); the
runners, the engine, the bankroll and the store are the paper bot's own code, writing to
a separate database. This checks the mechanics (stakes per grade, busy candles,
rotation, the books), not performance: the cache is seen data.

The panel starts a replay as a background process (start_job) because the fakes
replace module functions while it runs; `python -m kripto_bot bankroll-replay` runs one
from the command line. Results go to results/replays/<instance>/<job>/.
"""
import argparse
import contextlib
import dataclasses
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid

import pandas as pd

from kripto_bot import instances, paths
from kripto_bot.core import bankroll, lower_timeframes
from kripto_bot.core.data import cache_path, timeframe_ms, to_datetime_index
from kripto_bot.trading import run_realtime
from kripto_bot.trading.store import PaperStore

CHUNK = 300                      # candles per update (run_realtime fetches at most MAX_FETCH - HISTORY new ones)
KEEP_JOBS = 20                   # finished replays kept per instance
EXAMPLES = 20                    # rotation decisions shown in the report


# ---------------------------------------------------------------- cached candles
def _edge_times(path):
    """(first, last) open time of a cache CSV, reading only its ends; None if empty."""
    with open(path, "rb") as f:
        f.readline()
        first = f.readline()
        if not first.strip():
            return None
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        f.seek(max(0, pos - 4096))
        last = f.read().strip().splitlines()[-1]
    to_ts = lambda line: pd.Timestamp(int(float(line.split(b",")[0])), unit="ms")
    return to_ts(first), to_ts(last)


def needed_timeframes(inst):
    tfs = [inst.timeframe]
    uses_lower = (inst.lower_tf_entry or inst.lower_tf_exit
                  or "lower_tf" in inst.bankroll_config().inputs)
    if uses_lower:
        tfs += lower_timeframes.LOWER_TIMEFRAMES.get(inst.timeframe, [])
    return tfs


def coverage(inst):
    """The period the cache can replay: from the first time every needed timeframe has
    data and its warm-up, to the last close of the main timeframe. Symbols without a
    cache, or whose cache ends more than a day earlier, are listed."""
    starts, ends, missing, stale = [], [], [], []
    per_symbol_end = {}
    for tf in needed_timeframes(inst):
        firsts, lasts = [], []
        warm = (run_realtime.HISTORY if tf == inst.timeframe else lower_timeframes.WARMUP) * timeframe_ms(tf)
        for s in inst.symbol_list:
            path = cache_path(s, tf)
            edges = _edge_times(path) if os.path.exists(path) else None
            if edges is None:
                missing.append(f"{s} {tf}")
                continue
            firsts.append(edges[0] + pd.Timedelta(milliseconds=warm))
            lasts.append(edges[1])
            if tf == inst.timeframe:
                per_symbol_end[s] = edges[1]
        if firsts:
            starts.append(min(firsts))
            ends.append(max(lasts) + pd.Timedelta(milliseconds=timeframe_ms(tf)))
    if not starts:
        return {"start": None, "end": None, "missing": missing, "stale": [], "timeframes": needed_timeframes(inst)}
    end = min(ends)
    stale = sorted(s for s, t in per_symbol_end.items() if end - t > pd.Timedelta(days=1))
    return {"start": max(starts).isoformat(), "end": end.isoformat(), "missing": missing, "stale": stale,
            "timeframes": needed_timeframes(inst)}


class CacheSource:
    """Candles from the cache as the paper bot would have fetched them at `now`."""

    def __init__(self):
        self.now = 0              # ms
        self.frames = {}

    def frame(self, symbol, timeframe):
        key = (symbol, timeframe)
        if key not in self.frames:
            path = cache_path(symbol, timeframe)
            df = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"])
            df = to_datetime_index(df).drop_duplicates("timestamp").sort_values("timestamp")
            self.frames[key] = df.reset_index(drop=True)
        return self.frames[key]

    def closed(self, symbol, timeframe):
        df = self.frame(symbol, timeframe)
        cut = pd.Timestamp(self.now - timeframe_ms(timeframe), unit="ms")
        return df.iloc[:df["timestamp"].searchsorted(cut, side="right")]

    def fetch_recent(self, symbol, timeframe, limit=200, **kw):
        return self.closed(symbol, timeframe).tail(limit).reset_index(drop=True)

    def closed_candles(self, symbol, timeframe, since, now=None, exchange=None):
        df = self.closed(symbol, timeframe)
        return df[df["timestamp"] >= pd.Timestamp(since)].reset_index(drop=True)


@contextlib.contextmanager
def faked(source):
    """Point the paper bot's clock and candle fetches at `source` while the block runs."""
    saved = (run_realtime.fetch_recent, run_realtime.now_ms, lower_timeframes.closed_candles)
    run_realtime.fetch_recent = source.fetch_recent
    run_realtime.now_ms = lambda: source.now
    lower_timeframes.closed_candles = source.closed_candles
    try:
        yield source
    finally:
        run_realtime.fetch_recent, run_realtime.now_ms, lower_timeframes.closed_candles = saved


def variant(inst, strategy=None, params=None):
    """The instance with another strategy (same bankroll, symbols and timeframe)."""
    if not strategy or (strategy == inst.strategy and params is None):
        return inst
    if strategy == instances.ANALYST:
        raise instances.InstanceError("the AI analyst has no signals to replay")
    return dataclasses.replace(inst, strategy=strategy, params=instances._clean_params(strategy, params or {}))


def replay(inst, start, end, db_path, chunk=CHUNK, progress=None, out=None):
    """Run `inst` from `start` to `end` (UTC) on the cache into a new database at
    db_path. progress(time) is called after each update; out receives the bot's prints."""
    if os.path.exists(db_path):
        os.remove(db_path)
    store = PaperStore(db_path)
    source = CacheSource()
    step = timeframe_ms(inst.timeframe)
    source.now = int(pd.Timestamp(start).value // 10**6) + run_realtime.CLOSE_DELAY_S * 1000
    stop = int(pd.Timestamp(end).value // 10**6)
    with faked(source), contextlib.redirect_stdout(out or open(os.devnull, "w")):
        cfg = inst.engine_config()
        runners = [run_realtime.SymbolRunner(s, inst.timeframe, inst.strategy, cfg, store, params=inst.params,
                                             label=inst.run_key, lower_tf_entry=inst.lower_tf_entry,
                                             lower_tf_exit=inst.lower_tf_exit, lower_tf_sma=inst.lower_tf_sma,
                                             trend_sma=inst.trend_sma)
                   for s in inst.symbol_list]
        br = run_realtime.BankrollRunner(runners, store, inst, rules={})
        while True:
            br.update()
            if progress:
                progress(pd.Timestamp(source.now, unit="ms"))
            if source.now >= stop:
                break
            source.now = min(source.now + chunk * step, stop)
    store.conn.close()
    return db_path


# ---------------------------------------------------------------- checks
def _check(name, problems, detail=""):
    return {"name": name, "ok": not problems, "problems": problems[:10], "count": len(problems), "detail": detail}


def report(db_path, inst):
    """What happened and whether the bankroll's rules held, from a replay's database."""
    cfg = inst.bankroll_config()
    step = pd.Timedelta(milliseconds=timeframe_ms(inst.timeframe))
    c = sqlite3.connect(db_path)
    led = pd.read_sql("SELECT * FROM bankroll_ledger ORDER BY id", c)
    snaps = pd.read_sql("SELECT * FROM bankroll_snapshots ORDER BY time", c)
    trades = pd.read_sql("SELECT * FROM trades", c)
    sig = pd.read_sql("SELECT candle_time, symbol, signal, score, action FROM signals", c)
    c.close()
    led["t"] = pd.to_datetime(led["time"])
    note = led["note"].fillna("")
    start_value = float(inst.equity)

    # what happened
    res = led[led.kind == "RESERVE"]
    from_rotation = note.loc[res.index].str.contains("rotating out")
    decisions = led[(led.kind == "ROTATE") & (led.amount == 0)]
    buys = sig[sig.signal == "BUY"]
    last = snaps.iloc[-1] if len(snaps) else None
    summary = {
        "candles": max(int(len(snaps)) - 1, 0),          # the first snapshot is the start, before any candle
        "first": snaps.time.iloc[0] if len(snaps) else None, "last": snaps.time.iloc[-1] if len(snaps) else None,
        "value": None if last is None else float(last.value),
        "return_pct": None if last is None else (float(last.twr) - 1) * 100,
        "entries": {g: int((res.grade == g).sum()) for g in bankroll.GRADES},
        "cut_to_free_cash": int(note.loc[res.index].str.contains("cut to the free cash").sum()),
        "skipped": int((led.kind == "SKIP").sum()),
        "skipped_no_cash": int(((led.kind == "SKIP") & note.str.contains("no free cash")).sum()),
        "rotations": int(len(decisions)),
        "rotation_entries": int(from_rotation.sum()),
        "trims": int((led.kind == "TRIM").sum()),
        "stake_cuts": int(((led.kind == "RELEASE") & note.str.contains("rotation brought less")).sum()),
        "buy_signals": int(len(buys)),
        "buy_score_max": None if buys.score.dropna().empty else float(buys.score.max()),
        "buy_scores_from": {g: int((buys.score >= t).sum()) for g, t in zip(bankroll.GRADES[1:], bankroll.GRADE_FROM)},
        "closed_trades": int(trades.exit_time.notna().sum()) if "exit_time" in trades else 0,
    }

    checks = []
    # 1. books
    bad = led[(led.free_after + led.reserved_after + led.invested_after - led.value_after).abs() > 0.01]
    checks.append(_check("books", [f"{r.time} {r.kind} {r.symbol or ''}" for r in bad.itertuples()],
                         "free + reserved + invested = value on every ledger row"))
    # 2. free cash below zero only where an entry was funded against rotation proceeds
    rot_times = set(res[from_rotation].time)
    neg = snaps[(snaps.free < -1e-6) & ~snaps.time.isin(rot_times)]
    checks.append(_check("free_cash", [f"{r.time}: {r.free:.2f}" for r in neg.itertuples()],
                         "free cash ends a candle below zero only after funding an entry against a rotation"))
    # 3. stakes
    problems = []
    for r in res.itertuples():
        g = bankroll.grade(r.score)
        want = start_value * cfg.stakes[g] / 100
        if cfg.max_entry:
            want = min(want, cfg.max_entry)
        if bankroll.GRADES[g] != r.grade:
            problems.append(f"{r.time} {r.symbol}: score {r.score} graded {r.grade}")
        elif -r.amount > want + 1e-6:
            problems.append(f"{r.time} {r.symbol}: {-r.amount:.2f} above the {r.grade} stake {want:.2f}")
        elif not re.search("cut|rotating", r.note or "") and abs(-r.amount - want) > 1e-6:
            problems.append(f"{r.time} {r.symbol}: {-r.amount:.2f}, the {r.grade} stake is {want:.2f}")
        elif -r.amount < cfg.floor - 1e-6:
            problems.append(f"{r.time} {r.symbol}: {-r.amount:.2f} below the minimum entry")
    checks.append(_check("stakes", problems, "each entry gets its grade's stake, less only when cut to the free cash"))
    # 4. strongest first
    problems = []
    for t, g in led[led.kind.isin(["RESERVE", "SKIP"])].groupby("time"):
        gn = g.note.fillna("")
        funded = g[(g.kind == "RESERVE") & ~gn.str.contains("rotating")]
        skipped = g[(g.kind == "SKIP") & gn.str.contains("no free cash")]
        if len(funded) and len(skipped) and skipped.score.max() > funded.score.min() + 1e-9:
            problems.append(f"{t}: skipped {skipped.score.max():g} while {funded.score.min():g} was funded")
    checks.append(_check("priority", problems, "on a busy candle higher scores are funded first"))
    # 5. rotation rules
    problems, examples = [], []
    per_candle = decisions.groupby("time").size()
    problems += [f"{t}: {n} rotations" for t, n in per_candle[per_candle > bankroll.ROTATE_MAX].items()]
    for d in decisions.itertuples():
        entry = res[(res.time == d.time) & res.note.fillna("").str.contains(re.escape(d.symbol), regex=True)
                    & res.note.fillna("").str.contains("rotating out")]
        if len(entry) != 1:
            problems.append(f"{d.time} {d.symbol}: no entry funded by this rotation")
            continue
        e = entry.iloc[0]
        if e.grade != bankroll.GRADES[-1]:
            problems.append(f"{d.time}: rotation for a {e.grade} entry")
        if e.score - d.score < bankroll.ROTATE_MARGIN - 1e-9:
            problems.append(f"{d.time} {d.symbol}: only {e.score - d.score:.1f} points below {e.symbol}")
        held_trades = trades[(trades.symbol == d.symbol) & (pd.to_datetime(trades.entry_time) <= d.t)]
        if len(held_trades):
            entry_time = pd.to_datetime(held_trades.entry_time).max()
            held = int((d.t - entry_time) / step) + 1
            if held < bankroll.ROTATE_MIN_HOLD:
                problems.append(f"{d.time} {d.symbol}: held {held} candles")
        fill = led[(led.id > d.id) & (led.symbol == d.symbol) & led.kind.isin(["ROTATE", "TRIM"]) & (led.amount > 0)]
        expected = float(re.search(r"expected ([\d.]+)", d.note).group(1))
        got, pnl = None, None
        if fill.empty:
            if d.t + step <= pd.Timestamp(summary["last"]):
                problems.append(f"{d.time} {d.symbol}: never sold")
        else:
            f = fill.iloc[0]
            got = float(f.amount)
            m = re.search(r"PnL ([+-][\d.]+)", f.note or "")
            pnl = float(m.group(1)) if m else None
            if f.t - d.t != step:
                problems.append(f"{d.time} {d.symbol}: sold at {f.time}, not the next open")
            if cfg.profit_only and pnl is not None and pnl <= 0:
                problems.append(f"{d.time} {d.symbol}: sold at a loss ({pnl:+.2f})")
        if len(examples) < EXAMPLES:
            examples.append({"time": d.time, "sold": d.symbol, "sold_score": d.score, "for": e.symbol,
                             "for_score": e.score, "what": "close" if "close at" in d.note else "reduce",
                             "expected": expected, "got": got, "pnl": pnl})
    checks.append(_check("rotation", problems,
                         f"margin {bankroll.ROTATE_MARGIN:g} points, held {bankroll.ROTATE_MIN_HOLD}+ candles, "
                         f"{'profit only, ' if cfg.profit_only else ''}at most {bankroll.ROTATE_MAX} per candle, "
                         "sold at the next open, only for Very strong entries"))
    return {"summary": summary, "checks": checks, "rotation_examples": examples}


# ---------------------------------------------------------------- jobs (the panel)
def jobs_dir(name):
    return os.path.join(paths.RESULTS_DIR, "replays", name)


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, default=str)
    os.replace(tmp, path)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def jobs(name):
    """The instance's replays, newest first: status, and the report once done."""
    base = jobs_dir(name)
    out = []
    for job in sorted(os.listdir(base), reverse=True) if os.path.isdir(base) else []:
        path = os.path.join(base, job, "status.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            st = json.load(f)
        if st["state"] in ("queued", "running") and not _alive(st.get("pid")):
            st["state"], st["error"] = "failed", st.get("error") or "the replay process stopped"
        rep = os.path.join(base, job, "report.json")
        if st["state"] == "done" and os.path.exists(rep):
            with open(rep) as f:
                st["report"] = json.load(f)
        out.append(st)
    return out


def running(name):
    return any(j["state"] in ("queued", "running") for j in jobs(name))


def start_job(inst, start, end, strategy=None, params=None):
    """Check the request and start a replay in a background process; returns its status."""
    if inst.sizing != "bankroll":
        raise instances.InstanceError("only bankroll instances can be replayed")
    run_inst = variant(inst, strategy, params)
    cov = coverage(run_inst)
    if cov["start"] is None:
        raise instances.InstanceError("no cached candles for this instance")
    try:
        start, end = pd.Timestamp(start), pd.Timestamp(end)
    except (TypeError, ValueError):
        raise instances.InstanceError("start and end must be dates")
    if pd.isna(start) or pd.isna(end):
        raise instances.InstanceError("start and end must be dates")
    if start.tzinfo is not None:
        start, end = start.tz_convert(None), end.tz_convert(None)
    lo, hi = pd.Timestamp(cov["start"]), pd.Timestamp(cov["end"])
    if start < lo.floor("D") or end > hi.floor("D") + pd.Timedelta(days=1) or start >= end:
        raise instances.InstanceError(f"the cache covers {lo:%Y-%m-%d %H:%M} to {hi:%Y-%m-%d %H:%M} UTC")
    start, end = max(start, lo), min(end, hi)
    if running(inst.name):
        raise instances.InstanceError("a replay of this instance is already running")
    job = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = os.path.join(jobs_dir(inst.name), job)
    os.makedirs(d)
    status = {"id": job, "instance": inst.name, "strategy": run_inst.strategy, "params": run_inst.params,
              "timeframe": inst.timeframe, "start": start.isoformat(), "end": end.isoformat(), "state": "queued",
              "progress": None, "requested_at": pd.Timestamp.now(tz="UTC").isoformat(), "error": None}
    _write(os.path.join(d, "status.json"), status)
    cmd = [sys.executable, "-m", "kripto_bot", "bankroll-replay", "--instance", inst.name, "--job", job]
    with open(os.path.join(d, "run.log"), "w") as log:
        proc = subprocess.Popen(cmd, cwd=paths.ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    current = _read_status(d) or status       # the process may already have written "running"
    current.setdefault("pid", proc.pid)
    _write(os.path.join(d, "status.json"), current)
    _prune(inst.name)
    return {**status, "pid": proc.pid}


def _read_status(d):
    try:
        with open(os.path.join(d, "status.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _prune(name):
    done = [j for j in jobs(name) if j["state"] not in ("queued", "running")]
    for j in done[KEEP_JOBS:]:
        d = os.path.join(jobs_dir(name), j["id"])
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
        os.rmdir(d)


def run_job(name, job, keep_db=False):
    """The background process: replay, write the report, drop the database."""
    d = os.path.join(jobs_dir(name), job)
    st = _read_status(d)
    st.update(state="running", pid=os.getpid(), started_at=pd.Timestamp.now(tz="UTC").isoformat())
    _write(os.path.join(d, "status.json"), st)
    db = os.path.join(d, "paper.db")
    t0 = time.time()
    try:
        inst = variant(instances.get(name), st["strategy"], st["params"])

        def progress(t):
            st["progress"] = t.isoformat()
            _write(os.path.join(d, "status.json"), st)

        with open(os.path.join(d, "bot.log"), "w") as out:
            replay(inst, st["start"], st["end"], db, progress=progress, out=out)
        _write(os.path.join(d, "report.json"), report(db, inst))
        st.update(state="done", seconds=round(time.time() - t0))
    except Exception as e:
        st.update(state="failed", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        st["finished_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        _write(os.path.join(d, "status.json"), st)
        if not keep_db:
            for side in ("", "-wal", "-shm"):
                if os.path.exists(db + side):
                    os.remove(db + side)


def _print_report(rep):
    s = rep["summary"]
    print(f"{s['first']} → {s['last']}: {s['candles']} candles, value {s['value']:.2f}, "
          f"return {s['return_pct']:+.2f}%, {s['closed_trades']} closed trades")
    print("entries: " + ", ".join(f"{g} {n}" for g, n in s["entries"].items())
          + f" | cut {s['cut_to_free_cash']}, skipped {s['skipped']} ({s['skipped_no_cash']} for cash)")
    print(f"rotations {s['rotations']} for {s['rotation_entries']} entries, TRIM {s['trims']}, "
          f"stake cuts {s['stake_cuts']} | BUY score max {s['buy_score_max']}, {s['buy_scores_from']}")
    for ch in rep["checks"]:
        print(f"{'✅' if ch['ok'] else '❌'} {ch['name']}: {ch['detail']}"
              + ("" if ch["ok"] else f" — {ch['count']} problem(s): {'; '.join(ch['problems'])}"))


def main():
    ap = argparse.ArgumentParser(description="Replay a bankroll instance on cached candles (mechanics check).")
    ap.add_argument("--instance", required=True)
    ap.add_argument("--job", help="(the panel) run this queued replay")
    ap.add_argument("--start", help="UTC start; default: the start of the cache")
    ap.add_argument("--end", help="UTC end; default: the end of the cache")
    ap.add_argument("--strategy", help="another strategy with the same bankroll, e.g. v9")
    ap.add_argument("--params", help='strategy parameters as JSON, e.g. \'{"fast": 1, "slow": 50}\'')
    ap.add_argument("--keep-db", action="store_true", help="keep the replay's paper.db")
    a = ap.parse_args()
    if a.job:
        run_job(a.instance, a.job, a.keep_db)
        return
    inst = variant(instances.get(a.instance), a.strategy, json.loads(a.params) if a.params else None)
    cov = coverage(inst)
    if cov["start"] is None:
        raise SystemExit("❌ no cached candles for this instance")
    for what in ("missing", "stale"):
        if cov[what]:
            print(f"⚠️ {what}: {', '.join(cov[what])}")
    start, end = a.start or cov["start"], a.end or cov["end"]
    d = os.path.join(jobs_dir(inst.name), "cli")
    os.makedirs(d, exist_ok=True)
    db = os.path.join(d, "paper.db")
    print(f"replaying {inst.name} ({inst.strategy} {inst.params}, {inst.timeframe}) {start} → {end}")
    with open(os.path.join(d, "bot.log"), "w") as out:
        replay(inst, start, end, db, progress=lambda t: print(f"  {t}", flush=True), out=out)
    rep = report(db, inst)
    _write(os.path.join(d, "report.json"), rep)
    _print_report(rep)
    if not a.keep_db:
        os.remove(db)
    print(f"report: {os.path.join(d, 'report.json')}")


if __name__ == "__main__":
    main()
