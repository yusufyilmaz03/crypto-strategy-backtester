# kripto_bot/trading/run_realtime.py
"""Paper trading loop on live Binance data.

Each newly closed candle is fed to the same Engine the backtest uses, so paper
results follow exactly the backtest rules (next-open fills, intrabar stops,
fees and slippage). State, trades, signals and the equity curve are stored in
SQLite (paper.db), one transaction per candle. After a restart each symbol
resumes from its saved state and first processes the candles it missed.
Times are UTC.
"""
import time
from functools import partial

import pandas as pd

from kripto_bot import config
from kripto_bot.core import bankroll, lower_timeframes, sizing, strategy
from kripto_bot.core.data import fetch_recent, get_exchange, now_ms, timeframe_ms
from kripto_bot.core.engine import Engine
from kripto_bot.core.indicators import add_indicators
from kripto_bot.trading import position_watch, store as store_mod
from kripto_bot.trading.store import PaperStore, single_instance

HISTORY = 300          # closed candles needed for indicator warm-up
MAX_FETCH = 1000       # Binance kline limit per request
CLOSE_DELAY_S = 3      # wait a few seconds after a candle closes before fetching
RETRY_S = 60           # after a failed update, try again this soon (doubling up to MAX_RETRY_S)
MAX_RETRY_S = 1800     # instead of waiting for the next close


