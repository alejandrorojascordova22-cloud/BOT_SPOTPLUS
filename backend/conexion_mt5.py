try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
import functools
import threading
import pandas as pd

# Bloque seguro para importar MetaTrader5 en Linux o Windows


from .config import (
    MT5_LOGIN, 
    MT5_PASSWORD, 
    MT5_SERVER, 
    SIMBOLO, 
    TIMEFRAME_STR, 
    TERMINAL_PATH, 
    NOMBRE_CUENTA
)
TIMEFRAMES = {
    k: getattr(mt5, "TIMEFRAME_" + k, None)
    for k in ["M1","M3","M5","M15","M30","H1","H2","H4","D1","W1","MN1"]
}

# La librería MetaTrader5 NO es segura para llamarse desde varios hilos al
# mismo tiempo. Este backend tiene el hilo de fondo ciclo_bot() (revisa
# precios/velas cada 60s) además del hilo que atiende cada request HTTP
# (FastAPI corre las funciones "def" normales en un threadpool). Si ambos
# llaman a mt5.* en el mismo instante, MT5 puede cortar la conexión sin dar
# respuesta -> el navegador lo ve como "Failed to fetch"/CORS aunque el
# problema real no tiene nada que ver con CORS. Este candado obliga a que
# solo un hilo hable con MT5 a la vez.
_mt5_lock = threading.RLock()


def _con_candado(func):
    """Envuelve una función para que TODO su cuerpo corra con el candado de
    MT5 tomado (ninguna otra función decorada puede correr al mismo tiempo)."""
    @functools.wraps(func)
    def envoltura(*args, **kwargs):
        with _mt5_lock:
            return func(*args, **kwargs)
    return envoltura


@_con_candado
def conectar():
    # TERMINAL_PATH viene de config.py: si este proceso arranco con
    # CUENTA_ID=algo, apunta al terminal MT5 propio de esa cuenta (cada
    # cuenta necesita su propia instalacion de MT5, ya que un mismo
    # terminal64.exe solo puede iniciar sesion en una cuenta a la vez).
    if not mt5.initialize(path=TERMINAL_PATH):
        print(f"[{NOMBRE_CUENTA}] Error al iniciar MT5:", mt5.last_error())
        return False

    autorizado = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if not autorizado:
        print(f"[{NOMBRE_CUENTA}] Error al hacer login:", mt5.last_error())
        return False

    print(f"[{NOMBRE_CUENTA}] Conectado a MT5 - cuenta {MT5_LOGIN}")
    return True

@_con_candado
def desconectar():
    mt5.shutdown()


@_con_candado
def verificar_conexion():
    """Watchdog de reconexión: antes, si el terminal MT5 se caía o perdía la
    sesión a mitad de la corrida, nada lo notaba hasta que una llamada
    cualquiera fallaba (o directamente no se notaba y el bot se quedaba
    "vivo" pero sin poder operar). Se llama al inicio de cada vuelta del
    ciclo del bot; si detecta que la conexión ya no responde, reconecta sola.
    Devuelve True si al final de la función la conexión está en pie."""
    info_terminal = mt5.terminal_info()
    info_cuenta = mt5.account_info()
    if info_terminal is not None and info_cuenta is not None:
        return True

    print(f"[{NOMBRE_CUENTA}] Conexión con MT5 caída, reconectando...", mt5.last_error())
    mt5.shutdown()
    return conectar()


def _seleccionar_simbolo(simbolo):
    if mt5.symbol_select(simbolo, True):
        return True

    print(
        f"[{NOMBRE_CUENTA}] No se pudo seleccionar el símbolo {simbolo} en Market Watch:",
        mt5.last_error(),
    )
    return False


