import json
import subprocess
import sys
import time

import pandas as pd
import pytest

import instances
import run_all
from run_all import Service, Supervisor

SLEEP = [sys.executable, "-u", "-c", "import time; print('up'); time.sleep(60)"]


def _svc(tmp_path, name, code):
    return Service(name, [sys.executable, "-u", "-c", code], log_dir=str(tmp_path))


def test_output_is_prefixed_and_logged(tmp_path, capsys):
    s = _svc(tmp_path, "demo", "print('hello')")
    s.start()
    s.proc.wait(5)
    time.sleep(0.2)
    assert "[demo] hello" in capsys.readouterr().out
    assert "hello" in (tmp_path / "demo.log").read_text()


def test_crashed_service_is_restarted(tmp_path, monkeypatch):
    monkeypatch.setattr(run_all, "MIN_BACKOFF_S", 0)
    s = _svc(tmp_path, "crash", "raise SystemExit(3)")
    deadline = time.time() + 5
    while s.restarts < 2 and time.time() < deadline:
        s.check()
        time.sleep(0.05)
    assert s.restarts >= 2 and s.last_exit == 3
    s.stop()


def test_stop_terminates(tmp_path):
    s = Service("sleepy", SLEEP, log_dir=str(tmp_path))
    s.start()
    proc = s.proc
    s.stop(timeout=5)
    assert proc.poll() is not None


@pytest.fixture
def sup(tmp_path, monkeypatch):
    path = str(tmp_path / "instances.json")
    instances.load_all(path)                                  # forward-v9 enabled, testnet-v9 disabled
    monkeypatch.setattr(run_all, "instance_command", lambda inst: SLEEP)
    s = Supervisor(with_dashboard=False, instances_path=path, log_dir=str(tmp_path),
                   status_file=str(tmp_path / "status.json"))
    yield s, path
    for svc in s.services.values():
        svc.stop()


def test_supervisor_follows_instances(sup, tmp_path):
    s, path = sup
    s.tick()
    assert list(s.services) == ["forward-v9"] and s.services["forward-v9"].running

    instances.create({"name": "second"}, path)
    instances.update("second", path, enabled=True)
    s.last_reload = 0
    s.tick()
    assert set(s.services) == {"forward-v9", "second"}

    instances.update("second", path, enabled=False)
    s.last_reload = 0
    s.tick()
    assert set(s.services) == {"forward-v9"}

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["services"]["forward-v9"]["state"] == "running"


def test_restart_request(sup):
    s, path = sup
    s.tick()
    first = s.services["forward-v9"].proc.pid
    time.sleep(0.05)
    instances.update("forward-v9", path, restart_requested_at=pd.Timestamp.now(tz="UTC").isoformat())
    s.last_reload = 0
    s.tick()
    assert s.services["forward-v9"].running and s.services["forward-v9"].proc.pid != first


def test_testnet_without_keys_is_blocked(sup, monkeypatch):
    s, path = sup
    monkeypatch.setattr(run_all.config, "BINANCE_TESTNET_API_KEY", "")
    instances.update("testnet-v9", path, enabled=True)
    s.tick()
    assert "testnet-v9" not in s.services and "keys" in s.blocked["testnet-v9"]


def test_single_instance_lock(tmp_path):
    from store import single_instance

    db = str(tmp_path / "paper.db")
    held = single_instance(db, "a")
    code = ("import sys; sys.path.insert(0, %r); from store import single_instance, AlreadyRunning\n"
            "try:\n    single_instance(%r, sys.argv[1])\nexcept AlreadyRunning:\n    sys.exit(7)"
            % (run_all.HERE, db))
    assert subprocess.run([sys.executable, "-c", code, "a"]).returncode == 7   # same instance refused
    assert subprocess.run([sys.executable, "-c", code, "b"]).returncode == 0   # other instance fine
    held.close()
    assert subprocess.run([sys.executable, "-c", code, "a"]).returncode == 0
