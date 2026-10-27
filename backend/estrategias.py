# ============================================================
# TUS ESTRATEGIAS
# ============================================================
# Agrega aquí tus propias funciones. Cada una recibe (df, params)
# y devuelve "compra", "venta", o None.
#
# Puedes usar las herramientas de herramientas.py para armar
# tus condiciones, ej:
#   from herramientas import cruce_hacia_arriba, esta_arriba_de
#
# Al final, regístrala en el diccionario ESTRATEGIAS para que
# aparezca automáticamente en el selector de la web.
# ============================================================

from . import herramientas as h
from . import estructura as est
import pandas as pd

COLUMNAS_DISPONIBLES = ["close", "open", "high", "low", "rsi",
                         "sma_10", "sma_20", "sma_50", "sma_100", "sma_200"]

# ============================================================
# BLOQUES: piezas de "comportamiento de mercado" con las que se
# arma una estrategia sin escribir código, tipo formulario.
# Cada bloque:
#   - "nombre": cómo se ve en el selector
#   - "explicacion": qué significa en español, para cualquiera
#   - "parametros": lista de campos que pide (para dibujar el formulario)
#   - "evaluar(df, params)": devuelve True/False usando herramientas.py
# ============================================================

BLOQUES = {
    "rompimiento_arriba": {
        "nombre": "Rompimiento hacia arriba",
        "explicacion": "El precio (o el valor elegido) acaba de superar el punto más alto de las últimas N velas. Es la versión numérica de 've un rompimiento al alza'.",
        "parametros": [
            {"nombre": "columna", "etiqueta": "Sobre qué medir", "tipo": "columna", "default": "close"},
            {"nombre": "velas", "etiqueta": "Últimas cuántas velas", "tipo": "numero", "default": 20},
        ],
        "evaluar": lambda df, p: h.esta_arriba_de(
            df, p.get("columna", "close"),
            h.maximo_de_ultimas_velas(df, p.get("columna", "close"), int(p.get("velas", 20))),
        ),
    },
    "rompimiento_abajo": {
        "nombre": "Rompimiento hacia abajo",
        "explicacion": "El precio (o el valor elegido) acaba de caer por debajo del punto más bajo de las últimas N velas.",
        "parametros": [
            {"nombre": "columna", "etiqueta": "Sobre qué medir", "tipo": "columna", "default": "close"},
            {"nombre": "velas", "etiqueta": "Últimas cuántas velas", "tipo": "numero", "default": 20},
        ],
        "evaluar": lambda df, p: h.esta_abajo_de(
            df, p.get("columna", "close"),
            h.minimo_de_ultimas_velas(df, p.get("columna", "close"), int(p.get("velas", 20))),
        ),
    },
    "cruce_arriba": {
        "nombre": "Cruce hacia arriba",
        "explicacion": "Una línea (ej. una media rápida) acaba de cruzar por ARRIBA a otra línea (ej. una media lenta).",
        "parametros": [
            {"nombre": "rapida", "etiqueta": "Línea rápida", "tipo": "columna", "default": "sma_20"},
            {"nombre": "lenta", "etiqueta": "Línea lenta", "tipo": "columna", "default": "sma_50"},
        ],
        "evaluar": lambda df, p: h.cruce_hacia_arriba(df, p.get("rapida", "sma_20"), p.get("lenta", "sma_50")),
    },
    "cruce_abajo": {
        "nombre": "Cruce hacia abajo",
        "explicacion": "Una línea (ej. una media rápida) acaba de cruzar por ABAJO a otra línea (ej. una media lenta).",
        "parametros": [
            {"nombre": "rapida", "etiqueta": "Línea rápida", "tipo": "columna", "default": "sma_20"},
            {"nombre": "lenta", "etiqueta": "Línea lenta", "tipo": "columna", "default": "sma_50"},
        ],
        "evaluar": lambda df, p: h.cruce_hacia_abajo(df, p.get("rapida", "sma_20"), p.get("lenta", "sma_50")),
    },
    "esta_arriba_de": {
        "nombre": "Está arriba de",
        "explicacion": "El valor elegido está, ahora mismo, por arriba de otra línea o de un número fijo.",
        "parametros": [
            {"nombre": "columna", "etiqueta": "Valor", "tipo": "columna", "default": "close"},
            {"nombre": "referencia", "etiqueta": "Arriba de qué (línea o número)", "tipo": "columna_o_numero", "default": "sma_50"},
        ],
        "evaluar": lambda df, p: h.esta_arriba_de(df, p.get("columna", "close"), _num_o_columna(p.get("referencia"))),
    },
    "esta_abajo_de": {
        "nombre": "Está abajo de",
        "explicacion": "El valor elegido está, ahora mismo, por debajo de otra línea o de un número fijo.",
        "parametros": [
            {"nombre": "columna", "etiqueta": "Valor", "tipo": "columna", "default": "close"},
            {"nombre": "referencia", "etiqueta": "Abajo de qué (línea o número)", "tipo": "columna_o_numero", "default": "sma_50"},
        ],
        "evaluar": lambda df, p: h.esta_abajo_de(df, p.get("columna", "close"), _num_o_columna(p.get("referencia"))),
    },
    "racha_arriba": {
        "nombre": "Lleva rato arriba de",
        "explicacion": "Mide fuerza de tendencia: cuántas velas seguidas el valor ha estado arriba de una referencia, y compara ese conteo contra un mínimo.",
        "parametros": [
            {"nombre": "columna", "etiqueta": "Valor", "tipo": "columna", "default": "close"},
            {"nombre": "referencia", "etiqueta": "Arriba de qué", "tipo": "columna_o_numero", "default": "sma_50"},
            {"nombre": "minimo_velas", "etiqueta": "Mínimo de velas seguidas", "tipo": "numero", "default": 3},
        ],
        "evaluar": lambda df, p: h.cuantas_velas_lleva_arriba(
            df, p.get("columna", "close"), _num_o_columna(p.get("referencia"))
        ) >= int(p.get("minimo_velas", 3)),
    },
    "estructura_spotplus": {
        "nombre": "Estructura de mercado (SpotPlus) - señal de compra",
        "explicacion": "Detecta pivotes (picos/valles), clasifica HH/HL/LH/LL, y revisa si el "
                       "precio actual está tocando una zona de espera (HL) para compra, idealmente "
                       "confluyendo con un nivel de Fibonacci del tramo anterior.",
        "parametros": [
            {"nombre": "pivot_len", "etiqueta": "Sensibilidad de pivotes (velas a cada lado)", "tipo": "numero", "default": 5},
            {"nombre": "tolerancia_pct", "etiqueta": "Tolerancia de la zona (%)", "tipo": "numero", "default": 0.15},
        ],
        "evaluar": lambda df, p: (
            (resultado := est.ultima_zona_activa(df, int(p.get("pivot_len", 5)), float(p.get("tolerancia_pct", 0.15))))
            is not None and resultado["tipo"] == "compra"
        ),
    },
    "estructura_spotplus_venta": {
        "nombre": "Estructura de mercado (SpotPlus) - señal de venta",
        "explicacion": "Igual que el bloque de compra, pero para zonas LH (máximo más bajo) — "
                       "posible zona de espera para vender.",
        "parametros": [
            {"nombre": "pivot_len", "etiqueta": "Sensibilidad de pivotes (velas a cada lado)", "tipo": "numero", "default": 5},
            {"nombre": "tolerancia_pct", "etiqueta": "Tolerancia de la zona (%)", "tipo": "numero", "default": 0.15},
        ],
        "evaluar": lambda df, p: (
            (resultado := est.ultima_zona_activa(df, int(p.get("pivot_len", 5)), float(p.get("tolerancia_pct", 0.15))))
            is not None and resultado["tipo"] == "venta"
        ),
    },
    "estructura_spotplus_bos_compra": {
        "nombre": "Estructura de mercado (SpotPlus) - COMPRA confirmada (BOS)",
        "explicacion": "No basta con tocar la zona de espera (HL): exige que el precio ya haya "
                       "roto por ARRIBA el pico anterior a esa zona (rompimiento de estructura / "
                       "BOS), confirmando que la tendencia se reanudó antes de dar la señal.",
        "parametros": [
            {"nombre": "pivot_len", "etiqueta": "Sensibilidad de pivotes de entrada", "tipo": "numero", "default": 5},
            {"nombre": "max_velas_desde_zona", "etiqueta": "Máx. velas desde que se formó la zona (0 = sin límite)", "tipo": "numero", "default": 0},
            {"nombre": "modo_liquidez", "etiqueta": "Uso de liquidez (desactivado / evitar / esperar_barrida / confluencia)", "tipo": "texto", "default": "desactivado"},
        ],
        "evaluar": lambda df, p: (
            (resultado := est.señal_estructura(
                df,
                pivot_len=int(p.get("pivot_len", 5)),
                max_velas_desde_zona=(int(p["max_velas_desde_zona"]) if float(p.get("max_velas_desde_zona", 0)) > 0 else None),
                modo_liquidez=p.get("modo_liquidez"),
            )) is not None and resultado["tipo"] == "compra"
        ),
    },
    "estructura_spotplus_bos_venta": {
        "nombre": "Estructura de mercado (SpotPlus) - VENTA confirmada (BOS)",
        "explicacion": "Igual que el bloque de compra confirmada, pero exige que el precio ya "
                       "haya roto por DEBAJO el valle anterior a la zona LH (BOS bajista).",
        "parametros": [
            {"nombre": "pivot_len", "etiqueta": "Sensibilidad de pivotes de entrada", "tipo": "numero", "default": 5},
            {"nombre": "max_velas_desde_zona", "etiqueta": "Máx. velas desde que se formó la zona (0 = sin límite)", "tipo": "numero", "default": 0},
            {"nombre": "modo_liquidez", "etiqueta": "Uso de liquidez (desactivado / evitar / esperar_barrida / confluencia)", "tipo": "texto", "default": "desactivado"},
        ],
        "evaluar": lambda df, p: (
            (resultado := est.señal_estructura(
                df,
                pivot_len=int(p.get("pivot_len", 5)),
                max_velas_desde_zona=(int(p["max_velas_desde_zona"]) if float(p.get("max_velas_desde_zona", 0)) > 0 else None),
                modo_liquidez=p.get("modo_liquidez"),
            )) is not None and resultado["tipo"] == "venta"
        ),
    },
    "variacion_mayor_a": {
        "nombre": "Variación % en N velas es mayor a",
        "explicacion": "Qué tanto cambió (en porcentaje) el valor en las últimas N velas, comparado contra un mínimo. Útil para detectar movimientos fuertes/impulsivos.",
        "parametros": [
            {"nombre": "columna", "etiqueta": "Valor", "tipo": "columna", "default": "close"},
            {"nombre": "velas_atras", "etiqueta": "Comparado con hace cuántas velas", "tipo": "numero", "default": 5},
            {"nombre": "porcentaje_minimo", "etiqueta": "% mínimo (puede ser negativo)", "tipo": "numero", "default": 1.0},
        ],
        "evaluar": lambda df, p: h.variacion_porcentual(
            df, p.get("columna", "close"), int(p.get("velas_atras", 5))
        ) > float(p.get("porcentaje_minimo", 1.0)),
    },
}