def _copiar_velas_con_reconexion(simbolo, timeframe, cantidad):
    if not _seleccionar_simbolo(simbolo):
        return None

    velas = mt5.copy_rates_from_pos(simbolo, timeframe, 0, cantidad)
    if velas is not None and len(velas) > 0:
        return velas

    if mt5.last_error()[0] != -10001:
        return None

    mt5.shutdown()
    if not conectar():
        return None
    if not _seleccionar_simbolo(simbolo):
        return None

    velas = mt5.copy_rates_from_pos(simbolo, timeframe, 0, cantidad)
    return velas if velas is not None and len(velas) > 0 else None


MENSAJES_RETCODE = {
    10018: "Mercado cerrado para este símbolo ahora mismo",
    10019: "Fondos insuficientes para abrir esta operación",
    10021: "El bróker no tiene cotización disponible para este símbolo ahora mismo",
    10027: (
        "MetaTrader 5 bloqueó la operación porque AutoTrading está desactivado "
        "en la terminal. En la misma terminal MT5 conectada a esta cuenta, activa "
        "el botón «Algo Trading» y revisa Herramientas > Opciones > Asesores "
        "Expertos > «Permitir trading algorítmico». Después vuelve a intentar. "
        "SpotPlus no puede activar esta opción por su cuenta."
    ),
}


def _error_orden(resultado):
    retcode = resultado.retcode
    mensaje = MENSAJES_RETCODE.get(
        retcode,
        f"Retcode {retcode}: {resultado.comment}",
    )
    return {"ok": False, "error": mensaje, "retcode": retcode}


@_con_candado
def obtener_velas(simbolo=SIMBOLO, cantidad=200):
    timeframe = TIMEFRAMES.get(TIMEFRAME_STR, mt5.TIMEFRAME_M5)
    velas = _copiar_velas_con_reconexion(simbolo, timeframe, cantidad)

    if velas is None:
        print("Error al traer velas:", mt5.last_error())
        return None

    df = pd.DataFrame(velas)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

@_con_candado
def obtener_velas_multi(simbolo=SIMBOLO, timeframe_entrada=TIMEFRAME_STR, timeframe_macro="H1",
                         cantidad_entrada=200, cantidad_macro=200):
    """
    Trae DOS series de velas reales al mismo tiempo:
    - df_entrada: la temporalidad de confirmación/entradas (ej. M15).
    - df_macro: la temporalidad PRINCIPAL real (ej. H1), usada solo para
      medir el movimiento macro (el "ratio" del TP), NUNCA para entrar.

    Esto es lo que necesita señal_estructura(df, df_macro=...) para hacer
    multi-timeframe de verdad (no una aproximación de un solo timeframe).
    Devuelve (df_entrada, df_macro); cualquiera puede venir en None si
    falló esa consulta puntual (revisa mt5.last_error()).
    """
    tf_entrada = TIMEFRAMES.get(timeframe_entrada, mt5.TIMEFRAME_M15)
    tf_macro = TIMEFRAMES.get(timeframe_macro, mt5.TIMEFRAME_H1)

    velas_entrada = _copiar_velas_con_reconexion(simbolo, tf_entrada, cantidad_entrada)
    velas_macro = _copiar_velas_con_reconexion(simbolo, tf_macro, cantidad_macro)

    df_entrada = None
    if velas_entrada is not None:
        df_entrada = pd.DataFrame(velas_entrada)
        df_entrada["time"] = pd.to_datetime(df_entrada["time"], unit="s")
    else:
        print(f"Error al traer velas de entrada ({timeframe_entrada}):", mt5.last_error())

    df_macro = None
    if velas_macro is not None:
        df_macro = pd.DataFrame(velas_macro)
        df_macro["time"] = pd.to_datetime(df_macro["time"], unit="s")
    else:
        print(f"Error al traer velas macro ({timeframe_macro}):", mt5.last_error())

    return df_entrada, df_macro


