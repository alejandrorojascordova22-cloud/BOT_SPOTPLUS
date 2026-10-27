"""
============================================================
BACKTEST — Estrategia de Estructura de Mercado (SpotPlus)
============================================================
Corre la estrategia contra datos históricos y reporta:
- Cuántas operaciones habría tomado
- Cuántas ganaron / perdieron
- Ganancia/pérdida total (en "R" — múltiplos de riesgo)
- Drawdown máximo

CÓMO USARLO:
1. Pon tu archivo de velas históricas en formato CSV con columnas:
   time, open, high, low, close   (una fila por vela)
2. Por defecto, este script ya NO usa valores fijos a mano: lee
   automáticamente backend/bot_config.json (lo mismo que guarda la app
   cada vez que tocas "Guardar" en Estrategia) y toma de ahí el símbolo,
   las temporalidades y los parámetros de la estrategia ACTIVA — igual
   que hace /api/backtest en la app.
   Solo funciona así si la estrategia activa es "estructura_dinamica"
   (esta es la única que este script sabe simular). Si tienes activa
   otra cosa (código libre, bloques, texto), este script avisa y sigue
   con los valores de abajo como respaldo manual — no puede simular esos
   tipos de estrategia.
3. Corre: python backtest_estructura.py
============================================================
"""

import json
import os

import pandas as pd
from backend import estructura as est

RUTA_BOT_CONFIG = os.path.join(os.path.dirname(__file__), "backend", "bot_config.json")


