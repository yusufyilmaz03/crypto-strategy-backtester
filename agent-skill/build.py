#!/usr/bin/env python3
# agent-skill/build.py
"""Build the agent skill package: dist/crypto-strategy-backtester.zip with SKILL.md at its root.

    python agent-skill/build.py

The package is assembled from three places:
  package/   SKILL.md, scripts, references, examples, assets (written for the skill)
  overlay/   skill versions of kripto_bot/paths.py and kripto_bot/core/data.py
             (public klines over HTTPS, no ccxt, no .env)
  the repo   engine, strategies, indicators, metrics, evaluation and walk-forward
             optimization, copied unchanged so the skill runs the tested code

Before zipping, every Python file is scanned: the build fails if anything that
could send an order, read credentials or reach another host got in.
"""
import ast
import hashlib
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
NAME = "crypto-strategy-backtester"
DIST = os.path.join(HERE, "dist")
STAGE = os.path.join(DIST, NAME)
ZIP = os.path.join(DIST, f"{NAME}.zip")

# Copied unchanged from the repository into scripts/kripto_bot/.
VENDORED = [
    "kripto_bot/__init__.py",
    "kripto_bot/core/__init__.py",
    "kripto_bot/core/indicators.py",
    "kripto_bot/core/strategy.py",
    "kripto_bot/core/engine.py",
    "kripto_bot/core/metrics.py",
    "kripto_bot/analysis/__init__.py",
    "kripto_bot/analysis/evaluation.py",
    "kripto_bot/analysis/optimize.py",
]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")

# ---- safety scan ----
ALLOWED_IMPORTS = {
    "argparse", "ast", "dataclasses", "importlib", "inspect", "itertools", "json", "math", "os", "re",
    "statistics", "sys", "time", "uuid", "numpy", "pandas", "kripto_bot", "strategy_checks",
}
NETWORK_IMPORTS = {"urllib", "ssl", "certifi"}          # only in the data module
NETWORK_FILE = "scripts/kripto_bot/core/data.py"
ALLOWED_HOSTS = {"https://data-api.binance.vision", "https://api.binance.com"}
FORBIDDEN_NAMES = ("apikey", "api_key", "secret", "password", "token", "create_order", "dotenv",
                   "environ", "getenv", "withdraw")
FORBIDDEN_TEXT = FORBIDDEN_NAMES + (".env", "/order", "/account", "/sapi", "/fapi")
FORBIDDEN_CALLS = {"eval", "exec", "__import__", "system", "popen", "remove", "unlink", "rmtree"}
FORBIDDEN_FILES = (".env", ".db", ".pem", ".key", ".lock")


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def scan_python(rel, source):
    """Problems in one Python file (empty list = clean): imports outside the allowlist,
    dangerous calls, credential or order words in names and strings, unknown hosts.
    Docstrings and comments are not code and are skipped."""
    problems = []
    tree = ast.parse(source, rel)
    docstrings = _docstrings(tree)
    for node in ast.walk(tree):
        line = f"{rel}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            for name in names:
                root = name.split(".")[0]
                if root in ALLOWED_IMPORTS or (root in NETWORK_IMPORTS and rel == NETWORK_FILE):
                    continue
                problems.append(f"{line}: import of '{name}' is not allowed")
        elif isinstance(node, ast.Call):
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if called in FORBIDDEN_CALLS:
                problems.append(f"{line}: call to {called}() is not allowed")
        elif isinstance(node, (ast.Name, ast.Attribute, ast.arg, ast.keyword)):
            ident = (getattr(node, "id", None) or getattr(node, "attr", None) or getattr(node, "arg", None) or "")
            for word in FORBIDDEN_NAMES:
                if word in ident.lower():
                    problems.append(f"{line}: name '{ident}' contains '{word}'")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            text = node.value.lower()
            if text.startswith(("http://", "https://")) and node.value not in ALLOWED_HOSTS:
                problems.append(f"{line}: URL {node.value!r} is not an allowed host")
            for word in FORBIDDEN_TEXT:
                if word in text:
                    problems.append(f"{line}: string contains '{word}': {node.value[:60]!r}")
    return problems


def safety_scan(stage):
    problems = []
    for folder, _, files in os.walk(stage):
        for name in files:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, stage).replace(os.sep, "/")
            if name.startswith(".") or name.endswith(FORBIDDEN_FILES):
                problems.append(f"{rel}: file type is not allowed in the package")
            if os.path.islink(path):
                problems.append(f"{rel}: symbolic links are not allowed")
            if name.endswith(".py"):
                with open(path, encoding="utf-8") as f:
                    source = f.read()
                problems += scan_python(rel, source)
    return problems


# ---- build ----
def build():
    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    shutil.copytree(os.path.join(HERE, "package"), STAGE, ignore=IGNORE)
    for rel in VENDORED:
        target = os.path.join(STAGE, "scripts", rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, rel), target)
    shutil.copytree(os.path.join(HERE, "overlay"), os.path.join(STAGE, "scripts"), ignore=IGNORE, dirs_exist_ok=True)
    license_file = os.path.join(REPO, "LICENSE")
    if not os.path.isfile(license_file):
        sys.exit("build failed: the repository has no LICENSE file")
    shutil.copyfile(license_file, os.path.join(STAGE, "LICENSE"))

    if not os.path.isfile(os.path.join(STAGE, "SKILL.md")):
        sys.exit("build failed: SKILL.md is missing")
    problems = safety_scan(STAGE)
    if problems:
        print("build failed: the safety scan found problems\n  " + "\n  ".join(problems), file=sys.stderr)
        sys.exit(1)

    files = sorted(os.path.relpath(os.path.join(folder, name), STAGE)
                   for folder, _, names in os.walk(STAGE) for name in names)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(os.path.join(STAGE, rel), rel.replace(os.sep, "/"))
    with open(ZIP, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    print(f"{len(files)} files:")
    for rel in files:
        print(f"  {rel}")
    print(f"safety scan: clean\n{ZIP}\n{os.path.getsize(ZIP) / 1024:.1f} KB, sha256 {digest}")


if __name__ == "__main__":
    build()