@_con_candado
def obtener_velas_varias_temporalidades(simbolo=SIMBOLO, temporalidades=None, cantidad=200):
    """
    Trae velas reales de VARIAS temporalidades a la vez (ej. ["M5", "M15",
    "H1", "H4"]), pensado para est.escanear_multitemporalidad(), que
    necesita ver varias escalas al mismo tiempo para decidir cuál es la
    "principal" de cada señal (en vez de las 2 fijas de obtener_velas_multi).

    Devuelve un dict {"M5": df, "H1": df, ...}. Si alguna temporalidad
    falla al traerse, simplemente no aparece en el dict (no truena todo
    el escaneo por una sola temporalidad caída).
    """
    if not temporalidades:
        return {}

    resultado = {}
    for tf_str in temporalidades:
        tf_mt5 = TIMEFRAMES.get(tf_str.upper())
        if tf_mt5 is None:
            print(f"Temporalidad no reconocida, se ignora: {tf_str}")
            continue
        velas = _copiar_velas_con_reconexion(simbolo, tf_mt5, cantidad)
        if velas is None:
            print(f"Error al traer velas de {tf_str}:", mt5.last_error())
            continue
        df = pd.DataFrame(velas)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        resultado[tf_str.upper()] = df

    return resultado


@_con_candado
def obtener_precio_actual(simbolo=SIMBOLO):
    tick = mt5.symbol_info_tick(simbolo)
    if tick is None:
        print("Error al traer precio:", mt5.last_error())
        return None
    return {"bid": tick.bid, "ask": tick.ask}


@_con_candado
def obtener_info_cuenta():
    info = mt5.account_info()
    if info is None:
        return None
    return {
        "cuenta": info.login,
        "servidor": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "profit": info.profit,
        "margen_libre": info.margin_free,
        "apalancamiento": info.leverage,
        "moneda": info.currency,
        "es_real": info.trade_mode == 0,  # 0 = real, 1 = demo, 2 = contest
    }


@_con_candado
def obtener_posiciones_abiertas(simbolo=SIMBOLO):
    # simbolo=None -> trae TODAS las posiciones abiertas, sin filtrar por símbolo
    # (usado por los comandos de Telegram, que operan sobre cualquier activo).
    posiciones = mt5.positions_get() if simbolo is None else mt5.positions_get(symbol=simbolo)
    if posiciones is None:
        return []
    resultado = []
    for p in posiciones:
        resultado.append({
            "ticket": p.ticket,
            "simbolo": p.symbol,
            "tipo": "compra" if p.type == 0 else "venta",
            "volumen": p.volume,
            "precio_apertura": p.price_open,
            "precio_actual": p.price_current,
            "profit": p.profit,
            "sl": p.sl,
            "tp": p.tp,
            "hora_apertura": p.time,
            "comment": p.comment,
        })
    return resultado


def _razon_cierre_label(reason):
    """Normaliza la razón de cierre de MT5 a un valor simple: 'sl', 'tp' o None."""
    if reason is None:
        return None
    if hasattr(mt5, "DEAL_REASON_SL") and reason == mt5.DEAL_REASON_SL:
        return "sl"
    if hasattr(mt5, "DEAL_REASON_TP") and reason == mt5.DEAL_REASON_TP:
        return "tp"
    return None


@_con_candado
def obtener_historial_cerradas(dias=7, simbolo=SIMBOLO):
    # simbolo=None -> trae el historial de TODOS los símbolos, sin filtrar
    # (usado para exportar el historial completo por Telegram).
    import datetime
    desde = datetime.datetime.now() - datetime.timedelta(days=dias)
    hasta = datetime.datetime.now()
    if simbolo is None:
        deals = mt5.history_deals_get(desde, hasta)
    else:
        deals = mt5.history_deals_get(desde, hasta, group=f"*{simbolo}*")
    if deals is None:
        return []
    resultado = []
    for d in deals:
        if d.entry == 1:  # 1 = salida (cierre de operación)
            resultado.append({
                "ticket": d.ticket,
                "deal_id": getattr(d, "deal", d.ticket),
                "simbolo": d.symbol,
                "tipo": "compra" if d.type == 0 else "venta",
                "volumen": d.volume,
                "precio": d.price,
                "profit": d.profit,
                "hora": d.time,
                "reason": _razon_cierre_label(getattr(d, "reason", None)),
                "position_id": getattr(d, "position_id", None),
            })
    resultado.sort(key=lambda x: x["hora"], reverse=True)
    return resultado


