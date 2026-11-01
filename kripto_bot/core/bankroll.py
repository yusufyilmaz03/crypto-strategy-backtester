# kripto_bot/core/bankroll.py
"""Instance bankroll: signal strength scores, grades, stakes and rotation choices
(docs/bankroll-management.md).

An instance with sizing "bankroll" has one cash balance shared by its symbols. Every BUY
signal of a close is scored from closed candles only, graded, and funded with a stake of
the grade's percentage of the contributed capital (starting bankroll + deposits −
withdrawals), strongest first, while free cash lasts. Optionally, a very strong signal
that found no cash may close or reduce weaker positions (rotation).

Pure functions only: the paper bot (trading/run_realtime.BankrollRunner) and a later
portfolio backtest share them.
"""
import math
from dataclasses import dataclass

import pandas as pd

GRADES = ["Weak", "Normal", "Strong", "Very strong"]
GRADE_FROM = (40.0, 60.0, 80.0)          # score at which Normal, Strong and Very strong start
DEFAULT_STAKES = (0.0, 3.0, 5.0, 8.0)    # % of the contributed capital per grade
DEFAULT_MIN_ENTRY = 10.0                 # USDT
MIN_ORDER = 5.0                          # Binance minimum order (notional), USDT
ROTATE_MARGIN = 20.0                     # a position is rotated out only if its score is this much lower
ROTATE_MIN_HOLD = 3                      # ... and it was held at least this many candles
ROTATE_MAX = 2                           # rotations per candle at most
DEFAULT_LOW_PCT = 70.0                   # low-bankroll threshold, % of the contributed capital
LOW_HYSTERESIS = 5.0                     # points above the threshold to count as recovered

TREND_SMA = 200                          # trend input when the instance has no trend filter
SCORE_SMA = 50                           # distance and slope SMA when the strategy has none
SLOPE_LEN = 10
MOMENTUM_LEN = 60
BREAKOUT_LEN = 55
VOLUME_LEN = 20

INPUTS = {
    "trend_sma": "Close above the trend SMA (the instance's trend filter length, else 200)",
    "lower_tf": "The lower timeframes say BUY too (1d: 4h + 1h, 4h: 1h + 15m), half a point each",
    "distance": "Distance of the close above the SMA in ATRs: full from 2 to 4 ATR, half above 4 (stretched)",
    "slope": "Rise of the SMA over the last 10 candles: full at +0.5% per candle",
    "momentum": "60-candle return, ranked among the instance's symbols at this close",
    "rs_btc": "60-candle return above BTC's",
    "breakout": "Close at the highest close of the last 55 candles",
    "volume": "Volume against its 20-candle mean: half at 1x, full at 1.5x",
    "rsi": "RSI between 50 and 70 (half from 70 to 80, none above)",
    "low_vol": "Low volatility (ATR % of the close), ranked among the instance's symbols",
}
DEFAULT_INPUTS = ("trend_sma", "lower_tf", "distance", "slope", "momentum")
LOW_BASES = {"value": "Bankroll value (cash + positions)", "free": "Free cash"}


@dataclass
class BankrollConfig:
    stakes: tuple = DEFAULT_STAKES
    min_entry: float = DEFAULT_MIN_ENTRY
    max_entry: float | None = None
    inputs: tuple = DEFAULT_INPUTS
    score_sma: int = SCORE_SMA
    trend_sma: int = TREND_SMA
    rotation: bool = False
    profit_only: bool = False            # rotation option 1: only positions in profit
    trim: bool = False                   # rotation option 2: reduce instead of close
    fee_rate: float = 0.001
    slippage_bps: float = 5.0

    @property
    def floor(self):
        """Smallest stake that is bought."""
        return max(self.min_entry or 0.0, MIN_ORDER)

    def exit_value(self, qty, price):
        """Cash a market sale of qty at price brings after slippage and fee."""
        return qty * price * (1 - self.slippage_bps / 10_000) * (1 - self.fee_rate)


