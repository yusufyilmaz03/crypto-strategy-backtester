import fcntl
import sqlite3

import pytest

from kripto_bot import __main__ as cli
from kripto_bot import paths


def _layout(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    db = root / "paper.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
    (root / "paper.db-wal").write_text("wal of the real database")
    (root / "testnet-demo.db").write_text("db")
    (root / "instances.json").write_text("[]")
    (root / "logs").mkdir()
    (root / "logs" / "forward-v9.log").write_text("line\n")
    (root / "research_meta.json").write_text("{}")
    (root / "paper.db.forward-v9.lock").write_text("")
    (root / "status.json").write_text("{}")
    return root


def test_migrate_moves_state_and_results(tmp_path):
    tmp_path = _layout(tmp_path)
    rt, res = tmp_path / "runtime", tmp_path / "results"
    rt.mkdir()
    (rt / "paper.db-wal").write_text("stale wal of a deleted database")
    moved, skipped = paths.migrate_legacy(str(tmp_path), str(rt), str(res))
    assert skipped == []
    assert {"paper.db", "paper.db-wal", "testnet-demo.db", "instances.json", "logs",
            "research_meta.json"} <= set(moved)
    assert (rt / "paper.db-wal").read_text() == "wal of the real database"   # travels with its database
    assert (rt / "logs" / "forward-v9.log").exists() and (res / "research_meta.json").exists()
    assert not (tmp_path / "paper.db").exists() and not (tmp_path / "paper.db.forward-v9.lock").exists()
    assert not (tmp_path / "status.json").exists()
    assert paths.migrate_legacy(str(tmp_path), str(rt), str(res)) == ([], [])  # idempotent


def test_migrate_keeps_existing_targets(tmp_path):
    tmp_path = _layout(tmp_path)
    rt = tmp_path / "runtime"
    rt.mkdir()
    (rt / "instances.json").write_text('[{"name": "new"}]')
    _, skipped = paths.migrate_legacy(str(tmp_path), str(rt), str(tmp_path / "results"))
    assert skipped == ["instances.json"]
    assert (rt / "instances.json").read_text() == '[{"name": "new"}]'
    assert (tmp_path / "instances.json").exists()          # left alone, nothing overwritten


def test_migrate_refuses_while_old_process_runs(tmp_path):
    tmp_path = _layout(tmp_path)
    with open(tmp_path / "paper.db.forward-v9.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(paths.LegacyInUse, match="forward-v9"):
            paths.migrate_legacy(str(tmp_path), str(tmp_path / "runtime"), str(tmp_path / "results"))
    assert (tmp_path / "paper.db").exists()


def test_cli_usage_and_unknown_command(capsys, monkeypatch):
    monkeypatch.setattr(paths, "migrate_legacy", lambda: pytest.fail("no migration for help"))
    assert cli.main([]) == 0 and "forward-report" in capsys.readouterr().out
    assert cli.main(["nope"]) == 2 and "unknown command" in capsys.readouterr().err


def test_cli_dispatches_to_module(monkeypatch):
    calls = []
    monkeypatch.setattr(paths, "migrate_legacy", lambda: ([], []))
    monkeypatch.setattr(cli.runpy, "run_module", lambda mod, **kw: calls.append((mod, list(cli.sys.argv))))
    monkeypatch.setattr(cli.sys, "argv", ["x"])
    assert cli.main(["paper", "--instance", "demo"]) == 0
    assert calls == [("kripto_bot.trading.run_realtime", ["kripto_bot paper", "--instance", "demo"])]