def evaluar_estructura_dinamica(df, params, df_macro=None):
    """
    A diferencia de los bloques de arriba (que solo devuelven True/False),
    esta función devuelve la señal COMPLETA de est.señal_estructura():
    tipo, precio de entrada, SL (detrás de la zona) y TP (% del
    movimiento macro). Es la que usan backtesting.py y servidor.py cuando
    la estrategia activa es "estructura_dinamica", para poder abrir cada
    operación con su propio SL/TP calculado en vez de los valores fijos
    del formulario de riesgo.

    df_macro (opcional): velas reales de la temporalidad PRINCIPAL (ej. H1)
    para el movimiento macro, separadas de 'df' (temporalidad de entrada,
    ej. M15). Si no se pasa, cae en la aproximación de un solo timeframe.

    Devuelve el dict de señal_estructura(), o None si no hay señal.

    params["modo_liquidez"] (opcional, string): "evitar" | "esperar_barrida" |
    "confluencia" | None/"desactivado". Ver señal_estructura() /
    filtrar_por_liquidez() en estructura.py para el detalle de cada modo.
    """
    return est.señal_estructura(
        df,
        pivot_len=int(params.get("pivot_len", 5)),
        pivot_len_macro=int(params.get("pivot_len_macro", 15)),
        porcentaje_tp=float(params.get("porcentaje_tp", 0.8)),
        sl_buffer_pct=float(params.get("sl_buffer_pct", 0.1)),
        max_velas_desde_zona=(
            int(params["max_velas_desde_zona"])
            if float(params.get("max_velas_desde_zona", 0)) > 0
            else None
        ),
        df_macro=df_macro,
        minimo_ratio_tp=(
            float(params["minimo_ratio_tp"])
            if float(params.get("minimo_ratio_tp", 0)) > 0
            else None
        ),
        extensiones_liquidez=params.get("extensiones_liquidez"),
        modo_liquidez=params.get("modo_liquidez"),
        tolerancia_pct_liquidez=float(params.get("tolerancia_pct_liquidez", 0.15)),
    )


