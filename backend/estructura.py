import pandas as pd

# ============================================================
# ESTRUCTURA DE MERCADO — SpotPlus
# ============================================================
# Detecta pivotes (picos/valles), los clasifica en HH/HL/LH/LL,
# encuentra zonas de espera (EV: HL para compra, LH para venta),
# EXIGE una confirmación de rompimiento de estructura (BOS) antes
# de dar la señal, y calcula el TP como un % del movimiento MACRO
# (el impulso grande de fondo), no del pullback donde se entra.
#
# Flujo completo de una señal de compra (venta es el espejo):
#   1. El precio forma un pico (P1).
#   2. Hace un pullback y forma un valle más alto que el anterior -> HL.
#      Ese HL es la "zona de espera" (EV).
#   3. Mientras el precio esté ahí, TODAVÍA NO hay señal.
#   4. La señal se confirma solo cuando el precio, después de tocar el
#      HL, rompe de nuevo por ARRIBA del pico P1 (ese rompimiento es
#      el BOS = Break of Structure). Eso confirma que el mercado
#      reanudó la tendencia y no fue una reversión.
#   5. El TP se proyecta como un % (configurable) de la distancia del
#      movimiento macro más reciente (A -> B, en una temporalidad/
#      sensibilidad más "grande" que la de las entradas), medido
#      desde el precio de entrada.
# ============================================================


