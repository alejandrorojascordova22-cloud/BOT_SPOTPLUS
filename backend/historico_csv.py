# ============================================================
# HISTÓRICO LOCAL (CSV exportados de MT5) — respaldo para el backtest
# ============================================================
# MT5 solo guarda ~100,000 velas de M1 (en tu caso desde 2026-05-19), así que
# un backtest que pida M1 desde enero devuelve vacío y la estrategia (que usa
# M1 para confirmar) nunca da señales. Este módulo lee los CSV que ya tienes
# en la raíz del proyecto (XAUUSD_M1_*.csv, XAUUSD_M3_*, XAUUSD_M5_*,
# XAUUSD_H1_*) y construye las demás temporalidades por resample:
#   M15, M30        <- M5
#   H2, H4, D1, W1, MN1 <- H1
# ============================================================
import glob
import os

import pandas as pd

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (temporalidad base, regla pandas) por cada marco derivado
_DERIVADAS = {
    "M15": ("M5", "15min"),
    "M30": ("M5", "30min"),
    "H2": ("H1", "2h"),
    "H4": ("H1", "4h"),
    "D1": ("H1", "1D"),
    "W1": ("H1", "W-SUN"),
    "MN1": ("H1", "MS"),
}
_cache = {}


def _archivo(simbolo, tf):
    patron = os.path.join(RAIZ, f"{simbolo}_{tf}_*.csv")
    candidatos = sorted(glob.glob(patron))
    return candidatos[-1] if candidatos else None


def _leer_csv(ruta):
    df = pd.read_csv(ruta, sep="\t")
    df.columns = [c.strip().strip("<>").lower() for c in df.columns]
    df["time"] = pd.to_datetime(
        df["date"].astype(str) + " " + df["time"].astype(str), format="%Y.%m.%d %H:%M:%S"
    )
    return df[["time", "open", "high", "low", "close"]].sort_values("time").reset_index(drop=True)


def _resample(df, regla, tf):
    o = df.set_index("time")
    # W-SUN con closed/label a la izquierda => semanas que empiezan en domingo (como MT5)
    r = o.resample(regla, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    return r.reset_index()


def cargar_tf(simbolo, tf):
    """DataFrame completo (time, open, high, low, close) de una temporalidad, o None."""
    clave = (simbolo, tf)
    if clave in _cache:
        return _cache[clave]
    df = None
    ruta = _archivo(simbolo, tf)
    if ruta:
        df = _leer_csv(ruta)
    elif tf in _DERIVADAS:
        base, regla = _DERIVADAS[tf]
        df_base = cargar_tf(simbolo, base)
        if df_base is not None:
            df = _resample(df_base, regla, tf)
    _cache[clave] = df
    return df


def velas_csv(simbolo, tf, desde, hasta):
    """Igual que obtener_velas_historicas pero desde los CSV. desde/hasta 'YYYY-MM-DD'."""
    df = cargar_tf(simbolo, tf)
    if df is None:
        return None
    ini = pd.Timestamp(desde)
    fin = pd.Timestamp(hasta) + pd.Timedelta(days=1)
    sub = df[(df["time"] >= ini) & (df["time"] < fin)].reset_index(drop=True)
    return sub if len(sub) else None


def rango_disponible(simbolo, tf):
    df = cargar_tf(simbolo, tf)
    if df is None or df.empty:
        return None
    return df["time"].iloc[0], df["time"].iloc[-1]


def completar_con_csv(df_mt5, simbolo, tf, desde, hasta):
    """Velas de MT5 completadas por delante con el CSV cuando MT5 empieza tarde
    (M1 solo guarda ~100k velas). Ambos están en hora de servidor MT5.
    Devuelve (df, fuente) con fuente en {"mt5", "csv", "mt5+csv", None}."""
    csv_df = velas_csv(simbolo, tf, desde, hasta)
    hay_mt5 = df_mt5 is not None and len(df_mt5) > 0
    if not hay_mt5:
        return (csv_df, "csv") if csv_df is not None else (None, None)
    if csv_df is None:
        return df_mt5, "mt5"
    antes = csv_df[csv_df["time"] < df_mt5["time"].min()]
    if antes.empty:
        return df_mt5, "mt5"
    cols = ["time", "open", "high", "low", "close"]
    unido = pd.concat([antes[cols], df_mt5[cols]], ignore_index=True)
    unido = unido.sort_values("time").drop_duplicates(subset="time").reset_index(drop=True)
    return unido, "mt5+csv"