def _num_o_columna(valor):
    """Si 'valor' se puede convertir a número, lo usa como número fijo;
    si no, lo trata como nombre de columna (ej. 'sma_50')."""
    if valor is None:
        return 0
    try:
        return float(valor)
    except (TypeError, ValueError):
        return valor


def evaluar_bloques(df, condiciones, operador_logico="and"):
    """
    Evalúa una lista de bloques armados desde el formulario web.
    Cada condición: {"bloque": "rompimiento_arriba", "parametros": {"columna": "close", "velas": 20}}
    """
    if not condiciones:
        return False

    resultados = []
    for cond in condiciones:
        clave = cond.get("bloque")
        params = cond.get("parametros", {}) or {}
        bloque = BLOQUES.get(clave)
        if bloque is None:
            resultados.append(False)
            continue
        try:
            resultados.append(bool(bloque["evaluar"](df, params)))
        except Exception as e:
            print(f"Error evaluando bloque '{clave}': {e}")
            resultados.append(False)

    if operador_logico == "or":
        return any(resultados)
    return all(resultados)


def evaluar_estrategia_bloques(df, condiciones_compra, operador_compra, condiciones_venta, operador_venta):
    """Evalúa los bloques de compra y venta armados en el formulario y devuelve la señal."""
    if evaluar_bloques(df, condiciones_compra, operador_compra):
        return "compra"
    if evaluar_bloques(df, condiciones_venta, operador_venta):
        return "venta"
    return None


