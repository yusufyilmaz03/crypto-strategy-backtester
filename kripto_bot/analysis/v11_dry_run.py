# kripto_bot/analysis/v11_dry_run.py
"""The v11 dry run (docs/v11-ai-analyst.md, "Grouped scheduled reviews" and the dry run).

After a daily close the inputs are gathered once (flat state: nothing is traded) and sent:
- with the primary setup twice one symbol per call (single-a, single-b) and once in fixed
  groups of five (group): the comparison that chooses single or grouped calls;
- with the two secondary setups and the no-news control in groups: the rest of the daily
  load, so the plan's usage limit is measured as the test would meet it.
Plans are validated and stored but never traded or evaluated. The report compares how often
the plans agree, rejections, tokens, latency and failures; outcomes are not looked at.

    python -m kripto_bot v11-dry-run run [--loop]     # once now, or after every daily close
    python -m kripto_bot v11-dry-run report
"""
import argparse
import glob
import json
import os
import time

import pandas as pd

from kripto_bot import config, paths
from kripto_bot.core import universe
from kripto_bot.llm import codex_cli, inputs, review
from kripto_bot.llm import provider as p
from kripto_bot.llm.store import LLMStore

SETUPS = {"astra-high": ("gpt-6-astra", "high"), "astra-medium": ("gpt-6-astra", "medium"),
          "sol-high": ("gpt-6.1-sol", "high")}
# (setup, mode, symbols per call, with news), in the order they run: the comparison first.
RUNS = [("astra-high", "single-a", 1, True), ("astra-high", "single-b", 1, True),
        ("astra-high", "group", 5, True), ("astra-medium", "group", 5, True),
        ("sol-high", "group", 5, True), ("astra-high", "group-no-news", 5, False)]
PREFIX = "dry-run:"
SYMBOLS = 20
GROUP_SIZE = 5
AGREE_ATR = 0.5
AGREE_TOLERANCE = 0.10       # grouped agreement may be this much lower than single vs single
RUN_AFTER_CLOSE_S = 5


def provider_for(setup):
    model, effort = SETUPS[setup]
    return codex_cli.CodexCLI(model, effort, config.CODEX_PATH, config.CODEX_VERSION, config.CODEX_HOME,
                              timeout=config.LLM_TIMEOUT_SEC)


def chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def fixed_universe(log=print):
    """The symbols chosen on the first run, saved in results/; later runs reuse them."""
    files = sorted(glob.glob(paths.results("universe_*.json")))
    if files:
        with open(files[0]) as f:
            chosen = json.load(f)
    else:
        chosen = universe.select(SYMBOLS, log=log)
        with open(paths.results(f"universe_{chosen['as_of'][:10]}.json"), "w") as f:
            json.dump(chosen, f, indent=1)
    return chosen["symbols"][:SYMBOLS]


def run_day(call_time, symbols, store, providers=None, snapshot=inputs.snapshot, log=print, sleep=time.sleep):
    """Every run of RUNS on one snapshot; stops early on a usage limit. Returns {(setup, mode): verdicts}."""
    providers = providers or {s: provider_for(s) for s in SETUPS}
    snap = snapshot(symbols, call_time, log=log)
    ordered = [snap.symbols[s] for s in symbols if s in snap.symbols]
    contexts = {s.symbol: inputs.validator_context(s) for s in ordered}
    results = {}
    for setup, mode, size, news in RUNS:
        verdicts = {}
        for group in chunks(ordered, size):
            out = review.run(providers[setup], store, PREFIX + setup, mode, snap, group, contexts,
                             news=news, sleep=sleep)
            verdicts.update(out.verdicts)
            if out.call_kind == p.USAGE_LIMIT:
                log(f"⛔ usage limit reached in {setup} {mode}; the rest of today's runs are skipped")
                results[(setup, mode)] = verdicts
                return results
        ok = sum(v.ok for v in verdicts.values())
        log(f"{setup:<12} {mode:<14} {ok}/{len(verdicts)} plans accepted")
        results[(setup, mode)] = verdicts
    return results


# ---------- report ----------

def decision(row):
    """('hold' | 'enter', entry, stop, take-profit) of a stored plan; a rejected plan holds."""
    plan = json.loads(row["plan"]) if row["accepted"] else {}
    if plan.get("action") != "enter":
        return ("hold", None, None, None)
    ctx = json.loads(row["context"])
    entry = plan["entry"]["price"] if plan["entry"]["type"] == "limit" else ctx["last"]
    return ("enter", entry, plan["stop"], plan.get("take_profit"))


