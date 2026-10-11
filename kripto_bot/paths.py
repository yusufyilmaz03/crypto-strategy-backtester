# kripto_bot/paths.py
"""Where files live. Everything the bot writes stays out of the source tree:

    runtime/   databases, instance/settings/status files, logs and the kill switch
    results/   backtest and research outputs (CSV, JSON, charts)
    data/      OHLCV cache

All three are gitignored and created on first use.
"""
import fcntl
import glob
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
RESULTS_DIR = os.path.join(ROOT, "results")
DATA_DIR = os.path.join(ROOT, "data")
LOG_DIR = os.path.join(RUNTIME_DIR, "logs")


def runtime(name):
    """Path of a file in runtime/; an absolute path is returned unchanged."""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    return name if os.path.isabs(name) else os.path.join(RUNTIME_DIR, name)


def results(name):
    """Path of a file in results/."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    return os.path.join(RESULTS_DIR, name)


# ---------- one-time move from the old flat layout ----------
LEGACY_DATABASES = ["paper.db", "testnet.db", "testnet-*.db", "live.db", "playground.db"]
LEGACY_RUNTIME = ["settings.json", "instances.json", "KILL_SWITCH", "logs"]
LEGACY_RESULTS = ["csv_old", "multi_backtest_strategies.csv", "research_results.csv", "research_folds.csv",
                  "research_summary.csv", "research_meta.json", "research_holdout.csv", "summary.png"]
SQLITE_SIDE_FILES = ("-wal", "-shm", "-journal")


class LegacyInUse(RuntimeError):
    pass


def _held(lock_file):
    with open(lock_file, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def migrate_legacy(root=ROOT, runtime_dir=None, results_dir=None):
    """Move state and outputs left in the project root by older versions into
    runtime/ and results/. Refuses while an old bot process still holds a lock
    in the root (moving an open database would split its data). A database moves
    together with its -wal/-shm files; stale side files at the target are replaced
    so they can't be paired with the wrong database. Nothing is overwritten: a file
    whose target already exists stays in the root. Returns (moved, skipped) names."""
    runtime_dir = runtime_dir or os.path.join(root, "runtime")
    results_dir = results_dir or os.path.join(root, "results")
    locks = glob.glob(os.path.join(root, "*.lock"))
    busy = [os.path.basename(p) for p in locks if _held(p)]
    if busy:
        raise LegacyInUse(f"an old bot process is still running ({', '.join(busy)} is locked); stop it first")
    moved, skipped = [], []

    def move(src, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(src, os.path.join(dest_dir, os.path.basename(src)))
        moved.append(os.path.basename(src))

    for pattern in LEGACY_DATABASES:
        for src in glob.glob(os.path.join(root, pattern)):
            target = os.path.join(runtime_dir, os.path.basename(src))
            if os.path.exists(target):
                skipped.append(os.path.basename(src))
                continue
            for side in SQLITE_SIDE_FILES:
                if os.path.exists(target + side):
                    os.remove(target + side)
            move(src, runtime_dir)
            for side in SQLITE_SIDE_FILES:
                if os.path.exists(src + side):
                    move(src + side, runtime_dir)
    for patterns, dest in ((LEGACY_RUNTIME, runtime_dir), (LEGACY_RESULTS, results_dir)):
        for pattern in patterns:
            for src in glob.glob(os.path.join(root, pattern)):
                if os.path.exists(os.path.join(dest, os.path.basename(src))):
                    skipped.append(os.path.basename(src))
                else:
                    move(src, dest)
    for p in locks:     # stale: nobody holds them
        os.remove(p)
    old_status = os.path.join(root, "status.json")
    if os.path.exists(old_status):
        os.remove(old_status)
    return moved, skipped