def evaluar_condiciones(df, condiciones, operador_logico="and"):
    """
    Evalúa una lista de condiciones armadas desde la web.
    Cada condición es un dict: {"indicador": "rsi", "comparador": "<", "valor": 30}
    indicador puede ser: "rsi", "close", "sma_20", "sma_50", etc.
    comparador puede ser: "<", ">", "<=", ">="
    valor puede ser un número, o el nombre de otra columna (ej. "sma_50") para comparar dos indicadores entre sí.
    """
    ultima = df.iloc[-1]
    resultados = []

    for cond in condiciones:
        indicador = cond.get("indicador")
        comparador = cond.get("comparador")
        valor = cond.get("valor")

        if indicador not in df.columns:
            resultados.append(False)
            continue

        valor_izquierda = ultima[indicador]

        # si "valor" es el nombre de otra columna existente, comparamos columna contra columna
        if isinstance(valor, str) and valor in df.columns:
            valor_derecha = ultima[valor]
        else:
            try:
                valor_derecha = float(valor)
            except (TypeError, ValueError):
                resultados.append(False)
                continue

        if comparador == "<":
            resultados.append(valor_izquierda < valor_derecha)
        elif comparador == ">":
            resultados.append(valor_izquierda > valor_derecha)
        elif comparador == "<=":
            resultados.append(valor_izquierda <= valor_derecha)
        elif comparador == ">=":
            resultados.append(valor_izquierda >= valor_derecha)
        else:
            resultados.append(False)

    if not resultados:
        return False

    if operador_logico == "or":
        return any(resultados)
    return all(resultados)


def evaluar_condicion_texto(df, texto_condicion):
    """
    Evalúa una condición escrita como texto libre por el usuario, ej:
    "ultima['rsi'] < 30 and ultima['close'] > ultima['sma_50']"
    Tiene acceso a: ultima, anterior, df
    """
    if not texto_condicion or not texto_condicion.strip():
        return False

    ultima = df.iloc[-1]
    anterior = df.iloc[-2]

    contexto_seguro = {
        "ultima": ultima,
        "anterior": anterior,
        "df": df,
        "abs": abs,
        "min": min,
        "max": max,
        "__builtins__": {},
    }

    try:
        return bool(eval(texto_condicion, contexto_seguro))
    except Exception as e:
        print(f"Error evaluando condición '{texto_condicion}': {e}")
        return False


def evaluar_estrategia_texto(df, texto_compra, texto_venta):
    """Evalúa dos condiciones de texto libre (compra y venta) y devuelve la señal."""
    if evaluar_condicion_texto(df, texto_compra):
        return "compra"
    if evaluar_condicion_texto(df, texto_venta):
        return "venta"
    return None


