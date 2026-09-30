# paper_trader.py
import csv
import os
import uuid
import pandas as pd

# Config
try:
    from config import ATR_MULTIPLIER
except Exception:
    ATR_MULTIPLIER = 1.5

try:
    from config import FEE_RATE_TAKER, SLIPPAGE_BPS
except Exception:
    FEE_RATE_TAKER = 0.001  # 0.10%
    SLIPPAGE_BPS   = 2      # 2 bps = 0.02%

class PaperTrader:
    def __init__(self):
        self.position = None            # "LONG" | "SHORT" | None
        self.position_price = None      # raw entry price (for logging)
        self.entry_fill = None          # actual entry after slippage
        self.position_rsi = None
        self.position_symbol = None
        self.stop_loss = None
        self.fees_accum = 0.0
        self.trade_id = None

    def status(self):
        return self.position

    def _slip(self) -> float:
        return SLIPPAGE_BPS / 10000.0

    def open_position(self, position_type, price, rsi, atr=None):
        """price: last close (raw). Slippage and fee are applied here; entry_fill is stored."""
        self.position = position_type
        self.position_price = float(price)
        self.position_rsi = rsi
        self.trade_id = uuid.uuid4().hex[:8]
        self.fees_accum = 0.0

        # The stop price (ATR * multiplier) is set by run_realtime.
        # Here we only set the side and fill prices.
        slip = self._slip()
        if position_type == "LONG":
            self.entry_fill = self.position_price * (1 + slip)
        else:  # SHORT
            self.entry_fill = self.position_price * (1 - slip)

        # Entry fee (assuming a 1-unit position)
        fee_open = self.entry_fill * FEE_RATE_TAKER
        self.fees_accum += fee_open

        self.log_trade_open(position_type, self.position_price, rsi)

    def close_position(self, price, reason="SIGNAL"):
        """price: raw price at exit. Slippage and fee are applied and net PnL is computed."""
        if not self.position:
            return

        slip = self._slip()
        exit_raw = float(price)
        if self.position == "LONG":
            exit_fill = exit_raw * (1 - slip)
            gross = exit_fill - self.entry_fill
        else:  # SHORT
            exit_fill = exit_raw * (1 + slip)
            gross = self.entry_fill - exit_fill

        fee_exit = exit_fill * FEE_RATE_TAKER
        net_pnl = gross - (self.fees_accum + fee_exit)

        self.log_trade_close(exit_raw, net_pnl, reason)

        # reset
        self.position = None
        self.position_price = None
        self.entry_fill = None
        self.position_rsi = None
        self.stop_loss = None
        self.fees_accum = 0.0
        self.position_symbol = None
        self.trade_id = None

    def check_stop_loss(self, current_price):
        """Close the position and return True if the stop-loss is hit."""
        if self.position and self.stop_loss is not None:
            if self.position == "LONG" and current_price <= self.stop_loss:
                self.close_position(current_price, reason="STOP")
                return True
            elif self.position == "SHORT" and current_price >= self.stop_loss:
                self.close_position(current_price, reason="STOP")
                return True
        return False

    # ---------- LOGGING ----------
    def _trades_file(self):
        return "trades_log.csv"

    def log_trade_open(self, position_type, price, rsi):
        filename = self._trades_file()
        file_exists = os.path.isfile(filename)
        with open(filename, mode="a", newline="") as f:
            w = csv.writer(f)
            if not file_exists:
                w.writerow([
                    "TradeID", "Timestamp Entry", "Timestamp Exit", "Symbol",
                    "Position", "Entry Price", "Exit Price", "Entry RSI",
                    "PnL", "Close Reason"
                ])
            w.writerow([
                self.trade_id,
                pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                "",  # exit
                self.position_symbol or "",
                position_type,
                f"{price:.4f}",
                "",
                f"{rsi:.2f}" if rsi is not None else "",
                "",  # PnL
                ""   # reason
            ])

    def log_trade_close(self, price, pnl, reason):
        filename = self._trades_file()
        rows = []
        if not os.path.exists(filename):
            return

        with open(filename, mode="r", newline="") as f:
            rows = list(csv.reader(f))

        # Close the matching open trade by TradeID
        for i in range(len(rows)-1, 0, -1):
            row = rows[i]
            # columns: 0..9
            if len(row) < 10:
                continue
            if row[0] == self.trade_id and row[2] == "":
                rows[i][2] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")  # Timestamp Exit
                rows[i][6] = f"{price:.4f}"    # Exit Price
                rows[i][8] = f"{pnl:.6f}"      # PnL
                rows[i][9] = reason            # Close Reason
                break

        with open(filename, mode="w", newline="") as f:
            csv.writer(f).writerows(rows)
