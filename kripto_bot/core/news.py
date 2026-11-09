# kripto_bot/core/news.py
"""Crypto news headlines for the v11 analyst, from free RSS feeds, kept in our own archive.

Every 15 minutes each feed is read and its new items go into news_items (paper.db). An item
is stored once, by URL, and never changed: published_at is what the feed said, fetched_at the
first time we saw it. The analyst uses an item only when both times are before its call
(docs/v11-ai-analyst.md), which guards against back-dated items.

Runs as a supervisor service (python -m kripto_bot news --loop). It doesn't touch trading.
"""
import argparse
import html
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd

from kripto_bot import net
from kripto_bot.trading import store

FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss",     # no trailing slash: a 308 urllib won't follow
    "cointelegraph": "https://cointelegraph.com/rss",
    "theblock": "https://www.theblock.co/rss.xml",
    "decrypt": "https://decrypt.co/feed",
}
CHECK_S = 900
SUMMARY_CHARS = 400
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"      # one format for every stored time, so text order is time order

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, url TEXT UNIQUE, title TEXT,
    summary TEXT, published_at TEXT, fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS news_items_published ON news_items (published_at);
"""


def get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "kripto-bot news reader"})
    with net.urlopen(req, timeout=30) as r:
        return r.read()


def _now():
    return pd.Timestamp.now(tz="UTC")


def stamp(ts):
    return pd.Timestamp(ts).tz_convert("UTC").strftime(TIME_FORMAT)


def plain(text):
    """Feed HTML → one line of plain text."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_feed(xml_bytes):
    """RSS 2.0 → [{url, title, summary, published_at}]. Items without a usable date or link
    are skipped: without a published time the analyst could never use them."""
    items = []
    for node in ET.fromstring(xml_bytes).iter("item"):
        url = (node.findtext("link") or "").strip()
        title = plain(node.findtext("title"))
        try:
            published = parsedate_to_datetime((node.findtext("pubDate") or "").strip())
        except (TypeError, ValueError):
            continue
        if not url or not title or published is None or published.tzinfo is None:
            continue
        summary = plain(node.findtext("description"))
        if len(summary) > SUMMARY_CHARS:
            summary = summary[:SUMMARY_CHARS - 1].rstrip() + "…"
        items.append({"url": url, "title": title, "summary": summary, "published_at": stamp(published)})
    return items


class News:
    def __init__(self, path=None, fetch=get_bytes, clock=_now, feeds=None):
        self.conn = store._connect(path or store.DB_FILE)
        self.conn.executescript(SCHEMA)
        self.fetch, self.clock = fetch, clock
        self.feeds = FEEDS if feeds is None else feeds

    def close(self):
        self.conn.close()

    def update(self, log=print):
        """Read every feed once; returns the number of new items."""
        added = 0
        for source, url in self.feeds.items():
            try:
                items = parse_feed(self.fetch(url))
            except Exception as e:
                log(f"[NEWS ERROR] {source}: {type(e).__name__}: {e}")
                continue
            fetched_at = stamp(self.clock())
            rows = [(source, i["url"], i["title"], i["summary"], i["published_at"], fetched_at) for i in items]
            with self.conn:
                cur = self.conn.executemany(
                    "INSERT OR IGNORE INTO news_items (source, url, title, summary, published_at, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)", rows)
            added += cur.rowcount
        if added:
            log(f"📰 {added} new headline(s)")
        return added

    def items(self, before, hours=72):
        """Items published in the `hours` before `before` and fetched before it, newest first."""
        end = stamp(before)
        start = stamp(pd.Timestamp(before) - pd.Timedelta(hours=hours))
        return pd.read_sql_query(
            "SELECT * FROM news_items WHERE published_at >= ? AND published_at < ? AND fetched_at < ? "
            "ORDER BY published_at DESC, id DESC", self.conn, params=(start, end, end))


def main():
    ap = argparse.ArgumentParser(description="Store new headlines from the v11 news feeds.")
    ap.add_argument("--loop", action="store_true", help=f"keep running, reading the feeds every {CHECK_S}s")
    a = ap.parse_args()
    news = News()
    try:
        while True:
            news.update()
            if not a.loop:
                break
            time.sleep(CHECK_S)
    finally:
        news.close()


if __name__ == "__main__":
    main()