# ---------------------------------------------------------------- scores
def features(df, score_sma=SCORE_SMA, trend_sma=TREND_SMA):
    """Raw score inputs for every candle of `df` (open, high, low, close, volume, ATR,
    RSI), indexed by candle time. Each row uses that candle and earlier ones only."""
    close = df["close"].astype(float)
    sma = close.rolling(score_sma).mean()
    atr = df["ATR"] if "ATR" in df.columns else pd.Series(float("nan"), index=df.index)
    volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(float("nan"), index=df.index)
    out = pd.DataFrame({
        "above_trend": (close > close.rolling(trend_sma).mean()).where(close.rolling(trend_sma).count() == trend_sma),
        "distance": (close - sma) / atr,
        "slope": (sma / sma.shift(SLOPE_LEN) - 1) / SLOPE_LEN,
        "ret": close / close.shift(MOMENTUM_LEN) - 1,
        "breakout": (close >= close.rolling(BREAKOUT_LEN).max()).where(close.rolling(BREAKOUT_LEN).count() == BREAKOUT_LEN),
        "volume": volume / volume.rolling(VOLUME_LEN).mean(),
        "rsi": df["RSI"] if "RSI" in df.columns else float("nan"),
        "atr_pct": atr / close,
    })
    out.index = pd.DatetimeIndex(df["timestamp"])
    return out


def _bad(x):
    return x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x)


def _clip(x):
    return min(max(x, 0.0), 1.0)


def _distance_points(d):
    if d <= 0:
        return 0.0
    if d <= 2:
        return d / 2
    return 1.0 if d <= 4 else 0.5


def _rsi_points(r):
    if 50 <= r <= 70:
        return 1.0
    return 0.5 if 70 < r <= 80 else 0.0


def scores(rows, inputs, lower=None, btc_ret=None):
    """Strength score 0-100 of each symbol at one close.

    rows: {symbol: features row (a Series from `features`)} of every symbol of the
    instance that has this close (the ranked inputs compare them with each other);
    lower: {symbol: share of the lower timeframes that say BUY, or None};
    btc_ret: BTC's 60-candle return at this close (rs_btc), or None.
    Missing values give no points. Returns {symbol: score}."""
    if not rows or not inputs:
        return {s: 0.0 for s in rows}
    frame = pd.DataFrame(rows).T
    rank_ret = pd.to_numeric(frame["ret"], errors="coerce").rank(pct=True)
    rank_vol = pd.to_numeric(frame["atr_pct"], errors="coerce").rank(ascending=False, pct=True)
    lower = lower or {}
    out = {}
    for s, f in rows.items():
        pts = []
        for name in inputs:
            if name == "trend_sma":
                p = 0.0 if _bad(f["above_trend"]) else float(bool(f["above_trend"]))
            elif name == "lower_tf":
                p = 0.0 if _bad(lower.get(s)) else _clip(lower[s])
            elif name == "distance":
                p = 0.0 if _bad(f["distance"]) else _distance_points(float(f["distance"]))
            elif name == "slope":
                p = 0.0 if _bad(f["slope"]) else _clip(float(f["slope"]) / 0.005)
            elif name == "momentum":
                p = 0.0 if _bad(rank_ret.get(s)) else float(rank_ret[s])
            elif name == "rs_btc":
                if _bad(f["ret"]) or _bad(btc_ret):
                    p = 0.0
                else:
                    p = 1.0 if f["ret"] > btc_ret else 0.5 if f["ret"] == btc_ret else 0.0
            elif name == "breakout":
                p = 0.0 if _bad(f["breakout"]) else float(bool(f["breakout"]))
            elif name == "volume":
                p = 0.0 if _bad(f["volume"]) else _clip(float(f["volume"]) - 0.5)
            elif name == "rsi":
                p = 0.0 if _bad(f["rsi"]) else _rsi_points(float(f["rsi"]))
            elif name == "low_vol":
                p = 0.0 if _bad(rank_vol.get(s)) else float(rank_vol[s])
            else:
                raise ValueError(f"unknown score input {name}")
            pts.append(p)
        out[s] = round(100 * sum(pts) / len(pts), 1)
    return out


def grade(score):
    """Grade index 0-3 (GRADES) of a score."""
    return sum(score >= g for g in GRADE_FROM)


