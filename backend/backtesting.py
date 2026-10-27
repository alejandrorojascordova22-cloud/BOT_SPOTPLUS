# ============================================================
# BACKTESTING
# ============================================================
# Corre la MISMA lógica de señales que usa el bot en vivo (ver ciclo_bot en
# servidor.py), vela por vela, sobre datos históricos de MT5. En cada paso
# solo se le pasa a la estrategia la ventana de velas HASTA ese punto —
# igual que en vivo, donde el bot nunca ve el futuro. Los indicadores
# (rsi, sma_*) se calculan con ventanas móviles causales (pandas_ta), así
# que precalcularlos sobre todo el histórico de una vez no introduce
# ninguna fuga de información del futuro.
#
# Simplificación consciente frente al bot en vivo: aquí solo se mantiene
# UNA posición abierta a la vez (se ignoran señales nuevas mientras hay
# una posición abierta, hasta que cierre por SL/TP). El bot en vivo no
# tiene ese límite y podría abrir varias si configuras SL/TP en 0 y el
# símbolo da señales seguidas. Para un backtest de riesgo controlado,
# una posición a la vez es el comportamiento que casi siempre quieres.
# ============================================================

from .indicadores import calcular_rsi, calcular_media_movil
from .estrategias import (
    evaluar_estrategia,
    evaluar_estrategia_texto,
    evaluar_estrategia_bloques,
    evaluar_codigo_libre,
    evaluar_estructura_dinamica,
)
from .tiempo import timestamps_epoch_segundos
import numpy as np

VENTANA_MINIMA = 210  # suficientes velas para que sma_200 ya tenga datos válidos

# Segundos por temporalidad (para saber cuándo CIERRA cada vela de un marco mayor
# y no dársela a la estrategia antes de tiempo).
TF_SEGUNDOS = {
    "M1": 60, "M3": 180, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H2": 7200, "H4": 14400, "D1": 86400, "W1": 604800, "MN1": 2592000,
}
# Igual que el bot en vivo (obtener_velas_varias_temporalidades -> 200 velas por marco).
VELAS_POR_TEMPORALIDAD = 200
# Opcional: config["velas_por_temporalidad_backtest"] (ej. 3000) amplía cuántas velas ve la
# estrategia por marco. Con 200, la ruptura (BOS) + retroceso de las zonas de temporalidades
# altas casi nunca cae dentro de la ventana de M1/M3/M5 y salen 0 señales.


def _preparar_temporalidades(dfs_tf, timeframe_entrada):
    """Ordena cada DataFrame de temporalidad y precalcula el instante de CIERRE de
    cada vela (epoch s) para recortarlo de forma causal en cada paso."""
    preparadas = {}
    for tf, d in (dfs_tf or {}).items():
        tf = str(tf).upper()
        if d is None or len(d) == 0 or tf == timeframe_entrada or tf not in TF_SEGUNDOS:
            continue
        d = d.sort_values("time").drop_duplicates(subset="time").reset_index(drop=True)
        cierres = np.array(timestamps_epoch_segundos(d["time"]), dtype="int64") + TF_SEGUNDOS[tf]
        preparadas[tf] = (d, cierres)
    return preparadas


