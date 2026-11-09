# kripto_bot/llm/store.py
"""Records of the v11 analyst in paper.db (docs/v11-ai-analyst.md, "Records").

llm_calls holds every attempt of every call: the prompt, the raw answer and events, the
model setup and the token counts. llm_plans holds every plan in an answer, accepted or not,
with the validator's reasons. Rows are only ever added, so any plan can be audited later.
`instance` is the v11 instance name, or a dry-run label.
"""
import json

import pandas as pd

from kripto_bot.trading import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, instance TEXT, time TEXT, kind TEXT, mode TEXT,
    symbols TEXT, retry INTEGER, outcome TEXT, model TEXT, effort TEXT, client_version TEXT,
    input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
    latency_sec REAL, exit_code INTEGER, prompt TEXT, raw TEXT, events TEXT, stderr TEXT
);
CREATE INDEX IF NOT EXISTS llm_calls_instance_time ON llm_calls (instance, time);
CREATE TABLE IF NOT EXISTS llm_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, instance TEXT, call_id INTEGER, time TEXT, mode TEXT,
    symbol TEXT, action TEXT, accepted INTEGER, reasons TEXT, plan TEXT, context TEXT
);
CREATE INDEX IF NOT EXISTS llm_plans_instance_symbol ON llm_plans (instance, symbol, time);
"""


def _iso(t):
    return pd.Timestamp(t).isoformat()


class LLMStore:
    def __init__(self, path=None):
        self.conn = store._connect(path or store.DB_FILE)
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    def record_call(self, instance, time, kind, mode, symbols, prompt, result):
        """One attempt (an LLMResult); returns its id."""
        u = result.usage or {}
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO llm_calls (instance, time, kind, mode, symbols, retry, outcome, model, effort, "
                "client_version, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, latency_sec, "
                "exit_code, prompt, raw, events, stderr) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (instance, _iso(time), kind, mode, json.dumps(symbols), int(result.retry), result.kind, result.model,
                 result.effort, result.client_version, u.get("input_tokens"), u.get("cached_input_tokens"),
                 u.get("output_tokens"), u.get("reasoning_output_tokens"), result.latency_sec, result.exit_code,
                 prompt, result.raw, result.events, result.stderr))
        return cur.lastrowid

    def record_plan(self, instance, call_id, time, mode, symbol, plan, verdict, context=None):
        """One plan from an answer with the validator's verdict; returns its id."""
        action = plan.get("action") if isinstance(plan, dict) else None
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO llm_plans (instance, call_id, time, mode, symbol, action, accepted, reasons, plan, context) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (instance, call_id, _iso(time), mode, symbol, action, int(verdict.ok), json.dumps(verdict.reasons),
                 json.dumps(verdict.plan if verdict.ok else plan), json.dumps(context) if context else None))
        return cur.lastrowid

    def calls(self, instance=None):
        q, args = "SELECT * FROM llm_calls", ()
        if instance is not None:
            q, args = q + " WHERE instance LIKE ?", (instance,)
        return pd.read_sql_query(q + " ORDER BY id", self.conn, params=args)

    def plans(self, instance=None):
        q, args = "SELECT * FROM llm_plans", ()
        if instance is not None:
            q, args = q + " WHERE instance LIKE ?", (instance,)
        return pd.read_sql_query(q + " ORDER BY id", self.conn, params=args)
