import pandas as pd
import matplotlib.pyplot as plt

# Load the CSV
df = pd.read_csv("multi_backtest_strategies.csv")  # output of multi_backtest.py

# Profitable combinations only
df_positive = df[df["Total PnL"] > 0].sort_values(by="Total PnL", ascending=True)

# Horizontal bar chart
plt.figure(figsize=(12, 6))
plt.barh(df_positive["Symbol"] + " " + df_positive["Timeframe"] + " " + df_positive["Strategy"], df_positive["Total PnL"], color='green')
plt.xlabel("Total PnL (quote currency, per 1 unit position)")
plt.title("Profitable combinations (symbol + timeframe + strategy)")
plt.grid(True, axis='x')
plt.tight_layout()
plt.show()
