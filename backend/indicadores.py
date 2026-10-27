import pandas_ta as ta

def calcular_rsi(df, periodo=14):
    df["rsi"] = ta.rsi(df["close"], length=periodo)
    return df

def calcular_media_movil(df, periodo=50):
    df[f"sma_{periodo}"] = ta.sma(df["close"], length=periodo)
    return df

def calcular_macd(df):
    macd = ta.macd(df["close"])
    df = df.join(macd)
    return df