def detectar_pivotes(df, pivot_len=5):
    """
    Recorre todo el DataFrame y devuelve una lista de pivotes confirmados.
    Cada pivote es un dict: {"indice": int, "precio": float, "tipo": "high"/"low"}

    pivot_len = cuántas velas a cada lado debe superar el pico/valle.
    Guía (igual que en el indicador):
        micro (1m-5m): 2-3
        intermedia (15m-1h): 5-7
        macro (4h-Diario): 8-12
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    pivotes = []

    for i in range(pivot_len, n - pivot_len):
        ventana_high = highs[i - pivot_len : i + pivot_len + 1]
        ventana_low = lows[i - pivot_len : i + pivot_len + 1]

        if highs[i] == ventana_high.max() and (ventana_high == ventana_high.max()).sum() == 1:
            pivotes.append({"indice": i, "precio": highs[i], "tipo": "high"})

        if lows[i] == ventana_low.min() and (ventana_low == ventana_low.min()).sum() == 1:
            pivotes.append({"indice": i, "precio": lows[i], "tipo": "low"})

    pivotes.sort(key=lambda p: p["indice"])
    return pivotes


def clasificar_pivotes(pivotes):
    """
    Recorre los pivotes en orden y le agrega a cada uno su clasificación:
    "HH", "HL", "LH", "LL", o "primero" (si es el primer pivote de su tipo).
    Devuelve la misma lista, con el campo "clase" agregado.
    """
    ultimo_high = None
    ultimo_low = None

    for p in pivotes:
        if p["tipo"] == "high":
            if ultimo_high is None:
                p["clase"] = "primero"
            elif p["precio"] > ultimo_high:
                p["clase"] = "HH"
            else:
                p["clase"] = "LH"
            ultimo_high = p["precio"]
        else:
            if ultimo_low is None:
                p["clase"] = "primero"
            elif p["precio"] > ultimo_low:
                p["clase"] = "HL"
            else:
                p["clase"] = "LL"
            ultimo_low = p["precio"]

    return pivotes


def sesgo_direccion(df, pivot_len=5):
    """Devuelve ``alcista``, ``bajista`` o ``None`` según los últimos pivotes.

    El sesgo solo queda confirmado cuando hay estructura en ambos lados:
    HH+HL para alcista o LH+LL para bajista. Así una sola vela que atraviesa
    un nivel no cambia por sí misma la dirección macro.
    """
    if df is None or len(df) < pivot_len * 2 + 1:
        return None
    pivotes = clasificar_pivotes(detectar_pivotes(df, pivot_len))
    ultimo_high = next((p for p in reversed(pivotes) if p["tipo"] == "high"), None)
    ultimo_low = next((p for p in reversed(pivotes) if p["tipo"] == "low"), None)
    if ultimo_high is None or ultimo_low is None:
        return None
    if ultimo_high.get("clase") == "HH" and ultimo_low.get("clase") == "HL":
        return "alcista"
    if ultimo_high.get("clase") == "LH" and ultimo_low.get("clase") == "LL":
        return "bajista"
    return None


def alineacion_con_macro(df, tipo_operacion, pivot_len_macro=15):
    """Evalua si el sesgo de una temporalidad mayor favorece la operacion."""
    sesgo = sesgo_direccion(df, pivot_len_macro)
    direccion_esperada = "alcista" if tipo_operacion == "compra" else "bajista"
    return {
        "sesgo": sesgo,
        "alineada": sesgo == direccion_esperada if sesgo is not None else None,
    }


def zonas_espera(pivotes):
    """
    A partir de pivotes ya clasificados, devuelve las zonas EV:
    - Un HL es zona de espera para COMPRA
    - Un LH es zona de espera para VENTA

    Devuelve una lista de dicts:
    {"indice": int, "precio": float, "tipo": "compra"/"venta"}
    """
    zonas = []
    for p in pivotes:
        if p["tipo"] == "low" and p.get("clase") == "HL":
            zonas.append({"indice": p["indice"], "precio": p["precio"], "tipo": "compra"})
        elif p["tipo"] == "high" and p.get("clase") == "LH":
            zonas.append({"indice": p["indice"], "precio": p["precio"], "tipo": "venta"})
    return zonas


def fibonacci_del_tramo(precio_inicio, precio_fin):
    """
    Calcula los niveles de retroceso de Fibonacci de un tramo de impulso.
    precio_inicio = donde empezó el impulso (valle si es alcista, pico si es bajista)
    precio_fin = donde terminó el impulso

    Devuelve un dict con los niveles completos: 0, 0.27, 0.38, 0.5, 0.618,
    0.786, 0.886, 1, y las extensiones 1.1, 1.27, 1.38 (zonas típicas de
    manipulación/liquidez más allá del 100% del impulso). NO se usa FVG
    como confluencia en esta estrategia.
    """
    rango = precio_fin - precio_inicio
    niveles = {}
    for pct in [0, 0.27, 0.38, 0.5, 0.618, 0.786, 0.886, 1, 1.1, 1.27, 1.38]:
        niveles[pct] = precio_fin - rango * pct
    return niveles


# Extensiones usadas como zonas de liquidez después de una barrida.
# Son configurables al llamar a ``zona_extension_liquidez``.
EXTENSIONES_LIQUIDEZ_DEFECTO = (1.1, 1.27, 1.38)


def zona_extension_liquidez(df, pivot_len=5, extensiones=None, tolerancia_pct=0.15):
    """Detecta una extensión de Fibonacci que fue barrida y luego rechazada.

    En un impulso alcista, una mecha por encima de la extensión y un cierre
    de vuelta debajo de ella crean una zona de liquidez para venta. En un
    impulso bajista ocurre lo contrario y la zona queda para compra.
    Devuelve una zona en espera; todavía exige BOS en ``señal_estructura``.
    """
    extensiones = tuple(extensiones or EXTENSIONES_LIQUIDEZ_DEFECTO)
    movimiento = movimiento_macro(df, pivot_len)
    if movimiento is None:
        return None

    inicio = movimiento["precio_inicio"]
    fin = movimiento["precio_fin"]
    rango = abs(fin - inicio)
    if rango <= 0 or len(df) < 2:
        return None

    actual = df.iloc[-1]
    anterior = df.iloc[-2]
    es_alcista = movimiento["tipo"] == "alcista"
    for extension in extensiones:
        if extension < 1:
            continue
        nivel = fin + rango * (extension - 1) if es_alcista else fin - rango * (extension - 1)
        tolerancia = abs(nivel) * tolerancia_pct / 100
        if es_alcista:
            barrida = anterior["high"] >= nivel - tolerancia and actual["close"] < nivel
            tipo = "venta"
        else:
            barrida = anterior["low"] <= nivel + tolerancia and actual["close"] > nivel
            tipo = "compra"
        if barrida:
            return {
                "precio": nivel,
                "tipo": tipo,
                "bloque": "liquidez_extension",
                "fibo_pct": extension,
                "liquidez_barrida": True,
            }
    return None


def clasificar_bloque(pivotes, idx_zona):
    """
    Asigna a la zona de espera uno de los 4 escenarios de SpotPlus:
      - "flip": el pivote de tendencia justo antes de esta zona fue un
        rompimiento de estructura (BOS) -> es el primer retroceso de la
        tendencia nueva, se vuelve soporte/resistencia (zona Flip).
      - "ev": 1er o 2do retroceso normal desde el último Flip (zona de
        evaluación).
      - "obc": 3er retroceso en adelante desde el último Flip (varios
        impulsos seguidos, zona/bloque de continuación).
    ("fractal" no pasa por aquí: se calcula aparte, solo con fibo).
    """
    conteo_desde_flip = 0
    bloque_zona = "ev"
    for i, p in enumerate(pivotes):
        es_zona = (p["tipo"] == "low" and p.get("clase") == "HL") or \
                  (p["tipo"] == "high" and p.get("clase") == "LH")
        if not es_zona:
            continue

        # "Flip": justo antes de esta zona hubo un pivote LL (para zonas de
        # compra) o HH (para zonas de venta) EN EL SENTIDO CONTRARIO a la
        # zona -> significa que la tendencia venía rota y este es el primer
        # retroceso de la tendencia nueva. Se busca el pivote de tendencia
        # (no de espera) más reciente antes de esta zona.
        candidatos_tendencia = [q for q in pivotes[:i] if q.get("clase") in ("HH", "LL")]
        es_flip = False
        if candidatos_tendencia:
            ultimo_tendencia = candidatos_tendencia[-1]
            if p["tipo"] == "low" and ultimo_tendencia["clase"] == "LL":
                es_flip = True
            elif p["tipo"] == "high" and ultimo_tendencia["clase"] == "HH":
                es_flip = True

        if es_flip:
            conteo_desde_flip = 1
            bloque = "flip"
        else:
            conteo_desde_flip += 1
            bloque = "ev" if conteo_desde_flip <= 2 else "obc"

        if i == idx_zona:
            return bloque
    return bloque_zona


def zona_fractal(df, pivot_len_macro=15, tolerancia_pct=0.15, df_macro=None):
    """
    Zona FRACTAL (antes llamada "autonomy" en el código — es la misma zona,
    solo se corrige el nombre para que coincida con la metodología SpotPlus:
    Flip, Fractal, Evaluación, Continuación son las 4 zonas de espera).

    No depende de que haya un pivote HL/LH exacto ahí. Se calcula directo
    sobre los niveles de Fibonacci completos del último impulso MACRO
    (A->B). Si el precio actual está tocando cualquiera de esos niveles,
    es una zona Fractal válida por sí sola (puede dar entrada aunque la
    temporalidad de entrada no muestre un pivote clasificado justo ahí).
    """
    movimiento = movimiento_macro(df, pivot_len_macro, df_macro=df_macro)
    if movimiento is None:
        return None

    niveles = fibonacci_del_tramo(movimiento["precio_inicio"], movimiento["precio_fin"])
    precio_actual = float(df["close"].iloc[-1])
    tipo = "compra" if movimiento["tipo"] == "bajista" else "venta"

    for pct in (0.27, 0.38, 0.5):
        precio_nivel = niveles[pct]
        tolerancia = precio_nivel * (tolerancia_pct / 100)
        if abs(precio_actual - precio_nivel) <= tolerancia:
            return {"precio": precio_nivel, "tipo": tipo, "bloque": "fractal", "fibo_pct": pct}
    return None


def zona_fractal_2(df, pivot_len_macro=15, tolerancia_pct=0.15, df_macro=None, extensiones=None):
    """Detecta la franja de continuación 110–138% si los pools externos siguen sin barrer."""
    movimiento = movimiento_macro(df, pivot_len_macro, df_macro=df_macro)
    if movimiento is None:
        return None

    rango = abs(movimiento["precio_fin"] - movimiento["precio_inicio"])
    if rango <= 0:
        return None

    fuente_pivotes = df_macro if df_macro is not None else df
    pivotes_fractal2 = clasificar_pivotes(detectar_pivotes(fuente_pivotes, pivot_len_macro))
    pools = detectar_liquidez_externa(pivotes_fractal2)
    if any(pool["barrida"] for pool in pools):
        return None

    es_alcista = movimiento["tipo"] == "alcista"
    porcentajes = tuple(extensiones or (1.1, 1.27, 1.38))
    if not porcentajes or any(nivel < 1 for nivel in porcentajes):
        return None
    niveles = [
        movimiento["precio_fin"] + rango * (nivel - 1) if es_alcista
        else movimiento["precio_fin"] - rango * (nivel - 1)
        for nivel in porcentajes
    ]
    inferior, superior = min(niveles), max(niveles)
    precio_actual = float(df["close"].iloc[-1])
    tolerancia = abs(precio_actual) * tolerancia_pct / 100
    if inferior - tolerancia <= precio_actual <= superior + tolerancia:
        indice_referencia = min(movimiento["indice_fin"] + 1, len(fuente_pivotes) - 1)
        indice_confirmacion = min(
            movimiento["indice_fin"] + pivot_len_macro,
            len(fuente_pivotes) - 1,
        )
        return {
            "precio": min(niveles, key=lambda nivel: abs(nivel - precio_actual)),
            "tipo": "compra" if es_alcista else "venta",
            "bloque": "fractal2",
            "fibo_pct": porcentajes[min(range(len(niveles)), key=lambda i: abs(niveles[i] - precio_actual))],
            "limite_inferior": inferior,
            "limite_superior": superior,
            "indice": indice_referencia,
            "hora_confirmacion": (
                fuente_pivotes["time"].iloc[indice_confirmacion]
                if "time" in fuente_pivotes.columns else None
            ),
        }
    return None


# Alias de compatibilidad hacia atrás (por si algo externo aún llama al
# nombre viejo "zona_autonomy" o al bloque "autonomy").
zona_autonomy = zona_fractal


def _pivote_anterior_de_tipo(pivotes, indice_limite, tipo):
    """Busca el pivote más reciente de 'tipo' (high/low) con índice < indice_limite."""
    candidatos = [p for p in pivotes if p["indice"] < indice_limite and p["tipo"] == tipo]
    return candidatos[-1] if candidatos else None


def confirmar_bos(df, pivotes, zona):
    """
    Confirma el rompimiento de estructura (BOS) a favor de la zona de espera.

    - Si la zona es de COMPRA (viene de un HL): busca el pico (high) que
      quedó justo ANTES de ese HL (el que originó el pullback) y confirma
      BOS si el precio actual ya cerró por ENCIMA de ese pico.
    - Si la zona es de VENTA (viene de un LH): busca el valle (low) que
      quedó justo ANTES de ese LH y confirma BOS si el precio actual ya
      cerró por DEBAJO de ese valle.

    Devuelve el pivote de referencia usado (para poder ubicar el SL) y si
    el BOS quedó confirmado, o None si no hay pivote de referencia previo.
    """
    precio_actual = float(df["close"].iloc[-1])

    if zona["tipo"] == "compra":
        referencia = _pivote_anterior_de_tipo(pivotes, zona["indice"], "high")
        if referencia is None:
            return None
        confirmado = precio_actual > referencia["precio"]
    else:
        referencia = _pivote_anterior_de_tipo(pivotes, zona["indice"], "low")
        if referencia is None:
            return None
        confirmado = precio_actual < referencia["precio"]

    return {"referencia": referencia, "confirmado": confirmado, "precio_actual": precio_actual}


def movimiento_macro(df, pivot_len_macro=15, df_macro=None, hasta_tiempo=None):
    """
    Identifica el último tramo de impulso MACRO (A -> B).

    Hay dos formas de usar esto:

    1) MULTI-TIMEFRAME REAL (recomendado): pasas 'df_macro', que es un
       DataFrame de velas de una temporalidad PRINCIPAL/mayor de verdad
       (ej. H1), separado del 'df' de entradas (ej. M15). El movimiento
       macro se mide sobre esas velas H1 reales, no sobre una versión
       "menos sensible" del mismo M15.
       'hasta_tiempo' (opcional, un pd.Timestamp) recorta df_macro para
       no usar velas H1 que en la realidad todavía no habían cerrado en
       el momento de la vela de entrada (evita fuga de información en
       backtest).

    2) APROXIMACIÓN de un solo timeframe (compatibilidad hacia atrás,
       para cuando solo tienes un CSV): si no se pasa 'df_macro', se usa
       el mismo 'df' con un pivot_len_macro más grande. Es menos preciso
       porque no es una temporalidad real distinta, solo "menos ruido".

    Devuelve:
    {"precio_inicio": A, "precio_fin": B, "indice_inicio", "indice_fin", "tipo": "alcista"/"bajista"}
    o None si todavía no hay suficientes pivotes macro.
    """
    fuente = df_macro if df_macro is not None else df
    if hasta_tiempo is not None and "time" in fuente.columns:
        fuente = fuente[fuente["time"] <= hasta_tiempo]

    pivotes_macro = detectar_pivotes(fuente, pivot_len_macro)
    if len(pivotes_macro) < 2:
        return None

    p_inicio, p_fin = pivotes_macro[-2], pivotes_macro[-1]
    return {
        "precio_inicio": p_inicio["precio"],
        "precio_fin": p_fin["precio"],
        "indice_inicio": p_inicio["indice"],
        "indice_fin": p_fin["indice"],
        "tipo": "alcista" if p_fin["precio"] > p_inicio["precio"] else "bajista",
    }


def calcular_tp_desde_macro(precio_entrada, movimiento, tipo_operacion, porcentaje_tp=0.8):
    """
    TP = precio_entrada +/- (distancia del movimiento macro A->B) * porcentaje_tp
    porcentaje_tp = 0.8 significa "proyectar el 80% del tamaño del impulso macro".
    """
    if movimiento is None:
        return None
    distancia = abs(movimiento["precio_fin"] - movimiento["precio_inicio"])
    if tipo_operacion == "compra":
        return precio_entrada + distancia * porcentaje_tp
    return precio_entrada - distancia * porcentaje_tp


def ultima_zona_activa(df, pivot_len=5, tolerancia_pct=0.15):
    """
    (Se mantiene igual que antes, para no romper lo que ya usa el
    constructor visual de bloques: compat hacia atrás.)

    Detecta la estructura reciente y dice si el precio ACTUAL está
    tocando una zona de espera (EV) válida, con confluencia opcional
    de Fibonacci. NO exige BOS — es solo "toque de zona". Para la señal
    completa con confirmación, usa señal_estructura().
    """
    pivotes = detectar_pivotes(df, pivot_len)
    if len(pivotes) < 3:
        return None

    pivotes = clasificar_pivotes(pivotes)
    zonas = zonas_espera(pivotes)
    if not zonas:
        return None

    precio_actual = df["close"].iloc[-1]
    zona = zonas[-1]  # la más reciente

    tolerancia = zona["precio"] * (tolerancia_pct / 100)
    if abs(precio_actual - zona["precio"]) > tolerancia:
        return None

    idx_zona = pivotes.index(next(p for p in pivotes if p["indice"] == zona["indice"]))
    fibo_pct = None
    if idx_zona > 0:
        pivote_anterior = pivotes[idx_zona - 1]
        niveles = fibonacci_del_tramo(pivote_anterior["precio"], pivotes[idx_zona]["precio"])
        distancias = {pct: abs(precio - zona["precio"]) for pct, precio in niveles.items()}
        fibo_pct = min(distancias, key=distancias.get)

    return {"tipo": zona["tipo"], "zona_precio": zona["precio"], "fibo_pct": fibo_pct}


def estado_actual_estructura(df, pivot_len=5, pivot_len_macro=15, tolerancia_pct=0.15,
                              porcentaje_tp=0.8, sl_buffer_pct=0.1, max_velas_desde_zona=None,
                              df_macro=None, minimo_ratio_tp=None):
    """
    Devuelve el ESTADO de la estructura ahora mismo, para mostrar en la
    interfaz (no para operar). Tres posibilidades:

      1. {"estado": "sin_zona"}
         No hay ninguna zona de espera activa cerca del precio actual.

      2. {"estado": "esperando_confirmacion", "tipo": ..., "bloque": ...,
          "zona_precio": ...}
         El precio está TOCANDO una zona de espera (Flip/EV/OBC), pero
         TODAVÍA NO rompe el pivote opuesto a favor (falta el BOS).
         Aquí NO se ha mandado ninguna orden — es solo información.

      3. {"estado": "confirmada", ...todo lo de señal_estructura()...}
         Ya hubo rompimiento a favor (BOS): esta es la señal que el bot
         YA EJECUTÓ como orden real (o ejecutará en el siguiente ciclo).
    """
    confirmada = señal_estructura(
        df, pivot_len=pivot_len, pivot_len_macro=pivot_len_macro,
        porcentaje_tp=porcentaje_tp, sl_buffer_pct=sl_buffer_pct,
        max_velas_desde_zona=max_velas_desde_zona, df_macro=df_macro,
        minimo_ratio_tp=minimo_ratio_tp,
    )
    if confirmada is not None:
        return {"estado": "confirmada", **confirmada}

    tocando = ultima_zona_activa(df, pivot_len=pivot_len, tolerancia_pct=tolerancia_pct)
    if tocando is not None:
        return {"estado": "esperando_confirmacion", **tocando}

    return {"estado": "sin_zona"}


def señal_estructura(
    df,
    pivot_len=5,
    pivot_len_macro=15,
    porcentaje_tp=0.8,
    sl_buffer_pct=0.1,
    max_velas_desde_zona=None,
    df_macro=None,
    minimo_ratio_tp=None,
    extensiones_liquidez=None,
    modo_liquidez=None,
    tolerancia_pct_liquidez=0.15,
):
    """
    Señal COMPLETA y lista para operar. Combina todo lo definido:

      1. Detecta la última zona de espera (HL para compra / LH para venta).
      2. Exige confirmación por rompimiento de estructura (BOS): el precio
         debe haber roto ya el pico/valle anterior a la zona, a favor de
         la tendencia. Sin BOS confirmado -> no hay señal (devuelve None).
      3. Calcula el SL justo detrás de la zona de espera (con un pequeño
         buffer en %, para no quedar exactamente pegado al pivote).
      4. Calcula el TP como un % (porcentaje_tp) de la distancia del
         movimiento macro más reciente (A->B), detectado con
         pivot_len_macro (una sensibilidad más "grande"/menos ruidosa
         que pivot_len, que es la de las entradas).

    max_velas_desde_zona (opcional): si se define, invalida la señal si
    la zona de espera quedó formada hace más velas que este número (evita
    disparar BOS sobre estructuras muy viejas).

    df_macro (opcional): velas de la temporalidad PRINCIPAL real (ej. H1)
    para medir el movimiento macro de verdad, separada de 'df' (la
    temporalidad de entrada/confirmación, ej. M15). Si no se pasa, se usa
    la aproximación de un solo timeframe (ver movimiento_macro).

    minimo_ratio_tp (opcional): filtro de calidad. A veces el movimiento
    macro ya está "agotado" (el precio ya recorrió casi todo el impulso de
    fondo) y el TP calculado queda casi del mismo tamaño que el riesgo
    (ej. 1:0.4) — otras veces el impulso todavía tiene mucho recorrido y
    da un TP grande (ej. 1:3.5). Si se define este valor (ej. 1.5), la
    señal se descarta (devuelve None) cuando el ratio TP/riesgo real de
    esta operación queda por debajo de ese mínimo, en vez de tomarla igual.

    modo_liquidez / tolerancia_pct_liquidez (opcional): cómo usar la
    liquidez externa (equal highs/lows) para filtrar Flip/EV/OBC/Fractal.
    Fractal 2 usa extensiones 110–138% si los pools externos siguen sin
    barrer. Una extensión con liquidez barrida se muestra como contexto,
    pero no es una zona de entrada.

    Devuelve None si no hay señal confirmada, o un dict:
    {
      "tipo": "compra"/"venta",
      "precio_entrada": float,
      "sl": float,
      "tp": float o None (si aún no hay suficientes pivotes macro),
      "zona_precio": float,          # el HL/LH que originó la señal
      "pivote_referencia": float,    # el pico/valle roto (BOS)
      "movimiento_macro": dict o None,
    }
    """
    pivotes = detectar_pivotes(df, pivot_len)
    if len(pivotes) < 4:
        return None

    pivotes = clasificar_pivotes(pivotes)
    zonas = zonas_espera(pivotes)

    hasta_tiempo = df["time"].iloc[-1] if df_macro is not None and "time" in df.columns else None
    tipo = None
    bloque = None
    zona_precio = None
    precio_entrada = None
    sl = None

    if zonas:
        zona = zonas[-1]
        valido_por_edad = True
        if max_velas_desde_zona is not None:
            velas_transcurridas = (len(df) - 1) - zona["indice"]
            valido_por_edad = velas_transcurridas <= max_velas_desde_zona

        bos = confirmar_bos(df, pivotes, zona) if valido_por_edad else None
        if bos is not None and bos["confirmado"] and filtrar_por_liquidez(
            zona["tipo"], zona["precio"], pivotes, modo_liquidez, tolerancia_pct_liquidez
        ):
            idx_zona = pivotes.index(next(p for p in pivotes if p["indice"] == zona["indice"]))
            tipo = zona["tipo"]
            bloque = clasificar_bloque(pivotes, idx_zona)
            zona_precio = zona["precio"]
            precio_entrada = bos["precio_actual"]
            sl = zona_precio * (1 - sl_buffer_pct / 100) if tipo == "compra" else zona_precio * (1 + sl_buffer_pct / 100)

    # Si no hay señal estructural (Flip/EV/OBC) confirmada, probar Autonomy:
    # zona basada solo en Fibonacci del movimiento macro, independiente de
    # que haya un pivote HL/LH exacto ahí. También exige rompimiento a favor
    # (mismo criterio de confirmación, usando el pivote de tendencia previo).
    if tipo is None:
        auto = zona_fractal(df, pivot_len_macro, tolerancia_pct=0.15, df_macro=df_macro)
        if auto is not None:
            precio_actual = float(df["close"].iloc[-1])
            # confirmación: mismo criterio de "rompimiento a favor", usando
            # el pivote de tendencia (HH/LL) más reciente en la temporalidad
            # de entrada como referencia a romper.
            referencia = [p for p in pivotes if p.get("clase") in ("HH", "LL")]
            confirmado_auto = False
            if referencia:
                ref = referencia[-1]
                confirmado_auto = precio_actual > ref["precio"] if auto["tipo"] == "compra" else precio_actual < ref["precio"]
            if confirmado_auto and filtrar_por_liquidez(
                auto["tipo"], auto["precio"], pivotes, modo_liquidez, tolerancia_pct_liquidez
            ):
                tipo = auto["tipo"]
                bloque = "fractal"
                zona_precio = auto["precio"]
                precio_entrada = precio_actual
                sl = zona_precio * (1 - sl_buffer_pct / 100) if tipo == "compra" else zona_precio * (1 + sl_buffer_pct / 100)

    if tipo is None:
        fractal2 = zona_fractal_2(
            df,
            pivot_len_macro,
            tolerancia_pct=0.15,
            df_macro=df_macro,
            extensiones=extensiones_liquidez,
        )
        if fractal2 is not None:
            referencia = next(
                (p for p in reversed(pivotes) if p["tipo"] == ("low" if fractal2["tipo"] == "venta" else "high")),
                None,
            )
            precio_actual = float(df["close"].iloc[-1])
            confirmado = referencia is not None and (
                precio_actual < referencia["precio"] if fractal2["tipo"] == "venta"
                else precio_actual > referencia["precio"]
            )
            if confirmado:
                tipo = fractal2["tipo"]
                bloque = "fractal2"
                zona_precio = fractal2["precio"]
                precio_entrada = precio_actual
                sl = zona_precio * (1 - sl_buffer_pct / 100) if tipo == "compra" else zona_precio * (1 + sl_buffer_pct / 100)

    if tipo is None:
        return None

    # El TP se mide sobre el tramo de la MISMA temporalidad donde se armó la
    # señal, no siempre sobre la macro fija: si la zona es Flip/OBC/EV
    # (detectada en la temporalidad de ENTRADA/micro), el TP usa el último
    # movimiento de esa misma micro (df). Solo cuando el bloque es
    # "fractal" (basado en Fibonacci del tramo macro) el TP usa la macro,
    # porque ahí la propia zona ya viene medida en esa temporalidad.
    if bloque in ("fractal", "fractal2"):
        movimiento = movimiento_macro(df, pivot_len_macro, df_macro=df_macro, hasta_tiempo=hasta_tiempo)
    else:
        movimiento = movimiento_macro(df, pivot_len, df_macro=None)
    tp = calcular_tp_desde_macro(precio_entrada, movimiento, tipo, porcentaje_tp)

    if minimo_ratio_tp is not None and tp is not None:
        riesgo = abs(precio_entrada - sl)
        if riesgo > 0:
            ratio_real = abs(tp - precio_entrada) / riesgo
            if ratio_real < minimo_ratio_tp:
                return None

    return {
        "tipo": tipo,
        "bloque": bloque,  # "flip" | "ev" | "obc" | "fractal"
        "precio_entrada": precio_entrada,
        "sl": sl,
        "tp": tp,
        "zona_precio": zona_precio,
        "movimiento_macro": movimiento,
    }


# ============================================================
# LIQUIDEZ — SpotPlus
# ============================================================
# Externa: pools de liquidez FUERA de la zona actual (puntos iguales /
#          "equal highs/lows", o el pico/valle de tendencia previo). Sirve
#          como OBJETIVO (ahí es probable que el precio vaya a "barrer" antes
#          de girar) — no como entrada.
# Interna: liquidez DENTRO del tramo, entre la zona de espera y el precio
#          actual (un pivote menor ya tomado). Sirve como CONFLUENCIA /
#          confirmación: ya se usó como "combustible" antes de llegar a la
#          zona, lo que da mayor probabilidad a la señal.
# ============================================================

def detectar_liquidez_externa(pivotes, tolerancia_pct=0.1):
    """
    Agrupa pivotes del mismo tipo (high/low) cuyo precio es prácticamente
    igual (dentro de tolerancia_pct %) -> "puntos iguales" (equal highs /
    equal lows), el patrón clásico de acumulación de liquidez externa.

    Devuelve una lista de pools:
    {"tipo": "high"/"low", "precio": float (promedio del grupo),
     "pivotes": [indices], "barrida": bool}
    'barrida' = True si ya existe, DESPUÉS del último pivote del grupo, una
    vela/pivote que superó ese nivel (el pool ya fue tomado por el precio).
    """
    pools = []
    for tipo in ("high", "low"):
        del_tipo = [p for p in pivotes if p["tipo"] == tipo]
        usados = set()
        for i, p in enumerate(del_tipo):
            if i in usados:
                continue
            grupo = [p]
            tolerancia = p["precio"] * (tolerancia_pct / 100)
            for j in range(i + 1, len(del_tipo)):
                if j in usados:
                    continue
                if abs(del_tipo[j]["precio"] - p["precio"]) <= tolerancia:
                    grupo.append(del_tipo[j])
                    usados.add(j)
            if len(grupo) >= 2:
                precio_prom = sum(g["precio"] for g in grupo) / len(grupo)
                ultimo_indice = max(g["indice"] for g in grupo)
                posteriores = [q for q in pivotes if q["indice"] > ultimo_indice]
                if tipo == "high":
                    barrida = any(q["precio"] > precio_prom for q in posteriores)
                else:
                    barrida = any(q["precio"] < precio_prom for q in posteriores)
                pools.append({
                    "tipo": tipo,
                    "precio": precio_prom,
                    "pivotes": [g["indice"] for g in grupo],
                    "barrida": barrida,
                })
    return pools


def detectar_liquidez_interna(pivotes, zona, precio_actual):
    """
    Liquidez INTERNA respecto a una zona de espera ya detectada: busca
    pivotes menores que quedaron ENTRE el precio de la zona y el precio
    actual (es decir, ya "consumidos" en el camino hacia la zona/hacia el
    rompimiento). Su presencia se usa como confluencia: indica que hubo
    "combustible" (liquidez tomada) antes de llegar a la zona.

    Devuelve lista de {"indice", "precio", "tipo"} de los pivotes internos,
    ordenada por índice (más antiguo primero). Lista vacía si no hay.
    """
    lo, hi = sorted([zona["precio"], precio_actual])
    internos = [
        p for p in pivotes
        if p["indice"] > zona["indice"] and lo < p["precio"] < hi
    ]
    return sorted(internos, key=lambda p: p["indice"])


def liquidez_relevante_para_zona(pivotes, zona_precio, tipo, tolerancia_pct=0.15):
    """
    Pools de liquidez externa (equal highs/lows) que SÍ importan para decidir
    si una zona de compra/venta es de fiar, según la lógica:
      - compra -> lo relevante son los pools de "low" iguales por DEBAJO
        (o al nivel) de la zona: si todavía no se barrieron, el precio
        puede necesitar ir por ellos antes de que la compra funcione de
        verdad (esa liquidez es la "carnada" que faltaría cazar).
      - venta  -> lo mismo pero con pools de "high" por ENCIMA (o al nivel)
        de la zona.
    No mira liquidez del lado contrario (esa no interfiere con esta zona).
    """
    pools = detectar_liquidez_externa(pivotes, tolerancia_pct)
    tolerancia = zona_precio * (tolerancia_pct / 100)
    if tipo == "compra":
        return [p for p in pools if p["tipo"] == "low" and p["precio"] <= zona_precio + tolerancia]
    return [p for p in pools if p["tipo"] == "high" and p["precio"] >= zona_precio - tolerancia]


def filtrar_por_liquidez(tipo, zona_precio, pivotes, modo_liquidez, tolerancia_pct=0.15):
    """
    Decide si una zona de espera (Flip/EV/OBC/Fractal) pasa el filtro de
    liquidez, según 'modo_liquidez'. NO se aplica a la 5ta zona (extensión
    de liquidez): esa ya ES en sí misma una barrida, sería redundante.

    Valores de 'modo_liquidez':
      - None / "desactivado" / "confluencia": no descarta nada. Con
        "confluencia" la liquidez solo suma puntaje en analizar_confluencias,
        no bloquea la señal aquí.
      - "evitar": descarta la zona si TODAVÍA hay un pool de liquidez
        relevante SIN barrer en el camino — evita entrar cuando el precio
        podría ir primero a cazar esa liquidez antes de que la zona
        funcione de verdad (la zona podría romperse/invalidarse en el
        camino a esa caza).
      - "esperar_barrida": al revés — solo acepta la zona si esa liquidez
        relevante YA fue barrida antes de llegar a la zona (exige que la
        caza de stops ya haya ocurrido, como confirmación extra de que el
        "combustible" ya se usó).
    Si no hay ningún pool relevante detectado, ambos modos dejan pasar la
    zona (no hay liquidez que evitar ni que esperar).
    """
    if not modo_liquidez or modo_liquidez in ("desactivado", "confluencia"):
        return True
    relevantes = liquidez_relevante_para_zona(pivotes, zona_precio, tipo, tolerancia_pct)
    if not relevantes:
        return True
    if modo_liquidez == "evitar":
        return not any(not p["barrida"] for p in relevantes)
    if modo_liquidez == "esperar_barrida":
        return any(p["barrida"] for p in relevantes)
    return True


def detectar_acumulacion(pivotes, ventana=6, tolerancia_pct=0.15):
    """
    Detecta una zona de ACUMULACIÓN (piscina de liquidez por compresión):
    cuando el precio hace varios impulsos y retrocesos SEGUIDOS (mínimo
    'ventana' pivotes) sin sacar liquidez de forma clara — es decir, los
    picos entre sí quedan casi al mismo nivel (techo) y los valles entre
    sí también (piso), formando un rango comprimido en vez de una
    tendencia clara con HH/LL nuevos.

    Es distinta de 'detectar_liquidez_externa' (puntos iguales sueltos):
    aquí se exige que los ÚLTIMOS 'ventana' pivotes en conjunto formen
    ese rango, es decir, que el precio esté ACTUALMENTE comprimido, no
    solo que haya dos picos iguales en algún punto del historial.

    Devuelve None si no hay compresión reciente, o:
    {"techo": float, "piso": float, "indice_inicio": int, "indice_fin": int,
     "toques": int}
    'techo'/'piso' son el nivel promedio de los picos/valles del rango —
    ahí es donde se concentra la liquidez que el precio probablemente
    "barra" cuando por fin rompa la compresión.
    """
    if len(pivotes) < ventana:
        return None

    recientes = pivotes[-ventana:]
    highs = [p for p in recientes if p["tipo"] == "high"]
    lows = [p for p in recientes if p["tipo"] == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None

    max_h, min_h = max(p["precio"] for p in highs), min(p["precio"] for p in highs)
    max_l, min_l = max(p["precio"] for p in lows), min(p["precio"] for p in lows)
    rango_highs_pct = (max_h - min_h) / min_h * 100
    rango_lows_pct = (max_l - min_l) / min_l * 100

    if rango_highs_pct > tolerancia_pct or rango_lows_pct > tolerancia_pct:
        return None  # sigue habiendo HH/LL nuevos -> no está comprimido

    return {
        "techo": (max_h + min_h) / 2,
        "piso": (max_l + min_l) / 2,
        "indice_inicio": recientes[0]["indice"],
        "indice_fin": recientes[-1]["indice"],
        "toques": len(recientes),
    }


def analizar_confluencias(df, pivotes, zona, idx_zona, precio_actual,
                           tolerancia_pct_liquidez=0.1, confluencia_fundamental=None):
    """
    Junta las confluencias disponibles para una zona de espera dada, para
    dar un "score" simple de mayor probabilidad (no cambia la señal, es
    informativo/filtrable desde afuera):

      - fibo: si la zona cae cerca de un nivel de Fibonacci del tramo previo.
      - liquidez_interna: pivotes ya "tomados" entre la zona y el precio actual.
      - liquidez_externa_cercana: pools de puntos iguales (equal highs/lows)
        cerca de la zona (mismo criterio de tolerancia), que reforzarían la
        zona como origen de un movimiento hacia esa liquidez.
      - acumulacion: si la zona está en el techo o piso de un rango de
        compresión reciente (piscina de liquidez por acumulación).
      - fundamental: si se pasa 'confluencia_fundamental' (el resultado de
        backend.fundamentales.confluencia_fundamental(...) ya calculado
        afuera, para no acoplar este módulo a requests/red), y no es None,
        cuenta como una confluencia más — la noticia salió a favor de la
        misma dirección que esta zona.

    Devuelve:
    {"fibo_pct": float o None, "liquidez_interna": [...], "liquidez_externa": [...],
     "acumulacion": dict o None, "fundamental": dict o None, "score": int}
    """
    fibo_pct = None
    if idx_zona > 0:
        pivote_anterior = pivotes[idx_zona - 1]
        niveles = fibonacci_del_tramo(pivote_anterior["precio"], pivotes[idx_zona]["precio"])
        distancias = {pct: abs(precio - zona["precio"]) for pct, precio in niveles.items()}
        fibo_pct = min(distancias, key=distancias.get)

    liquidez_interna = detectar_liquidez_interna(pivotes, zona, precio_actual)

    pools = detectar_liquidez_externa(pivotes, tolerancia_pct_liquidez)
    tolerancia_zona = zona["precio"] * (tolerancia_pct_liquidez / 100)
    liquidez_externa_cercana = [
        pool for pool in pools if abs(pool["precio"] - zona["precio"]) <= tolerancia_zona
    ]

    acumulacion = detectar_acumulacion(pivotes)
    acumulacion_cercana = None
    if acumulacion is not None:
        if abs(zona["precio"] - acumulacion["techo"]) <= tolerancia_zona or \
           abs(zona["precio"] - acumulacion["piso"]) <= tolerancia_zona:
            acumulacion_cercana = acumulacion

    score = sum([
        fibo_pct is not None,
        len(liquidez_interna) > 0,
        len(liquidez_externa_cercana) > 0,
        acumulacion_cercana is not None,
        confluencia_fundamental is not None,
    ])

    return {
        "fibo_pct": fibo_pct,
        "liquidez_interna": liquidez_interna,
        "liquidez_externa": liquidez_externa_cercana,
        "acumulacion": acumulacion_cercana,
        "fundamental": confluencia_fundamental,
        "score": score,
    }


# ============================================================
# INTERTEMPORALIDADES — SpotPlus
# ============================================================
# Clasificación de las 11 temporalidades del sistema en 4 grupos, y la
# sensibilidad de pivotes (pivot_len) sugerida para cada grupo, siguiendo
# la guía ya usada en detectar_pivotes():
#   micro (1-5): 2-3   |  intermedia (15m-1h): 5-7  |  macro (2h-D1): 8-12
#   extra_macro (semanal/mensual): se usa como contexto de tendencia mayor,
#   no para pivotes de entrada (pivot_len orientativo, no se opera ahí).
# ============================================================

TEMPORALIDADES = {
    "M1": "micro", "M3": "micro", "M5": "micro",
    "M15": "intermedia", "M30": "intermedia", "H1": "intermedia",
    "H2": "macro", "H4": "macro", "D1": "macro",
    "W1": "extra_macro", "MN1": "extra_macro",
}

PIVOT_LEN_SUGERIDO = {
    "micro": (2, 3),
    "intermedia": (5, 7),
    "macro": (8, 12),
    "extra_macro": (12, 20),
}


def clasificar_temporalidad(timeframe_str, grupos_custom=None):
    """
    Devuelve el grupo ("micro"/"intermedia"/"macro"/"extra_macro") de una
    temporalidad (ej. "M15", "H4"), o None si no está en la lista de las
    11 temporalidades de SpotPlus.

    grupos_custom (opcional): dict {"M15": "macro", "H1": "intermedia", ...}
    que viene de config_bot["grupos_temporalidad"] (configurado desde el
    panel). Si la temporalidad está ahí, esa asignación manda por encima
    de la clasificación fija de TEMPORALIDADES.
    """
    tf = timeframe_str.upper()
    if grupos_custom and tf in grupos_custom:
        return grupos_custom[tf]
    return TEMPORALIDADES.get(tf)


def pivot_len_sugerido(timeframe_str):
    """
    Devuelve (min, max) de pivot_len recomendado para 'timeframe_str',
    según el grupo al que pertenece. None si la temporalidad no es
    reconocida.
    """
    grupo = clasificar_temporalidad(timeframe_str)
    if grupo is None:
        return None
    return PIVOT_LEN_SUGERIDO[grupo]


# ============================================================
# ESCANEO MULTI-TEMPORALIDAD — detecta automáticamente cuál es la
# temporalidad "principal" de CADA señal (no un macro fijo config_bot).
# ============================================================
#
# Reglas (confirmadas con el usuario):
#   1. La oportunidad (zona de espera confirmada) puede aparecer en
#      CUALQUIER temporalidad intermedia (M15/M30/H1) o macro (H2/H4/D1).
#      Esa temporalidad se vuelve la "principal" de ESA señal específica.
#   2. La confirmación (rompimiento/BOS) SIEMPRE se busca en micro
#      (M1/M3/M5, cualquiera que esté disponible) — nunca en la misma
#      temporalidad principal.
#   3. Si la principal detectada es INTERMEDIA: el TP se mide sobre el
#      propio tramo A->B de esa temporalidad (% configurable, no un
#      macro externo fijo).
#   4. Si la principal detectada es MACRO: se exige alineación (misma
#      dirección de zona) en al menos una temporalidad intermedia, y el
#      TP se calcula como un ratio riesgo/beneficio fijo (ej. 1:4), no
#      como % de un tramo.
#   5. Prioridad de búsqueda: primero macro (oportunidad más grande/rara,
#      exige más), luego intermedia.

def _pivot_len_medio(timeframe_str):
    rango = pivot_len_sugerido(timeframe_str)
    if rango is None:
        return 5
    return round(sum(rango) / 2)


def _ultima_zona_confirmada_por_tf(df, timeframe_str, tolerancia_pct=0.15):
    """Detecta la última zona de espera de un DataFrame propio y regresa
    (zona, pivotes) o (None, None) si no hay ninguna zona formada."""
    pivot_len = _pivot_len_medio(timeframe_str)
    pivotes = detectar_pivotes(df, pivot_len)
    if len(pivotes) < 4:
        return None, None
    pivotes = clasificar_pivotes(pivotes)
    fractal2 = zona_fractal_2(df, pivot_len, tolerancia_pct=tolerancia_pct)
    if fractal2 is not None:
        return fractal2, pivotes
    zonas = zonas_espera(pivotes)
    if not zonas:
        return None, None
    zona = dict(zonas[-1])
    indice_confirmacion = min(zona["indice"] + pivot_len, len(df) - 1)
    vela_pivote = df.iloc[zona["indice"]]
    if zona["tipo"] == "compra":
        zona["limite_inferior"] = float(vela_pivote["low"])
        zona["limite_superior"] = float(max(vela_pivote["open"], vela_pivote["close"]))
    else:
        zona["limite_inferior"] = float(min(vela_pivote["open"], vela_pivote["close"]))
        zona["limite_superior"] = float(vela_pivote["high"])
    if "time" in df.columns:
        zona["hora_confirmacion"] = df["time"].iloc[indice_confirmacion]
    return zona, pivotes


def _seleccionar_zona_micro(velas_micro, tipo, zona_principal, tolerancia_pct=0.15):
    """Elige una zona micro solapada con la principal y devuelve sus marcos confluyentes."""
    orden_tf = {"M5": 3, "M3": 2, "M1": 1}
    candidatas = []
    base_inferior = float(zona_principal.get("limite_inferior", zona_principal["precio"]))
    base_superior = float(zona_principal.get("limite_superior", zona_principal["precio"]))

    for tf, df in velas_micro.items():
        zona, _ = _ultima_zona_confirmada_por_tf(df, tf, tolerancia_pct)
        if zona is None or zona["tipo"] != tipo:
            continue
        tolerancia = abs(float(zona["precio"])) * tolerancia_pct / 100
        if (
            float(zona["limite_inferior"]) > base_superior + tolerancia
            or float(zona["limite_superior"]) < base_inferior - tolerancia
        ):
            continue
        candidatas.append((tf, zona))

    if not candidatas:
        return None

    grupos = []
    for _, zona_base in candidatas:
        tol_base = abs(float(zona_base["precio"])) * tolerancia_pct / 100
        compatibles = [
            (tf, zona)
            for tf, zona in candidatas
            if (
                float(zona["limite_inferior"]) <= float(zona_base["limite_superior"]) + tol_base
                and float(zona["limite_superior"]) >= float(zona_base["limite_inferior"]) - tol_base
            )
        ]
        grupos.append(compatibles)

    grupo = max(
        grupos,
        key=lambda zonas: (
            len(zonas),
            max(orden_tf.get(tf, 0) for tf, _ in zonas),
        ),
    )
    mediana = sorted(float(zona["precio"]) for _, zona in grupo)[len(grupo) // 2]
    tf_elegido, zona_elegida = min(
        grupo,
        key=lambda par: (
            abs(float(par[1]["precio"]) - mediana),
            -orden_tf.get(par[0], 0),
        ),
    )
    return {
        "timeframe": tf_elegido,
        "zona": zona_elegida,
        "temporalidades": [tf for tf, _ in grupo],
        "confluencias": len(grupo),
    }


def _atr_en_indice(df, indice, periodo=14):
    """ATR simple, calculado solo con velas disponibles hasta ``indice``."""
    if indice < periodo or len(df) <= indice or not {"high", "low", "close"}.issubset(df.columns):
        return None
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close_anterior = df["close"].astype(float).shift(1)
    rango_real = pd.concat(
        [
            high - low,
            (high - close_anterior).abs(),
            (low - close_anterior).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = rango_real.rolling(periodo, min_periods=periodo).mean().iloc[indice]
    return float(atr) if pd.notna(atr) and atr > 0 else None


def _indice_desde_tiempo(df, tiempo):
    """Primer índice posterior a ``tiempo``; si falta tiempo, usa el historial completo."""
    if tiempo is None or "time" not in df.columns:
        return 0
    try:
        serie = pd.to_datetime(df["time"], utc=True)
        base = pd.Timestamp(tiempo)
        if base.tzinfo is None:
            base = base.tz_localize("UTC")
        else:
            base = base.tz_convert("UTC")
        indices = serie[serie >= base].index
        return int(indices[0]) if len(indices) else len(df)
    except Exception:
        return 0


def _indice_ultima_vela_cerrada(df, timeframe_str):
    """No usa la vela en formación de MT5 para confirmar un cierre."""
    timeframe = str(timeframe_str).upper()
    minutos = {
        "M1": 1, "M3": 3, "M5": 5, "M15": 15, "M30": 30,
        "H1": 60, "H2": 120, "H4": 240, "D1": 1440,
        "W1": 10080,
    }.get(timeframe)
    if (minutos is None and timeframe != "MN1") or "time" not in df.columns or not len(df):
        return len(df) - 1

    apertura = pd.to_datetime(df["time"].iloc[-1], utc=True)
    cierre = apertura + (
        pd.DateOffset(months=1) if timeframe == "MN1"
        else pd.Timedelta(minutes=minutos)
    )
    ahora = pd.Timestamp.now(tz="UTC")
    return len(df) - 2 if cierre > ahora and len(df) > 1 else len(df) - 1


def _patron_reversion_en_cierre(df, indice, tipo):
    """Reconoce envolvente, pinbar o doji de rechazo en la vela ya cerrada."""
    if indice < 1 or not {"open", "high", "low", "close"}.issubset(df.columns):
        return None

    anterior = df.iloc[indice - 1]
    vela = df.iloc[indice]
    apertura = float(vela["open"])
    cierre = float(vela["close"])
    maximo = float(vela["high"])
    minimo = float(vela["low"])
    rango = maximo - minimo
    cuerpo = abs(cierre - apertura)
    if rango <= 0:
        return None

    max_anterior = max(float(anterior["open"]), float(anterior["close"]))
    min_anterior = min(float(anterior["open"]), float(anterior["close"]))
    max_actual = max(apertura, cierre)
    min_actual = min(apertura, cierre)
    mecha_superior = maximo - max_actual
    mecha_inferior = min_actual - minimo
    es_doji = cuerpo <= rango * 0.1

    if tipo == "compra":
        envolvente = (
            cierre > apertura
            and float(anterior["close"]) < float(anterior["open"])
            and min_actual <= min_anterior
            and max_actual >= max_anterior
        )
        rechazo = (
            cierre > apertura
            and mecha_inferior >= max(cuerpo * 2, rango * 0.5)
            and cierre >= minimo + rango * 0.65
        )
        doji_rechazo = es_doji and mecha_inferior >= rango * 0.6 and cierre >= minimo + rango * 0.7
    else:
        envolvente = (
            cierre < apertura
            and float(anterior["close"]) > float(anterior["open"])
            and min_actual <= min_anterior
            and max_actual >= max_anterior
        )
        rechazo = (
            cierre < apertura
            and mecha_superior >= max(cuerpo * 2, rango * 0.5)
            and cierre <= maximo - rango * 0.65
        )
        doji_rechazo = es_doji and mecha_superior >= rango * 0.6 and cierre <= maximo - rango * 0.7

    if envolvente:
        return "envolvente"
    if rechazo:
        return "pinbar"
    if doji_rechazo:
        return "doji_rechazo"
    return None


def _ruptura_y_retroceso_en_micro(
    pivotes_principal,
    zona,
    df_micro,
    timeframe_str=None,
    tolerancia_pct=0.15,
    ruptura_cuerpo_atr=1.0,
    ruptura_cierre_atr=0.1,
):
    """Confirma BOS cerrado, retesteo y vela de reversión cerrada a favor."""
    tipo_referencia = "high" if zona["tipo"] == "compra" else "low"
    referencia = _pivote_anterior_de_tipo(pivotes_principal, zona["indice"], tipo_referencia)
    if referencia is None or df_micro is None or len(df_micro) < 17:
        return None

    indice_inicio = _indice_desde_tiempo(df_micro, zona.get("hora_confirmacion"))
    indice_ruptura = None
    ruptura_fuerte = False
    indice_actual = _indice_ultima_vela_cerrada(df_micro, timeframe_str)
    if indice_actual < 16:
        return None
    for indice in range(max(14, indice_inicio), indice_actual + 1):
        vela = df_micro.iloc[indice]
        cierre = float(vela["close"])
        distancia = (
            cierre - float(referencia["precio"])
            if zona["tipo"] == "compra"
            else float(referencia["precio"]) - cierre
        )
        if distancia <= 0:
            continue
        atr = _atr_en_indice(df_micro, indice)
        if atr is None:
            continue
        cuerpo = abs(float(vela["close"]) - float(vela["open"]))
        indice_ruptura = indice
        ruptura_fuerte = (
            cuerpo >= atr * ruptura_cuerpo_atr
            and distancia >= atr * ruptura_cierre_atr
        )
        break

    # La vela cerrada que confirma el retesteo debe ser posterior al BOS.
    if indice_ruptura is None or indice_actual <= indice_ruptura:
        return None

    patron_vela = _patron_reversion_en_cierre(df_micro, indice_actual, zona["tipo"])
    if patron_vela is None:
        return None

    precio_confirmacion = float(df_micro["close"].iloc[indice_actual])
    precio_actual = float(df_micro["close"].iloc[-1])
    inferior = float(zona.get("limite_inferior", zona["precio"]))
    superior = float(zona.get("limite_superior", zona["precio"]))
    tolerancia = abs(float(zona["precio"])) * tolerancia_pct / 100
    inferior -= tolerancia
    superior += tolerancia
    if not (
        inferior <= precio_confirmacion <= superior
        and inferior <= precio_actual <= superior
    ):
        return None

    return {
        "confirmado": True,
        "precio_actual": precio_actual,
        "indice_ruptura": indice_ruptura,
        "ruptura_fuerte": ruptura_fuerte,
        "tipo_ruptura": "fuerte" if ruptura_fuerte else "debil",
        "patron_vela": patron_vela,
    }


def _clasificar_zona_multitemporal(zona, pivotes, df, timeframe_str, ruptura_fuerte, tolerancia_pct):
    """Clasifica EV/Flip/OBC y confluencias Fibo del 27–78.6% del impulso."""
    if zona.get("bloque") == "fractal2":
        return "fractal2", zona.get("fibo_pct")
    if not ruptura_fuerte:
        return "obc", None

    idx_zona = next(
        (i for i, pivote in enumerate(pivotes) if pivote["indice"] == zona["indice"]),
        None,
    )
    bloque_estructural = clasificar_bloque(pivotes, idx_zona) if idx_zona is not None else "ev"

    pivot_len = _pivot_len_medio(timeframe_str)
    movimiento = movimiento_macro(df, pivot_len_macro=pivot_len)
    if movimiento is not None:
        niveles = fibonacci_del_tramo(movimiento["precio_inicio"], movimiento["precio_fin"])
        candidatos = (0.27, 0.38, 0.5, 0.618, 0.786)
        nivel_cercano = min(candidatos, key=lambda pct: abs(niveles[pct] - zona["precio"]))
        tolerancia = max(
            abs(float(zona["precio"])) * tolerancia_pct / 100,
            abs(float(zona.get("limite_superior", zona["precio"])) - float(zona.get("limite_inferior", zona["precio"]))),
        )
        if abs(niveles[nivel_cercano] - zona["precio"]) <= tolerancia:
            return "fractal", nivel_cercano

        rango = abs(movimiento["precio_fin"] - movimiento["precio_inicio"])
        if rango > 0:
            if movimiento["tipo"] == "alcista":
                extensiones = [
                    movimiento["precio_fin"] + rango * (nivel - 1)
                    for nivel in (1.1, 1.27, 1.38)
                ]
                tipos_liquidez = ("high", "low")
            else:
                extensiones = [
                    movimiento["precio_fin"] - rango * (nivel - 1)
                    for nivel in (1.1, 1.27, 1.38)
                ]
                tipos_liquidez = ("high", "low")

            zona_baja, zona_alta = min(extensiones), max(extensiones)
            if zona_baja - tolerancia <= zona["precio"] <= zona_alta + tolerancia:
                pools = detectar_liquidez_externa(pivotes, tolerancia_pct)
                if not any(pool["barrida"] for pool in pools if pool["tipo"] in tipos_liquidez):
                    return "fractal2", None

    return ("flip" if bloque_estructural == "flip" else "ev"), None


def _confirmar_en_micro(pivotes_principal, zona, velas_micro):
    """
    La confirmación (rompimiento del pivote de referencia) se busca en
    CUALQUIER temporalidad micro disponible, usando el precio de esa
    micro contra el nivel de referencia calculado en la temporalidad
    principal (confirmar_bos ya sabe encontrar ese pivote de referencia).
    Devuelve (confirmado: bool, precio_actual, timeframe_que_confirmo) o
    (False, None, None) si ninguna micro confirma todavía.
    """
    for tf_micro, df_micro in velas_micro.items():
        precio_actual = float(df_micro["close"].iloc[-1])
        if zona["tipo"] == "compra":
            referencia = _pivote_anterior_de_tipo(pivotes_principal, zona["indice"], "high")
            if referencia is not None and precio_actual > referencia["precio"]:
                return True, precio_actual, tf_micro
        else:
            referencia = _pivote_anterior_de_tipo(pivotes_principal, zona["indice"], "low")
            if referencia is not None and precio_actual < referencia["precio"]:
                return True, precio_actual, tf_micro
    return False, None, None


def _hay_alineacion_intermedia(velas_intermedia, tipo, tolerancia_pct=0.15):
    """Para señales con principal MACRO: exige que al menos una
    temporalidad intermedia tenga su última zona de espera del MISMO
    tipo (compra/venta), como evidencia de que ambas escalas apuntan
    a la misma dirección."""
    for tf, df in velas_intermedia.items():
        zona, _ = _ultima_zona_confirmada_por_tf(df, tf, tolerancia_pct)
        if zona is not None and zona["tipo"] == tipo:
            return True
    return False


def escanear_multitemporalidad(
    velas_por_temporalidad,
    porcentaje_tp_intermedia=0.8,
    ratio_riesgo_beneficio_macro=4.0,
    sl_buffer_pct=0.1,
    tolerancia_pct=0.15,
    minimo_ratio_tp=None,
    grupos_temporalidad=None,
    bloques_permitidos=None,
):
    """
    velas_por_temporalidad: dict {"M5": df, "M15": df, "H1": df, "H4": df, ...}
    con las velas reales de cada temporalidad que el bot tenga disponibles
    (no hace falta traer las 11, solo las que uses).

    grupos_temporalidad (opcional): dict {"M15": "macro", ...} configurado
    desde el panel para sobreescribir a qué grupo (micro/intermedia/macro)
    pertenece cada temporalidad, en vez de usar siempre TEMPORALIDADES.

    Escanea automáticamente cuál temporalidad tiene AHORA una oportunidad
    (zona confirmada por rompimiento en micro) y arma la señal completa
    usando esa temporalidad como "principal" — sin necesidad de fijar de
    antemano cuál es "la macro" en la configuración del bot.

    Devuelve None si no hay ninguna señal en ninguna temporalidad, o:
    {
      "tipo": "compra"/"venta",
      "bloque": "flip"/"ev"/"obc"/"fractal",
      "precio_entrada": float,
      "sl": float,
      "tp": float,
      "zona_precio": float,
      "temporalidad_principal": "H1" (por ejemplo),
      "grupo_principal": "intermedia"/"macro",
      "confirmado_en": "M5" (la micro que confirmó el rompimiento),
    }
    """
    micro = {tf: df for tf, df in velas_por_temporalidad.items() if clasificar_temporalidad(tf, grupos_temporalidad) == "micro"}
    intermedia = {tf: df for tf, df in velas_por_temporalidad.items() if clasificar_temporalidad(tf, grupos_temporalidad) == "intermedia"}
    macro = {tf: df for tf, df in velas_por_temporalidad.items() if clasificar_temporalidad(tf, grupos_temporalidad) == "macro"}

    zonas_ok = _normalizar_zonas_permitidas(bloques_permitidos)
    if not micro:
        return None  # sin ninguna micro disponible no hay forma de confirmar nada

    # --- 1) Buscar primero en MACRO (oportunidad más grande, exige más) ---
    for tf_macro, df_macro_tf in macro.items():
        zona, pivotes = _ultima_zona_confirmada_por_tf(df_macro_tf, tf_macro, tolerancia_pct)
        if zona is None:
            continue
        if zonas_ok is not None and zona.get("bloque", "ev") not in zonas_ok:
            continue

        confirmado, precio_actual, tf_confirmo = _confirmar_en_micro(pivotes, zona, micro)
        if not confirmado:
            continue

        # regla 4: exige alineación con al menos una intermedia
        if not _hay_alineacion_intermedia(intermedia, zona["tipo"], tolerancia_pct):
            continue

        sl = zona["precio"] * (1 - sl_buffer_pct / 100) if zona["tipo"] == "compra" else zona["precio"] * (1 + sl_buffer_pct / 100)
        riesgo = abs(precio_actual - sl)
        tp = precio_actual + riesgo * ratio_riesgo_beneficio_macro if zona["tipo"] == "compra" else precio_actual - riesgo * ratio_riesgo_beneficio_macro

        if minimo_ratio_tp is not None and riesgo > 0:
            if abs(tp - precio_actual) / riesgo < minimo_ratio_tp:
                continue

        return {
            "id": f"{zona['tipo']}_{round(zona['precio'], 2)}_{tf_macro}",
            "tipo": zona["tipo"],
            "bloque": zona.get("bloque", "ev"),
            "precio_entrada": precio_actual,
            "sl": sl,
            "tp": tp,
            "zona_precio": zona["precio"],
            "temporalidad_principal": tf_macro,
            "grupo_principal": "macro",
            "confirmado_en": tf_confirmo,
        }

    # --- 2) Si ninguna macro dio señal, buscar en INTERMEDIA ---
    for tf_int, df_int in intermedia.items():
        zona, pivotes = _ultima_zona_confirmada_por_tf(df_int, tf_int, tolerancia_pct)
        if zona is None:
            continue
        if zonas_ok is not None and zona.get("bloque", "ev") not in zonas_ok:
            continue

        confirmado, precio_actual, tf_confirmo = _confirmar_en_micro(pivotes, zona, micro)
        if not confirmado:
            continue

        sl = zona["precio"] * (1 - sl_buffer_pct / 100) if zona["tipo"] == "compra" else zona["precio"] * (1 + sl_buffer_pct / 100)

        # TP = % del propio tramo A->B de ESTA temporalidad intermedia
        movimiento = movimiento_macro(df_int, pivot_len_macro=_pivot_len_medio(tf_int))
        tp = calcular_tp_desde_macro(precio_actual, movimiento, zona["tipo"], porcentaje_tp_intermedia)
        if tp is None:
            continue

        if minimo_ratio_tp is not None:
            riesgo = abs(precio_actual - sl)
            if riesgo > 0 and abs(tp - precio_actual) / riesgo < minimo_ratio_tp:
                continue

        return {
            "id": f"{zona['tipo']}_{round(zona['precio'], 2)}_{tf_int}",
            "tipo": zona["tipo"],
            "bloque": zona.get("bloque", "ev"),
            "precio_entrada": precio_actual,
            "sl": sl,
            "tp": tp,
            "zona_precio": zona["precio"],
            "temporalidad_principal": tf_int,
            "grupo_principal": "intermedia",
            "confirmado_en": tf_confirmo,
        }

    return None


# ============================================================
# MÚLTIPLES OPORTUNIDADES SIMULTÁNEAS + REPARTO DE RIESGO
#
# Caso real (confirmado con el usuario): en vez de una sola operación con
# un TP lejano (extra macro) que aguanta un retroceso profundo a la mitad
# del camino, se abren VARIAS operaciones independientes al mismo tiempo
# — una por cada temporalidad que tenga ahora mismo una oportunidad
# válida (ej. una corta en 5m, otra en 2h) — cada una con su propio SL/TP
# ajustado a su propia escala, y el riesgo total se reparte entre ellas.
# ============================================================

def tp_desde_liquidez(pivotes, precio_entrada, tipo, sl, ratio_minimo=1.0, tolerancia_pct=0.1):
    """
    TP en la liquidez más CERCANA en la dirección de la operación:
    compra -> pool de highs iguales por encima del precio; venta -> pool de
    lows iguales por debajo. Usa detectar_liquidez_externa (puntos iguales).
    Ignora pools ya barridos y los que dejen menos de 'ratio_minimo' R:R.
    Devuelve el precio o None si no hay liquidez que cumpla.
    """
    if not pivotes or not sl:
        return None
    riesgo = abs(precio_entrada - sl)
    if riesgo <= 0:
        return None
    pools = detectar_liquidez_externa(pivotes, tolerancia_pct)
    if tipo == "compra":
        candidatos = [p["precio"] for p in pools
                      if p["tipo"] == "high" and not p["barrida"]
                      and p["precio"] - precio_entrada >= riesgo * ratio_minimo]
        return min(candidatos) if candidatos else None
    candidatos = [p["precio"] for p in pools
                  if p["tipo"] == "low" and not p["barrida"]
                  and precio_entrada - p["precio"] >= riesgo * ratio_minimo]
    return max(candidatos) if candidatos else None


_PRIORIDAD_GRUPO = {"macro": 3, "intermedia": 2, "micro": 1}


def filtrar_contradicciones(oportunidades):
    """
    Anti-contradicción: si el mismo escaneo trae compras Y ventas, se queda
    solo con la dirección de la temporalidad más alta (macro > intermedia >
    micro) y descarta las opuestas. Así no se abren una compra y una venta
    a la vez sobre el mismo movimiento.
    """
    if len({op["tipo"] for op in oportunidades}) < 2:
        return oportunidades
    guia = max(oportunidades, key=lambda op: _PRIORIDAD_GRUPO.get(op.get("grupo_principal"), 0))
    return [op for op in oportunidades if op["tipo"] == guia["tipo"]]


def contexto_extra_macro(velas_extra_macro, tipo_operacion, pivot_len=15, tolerancia_pct=0.15):
    """
    Filtro de CONTEXTO usando temporalidades extra_macro (W1/MN1) — NO se
    opera ahí, solo se usa para permitir o bloquear señales de macro/
    intermedia según hacia dónde apunte la estructura semanal/mensual
    (ej. si el semanal ya muestra un cambio de dirección hacia venta,
    una señal de compra en macro/intermedia se descarta aunque ahí se
    vea bien, y viceversa).

    velas_extra_macro: dict {"W1": df, "MN1": df} (puede venir vacío).
    Devuelve (permitido: bool, motivo: str). permitido=True si no hay
    datos extra_macro (no bloquea por falta de información) o si el
    contexto no contradice.
    """
    if not velas_extra_macro:
        return True, "sin_datos_extra_macro"

    for tf, df in velas_extra_macro.items():
        alineacion = alineacion_con_macro(df, tipo_operacion, pivot_len_macro=pivot_len)
        if alineacion.get("alineada") is False:
            return False, f"contexto_{tf}_en_contra"

    return True, "contexto_a_favor_o_neutral"


def _barrida_confirmada(pool, pivotes, cierre_actual, max_pivotes_recientes=3):
    """True solo si la liquidez fue barrida de forma RELEVANTE:
      1) el pivote que la tomó está entre los últimos ``max_pivotes_recientes``
         (no una barrida vieja que ya quedó atrás),
      2) el precio actual volvió al lado de origen (hubo reacción / rechazo).
    Que "se rompa un lado" sin reacción NO cuenta: sigue siendo tendencia."""
    if not pool.get("barrida"):
        return False
    ultimo_indice = max(pool["pivotes"])
    tipo = pool["tipo"]
    nivel = pool["precio"]
    if tipo == "high":
        tomadores = [q for q in pivotes if q["indice"] > ultimo_indice and q["precio"] > nivel]
        reaccion = cierre_actual < nivel
    else:
        tomadores = [q for q in pivotes if q["indice"] > ultimo_indice and q["precio"] < nivel]
        reaccion = cierre_actual > nivel
    if not tomadores or not reaccion:
        return False
    recientes = {q["indice"] for q in pivotes[-max_pivotes_recientes:]}
    return any(q["indice"] in recientes for q in tomadores)


def analizar_contexto_multitemporal(velas_por_temporalidad, grupos_temporalidad=None):
    """Resume sesgo, compresión y pools externos detectados en cada marco."""
    resultado = {}
    prioridades = {"micro": 1, "intermedia": 2, "macro": 3, "extra_macro": 4}
    for tf, df in velas_por_temporalidad.items():
        grupo = clasificar_temporalidad(tf, grupos_temporalidad)
        if grupo is None or grupo == "no_usar" or df is None or len(df) < 5:
            continue
        pivot_len = _pivot_len_medio(tf)
        pivotes = clasificar_pivotes(detectar_pivotes(df, pivot_len))
        ultimo_high = next((p for p in reversed(pivotes) if p["tipo"] == "high"), None)
        ultimo_low = next((p for p in reversed(pivotes) if p["tipo"] == "low"), None)
        acumulacion = detectar_acumulacion(pivotes)
        pools = detectar_liquidez_externa(pivotes)
        cierre_actual = float(df["close"].iloc[-1])
        resultado[tf] = {
            "grupo": grupo,
            "sesgo": sesgo_direccion(df, pivot_len),
            "ultimo_maximo": (
                {"precio": float(ultimo_high["precio"]), "clase": ultimo_high.get("clase")}
                if ultimo_high else None
            ),
            "ultimo_minimo": (
                {"precio": float(ultimo_low["precio"]), "clase": ultimo_low.get("clase")}
                if ultimo_low else None
            ),
            "acumulacion": acumulacion,
            "liquidez_externa": [
                {
                    "tipo": pool["tipo"],
                    "precio": float(pool["precio"]),
                    "barrida": bool(pool["barrida"]),
                    # Barrida CON contexto: el pivote que tomó la liquidez es
                    # reciente y el precio ya reaccionó (volvió al lado de
                    # origen). Solo esta es digna de aviso.
                    "barrida_confirmada": _barrida_confirmada(pool, pivotes, cierre_actual),
                }
                for pool in pools
            ],
        }

    conflictos = []
    marcos = [
        (tf, info)
        for tf, info in resultado.items()
        if info["sesgo"] and info["grupo"] in prioridades
    ]
    for tf_mayor, mayor in marcos:
        for tf_menor, menor in marcos:
            if prioridades[mayor["grupo"]] <= prioridades[menor["grupo"]]:
                continue
            if mayor["sesgo"] != menor["sesgo"]:
                conflictos.append({
                    "mayor": tf_mayor,
                    "sesgo_mayor": mayor["sesgo"],
                    "menor": tf_menor,
                    "sesgo_menor": menor["sesgo"],
                })
    return {"temporalidades": resultado, "conflictos": conflictos}


ZONAS_ESPERA_VALIDAS = ("flip", "ev", "obc", "fractal", "fractal2")


def _normalizar_zonas_permitidas(zonas):
    """Lista de zonas permitidas -> tupla en minúsculas, o None = automático (todas).
    Acepta lista o texto separado por comas; ignora nombres que no existan."""
    if not zonas:
        return None
    if isinstance(zonas, str):
        zonas = zonas.split(",")
    limpias = []
    for z in zonas:
        z = str(z).strip().lower().replace(" ", "").replace("_", "")
        if z in ZONAS_ESPERA_VALIDAS and z not in limpias:
            limpias.append(z)
    return tuple(limpias) or None


def escanear_todas_las_oportunidades(
    velas_por_temporalidad,
    porcentaje_tp_intermedia=0.8,
    ratio_riesgo_beneficio_macro=4.0,
    sl_buffer_pct=0.1,
    tolerancia_pct=0.15,
    grupos_temporalidad=None,
    roles=None,
    modo_liquidez=None,
    minimo_ratio_tp=None,
    dividir_con_confirmador=False,
    porcentaje_tp_confirmador=0.8,
    esperar_retroceso=True,
    ruptura_cuerpo_atr=1.0,
    ruptura_cierre_atr=0.1,
    bloques_permitidos=None,
):
    """
    A diferencia de escanear_multitemporalidad() (que regresa SOLO la
    primera señal encontrada, con prioridad macro > intermedia),
    esta función revisa TODOS los grupos disponibles (micro, intermedia,
    macro) y devuelve una LISTA con una señal por cada temporalidad que
    tenga AHORA una zona confirmada — pensada para abrir varias
    operaciones simultáneas, cada una con su propia escala.

    roles (opcional): dict que dice qué hace cada grupo, ej.
        {"micro": "confirmar", "intermedia": "buscar", "macro": "buscar",
         "extra_macro": "contexto"}
    Valores posibles: "buscar" (se le detecta zona y se puede abrir
    operación ahí), "confirmar" (solo se usa para validar el rompimiento
    de las zonas de los grupos "buscar", no abre operación propia),
    "contexto" (solo filtra/veta a favor o en contra, nunca abre
    operación ni confirma nada), "no_usar" (se ignora por completo).
    Si no se pasa, usa el comportamiento por defecto ya validado:
    micro=confirmar, intermedia=buscar, macro=buscar, extra_macro=contexto.

    Reglas por grupo cuando su rol es "buscar":
      - Si el grupo tiene rol "confirmar" disponible: se exige romper
        el pivote de referencia en esa temporalidad menor.
      - Si NO hay ningún grupo "confirmar" disponible: se autoconfirma
        con su propio rompimiento (BOS de su propia temporalidad).
      - TP = % de su propio tramo A->B, salvo el grupo "macro" sin
        confirmador micro disponible, que usa ratio fijo riesgo/beneficio.
      - Grupo "macro" además exige alineación con al menos una
        temporalidad "intermedia" disponible (si existe alguna).
      - Cualquier grupo con rol "contexto" (ej. extra_macro) puede VETAR
        la señal si su estructura apunta claramente en contra.

    modo_liquidez (opcional): mismo filtro que en señal_estructura() —
    "evitar", "esperar_barrida", "confluencia"/None — aplicado a la zona
    detectada en cada temporalidad "buscar" antes de aceptarla.

    dividir_con_confirmador (opcional): escenario "zona macro confirmada
    por una temporalidad menor" (ej. zona en 2h, confirmada por rompimiento
    en 5m) — cuando es True, además de la operación normal del grupo
    "buscar" (que corre hasta el TP de SU escala, ej. el tramo del 2h:
    la "dejas correr"), se agrega una SEGUNDA operación con la MISMA
    entrada/SL pero el TP calculado sobre el tramo propio de la
    temporalidad que confirmó (ej. el 5m: la "tomas" rápido, un TP corto).
    Ambas comparten "id" base pero terminan en "_runner" y "_corta"
    respectivamente, y llevan "escala": "runner"/"corta" para que
    servidor.py/riesgo.py las repartan como dos posiciones independientes.
    Si el grupo se autoconfirmó (no había temporalidad "confirmar"
    disponible), no hay de dónde sacar la escala corta y no se duplica.
    porcentaje_tp_confirmador: el % del tramo de la confirmadora usado
    para esa segunda operación (mismo rol que porcentaje_tp_intermedia
    pero medido en la escala de la confirmadora).

    Con esperar_retroceso=True, el BOS debe cerrar en una micro y la entrada
    espera el retorno a la zona principal con una vela cerrada de reversión
    (envolvente, pinbar o doji de rechazo) a favor. Una zona micro solapada
    suma confluencia, pero no es un requisito adicional. ruptura_cuerpo_atr
    y ruptura_cierre_atr definen fuerza:
    cuerpo >= 1 ATR y cierre >= 0.1 ATR más allá del nivel por defecto.
    Las rupturas débiles se clasifican como OBC; las fuertes como Flip,
    EV o Fractal según estructura y retroceso Fibonacci (incluye 61.8% y
    78.6%). Fractal 2 usa extensiones 110–138% mientras los pools externos
    sigan sin barrer.
    La liquidez se adjunta como confluencia/contexto, no como entrada propia.

    bloques_permitidos (opcional): qué tipos de zona de espera pueden abrir
    operación: "flip", "ev", "obc", "fractal", "fractal2". None o vacío =
    automático (acepta todas las que el motor detecte). Si la zona detectada
    en una temporalidad no es de un tipo permitido, esa temporalidad se salta.

    Devuelve una lista de dicts (puede tener 0 o más elementos, uno por
    temporalidad "buscar" con señal, o dos si dividir_con_confirmador
    generó la pareja corta/runner), cada uno con "grupo_principal" para
    identificar de qué escala viene.
    """
    if ruptura_cuerpo_atr < 0 or ruptura_cierre_atr < 0:
        raise ValueError("Los umbrales de ruptura ATR no pueden ser negativos")
    zonas_ok = _normalizar_zonas_permitidas(bloques_permitidos)

    roles = roles or {"micro": "confirmar", "intermedia": "buscar", "macro": "buscar", "extra_macro": "contexto"}

    por_grupo = {"micro": {}, "intermedia": {}, "macro": {}, "extra_macro": {}}
    for tf, df in velas_por_temporalidad.items():
        g = clasificar_temporalidad(tf, grupos_temporalidad)
        if g in por_grupo:
            por_grupo[g][tf] = df

    confirmadores = {}
    contexto_grupos = {}
    buscadores = {}
    for grupo, tfs in por_grupo.items():
        rol = roles.get(grupo, "no_usar")
        if rol == "confirmar":
            confirmadores.update(tfs)
        elif rol == "contexto":
            contexto_grupos[grupo] = tfs
        elif rol == "buscar":
            buscadores[grupo] = tfs

    intermedia_para_alineacion = por_grupo["intermedia"] if roles.get("intermedia") != "no_usar" else {}

    orden_tf = {
        "MN1": 12, "W1": 11, "D1": 10, "H4": 9, "H2": 8,
        "H1": 7, "M30": 6, "M15": 5, "M5": 4, "M3": 3, "M1": 2,
    }
    micro_ordenadas = dict(sorted(
        confirmadores.items(),
        key=lambda item: orden_tf.get(item[0].upper(), 0),
        reverse=True,
    ))
    oportunidades = []
    for grupo_nombre, tfs in buscadores.items():
        tfs_ordenadas = sorted(
            tfs.items(),
            key=lambda item: orden_tf.get(item[0].upper(), 0),
            reverse=True,
        )
        for tf, df in tfs_ordenadas:
            zona, pivotes = _ultima_zona_confirmada_por_tf(df, tf, tolerancia_pct)
            if zona is None:
                continue

            if not filtrar_por_liquidez(zona["tipo"], zona["precio"], pivotes, modo_liquidez, tolerancia_pct):
                continue

            if micro_ordenadas:
                tf_micro_guia, df_micro_guia = next(iter(micro_ordenadas.items()))
                sesgo_micro = sesgo_direccion(df_micro_guia, _pivot_len_medio(tf_micro_guia))
                direccion_zona = "alcista" if zona["tipo"] == "compra" else "bajista"
                if sesgo_micro is not None and sesgo_micro != direccion_zona:
                    continue

            confirmaciones = []
            if micro_ordenadas:
                for tf_micro, df_micro in micro_ordenadas.items():
                    if esperar_retroceso:
                        confirmacion = _ruptura_y_retroceso_en_micro(
                            pivotes,
                            zona,
                            df_micro,
                            timeframe_str=tf_micro,
                            tolerancia_pct=tolerancia_pct,
                            ruptura_cuerpo_atr=ruptura_cuerpo_atr,
                            ruptura_cierre_atr=ruptura_cierre_atr,
                        )
                    else:
                        confirmado, precio_actual, _ = _confirmar_en_micro(
                            pivotes, zona, {tf_micro: df_micro}
                        )
                        confirmacion = (
                            {
                                "precio_actual": precio_actual,
                                "ruptura_fuerte": False,
                                "tipo_ruptura": "sin_clasificar",
                            }
                            if confirmado else None
                        )
                    if confirmacion is not None:
                        confirmacion["timeframe"] = tf_micro
                        confirmaciones.append(confirmacion)
            elif esperar_retroceso:
                confirmacion = _ruptura_y_retroceso_en_micro(
                    pivotes,
                    zona,
                    df,
                    timeframe_str=tf,
                    tolerancia_pct=tolerancia_pct,
                    ruptura_cuerpo_atr=ruptura_cuerpo_atr,
                    ruptura_cierre_atr=ruptura_cierre_atr,
                )
                if confirmacion is not None:
                    confirmacion["timeframe"] = tf
                    confirmaciones.append(confirmacion)
            else:
                bos = confirmar_bos(df, pivotes, zona)
                if bos is not None and bos["confirmado"]:
                    confirmaciones.append({
                        "precio_actual": bos["precio_actual"],
                        "timeframe": tf,
                        "ruptura_fuerte": False,
                        "tipo_ruptura": "sin_clasificar",
                    })
            if not confirmaciones:
                continue

            confirmacion = confirmaciones[0]
            precio_actual = confirmacion["precio_actual"]
            tf_confirmo = confirmacion["timeframe"]
            ruptura_fuerte = confirmacion["ruptura_fuerte"]

            zona_micro_info = _seleccionar_zona_micro(
                micro_ordenadas,
                zona["tipo"],
                zona,
                tolerancia_pct,
            ) if micro_ordenadas else None
            if zona_micro_info is not None:
                zona_micro = zona_micro_info["zona"]
                df_zona_micro = micro_ordenadas[zona_micro_info["timeframe"]]
                indice_cierre_micro = _indice_ultima_vela_cerrada(
                    df_zona_micro, zona_micro_info["timeframe"]
                )
                precio_micro = float(df_zona_micro["close"].iloc[indice_cierre_micro])
                tolerancia_micro = abs(float(zona_micro["precio"])) * tolerancia_pct / 100
                if not (
                    float(zona_micro["limite_inferior"]) - tolerancia_micro
                    <= precio_micro
                    <= float(zona_micro["limite_superior"]) + tolerancia_micro
                ):
                    continue

            if grupo_nombre == "macro" and intermedia_para_alineacion:
                if not _hay_alineacion_intermedia(intermedia_para_alineacion, zona["tipo"], tolerancia_pct):
                    continue

            vetado = False
            for tf_ctx, df_ctx in contexto_grupos.get("extra_macro", {}).items():
                permitido, _motivo = contexto_extra_macro(
                    {tf_ctx: df_ctx},
                    zona["tipo"],
                    pivot_len=_pivot_len_medio(tf_ctx),
                    tolerancia_pct=tolerancia_pct,
                )
                if not permitido:
                    vetado = True
                    break
            if vetado:
                continue

            bloque, fibo_pct = _clasificar_zona_multitemporal(
                zona, pivotes, df, tf, ruptura_fuerte, tolerancia_pct
            )
            if zonas_ok is not None and bloque not in zonas_ok:
                continue
            liquidez_externa = liquidez_relevante_para_zona(
                pivotes, zona["precio"], zona["tipo"], tolerancia_pct
            )
            liquidez_interna = detectar_liquidez_interna(pivotes, zona, precio_actual)
            pools_barridos = [pool for pool in liquidez_externa if pool["barrida"]]
            acumulacion = detectar_acumulacion(pivotes)

            sl = zona["precio"] * (1 - sl_buffer_pct / 100) if zona["tipo"] == "compra" else zona["precio"] * (1 + sl_buffer_pct / 100)
            movimiento = movimiento_macro(df, pivot_len_macro=_pivot_len_medio(tf))
            if grupo_nombre == "macro" and not micro_ordenadas:
                riesgo = abs(precio_actual - sl)
                tp = precio_actual + riesgo * ratio_riesgo_beneficio_macro if zona["tipo"] == "compra" else precio_actual - riesgo * ratio_riesgo_beneficio_macro
            else:
                tp = calcular_tp_desde_macro(precio_actual, movimiento, zona["tipo"], porcentaje_tp_intermedia)
            if tp is None:
                continue

            if minimo_ratio_tp is not None:
                riesgo = abs(precio_actual - sl)
                if riesgo > 0 and abs(tp - precio_actual) / riesgo < minimo_ratio_tp:
                    continue

            id_base = f"{zona['tipo']}_{round(zona['precio'], 2)}_{tf}_{zona.get('hora_confirmacion', zona['indice'])}"
            confirmada_por_menor = tf_confirmo != tf and tf_confirmo in confirmadores
            escala = "runner" if (dividir_con_confirmador and confirmada_por_menor) else None
            metadatos = {
                "tipo_ruptura": confirmacion["tipo_ruptura"],
                "patron_vela": confirmacion.get("patron_vela"),
                "ruptura_fuerte": ruptura_fuerte,
                "confluencias_micro": zona_micro_info["confluencias"] if zona_micro_info else len(confirmaciones),
                "temporalidades_micro": zona_micro_info["temporalidades"] if zona_micro_info else [item["timeframe"] for item in confirmaciones],
                "fibo_pct": fibo_pct,
                "liquidez_externa": liquidez_externa,
                "liquidez_interna": liquidez_interna,
                "liquidez_barrida": bool(pools_barridos),
                "acumulacion": acumulacion,
            }
            oportunidades.append({
                "id": id_base if escala is None else f"{id_base}_runner",
                "tipo": zona["tipo"],
                "bloque": bloque,
                "precio_entrada": precio_actual,
                "sl": sl,
                "tp": tp,
                "zona_precio": zona["precio"],
                "temporalidad_principal": tf,
                "grupo_principal": grupo_nombre,
                "confirmado_en": tf_confirmo,
                "tp_liquidez": tp_desde_liquidez(pivotes, precio_actual, zona["tipo"], sl),
                **metadatos,
                **({"escala": escala} if escala else {}),
            })

            if escala == "runner":
                tf_entrada_corta = zona_micro_info["timeframe"] if zona_micro_info else tf_confirmo
                df_corto = micro_ordenadas.get(tf_entrada_corta, confirmadores.get(tf_confirmo))
                movimiento_corto = movimiento_macro(df_corto, pivot_len_macro=_pivot_len_medio(tf_entrada_corta))
                zona_entrada_corta = zona_micro_info["zona"] if zona_micro_info else zona
                precio_entrada_corta = (
                    float(df_corto["close"].iloc[-1]) if df_corto is not None else precio_actual
                )
                tp_corta = calcular_tp_desde_macro(
                    precio_entrada_corta,
                    movimiento_corto,
                    zona["tipo"],
                    porcentaje_tp_confirmador,
                )
                if tp_corta is not None:
                    sl_corta = zona_entrada_corta["precio"] * (1 - sl_buffer_pct / 100) if zona["tipo"] == "compra" else zona_entrada_corta["precio"] * (1 + sl_buffer_pct / 100)
                    oportunidades.append({
                        "id": f"{id_base}_corta",
                        "tipo": zona["tipo"],
                        "bloque": bloque,
                        "precio_entrada": precio_entrada_corta,
                        "sl": sl_corta,
                        "tp": tp_corta,
                        "zona_precio": zona_entrada_corta["precio"],
                        "temporalidad_principal": tf_entrada_corta,
                        "grupo_principal": grupo_nombre,
                        "confirmado_en": tf_confirmo,
                        "tp_liquidez": tp_desde_liquidez(pivotes, precio_actual, zona["tipo"], sl),
                        **metadatos,
                        "escala": "corta",
                    })
            break

    return filtrar_contradicciones(oportunidades)


def be_dinamico(
    df_entrada,
    velas_por_temporalidad,
    tipo_posicion,
    precio_apertura,
    precio_actual,
    fibo_nivel=0.5,
    tolerancia_pct=0.15,
    grupos_temporalidad=None,
):
    """
    Decide si una posición YA ABIERTA debe protegerse moviendo el SL a
    breakeven, combinando 3 señales (confirmado con el usuario: basta con
    que se cumpla UNA sola de las tres para proteger, no las tres a la vez):

      1) FIBO: el precio ya alcanzó (o superó) el nivel de Fibonacci
         'fibo_nivel' (ej. 0.5 = 50%) del último tramo/impulso de la
         temporalidad de entrada (df_entrada), del lado ganador.
      2) NUEVA ESTRUCTURA: se confirmó una nueva zona + rompimiento en
         alguna temporalidad MICRO a favor de la operación — evidencia de
         que el impulso sigue, así que ya se puede proteger lo ganado.
      3) PÉRDIDA DE ALINEACIÓN: la alineación entre temporalidades (la del
         punto de escanear_multitemporalidad: macro + al menos una
         intermedia en la misma dirección) que sostenía la operación ya
         no se cumple -> se protege por si el movimiento se está agotando.

    velas_por_temporalidad puede venir vacío/None (si el bot no tiene
    temporalidades de escaneo configuradas) — en ese caso solo se evalúa
    la regla 1 (Fibo), sobre df_entrada.

    Devuelve (proteger: bool, motivo: str | None). motivo es uno de
    "fibo", "nueva_estructura_<TF>" o "perdida_alineacion", útil para el
    aviso de Telegram.
    """
    tipo = tipo_posicion

    # --- 1) Nivel de Fibonacci del tramo de la propia temporalidad de entrada ---
    movimiento = movimiento_macro(df_entrada) if df_entrada is not None else None
    if movimiento is not None:
        niveles = fibonacci_del_tramo(movimiento["precio_inicio"], movimiento["precio_fin"])
        nivel_precio = niveles.get(fibo_nivel)
        if nivel_precio is not None:
            del_lado_ganador = (
                nivel_precio > precio_apertura if tipo == "compra"
                else nivel_precio < precio_apertura
            )
            alcanzado = (
                precio_actual >= nivel_precio if tipo == "compra"
                else precio_actual <= nivel_precio
            )
            if del_lado_ganador and alcanzado:
                return True, "fibo"

    if not velas_por_temporalidad:
        return False, None

    # --- 2) Nueva estructura confirmada a favor en alguna micro ---
    micro = {
        tf: df for tf, df in velas_por_temporalidad.items()
        if clasificar_temporalidad(tf, grupos_temporalidad) == "micro"
    }
    for tf_micro, df_micro in micro.items():
        zona, pivotes = _ultima_zona_confirmada_por_tf(df_micro, tf_micro, tolerancia_pct)
        if zona is None or zona["tipo"] != tipo:
            continue
        bos = confirmar_bos(df_micro, pivotes, zona)
        if bos is not None and bos.get("confirmado"):
            return True, f"nueva_estructura_{tf_micro}"

    # --- 3) Pérdida de alineación entre macro e intermedia ---
    intermedia = {
        tf: df for tf, df in velas_por_temporalidad.items()
        if clasificar_temporalidad(tf, grupos_temporalidad) == "intermedia"
    }
    macro = {
        tf: df for tf, df in velas_por_temporalidad.items()
        if clasificar_temporalidad(tf, grupos_temporalidad) == "macro"
    }
    if intermedia and macro:
        # Si ya no hay NINGUNA zona macro apuntando al tipo de la operación,
        # o ninguna intermedia la respalda, se perdió la alineación.
        hay_macro_a_favor = any(
            (zona := _ultima_zona_confirmada_por_tf(df_m, tf_m, tolerancia_pct)[0]) is not None
            and zona["tipo"] == tipo
            for tf_m, df_m in macro.items()
        )
        if not hay_macro_a_favor or not _hay_alineacion_intermedia(intermedia, tipo, tolerancia_pct):
            return True, "perdida_alineacion"

    return False, None


def dividir_riesgo_entre_operaciones(oportunidades, riesgo_total_valor):
    """
    Reparte 'riesgo_total_valor' (ej. 1% del balance, o $60 fijos) en
    PARTES IGUALES entre todas las oportunidades encontradas por
    escanear_todas_las_oportunidades(). Le agrega a cada dict el campo
    "riesgo_valor" con la parte que le toca — listo para pasarlo a
    riesgo.calcular_lotaje(..., valor=oportunidad["riesgo_valor"]).

    Ejemplo del usuario: riesgo total 1% del balance, se encuentran 2
    oportunidades (una en 5m, otra en 2h) -> cada una recibe 0.5%.
    Si solo hubiera 1 oportunidad, esa recibe el 1% completo. Si no hay
    ninguna, regresa la lista vacía tal cual (nada que operar).
    """
    n = len(oportunidades)
    if n == 0:
        return oportunidades
    parte = riesgo_total_valor / n
    for op in oportunidades:
        op["riesgo_valor"] = parte
    return oportunidades