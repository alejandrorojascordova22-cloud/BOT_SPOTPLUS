# ============================================================
# HERRAMIENTAS PARA FORMALIZAR ESTRATEGIAS
# ============================================================
# Funciones reutilizables. Impórtalas en tu estrategia con:
#   from herramientas import cruce_hacia_arriba, cruce_hacia_abajo, ...
#
# Todas trabajan sobre "df" (tus velas con indicadores) y te
# devuelven True/False, para que armes tu condición combinando
# las que necesites con "and" / "or".
# ============================================================


def cruce_hacia_arriba(df, columna_rapida, columna_lenta):
    """True si columna_rapida acaba de cruzar por ARRIBA a columna_lenta."""
    anterior = df.iloc[-2]
    ultima = df.iloc[-1]
    return anterior[columna_rapida] <= anterior[columna_lenta] and ultima[columna_rapida] > ultima[columna_lenta]


def cruce_hacia_abajo(df, columna_rapida, columna_lenta):
    """True si columna_rapida acaba de cruzar por ABAJO a columna_lenta."""
    anterior = df.iloc[-2]
    ultima = df.iloc[-1]
    return anterior[columna_rapida] >= anterior[columna_lenta] and ultima[columna_rapida] < ultima[columna_lenta]


def esta_arriba_de(df, columna, referencia):
    """True si 'columna' está arriba de 'referencia' en la última vela.
    'referencia' puede ser otra columna (texto) o un número fijo."""
    ultima = df.iloc[-1]
    valor_ref = ultima[referencia] if isinstance(referencia, str) else referencia
    return ultima[columna] > valor_ref


def esta_abajo_de(df, columna, referencia):
    """True si 'columna' está abajo de 'referencia' en la última vela."""
    ultima = df.iloc[-1]
    valor_ref = ultima[referencia] if isinstance(referencia, str) else referencia
    return ultima[columna] < valor_ref


def distancia_entre(df, columna_a, columna_b):
    """Diferencia absoluta entre dos columnas en la última vela.
    Útil para condiciones tipo 'el precio está a menos de X puntos de la media'."""
    ultima = df.iloc[-1]
    return abs(ultima[columna_a] - ultima[columna_b])


def maximo_de_ultimas_velas(df, columna, cantidad):
    """El valor máximo de 'columna' en las últimas N velas (sin contar la actual).
    Útil para estrategias de ruptura (breakout)."""
    return df[columna].iloc[-(cantidad + 1):-1].max()


def minimo_de_ultimas_velas(df, columna, cantidad):
    """El valor mínimo de 'columna' en las últimas N velas (sin contar la actual)."""
    return df[columna].iloc[-(cantidad + 1):-1].min()


def cuantas_velas_lleva_arriba(df, columna, referencia, maximo_a_revisar=20):
    """Cuenta cuántas velas consecutivas (desde la más reciente hacia atrás)
    'columna' ha estado arriba de 'referencia'. Útil para medir fuerza de tendencia."""
    contador = 0
    for i in range(1, maximo_a_revisar + 1):
        fila = df.iloc[-i]
        valor_ref = fila[referencia] if isinstance(referencia, str) else referencia
        if fila[columna] > valor_ref:
            contador += 1
        else:
            break
    return contador


def variacion_porcentual(df, columna, velas_atras=1):
    """Cambio porcentual de 'columna' entre la vela actual y N velas atrás.
    Ej: variacion_porcentual(df, 'close', 5) -> % que cambió el precio en 5 velas."""
    actual = df[columna].iloc[-1]
    pasado = df[columna].iloc[-1 - velas_atras]
    if pasado == 0:
        return 0
    return ((actual - pasado) / pasado) * 100


# ============================================================
# EJEMPLO DE USO EN UNA ESTRATEGIA (así las combinas)
# ============================================================
#
# from herramientas import cruce_hacia_arriba, esta_arriba_de
#
# def mi_estrategia(df, params):
#     if cruce_hacia_arriba(df, "sma_20", "sma_50") and esta_arriba_de(df, "close", "sma_50"):
#         return "compra"
#     return None