@_con_candado
def obtener_comentario_entrada(position_id):
    """Comentario con el que se ABRIÓ una posición (ya cerrada). El deal de
    salida trae un comentario propio ('[tp 3500]'), así que hay que buscar
    el deal de entrada de esa misma posición."""
    if not position_id:
        return ""
    deals = mt5.history_deals_get(position=position_id)
    if not deals:
        return ""
    for d in deals:
        if d.entry == 0:  # 0 = entrada
            return d.comment or ""
    return ""


@_con_candado
def enviar_orden_manual(tipo, volumen, simbolo=SIMBOLO, sl=0.0, tp=0.0,
                        tipo_orden="mercado", precio_entrada=0.0, spread_maximo_puntos=0.0,
                        comment=None):
    """Envía una orden de mercado o pendiente límite.
    spread_maximo_puntos: si es > 0, rechaza la orden sin enviarla cuando el
    spread actual (ask-bid, en "puntos" del símbolo) lo supera. En 0 (default)
    no valida nada, para no cambiar el comportamiento de las órdenes manuales
    desde la web/Telegram."""
    if tipo not in ("compra", "venta"):
        return {"ok": False, "error": "Tipo de orden no válido"}
    if tipo_orden not in ("mercado", "limite"):
        return {"ok": False, "error": "Tipo de orden no válido"}
    if float(volumen) <= 0:
        return {"ok": False, "error": "El volumen debe ser mayor que cero"}

    info_simbolo = mt5.symbol_info(simbolo)
    if info_simbolo is None:
        return {"ok": False, "error": f"El símbolo {simbolo} no existe en MT5"}
    if not info_simbolo.visible and not mt5.symbol_select(simbolo, True):
        return {"ok": False, "error": f"No se pudo activar el símbolo {simbolo} en MT5"}

    tick = mt5.symbol_info_tick(simbolo)
    if tick is None:
        return {"ok": False, "error": "No se pudo obtener el precio"}

    # Validación de spread: en oro, el spread se puede disparar varias veces
    # su valor normal en el segundo de una noticia fuerte. Antes esto no se
    # revisaba, así que el bot podía abrir (o cerrar) justo en ese pico y
    # arrancar la operación ya "perdiendo" solo por el spread, aparte del
    # movimiento real del precio.
    if spread_maximo_puntos and spread_maximo_puntos > 0:
        spread_actual_puntos = (tick.ask - tick.bid) / info_simbolo.point
        if spread_actual_puntos > spread_maximo_puntos:
            return {
                "ok": False,
                "error": (
                    f"Spread demasiado ancho ahora mismo ({spread_actual_puntos:.1f} puntos, "
                    f"máximo configurado {spread_maximo_puntos:.1f}). No se envió la orden."
                ),
            }

    precio_actual = tick.ask if tipo == "compra" else tick.bid
    if tipo_orden == "limite":
        precio = float(precio_entrada)
        if precio <= 0:
            return {"ok": False, "error": "Indica un precio de entrada para la orden límite"}
        if tipo == "compra" and precio >= precio_actual:
            return {"ok": False, "error": "Una compra límite debe estar por debajo del precio actual"}
        if tipo == "venta" and precio <= precio_actual:
            return {"ok": False, "error": "Una venta límite debe estar por encima del precio actual"}
        orden_tipo = mt5.ORDER_TYPE_BUY_LIMIT if tipo == "compra" else mt5.ORDER_TYPE_SELL_LIMIT
        accion = mt5.TRADE_ACTION_PENDING
    else:
        precio = precio_actual
        orden_tipo = mt5.ORDER_TYPE_BUY if tipo == "compra" else mt5.ORDER_TYPE_SELL
        accion = mt5.TRADE_ACTION_DEAL

    # Retcode 10016 (Invalid stops): MT5 rechaza la orden si el SL/TP viene con
    # más decimales de los que acepta el símbolo, o si está demasiado cerca del
    # precio actual (menos de SYMBOL_TRADE_STOPS_LEVEL puntos). Antes esto no se
    # validaba aquí, así que una señal calculada contra una zona_precio vieja
    # (el precio ya se movió) podía mandar un stop inválido y la orden se perdía
    # en silencio (solo quedaba el retcode en el log). Se redondea y se empuja
    # el stop al mínimo permitido antes de enviarlo.
    digits = info_simbolo.digits
    point = info_simbolo.point
    distancia_minima = info_simbolo.trade_stops_level * point

    def _ajustar_stop(valor, precio_ref, hacia_arriba):
        if not valor:
            return 0.0
        valor = round(float(valor), digits)
        distancia = (valor - precio_ref) if hacia_arriba else (precio_ref - valor)
        if distancia < distancia_minima:
            faltante = distancia_minima - distancia
            valor = valor + faltante if hacia_arriba else valor - faltante
            valor = round(valor, digits)
        return valor

    # compra: SL abajo del precio, TP arriba. venta: al revés.
    sl_ajustado = _ajustar_stop(sl, precio, hacia_arriba=(tipo == "venta"))
    tp_ajustado = _ajustar_stop(tp, precio, hacia_arriba=(tipo == "compra"))

    solicitud_base = {
        "action": accion,
        "symbol": simbolo,
        "volume": float(volumen),
        "type": orden_tipo,
        "price": round(precio, digits),
        "sl": sl_ajustado,
        "tp": tp_ajustado,
        "magic": 123456,
        "comment": (str(comment)[:31] if comment else "orden manual - SpotPlus"),
        "type_time": mt5.ORDER_TIME_GTC,
    }
    if tipo_orden == "mercado":
        solicitud_base["deviation"] = 20

    # El bitmask real de "filling_mode" varía por bróker y no siempre es
    # confiable para adivinar el modo correcto de una sola vez (por eso antes
    # tronaba con Retcode 10030: Unsupported filling mode). En vez de
    # adivinar, se prueban los 3 modos en orden hasta que MT5 acepte uno.
    modos_a_probar = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]

    # Retcodes que antes NO se manejaban: la orden se rechazaba y quedaba
    # solo el número en el log (nadie se enteraba, igual que pasó con el
    # 10016). Ahora cada uno da un mensaje claro, y el requote reintenta
    # solo con el precio nuevo (es la única categoría de estas donde
    # reintentar tiene sentido: las otras tres son rechazos "de fondo" que
    # no se arreglan mandando la misma orden de nuevo).
    RETCODES_REQUOTE = (10004, 10008)  # REQUOTE / REQUOTE (distintos brokers usan uno u otro)
    MAX_REINTENTOS_REQUOTE = 2

    ultimo_resultado = None
    intentos_requote = 0
    for modo in modos_a_probar:
        solicitud = {**solicitud_base, "type_filling": modo}
        resultado = mt5.order_send(solicitud)
        if resultado is None:
            ultimo_resultado = {"ok": False, "error": str(mt5.last_error())}
            continue
        if resultado.retcode == mt5.TRADE_RETCODE_DONE:
            return {"ok": True, "ticket": resultado.order, "precio": resultado.price}

        if resultado.retcode in RETCODES_REQUOTE and intentos_requote < MAX_REINTENTOS_REQUOTE and tipo_orden == "mercado":
            # El precio se movió entre que se armó la solicitud y se envió.
            # Se pide un tick fresco y se reintenta con ese precio, en vez de
            # devolver el error e insistir con un precio ya viejo.
            intentos_requote += 1
            tick_fresco = mt5.symbol_info_tick(simbolo)
            if tick_fresco is not None:
                precio_fresco = tick_fresco.ask if tipo == "compra" else tick_fresco.bid
                solicitud_base["price"] = round(precio_fresco, digits)
                solicitud_base["sl"] = _ajustar_stop(sl, precio_fresco, hacia_arriba=(tipo == "venta"))
                solicitud_base["tp"] = _ajustar_stop(tp, precio_fresco, hacia_arriba=(tipo == "compra"))
            ultimo_resultado = {"ok": False, "error": f"Retcode {resultado.retcode}: {resultado.comment}"}
            continue

        if resultado.retcode in MENSAJES_RETCODE:
            return _error_orden(resultado)

        ultimo_resultado = _error_orden(resultado)
        # Solo reintenta con otro modo si el rechazo fue específicamente por
        # modo de llenado no soportado (10030). Cualquier otro error (precio
        # inválido, stops muy cerca, volumen fuera de rango, etc.) se
        # devuelve de inmediato, sin seguir probando modos inútilmente.
        if resultado.retcode != 10030:
            return ultimo_resultado

    return ultimo_resultado or {"ok": False, "error": "No se pudo enviar la orden con ningún modo de llenado"}


