# kripto_bot/core/engine.py
"""Bar-by-bar trading engine shared by the backtest and the paper trader.

The engine only ever sees closed candles. For each candle it:
  1. fills the order queued on the previous candle at this candle's open,
  2. checks the stop-loss against this candle's high/low (gaps fill at the open),
  3. marks equity to this candle's close,
  4. queues an order for the next candle's open based on this candle's signal
     (or on the trailing exit, when enabled).

Sizing: the whole equity goes into each position (no leverage, compounding).
Fees are charged on notional on both legs; slippage always works against us.
By default only long positions are taken (spot); a SELL signal just exits.
An opposite signal closes the open position, it does not reverse it.
"""
import math
import uuid
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class EngineConfig:
    fee_rate: float = 0.001        # taker fee per leg (0.1%)
    slippage_bps: float = 5.0      # adverse slippage per fill
    atr_multiplier: float | None = 2.0  # stop distance = ATR * multiplier; None or NaN ATR -> no stop
    trail_atr: float | None = None  # trailing exit: a long closing below its highest close since
                                    # entry - ATR * this exits at the next open; None -> off
    allow_short: bool = False      # spot trading: long-only
    initial_equity: float = 1000.0


@dataclass
class Position:
    trade_id: str
    side: str                      # "LONG" | "SHORT"
    qty: float
    entry_time: object
    entry_price: float             # fill price after slippage
    entry_fee: float
    equity_before: float
    stop: float | None
    signal_rsi: float | None = None
    high: float | None = None      # highest close since entry (trailing exit)


@dataclass
class Trade:
    trade_id: str
    symbol: str
    side: str
    entry_time: object
    exit_time: object
    entry_price: float
    exit_price: float
    qty: float
    fees: float
    pnl: float
    return_pct: float
    reason: str                    # "SIGNAL" | "STOP" | "TRAIL" | "END"
    entry_rsi: float | None = None


def _num(x):
    """float(x), or None for missing values."""
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else x