def agree(a, b, atr):
    if a[0] != b[0]:
        return False
    if a[0] == "hold":
        return True
    for x, y in zip(a[1:], b[1:]):
        if (x is None) != (y is None) or (x is not None and abs(x - y) > AGREE_ATR * atr):
            return False
    return True


def comparison(plans):
    """Agreement and rejection rates of the primary setup's single and grouped runs."""
    df = plans[plans["instance"] == PREFIX + "astra-high"].copy()
    df["day"] = df["time"].str[:10]
    modes = ("single-a", "single-b", "group")
    by = {m: {(r["day"], r["symbol"]): r for _, r in df[df["mode"] == m].iterrows()} for m in modes}
    keys = set.intersection(*(set(v) for v in by.values()))
    out = {"symbol_days": len(keys)}
    for a, b in (("single-a", "single-b"), ("single-a", "group"), ("single-b", "group")):
        hits = [agree(decision(by[a][k]), decision(by[b][k]), json.loads(by[a][k]["context"])["atr"]) for k in keys]
        out[f"{a} vs {b}"] = sum(hits) / len(hits) if hits else None
    for m in modes:
        rows = list(by[m].values())
        out[f"rejected {m}"] = sum(not r["accepted"] for r in rows) / len(rows) if rows else None
    if keys:
        single = out["single-a vs single-b"]
        grouped = (out["single-a vs group"] + out["single-b vs group"]) / 2
        out["use groups"] = (grouped >= single - AGREE_TOLERANCE
                             and out["rejected group"] <= max(out["rejected single-a"], out["rejected single-b"]))
    return out


def report(store, log=print):
    calls = store.calls(PREFIX + "%")
    plans = store.plans(PREFIX + "%")
    if calls.empty:
        log("no dry-run calls yet")
        return None
    calls["day"] = calls["time"].str[:10]
    usage = calls.groupby(["day", "instance", "mode"]).agg(
        calls=("id", "size"), failed=("outcome", lambda s: int((s != p.OK).sum())),
        input_tokens=("input_tokens", "sum"), output_tokens=("output_tokens", "sum"),
        reasoning_tokens=("reasoning_tokens", "sum"), mean_latency=("latency_sec", "mean"))
    log(usage.to_string())
    outcomes = calls[calls["outcome"] != p.OK].groupby(["day", "outcome"]).size()
    if not outcomes.empty:
        log("\nfailed attempts:\n" + outcomes.to_string())
    out = comparison(plans)
    log("\n" + "\n".join(f"{k}: {v:.0%}" if isinstance(v, float) else f"{k}: {v}" for k, v in out.items()))
    return out


def seconds_to_next_run(now=None):
    now = now or pd.Timestamp.now(tz="UTC")
    target = now.floor("D") + pd.Timedelta(days=1, seconds=RUN_AFTER_CLOSE_S)
    return (target - now).total_seconds()


def wait_for_next_close(check_every_s=30, clock=lambda: pd.Timestamp.now(tz="UTC"), sleep=time.sleep):
    """Wait by the wall clock: time.sleep stops counting while the Mac sleeps."""
    target = clock() + pd.Timedelta(seconds=seconds_to_next_run(clock()))
    print(f"next run at {target:%Y-%m-%d %H:%M:%S} UTC")
    while clock() < target:
        sleep(min(check_every_s, max(0.0, (target - clock()).total_seconds())))


def main():
    ap = argparse.ArgumentParser(description="The v11 dry run: plans are stored, never traded.")
    ap.add_argument("command", choices=["run", "report"])
    ap.add_argument("--loop", action="store_true", help="run after every daily close, starting with the next one")
    a = ap.parse_args()
    st = LLMStore()
    try:
        if a.command == "report":
            report(st)
            return
        symbols = fixed_universe()
        print(f"v11 dry run on {len(symbols)} symbols: {', '.join(symbols)}")
        while True:
            if a.loop:
                wait_for_next_close()
            run_day(pd.Timestamp.now(tz="UTC"), symbols, st)
            if not a.loop:
                break
    finally:
        st.close()


if __name__ == "__main__":
    main()
