# Versión sin pandas_ta (solo pandas): pandas_ta no instala bien en Python 3.14.
# Mantiene las mismas funciones y nombres de columnas que antes.
import pandas as pd


def _rma(serie, periodo):
    # Media de Wilder (la que usa el RSI clásico)
    return serie.ewm(alpha=1.0 / periodo, min_periods=periodo, adjust=False).mean()


def _rsi(close, periodo=14):
    delta = close.diff()
    sube = delta.clip(lower=0)
    baja = -delta.clip(upper=0)
    prom_sube = _rma(sube, periodo)
    prom_baja = _rma(baja, periodo)
    return 100 * prom_sube / (prom_sube + prom_baja)


def calcular_rsi(df, periodo=14):
    df["rsi"] = _rsi(df["close"], periodo)
    return df


def calcular_media_movil(df, periodo=50):
    df[f"sma_{periodo}"] = df["close"].rolling(window=periodo, min_periods=periodo).mean()
    return df


def calcular_macd(df, rapida=12, lenta=26, senal=9):
    ema_r = df["close"].ewm(span=rapida, min_periods=rapida, adjust=False).mean()
    ema_l = df["close"].ewm(span=lenta, min_periods=lenta, adjust=False).mean()
    macd = ema_r - ema_l
    sig = macd.ewm(span=senal, min_periods=senal, adjust=False).mean()
    out = pd.DataFrame({
        f"MACD_{rapida}_{lenta}_{senal}": macd,
        f"MACDh_{rapida}_{lenta}_{senal}": macd - sig,
        f"MACDs_{rapida}_{lenta}_{senal}": sig,
    }, index=df.index)
    return df.join(out)