class _EstructuraConGrupos:
    """Envuelve el módulo estructura para que escanear_multitemporalidad() y
    escanear_todas_las_oportunidades(), llamadas desde el código libre del
    usuario como est.escanear_multitemporalidad(...), usen automáticamente
    los grupos de temporalidad configurados en el panel
    (config_bot["grupos_temporalidad"]) sin que el usuario tenga que pasar
    ese argumento a mano ni editar estrategias ya guardadas."""

    def __init__(self, modulo_estructura, grupos_temporalidad, zonas_permitidas=None):
        self._est = modulo_estructura
        self._grupos = grupos_temporalidad or None
        # Zonas de espera permitidas (flip/ev/obc/fractal/fractal2) elegidas en el
        # panel para esta estrategia. Vacío/None = automático (todas).
        self._zonas = list(zonas_permitidas) if zonas_permitidas else None

    def escanear_multitemporalidad(self, velas_por_temporalidad, **kwargs):
        kwargs.setdefault("grupos_temporalidad", self._grupos)
        kwargs.setdefault("bloques_permitidos", self._zonas)
        return self._est.escanear_multitemporalidad(velas_por_temporalidad, **kwargs)

    def escanear_todas_las_oportunidades(self, velas_por_temporalidad, **kwargs):
        kwargs.setdefault("grupos_temporalidad", self._grupos)
        kwargs.setdefault("bloques_permitidos", self._zonas)
        return self._est.escanear_todas_las_oportunidades(velas_por_temporalidad, **kwargs)

    def __getattr__(self, nombre):
        return getattr(self._est, nombre)


def evaluar_codigo_libre(
    df,
    codigo,
    df_macro=None,
    velas_por_temporalidad=None,
    grupos_temporalidad=None,
    roles_temporalidad=None,
    parametros=None,
    zonas_permitidas=None,
):
    """
    Ejecuta UNA sola estrategia completa, escrita libremente en Python por el usuario.
    No obliga a usar ningún indicador: el usuario decide con qué compara.

    Tiene acceso a: df (todas las velas), ultima (última vela), anterior (vela anterior).
    Si la estrategia activó "multi-temporalidad", también tiene acceso a df_macro
    (velas de la temporalidad macro configurada) — si no, df_macro es None.

    velas_por_temporalidad (opcional): dict {"M5": df, "H1": df, "H4": df, ...}
    con TODAS las temporalidades que el bot tenga configuradas para escanear
    (ver servidor.py -> config_bot["temporalidades_escaneo"]). Se expone en el
    código libre como la variable `velas_por_temporalidad`, pensada para usarse
    con est.escanear_multitemporalidad(velas_por_temporalidad). Si no hay
    ninguna configurada, llega como {} (diccionario vacío, no None, para que
    est.escanear_multitemporalidad(velas_por_temporalidad) no truene).

    El código debe dejar el resultado en una variable llamada 'senal', con el valor
    "compra", "venta", o None. Ejemplo:

        if ultima['close'] > anterior['close']:
            senal = "compra"
        elif ultima['close'] < anterior['close']:
            senal = "venta"
        else:
            senal = None
    """
    if not codigo or not codigo.strip():
        return None

    ultima = df.iloc[-1]
    anterior = df.iloc[-2]

    from . import estructura as est_modulo
    est = _EstructuraConGrupos(est_modulo, grupos_temporalidad, zonas_permitidas)

    def importar_permitido(nombre, globals=None, locals=None, fromlist=(), level=0):
        """Permite solo los imports que necesita la biblioteca de estrategias.

        El código guardado se ejecuta con builtins restringidos; sin esta
        función incluso ``import pandas as pd`` falla con ``__import__ not
        found``. No se habilitan módulos del sistema como os, subprocess o
        pathlib.
        """
        if nombre == "pandas" and level == 0:
            return pd
        raise ImportError(f"Módulo no permitido en una estrategia: {nombre}")

    contexto = {
        "df": df,
        "df_macro": df_macro,
        "velas_por_temporalidad": velas_por_temporalidad or {},
        "roles_temporalidad": roles_temporalidad or {},
        "zonas_permitidas": list(zonas_permitidas or []),
        "parametros": parametros or {},
        "ultima": ultima,
        "anterior": anterior,
        "senal": None,
        "est": est,
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
        "len": len,
        "pd": pd,
        "__builtins__": {"__import__": importar_permitido},
    }

    try:
        exec(codigo, contexto)
    except Exception as e:
        print(f"Error ejecutando estrategia: {e}")
        return None

    resultado = contexto.get("senal")

    # Caso "varias operaciones a la vez" (ej. est.escanear_todas_las_oportunidades
    # + est.dividir_riesgo_entre_operaciones): el código deja una lista de dicts
    # en la variable 'operaciones', cada uno con tipo/sl/tp/riesgo_valor propios.
    operaciones = contexto.get("operaciones")
    if isinstance(operaciones, list) and operaciones:
        validas = []
        for op in operaciones:
            if not isinstance(op, dict) or op.get("tipo") not in ("compra", "venta"):
                continue
            validas.append({
                "id": op.get("id", f"{op['tipo']}_{op.get('sl')}_{op.get('tp')}"),
                "tipo": op["tipo"],
                "sl": float(op.get("sl") or 0.0),
                "tp": float(op.get("tp") or 0.0),
                "precio_entrada": float(op.get("precio_entrada") or ultima["close"]),
                "riesgo_valor": op.get("riesgo_valor"),  # None = usa el modo de riesgo global de la estrategia
                "volumen": op.get("volumen"),
                "temporalidad_principal": op.get("temporalidad_principal"),
                "grupo_principal": op.get("grupo_principal"),
                "bloque": op.get("bloque"),
                "escala": op.get("escala"),
                "confirmado_en": op.get("confirmado_en"),
                "tp_liquidez": op.get("tp_liquidez"),
                "tipo_ruptura": op.get("tipo_ruptura"),
                "patron_vela": op.get("patron_vela"),
                "ruptura_fuerte": op.get("ruptura_fuerte"),
                "confluencias_micro": op.get("confluencias_micro"),
                "temporalidades_micro": op.get("temporalidades_micro"),
                "fibo_pct": op.get("fibo_pct"),
                "liquidez_externa": op.get("liquidez_externa"),
                "liquidez_interna": op.get("liquidez_interna"),
                "liquidez_barrida": op.get("liquidez_barrida"),
                "acumulacion": op.get("acumulacion"),
            })
        return validas if validas else None

    if resultado not in ("compra", "venta", None):
        return None

    sl_propio = contexto.get("sl")
    tp_propio = contexto.get("tp")
    if resultado is not None and (sl_propio is not None or tp_propio is not None):
        # El código definió su propio sl/tp (ej. tomado de est.señal_estructura) —
        # se devuelve como dict para que servidor.py/backtesting.py los usen en
        # vez de los campos fijos de SL/TP del formulario.
        return {
            "tipo": resultado,
            "sl": float(sl_propio) if sl_propio else 0.0,
            "tp": float(tp_propio) if tp_propio else 0.0,
            "precio_entrada": float(ultima["close"]),
        }
    return resultado