def _calcular_senal(ventana, config, ventana_macro=None, velas_tf=None):
    """Misma prioridad que ciclo_bot en servidor.py: código libre > bloques
    visuales > texto libre > estrategia registrada en ESTRATEGIAS.

    Caso especial: si config['estrategia'] == 'estructura_dinamica', esto
    devuelve el DICT completo de la señal (tipo, sl, tp propios) en vez de
    solo "compra"/"venta"/None — simular_backtest lo detecta y usa ese
    sl/tp por operación en lugar de los puntos fijos del formulario.

    ventana_macro (opcional): recorte causal (solo hasta "ahora") del
    segundo CSV de temporalidad macro real, si se pasó uno a
    simular_backtest. Si es None, estructura_dinamica cae en la
    aproximación de un solo timeframe.
    """
    if config.get("estrategia") == "estructura_dinamica":
        return evaluar_estructura_dinamica(ventana, config.get("parametros", {}), df_macro=ventana_macro)
    if config.get("codigo_estrategia"):
        timeframe_entrada = str(config.get("timeframe_entrada", "M15")).upper()
        timeframe_macro = str(config.get("timeframe_macro", "H1")).upper()
        velas_por_temporalidad = {timeframe_entrada: ventana}
        if ventana_macro is not None and timeframe_macro != timeframe_entrada:
            velas_por_temporalidad[timeframe_macro] = ventana_macro
        if velas_tf:
            # Multi-temporalidad real (como en vivo): todos los marcos configurados.
            velas_por_temporalidad.update(velas_tf)
            velas_por_temporalidad[timeframe_entrada] = ventana.iloc[-int(config.get("velas_por_temporalidad_backtest") or VELAS_POR_TEMPORALIDAD):]
        return evaluar_codigo_libre(
            ventana,
            config["codigo_estrategia"],
            df_macro=ventana_macro,
            velas_por_temporalidad=velas_por_temporalidad,
            grupos_temporalidad=config.get("grupos_temporalidad"),
            roles_temporalidad=config.get("roles_temporalidad"),
            zonas_permitidas=config.get("zonas_permitidas"),
            parametros=config.get("parametros", {}),
        )
    if config.get("condiciones_compra") or config.get("condiciones_venta"):
        return evaluar_estrategia_bloques(
            ventana,
            config.get("condiciones_compra", []),
            config.get("operador_compra", "and"),
            config.get("condiciones_venta", []),
            config.get("operador_venta", "and"),
        )
    if config.get("condicion_compra_texto") or config.get("condicion_venta_texto"):
        return evaluar_estrategia_texto(
            ventana,
            config.get("condicion_compra_texto", ""),
            config.get("condicion_venta_texto", ""),
        )
    if config.get("estrategia"):
        return evaluar_estrategia(ventana, config["estrategia"], config.get("parametros", {}))
    return None


