# kripto_bot/trading/run_testnet.py
"""Order-sending trading loop (Binance Spot Testnet by default).

Uses the paper trading strategy settings (STRATEGY, STRATEGY_PARAMS, TIMEFRAME).
Signals are computed on closed mainnet candles, exactly like paper trading; when a
candle closes, the resulting decision is sent right away as a market order, which
matches the backtest's fill at the next open.

Differences from paper trading:
- Candles missed while the bot was down are not traded (orders can't be placed in
  the past); only the latest closed candle is acted on.
- The bot tracks the positions it opened itself (testnet accounts come with preset
  balances) and checks them against the exchange balance on start.
- Risk controls: MAX_ORDER_USDT per order, DAILY_LOSS_LIMIT_PCT (no new entries for
  the rest of the UTC day), and the KILL_SWITCH_FILE (exists: no new entries; contains
  "flatten": close every position).
Live trading needs TRADING_MODE = "live" and LIVE_TRADING_ENABLED = True.
"""
import os
from functools import partial

import pandas as pd

from kripto_bot import config
from kripto_bot.core import strategy
from kripto_bot.trading.broker import CcxtBroker, Fill, client_order_id
from kripto_bot.core.data import fetch_recent, timeframe_ms
from kripto_bot.core.indicators import add_indicators
from kripto_bot.trading.run_realtime import HISTORY, sleep_until_next_close
from kripto_bot.trading.store import TradingStore, single_instance


def decide(in_position, signal):
    """Same rules as the Engine (long-only): BUY enters when flat, SELL exits a long."""
    if not in_position and signal == "BUY":
        return "buy"
    if in_position and signal == "SELL":
        return "sell"
    return None


class RiskGuard:
    def __init__(self, store, kill_file=None, daily_loss_limit_pct=None):
        self.store = store
        self.kill_file = kill_file or config.KILL_SWITCH_FILE
        self.limit = config.DAILY_LOSS_LIMIT_PCT if daily_loss_limit_pct is None else daily_loss_limit_pct

    def kill_state(self):
        """None, "block" (no new entries) or "flatten" (close everything)."""
        if not os.path.exists(self.kill_file):
            return None
        with open(self.kill_file) as f:
            return "flatten" if "flatten" in f.read().lower() else "block"

    def entries_allowed(self, total_equity, day):
        if self.kill_state():
            return False
        start, blocked = self.store.day(day)
        if start is None:
            self.store.start_day(day, total_equity)
            start = total_equity
        if blocked:
            return False
        if total_equity < start * (1 - self.limit / 100):
            self.store.block_day(day)
            self.store.event("WARN", None, f"daily loss limit hit ({total_equity:.2f} vs start "
                                           f"{start:.2f}); no new entries until the next UTC day")
            return False
        return True


