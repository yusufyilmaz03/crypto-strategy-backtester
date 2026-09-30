import pandas as pd
import matplotlib.pyplot as plt

# Settings
CSV_PATH = "multi_backtest_strategies.csv"  # output of multi_backtest.py
STRATEGY = "v2"  # which strategy to plot
SEGMENT = "OOS"  # ALL, IS, OOS or WF1..
TRADE_COUNT_THRESHOLD = 1  # show combinations with more than this many trades

# Load the CSV
df = pd.read_csv(CSV_PATH)

# Filter by trade count and strategy
df_filtered = df[(df["Trades"] > TRADE_COUNT_THRESHOLD) & (df["Strategy"] == STRATEGY)
                 & (df["Segment"] == SEGMENT)]

# Sort by return
df_filtered = df_filtered.sort_values(by="Return%", ascending=True)

# Y label: Symbol + Timeframe
df_filtered["Label"] = df_filtered["Symbol"] + " (" + df_filtered["Timeframe"] + ")"

# Color: profit vs loss
colors = df_filtered["Return%"].apply(lambda x: "green" if x > 0 else "red")

# Scale figure height with the number of rows
fig_height = max(6, len(df_filtered) * 0.4)

# Plot
plt.figure(figsize=(12, fig_height))
plt.barh(df_filtered["Label"], df_filtered["Return%"], color=colors)
plt.axvline(x=0, color='gray', linestyle='--')
plt.xlabel("Return (%)")
plt.title(f"Backtest results ({STRATEGY}, {SEGMENT})")
plt.grid(axis='x')
plt.tight_layout()
plt.savefig("summary.png")

plt.show()