@_con_candado
def modificar_sl(ticket, nuevo_sl):
    """Mueve el SL de una posición ya abierta (usado para pasar a breakeven).
    No toca volumen ni TP: solo cambia el precio de stop loss."""
    posiciones = mt5.positions_get(ticket=ticket)
    if not posiciones:
        return {"ok": False, "error": "Posición no encontrada"}

    p = posiciones[0]
    solicitud = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": p.symbol,
        "position": ticket,
        "sl": float(nuevo_sl),
        "tp": p.tp,
    }
    resultado = mt5.order_send(solicitud)
    if resultado is None:
        return {"ok": False, "error": str(mt5.last_error())}
    if resultado.retcode == 10025:  # TRADE_RETCODE_NO_CHANGES: el SL ya estaba en ese valor
        return {"ok": True, "sin_cambios": True}
    if resultado.retcode != mt5.TRADE_RETCODE_DONE:
        return _error_orden(resultado)
    return {"ok": True}


@_con_candado
def modificar_tp(ticket, nuevo_tp):
    """Mueve el TP de una posición ya abierta. No toca volumen ni SL."""
    posiciones = mt5.positions_get(ticket=ticket)
    if not posiciones:
        return {"ok": False, "error": "Posición no encontrada"}

    p = posiciones[0]
    solicitud = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": p.symbol,
        "position": ticket,
        "sl": p.sl,
        "tp": float(nuevo_tp),
    }
    resultado = mt5.order_send(solicitud)
    if resultado is None:
        return {"ok": False, "error": str(mt5.last_error())}
    if resultado.retcode == 10025:  # TRADE_RETCODE_NO_CHANGES: el TP ya estaba en ese valor
        return {"ok": True, "sin_cambios": True}
    if resultado.retcode != mt5.TRADE_RETCODE_DONE:
        return _error_orden(resultado)
    return {"ok": True}