class Engine:
    def __init__(self, config=None, symbol="", on_open=None, on_close=None):
        self.config = config or EngineConfig()
        self.symbol = symbol
        self.equity = self.config.initial_equity   # realized equity (cash when flat)
        self.position: Position | None = None
        self.pending = None                         # ("ENTER", side, atr, rsi) | ("EXIT", reason)
        self.reentry_above = None                   # after a trailing exit: see on_bar step 4
        self.trades: list[Trade] = []
        self.equity_curve: list[tuple] = []         # (time, marked-to-market equity)
        self.on_open = on_open
        self.on_close = on_close

    # ---------- fills ----------
    def _slip(self, price, side, entering):
        """Adverse slippage: buying fills higher, selling fills lower."""
        s = self.config.slippage_bps / 10_000
        buying = (side == "LONG") == entering
        return price * (1 + s) if buying else price * (1 - s)

    def _stop_for(self, side, fill, atr):
        if atr is None or not self.config.atr_multiplier:
            return None
        dist = atr * self.config.atr_multiplier
        return fill - dist if side == "LONG" else fill + dist

    def entry_stop(self, open_):
        """Stop of the position the pending entry would open at `open_` (None if no entry
        is pending or it gets no stop). Lets an intraday watcher check the entry candle."""
        if self.pending is None or self.pending[0] != "ENTER" or self.position is not None:
            return None
        _, side, atr, _ = self.pending
        return self._stop_for(side, self._slip(open_, side, entering=True), atr)

    def _open(self, side, price, time, atr, rsi):
        fill = self._slip(price, side, entering=True)
        fee_rate = self.config.fee_rate
        qty = self.equity / (fill * (1 + fee_rate))
        self.position = Position(
            trade_id=uuid.uuid4().hex[:8], side=side, qty=qty, entry_time=time,
            entry_price=fill, entry_fee=qty * fill * fee_rate, equity_before=self.equity,
            stop=self._stop_for(side, fill, atr), signal_rsi=rsi,
        )
        if self.on_open:
            self.on_open(self.symbol, self.position)

    def _close(self, price, time, reason, slip=True):
        p = self.position
        fill = self._slip(price, p.side, entering=False) if slip else price
        direction = 1 if p.side == "LONG" else -1
        exit_fee = p.qty * fill * self.config.fee_rate
        pnl = direction * p.qty * (fill - p.entry_price) - p.entry_fee - exit_fee
        self.equity = p.equity_before + pnl
        trade = Trade(
            trade_id=p.trade_id, symbol=self.symbol, side=p.side,
            entry_time=p.entry_time, exit_time=time,
            entry_price=p.entry_price, exit_price=fill, qty=p.qty,
            fees=p.entry_fee + exit_fee, pnl=pnl, return_pct=100 * pnl / p.equity_before,
            reason=reason, entry_rsi=p.signal_rsi,
        )
        self.trades.append(trade)
        self.position = None
        if self.on_close:
            self.on_close(trade)
        return trade

    def marked_equity(self, price):
        p = self.position
        if p is None:
            return self.equity
        direction = 1 if p.side == "LONG" else -1
        return p.equity_before - p.entry_fee + direction * p.qty * (price - p.entry_price)

    def resize(self, target, price):
        """Set the sleeve's marked equity to `target` at `price` (portfolio rebalancing).
        In a position the difference is traded at `price` with fee and slippage, which is
        taken from the target; flat, only cash moves. Returns the cost."""
        marked = self.marked_equity(price)
        if marked <= 0:
            return 0.0
        cost = 0.0
        if self.position is not None:
            cost = abs(target - marked) * (self.config.fee_rate + self.config.slippage_bps / 10_000)
        k = (target - cost) / marked
        self.equity *= k
        if self.position is not None:
            p = self.position
            p.qty *= k
            p.entry_fee *= k
            p.equity_before *= k
        return cost

    # ---------- main step ----------
    def on_bar(self, time, open_, high, low, close, signal="-", atr=None, rsi=None):
        """Process one closed candle and the signal computed at its close."""
        # 1) Fill the order queued on the previous candle at this open.
        if self.pending is not None:
            order, self.pending = self.pending, None
            if order[0] == "EXIT" and self.position is not None:
                reason = order[1] if len(order) > 1 else "SIGNAL"
                peak = self.position.high
                self._close(open_, time, reason)
                if reason == "TRAIL":
                    self.reentry_above = peak
            elif order[0] == "ENTER" and self.position is None:
                _, side, sig_atr, sig_rsi = order
                self._open(side, open_, time, sig_atr, sig_rsi)

        # 2) Stop-loss against this candle's range.
        p = self.position
        if p is not None and p.stop is not None:
            if p.side == "LONG" and low <= p.stop:
                self._close(min(open_, p.stop), time, "STOP")
            elif p.side == "SHORT" and high >= p.stop:
                self._close(max(open_, p.stop), time, "STOP")

        # 3) Mark to market at the close.
        self.equity_curve.append((time, self.marked_equity(close)))
        p = self.position
        if p is not None and p.side == "LONG":
            p.high = close if p.high is None else max(p.high, close)

        # 4) Queue an order for the next open. After a trailing exit the signal that was
        # still on doesn't buy back at once: entries wait for a candle without a BUY
        # signal (the regime reset) or a close above the exited trade's highest close.
        atr, rsi = _num(atr), _num(rsi)
        if self.reentry_above is not None and (signal != "BUY" or close > self.reentry_above):
            self.reentry_above = None
        trail = self.config.trail_atr
        if p is None:
            if signal == "BUY" and self.reentry_above is None:
                self.pending = ("ENTER", "LONG", atr, rsi)
            elif signal == "SELL" and self.config.allow_short:
                self.pending = ("ENTER", "SHORT", atr, rsi)
        elif (p.side == "LONG" and signal == "SELL") or (p.side == "SHORT" and signal == "BUY"):
            self.pending = ("EXIT", "SIGNAL")
        elif trail and atr is not None and p.side == "LONG" and close < p.high - trail * atr:
            self.pending = ("EXIT", "TRAIL")

    # ---------- persistence ----------
    def to_state(self):
        """JSON-serializable snapshot of the engine (for restarts)."""
        pos = None
        if self.position is not None:
            pos = asdict(self.position)
            pos["entry_time"] = pd.Timestamp(pos["entry_time"]).isoformat()
        return {"equity": self.equity, "position": pos,
                "pending": list(self.pending) if self.pending else None,
                "reentry_above": self.reentry_above}

    def load_state(self, state):
        self.equity = state["equity"]
        pos = state.get("position")
        if pos:
            self.position = Position(**{**pos, "entry_time": pd.Timestamp(pos["entry_time"])})
        else:
            self.position = None
        pending = state.get("pending")
        self.pending = tuple(pending) if pending else None
        self.reentry_above = state.get("reentry_above")

    def finish(self, time, close):
        """Close any open position at the final close (end of backtest data)."""
        self.pending = None
        if self.position is not None:
            self._close(close, time, "END")
            self.equity_curve[-1] = (self.equity_curve[-1][0], self.equity)


def run_backtest(df, signals, config=None, symbol=""):
    """Run the engine over a frame with OHLC, ATR and RSI columns and a signal Series.

    Returns (trades DataFrame, equity Series indexed by candle time).
    """
    engine = Engine(config, symbol)
    atr = df["ATR"] if "ATR" in df.columns else pd.Series(float("nan"), index=df.index)
    rsi = df["RSI"] if "RSI" in df.columns else pd.Series(float("nan"), index=df.index)
    for row in zip(df["timestamp"], df["open"], df["high"], df["low"], df["close"],
                   signals, atr, rsi):
        engine.on_bar(*row)
    if len(df):
        engine.finish(df["timestamp"].iloc[-1], float(df["close"].iloc[-1]))

    trades = pd.DataFrame([t.__dict__ for t in engine.trades],
                          columns=list(Trade.__dataclass_fields__))
    times, values = zip(*engine.equity_curve) if engine.equity_curve else ((), ())
    equity = pd.Series(values, index=pd.Index(times, name="timestamp"), name="equity", dtype=float)
    return trades, equity
