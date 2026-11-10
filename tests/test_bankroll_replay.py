import json
import os
import sqlite3

import numpy as np
import pandas as pd
import pytest

from conftest import make_ohlcv
from kripto_bot import instances, paths
from kripto_bot.analysis import bankroll_replay
from kripto_bot.core import data, lower_timeframes
from kripto_bot.trading import run_realtime

N = 380
SYMBOLS = ["AAA/USDT", "BBB/USDT", "CCC/USDT", "DDD/USDT"]


def _days(k, seed):
    t = np.arange(N)
    df = make_ohlcv(N, seed=seed, start="2025-01-01", freq="1D")
    c = 100 * np.exp(0.002 * k * t + 0.03 * np.sin(t / (4 + k)))
    df["open"] = np.r_[c[0], c[:-1]]
    df["close"] = c
    df["high"] = np.maximum(df["open"], c) * 1.01
    df["low"] = np.minimum(df["open"], c) * 0.99
    return df


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    for i, s in enumerate(SYMBOLS):
        df = _days(0.6 + 0.3 * i, i)
        df = df.assign(timestamp=df["timestamp"].astype("int64") // 10**6)
        df.to_csv(d / f"{s.replace('/', '_')}_1d.csv", index=False)
    monkeypatch.setattr(bankroll_replay, "cache_path", lambda s, tf: str(d / f"{s.replace('/', '_')}_{tf}.csv"))
    monkeypatch.setattr(instances, "INSTANCES_FILE", str(tmp_path / "instances.json"))
    monkeypatch.setattr(paths, "RESULTS_DIR", str(tmp_path / "results"))
    return d


def _plain():
    return instances.create({"name": "plain", "strategy": "v9", "timeframe": "1d", "symbols": SYMBOLS})


def _inst(name="br", **setup):
    return instances.create({"name": name, "strategy": "v9", "params": {"fast": 1, "slow": 50}, "timeframe": "1d",
                             "symbols": SYMBOLS, "equity": 1000, "sizing": "bankroll", "stakes": [10, 20, 30, 40],
                             "score_inputs": ["trend_sma", "distance", "slope", "momentum"], **setup})


def test_coverage_is_the_cache_after_the_warm_up(cache):
    cov = bankroll_replay.coverage(_inst())
    first = pd.Timestamp("2025-01-01")
    assert pd.Timestamp(cov["start"]) == first + pd.Timedelta(days=run_realtime.HISTORY)
    assert pd.Timestamp(cov["end"]) == first + pd.Timedelta(days=N)          # the last candle's close
    assert cov["missing"] == [] and cov["stale"] == [] and cov["timeframes"] == ["1d"]


def test_replay_runs_the_paper_code_and_its_rules_hold(cache, tmp_path):
    inst = _inst()
    cov = bankroll_replay.coverage(inst)
    db = bankroll_replay.replay(inst, cov["start"], cov["end"], str(tmp_path / "r.db"))
    # the fakes are gone afterwards
    assert run_realtime.fetch_recent is data.fetch_recent and run_realtime.now_ms is data.now_ms
    assert lower_timeframes.closed_candles.__module__ == lower_timeframes.__name__
    rep = bankroll_replay.report(db, inst)
    s = rep["summary"]
    assert s["candles"] == N - run_realtime.HISTORY and sum(s["entries"].values()) > 0
    assert s["skipped_no_cash"] > 0                       # the cash ran short: priority was exercised
    assert all(c["ok"] for c in rep["checks"]), rep["checks"]


def test_the_result_does_not_depend_on_the_chunk(cache, tmp_path):
    inst = _inst()
    cov = bankroll_replay.coverage(inst)
    q = "SELECT time, kind, symbol, round(amount, 8), score FROM bankroll_ledger ORDER BY id"
    out = []
    for chunk in (7, 300):
        db = bankroll_replay.replay(inst, cov["start"], cov["end"], str(tmp_path / f"r{chunk}.db"), chunk=chunk)
        out.append(sqlite3.connect(db).execute(q).fetchall())
    assert out[0] == out[1] and len(out[0]) > 10


def test_the_report_catches_broken_books(cache, tmp_path):
    inst = _inst()
    cov = bankroll_replay.coverage(inst)
    db = bankroll_replay.replay(inst, cov["start"], cov["end"], str(tmp_path / "r.db"))
    c = sqlite3.connect(db)
    c.execute("UPDATE bankroll_ledger SET value_after = value_after + 5 WHERE id = 3")
    c.execute("UPDATE bankroll_ledger SET amount = amount * 2 WHERE id = (SELECT min(id) FROM bankroll_ledger "
              "WHERE kind = 'RESERVE')")
    c.commit()
    checks = {ch["name"]: ch for ch in bankroll_replay.report(db, inst)["checks"]}
    assert not checks["books"]["ok"] and checks["books"]["count"] == 1
    assert not checks["stakes"]["ok"]


def test_variant_keeps_the_bankroll_and_refuses_the_analyst(cache):
    inst = _inst()
    v = bankroll_replay.variant(inst, "v10", {"entry": "20"})
    assert v.strategy == "v10" and v.params == {"entry": 20} and v.stakes == inst.stakes and v.name == inst.name
    assert bankroll_replay.variant(inst, "v9") is inst
    with pytest.raises(instances.InstanceError):
        bankroll_replay.variant(inst, "v11", {})


class FakePopen:
    calls = []

    def __init__(self, cmd, **kw):
        FakePopen.calls.append(cmd)
        self.pid = os.getpid()                       # "alive" while the test runs


def test_start_job_checks_the_request_then_starts_a_process(cache, monkeypatch):
    monkeypatch.setattr(bankroll_replay.subprocess, "Popen", FakePopen)
    inst = _inst()
    cov = bankroll_replay.coverage(inst)
    with pytest.raises(instances.InstanceError, match="cache covers"):
        bankroll_replay.start_job(inst, "2024-01-01", cov["end"])
    with pytest.raises(instances.InstanceError, match="dates"):
        bankroll_replay.start_job(inst, "", cov["end"])
    with pytest.raises(instances.InstanceError, match="only bankroll instances"):
        bankroll_replay.start_job(_plain(), cov["start"], cov["end"])
    job = bankroll_replay.start_job(inst, cov["start"][:10], cov["end"][:10] + "T23:59:59", "v10", {"entry": 20})
    assert FakePopen.calls[-1][-4:] == ["--instance", "br", "--job", job["id"]]
    [st] = bankroll_replay.jobs("br")
    assert st["state"] == "queued" and st["strategy"] == "v10" and st["params"] == {"entry": 20}
    assert st["start"] == cov["start"] and st["end"] == cov["end"]          # clipped to the cache
    with pytest.raises(instances.InstanceError, match="already running"):
        bankroll_replay.start_job(inst, cov["start"], cov["end"])


def test_a_dead_job_shows_as_failed(cache):
    d = os.path.join(bankroll_replay.jobs_dir("br"), "20260101-000000-abcd")
    os.makedirs(d)
    with open(os.path.join(d, "status.json"), "w") as f:
        json.dump({"id": "20260101-000000-abcd", "state": "running", "pid": 2**22 + 12345, "error": None}, f)
    [st] = bankroll_replay.jobs("br")
    assert st["state"] == "failed" and "stopped" in st["error"]


def test_run_job_writes_the_report_and_drops_the_database(cache, monkeypatch):
    monkeypatch.setattr(bankroll_replay.subprocess, "Popen", FakePopen)
    inst = _inst()
    cov = bankroll_replay.coverage(inst)
    job = bankroll_replay.start_job(inst, cov["start"], cov["end"])
    bankroll_replay.run_job("br", job["id"])
    d = os.path.join(bankroll_replay.jobs_dir("br"), job["id"])
    assert sorted(os.listdir(d)) == ["bot.log", "report.json", "run.log", "status.json"]
    [st] = bankroll_replay.jobs("br")
    assert st["state"] == "done" and st["progress"] == cov["end"][:19]
    assert all(c["ok"] for c in st["report"]["checks"])


def test_old_replays_are_pruned(cache, monkeypatch):
    monkeypatch.setattr(bankroll_replay, "KEEP_JOBS", 2)
    for i in range(4):
        d = os.path.join(bankroll_replay.jobs_dir("br"), f"2026010{i}-000000-abcd")
        os.makedirs(d)
        with open(os.path.join(d, "status.json"), "w") as f:
            json.dump({"id": os.path.basename(d), "state": "done"}, f)
    bankroll_replay._prune("br")
    assert [j["id"][:8] for j in bankroll_replay.jobs("br")] == ["20260103", "20260102"]


def test_panel_lists_and_starts_replays(cache, monkeypatch):
    from kripto_bot.app import dashboard
    monkeypatch.setattr(bankroll_replay.subprocess, "Popen", FakePopen)
    dashboard.app.config["TESTING"] = True
    c = dashboard.app.test_client()
    h = {"X-Requested-With": "kripto-bot"}
    _inst()
    _plain()
    d = c.get("/api/instances/br/replays").get_json()
    assert d["jobs"] == [] and d["coverage"]["start"].startswith("2025-10-28")
    assert c.get("/api/instances/plain/replays").status_code == 400
    r = c.post("/api/instances/br/replays", json={"strategy": "v10", "params": {"entry": 30},
                                                  "start": "2025-11-01", "end": "2026-01-16T23:59:59"}, headers=h)
    assert r.status_code == 202 and r.get_json()["strategy"] == "v10"
    r = c.post("/api/instances/br/replays", json={"start": "2025-11-01", "end": "2026-01-10"}, headers=h)
    assert r.status_code == 400 and "already running" in r.get_json()["error"]
    [job] = c.get("/api/instances/br/replays").get_json()["jobs"]
    assert job["state"] == "queued" and job["params"] == {"entry": 30}
    assert c.post("/api/instances/br/replays", json={"params": [1]}, headers=h).status_code == 400
