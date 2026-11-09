import pandas as pd

from kripto_bot.core import news as nw


def _rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate>"
        f"<description><![CDATA[{s}]]></description></item>" for t, u, d, s in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


A = ("Ether ETFs see inflows", "https://ex.com/a", "Mon, 09 Nov 2026 16:19:45 +0000", "<p>Big <b>inflows</b> &amp; more</p>")
B = ("Bitcoin steady", "https://ex.com/b", "Mon, 09 Nov 2026 13:00:00 -0500", "flat")


class Clock:
    def __init__(self, t):
        self.t = pd.Timestamp(t, tz="UTC")

    def __call__(self):
        return self.t


def _news(tmp_path, feeds, clock):
    return nw.News(str(tmp_path / "paper.db"), fetch=lambda url: feeds[url], clock=clock,
                   feeds={name: name for name in feeds})


def test_parse_feed_reads_rss_items_as_plain_text_in_utc():
    items = nw.parse_feed(_rss(A, B))
    assert items[0] == {"url": "https://ex.com/a", "title": "Ether ETFs see inflows",
                        "summary": "Big inflows & more", "published_at": "2026-11-09T16:19:45Z"}
    assert items[1]["published_at"] == "2026-11-09T18:00:00Z"          # -0500 converted


def test_items_without_a_date_or_link_are_skipped():
    no_date = ("No date", "https://ex.com/c", "", "x")
    bad_date = ("Bad date", "https://ex.com/d", "yesterday", "x")
    no_link = ("No link", "", "Mon, 09 Nov 2026 16:19:45 +0000", "x")
    assert [i["title"] for i in nw.parse_feed(_rss(no_date, bad_date, no_link, A))] == [A[0]]


def test_long_summaries_are_cut():
    item = ("T", "https://ex.com/e", A[2], "word " * 200)
    s = nw.parse_feed(_rss(item))[0]["summary"]
    assert len(s) == nw.SUMMARY_CHARS and s.endswith("…")


def test_an_item_is_stored_once_and_never_changed(tmp_path):
    clock = Clock("2026-11-09 17:00")
    feeds = {"coindesk": _rss(A)}
    n = _news(tmp_path, feeds, clock)
    assert n.update(log=lambda *_: None) == 1
    clock.t = pd.Timestamp("2026-11-09 17:15", tz="UTC")
    feeds["coindesk"] = _rss(("Ether ETFs see record inflows", A[1], A[2], "edited"))
    assert n.update(log=lambda *_: None) == 0
    row = n.conn.execute("SELECT title, fetched_at FROM news_items").fetchone()
    assert row == ("Ether ETFs see inflows", "2026-11-09T17:00:00Z")


def test_one_broken_feed_does_not_stop_the_others(tmp_path):
    logs = []
    n = nw.News(str(tmp_path / "paper.db"), fetch=lambda url: _rss(A) if url == "ok" else b"<not xml",
                clock=Clock("2026-11-09 17:00"), feeds={"good": "ok", "bad": "broken"})
    assert n.update(log=logs.append) == 1
    assert any("[NEWS ERROR] bad" in line for line in logs)


def test_items_before_a_call_need_both_times_before_it(tmp_path):
    clock = Clock("2026-11-09 17:00")
    late = ("Back-dated", "https://ex.com/late", "Mon, 09 Nov 2026 15:00:00 +0000", "x")
    feeds = {"coindesk": _rss(A)}
    n = _news(tmp_path, feeds, clock)
    n.update(log=lambda *_: None)
    clock.t = pd.Timestamp("2026-11-10 00:10", tz="UTC")       # first seen after the call below
    feeds["coindesk"] = _rss(A, late)
    n.update(log=lambda *_: None)
    call = pd.Timestamp("2026-11-10 00:00:05", tz="UTC")
    assert list(n.items(call)["title"]) == [A[0]]
    assert list(n.items(pd.Timestamp("2026-11-10 01:00", tz="UTC"))["title"]) == [A[0], "Back-dated"]


def test_items_older_than_the_window_are_left_out(tmp_path):
    n = _news(tmp_path, {"coindesk": _rss(A)}, Clock("2026-11-09 17:00"))
    n.update(log=lambda *_: None)
    assert len(n.items(pd.Timestamp("2026-11-12 16:00", tz="UTC"))) == 1
    assert len(n.items(pd.Timestamp("2026-11-12 16:20", tz="UTC"))) == 0      # 72 h passed