def _fmt_time(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def describe_action(engine, signal, blocked=None):
    """What the engine did with this candle's signal. blocked: why a filter kept a BUY
    from entering (lower_timeframes.entry_block), if it did."""
    if engine.pending is None:
        if engine.position:
            return "ignored (already in position)"
        if signal == "BUY" and engine.reentry_above is not None:
            return "ignored (waiting after a trailing or lower-timeframe exit)"
        if signal == "BUY" and blocked:
            return f"ignored ({blocked})"
        return "ignored (long-only)"
    if engine.pending[0] == "EXIT":
        return "trailing exit at next open" if engine.pending[1:] == ("TRAIL",) else "exit at next open"
    if engine.pending[0] == "TRIM":
        return "reduce at next open"
    return f"enter {engine.pending[1]} at next open"


class SymbolRunner:
    def __init__(self, symbol, timeframe, strategy_key, engine_cfg, store, params=None, label=None,
                 lower_tf_entry=False, lower_tf_exit=False, lower_tf_sma=None, trend_sma=None):
        self.symbol = symbol
        self.timeframe = timeframe
        self.step_delta = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
        self.lower_tf_entry, self.lower_tf_exit = lower_tf_entry, lower_tf_exit
        self.trend_sma = trend_sma         # trend filter length (None: off)
        self.exit_only = False             # set by run_loop from the instance: BUYs don't enter
        self.lower = None                  # lower-timeframe candles and signals of the last prepare()
        self.score_lower = False           # the bankroll's score reads the lower timeframes too
        self.bankroll = None               # BankrollRunner of a bankroll instance
        self.features = None               # bankroll score inputs of the last prepare()
        self.key = label or strategy_key   # run name used in paper.db
        self.signal_fn = partial(strategy.get_strategy(strategy_key), **(params or {}))
        # E and X read the strategy's signal on the lower timeframes, or the SMA mode's
        self.lower_signal_fn = lower_timeframes.sma_signal(lower_tf_sma) if lower_tf_sma else self.signal_fn
        self.store = store
        self.engine = Engine(engine_cfg, symbol, on_open=self._opened, on_close=self._closed)
        self.history = None                # candles of the last prepare()
        state, self.last_time = store.load_state(symbol, self.key, timeframe)   # saved under self.key too
        if state is not None:
            self.engine.load_state(state)

    # engine callbacks: written inside the current candle's transaction
    def _opened(self, symbol, p):
        score, grade = self.bankroll.entry_score(symbol) if self.bankroll else (None, None)
        self.store.trade_opened(self.key, self.timeframe, symbol, p, score, grade)
        stop = "-" if p.stop is None else f"{p.stop:.8g}"
        print(f"🟢 [{symbol}] OPEN {p.side} {p.qty:.8g} @ {p.entry_price:.8g} | stop: {stop}")
        if self.bankroll:
            self.bankroll.opened(self, p)

    def _closed(self, t):
        if t.reason == "TRIM":
            score, grade = self.bankroll.entry_score(t.symbol) if self.bankroll else (None, None)
            p = self.engine.position
            self.store.trade_partial(self.key, self.timeframe, t, p.trade_id, p.qty, score, grade)
        else:
            self.store.trade_closed(t)
        print(f"🔴 [{t.symbol}] {'REDUCE' if t.reason == 'TRIM' else 'CLOSE'} {t.side} {t.qty:.8g} "
              f"@ {t.exit_price:.8g} ({t.reason}) | PnL: {t.pnl:.4f} ({t.return_pct:+.2f}%)")
        if self.bankroll:
            self.bankroll.released(self, t)

    def _fetch_limit(self):
        if self.last_time is None:
            return HISTORY
        missed = (now_ms() - pd.Timestamp(self.last_time).value // 10**6) // timeframe_ms(self.timeframe)
        return int(min(MAX_FETCH, max(HISTORY, missed + HISTORY)))

    def prepare(self):
        """Fetch candles and return the closed ones not processed yet (with a 'signal'
        column), or an empty frame. On the first run only the starting point is saved."""
        df = fetch_recent(self.symbol, self.timeframe, self._fetch_limit())
        if df.empty:
            return df
        df = add_indicators(df)
        self.store.fill_missing_closes(self.key, self.timeframe, self.symbol, df)
        self.history = df
        if self.last_time is None:
            # First run: start from the next closed candle; don't simulate trades on past data.
            self.last_time = df["timestamp"].iloc[-1]
            with self.store.candle():
                self.store.save_state(self.symbol, self.key, self.timeframe,
                                      self.engine.to_state(), self.last_time)
            return df.iloc[0:0]
        df = df.assign(signal=self.signal_fn(df))
        if self.trend_sma:
            df = df.assign(above_trend=lower_timeframes.trend_filter(df, self.trend_sma))
        new = df["timestamp"] > self.last_time
        if new.any():
            first_new = df.loc[new, "timestamp"].iloc[0]
            if self.lower_tf_entry or self.lower_tf_exit or self.score_lower:
                self.lower = lower_timeframes.load_signals(self.symbol, self.timeframe, self.lower_signal_fn,
                                                           first_new, fetch=lower_timeframes.closed_candles)
            gap = (first_new - pd.Timestamp(self.last_time)) // pd.Timedelta(milliseconds=timeframe_ms(self.timeframe)) - 1
            if gap > 0:
                print(f"⚠️ [{self.symbol}] {gap} candles missed beyond the fetch window; continuing")
        return df[new]

    def exit_watchable(self):
        return lower_timeframes.watchable(self.engine)

    def exit_early(self, period, open_, time, price):
        """Sell at `price` at `time` inside the main candle that opened at `period`. Call
        inside the store's candle() transaction."""
        fine = lower_timeframes.LOWER_TIMEFRAMES[self.timeframe][-1]
        if self.engine.exit_inside(period, open_, time, price) is not None:
            self.store.log_signal(self.key, self.timeframe, self.symbol, time, "SELL", None, price, None,
                                  f"lower-timeframe exit at the {fine} open")

    def step(self, row):
        """Process one closed candle. Call inside the store's candle() transaction."""
        blocked = self.bar(row)
        self.record(row, describe_action(self.engine, row["signal"], blocked))

    def bar(self, row, defer_entry=False):
        """Feed one closed candle to the engine; returns why a filter blocked a BUY (or
        None). defer_entry: queue no entry (the bankroll decides and queues it)."""
        sig, period = row["signal"], row["timestamp"]
        # the same decisions as lower_timeframes.run_backtest (the backtest of E and X)
        if self.lower_tf_exit:
            # the watcher normally exits during the candle; this covers what it missed
            x = lower_timeframes.early_exit(self.engine, self.lower, period, row["open"], self.timeframe)
            if x is not None:
                self.exit_early(period, row["open"], *x)
        blocked = lower_timeframes.entry_block(sig, period + self.step_delta,
                                               self.lower if self.lower_tf_entry else None,
                                               bool(row["above_trend"]) if self.trend_sma else None)
        if blocked is None and sig == "BUY" and self.exit_only:
            blocked = "exit only"
        self.engine.on_bar(period, row["open"], row["high"], row["low"],
                           row["close"], sig, row["ATR"], row["RSI"], entry_ok=blocked is None and not defer_entry)
        return blocked

    def lower_share(self, close_time):
        """Share of the lower timeframes whose last closed candle says BUY at close_time,
        or None without lower-timeframe data."""
        if not self.lower:
            return None
        says = [lower_timeframes.signal_at(df, tf, close_time) for tf, df in self.lower.items()]
        return sum(s == "BUY" for s in says) / len(says)

    def record(self, row, action, score=None, grade=None):
        """Log the candle's signal, equity and engine state (after bar())."""
        sig = row["signal"]
        if sig in ("BUY", "SELL"):
            side = self.engine.position.side if self.engine.position else None
            self.store.log_signal(self.key, self.timeframe, self.symbol, row["timestamp"], sig,
                                  row["RSI"], row["close"], side, action, score, grade)
        t, eq = self.engine.equity_curve[-1]
        self.store.log_equity(self.key, self.timeframe, self.symbol, t, eq, row["close"])
        self.store.save_state(self.symbol, self.key, self.timeframe,
                              self.engine.to_state(), row["timestamp"])
        self.engine.equity_curve.clear()  # already stored; keep memory flat
        self.last_time = row["timestamp"]

    def report(self):
        if self.history is None or self.history.empty:
            return
        last = self.history.iloc[-1]
        pos = self.engine.position
        pos_show = f"{pos.side} stop {pos.stop:.8g}" if pos and pos.stop else (pos.side if pos else "-")
        sig = self.signal_fn(self.history).iloc[-1]
        print(f"[{self.symbol}] {_fmt_time(last['timestamp'])} | Signal: {sig} | "
              f"RSI: {last['RSI']:.2f} | Close: {last['close']:.8g} | Position: {pos_show} | "
              f"Equity: {self.engine.equity:.2f}")

    def update(self):
        for _, row in self.prepare().iterrows():
            with self.store.candle():
                self.step(row)
        self.report()


def period_ends(t, timeframe):
    """True if candle t is the last one of its calendar month."""
    nxt = pd.Timestamp(t) + pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    return nxt.month != pd.Timestamp(t).month


class PortfolioRunner:
    """Runs all symbols of an instance in step and rebalances them to inverse-volatility
    weights after the last candle of each month (and once at the start). A rebalance
    resizes each symbol's engine to weight × portfolio value; a symbol in a position
    trades the difference at the close with fee and slippage. Candle times are processed
    in order across symbols, each time in one transaction together with its rebalance."""

    exit_only = False      # set by run_loop: no rebalance (it could add to positions)

    def __init__(self, runners, store, key, timeframe, lookback=sizing.LOOKBACK):
        self.runners, self.store, self.key, self.timeframe = runners, store, key, timeframe
        self.lookback = lookback

    def rebalance(self, t, closes):
        """closes: {symbol: close Series ending at t}."""
        if self.exit_only:
            print(f"⚖️  No rebalance at {_fmt_time(t)}: exit only")
            return
        price = {s: float(c.iloc[-1]) for s, c in closes.items()}
        engines = {r.symbol: r.engine for r in self.runners if r.symbol in closes}
        marked = {s: e.marked_equity(price[s]) for s, e in engines.items()}
        total = sum(marked.values())
        weights = sizing.inverse_vol_weights(closes, self.lookback)
        costs = {}
        for s, e in engines.items():
            costs[s] = e.resize(weights[s] * total, price[s])
            self.store.log_weight(self.key, self.timeframe, t, s, weights[s], costs[s])
            self.store.save_state(s, self.key, self.timeframe, e.to_state(), t)
            if self.store.has_equity(self.key, self.timeframe, s, t):
                self.store.log_equity(self.key, self.timeframe, s, t, e.marked_equity(price[s]), price[s])
        top = sorted(weights.items(), key=lambda x: -x[1])[:3]
        print(f"⚖️  Rebalanced {len(engines)} symbols at {_fmt_time(t)}: value {total:.2f}, "
              f"cost {sum(costs.values()):.2f}; largest weights "
              + ", ".join(f"{s} {w * 100:.1f}%" for s, w in top))

    def update(self):
        """Process the new candles of all symbols; returns the symbols that failed."""
        pending, failed = {}, []
        for r in self.runners:
            try:
                pending[r] = r.prepare()
            except Exception as e:
                print(f"[ERROR] {r.symbol}: {e}")
                failed.append(r.symbol)
        ready = [r for r in pending if r.history is not None and not r.history.empty]
        if self.store.last_rebalance(self.key, self.timeframe) is None and ready:
            # Start: split the capital by weight before the first candle is traded.
            t = max(r.last_time for r in ready)
            with self.store.candle():
                self.rebalance(t, {r.symbol: r.history.set_index("timestamp")["close"].loc[:t] for r in ready})
        times = sorted({t for df in pending.values() for t in df["timestamp"]})
        for t in times:
            with self.store.candle():
                for r, df in pending.items():
                    rows = df[df["timestamp"] == t]
                    if len(rows):
                        r.step(rows.iloc[0])
                if period_ends(t, self.timeframe):
                    have = [r for r in ready if r.last_time == t]
                    if len(have) < len(ready):
                        print(f"⚠️ Rebalance at {_fmt_time(t)} without "
                              f"{', '.join(r.symbol for r in ready if r not in have)} (no candle yet)")
                    self.rebalance(t, {r.symbol: r.history.set_index("timestamp")["close"].loc[:t] for r in have})
        for r in self.runners:
            r.report()
        return failed


def market_rules(symbols):
    """{symbol: (amount step, minimum order cost)} from Binance's market info, for the
    bankroll's quantity rounding and minimum order. Missing markets are left out."""
    try:
        ex = get_exchange()
        ex.load_markets()
    except Exception as e:
        print(f"⚠️ market info unavailable ({type(e).__name__}: {e}); no lot rounding, "
              f"minimum order {bankroll.MIN_ORDER} USDT")
        return {}
    out = {}
    for s in symbols:
        m = ex.markets.get(s)
        if not m:
            continue
        step = (m.get("precision") or {}).get("amount")
        cost = ((m.get("limits") or {}).get("cost") or {}).get("min")
        out[s] = (float(step) if step else None, float(cost) if cost else bankroll.MIN_ORDER)
    return out


class BankrollRunner:
    """One bankroll for all symbols of an instance (sizing "bankroll",
    docs/bankroll-management.md). Candle times are processed in order across symbols,
    each in one transaction:

      1. every engine processes the candle (fills at the open: sells first, then held
         positions, then entries; stops; mark to market); no engine queues an entry itself,
      2. unused cash of flat engines goes back to the bankroll,
      3. every symbol is scored; the BUY signals that may enter are funded strongest
         first with their grade's % of the contributed capital while free cash lasts,
      4. with rotation, a very strong signal left without cash may sell weaker positions
         at the next open (and is funded against what they are expected to bring),
      5. signals, equity, engine states, the ledger and a bankroll snapshot are written.

    A flat engine holds 0, or the stake reserved for its entry at the next open. Cash
    from any close (signal, stop, trailing, lower-timeframe, manual, rotation) returns
    through the runners' close callback, also when a watcher closes inside a candle."""

    def __init__(self, runners, store, inst, rules=None):
        self.runners, self.store = runners, store
        self.key, self.timeframe, self.start = inst.run_key, inst.timeframe, float(inst.equity)
        self.cfg = inst.bankroll_config()
        self.step = pd.Timedelta(milliseconds=timeframe_ms(inst.timeframe))
        self.by_symbol = {r.symbol: r for r in runners}
        rules = market_rules(list(self.by_symbol)) if rules is None else rules
        for r in runners:
            r.bankroll = self
            r.score_lower = "lower_tf" in self.cfg.inputs
            step, cost = rules.get(r.symbol, (None, bankroll.MIN_ORDER))
            r.engine.lot_step, r.engine.min_cost = step, max(cost or 0.0, bankroll.MIN_ORDER)
        self.state = store.load_bankroll(self.key, self.timeframe)
        self.prices = {}                   # latest close of each symbol
        self.btc = None                    # BTC features for the rs_btc input (when BTC isn't traded)

    # ----- balances
    def balances(self):
        reserved = invested = 0.0
        for r in self.runners:
            e = r.engine
            if e.position is None:
                reserved += e.equity
            else:
                invested += e.marked_equity(self.prices.get(r.symbol, e.position.entry_price))
        free = self.state["free"]
        return {"free": free, "reserved": reserved, "invested": invested,
                "value": free + reserved + invested, "contributed": self.state["contributed"]}

    def _ledger(self, time, kind, amount, symbol=None, note=None, **kw):
        self.store.log_ledger(self.key, self.timeframe, time, kind, amount, self.balances(), symbol=symbol,
                              note=note, **kw)

    def save(self):
        self.store.save_bankroll(self.key, self.timeframe, self.state)

    def entry_score(self, symbol):
        e = (self.state or {}).get("entries", {}).get(symbol)
        return (e[0], e[1]) if e else (None, None)

    # ----- callbacks from the runners (inside the current transaction)
    def opened(self, r, p):
        left, r.engine.leftover = r.engine.leftover, 0.0
        if left > 1e-9:
            self.state["free"] += left
            self._ledger(p.entry_time, "RELEASE", left, r.symbol, "not used: quantity rounded to the lot size",
                         trade_id=p.trade_id)
            self.save()

    def released(self, r, t):
        e = r.engine
        if t.reason == "TRIM":
            amount, e.released = e.released, 0.0
        else:
            amount, e.equity = e.equity, 0.0
        self.state["free"] += amount
        kind = t.reason if t.reason in ("TRIM", "ROTATE") else "SELL"
        score, grade = self.entry_score(r.symbol)
        if t.reason != "TRIM":
            self.state["entries"].pop(r.symbol, None)
        self._ledger(t.exit_time, kind, amount, r.symbol, f"exit {t.reason}, PnL {t.pnl:+.2f}",
                     trade_id=t.trade_id, fee=t.fees, score=score, grade=grade)
        self.save()

    # ----- deposits and withdrawals (the panel files them; bankroll_requests)
    def apply_requests(self):
        if self.state is None:
            return                                         # not started yet: applied after the start
        for request_id, kind, amount, note, source in self.store.bankroll_requests(self.key, self.timeframe):
            now = pd.Timestamp(now_ms(), unit="ms")
            with self.store.candle():
                result = self._apply(now, kind, float(amount), note, source)
                self.store.finish_bankroll_request(request_id, result)
            print(f"💰 bankroll {kind} {amount:.2f} USDT: {result}")

    def _apply(self, now, kind, amount, note, source):
        st = self.state
        if amount <= 0:
            return "refused: amount must be positive"
        if kind == "withdraw":
            if amount > st["free"] + 1e-9:
                return f"refused: only {st['free']:.2f} USDT free (money in positions can't be withdrawn)"
            amount = -amount
        elif kind != "deposit":
            return f"refused: unknown kind {kind}"
        st["free"] += amount
        st["contributed"] += amount
        st["flow"] += amount
        self._ledger(now, kind.upper(), amount, note=note or None, source=source)
        self.save()
        return "done"

    # ----- candles
    def _start(self, t):
        self.state = {"free": self.start, "contributed": self.start, "flow": 0.0, "twr": 1.0,
                      "last_value": self.start, "last_time": None, "scores": {}, "entries": {}}
        self._ledger(t, "INITIAL", self.start, note="starting bankroll")
        self.store.log_snapshot(self.key, self.timeframe, t, self.balances(), 1.0)
        self.save()
        print(f"💰 Bankroll started with {self.start:.2f} USDT")

    def update(self):
        """Process the new candles of all symbols; returns the symbols that failed."""
        pending, failed = {}, []
        for r in self.runners:
            try:
                pending[r] = r.prepare()
            except Exception as e:
                print(f"[ERROR] {r.symbol}: {e}")
                failed.append(r.symbol)
        for r in pending:
            if r.history is not None and not r.history.empty:
                r.features = bankroll.features(r.history, self.cfg.score_sma, self.cfg.trend_sma)
                if r.symbol not in self.prices:
                    last = r.history[r.history["timestamp"] <= r.last_time] if r.last_time is not None else r.history
                    if len(last):
                        self.prices[r.symbol] = float(last["close"].iloc[-1])
        if "rs_btc" in self.cfg.inputs and "BTC/USDT" not in self.by_symbol:
            self._load_btc(pending)
        ready = [r for r in pending if r.history is not None and not r.history.empty]
        if self.state is None and ready:
            with self.store.candle():
                self._start(max(r.last_time for r in ready))
        times = sorted({t for df in pending.values() for t in df["timestamp"]})
        for t in times:
            with self.store.candle():
                self._candle(t, {r: df[df["timestamp"] == t].iloc[0] for r, df in pending.items()
                                 if (df["timestamp"] == t).any()})
        for r in self.runners:
            r.report()
        return failed

    def _load_btc(self, pending):
        n = max((len(r.history) for r in pending if r.history is not None), default=0)
        try:
            df = add_indicators(fetch_recent("BTC/USDT", self.timeframe, max(n, HISTORY)))
            self.btc = bankroll.features(df, self.cfg.score_sma, self.cfg.trend_sma)
        except Exception as e:
            print(f"[ERROR] BTC/USDT for the rs_btc score input: {e}")
            self.btc = None

    def _btc_ret(self, t):
        if "BTC/USDT" in self.by_symbol:
            f = self.by_symbol["BTC/USDT"].features
        else:
            f = self.btc
        return None if f is None or t not in f.index else f.at[t, "ret"]

    def _order(self, r):
        p = r.engine.pending
        if p is not None and p[0] in ("EXIT", "TRIM"):
            return 0                                       # sells first: their cash funds the entries
        return 1 if r.engine.position is not None else 2

    def _settle(self, r, t):
        """Before an entry fills: if rotations brought less than expected, the free cash is
        negative; the entry's stake covers the difference (or the entry is dropped)."""
        st, e = self.state, r.engine
        if st["free"] >= 0:
            return
        take = min(-st["free"], e.equity)
        e.equity -= take
        st["free"] += take
        if e.equity < self.cfg.floor:
            st["free"] += e.equity
            take += e.equity
            e.equity, e.pending = 0.0, None
            st["entries"].pop(r.symbol, None)
            note = "entry dropped: the rotation brought less than expected"
        else:
            note = "stake cut: the rotation brought less than expected"
        self._ledger(t, "RELEASE", take, r.symbol, note)

    def _candle(self, t, rows):
        st, cfg = self.state, self.cfg
        blocked = {}
        for r in sorted(rows, key=self._order):
            e = r.engine
            if e.pending is not None and e.pending[0] == "ENTER" and e.position is None:
                self._settle(r, t)
            blocked[r] = r.bar(rows[r], defer_entry=True)
            self.prices[r.symbol] = float(rows[r]["close"])
        for r in rows:                                     # entries that didn't open (below the minimum)
            e = r.engine
            if e.position is None and e.pending is None and e.equity > 1e-9:
                amount, e.equity = e.equity, 0.0
                st["free"] += amount
                st["entries"].pop(r.symbol, None)
                self._ledger(t, "RELEASE", amount, r.symbol, "entry not opened: below the minimum order at the open")

        feats = {r.symbol: r.features.loc[t] for r in rows if r.features is not None and t in r.features.index}
        lower = {r.symbol: r.lower_share(t + self.step) for r in rows} if "lower_tf" in cfg.inputs else None
        sc = bankroll.scores(feats, cfg.inputs, lower, self._btc_ret(t) if "rs_btc" in cfg.inputs else None)
        st["scores"].update(sc)

        candidates = []
        for r, row in rows.items():
            e = r.engine
            if (row["signal"] == "BUY" and blocked[r] is None and e.position is None and e.pending is None
                    and e.reentry_above is None and r.symbol in sc):
                candidates.append((r.symbol, sc[r.symbol]))
        fundings, _ = bankroll.fund(cfg, st["contributed"], st["free"], candidates)
        actions, wanting = {}, []
        top = len(bankroll.GRADES) - 1
        for f in fundings:
            if cfg.rotation and f.grade == top and f.amount < f.wanted and f.wanted >= cfg.floor:
                wanting.append(f)          # very strong without its full stake: rotation first
            else:
                self._fund(t, rows, f, f.amount, f.reason, actions)
        if wanting:
            self._rotate(t, rows, wanting, sc, actions)

        for r, row in rows.items():
            score = sc.get(r.symbol)
            grade = None if score is None else bankroll.GRADES[bankroll.grade(score)]
            r.record(row, actions.get(r.symbol) or describe_action(r.engine, row["signal"], blocked[r]), score, grade)
        b = self.balances()
        st["twr"] = bankroll.twr_step(st["twr"], st["last_value"], b["value"], st["flow"])
        st.update(last_value=b["value"], flow=0.0, last_time=pd.Timestamp(t).isoformat())
        self.store.log_snapshot(self.key, self.timeframe, t, b, st["twr"])
        self.save()

    def _fund(self, t, rows, f, amount, note, actions, extra=""):
        """Reserve `amount` for f's entry at the next open, or log why it isn't bought."""
        label = f"{bankroll.GRADES[f.grade]} {f.score:g}"
        if amount >= self.cfg.floor:
            self._reserve(t, rows, f, amount, note)
            actions[f.symbol] = f"enter LONG at next open (stake {amount:.2f} USDT, {label}" + \
                                (f"; {note})" if note else ")")
        else:
            note = note or "no free cash"
            actions[f.symbol] = f"ignored (not funded: {note}; {label})"
            self._ledger(t, "SKIP", 0.0, f.symbol, note, score=f.score, grade=bankroll.GRADES[f.grade])

    def _reserve(self, t, rows, f, amount, note=None):
        r = self.by_symbol[f.symbol]
        row = rows[r]
        self.state["free"] -= amount
        r.engine.equity += amount
        r.engine.queue_entry(row["ATR"], row["RSI"])
        grade = bankroll.GRADES[f.grade]
        self.state["entries"][f.symbol] = [f.score, grade]
        self._ledger(t, "RESERVE", -amount, f.symbol, note or "entry at the next open", score=f.score, grade=grade)

    def _rotate(self, t, rows, wanting, sc, actions):
        """Very strong signals short of their stake: the free cash is used first, and only
        the rest is freed by selling weaker positions at the next open (the entry is funded
        against what they are expected to bring). Without a plan the entry gets what free
        cash is left."""
        cfg, used, chosen = self.cfg, 0, set()
        for f in wanting:
            free = max(self.state["free"], 0.0)
            if used >= bankroll.ROTATE_MAX:
                self._fund(t, rows, f, min(f.amount, free), f.reason, actions)
                continue
            holdings = []
            for r in self.runners:
                p = r.engine.position
                if p is None or r.symbol not in self.prices:
                    continue
                held = int((pd.Timestamp(t) - pd.Timestamp(p.entry_time)) / self.step) + 1
                holdings.append(bankroll.Holding(r.symbol, sc.get(r.symbol, self.state["scores"].get(r.symbol, 0.0)),
                                                 p.qty, self.prices[r.symbol], p.equity_before, held,
                                                 busy=r.engine.pending is not None or r.symbol in chosen))
            plan = bankroll.rotation_plan(cfg, f.wanted - free, f.score, holdings, used)
            if not plan:
                self._fund(t, rows, f, min(f.amount, free), f.reason or "no free cash", actions)
                continue
            for rot in plan:
                victim = self.by_symbol[rot.symbol]
                victim.engine.queue_exit("ROTATE", rot.fraction)
                chosen.add(rot.symbol)
                what = "close" if rot.fraction >= 1 else f"reduce by {rot.fraction * 100:.1f}%"
                actions[rot.symbol] = f"rotation: {what} at next open for {f.symbol}"
                self._ledger(t, "ROTATE", 0.0, rot.symbol,
                             f"{what} at the next open for {f.symbol} (expected {rot.expected:.2f} USDT)",
                             score=sc.get(rot.symbol))
            used += len(plan)
            amount = min(f.wanted, free + sum(rot.expected for rot in plan))
            names = ", ".join(rot.symbol for rot in plan)
            from_cash = min(free, amount)
            self._fund(t, rows, f, amount, f"{from_cash:.2f} from free cash + {amount - from_cash:.2f} "
                                           f"expected from rotating out {names}", actions)


class BankrollRequests:
    """Applies the deposits and withdrawals the panel filed (a watch, once a minute)."""

    def __init__(self, runner):
        self.runner = runner

    def check(self):
        try:
            self.runner.apply_requests()
        except Exception as e:     # tried again next minute
            print(f"[BANKROLL ERROR] {type(e).__name__}: {e}")


def update_runners(runners, portfolio=None):
    """Process new candles; returns the symbols whose update failed."""
    if portfolio:
        return portfolio.update()
    failed = []
    for r in runners:
        try:
            r.update()
        except Exception as e:
            print(f"[ERROR] {r.symbol}: {e}")
            failed.append(r.symbol)
    return failed


def next_close_ms(timeframe):
    step = timeframe_ms(timeframe)
    return (now_ms() // step + 1) * step + CLOSE_DELAY_S * 1000


def _next_minute():
    return (now_ms() // 60_000 + 1) * 60_000 + CLOSE_DELAY_S * 1000


def sleep_until_next_close(timeframe, check_every_s=30, on_minute=None):
    """Wait until the next candle close (+ CLOSE_DELAY_S), checking the wall clock in short
    steps: on macOS a single long sleep doesn't count time the computer spends asleep.
    on_minute, if given, is called a few seconds after each 1m close while waiting."""
    target = next_close_ms(timeframe)
    print(f"\n⏳ Next {timeframe} candle closes in {(target - now_ms()) / 1000:.0f}s "
          f"({pd.Timestamp(target, unit='ms'):%Y-%m-%d %H:%M:%S} UTC)...\n")
    wait_until(target, check_every_s, on_minute)


def wait_until(target, check_every_s=30, on_minute=None):
    """Wait until wall-clock time `target` (ms); see sleep_until_next_close."""
    minute = _next_minute()
    while (remaining := target - now_ms()) > 0:
        if on_minute is not None:
            if now_ms() >= minute:
                on_minute()
                minute = _next_minute()
            remaining = min(remaining, max(minute - now_ms(), 0))
        time.sleep(min(check_every_s, remaining / 1000))


class RebuildError(RuntimeError):
    pass


def rebuild_state(inst, store, log=print, tol=1e-6):
    """Recompute each symbol's saved engine state by replaying the candles the instance
    already recorded, and save it only if the replay reproduces the recorded equity,
    closed trades and open position exactly. Recorded results are not changed.

    Repairs states lost to the bug that saved under the run key but loaded under the
    strategy key (every restart then started the engines from scratch)."""
    if inst.sizing == "bankroll":
        raise RebuildError("bankroll instances can't be rebuilt yet: their symbols share one cash balance")
    fn = partial(strategy.get_strategy(inst.strategy), **inst.params)
    cfg = inst.engine_config()
    eq = store.read("equity", "WHERE strategy=? AND timeframe=?", (inst.run_key, inst.timeframe))
    trades = store.read("trades", "WHERE strategy=? AND timeframe=?", (inst.run_key, inst.timeframe))
    step = pd.Timedelta(milliseconds=timeframe_ms(inst.timeframe))
    rebuilt, problems = {}, []
    for symbol in inst.symbol_list:
        rec = eq[eq.symbol == symbol].assign(time=lambda d: pd.to_datetime(d.time)).sort_values("time")
        if rec.empty:
            log(f"  {symbol}: nothing recorded, skipped")
            continue
        first, last = rec.time.iloc[0], rec.time.iloc[-1]
        need = int((pd.Timestamp(now_ms(), unit="ms") - first) / step) + HISTORY + 2
        if need > MAX_FETCH:
            raise RebuildError(f"{symbol}: {need} candles needed, more than one request can fetch")
        df = add_indicators(fetch_recent(symbol, inst.timeframe, need))
        sig = fn(df)
        engine = Engine(cfg, symbol)
        sel = (df.timestamp >= first) & (df.timestamp <= last)
        for (_, row), x in zip(df[sel].iterrows(), sig[sel]):
            engine.on_bar(row["timestamp"], row["open"], row["high"], row["low"], row["close"],
                          x, row["ATR"], row["RSI"])
        replay = pd.Series(dict(engine.equity_curve))
        recorded = rec.set_index("time")["equity"]
        diff = (replay.reindex(recorded.index) / recorded - 1).abs().max()
        mine = trades[trades.symbol == symbol]
        closed = mine[mine.exit_time.notna()].sort_values("entry_time")
        open_ = mine[mine.exit_time.isna()]
        issues = []
        if len(replay) != len(recorded) or not diff <= tol:
            issues.append(f"equity differs (max {diff:.2e})")
        if len(engine.trades) != len(closed) or any(
                abs(t.pnl - p) > tol * 1000 for t, p in zip(engine.trades, closed.pnl)):
            issues.append(f"closed trades differ ({len(engine.trades)} replayed vs {len(closed)} recorded)")
        if (engine.position is None) != open_.empty or len(open_) > 1:
            issues.append(f"open position differs (replay {'open' if engine.position else 'flat'}, "
                          f"{len(open_)} open recorded)")
        elif engine.position is not None:
            o = open_.iloc[0]
            if pd.Timestamp(o.entry_time) != pd.Timestamp(engine.position.entry_time) or \
                    abs(o.entry_price / engine.position.entry_price - 1) > tol:
                issues.append("open position entry differs")
            engine.position.trade_id = o.trade_id      # later exits update the recorded row
        if issues:
            problems.append(f"{symbol}: {'; '.join(issues)}")
        rebuilt[symbol] = (engine.to_state(), last)
        log(f"  {symbol}: {len(recorded)} candles, {len(engine.trades)} closed, "
            f"{'in position' if engine.position else 'flat'}{', MISMATCH' if issues else ''}")
    if problems:
        raise RebuildError("replay doesn't match the records, nothing saved:\n  " + "\n  ".join(problems))
    with store.candle():
        for symbol, (state, last) in rebuilt.items():
            store.save_state(symbol, inst.run_key, inst.timeframe, state, last)
    return rebuilt


def main(instance_name="forward-v9", rebuild=False):
    from kripto_bot import instances
    inst = instances.get(instance_name)
    if inst.mode != "paper":
        raise SystemExit(f"{inst.name} is a {inst.mode} instance; use `python -m kripto_bot testnet`")
    lock = single_instance(store_mod.DB_FILE, inst.name)  # noqa: F841 (held until exit)
    cfg = inst.engine_config()
    store = PaperStore()
    if rebuild:
        print(f"🔧 Rebuilding the engine states of '{inst.name}' from its records...")
        try:
            rebuilt = rebuild_state(inst, store)
        except RebuildError as e:
            raise SystemExit(f"❌ {e}")
        print(f"✅ {len(rebuilt)} symbol states verified and saved. Start the instance again.")
        return
    runners = [SymbolRunner(s, inst.timeframe, inst.strategy, cfg, store, params=inst.params, label=inst.run_key,
                            lower_tf_entry=inst.lower_tf_entry, lower_tf_exit=inst.lower_tf_exit,
                            lower_tf_sma=inst.lower_tf_sma, trend_sma=inst.trend_sma)
               for s in inst.symbol_list]
    resumed = sum(r.last_time is not None for r in runners)
    print(f"📡 Paper instance '{inst.name}': {inst.strategy} {inst.params} stop={inst.stop}, "
          f"sizing {inst.sizing}, timeframe {inst.timeframe}, {len(runners)} symbols, "
          f"lower-timeframe entry {'on' if inst.lower_tf_entry else 'off'}, exit {'on' if inst.lower_tf_exit else 'off'}"
          f"{f' (SMA{inst.lower_tf_sma})' if inst.lower_tf_sma else ''}, "
          f"trend filter {f'SMA{inst.trend_sma}' if inst.trend_sma else 'off'} "
          f"({resumed} resumed from paper.db)\n")
    if inst.sizing == "inverse_vol":
        portfolio = PortfolioRunner(runners, store, inst.run_key, inst.timeframe)
    elif inst.sizing == "bankroll":
        portfolio = BankrollRunner(runners, store, inst)
        b = inst.bankroll_config()
        print(f"💰 Bankroll {inst.equity:.2f} USDT start; stakes {'/'.join(f'{x:g}' for x in b.stakes)}% "
              f"of the contributed capital; entry {b.floor:g}-{b.max_entry or '∞'} USDT; score "
              f"{', '.join(b.inputs)}; rotation {'on' if b.rotation else 'off'}"
              f"{' (profit only)' if b.profit_only else ''}{' (reduce allowed)' if b.trim else ''}\n")
    else:
        portfolio = None
    watch = position_watch.for_instance(inst, runners, store,
                                        extra=[BankrollRequests(portfolio)] if inst.sizing == "bankroll" else None)
    run_loop(inst, runners, portfolio, watch)


def set_exit_only(name, runners, portfolio=None, load=None):
    """Read the instance's exit-only switch (set on the panel while it runs) before a close is
    processed. An entry decided at an earlier close still fills at its open."""
    from kripto_bot import instances
    try:
        on = bool((load or instances.get)(name).exit_only)
    except Exception as e:                  # keep the last value if instances.json can't be read
        print(f"[EXIT ONLY] can't read the instance: {type(e).__name__}: {e}")
        return
    if runners and on != runners[0].exit_only:
        print("🚪 Exit only on: no new entries, open positions close by their rules\n" if on
              else "🚪 Exit only off: entries allowed again\n")
    for r in runners:
        r.exit_only = on
    if portfolio is not None:
        portfolio.exit_only = on


AUTO_STOP_REASON = "exit only and no open positions"


def flat(runners):
    """No position and no entry waiting for its open in any symbol."""
    return all(r.engine.position is None and r.engine.pending is None for r in runners)


def auto_stop(name, runners, update=None, wait=lambda: time.sleep(60)):
    """In exit only, once the last position is closed the instance has nothing left to do:
    it disables itself (and clears exit only, so Start later trades normally) and waits for
    the supervisor to stop its process. Returns False while there is something to watch."""
    from kripto_bot import instances
    if not (runners and runners[0].exit_only and flat(runners)):
        return False
    now = pd.Timestamp.now(tz="UTC").isoformat()
    try:
        (update or instances.update)(name, enabled=False, exit_only=False, stopped_reason=AUTO_STOP_REASON,
                                     stopped_at=now)
    except Exception as e:                  # try again at the next check
        print(f"[AUTO STOP] {type(e).__name__}: {e}")
        return False
    print(f"⏹  Stopped itself: {AUTO_STOP_REASON}. Waiting for the supervisor to end this process.")
    while True:
        wait()


def run_loop(inst, runners, portfolio=None, watch=None):
    """Update at every close. When an update fails (e.g. no network right after the
    computer wakes up), retry the failed symbols soon instead of a whole candle later.
    Exit only is read before every close and every minute; see auto_stop."""
    todo, retry_s = runners, RETRY_S

    def on_minute():
        if watch:
            watch.check()
        set_exit_only(inst.name, runners, portfolio)
        auto_stop(inst.name, runners)

    while True:
        set_exit_only(inst.name, runners, portfolio)
        if watch:
            watch.check()          # the last minutes of the candle that just closed
        failed = update_runners(todo, portfolio)
        auto_stop(inst.name, runners)
        retry_at = now_ms() + retry_s * 1000
        if failed and retry_at < next_close_ms(inst.timeframe):
            if not portfolio:
                todo = [r for r in runners if r.symbol in failed]
            print(f"🔁 {len(failed)} symbol(s) failed ({', '.join(failed[:5])}{', …' if len(failed) > 5 else ''}); "
                  f"retrying in {retry_s}s")
            wait_until(retry_at, on_minute=on_minute)
            retry_s = min(retry_s * 2, MAX_RETRY_S)
            continue
        todo, retry_s = runners, RETRY_S
        sleep_until_next_close(inst.timeframe, on_minute=on_minute)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Paper trading for one instance (see instances.json).")
    ap.add_argument("--instance", default="forward-v9")
    ap.add_argument("--rebuild-state", action="store_true",
                    help="recompute saved engine states from the recorded candles (instance must be stopped)")
    a = ap.parse_args()
    main(a.instance, a.rebuild_state)
