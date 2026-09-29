import pandas as pd
import matplotlib.pyplot as plt

# Ayarlar
CSV_PATH = "multi_backtest_strategies.csv"  # output of multi_backtest.py
STRATEGY = "v2"  # which strategy to plot
TRADE_COUNT_THRESHOLD = 1  # 1'den fazla işlem yapanları göster

# CSV dosyasını oku
df = pd.read_csv(CSV_PATH)

# İşlem sayısı filtresi uygula
df_filtered = df[(df["Trade Count"] > TRADE_COUNT_THRESHOLD) & (df["Strategy"] == STRATEGY)]

# PnL'ye göre sırala
df_filtered = df_filtered.sort_values(by="Total PnL", ascending=True)

# Y label: Symbol + Timeframe
df_filtered["Label"] = df_filtered["Symbol"] + " (" + df_filtered["Timeframe"] + ")"

# Renk: kârlı-zararlı
colors = df_filtered["Total PnL"].apply(lambda x: "green" if x > 0 else "red")

# ↕️ Yüksekliği satır sayısına göre otomatik ayarla
fig_height = max(6, len(df_filtered) * 0.4)

# Grafik
plt.figure(figsize=(12, fig_height))
plt.barh(df_filtered["Label"], df_filtered["Total PnL"], color=colors)
plt.axvline(x=0, color='gray', linestyle='--')
plt.xlabel("Total PnL (quote currency, per 1 unit position)")
plt.title(f"Backtest results ({STRATEGY})")
plt.grid(axis='x')
plt.tight_layout()
plt.savefig("summary.png")

plt.show()