@_con_candado
def cerrar_posicion(ticket):
    posiciones = mt5.positions_get(ticket=ticket)
    if not posiciones:
        return {"ok": False, "error": "Posición no encontrada"}

    p = posiciones[0]
    tick = mt5.symbol_info_tick(p.symbol)
    tipo_cierre = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
    precio = tick.bid if p.type == 0 else tick.ask

    solicitud = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": p.symbol,
        "volume": p.volume,
        "type": tipo_cierre,
        "position": ticket,
        "price": precio,
        "deviation": 20,
        "magic": 123456,
        "comment": "cierre manual - SpotPlus",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    resultado = mt5.order_send(solicitud)
    if resultado is None:
        return {"ok": False, "error": str(mt5.last_error())}
    if resultado.retcode != mt5.TRADE_RETCODE_DONE:
        return _error_orden(resultado)

    return {"ok": True}


@_con_candado
def cerrar_posicion_parcial(ticket, volumen_a_cerrar):
    """Cierra solo una parte del volumen de una posición (sacar parciales)."""
    posiciones = mt5.positions_get(ticket=ticket)
    if not posiciones:
        return {"ok": False, "error": "Posición no encontrada"}

    p = posiciones[0]

    if volumen_a_cerrar >= p.volume:
        return cerrar_posicion(ticket)

    tick = mt5.symbol_info_tick(p.symbol)
    tipo_cierre = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
    precio = tick.bid if p.type == 0 else tick.ask

    solicitud = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": p.symbol,
        "volume": float(volumen_a_cerrar),
        "type": tipo_cierre,
        "position": ticket,
        "price": precio,
        "deviation": 20,
        "magic": 123456,
        "comment": "parcial - SpotPlus",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    resultado = mt5.order_send(solicitud)
    if resultado is None:
        return {"ok": False, "error": str(mt5.last_error())}
    if resultado.retcode != mt5.TRADE_RETCODE_DONE:
        return _error_orden(resultado)

    return {"ok": True, "volumen_cerrado": volumen_a_cerrar, "volumen_restante": round(p.volume - volumen_a_cerrar, 2)}


