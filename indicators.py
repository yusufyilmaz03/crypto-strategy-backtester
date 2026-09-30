# indicators.py

def add_indicators(df):
    # RSI
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # EMAs
    df["EMA_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["EMA_21"] = df["close"].ewm(span=21, adjust=False).mean()

    # ATR (Average True Range)
    df["H-L"] = df["high"] - df["low"]
    df["H-C"] = (df["high"] - df["close"].shift()).abs()
    df["L-C"] = (df["low"] - df["close"].shift()).abs()
    df["TR"] = df[["H-L", "H-C", "L-C"]].max(axis=1)
    df["ATR"] = df["TR"].rolling(window=14).mean()

    # Drop helper columns
    df.drop(columns=["H-L", "H-C", "L-C", "TR"], inplace=True)

    return df
