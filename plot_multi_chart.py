import pandas as pd
import matplotlib.pyplot as plt

# Load the CSV
df = pd.read_csv("multi_backtest_strategies.csv")  # output of multi_backtest.py

# Profitable out-of-sample combinations only
df = df[df["Segment"] == "OOS"]
df_positive = df[df["Return%"] > 0].sort_values(by="Return%", ascending=True)

# Horizontal bar chart
plt.figure(figsize=(12, 6))
plt.barh(df_positive["Symbol"] + " " + df_positive["Timeframe"] + " " + df_positive["Strategy"], df_positive["Return%"], color='green')
plt.xlabel("Out-of-sample return (%)")
plt.title("Profitable OOS combinations (symbol + timeframe + strategy)")
plt.grid(True, axis='x')
plt.tight_layout()
plt.show()
