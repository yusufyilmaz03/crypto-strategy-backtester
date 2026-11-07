# kripto_bot/core/market_context.py
"""Daily market context: the crypto Fear & Greed index and BTC/ETH dominance.

Fear & Greed (alternative.me) has a free daily history since 2018-02-01: the whole history
is loaded once, then each new day's value. Dominance (CoinGecko /global) is free only as
the current value, so its history is what this module builds: one snapshot per UTC day,
the first one taken after the day starts (fetched_at shows how late it was, e.g. after the
computer slept).

Rows are never overwritten: the first value recorded for a day stays, so a later revision
by the source can't change what a test would have seen.

Runs as a supervisor service (python -m kripto_bot market-context --loop) and writes to
paper.db. It doesn't touch trading; nothing reads these values for decisions yet.
"""
import argparse
import json
import time

import pandas as pd

from kripto_bot import net
from kripto_bot.trading import store

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit={limit}&format=json"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
CHECK_S = 600            # how often the loop looks for a missing day

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_context (
    date TEXT, source TEXT, field TEXT, value REAL, label TEXT, fetched_at TEXT,
    PRIMARY KEY (date, source, field)
);
"""


def get_json(url):
    with net.urlopen(url, timeout=30) as r:
        return json.load(r)


def _now():
    return pd.Timestamp.now(tz="UTC")


def fear_greed_rows(data, fetched_at):
    """alternative.me response → rows. A value's timestamp is 00:00 UTC of its day."""
    rows = []
    for item in data.get("data", []):
        day = pd.Timestamp(int(item["timestamp"]), unit="s", tz="UTC").strftime("%Y-%m-%d")
        rows.append((day, "fear_greed", "value", float(item["value"]), item.get("value_classification"),
                     fetched_at))
    return rows


def global_rows(data, fetched_at):
    """CoinGecko /global response → rows for the UTC day of fetched_at."""
    d = data["data"]
    day = pd.Timestamp(fetched_at).strftime("%Y-%m-%d")
    pct = d.get("market_cap_percentage", {})
    values = {"btc_dominance": pct.get("btc"), "eth_dominance": pct.get("eth"),
              "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
              "total_volume_usd": d.get("total_volume", {}).get("usd")}
    return [(day, "coingecko_global", k, float(v), None, fetched_at) for k, v in values.items() if v is not None]


class MarketContext:
    def __init__(self, path=None, fetch=get_json, clock=_now):
        self.conn = store._connect(path or store.DB_FILE)
        self.conn.executescript(SCHEMA)
        self.fetch, self.clock = fetch, clock

    def close(self):
        self.conn.close()

    def _insert(self, rows):
        with self.conn:
            cur = self.conn.executemany("INSERT OR IGNORE INTO market_context VALUES (?, ?, ?, ?, ?, ?)", rows)
        return cur.rowcount

    def has(self, source, day=None):
        q, args = "SELECT 1 FROM market_context WHERE source=?", [source]
        if day is not None:
            q, args = q + " AND date=?", args + [day]
        return self.conn.execute(q + " LIMIT 1", args).fetchone() is not None

    def update(self, log=print):
        """Record whatever is missing for today; returns the number of new rows."""
        now = self.clock()
        today, fetched_at = now.strftime("%Y-%m-%d"), now.isoformat()
        added = 0
        if not self.has("fear_greed", today):
            limit = 2 if self.has("fear_greed") else 0      # 0 = the whole history
            try:
                n = self._insert(fear_greed_rows(self.fetch(FEAR_GREED_URL.format(limit=limit)), fetched_at))
                if n:
                    log(f"😨 Fear & Greed: {n} new day(s)")
                added += n
            except Exception as e:
                log(f"[CONTEXT ERROR] Fear & Greed: {type(e).__name__}: {e}")
        if not self.has("coingecko_global", today):
            try:
                rows = global_rows(self.fetch(GLOBAL_URL), fetched_at)
                n = self._insert(rows)
                if n:
                    dom = {r[2]: r[3] for r in rows}
                    log(f"🌐 Dominance {today}: BTC {dom.get('btc_dominance', float('nan')):.2f}%, "
                        f"ETH {dom.get('eth_dominance', float('nan')):.2f}%")
                added += n
            except Exception as e:
                log(f"[CONTEXT ERROR] CoinGecko /global: {type(e).__name__}: {e}")
        return added

    def frame(self, source=None):
        """Recorded values, one row per day and field."""
        q, args = "SELECT * FROM market_context", ()
        if source:
            q, args = q + " WHERE source=?", (source,)
        return pd.read_sql_query(q + " ORDER BY date, source, field", self.conn, params=args)


def main():
    ap = argparse.ArgumentParser(description="Record the daily Fear & Greed index and BTC/ETH dominance.")
    ap.add_argument("--loop", action="store_true", help=f"keep running, checking every {CHECK_S}s")
    a = ap.parse_args()
    mc = MarketContext()
    try:
        while True:
            mc.update()
            if not a.loop:
                break
            time.sleep(CHECK_S)
    finally:
        mc.close()


if __name__ == "__main__":
    main()