# ---------------------------------------------------------------- funding
def stake(cfg, base, g, free):
    """(USDT to put into an entry of grade g, note). base: contributed capital. The note
    says why nothing is bought, or that the stake was cut to the free cash."""
    pct = cfg.stakes[g]
    if pct <= 0:
        return 0.0, f"grade {GRADES[g]}: stake 0%"
    want = base * pct / 100
    if cfg.max_entry:
        want = min(want, cfg.max_entry)
    if want < cfg.floor:
        return 0.0, f"stake {want:.2f} USDT below the minimum entry"
    if free < cfg.floor:
        return 0.0, "no free cash"
    if free < want:
        return free, f"cut to the free cash ({want:.2f} wanted)"
    return want, None


@dataclass
class Funding:
    symbol: str
    score: float
    grade: int
    amount: float          # 0: not funded
    wanted: float          # stake before the free-cash cap
    reason: str | None     # why not funded (or why less)


def fund(cfg, base, free, candidates):
    """Fund BUY candidates [(symbol, score)], strongest first (ties: symbol name).
    Returns ([Funding], free cash left)."""
    out = []
    for symbol, score in sorted(candidates, key=lambda c: (-c[1], c[0])):
        g = grade(score)
        wanted, _ = stake(cfg, base, g, float("inf"))
        amount, reason = stake(cfg, base, g, free)
        free -= amount
        out.append(Funding(symbol, score, g, amount, wanted, reason))
    return out, free


# ---------------------------------------------------------------- rotation
@dataclass
class Holding:
    symbol: str
    score: float
    qty: float
    price: float           # close the decision is made at
    cost: float            # cash put into the position (equity_before: entry cost with fee)
    held: int              # candles since entry
    busy: bool = False     # already queued to exit, or chosen this candle


@dataclass
class Rotation:
    symbol: str
    fraction: float        # 1.0: close; else the share of the position to sell
    expected: float        # cash expected at the next open, at today's close


def rotation_plan(cfg, need, new_score, holdings, used=0):
    """Positions to sell at the next open to free `need` USDT for an entry scoring
    `new_score`. Candidates: score lower by ROTATE_MARGIN, held ROTATE_MIN_HOLD candles,
    not busy, and with profit_only in profit after the exit costs; weakest score first,
    then the smallest unrealized return. At most ROTATE_MAX - used rotations. With trim a
    position is reduced by what is still needed, unless the rest would fall below the
    minimum entry. Returns [Rotation] (empty if they can't free enough)."""
    picks = []
    pool = []
    for h in holdings:
        if h.busy or h.held < ROTATE_MIN_HOLD or h.score > new_score - ROTATE_MARGIN or h.qty <= 0:
            continue
        value = cfg.exit_value(h.qty, h.price)
        if cfg.profit_only and value <= h.cost:
            continue
        pool.append((h.score, value / h.cost - 1 if h.cost else 0.0, h.symbol, value))
    left = need
    for _, _, symbol, value in sorted(pool):
        if len(picks) + used >= ROTATE_MAX or left <= 0:
            break
        if cfg.trim and value - left >= cfg.floor:
            picks.append(Rotation(symbol, left / value, left))
            left = 0.0
        else:
            picks.append(Rotation(symbol, 1.0, value))
            left -= value
    return picks if left <= 0 else []


# ---------------------------------------------------------------- low bankroll
def low_state(basis, pct, value, free, contributed, was_low=False):
    """Is the bankroll low? basis 'value' compares the bankroll value, 'free' the free
    cash, with pct % of the contributed capital. Once low, it stays low until it is
    LOW_HYSTERESIS points above the threshold."""
    if not contributed or contributed <= 0:
        return False
    level = 100 * (value if basis == "value" else free) / contributed
    return level < pct + (LOW_HYSTERESIS if was_low else 0.0)


def twr_step(twr, value_before, value_now, flow):
    """Time-weighted return index after one period: cash flows (deposits +,
    withdrawals −) made during the period are not return."""
    if value_before is None or value_before <= 0:
        return twr
    return twr * (value_now - flow) / value_before