def cargar_config_activa():
    """
    Lee backend/bot_config.json (la config real que la app tiene guardada)
    y devuelve (activo, tf_entrada, tf_macro, params) si la estrategia
    activa es "estructura_dinamica", o None si no existe el archivo o la
    estrategia activa es otra (en ese caso el script usa los valores
    manuales de abajo como respaldo).
    """
    if not os.path.exists(RUTA_BOT_CONFIG):
        print(f"⚠ No encontré {RUTA_BOT_CONFIG} — usando los valores manuales de este archivo.")
        return None

    with open(RUTA_BOT_CONFIG, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if cfg.get("estrategia") != "estructura_dinamica":
        print(
            f"⚠ La estrategia activa en la app es '{cfg.get('estrategia')}', no "
            f"'estructura_dinamica'. Este script solo sabe simular esa — usando "
            f"los valores manuales de abajo como respaldo, pero OJO: no van a "
            f"coincidir con lo que el bot realmente correría en vivo."
        )
        return None

    return {
        "activo": cfg.get("simbolo", ACTIVO),
        "tf_entrada": cfg.get("timeframe_entrada", TEMPORALIDAD_ENTRADA),
        "tf_macro": cfg.get("timeframe_macro", TEMPORALIDAD_MACRO),
        "params": cfg.get("parametros") or {},
    }


# ============================================================
# CONFIGURACIÓN — ajusta esto a tu gusto
# ============================================================

# --- SELECTOR DE ACTIVO Y TEMPORALIDADES ---
ACTIVO = "XAUUSD"  # "XAUUSD" (oro), "EURUSD", "AAPL", "MSFT", etc.
TEMPORALIDAD_ENTRADA = "M5"  # M1, M3, M5, H1, H4, etc.
TEMPORALIDAD_MACRO = "H1"    # H1, H4, D1, etc. (la que marca el movimiento macro)

# Los archivos se buscarán automáticamente como: {ACTIVO}_{TEMPORALIDAD}_*.csv
ARCHIVO_CSV = None  # Se detecta automáticamente
ARCHIVO_CSV_MACRO = None  # Se detecta automáticamente

PIVOT_LEN = 5                           # sensibilidad de pivotes de ENTRADA
PIVOT_LEN_MACRO = 15                    # sensibilidad de pivotes del movimiento MACRO (para el TP)
PORCENTAJE_TP = 0.8                     # % de la distancia macro que se proyecta como TP
SL_BUFFER_PCT = 0.1                     # % de colchón entre el SL y la zona HL/LH
MAX_VELAS_DESDE_ZONA = None             # None = sin límite; o un número para ignorar zonas viejas
MINIMO_RATIO_TP = 1.5                   # descarta señales cuyo TP/riesgo real quede por debajo de
                                         # esto (evita tomar señales donde el movimiento macro ya
                                         # está agotado y el TP calculado casi no vale la pena).
                                         # None = sin filtro (toma todas, como antes).
BE_ACTIVO = False                       # si True, cuando el precio avanza BE_PORCENTAJE de la
                                         # distancia entrada->tp a favor, el SL se mueve a la
                                         # entrada (breakeven). El volumen/riesgo de la operación
                                         # NO cambia, solo se protege de que una ganada se vuelva
                                         # pérdida completa.
BE_PORCENTAJE = 0.5                     # 0.5 = mueve a BE cuando ya recorrió el 50% del camino
                                         # hacia el TP (macro 1h / entrada 5m del ejemplo).

# --- Cuenta / dinero real ---
BALANCE_CUENTA = 5000.0                 # tamaño de cuenta para traducir R a dinero
RIESGO_PORCENTAJE = 0.01                # 1% de riesgo por operación (0.01 = 1%)
VELAS_MINIMAS_PARA_EMPEZAR = 60         # no evaluamos señales hasta tener esta cantidad de velas
VENTANA_MAXIMA = 500                    # cuántas velas hacia atrás mirar para detectar pivotes de
                                         # ENTRADA en cada paso (evita recalcular sobre TODO el
                                         # historial acumulado, que se vuelve muy lento con CSVs
                                         # grandes de M1/M3 y no aporta nada: los pivotes viejos
                                         # de hace meses no afectan una señal de hoy).


def buscar_archivo_csv(activo, temporalidad):
    """
    Busca automáticamente el archivo CSV para el activo y temporalidad dado.
    Ejemplo: buscar_archivo_csv("XAUUSD", "M5") -> "XAUUSD_M5_*.csv"
    """
    import glob
    import os
    
    patrón = f"{activo}_{temporalidad}_*.csv"
    candidatos = glob.glob(patrón)
    
    if candidatos:
        return candidatos[0]
    else:
        raise FileNotFoundError(
            f"No encontré ningún archivo para {activo} en temporalidad {temporalidad}.\n"
            f"Patrones buscados: {patrón}\n"
            f"Archivos disponibles: {', '.join(glob.glob('*.csv'))}"
        )


def cargar_datos(archivo):
    """
    Carga el CSV y normaliza los nombres de columna a minúsculas
    (open, high, low, close). Soporta tanto un CSV simple separado por
    comas como el formato de exportación de MT5 ("Export Bars"), que
    viene separado por tabulaciones y con encabezados como <OPEN>, <HIGH>.
    """
    import os

    if not os.path.exists(archivo):
        raise FileNotFoundError(f"No encontré el archivo: {archivo}")

    # Intenta primero como CSV normal (separado por comas)
    df = pd.read_csv(archivo)

    # Si solo detectó 1 columna, probablemente viene separado por tabulaciones (formato MT5)
    if df.shape[1] == 1:
        df = pd.read_csv(archivo, sep="\t")

    # Normaliza nombres de columna: quita < >, espacios, y pasa a minúsculas
    df.columns = [c.strip().strip("<>").lower() for c in df.columns]

    # Formato MT5 trae fecha y hora en columnas separadas (date, time) — hay
    # que combinarlas en una sola columna "time" con fecha+hora real, o el
    # alineado entre temporalidades (multi-timeframe) queda mal (solo
    # compararía la hora del día, sin la fecha).
    if "date" in df.columns and "time" in df.columns:
        df["time"] = pd.to_datetime(
            df["date"].astype(str) + " " + df["time"].astype(str),
            format="%Y.%m.%d %H:%M:%S",
        )
        df = df.drop(columns=["date"])
    elif "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])

    columnas_necesarias = {"open", "high", "low", "close"}
    if not columnas_necesarias.issubset(df.columns):
        raise ValueError(
            f"El CSV debe tener las columnas open, high, low, close.\n"
            f"Columnas encontradas: {list(df.columns)}"
        )
    return df


def simular_operacion(df, indice_entrada, tipo, precio_entrada, sl, tp,
                       be_activo=False, be_porcentaje=0.8):
    """
    Desde 'indice_entrada' hacia adelante, revisa vela por vela si el precio
    toca primero el SL o el TP. Devuelve "gano", "perdio", "breakeven" (se
    cerró en el SL pero el SL ya estaba movido a la entrada, así que no
    hubo pérdida real), o "sin_cerrar".

    Si be_activo=True: en cuanto el precio avanza be_porcentaje de la
    distancia entrada->tp a favor, el SL efectivo pasa a ser la entrada
    misma (breakeven). Esto se revisa ANTES de comprobar el toque de SL/TP
    en esa misma vela, igual que en simular_backtest (backend/backtesting.py).
    """
    distancia_tp = abs(tp - precio_entrada)
    umbral = distancia_tp * be_porcentaje
    sl_actual = sl
    be_aplicado = False

    for i in range(indice_entrada + 1, len(df)):
        vela = df.iloc[i]

        if be_activo and not be_aplicado:
            if tipo == "compra":
                if vela["high"] >= precio_entrada + umbral:
                    sl_actual = precio_entrada
                    be_aplicado = True
            else:
                if vela["low"] <= precio_entrada - umbral:
                    sl_actual = precio_entrada
                    be_aplicado = True

        if tipo == "compra":
            if vela["low"] <= sl_actual:
                return "breakeven" if be_aplicado else "perdio"
            if vela["high"] >= tp:
                return "gano"
        else:  # venta
            if vela["high"] >= sl_actual:
                return "breakeven" if be_aplicado else "perdio"
            if vela["low"] <= tp:
                return "gano"
    return "sin_cerrar"


def correr_backtest():
    global ACTIVO, TEMPORALIDAD_ENTRADA, TEMPORALIDAD_MACRO
    global PIVOT_LEN, PIVOT_LEN_MACRO, PORCENTAJE_TP, SL_BUFFER_PCT
    global MAX_VELAS_DESDE_ZONA, MINIMO_RATIO_TP

    activa = cargar_config_activa()
    if activa is not None:
        ACTIVO = activa["activo"]
        TEMPORALIDAD_ENTRADA = activa["tf_entrada"]
        TEMPORALIDAD_MACRO = activa["tf_macro"]
        p = activa["params"]
        PIVOT_LEN = int(p.get("pivot_len", PIVOT_LEN))
        PIVOT_LEN_MACRO = int(p.get("pivot_len_macro", PIVOT_LEN_MACRO))
        PORCENTAJE_TP = float(p.get("porcentaje_tp", PORCENTAJE_TP))
        SL_BUFFER_PCT = float(p.get("sl_buffer_pct", SL_BUFFER_PCT))
        MAX_VELAS_DESDE_ZONA = (
            int(p["max_velas_desde_zona"])
            if float(p.get("max_velas_desde_zona", 0)) > 0
            else None
        )
        MINIMO_RATIO_TP = (
            float(p["minimo_ratio_tp"])
            if float(p.get("minimo_ratio_tp", 0)) > 0
            else None
        )
        print(f"✓ Usando la configuración ACTIVA de la app (backend/bot_config.json)\n")

    # Detecta automáticamente los archivos CSV basado en ACTIVO y TEMPORALIDADES
    archivo_entrada = buscar_archivo_csv(ACTIVO, TEMPORALIDAD_ENTRADA)
    print(f"✓ Activo: {ACTIVO} | Entrada: {TEMPORALIDAD_ENTRADA} | Macro: {TEMPORALIDAD_MACRO}")
    print(f"  Archivo entrada: {archivo_entrada}")
    
    df = cargar_datos(archivo_entrada)
    print(f"  Datos de entrada cargados: {len(df)} velas.")

    df_macro_completo = None
    tiempos_macro = None
    try:
        archivo_macro = buscar_archivo_csv(ACTIVO, TEMPORALIDAD_MACRO)
        print(f"  Archivo macro: {archivo_macro}")
        df_macro_completo = cargar_datos(archivo_macro)
        df_macro_completo = df_macro_completo.sort_values("time").reset_index(drop=True)
        tiempos_macro = df_macro_completo["time"].values
        print(f"  Datos macro cargados: {len(df_macro_completo)} velas (multi-timeframe real).\n")
    except FileNotFoundError:
        print(f"  ⚠ No se encontró archivo macro {ACTIVO}_{TEMPORALIDAD_MACRO}_*.csv")
        print("  Usando aproximación de un solo timeframe.\n")

    resultados = []
    ultimo_indice_operado = -1  # para no abrir 2 operaciones sobre el mismo tramo

    import time as _time
    total_velas = len(df) - VELAS_MINIMAS_PARA_EMPEZAR
    inicio_reloj = _time.time()
    cada_cuantas = 5000  # imprime avance cada 5,000 velas procesadas

    for pos, i in enumerate(range(VELAS_MINIMAS_PARA_EMPEZAR, len(df))):
        if pos > 0 and pos % cada_cuantas == 0:
            transcurrido = _time.time() - inicio_reloj
            velas_por_seg = pos / transcurrido if transcurrido > 0 else 0
            restantes = total_velas - pos
            eta_seg = restantes / velas_por_seg if velas_por_seg > 0 else 0
            porcentaje = pos / total_velas * 100
            print(
                f"  ... {pos:,}/{total_velas:,} velas ({porcentaje:.1f}%) | "
                f"{len(resultados)} señales encontradas | "
                f"transcurrido {transcurrido/60:.1f} min | "
                f"faltan ≈{eta_seg/60:.1f} min",
                flush=True,
            )

        inicio_ventana = max(0, i + 1 - VENTANA_MAXIMA)
        df_hasta_aqui = df.iloc[inicio_ventana : i + 1]

        # Evita re-evaluar sobre velas ya usadas en una operación reciente
        if i <= ultimo_indice_operado:
            continue

        df_macro_hasta_aqui = None
        if tiempos_macro is not None:
            corte = tiempos_macro.searchsorted(df_hasta_aqui["time"].iloc[-1], side="right")
            if corte > 0:
                df_macro_hasta_aqui = df_macro_completo.iloc[:corte]

        resultado = est.señal_estructura(
            df_hasta_aqui,
            pivot_len=PIVOT_LEN,
            pivot_len_macro=PIVOT_LEN_MACRO,
            porcentaje_tp=PORCENTAJE_TP,
            sl_buffer_pct=SL_BUFFER_PCT,
            max_velas_desde_zona=MAX_VELAS_DESDE_ZONA,
            df_macro=df_macro_hasta_aqui,
            minimo_ratio_tp=MINIMO_RATIO_TP,
        )
        if resultado is None or resultado["tp"] is None:
            continue

        precio_entrada = resultado["precio_entrada"]
        sl = resultado["sl"]
        tp = resultado["tp"]
        riesgo = abs(precio_entrada - sl)

        if riesgo <= 0:
            continue

        resultado_operacion = simular_operacion(
            df, i, resultado["tipo"], precio_entrada, sl, tp,
            be_activo=BE_ACTIVO, be_porcentaje=BE_PORCENTAJE,
        )
        ratio_tp_real = abs(tp - precio_entrada) / riesgo  # cuánto R representa el TP macro en esta operación

        resultados.append({
            "indice": i,
            "tipo": resultado["tipo"],
            "precio_entrada": precio_entrada,
            "sl": sl,
            "tp": tp,
            "ratio_tp": ratio_tp_real,
            "resultado": resultado_operacion,
        })

        ultimo_indice_operado = i + 1  # evita solaparse con la vela siguiente inmediata

    mostrar_reporte(resultados)
    return resultados


def mostrar_reporte(resultados):
    if not resultados:
        print("No se generó ninguna señal con esta configuración. Prueba ajustar PIVOT_LEN o TOLERANCIA_PCT.")
        return

    total = len(resultados)
    ganadas = sum(1 for r in resultados if r["resultado"] == "gano")
    perdidas = sum(1 for r in resultados if r["resultado"] == "perdio")
    breakeven = sum(1 for r in resultados if r["resultado"] == "breakeven")
    sin_cerrar = sum(1 for r in resultados if r["resultado"] == "sin_cerrar")

    # Cada operación tiene su propio ratio TP/riesgo (el TP es % del movimiento
    # macro, así que no es fijo como antes) -> se suma el R real de cada una.
    # "breakeven" = se cerró en el SL, pero el SL ya estaba en la entrada, así
    # que no cuenta como -1R, cuenta como 0.
    r_total = sum(
        r["ratio_tp"] if r["resultado"] == "gano"
        else (-1.0 if r["resultado"] == "perdio" else 0.0)
        for r in resultados
    )

    # Drawdown simple sobre la curva de R acumulado
    curva = []
    acumulado = 0
    for r in resultados:
        if r["resultado"] == "gano":
            acumulado += r["ratio_tp"]
        elif r["resultado"] == "perdio":
            acumulado -= 1
        curva.append(acumulado)

    pico = curva[0] if curva else 0
    max_drawdown = 0
    for valor in curva:
        pico = max(pico, valor)
        max_drawdown = min(max_drawdown, valor - pico)

    print("=" * 50)
    print("REPORTE DE BACKTEST — Estructura de Mercado SpotPlus")
    print("=" * 50)
    print(f"Total de señales tomadas: {total}")
    print(f"Ganadas: {ganadas}  |  Perdidas: {perdidas}  |  Breakeven (BE): {breakeven}  |  Sin cerrar (fin de datos): {sin_cerrar}")
    if total - sin_cerrar > 0:
        winrate = ganadas / (total - sin_cerrar) * 100
        print(f"Winrate: {winrate:.1f}%  (breakeven no cuenta como ganada ni perdida)")
    if BE_ACTIVO:
        print(f"Breakeven activo: se protege al {BE_PORCENTAJE*100:.0f}% del camino hacia el TP")
    print(f"Resultado total: {r_total:.2f} R  (múltiplos de riesgo)")
    print(f"Drawdown máximo: {max_drawdown:.2f} R")
    print("=" * 50)

    # --- Traducción a dinero real + punto de equilibrio ---
    riesgo_dinero = BALANCE_CUENTA * RIESGO_PORCENTAJE
    profit_dinero = r_total * riesgo_dinero
    drawdown_dinero = max_drawdown * riesgo_dinero

    ratios_ganadoras = [r["ratio_tp"] for r in resultados if r["resultado"] == "gano"]
    ratio_promedio_ganadoras = sum(ratios_ganadoras) / len(ratios_ganadoras) if ratios_ganadoras else 0.0

    print("\n" + "=" * 50)
    print("DINERO REAL Y PUNTO DE EQUILIBRIO")
    print("=" * 50)
    print(f"Cuenta: ${BALANCE_CUENTA:,.2f}  |  Riesgo por operación: {RIESGO_PORCENTAJE*100:.1f}% = ${riesgo_dinero:,.2f}")
    print(f"Resultado total en dinero: ${profit_dinero:,.2f}")
    print(f"Drawdown máximo en dinero: ${drawdown_dinero:,.2f}")
    if ratio_promedio_ganadoras > 0:
        print(f"\nRatio promedio de tus ganadoras: {ratio_promedio_ganadoras:.2f}R")
        print(f"-> Cada operación ganadora cubre, en promedio, {ratio_promedio_ganadoras:.2f} operaciones")
        print(f"   perdedoras de 1R cada una (punto de equilibrio: {1/ratio_promedio_ganadoras*100:.1f}% de acierto necesario).")
        print(f"   Ejemplo con tu cuenta: 1 ganadora (${ratio_promedio_ganadoras*riesgo_dinero:,.2f}) cubre")
        print(f"   {ratio_promedio_ganadoras:.2f} perdedoras (${riesgo_dinero:,.2f} c/u).")
    print("=" * 50)
    print("\nÚltimas 10 operaciones:")
    for r in resultados[-10:]:
        print(f"  vela {r['indice']:>6} | {r['tipo']:<7} | entrada {r['precio_entrada']:.3f} "
              f"| sl {r['sl']:.3f} | tp {r['tp']:.3f} (R≈{r['ratio_tp']:.2f}) | resultado: {r['resultado']}")


if __name__ == "__main__":
    correr_backtest()