# ============================================================
# TUS ESTRATEGIAS EN CÓDIGO (opcional, si prefieres escribir Python)
# ============================================================
# Si algún día quieres una lógica más compleja que el constructor
# visual no puede armar (ej. contar velas, cruces), la escribes aquí
# igual que antes y la agregas a ESTRATEGIAS.

ESTRATEGIAS = {
    "estructura_dinamica": {
        "nombre": "Estructura de mercado (SpotPlus) - COMPLETA con SL/TP propios",
        "explicacion": (
            "La versión completa: exige BOS confirmado (rompimiento del pico/valle "
            "anterior a la zona de espera) antes de dar la señal, coloca el SL justo "
            "detrás de la zona (HL/LH), y proyecta el TP como un % del movimiento "
            "MACRO más reciente (no del pullback de entrada). Ignora los campos de "
            "SL/TP en puntos del formulario de riesgo: cada operación usa su propio "
            "SL/TP calculado."
        ),
        "parametros": {
            "pivot_len": 5,            # sensibilidad de los pivotes de ENTRADA
            "pivot_len_macro": 15,     # sensibilidad de los pivotes del movimiento MACRO
            "porcentaje_tp": 0.8,      # % de la distancia macro que se proyecta como TP
            "sl_buffer_pct": 0.1,      # % de colchón entre el SL y la zona HL/LH
            "max_velas_desde_zona": 0, # 0 = sin límite; si no, invalida zonas muy viejas
        },
        # No se usa directamente (servidor.py y backtesting.py llaman a
        # evaluar_estructura_dinamica antes de llegar aquí), pero se deja
        # por si algo llama a evaluar_estrategia() genérico con este nombre.
        "funcion": lambda df, p: (
            (resultado := evaluar_estructura_dinamica(df, p)) is not None and resultado["tipo"] or None
        ),
    },
}


def evaluar_estrategia(df, nombre_estrategia, params):
    config = ESTRATEGIAS.get(nombre_estrategia)
    if config is None:
        return None
    return config["funcion"](df, params)