def simular_backtest(df, config, balance_inicial=10000.0, contrato=100.0, ventana_minima=VENTANA_MINIMA, df_macro=None, progreso_callback=None, dfs_tf=None):
    """
    df: DataFrame de velas históricas (columnas time, open, high, low, close),
        ordenadas de más antigua a más reciente. Esta es la temporalidad de
        ENTRADA (ej. M15 si vas a operar en M15).
    df_macro (opcional): segundo DataFrame, de una temporalidad PRINCIPAL
        real y MAYOR (ej. H1), usado SOLO por "estructura_dinamica" para
        medir el movimiento macro (el "ratio" del TP). Si no lo pasas, esa
        estrategia usa la aproximación de un solo timeframe (menos
        precisa). Debe traer columna "time" para poder recortarse de forma
        causal en cada paso (nunca usa velas macro que "todavía no habían
        cerrado" en ese punto del backtest).
    config: mismo dict que config_bot en servidor.py — usa lo que esté activo
            (código libre, bloques visuales, texto, o estrategia registrada),
            más volumen_operacion / sl_operacion / tp_operacion como riesgo.
    contrato: trade_contract_size del símbolo en MT5, para convertir puntos
              de precio a dinero real.
    dfs_tf (opcional): dict {"M15": df, "H1": df, "H4": df, ...} con el histórico de
              TODAS las temporalidades que usa la estrategia (temporalidades_escaneo).
              En cada vela se le entrega a la estrategia el recorte causal (solo velas
              ya cerradas, las últimas 200 por marco), igual que en vivo. Sin esto, una
              estrategia multi-temporalidad solo ve el marco de entrada y no opera.
    progreso_callback (opcional): función(procesado, total) llamada
              periódicamente durante el loop, para reportar avance (ej. un
              job en background que expone % de progreso a la interfaz).
    """
    df = calcular_rsi(df.copy())
    for periodo in (10, 20, 50, 100, 200):
        df = calcular_media_movil(df, periodo=periodo)

    total_velas = len(df)
    if total_velas <= ventana_minima + 1:
        return {
            "error": (
                f"No hay suficientes velas históricas para simular "
                f"(se necesitan al menos {ventana_minima + 1}, llegaron {total_velas}). "
                "Prueba con un rango de fechas más amplio."
            )
        }

    volumen = float(config.get("volumen_operacion", 0.01) or 0.01)
    sl_puntos = float(config.get("sl_operacion", 0.0) or 0.0)
    tp_puntos = float(config.get("tp_operacion", 0.0) or 0.0)

    # --- Breakeven ---
    # Si be_activo, cuando el precio avanza be_porcentaje (0-1) de la
    # distancia entrada->tp a favor, el SL se mueve a la entrada (0 riesgo).
    # El volumen NO cambia, solo se protege la operación. be_porcentaje=0.8
    # significa "cuando ya recorrió el 80% del camino hacia el TP".
    be_activo = bool(config.get("be_activo", False))
    be_porcentaje = float(config.get("be_porcentaje", 0.8) or 0.8)

    balance = balance_inicial
    equity_curve = []
    trades = []
    posicion = None  # {"tipo", "entrada", "sl", "tp", "volumen", "hora_apertura"}
    ultima_senal_ejecutada = None
    ids_ejecutados = set()  # señales con "id" propio (estructura): se deduplican por id, no por dirección
    evaluaciones_estrategia = 0
    senales_detectadas = 0
    oportunidades_detectadas = 0
    senales_ignoradas_misma_direccion = 0
    entradas_abiertas = 0

    # Si hay df_macro, lo dejamos ordenado y listo para recortarlo de forma
    # causal (searchsorted) en cada paso, sin recorrer todo el DataFrame
    # macro en cada vela.
    tiempos_macro = None
    if df_macro is not None and "time" in df_macro.columns:
        df_macro = df_macro.sort_values("time").reset_index(drop=True)
        tiempos_macro = df_macro["time"].values

    n_velas_tf = int(config.get("velas_por_temporalidad_backtest") or VELAS_POR_TEMPORALIDAD)
    tf_entrada_str = str(config.get("timeframe_entrada", "M15")).upper()
    tfs_preparadas = _preparar_temporalidades(dfs_tf, tf_entrada_str) if config.get("codigo_estrategia") else {}
    paso_entrada = TF_SEGUNDOS.get(tf_entrada_str, 300)
    tiempos_entrada = np.array(timestamps_epoch_segundos(df["time"]), dtype="int64")

    for i in range(ventana_minima, total_velas):
        if progreso_callback is not None and (i - ventana_minima) % 200 == 0:
            progreso_callback(i - ventana_minima, total_velas - ventana_minima)

        vela = df.iloc[i]
        ventana = df.iloc[: i + 1]

        ventana_macro = None
        if tiempos_macro is not None:
            # último índice macro cuyo "time" ya cerró antes/igual que esta vela
            corte = tiempos_macro.searchsorted(vela["time"], side="right")
            if corte > 0:
                ventana_macro = df_macro.iloc[:corte]

        velas_tf = None
        if tfs_preparadas:
            instante_cierre = int(tiempos_entrada[i]) + paso_entrada
            velas_tf = {}
            for tf, (d_tf, cierres_tf) in tfs_preparadas.items():
                corte_tf = int(cierres_tf.searchsorted(instante_cierre, side="right"))
                if corte_tf >= 3:
                    velas_tf[tf] = d_tf.iloc[max(0, corte_tf - n_velas_tf):corte_tf]

        # 1. Si hay posición abierta:
        #    1a. primero revisa si toca el umbral de breakeven y mueve el SL.
        #    1b. luego revisa si (con el SL ya actualizado) toca SL o TP.
        if posicion is not None:
            if be_activo and not posicion["be_aplicado"] and posicion["tp"]:
                distancia_tp = abs(posicion["tp"] - posicion["entrada"])
                umbral = distancia_tp * be_porcentaje
                if posicion["tipo"] == "compra":
                    if vela["high"] >= posicion["entrada"] + umbral:
                        posicion["sl"] = posicion["entrada"]
                        posicion["be_aplicado"] = True
                else:
                    if vela["low"] <= posicion["entrada"] - umbral:
                        posicion["sl"] = posicion["entrada"]
                        posicion["be_aplicado"] = True

            precio_cierre = None
            motivo_cierre = None
            if posicion["tipo"] == "compra":
                if posicion["sl"] and vela["low"] <= posicion["sl"]:
                    precio_cierre = posicion["sl"]
                    motivo_cierre = "be" if posicion["be_aplicado"] and precio_cierre == posicion["entrada"] else "sl"
                elif posicion["tp"] and vela["high"] >= posicion["tp"]:
                    precio_cierre = posicion["tp"]
                    motivo_cierre = "tp"
            else:
                if posicion["sl"] and vela["high"] >= posicion["sl"]:
                    precio_cierre = posicion["sl"]
                    motivo_cierre = "be" if posicion["be_aplicado"] and precio_cierre == posicion["entrada"] else "sl"
                elif posicion["tp"] and vela["low"] <= posicion["tp"]:
                    precio_cierre = posicion["tp"]
                    motivo_cierre = "tp"

            if precio_cierre is not None:
                direccion = 1 if posicion["tipo"] == "compra" else -1
                profit = (precio_cierre - posicion["entrada"]) * direccion * posicion["volumen"] * contrato
                balance += profit
                trades.append({
                    "tipo": posicion["tipo"],
                    "entrada": round(posicion["entrada"], 5),
                    "sl": round(posicion["sl"], 5),
                    "tp": round(posicion["tp"], 5),
                    "salida": round(precio_cierre, 5),
                    "volumen": posicion["volumen"],
                    "profit": round(profit, 2),
                    "hora_apertura": posicion["hora_apertura"],
                    "hora_cierre": str(vela["time"]),
                    "motivo_cierre": motivo_cierre,
                    "be_aplicado": posicion["be_aplicado"],
                })
                posicion = None

        # 2. Si no hay posición abierta, evalúa si hay una señal NUEVA para entrar
        #    (misma regla que el bot en vivo: solo actúa si la señal cambió).
        if posicion is None:
            senal = _calcular_senal(ventana, config, ventana_macro, velas_tf)
            evaluaciones_estrategia += 1
            if isinstance(senal, list):
                if senal:
                    senales_detectadas += 1
                    oportunidades_detectadas += len(senal)
            elif isinstance(senal, dict) and senal.get("tipo") in ("compra", "venta"):
                senales_detectadas += 1
                oportunidades_detectadas += 1
            elif senal in ("compra", "venta"):
                senales_detectadas += 1
                oportunidades_detectadas += 1

            # Caso "estructura_dinamica": la señal ya trae su propio sl/tp
            # (calculados por señal_estructura: sl detrás de la zona, tp
            # como % del movimiento macro). No se usan sl_puntos/tp_puntos.
            if isinstance(senal, list) and senal:
                # El backtest mantiene una sola posición abierta; representa
                # la pata corta si la estrategia devuelve una pareja.
                senal = next((op for op in senal if op.get("escala") == "corta"), senal[0])
            if isinstance(senal, dict):
                tipo_senal = senal.get("tipo")
                if tipo_senal in ("compra", "venta"):
                    id_senal = senal.get("id")
                    # Con id propio, solo se ignora el MISMO setup ya operado; una zona
                    # nueva en la misma dirección sí es otra operación. Sin id, se
                    # mantiene la regla antigua (ignorar misma dirección consecutiva).
                    repetida = (id_senal in ids_ejecutados) if id_senal is not None else (tipo_senal == ultima_senal_ejecutada)
                    if repetida:
                        senales_ignoradas_misma_direccion += 1
                    else:
                        posicion = {
                            "tipo": tipo_senal,
                            "entrada": float(senal["precio_entrada"]),
                            "sl": float(senal["sl"]) if senal.get("sl") else 0.0,
                            "tp": float(senal["tp"]) if senal.get("tp") else 0.0,
                            "volumen": volumen,
                            "hora_apertura": str(vela["time"]),
                            "be_aplicado": False,
                        }
                        ultima_senal_ejecutada = tipo_senal
                        if id_senal is not None:
                            ids_ejecutados.add(id_senal)
                        entradas_abiertas += 1
            elif senal in ("compra", "venta") and senal != ultima_senal_ejecutada:
                precio_entrada = float(vela["close"])
                if senal == "compra":
                    sl = precio_entrada - sl_puntos if sl_puntos else 0.0
                    tp = precio_entrada + tp_puntos if tp_puntos else 0.0
                else:
                    sl = precio_entrada + sl_puntos if sl_puntos else 0.0
                    tp = precio_entrada - tp_puntos if tp_puntos else 0.0
                posicion = {
                    "tipo": senal,
                    "entrada": precio_entrada,
                    "sl": sl,
                    "tp": tp,
                    "volumen": volumen,
                    "hora_apertura": str(vela["time"]),
                    "be_aplicado": False,
                }
                ultima_senal_ejecutada = senal
                entradas_abiertas += 1
            elif senal in ("compra", "venta"):
                senales_ignoradas_misma_direccion += 1

        equity_curve.append({"tiempo": str(vela["time"]), "balance": round(balance, 2)})

    if progreso_callback is not None:
        progreso_callback(total_velas - ventana_minima, total_velas - ventana_minima)

    # Si quedó una posición abierta al terminar el rango, se valora "a mercado"
    # (al último cierre) solo para informar, sin contarla como operación cerrada.
    profit_flotante = 0.0
    if posicion is not None:
        ultimo_precio = float(df.iloc[-1]["close"])
        direccion = 1 if posicion["tipo"] == "compra" else -1
        profit_flotante = (ultimo_precio - posicion["entrada"]) * direccion * posicion["volumen"] * contrato

    ganadoras = [t for t in trades if t["profit"] > 0]
    perdedoras = [t for t in trades if t["profit"] <= 0]
    profit_total = sum(t["profit"] for t in trades)

    pico = balance_inicial
    max_drawdown = 0.0
    for punto in equity_curve:
        pico = max(pico, punto["balance"])
        max_drawdown = max(max_drawdown, pico - punto["balance"])

    # El replay incluye también el warm-up para que la estructura previa a la
    # primera operación sea visible. El índice indica dónde empieza la
    # reproducción efectiva del backtest.
    df_replay = df.copy()
    tiempos_epoch = timestamps_epoch_segundos(df_replay["time"])
    velas_replay = [
        {
            "time": t,
            "open": round(float(o), 5),
            "high": round(float(h), 5),
            "low": round(float(l), 5),
            "close": round(float(c), 5),
        }
        for t, o, h, l, c in zip(
            tiempos_epoch,
            df_replay["open"], df_replay["high"], df_replay["low"], df_replay["close"],
        )
    ]

    timeframe_entrada = str(config.get("timeframe_entrada", "M15")).upper()
    if config.get("codigo_estrategia") and config.get("codigo_usa_multi_temporalidad"):
        timeframes_configurados = [
            str(tf).upper() for tf in (config.get("temporalidades_escaneo") or [])
        ]
    else:
        timeframes_configurados = [timeframe_entrada]
        if config.get("estrategia") == "estructura_dinamica":
            timeframe_macro = str(config.get("timeframe_macro", "H1")).upper()
            if df_macro is not None:
                timeframes_configurados.append(timeframe_macro)
    if timeframe_entrada not in timeframes_configurados:
        timeframes_configurados.insert(0, timeframe_entrada)

    timeframes_recibidos = {timeframe_entrada, *tfs_preparadas.keys()}
    if config.get("estrategia") == "estructura_dinamica" and df_macro is not None:
        timeframes_recibidos.add(str(config.get("timeframe_macro", "H1")).upper())
    timeframes_faltantes = [
        tf for tf in timeframes_configurados if tf not in timeframes_recibidos
    ]

    if config.get("estrategia") == "estructura_dinamica":
        estrategia_activa = config.get("estrategia_biblioteca_activa") or "estructura_dinamica"
        tipo_estrategia = "catálogo"
    elif config.get("codigo_estrategia"):
        estrategia_activa = config.get("estrategia_biblioteca_activa") or "Código libre"
        tipo_estrategia = "código libre"
    elif config.get("condiciones_compra") or config.get("condiciones_venta"):
        estrategia_activa = config.get("estrategia_biblioteca_activa") or "Bloques visuales"
        tipo_estrategia = "bloques visuales"
    elif config.get("condicion_compra_texto") or config.get("condicion_venta_texto"):
        estrategia_activa = config.get("estrategia_biblioteca_activa") or "Condiciones de texto"
        tipo_estrategia = "texto"
    elif config.get("estrategia"):
        estrategia_activa = config.get("estrategia_biblioteca_activa") or config["estrategia"]
        tipo_estrategia = "catálogo"
    else:
        estrategia_activa = "Sin estrategia"
        tipo_estrategia = "ninguna"

    return {
        "balance_inicial": balance_inicial,
        "balance_final": round(balance, 2),
        "profit_total": round(profit_total, 2),
        "profit_flotante_al_final": round(profit_flotante, 2),
        "total_operaciones": len(trades),
        "ganadoras": len(ganadoras),
        "perdedoras": len(perdedoras),
        "tasa_acierto": round(len(ganadoras) / len(trades) * 100, 1) if trades else 0.0,
        "max_drawdown": round(max_drawdown, 2),
        "posicion_abierta_al_final": posicion,
        "operaciones": trades,
        "curva_equity": equity_curve,
        "velas": velas_replay,
        "indice_inicio_replay": ventana_minima,
        "diagnostico": {
            "estrategia_activa": estrategia_activa,
            "tipo_estrategia": tipo_estrategia,
            "timeframes_configurados": timeframes_configurados,
            "timeframes_recibidos": [
                tf for tf in timeframes_configurados if tf in timeframes_recibidos
            ] + sorted(timeframes_recibidos - set(timeframes_configurados)),
            "timeframes_faltantes": timeframes_faltantes,
            "evaluaciones_estrategia": evaluaciones_estrategia,
            "senales_detectadas": senales_detectadas,
            "oportunidades_detectadas": oportunidades_detectadas,
            "entradas_ejecutadas": entradas_abiertas,
            "senales_ignoradas_misma_direccion": senales_ignoradas_misma_direccion,
        },
    }