@_con_candado
def obtener_simbolos_disponibles(filtro=None):
    simbolos = mt5.symbols_get()
    if simbolos is None:
        return []
    nombres = [s.name for s in simbolos if s.visible]
    if filtro:
        nombres = [n for n in nombres if filtro.upper() in n.upper()]
    return sorted(nombres)[:50]


@_con_candado
def obtener_velas_historicas(simbolo, desde, hasta, timeframe_str=None):
    """Trae velas de un rango de fechas pasado (para backtesting), a diferencia de
    obtener_velas() que trae las últimas N velas hasta 'ahora'.
    'desde' y 'hasta' son strings 'YYYY-MM-DD'."""
    import datetime
    timeframe = TIMEFRAMES.get(timeframe_str or TIMEFRAME_STR, mt5.TIMEFRAME_M5)
    try:
        desde_dt = datetime.datetime.strptime(desde, "%Y-%m-%d")
        # incluir todo el día "hasta"
        hasta_dt = datetime.datetime.strptime(hasta, "%Y-%m-%d") + datetime.timedelta(days=1)
    except ValueError:
        print("Fechas inválidas, usa el formato YYYY-MM-DD")
        return None

    if not _seleccionar_simbolo(simbolo):
        return None

    velas = mt5.copy_rates_range(simbolo, timeframe, desde_dt, hasta_dt)
    if velas is None or len(velas) == 0:
        print("Error al traer velas históricas:", mt5.last_error())
        return None

    df = pd.DataFrame(velas)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


@_con_candado
def obtener_tamano_contrato(simbolo=SIMBOLO):
    """Cuánto vale un movimiento de 1.0 en el precio, por lote. Se usa para pasar
    de 'puntos de precio' a dinero real en el backtest."""
    info = mt5.symbol_info(simbolo)
    if info is None or not getattr(info, "trade_contract_size", None):
        return 100.0  # valor típico de respaldo (ej. onzas por lote en oro)
    return float(info.trade_contract_size)


@_con_candado
def obtener_limites_volumen(simbolo=SIMBOLO):
    """Mínimo, máximo y paso de volumen que permite el broker para este símbolo.
    Se usa para no mandar un lotaje calculado que MT5 vaya a rechazar."""
    info = mt5.symbol_info(simbolo)
    if info is None:
        return {"minimo": 0.01, "maximo": 100.0, "paso": 0.01}
    return {
        "minimo": float(getattr(info, "volume_min", 0.01) or 0.01),
        "maximo": float(getattr(info, "volume_max", 100.0) or 100.0),
        "paso": float(getattr(info, "volume_step", 0.01) or 0.01),
    }