class SymbolTrader:
    def __init__(self, symbol, broker, store, signal_fn, timeframe, allocation):
        self.symbol = symbol
        self.base = symbol.split("/")[0]
        self.broker = broker
        self.store = store
        self.signal_fn = signal_fn
        self.timeframe = timeframe
        self.allocation = allocation
        self.last_close = None

    def cash(self):
        return self.store.cash(self.symbol, self.allocation)

    def equity(self, close=None):
        close = self.last_close if close is None else close
        pos = self.store.position(self.symbol)
        if pos and close is None:
            # no price seen yet in this session: use the last stored snapshot
            stored = self.store.latest_equity(self.symbol)
            return stored if stored is not None else self.cash() + pos["cost"]
        return self.cash() + (pos["qty"] * close if pos else 0.0)

    def reconcile(self):
        """Make sure the exchange still holds what the bot thinks it bought."""
        pos = self.store.position(self.symbol)
        if not pos:
            return
        free = self.broker.free_balance(self.base)
        if free < pos["qty"] * 0.999:
            self.store.event("WARN", self.symbol, f"tracked position {pos['qty']} but only {free} "
                                                  f"{self.base} free; tracking {free}")
            self.store.set_position(self.symbol, free, pos["cost"] * free / pos["qty"], pos["entry_price"],
                                    pos["entry_time"], pos["client_id"])
            self.store.commit()

    def step(self, entries_allowed=True, flatten=False, df=None):
        """Act on the latest closed candle (once). Returns the symbol's equity."""
        if df is None:
            df = fetch_recent(self.symbol, self.timeframe, HISTORY)
        df = add_indicators(df)
        signal = self.signal_fn(df).iloc[-1]
        row = df.iloc[-1]
        t, close = row["timestamp"], float(row["close"])
        self.last_close = close

        last = self.store.last_time(self.symbol)
        pos = self.store.position(self.symbol)
        if last is not None and t <= last and not (flatten and pos):
            return self.equity(close)
        if last is not None and t - last > pd.Timedelta(milliseconds=timeframe_ms(self.timeframe)):
            self.store.event("INFO", self.symbol, f"candles after {last} were missed and not traded")

        action = "sell" if (flatten and pos) else decide(pos is not None, signal)
        if action == "buy" and not entries_allowed:
            self.store.event("INFO", self.symbol, f"BUY signal at {t} not taken (entries blocked)")
            action = None
        if action:
            self.execute(action, t, close, pos)

        equity = self.equity(close)
        self.store.set_last_time(self.symbol, t)
        self.store.log_equity(t, self.symbol, equity, close)
        self.store.commit()
        print(f"[{self.symbol}] {t:%Y-%m-%d %H:%M} | Signal: {signal} | Close: {close:.8g} | "
              f"Position: {'LONG' if self.store.position(self.symbol) else '-'} | Equity: {equity:.2f}")
        return equity

    def execute(self, side, t, ref_price, pos):
        cid = client_order_id(self.symbol, t, side)
        if self.store.order_recorded(cid):
            return
        try:
            fill = self.broker.find_order(self.symbol, cid)   # sent before a crash?
            if fill is None:
                fill = self._send(side, ref_price, pos, cid)
                if fill is None:
                    return
        except Exception as e:
            self.store.record_order(Fill(cid, "", self.symbol, side, "error", 0, 0, 0, 0), t, ref_price,
                                    error=f"{type(e).__name__}: {e}")
            self.store.event("ERROR", self.symbol, f"{side} order failed: {type(e).__name__}: {e}")
            return

        if fill.filled <= 0:
            self.store.record_order(fill, t, ref_price, error="not filled")
            self.store.event("WARN", self.symbol, f"{side} order {cid} not filled (status {fill.status})")
            return

        cash = self.cash()
        if side == "buy":
            spent = fill.cost + (fill.fee_quote if fill.fee_base == 0 else 0.0)
            self.store.set_cash(self.symbol, cash - spent)
            self.store.set_position(self.symbol, fill.net_base, spent, fill.average, t, cid)
            self.store.record_order(fill, t, ref_price)
            print(f"🟢 [{self.symbol}] BUY {fill.net_base:.8g} @ {fill.average:.8g} "
                  f"(ref {ref_price:.8g}, cost {spent:.2f})")
        else:
            proceeds = fill.cost - fill.fee_quote
            share = min(1.0, fill.filled / pos["qty"]) if pos["qty"] else 1.0
            pnl = proceeds - pos["cost"] * share
            self.store.set_cash(self.symbol, cash + proceeds)
            remaining = pos["qty"] - fill.filled
            if remaining * fill.average < self.broker.min_cost(self.symbol):
                self.store.clear_position(self.symbol)
                if remaining > 0:
                    self.store.event("INFO", self.symbol, f"{remaining:.8g} {self.base} dust left (below min order)")
            else:
                self.store.set_position(self.symbol, remaining, pos["cost"] * (1 - share), pos["entry_price"],
                                        pos["entry_time"], pos["client_id"])
            self.store.record_order(fill, t, ref_price, pnl=pnl)
            print(f"🔴 [{self.symbol}] SELL {fill.filled:.8g} @ {fill.average:.8g} "
                  f"(ref {ref_price:.8g}) | PnL {pnl:+.2f}")
        self.store.commit()

    def _send(self, side, ref_price, pos, cid):
        if side == "buy":
            quote = min(self.cash(), config.MAX_ORDER_USDT)
            if quote < self.broker.min_cost(self.symbol):
                self.store.event("INFO", self.symbol, f"BUY skipped: {quote:.2f} USDT is below the minimum order")
                return None
            return self.broker.market_buy(self.symbol, quote, cid)
        amount = self.broker.round_amount(self.symbol, min(pos["qty"], self.broker.free_balance(self.base)))
        if amount * ref_price < self.broker.min_cost(self.symbol):
            self.store.event("WARN", self.symbol, f"SELL skipped: {amount} {self.base} is below the minimum order")
            return None
        return self.broker.market_sell(self.symbol, amount, cid)


class Trader:
    def __init__(self, broker, store, symbols=None, timeframe=None, capital=None, guard=None,
                 strategy_key=None, params=None):
        self.broker, self.store = broker, store
        self.timeframe = timeframe or config.TIMEFRAME
        symbols = [s for s in (symbols or config.SYMBOLS) if s in broker.markets] \
            if hasattr(broker, "markets") else list(symbols or config.SYMBOLS)
        capital = config.TRADING_CAPITAL_USDT if capital is None else capital
        key = strategy_key or config.STRATEGY
        signal_fn = partial(strategy.get_strategy(key), **(config.STRATEGY_PARAMS if params is None else params))
        self.traders = [SymbolTrader(s, broker, store, signal_fn, self.timeframe, capital / len(symbols))
                        for s in symbols]
        self.guard = guard or RiskGuard(store)

    def total_equity(self):
        return sum(t.equity() for t in self.traders)

    def run_once(self, frames=None):
        kill = self.guard.kill_state()
        if kill:
            self.store.event("WARN", None, f"kill switch active ({kill})")
        day = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
        allowed = self.guard.entries_allowed(self.total_equity(), day)
        for t in self.traders:
            try:
                t.step(entries_allowed=allowed, flatten=(kill == "flatten"),
                       df=None if frames is None else frames[t.symbol])
            except Exception as e:
                self.store.event("ERROR", t.symbol, f"{type(e).__name__}: {e}")


def main(instance_name="testnet-v9"):
    from kripto_bot import instances
    inst = instances.get(instance_name)
    if inst.mode != "testnet":
        raise SystemExit(f"{inst.name} is a {inst.mode} instance; use `python -m kripto_bot paper`")
    broker = CcxtBroker()
    lock = single_instance(inst.db_path)  # noqa: F841 (held until exit)
    store = TradingStore(inst.db_path)
    trader = Trader(broker, store, symbols=inst.symbol_list, timeframe=inst.timeframe,
                    capital=inst.equity, strategy_key=inst.strategy, params=inst.params)
    for t in trader.traders:
        t.reconcile()
    print(f"⚙️  {broker.mode.upper()} instance '{inst.name}': {inst.strategy} {inst.params}, "
          f"timeframe {trader.timeframe}, {len(trader.traders)} symbols, capital {inst.equity} USDT, "
          f"max order {config.MAX_ORDER_USDT} USDT\n")
    while True:
        trader.run_once()
        sleep_until_next_close(trader.timeframe)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Testnet trading for one instance (see instances.json).")
    ap.add_argument("--instance", default="testnet-v9")
    main(ap.parse_args().instance)
