import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .conexion_mt5 import (
    conectar, desconectar, obtener_velas, obtener_velas_multi, obtener_precio_actual,
    obtener_velas_varias_temporalidades,
    obtener_info_cuenta, obtener_posiciones_abiertas, obtener_historial_cerradas,
    enviar_orden_manual, obtener_comentario_entrada, cerrar_posicion, cerrar_posicion_parcial, obtener_simbolos_disponibles,
    modificar_sl, modificar_tp, obtener_velas_historicas, obtener_tamano_contrato,
    verificar_conexion,
)
from .conexion_mt5 import TIMEFRAMES
from .comandos_telegram import escuchar_comandos_operaciones
from .riesgo import encolar_aviso_be, riesgo_total_por_operacion, linea_estimacion, estimar_operacion, calculo_completo
from .estadisticas import registrar_auto, abrir_operacion, reconciliar_diario, texto_cierre
from .backtesting import simular_backtest, TF_SEGUNDOS
from . import historico_csv
from .riesgo import calcular_lotaje
from .telegram_bot import enviar_mensaje as tg_enviar_mensaje
from .indicadores import calcular_rsi, calcular_media_movil
from .estrategias import (
    evaluar_estrategia, ESTRATEGIAS, evaluar_estrategia_texto,
    BLOQUES, COLUMNAS_DISPONIBLES, evaluar_estrategia_bloques,
    evaluar_codigo_libre, evaluar_estructura_dinamica,
)
from . import estructura as est
from . import estadisticas as stats
from .config import SIMBOLO as SIMBOLO_INICIAL, CUENTA_ID, NOMBRE_CUENTA
from . import multicuenta as cuentas_mod
from .config import API_KEY
from .tiempo import timestamps_epoch_segundos
from fastapi import Request
from fastapi.responses import JSONResponse

if not API_KEY:
    raise RuntimeError(
        "Falta API_KEY en el .env. El servidor no arranca sin ella para no "
        "quedar expuesto sin autenticación (antes cualquiera con la "
        "IP:puerto podía operar la cuenta). Agrega una línea como "
        "API_KEY=un-token-largo-y-random en el .env y reinicia."
    )

app = FastAPI(debug=False)


def _diario_abrir(simbolo, tipo, volumen, resultado, sl, tp, origen=""):
    """Crea la ficha 'en curso' en la bitácora al abrir una operación."""
    try:
        info = obtener_info_cuenta()
        riesgo_txt = ""
        if config_bot.get("modo_riesgo") == "porcentaje":
            riesgo_txt = f"{config_bot.get('riesgo_valor')}% del balance"
        elif config_bot.get("modo_riesgo") == "monto_fijo":
            riesgo_txt = f"{config_bot.get('riesgo_valor')} fijo"
        abrir_operacion(
            ticket=resultado.get("ticket"), simbolo=simbolo, tipo=tipo, volumen=volumen,
            entrada=resultado.get("precio"), sl=sl, tp=tp, origen=origen,
            estimacion=estimar_operacion(simbolo, volumen, resultado.get("precio"), sl, tp),
            balance=info["balance"] if info else None, riesgo_txt=riesgo_txt,
        )
    except Exception as e:
        print(f"[diario] error al abrir ficha: {e}")


def _diario_cerrar(notificar_tg=None):
    """Completa las fichas en curso cuya posición ya cerró (TP/SL/BE/manual)."""
    try:
        cerradas = reconciliar_diario(
            obtener_posiciones_abiertas(simbolo=None),
            obtener_historial_cerradas(dias=3, simbolo=None),
        )
        if notificar_tg and config_bot.get("telegram_aviso_cierre", True):
            for ficha in cerradas:
                notificar_tg(texto_cierre(ficha))
        return cerradas
    except Exception as e:
        print(f"[diario] error al cerrar fichas: {e}")
        return []


@app.middleware("http")
async def exigir_api_key(request: Request, call_next):
    """Exige el header X-API-Key en todo lo que empiece con /api.
    Deja pasar OPTIONS (preflight de CORS, no lleva headers custom) y todo lo
    que no sea /api (el frontend estático servido con StaticFiles más abajo).
    """
    if request.method == "OPTIONS" or not request.url.path.startswith("/api"):
        return await call_next(request)
    if request.headers.get("X-API-Key") != API_KEY:
        return JSONResponse(status_code=401, content={"error": "API key inválida o faltante"})
    return await call_next(request)


# CORS se agrega DESPUÉS del middleware de API key a propósito: en Starlette el
# último middleware agregado es el más externo. Antes estaba antes, y los 401
# (API key mala) salían sin headers CORS, así que el navegador los mostraba como
# "Failed to fetch" en vez de "401". Ahora todas las respuestas llevan CORS.
# Se acepta cualquier puerto de localhost / 127.0.0.1 / red local (Expo cambia
# de puerto si el 8081 está ocupado).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Configuración en vivo (se puede cambiar desde la web) ---
config_bot = {
    "simbolo": SIMBOLO_INICIAL,
    "simbolos_permitidos": [SIMBOLO_INICIAL],
    "estrategia": None,
    # Lista de temporalidades que trae el bot en cada ciclo para el escaneo
    # multi-timeframe (est.escanear_multitemporalidad). Solo se usa si el
    # código libre llama a esa función; si está vacía, velas_por_temporalidad
    # llega como {} al código libre (ver evaluar_codigo_libre).
    "temporalidades_escaneo": [],
    "zonas_permitidas": [],  # zonas de espera permitidas: "flip","ev","obc","fractal","fractal2". Vacío = automático (todas)
    "roles_temporalidad": {},  # ej. {"micro":"confirmar","intermedia":"buscar","macro":"buscar","extra_macro":"contexto"}
    "parametros": {},
    "activo": True,  # si está en False, el bot solo observa, no calcula señales
    "estrategia_biblioteca_activa": None,  # nombre de la estrategia guardada activa (si aplica)
    "codigo_estrategia": "",
    "codigo_usa_multi_temporalidad": False,  # si True, el código libre también recibe df_macro
    "condicion_compra_texto": "",
    "condicion_venta_texto": "",
    "condiciones_compra": [],
    "operador_compra": "and",
    "condiciones_venta": [],
    "operador_venta": "and",
    # --- Riesgo / auto-trading de la estrategia activa ---
    "volumen_operacion": 0.01,
    "sl_operacion": 0.0,
    "tp_operacion": 0.0,
    # modo_riesgo: "lotaje_fijo" (usa volumen_operacion tal cual),
    # "porcentaje" (riesgo_valor = % del balance) o "monto_fijo" (riesgo_valor
    # = dinero fijo a arriesgar). En estos dos últimos, el lotaje se calcula
    # solo en cada operación según la distancia real del SL.
    "modo_riesgo": "lotaje_fijo",
    "riesgo_valor": 1.0,
    # --- Notificaciones a Telegram ---
    "telegram_activo": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_aviso_apertura": True,
    "telegram_aviso_cierre": True,
    "telegram_aviso_sl_tp": True,
    "telegram_aviso_parcial": True,
    "telegram_aviso_zona": True,
    # --- Multi-timeframe (solo lo usa "estructura_dinamica") ---
    # timeframe_entrada = donde se confirma la entrada (BOS), ej. M15.
    # timeframe_macro = temporalidad PRINCIPAL real usada solo para medir
    # el movimiento macro (el "ratio" del TP), ej. H1. NUNCA se entra ahí.
    "timeframe_entrada": "M15",
    "timeframe_macro": "H1",
    "filtro_sesgo_macro": False,
    "aviso_cambio_direccion": True,
    "proteger_cambio_direccion": True,
    # --- Breakeven ---
    # be_activo: si True, cuando una posición abierta avanza be_porcentaje
    # de la distancia entrada->tp a favor, el bot mueve el SL a la entrada
    # (0 riesgo). El volumen no cambia, solo se protege la operación.
    "be_activo": False,
    # be_cascada_buffer_pct: cuando una operación de un lote toca TP, las
    # demás del mismo lote se protegen a BE (_cascada_be_por_tp) SOLO si ya
    # llevan de avance al menos este % de su propia distancia al SL. Antes
    # se protegían con 1 pip a favor (podía ser "demasiado pronto" y sacar
    # la operación con cualquier micro-retroceso normal). 0 = comportamiento
    # viejo (cualquier avance a favor ya protege).
    "be_cascada_buffer_pct": 20.0,
    # --- Breakeven dinámico (est.be_dinamico): combina 3 señales, basta con
    # que se cumpla UNA para mover a BE — nivel de Fibonacci del tramo de
    # entrada, nueva estructura confirmada a favor en una micro, o pérdida
    # de la alineación entre temporalidades que sostenía la operación.
    "be_dinamico_activo": False,
    "be_dinamico_fibo_nivel": 0.5,  # 0.5 = nivel 50% de Fibonacci
    # grupos_temporalidad: sobreescribe, por temporalidad, a qué grupo
    # (micro/intermedia/macro) pertenece, en vez de la clasificación fija.
    # Ej: {"M15": "macro", "H1": "intermedia"}. {} = usar la clasificación
    # por defecto de estructura.TEMPORALIDADES.
    "grupos_temporalidad": {},
    # tp_por_liquidez: si True, las operaciones múltiples usan op["tp_liquidez"]
    # (liquidez externa más cercana) como TP cuando existe; si no, el TP normal.
    "tp_por_liquidez": False,
    "be_porcentaje": 0.8,
    # Si True, no abre una operación nueva en un símbolo mientras ya tenga
    # una posición abierta ahí (evita apilar varias compras/ventas seguidas
    # cuando la estrategia confirma zonas nuevas muy seguido, típico en
    # timeframes cortos como M1/M3).
    # Máximo de posiciones abiertas SIMULTÁNEAS por símbolo. 0 = sin límite
    # (comportamiento de antes: puede apilar todas las que la estrategia
    # confirme). Con esto en 2, por ejemplo, si ya hay 2 operaciones abiertas
    # en ese símbolo el bot deja de abrir más aunque la estrategia siga
    # confirmando zonas nuevas — así no se "escalera" en un retroceso.
    # Una operación por símbolo por defecto. 0 sigue disponible como opción
    # explícita para quien quiera permitir varias entradas.
    "max_operaciones_por_simbolo": 1,
    # --- Circuit breaker de pérdida diaria ---
    # Si circuito_perdida_diaria_activo=True, el bot se pausa SOLO (dentro
    # del ciclo, sin tocar "activo" para no pisar un pausado/reanudado
    # manual) en cuanto el equity cae circuito_perdida_diaria_pct% por
    # debajo del balance con el que arrancó el día. Se reactiva solo al
    # día siguiente (hora del servidor).
    "circuito_perdida_diaria_activo": False,
    "circuito_perdida_diaria_pct": 5.0,
    # --- Validación de spread antes de operar ---
    # 0 = sin límite (no valida). En "puntos" del símbolo (para XAUUSD con
    # 3 decimales, 1 punto = 0.001; conviene mirar el spread normal del
    # símbolo en tu bróker antes de fijar un número aquí).
    "spread_maximo_puntos": 0.0,
    # --- Bloqueo de entradas por noticias fundamentales (NFP/CPI/tasas) ---
    # Si bloqueo_noticias_activo=True, el bot NO abre una operación nueva
    # mientras haya una noticia de alto impacto "en proceso" (saliendo
    # justo ahora) o "pendiente" dentro de bloqueo_noticias_minutos_antes
    # minutos, para las monedas relevantes del símbolo (ver
    # fundamentales.monedas_de_simbolo). Las operaciones ya abiertas NO se
    # tocan, solo se bloquean entradas NUEVAS.
    "bloqueo_noticias_activo": True,
    "bloqueo_noticias_minutos_antes": 30,
    # Durante una noticia de alto impacto (en proceso o por salir dentro de
    # bloqueo_noticias_minutos_antes) las operaciones abiertas EN GANANCIA se
    # mueven a BE. No se abren operaciones nuevas (ver bloqueo arriba).
    "noticia_be_activo": True,
    # Riesgo total: si está activo, el riesgo_total_pct (ej. 1%) del balance se
    # reparte entre los activos que analiza el bot y sus operaciones máximas,
    # para no pasarte nunca del total aunque analices más activos.
    "riesgo_total_activo": False,
    "riesgo_total_pct": 1.0,
    # Bitácora automática: el bot anota solo cada apertura (con pérdida/ganancia
    # estimada y R:R) y cada cierre por SL/TP.
    "bitacora_auto_activa": True,
}

# Recuerda a qué tickets ya se les movió el SL a breakeven, para no mandar
# la orden de modificación una y otra vez en cada ciclo del bot.
# Se guarda en disco (no solo en memoria) porque si el backend se reinicia
# (aplicar un parche, un crash, etc.) el set en memoria se vacía y el bot
# volvía a mandar avisos de "SL activado"/"TP activado" de cierres viejos
# que ya se habían avisado antes del reinicio.
tickets_con_be_aplicado = set()
tickets_be_deshabilitado = set()

# Recuerda la última zona (tipo + precio) sobre la que ya se avisó por
# Telegram, para no repetir el mismo aviso en cada ciclo mientras el
# precio sigue tocando la misma zona.
# IMPORTANTE: es un diccionario por SÍMBOLO (antes era un solo valor
# compartido entre todos los activos permitidos). Con un solo valor, si el
# bot opera varios símbolos a la vez (ej. XAUUSD + AUDUSD + GBPUSD), la señal
# de un símbolo "pisaba" el recuerdo de otro: XAUUSD avisaba su zona, luego
# el ciclo pasaba a AUDUSD y cambiaba el valor global, y al volver a XAUUSD
# (que seguía en la misma zona, sin cambios reales) el bot creía que era una
# zona "nueva" y volvía a avisar — o peor, volvía a abrir la MISMA operación
# dos veces, porque "ultima_senal_ejecutada" tenía el mismo problema.
ultima_zona_notificada_por_simbolo = {}
avisos_contexto_multitemporal = set()  # (legado; ya no se usa para deduplicar)
sesgos_multitemporales_por_tf = {}
# Cooldown por aviso de contexto. Las llaves NO llevan precios exactos (antes sí,
# y como los pivotes se mueven cada ciclo cada vez era un aviso "nuevo").
avisos_contexto_ts = {}
conflictos_firma_por_simbolo = {}
COOLDOWN_AVISOS = {"estructura": 900, "acumulacion": 3600, "liquidez": 1800, "conflicto": 1800}


def _bucket_precio(precio, pct=0.15):
    """Redondea el precio a 'escalones' de pct% para que un nivel que se mueve
    unos centavos siga contando como el MISMO nivel."""
    paso = max(abs(precio) * pct / 100, 1e-9)
    return int(round(precio / paso))


def _aviso_permitido(clave, cooldown_seg):
    """True si ya pasó el cooldown desde el último aviso con esta clave.
    Se persiste en avisos_enviados.json para que reiniciar el backend no
    vuelva a mandar todo."""
    ahora = time.time()
    k = "|".join(str(c) for c in clave)
    ultimo = avisos_contexto_ts.get(k)
    if ultimo is not None and ahora - ultimo < cooldown_seg:
        return False
    avisos_contexto_ts[k] = ahora
    if len(avisos_contexto_ts) > 500:
        for kk in [kk for kk, t in avisos_contexto_ts.items() if ahora - t > 86400]:
            avisos_contexto_ts.pop(kk, None)
    _guardar_avisos_enviados()
    return True

# Evita repetir por error un aviso de cierre por SL/TP cuando MT5 devuelve el
# mismo deal varias veces en ciclos consecutivos, O cuando el backend se
# reinicia y vuelve a leer las últimas 24h de historial (ver ARCHIVO_AVISOS).
sl_tp_notificados = set()

ARCHIVO_AVISOS = Path(__file__).resolve().parent / f"avisos_enviados{'_' + CUENTA_ID if CUENTA_ID else ''}.json"


def _normalizar_timeframe(valor, predeterminado):
    """Acepta formatos antiguos como '3' o '30m' y devuelve 'M3'/'M30'."""
    texto = str(valor or predeterminado).strip().upper()
    if texto.isdigit():
        texto = f"M{texto}"
    elif texto.endswith("MIN"):
        texto = f"M{texto[:-3]}"
    elif texto.endswith("M") and texto[:-1].isdigit():
        texto = f"M{texto[:-1]}"
    if texto not in TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Temporalidad inválida '{valor}'. Usa una de: {', '.join(TIMEFRAMES)}",
        )
    return texto


def _normalizar_temporalidades_escaneo(temporalidades):
    """Normaliza y deduplica la lista de marcos elegidos para el escáner."""
    normalizadas = []
    for timeframe in temporalidades or []:
        if not str(timeframe).strip():
            continue
        normalizado = _normalizar_timeframe(timeframe, "M15")
        if normalizado not in normalizadas:
            normalizadas.append(normalizado)
    return normalizadas


def _cargar_avisos_enviados():
    global sl_tp_notificados, tickets_con_be_aplicado
    if not ARCHIVO_AVISOS.exists():
        return
    try:
        with ARCHIVO_AVISOS.open("r", encoding="utf-8") as f:
            datos = json.load(f)
        sl_tp_notificados = {tuple(clave) for clave in datos.get("sl_tp_notificados", [])}
        tickets_con_be_aplicado = set(datos.get("tickets_con_be_aplicado", []))
        tickets_be_deshabilitado.update(datos.get("tickets_be_deshabilitado", []))
        avisos_contexto_ts.update({k: float(v) for k, v in datos.get("avisos_contexto_ts", {}).items()})
    except (json.JSONDecodeError, OSError):
        pass


def _guardar_avisos_enviados():
    try:
        # Recorta a los últimos 500 para que el archivo no crezca sin límite.
        recientes = list(sl_tp_notificados)[-500:]
        with ARCHIVO_AVISOS.open("w", encoding="utf-8") as f:
            json.dump({
                "sl_tp_notificados": [list(clave) for clave in recientes],
                "tickets_con_be_aplicado": list(tickets_con_be_aplicado),
                "tickets_be_deshabilitado": list(tickets_be_deshabilitado),
                "avisos_contexto_ts": dict(avisos_contexto_ts),
            }, f)
    except OSError:
        pass


_cargar_avisos_enviados()


def _deshabilitar_be_ticket(ticket):
    tickets_be_deshabilitado.add(ticket)
    _guardar_avisos_enviados()


def notificar(texto):
    """Manda un aviso a Telegram si las notificaciones están activas y hay
    token/chat_id configurados. Nunca interrumpe el flujo del bot si falla."""
    if not config_bot.get("telegram_activo"):
        return
    resultado = tg_enviar_mensaje(
        config_bot.get("telegram_bot_token", ""),
        config_bot.get("telegram_chat_id", ""),
        texto,
    )
    if not resultado.get("ok"):
        print(f"⚠ No se pudo notificar a Telegram: {resultado.get('error')}")

estado = {
    "conectado": False,
    "precio": None,
    "senal": None,
    "ultima_actualizacion": None,
    "error": None,
    "ultima_senal_ejecutada": None,
    "ultima_orden": None,
    # Solo se llena cuando la estrategia activa es "estructura_dinamica":
    # {"estado": "sin_zona"} | {"estado": "esperando_confirmacion", ...} |
    # {"estado": "confirmada", ...} — ver estructura.estado_actual_estructura().
    "estado_zona": None,
    "contexto_estructura": None,
    # Se llena cada vez que el bot manda una orden en modo "porcentaje" o
    # "monto_fijo": {"lotaje", "dinero_riesgo", "distancia_sl", "advertencia"}.
    "ultimo_calculo_riesgo": None,
    # Símbolo que el bot está procesando/cotizando ahora mismo (puede ser
    # distinto de config_bot["simbolo"], que es solo el símbolo "principal"
    # guardado en config; el bot realmente opera lo que haya en
    # simbolos_permitidos). El frontend debe mostrar ESTE valor, no
    # config.simbolo, para no confundir con qué símbolo se está operando.
    "simbolo_actual": None,
    "sesgo_macro": None,
    # --- Circuit breaker de pérdida diaria ---
    "circuito_disparado": False,  # True = el bot dejó de abrir operaciones por hoy
    "circuito_balance_inicio_dia": None,
    "circuito_fecha_dia": None,  # "YYYY-MM-DD", para saber cuándo resetear
}


def _revisar_circuito_perdida_diaria():
    """Se llama al inicio de cada vuelta del ciclo del bot. Devuelve True si
    el bot puede seguir operando este ciclo, False si el circuito ya está
    disparado (o se disparó ahora mismo) y hay que saltarse esta vuelta.
    No toca config_bot["activo"]: son cosas independientes. Un pausado
    manual (Telegram/web) sigue pausado aunque el circuito no se haya
    disparado, y el circuito disparado no se "arregla" reanudando el bot a
    mano — se resetea solo al día siguiente."""
    hoy = datetime.now().strftime("%Y-%m-%d")
    if estado["circuito_fecha_dia"] != hoy:
        info = obtener_info_cuenta()
        estado["circuito_fecha_dia"] = hoy
        estado["circuito_balance_inicio_dia"] = info["balance"] if info else None
        estado["circuito_disparado"] = False

    if not config_bot["circuito_perdida_diaria_activo"]:
        return True
    if estado["circuito_disparado"]:
        return False
    if not estado["circuito_balance_inicio_dia"]:
        return True

    info = obtener_info_cuenta()
    if info is None:
        return True
    perdida_pct = (estado["circuito_balance_inicio_dia"] - info["equity"]) / estado["circuito_balance_inicio_dia"] * 100
    if perdida_pct >= config_bot["circuito_perdida_diaria_pct"]:
        estado["circuito_disparado"] = True
        notificar(
            f"🛑 <b>Circuit breaker activado</b>\n"
            f"Pérdida del día: {perdida_pct:.1f}% (límite {config_bot['circuito_perdida_diaria_pct']}%)\n"
            f"El bot dejó de abrir operaciones nuevas por hoy. Las posiciones abiertas "
            f"siguen su curso (SL/TP normales). Se reactiva solo mañana, o puedes "
            f"pausarlo/reanudarlo con el botón de siempre si quieres tocar algo antes."
        )
        return False
    return True

# Recuerda, POR SÍMBOLO, cuál fue la última señal ("compra"/"venta") que ya
# se ejecutó, para no volver a mandar la misma orden en cada ciclo del bot
# mientras la señal siga activa. Antes era un solo valor compartido entre
# todos los símbolos permitidos, lo que causaba operaciones DUPLICADAS: al
# operar XAUUSD y luego evaluar AUDUSD, la señal de AUDUSD sobreescribía el
# recuerdo de XAUUSD, y al volver a XAUUSD (misma señal, sin cambios reales)
# el bot la ejecutaba de nuevo.
ultima_senal_ejecutada_por_simbolo = {}
sesgo_macro_anterior_por_simbolo = {}
# Dedupe para operaciones múltiples simultáneas (est.escanear_todas_las_oportunidades):
# clave = (simbolo, id_de_la_oportunidad) -> True si ya se ejecutó, para no repetir
# cada ciclo mientras la misma zona/temporalidad siga dando la misma señal.
ids_operaciones_multiples_ejecutadas = set()


# --- Lotes de operaciones (cascada a Break Even) ---
# Cada orden múltiple lleva en el comment "SP<lote>:<tf>:<grupo>:<zona>:<micro>".
# Mientras haya alguna
# posición abierta del lote en ese símbolo, las nuevas se suman al MISMO lote.
# Cuando una del lote cierra en TP, las demás abiertas del lote pasan a BE.
def _parsear_comment_lote(comment):
    if not comment or not comment.startswith("SP") or ":" not in comment:
        return None, None
    lote, detalle = comment[2:].split(":", 1)
    tf = detalle.split(":", 1)[0]
    return lote or None, tf or None


def _comentario_orden_estrategia(lote, operacion):
    """Construye un comentario de MT5 breve con el contexto de la señal."""
    grupo = {
        "micro": "MIC",
        "intermedia": "INT",
        "macro": "MAC",
        "extra_macro": "XMA",
    }.get(operacion.get("grupo_principal"), "INT")
    zona = {
        "flip": "FLIP",
        "ev": "EV",
        "obc": "OBC",
        "fractal": "FRAC",
        "fractal2": "FR2",
    }.get(str(operacion.get("bloque") or "").lower(), "ZONA")
    patron = {
        "envolvente": "ENG",
        "pinbar": "PIN",
        "doji_rechazo": "DJ",
    }.get(operacion.get("patron_vela"))
    comentario = (
        f"SP{lote}:{operacion.get('temporalidad_principal', '')}:"
        f"{grupo}:{zona}:{operacion.get('confirmado_en', '')}"
    )
    if patron:
        comentario_con_patron = f"{comentario}:{patron}"
        if len(comentario_con_patron) <= 31:
            comentario = comentario_con_patron
    return comentario[:31]


def _lote_activo(posiciones):
    for p in posiciones:
        lote, _ = _parsear_comment_lote(p.get("comment"))
        if lote:
            return lote
    return datetime.now().strftime("%d%H%M%S")


def _cascada_be_por_tp(cierre, simbolo):
    """Si el cierre TP pertenece a un lote, mueve a BE las demás del lote.

    Antes se protegía apenas el precio tocaba 1 pip a favor de la entrada
    ("demasiado pronto": cualquier micro-retroceso normal sacaba la
    operación a 0). Ahora exige que el avance ya sea al menos
    be_cascada_buffer_pct % de la distancia propia al SL antes de mover el
    SL a breakeven.
    """
    lote, _ = _parsear_comment_lote(obtener_comentario_entrada(cierre.get("position_id")))
    if not lote:
        return
    buffer_pct = max(0.0, config_bot.get("be_cascada_buffer_pct", 20.0)) / 100
    movidas = []
    for p in obtener_posiciones_abiertas(simbolo=simbolo):
        lote_p, _ = _parsear_comment_lote(p.get("comment"))
        if lote_p != lote or p["ticket"] in tickets_con_be_aplicado:
            continue
        avance = (
            p["precio_actual"] - p["precio_apertura"] if p["tipo"] == "compra"
            else p["precio_apertura"] - p["precio_actual"]
        )
        if avance <= 0:
            continue  # no está del lado ganador de la entrada todavía
        if p.get("sl") and buffer_pct > 0:
            distancia_sl = abs(p["precio_apertura"] - p["sl"])
            if distancia_sl > 0 and avance < distancia_sl * buffer_pct:
                continue  # avance insuficiente todavía, "demasiado pronto"
        if modificar_sl(p["ticket"], p["precio_apertura"]).get("ok"):
            tickets_con_be_aplicado.add(p["ticket"])
            movidas.append(p["ticket"])
    if movidas:
        _guardar_avisos_enviados()
        notificar(f"🔒 <b>TP en lote {lote}</b>: BE en tickets {', '.join(map(str, movidas))}")

# --- Biblioteca de estrategias guardadas (hoja de "ya la tengo hecha") ---
# Aquí se guardan estrategias con nombre + explicación + condiciones en texto,
# para no perderlas al reiniciar el servidor.
# Con multi-cuenta (CUENTA_ID definido), cada proceso guarda su propio
# estado en archivos con sufijo "_<cuenta_id>" para que 2+ cuentas corriendo
# en paralelo (procesos distintos) nunca se pisen el bot_config, telegram,
# ni la biblioteca de estrategias entre si.
_SUFIJO_CUENTA = f"_{CUENTA_ID}" if CUENTA_ID else ""

ARCHIVO_BIBLIOTECA = Path(__file__).resolve().parent / f"estrategias_guardadas{_SUFIJO_CUENTA}.json"


def cargar_biblioteca():
    if ARCHIVO_BIBLIOTECA.exists():
        with ARCHIVO_BIBLIOTECA.open("r", encoding="utf-8") as f:
            return json.load(f)
    return []


def guardar_biblioteca(lista):
    with ARCHIVO_BIBLIOTECA.open("w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)


ARCHIVO_TELEGRAM = Path(__file__).resolve().parent / f"telegram_config{_SUFIJO_CUENTA}.json"


def cargar_config_telegram():
    if ARCHIVO_TELEGRAM.exists():
        with ARCHIVO_TELEGRAM.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_config_telegram():
    datos = {
        "telegram_activo": config_bot["telegram_activo"],
        "telegram_bot_token": config_bot["telegram_bot_token"],
        "telegram_chat_id": config_bot["telegram_chat_id"],
        "telegram_aviso_apertura": config_bot["telegram_aviso_apertura"],
        "telegram_aviso_cierre": config_bot["telegram_aviso_cierre"],
        "telegram_aviso_sl_tp": config_bot.get("telegram_aviso_sl_tp", True),
        "telegram_aviso_parcial": config_bot["telegram_aviso_parcial"],
        "telegram_aviso_zona": config_bot["telegram_aviso_zona"],
    }
    with ARCHIVO_TELEGRAM.open("w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


# Al iniciar, restaura el Telegram guardado (si existe) para no tener que
# volver a pegar el token y el chat ID cada vez que se reinicia el backend.
config_bot.update(cargar_config_telegram())
config_bot.setdefault("telegram_aviso_sl_tp", True)

ARCHIVO_CONFIG_BOT = Path(__file__).resolve().parent / f"bot_config{_SUFIJO_CUENTA}.json"

# Campos de config_bot que SÍ se guardan en disco (símbolo, activos permitidos,
# estrategia activa, riesgo, etc.). Lo que no está aquí (posiciones, estado en
# vivo, etc.) sigue siendo solo en memoria a propósito.
CAMPOS_CONFIG_BOT_PERSISTENTES = [
    "simbolo", "simbolos_permitidos", "estrategia", "parametros", "activo",
    "estrategia_biblioteca_activa", "codigo_estrategia", "codigo_usa_multi_temporalidad",
    "condicion_compra_texto", "condicion_venta_texto",
    "condiciones_compra", "operador_compra", "condiciones_venta", "operador_venta",
    "volumen_operacion", "sl_operacion", "tp_operacion", "modo_riesgo", "riesgo_valor",
    "timeframe_entrada", "timeframe_macro", "be_activo", "be_porcentaje", "tp_por_liquidez",
    "be_cascada_buffer_pct", "be_dinamico_activo", "be_dinamico_fibo_nivel", "grupos_temporalidad",
    "temporalidades_escaneo", "roles_temporalidad", "zonas_permitidas",
    "max_operaciones_por_simbolo", "filtro_sesgo_macro", "aviso_cambio_direccion",
    "proteger_cambio_direccion", "noticia_be_activo", "riesgo_total_activo", "riesgo_total_pct",
    "bloqueo_noticias_activo", "bloqueo_noticias_minutos_antes", "bitacora_auto_activa",
]


def cargar_config_bot():
    if ARCHIVO_CONFIG_BOT.exists():
        with ARCHIVO_CONFIG_BOT.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_config_bot():
    datos = {campo: config_bot[campo] for campo in CAMPOS_CONFIG_BOT_PERSISTENTES if campo in config_bot}
    with ARCHIVO_CONFIG_BOT.open("w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


# Restaura el símbolo, activos permitidos y estrategia activa guardados la
# última vez, para no tener que volver a configurar todo cada vez que se
# reinicia el backend.
config_bot.update(cargar_config_bot())
config_bot.setdefault("max_operaciones_por_simbolo", 1)

# Migra formatos antiguos guardados en bot_config.json (por ejemplo, "30m")
# antes de que el ciclo de trading solicite las velas a MT5.
config_bot["timeframe_entrada"] = _normalizar_timeframe(config_bot.get("timeframe_entrada"), "M15")
config_bot["timeframe_macro"] = _normalizar_timeframe(config_bot.get("timeframe_macro"), "H1")

biblioteca_estrategias = cargar_biblioteca()

# Migra también la biblioteca para que la interfaz muestre el mismo formato
# que usa el motor (M3/M30), en lugar de conservar valores antiguos como "3".
biblioteca_corregida = False
for estrategia in biblioteca_estrategias:
    entrada_corregida = _normalizar_timeframe(estrategia.get("timeframe_entrada"), "M15")
    macro_corregida = _normalizar_timeframe(estrategia.get("timeframe_macro"), "H1")
    if estrategia.get("timeframe_entrada") != entrada_corregida or estrategia.get("timeframe_macro") != macro_corregida:
        estrategia["timeframe_entrada"] = entrada_corregida
        estrategia["timeframe_macro"] = macro_corregida
        biblioteca_corregida = True
if biblioteca_corregida:
    guardar_biblioteca(biblioteca_estrategias)

# Versiones anteriores podían guardar el código fuente completo de SpotPlus
# como si fuera código libre. Ese archivo solo declara funciones: no llama a
# la estrategia ni deja una variable ``senal``, por lo que el backtest acaba
# con 0 operaciones. Se migra a la representación correcta del motor nativo.
hubo_migracion_estructura = False
for estrategia_guardada in biblioteca_estrategias:
    codigo_guardado = estrategia_guardada.get("codigo", "")
    es_copia_del_motor = (
        "def señal_estructura(" in codigo_guardado
        and "def estado_actual_estructura(" in codigo_guardado
    )
    if es_copia_del_motor and not estrategia_guardada.get("es_estructura_dinamica"):
        estrategia_guardada["es_estructura_dinamica"] = True
        estrategia_guardada["timeframe_entrada"] = str(estrategia_guardada.get("timeframe_entrada", "M15")).upper()
        macro = str(estrategia_guardada.get("timeframe_macro", "H1")).upper()
        estrategia_guardada["timeframe_macro"] = macro if macro in ("M1", "M5", "M15", "H1") else "H1"
        hubo_migracion_estructura = True

if hubo_migracion_estructura:
    guardar_biblioteca(biblioteca_estrategias)
    print("✓ Estrategia SpotPlus migrada al motor de estructura nativo")

# Si esa estrategia ya estaba marcada como activa en una instalación previa,
# cambia también la configuración en memoria para no seguir ejecutando el
# archivo de funciones como código libre.
estrategia_guardada_activa = next(
    (item for item in biblioteca_estrategias if item.get("nombre") == config_bot.get("estrategia_biblioteca_activa")),
    None,
)
if estrategia_guardada_activa:
    # La estrategia activa es la fuente de verdad también después de reiniciar.
    # Evita que bot_config.json conserve una temporalidad de una configuración anterior.
    config_bot["timeframe_entrada"] = _normalizar_timeframe(estrategia_guardada_activa.get("timeframe_entrada"), "M15")
    config_bot["timeframe_macro"] = _normalizar_timeframe(estrategia_guardada_activa.get("timeframe_macro"), "H1")
if estrategia_guardada_activa and estrategia_guardada_activa.get("es_estructura_dinamica"):
    config_bot["estrategia"] = "estructura_dinamica"
    config_bot["codigo_estrategia"] = ""
    config_bot["codigo_usa_multi_temporalidad"] = False
    config_bot["timeframe_entrada"] = _normalizar_timeframe(estrategia_guardada_activa.get("timeframe_entrada"), "M15")
    config_bot["timeframe_macro"] = _normalizar_timeframe(estrategia_guardada_activa.get("timeframe_macro"), "H1")
    config_bot["parametros"] = dict(estrategia_guardada_activa.get("parametros", {}) or {})
    guardar_config_bot()

# Si NO había nada persistido de una sesión anterior (primera vez que corre el
# bot, o bot_config.json no existe), cae de vuelta a la primera estrategia
# guardada como estrategia activa por defecto.
if biblioteca_estrategias and not config_bot.get("estrategia_biblioteca_activa") \
        and not config_bot.get("estrategia") and not config_bot.get("codigo_estrategia"):
    estrategia_inicial = biblioteca_estrategias[0]
    config_bot["codigo_estrategia"] = estrategia_inicial.get("codigo", "")
    config_bot["volumen_operacion"] = estrategia_inicial.get("volumen", 0.01)
    config_bot["sl_operacion"] = estrategia_inicial.get("sl", 0.0)
    config_bot["tp_operacion"] = estrategia_inicial.get("tp", 0.0)
    config_bot["modo_riesgo"] = estrategia_inicial.get("modo_riesgo", "lotaje_fijo")
    config_bot["riesgo_valor"] = estrategia_inicial.get("riesgo_valor", 1.0)
    print(f"✓ Estrategia cargada al iniciar: {estrategia_inicial['nombre']}")
else:
    print("✓ Config del bot restaurada desde bot_config.json")


def _simbolos_permitidos():
    simbolos = config_bot.get("simbolos_permitidos") or []
    if not simbolos:
        return [config_bot["simbolo"]]
    return [s.strip() for s in simbolos if str(s).strip()]


def ciclo_bot():
    while True:
        try:
            # El error describe la lectura actual, no una lectura fallida anterior.
            estado["error"] = None
            if not config_bot["activo"]:
                time.sleep(5)
                continue

            # Watchdog: antes, si el terminal MT5 se caía a mitad de la
            # corrida, nada lo notaba solo (había que revisar a mano). Ahora
            # se revisa al inicio de cada vuelta y se reconecta si hace falta.
            if not verificar_conexion():
                estado["conectado"] = False
                estado["error"] = "Se perdió la conexión con MT5 y no se pudo reconectar"
                time.sleep(15)
                continue
            estado["conectado"] = True

            if not _revisar_circuito_perdida_diaria():
                time.sleep(60)
                continue

            simbolos = _simbolos_permitidos()
            for simbolo in simbolos:
                temporalidades_escaneo = config_bot.get("temporalidades_escaneo") or []
                usa_escaneo_configurado = bool(
                    config_bot.get("codigo_estrategia") and temporalidades_escaneo
                )
                velas_por_temporalidad = {}
                df_macro = None
                if usa_escaneo_configurado:
                    velas_por_temporalidad = obtener_velas_varias_temporalidades(
                        simbolo=simbolo,
                        temporalidades=temporalidades_escaneo,
                    )
                    disponibles = [tf for tf in TIMEFRAMES if tf in velas_por_temporalidad]
                    if disponibles:
                        tf_entrada = str(config_bot.get("timeframe_entrada") or "").upper()
                        tf_macro = str(config_bot.get("timeframe_macro") or "").upper()
                        df = velas_por_temporalidad.get(tf_entrada)
                        if df is None:
                            df = velas_por_temporalidad[disponibles[0]]
                        df_macro = velas_por_temporalidad.get(tf_macro)
                        if df_macro is None:
                            df_macro = velas_por_temporalidad[disponibles[-1]]
                    else:
                        df = None
                elif config_bot["estrategia"] == "estructura_dinamica" or config_bot["codigo_estrategia"]:
                    df, df_macro = obtener_velas_multi(
                        simbolo=simbolo,
                        timeframe_entrada=config_bot["timeframe_entrada"],
                        timeframe_macro=config_bot["timeframe_macro"],
                    )
                    if not config_bot.get("codigo_usa_multi_temporalidad") and config_bot["estrategia"] != "estructura_dinamica":
                        df_macro = None
                else:
                    df = obtener_velas(simbolo=simbolo)

                if df is None:
                    estado["error"] = f"No se pudieron obtener velas para {simbolo}"
                    continue

                df = calcular_rsi(df)
                for periodo in [10, 20, 50, 100, 200]:
                    df = calcular_media_movil(df, periodo=periodo)

                sesgo_macro = None
                if df_macro is not None:
                    p_macro = config_bot.get("parametros", {})
                    sesgo_macro = est.sesgo_direccion(
                        df_macro,
                        pivot_len=max(2, int(p_macro.get("pivot_len_macro", 15))),
                    )
                estado["sesgo_macro"] = sesgo_macro
                sesgo_anterior = sesgo_macro_anterior_por_simbolo.get(simbolo)
                if sesgo_macro and sesgo_macro != sesgo_anterior:
                    sesgo_macro_anterior_por_simbolo[simbolo] = sesgo_macro
                    if sesgo_anterior and config_bot.get("aviso_cambio_direccion"):
                        notificar(
                            f"⚠️ <b>Cambio de dirección macro</b>\n"
                            f"{simbolo} · {sesgo_anterior.upper()} → {sesgo_macro.upper()}\n"
                            f"Se volverá a analizar la entrada antes de abrir otra operación."
                        )

                senal = None
                senal_dinamica = None
                operaciones_multiples = None  # lista de varias entradas simultáneas (una por temporalidad)
                # Se trae UNA sola vez por ciclo (si hay temporalidades_escaneo
                # configuradas) para que la use tanto el código libre como el
                # BE dinámico (est.be_dinamico) más abajo, sin refetchear.
                if temporalidades_escaneo and not usa_escaneo_configurado:
                    velas_por_temporalidad = obtener_velas_varias_temporalidades(
                        simbolo=simbolo,
                        temporalidades=temporalidades_escaneo,
                    )
                elif config_bot.get("codigo_usa_multi_temporalidad") and config_bot.get("codigo_estrategia"):
                    temporalidades_manuales = {
                        config_bot["timeframe_entrada"]: df,
                    }
                    if df_macro is not None:
                        temporalidades_manuales[config_bot["timeframe_macro"]] = df_macro
                    velas_por_temporalidad = temporalidades_manuales
                contexto_estructura = (
                    est.analizar_contexto_multitemporal(
                        velas_por_temporalidad,
                        grupos_temporalidad=config_bot.get("grupos_temporalidad") or None,
                    )
                    if velas_por_temporalidad else None
                )
                estado["contexto_estructura"] = contexto_estructura
                if contexto_estructura:
                    for tf, info in contexto_estructura["temporalidades"].items():
                        clave_sesgo = (simbolo, tf)
                        sesgo_actual_tf = info.get("sesgo")
                        sesgo_anterior_tf = sesgos_multitemporales_por_tf.get(clave_sesgo)
                        if sesgo_actual_tf:
                            sesgos_multitemporales_por_tf[clave_sesgo] = sesgo_actual_tf
                        incluir_micro = bool(config_bot.get("aviso_contexto_incluir_micro", False))
                        es_micro = info.get("grupo") == "micro"
                        if (
                            sesgo_actual_tf
                            and sesgo_anterior_tf
                            and sesgo_actual_tf != sesgo_anterior_tf
                            and config_bot.get("aviso_cambio_direccion")
                            and (incluir_micro or not es_micro)
                            and _aviso_permitido((simbolo, "estructura", tf), COOLDOWN_AVISOS["estructura"])
                        ):
                            notificar(
                                f"🔄 <b>Cambio de estructura</b>\n"
                                f"{simbolo} · {tf} · {sesgo_anterior_tf.upper()} → "
                                f"{sesgo_actual_tf.upper()}"
                            )
                        if es_micro and not incluir_micro:
                            continue  # el ruido de M1/M3/M5 no genera avisos de contexto
                        acumulacion = info.get("acumulacion")
                        if acumulacion and config_bot.get("telegram_aviso_zona", True):
                            centro = (acumulacion["techo"] + acumulacion["piso"]) / 2
                            if _aviso_permitido(
                                (simbolo, "acumulacion", tf, _bucket_precio(centro, 0.3)),
                                COOLDOWN_AVISOS["acumulacion"],
                            ):
                                notificar(
                                    f"🟨 <b>Posible acumulación</b>\n"
                                    f"{simbolo} · {tf} · rango "
                                    f"${acumulacion['piso']:.2f}–${acumulacion['techo']:.2f} · "
                                    f"{acumulacion['toques']} pivotes"
                                )
                        for pool in info.get("liquidez_externa", []):
                            # Solo barridas con contexto: pivote reciente + el precio reaccionó.
                            if not pool.get("barrida_confirmada"):
                                continue
                            if not config_bot.get("telegram_aviso_zona", True):
                                continue
                            if _aviso_permitido(
                                (simbolo, "liquidez", tf, pool["tipo"], _bucket_precio(pool["precio"], 0.15)),
                                COOLDOWN_AVISOS["liquidez"],
                            ):
                                notificar(
                                    f"💧 <b>Liquidez externa barrida</b>\n"
                                    f"{simbolo} · {tf} · "
                                    f"{'máximos' if pool['tipo'] == 'high' else 'mínimos'} "
                                    f"cercanos a ${pool['precio']:.2f} · el precio ya reaccionó"
                                )
                    # Conflictos: UN solo mensaje por símbolo (antes 1 por cada par de
                    # temporalidades), solo si cambió la combinación y pasó el cooldown.
                    incluir_micro_c = bool(config_bot.get("aviso_contexto_incluir_micro", False))
                    grupos_tf = {tf: i.get("grupo") for tf, i in contexto_estructura["temporalidades"].items()}
                    conflictos_validos = [
                        c for c in contexto_estructura["conflictos"]
                        if incluir_micro_c or grupos_tf.get(c["menor"]) != "micro"
                    ]
                    firma = tuple(sorted(
                        (c["mayor"], c["sesgo_mayor"], c["menor"], c["sesgo_menor"]) for c in conflictos_validos
                    ))
                    if not firma:
                        conflictos_firma_por_simbolo[simbolo] = ()
                    elif (
                        firma != conflictos_firma_por_simbolo.get(simbolo)
                        and config_bot.get("telegram_aviso_zona", True)
                        and _aviso_permitido((simbolo, "conflicto"), COOLDOWN_AVISOS["conflicto"])
                    ):
                        conflictos_firma_por_simbolo[simbolo] = firma
                        por_mayor = {}
                        for c in conflictos_validos:
                            por_mayor.setdefault((c["mayor"], c["sesgo_mayor"], c["sesgo_menor"]), []).append(c["menor"])
                        lineas = [
                            f"{simbolo} · {mayor} {sm} frente a {', '.join(menores)} {sme}"
                            for (mayor, sm, sme), menores in por_mayor.items()
                        ]
                        notificar(
                            "⚠️ <b>Conflicto entre temporalidades</b>\n"
                            + "\n".join(lineas)
                            + "\nSe prioriza el marco mayor."
                        )
                if config_bot["estrategia"] == "estructura_dinamica":
                    senal_dinamica = evaluar_estructura_dinamica(df, config_bot["parametros"], df_macro=df_macro)
                    senal = senal_dinamica["tipo"] if senal_dinamica else None
                    p = config_bot["parametros"]
                    estado["estado_zona"] = est.estado_actual_estructura(
                        df,
                        pivot_len=int(p.get("pivot_len", 5)),
                        pivot_len_macro=int(p.get("pivot_len_macro", 15)),
                        tolerancia_pct=float(p.get("tolerancia_pct", 0.15)),
                        porcentaje_tp=float(p.get("porcentaje_tp", 0.8)),
                        sl_buffer_pct=float(p.get("sl_buffer_pct", 0.1)),
                        max_velas_desde_zona=(int(p["max_velas_desde_zona"]) if float(p.get("max_velas_desde_zona", 0)) > 0 else None),
                        df_macro=df_macro,
                        minimo_ratio_tp=(float(p["minimo_ratio_tp"]) if float(p.get("minimo_ratio_tp", 0)) > 0 else None),
                    )
                else:
                    estado["estado_zona"] = None

                global ultima_zona_notificada_por_simbolo
                zona = estado["estado_zona"]
                if zona and zona.get("estado") == "esperando_confirmacion":
                    clave_zona = (zona.get("tipo"), zona.get("zona_precio"))
                    if clave_zona != ultima_zona_notificada_por_simbolo.get(simbolo):
                        ultima_zona_notificada_por_simbolo[simbolo] = clave_zona
                        if config_bot["telegram_aviso_zona"]:
                            notificar(
                                f"⏳ <b>Zona tocada — esperando confirmación</b>\n"
                                f"{simbolo} · {(zona.get('tipo') or '').upper()} en {zona.get('zona_precio')}\n"
                                f"Aún falta el rompimiento de estructura (BOS) a favor."
                            )
                # OJO: a propósito NO se borra ultima_zona_notificada_por_simbolo
                # cuando el estado pasa a "sin_zona". El indicador puede
                # "parpadear" (esperando_confirmacion -> sin_zona -> esperando_confirmacion
                # otra vez, con el MISMO precio de zona) de una vela a otra por
                # ruido normal del precio, y si se borraba aquí, cada parpadeo
                # mandaba el aviso de nuevo — eso era el spam de "Zona tocada"
                # repetida cada 1-3 minutos con el mismo precio. Ahora solo se
                # vuelve a avisar cuando aparece una zona REALMENTE distinta
                # (otro tipo o otro precio).
                #
                # ... con una excepción: cuando la zona ya se CONFIRMÓ (se
                # ejecutó o está por ejecutarse la operación), sí se libera la
                # memoria, para que la siguiente zona de espera (aunque caiga
                # en un precio parecido) pueda avisar de nuevo.
                if zona and zona.get("estado") == "confirmada":
                    ultima_zona_notificada_por_simbolo.pop(simbolo, None)

                if config_bot["estrategia"] == "estructura_dinamica":
                    pass
                elif config_bot["codigo_estrategia"]:
                    senal = evaluar_codigo_libre(
                        df, config_bot["codigo_estrategia"], df_macro=df_macro,
                        velas_por_temporalidad=velas_por_temporalidad,
                        grupos_temporalidad=config_bot.get("grupos_temporalidad") or None,
                        roles_temporalidad=config_bot.get("roles_temporalidad") or None,
                        zonas_permitidas=config_bot.get("zonas_permitidas") or None,
                        parametros=config_bot.get("parametros") or None,
                    )
                    if isinstance(senal, list):
                        operaciones_multiples = senal  # varias entradas a la vez (una por temporalidad), riesgo repartido
                        parejas = {}
                        for operacion in operaciones_multiples:
                            escala = operacion.get("escala")
                            if escala not in ("runner", "corta"):
                                continue
                            sufijo = f"_{escala}"
                            base = operacion["id"][:-len(sufijo)] if operacion["id"].endswith(sufijo) else operacion["id"]
                            parejas.setdefault(base, []).append(operacion)
                        for pareja in parejas.values():
                            if len(pareja) != 2:
                                continue
                            for operacion in pareja:
                                if config_bot["modo_riesgo"] in ("porcentaje", "monto_fijo"):
                                    operacion["riesgo_valor"] = config_bot["riesgo_valor"] / 2
                                else:
                                    operacion["volumen"] = config_bot["volumen_operacion"] / 2
                        senal = None  # no sigue el camino de "una sola señal" más abajo
                    elif isinstance(senal, dict):
                        senal_dinamica = senal  # trae su propio sl/tp, igual que estructura_dinamica
                        senal = senal_dinamica["tipo"]
                elif config_bot["condiciones_compra"] or config_bot["condiciones_venta"]:
                    senal = evaluar_estrategia_bloques(
                        df,
                        config_bot["condiciones_compra"],
                        config_bot["operador_compra"],
                        config_bot["condiciones_venta"],
                        config_bot["operador_venta"],
                    )
                elif config_bot["condicion_compra_texto"] or config_bot["condicion_venta_texto"]:
                    senal = evaluar_estrategia_texto(
                        df,
                        config_bot["condicion_compra_texto"],
                        config_bot["condicion_venta_texto"],
                    )
                elif config_bot["estrategia"]:
                    senal = evaluar_estrategia(df, config_bot["estrategia"], config_bot["parametros"])

                precio = obtener_precio_actual(simbolo=simbolo)
                estado["precio"] = precio
                estado["senal"] = senal or (
                    operaciones_multiples[0]["tipo"]
                    if operaciones_multiples else None
                )
                estado["simbolo_actual"] = simbolo
                estado["ultima_actualizacion"] = datetime.now().strftime("%H:%M:%S")
                estado["error"] = None

                if sesgo_macro and config_bot.get("proteger_cambio_direccion"):
                    movidos_dir = []
                    for posicion in obtener_posiciones_abiertas(simbolo=simbolo):
                        posicion_alcista = posicion["tipo"] == "compra"
                        sesgo_contrario = (
                            sesgo_macro == "bajista" and posicion_alcista
                        ) or (
                            sesgo_macro == "alcista" and not posicion_alcista
                        )
                        en_ganancia = posicion["profit"] > 0
                        if sesgo_contrario and en_ganancia and posicion["ticket"] not in tickets_con_be_aplicado:
                            resultado_proteccion = modificar_sl(
                                posicion["ticket"], posicion["precio_apertura"]
                            )
                            if resultado_proteccion.get("ok"):
                                tickets_con_be_aplicado.add(posicion["ticket"])
                                _guardar_avisos_enviados()
                                movidos_dir.append((posicion["ticket"], f"sesgo macro {sesgo_macro.upper()}"))
                    encolar_aviso_be("Protección por cambio de dirección", simbolo, movidos_dir, notificar)

                if (
                    sesgo_macro
                    and config_bot.get("filtro_sesgo_macro")
                    and senal in ("compra", "venta")
                    and ((senal == "compra" and sesgo_macro != "alcista") or (senal == "venta" and sesgo_macro != "bajista"))
                ):
                    estado["error"] = (
                        f"Entrada {senal} bloqueada: sesgo macro {sesgo_macro}"
                    )
                    senal = None

                historial_reciente = obtener_historial_cerradas(dias=1, simbolo=simbolo)
                for cierre in historial_reciente:
                    razon = cierre.get("reason")
                    if razon not in ("sl", "tp"):
                        continue
                    clave = (
                        cierre.get("deal_id") or cierre.get("ticket"),
                        razon,
                        cierre.get("hora"),
                    )
                    if clave in sl_tp_notificados:
                        continue
                    sl_tp_notificados.add(clave)
                    _guardar_avisos_enviados()
                    if razon == "tp":
                        try:
                            _cascada_be_por_tp(cierre, simbolo)
                        except Exception as e:
                            print(f"[cascada BE] error: {e}")
                    if not config_bot.get("telegram_aviso_sl_tp", True):
                        continue
                    tipo_desc = "SL" if razon == "sl" else "TP"
                    texto = (
                        f"{'🛑' if razon == 'sl' else '✅'} <b>{tipo_desc} activado</b>\n"
                        f"{simbolo} · Ticket {cierre.get('ticket')} · {cierre.get('tipo', '').upper()}\n"
                        f"Volumen: {cierre.get('volumen')} · Profit: {cierre.get('profit')}\n"
                        f"Precio de cierre: {cierre.get('precio')}"
                    )
                    notificar(texto)

                if config_bot.get("bitacora_auto_activa", True):
                    _diario_cerrar()

                if config_bot["be_activo"]:
                    for p in obtener_posiciones_abiertas(simbolo=simbolo):
                        ticket = p["ticket"]
                        if ticket in tickets_con_be_aplicado or ticket in tickets_be_deshabilitado or not p["tp"]:
                            continue
                        distancia_tp = abs(p["tp"] - p["precio_apertura"])
                        umbral = distancia_tp * config_bot["be_porcentaje"]
                        avance = (
                            (p["precio_actual"] - p["precio_apertura"]) if p["tipo"] == "compra"
                            else (p["precio_apertura"] - p["precio_actual"])
                        )
                        if avance >= umbral:
                            resultado_be = modificar_sl(ticket, p["precio_apertura"])
                            if resultado_be.get("ok"):
                                tickets_con_be_aplicado.add(ticket)
                                _guardar_avisos_enviados()

                # --- BE dinámico: Fibo del tramo / nueva estructura a favor /
                # pérdida de alineación entre temporalidades. Basta con que se
                # cumpla UNA de las tres para proteger (ver est.be_dinamico).
                if config_bot.get("be_dinamico_activo"):
                    movidos_be_din = []
                    for p in obtener_posiciones_abiertas(simbolo=simbolo):
                        ticket = p["ticket"]
                        if ticket in tickets_con_be_aplicado or ticket in tickets_be_deshabilitado:
                            continue
                        try:
                            proteger, motivo = est.be_dinamico(
                                df_entrada=df,
                                velas_por_temporalidad=velas_por_temporalidad,
                                tipo_posicion=p["tipo"],
                                precio_apertura=p["precio_apertura"],
                                precio_actual=p["precio_actual"],
                                fibo_nivel=config_bot.get("be_dinamico_fibo_nivel", 0.5),
                                grupos_temporalidad=config_bot.get("grupos_temporalidad") or None,
                            )
                        except Exception as e:
                            print(f"[BE dinámico] error: {e}")
                            continue
                        if proteger:
                            resultado_be_din = modificar_sl(ticket, p["precio_apertura"])
                            if resultado_be_din.get("ok"):
                                tickets_con_be_aplicado.add(ticket)
                                _guardar_avisos_enviados()
                                movidos_be_din.append((ticket, motivo))
                    if movidos_be_din and config_bot.get("telegram_aviso_sl_tp", True):
                        encolar_aviso_be("BE dinámico", simbolo, movidos_be_din, notificar)

                # --- Noticia de alto impacto: (1) no abrir nada nuevo (también en
                # la ruta de operaciones múltiples) y (2) proteger con BE las
                # abiertas que ya van en ganancia. ---
                evento_noticia = None
                if config_bot.get("bloqueo_noticias_activo") or config_bot.get("noticia_be_activo"):
                    try:
                        from . import fundamentales as fnd
                        evento_noticia = fnd.bloqueo_por_noticia(
                            simbolo,
                            minutos_antes=config_bot.get("bloqueo_noticias_minutos_antes", 30),
                        )
                    except Exception as e:
                        print(f"[noticias] error: {e}")
                if evento_noticia is not None and config_bot.get("noticia_be_activo"):
                    movidos_noticia = []
                    for p in obtener_posiciones_abiertas(simbolo=simbolo):
                        if (
                            p["ticket"] in tickets_con_be_aplicado
                            or p["ticket"] in tickets_be_deshabilitado
                            or p["profit"] <= 0
                        ):
                            continue
                        res_be_noticia = modificar_sl(p["ticket"], p["precio_apertura"])
                        if res_be_noticia.get("ok"):
                            tickets_con_be_aplicado.add(p["ticket"])
                            _guardar_avisos_enviados()
                            movidos_noticia.append((p["ticket"], f"noticia {evento_noticia.get('title')}"))
                    if config_bot.get("telegram_aviso_sl_tp", True):
                        encolar_aviso_be("BE por noticia", simbolo, movidos_noticia, notificar)

                limite_operaciones = config_bot.get("max_operaciones_por_simbolo") or 0
                se_alcanzo_limite = (
                    limite_operaciones > 0
                    and len(obtener_posiciones_abiertas(simbolo=simbolo)) >= limite_operaciones
                )
                if operaciones_multiples and evento_noticia is not None and config_bot.get("bloqueo_noticias_activo"):
                    estado["error"] = (
                        f"Entrada bloqueada por noticia: {evento_noticia.get('title')} "
                        f"({evento_noticia.get('country')})"
                    )
                elif operaciones_multiples and not se_alcanzo_limite:
                    abiertas_ahora = obtener_posiciones_abiertas(simbolo=simbolo)
                    lote_actual = _lote_activo(abiertas_ahora)
                    aperturas_en_ciclo = 0
                    for op in operaciones_multiples:
                        if limite_operaciones > 0 and len(abiertas_ahora) + aperturas_en_ciclo >= limite_operaciones:
                            break
                        clave_dedupe = (simbolo, op["id"])
                        if clave_dedupe in ids_operaciones_multiples_ejecutadas:
                            continue

                        # Anti-contradicción: no abrir en la misma temporalidad
                        # en sentido contrario a una posición ya abierta.
                        tf_op = op.get("temporalidad_principal", "")
                        if any(
                            _parsear_comment_lote(p.get("comment"))[1] == tf_op and p["tipo"] != op["tipo"]
                            for p in abiertas_ahora
                        ):
                            ids_operaciones_multiples_ejecutadas.add(clave_dedupe)
                            continue

                        sl_envio = op.get("sl") or 0.0
                        tp_envio = op.get("tp") or 0.0
                        if config_bot.get("tp_por_liquidez") and op.get("tp_liquidez"):
                            tp_envio = op["tp_liquidez"]
                        volumen_envio = op.get("volumen") or config_bot["volumen_operacion"]

                        # Si esta operación trae su propio riesgo_valor (repartido por
                        # est.dividir_riesgo_entre_operaciones), se usa ESE en vez del
                        # % global de la estrategia; si no trae, cae al modo de riesgo
                        # configurado normalmente.
                        riesgo_de_esta_operacion = op.get("riesgo_valor")
                        pct_total_op = riesgo_total_por_operacion(config_bot, len(_simbolos_permitidos()))
                        if pct_total_op is not None:
                            riesgo_de_esta_operacion = pct_total_op  # tope total: manda sobre el valor de la estrategia
                        if riesgo_de_esta_operacion is not None and sl_envio:
                            info_cuenta = obtener_info_cuenta()
                            balance = info_cuenta["balance"] if info_cuenta else None
                            calculo = calcular_lotaje(
                                balance=balance,
                                precio_entrada=precio["ask"] if op["tipo"] == "compra" else precio["bid"],
                                sl=sl_envio,
                                simbolo=simbolo,
                                modo="porcentaje" if (config_bot["modo_riesgo"] == "porcentaje" or pct_total_op is not None) else "fijo",
                                valor=riesgo_de_esta_operacion,
                            )
                            if calculo is not None:
                                volumen_envio = calculo["lotaje"]
                        elif config_bot["modo_riesgo"] in ("porcentaje", "monto_fijo") and sl_envio:
                            info_cuenta = obtener_info_cuenta()
                            balance = info_cuenta["balance"] if info_cuenta else None
                            calculo = calcular_lotaje(
                                balance=balance,
                                precio_entrada=precio["ask"] if op["tipo"] == "compra" else precio["bid"],
                                sl=sl_envio,
                                simbolo=simbolo,
                                modo="porcentaje" if config_bot["modo_riesgo"] == "porcentaje" else "fijo",
                                valor=config_bot["riesgo_valor"],
                            )
                            if calculo is not None:
                                volumen_envio = calculo["lotaje"]

                        resultado_multi = enviar_orden_manual(
                            tipo=op["tipo"],
                            volumen=volumen_envio,
                            simbolo=simbolo,
                            sl=sl_envio,
                            tp=tp_envio,
                            spread_maximo_puntos=config_bot["spread_maximo_puntos"],
                            comment=_comentario_orden_estrategia(lote_actual, op),
                        )
                        ids_operaciones_multiples_ejecutadas.add(clave_dedupe)
                        estado["ultima_orden"] = resultado_multi
                        if resultado_multi.get("ok"):
                            aperturas_en_ciclo += 1

                        if resultado_multi.get("ok") and config_bot.get("bitacora_auto_activa", True):
                            _diario_abrir(
                                simbolo, op["tipo"], volumen_envio, resultado_multi, sl_envio, tp_envio,
                                origen=f"{op.get('bloque', 'zona')} {op.get('temporalidad_principal', '')}".strip(),
                            )
                        if resultado_multi.get("ok") and config_bot["telegram_aviso_apertura"]:
                            tf_txt = op.get("temporalidad_principal", "")
                            escala_txt = f" · {op['escala']}" if op.get("escala") else ""
                            bloque_txt = op.get("bloque", "zona")
                            confluencia_txt = op.get("confluencias_micro", 0)
                            notificar(
                                f"📈 <b>Apertura {bloque_txt.upper()} ({tf_txt}{escala_txt})</b>\n"
                                f"{simbolo} · {op['tipo'].upper()} · {volumen_envio} lotes\n"
                                f"Confirmó: {op.get('confirmado_en', '--')} · "
                                f"Ruptura: {op.get('tipo_ruptura', '--')} · "
                                f"Confluencias micro: {confluencia_txt}\n"
                                f"Zona: ${op.get('zona_precio', 0):.5f} · "
                                f"SL: ${sl_envio:.5f} · TP: ${tp_envio:.5f}\n"
                                f"{linea_estimacion(simbolo, volumen_envio, resultado_multi.get('precio'), sl_envio, tp_envio)}"
                            )

                if (
                    senal in ("compra", "venta")
                    and senal != ultima_senal_ejecutada_por_simbolo.get(simbolo)
                    and not se_alcanzo_limite
                ):
                    # Bloqueo por noticia fundamental: si hay un NFP/CPI/tasas
                    # "en proceso" o a punto de salir para las monedas de este
                    # símbolo, no se abre la operación nueva (las que ya están
                    # abiertas NO se tocan). Es un bloqueo, no un descarte de
                    # confluencias — la señal técnica sigue siendo válida, solo
                    # se espera a que pase la noticia.
                    evento_bloqueante = None
                    if config_bot.get("bloqueo_noticias_activo"):
                        from . import fundamentales as fnd
                        evento_bloqueante = fnd.bloqueo_por_noticia(
                            simbolo,
                            minutos_antes=config_bot.get("bloqueo_noticias_minutos_antes", 30),
                        )

                    if evento_bloqueante is not None:
                        estado["error"] = (
                            f"Entrada bloqueada por noticia: {evento_bloqueante.get('title')} "
                            f"({evento_bloqueante.get('country')})"
                        )
                    else:
                        if senal_dinamica is not None:
                            sl_envio = senal_dinamica.get("sl") or 0.0
                            tp_envio = senal_dinamica.get("tp") or 0.0
                        else:
                            sl_envio = config_bot["sl_operacion"]
                            tp_envio = config_bot["tp_operacion"]

                        volumen_envio = config_bot["volumen_operacion"]
                        estado["ultimo_calculo_riesgo"] = None
                        pct_total_op = riesgo_total_por_operacion(config_bot, len(_simbolos_permitidos()))
                        if (pct_total_op is not None or config_bot["modo_riesgo"] in ("porcentaje", "monto_fijo")) and sl_envio:
                            info_cuenta = obtener_info_cuenta()
                            balance = info_cuenta["balance"] if info_cuenta else None
                            calculo = calcular_lotaje(
                                balance=balance,
                                precio_entrada=precio["ask"] if senal == "compra" else precio["bid"],
                                sl=sl_envio,
                                simbolo=simbolo,
                                modo="porcentaje" if (config_bot["modo_riesgo"] == "porcentaje" or pct_total_op is not None) else "fijo",
                                valor=pct_total_op if pct_total_op is not None else config_bot["riesgo_valor"],
                            )
                            if calculo is not None:
                                volumen_envio = calculo["lotaje"]
                                estado["ultimo_calculo_riesgo"] = calculo

                        resultado = enviar_orden_manual(
                            tipo=senal,
                            volumen=volumen_envio,
                            simbolo=simbolo,
                            sl=sl_envio,
                            tp=tp_envio,
                            spread_maximo_puntos=config_bot["spread_maximo_puntos"],
                        )
                        estado["ultima_orden"] = resultado
                        estado["ultima_senal_ejecutada"] = senal  # solo informativo (último símbolo operado)
                        ultima_senal_ejecutada_por_simbolo[simbolo] = senal

                        if resultado.get("ok") and config_bot.get("bitacora_auto_activa", True):
                            _diario_abrir(simbolo, senal, volumen_envio, resultado, sl_envio, tp_envio, origen="señal")
                        if resultado.get("ok") and config_bot["telegram_aviso_apertura"]:
                            # Incluye SIEMPRE la cuenta/servidor donde realmente se ejecutó
                            # la orden. Si el .env / cuentas.json apunta a una cuenta o un
                            # servidor distinto al que tienes abierto en la ventana de MT5,
                            # la orden se ejecuta igual (retcode DONE, ticket real) pero en
                            # ESA otra cuenta, y en tu pantalla no aparece nada. Con esta
                            # línea se ve de inmediato si la cuenta coincide con la que
                            # crees que estás viendo.
                            info_cuenta_orden = obtener_info_cuenta()
                            linea_cuenta = (
                                f"Cuenta: {info_cuenta_orden['cuenta']} · {info_cuenta_orden['servidor']}"
                                f" · {'REAL' if info_cuenta_orden['es_real'] else 'DEMO'}\n"
                                if info_cuenta_orden else ""
                            )
                            notificar(
                                f"✅ <b>Operación abierta</b>\n"
                                f"{simbolo} · {senal.upper()} · {volumen_envio} lotes\n"
                                f"Precio: {resultado.get('precio')} · SL {sl_envio} · TP {tp_envio}\n"
                                f"Ticket: {resultado.get('ticket')}\n"
                                f"{linea_estimacion(simbolo, volumen_envio, resultado.get('precio'), sl_envio, tp_envio)}"
                                f"{linea_cuenta}"
                            )
                        elif not resultado.get("ok"):
                            # Antes esto se guardaba solo en estado["ultima_orden"] (visible
                            # en la web si la tenías abierta en ese momento) pero no avisaba
                            # por Telegram: una señal rechazada por MT5 se perdía en
                            # silencio, igual que pasó con el retcode 10016. Se avisa
                            # siempre, esté o no prendido "telegram_aviso_apertura", porque
                            # es un error, no una apertura exitosa.
                            notificar(
                                f"⚠️ <b>Señal detectada pero la orden fue rechazada</b>\n"
                                f"{simbolo} · {senal.upper()}\n"
                                f"Motivo: {resultado.get('error')}"
                            )

        except Exception as e:
            estado["error"] = str(e)

        # Antes dormía 60 s fijos: el análisis (y todo lo que dependía de él: detectar
        # SL/TP cerrados, BE, señales) llegaba con hasta 1 min de retraso. Ahora es
        # configurable (config_bot["intervalo_ciclo_seg"], por defecto 10 s).
        time.sleep(max(2, min(60, int(config_bot.get("intervalo_ciclo_seg", 10) or 10))))


def ciclo_precio():
    """Hilo ligero: refresca SOLO el tick (bid/ask) cada segundo, independiente del
    ciclo pesado de análisis, para que el precio del panel vaya al ritmo de MT5."""
    while True:
        try:
            if estado.get("conectado"):
                simbolo = estado.get("simbolo_actual") or config_bot.get("simbolo")
                if simbolo:
                    precio = obtener_precio_actual(simbolo=simbolo)
                    if precio:
                        estado["precio"] = precio
                        estado["ultima_actualizacion"] = datetime.now().strftime("%H:%M:%S")
        except Exception:
            pass
        time.sleep(1)


@app.on_event("startup")
def iniciar_bot():
    try:
        ok = conectar()
        estado["conectado"] = ok
        if ok:
            hilo = threading.Thread(target=ciclo_bot, daemon=True)
            hilo.start()
            threading.Thread(target=ciclo_precio, daemon=True).start()
    except Exception as e:
        print(f"⚠ Aviso: No se pudo conectar a MT5: {e}")
        print("   El servidor funciona en modo demo (sin MT5)")
        estado["conectado"] = False

    hilo_comandos = threading.Thread(
        target=escuchar_comandos_operaciones,
        args=(
            config_bot, notificar, obtener_posiciones_abiertas,
            cerrar_posicion, cerrar_posicion_parcial, modificar_sl, modificar_tp,
            obtener_info_cuenta, obtener_historial_cerradas, _deshabilitar_be_ticket,
        ),
        daemon=True,
    )
    hilo_comandos.start()

    hilo_fundamentales = threading.Thread(target=_ciclo_fundamentales, daemon=True)
    hilo_fundamentales.start()


def _ciclo_fundamentales():
    """
    Descarga el calendario económico (NFP/CPI/tasas) automáticamente al
    arrancar el bot, y lo vuelve a refrescar cada 30 minutos para ir
    llenando el 'actual' de las noticias a medida que se publican.
    Usa forzar=True porque descargar_calendario() ya respeta su propio
    caché interno (CACHE_HORAS) para lo normal — aquí forzamos cada 30
    min a propósito, muy por debajo del límite de ForexFactory (2 cada
    5 min), para que "en_proceso"/"pasados" se actualicen con dato real
    pronto después de cada publicación.
    """
    from . import fundamentales as fnd

    while True:
        try:
            fnd.descargar_calendario(forzar=True)
        except Exception as e:
            print(f"⚠ Aviso: no se pudo actualizar el calendario de fundamentales: {e}")
        time.sleep(30 * 60)


@app.on_event("shutdown")
def apagar_bot():
    desconectar()


# ---------- Modelos de request ----------
class OrdenRequest(BaseModel):
    tipo: str  # "compra" o "venta"
    volumen: float
    sl: float = 0.0
    tp: float = 0.0
    tipo_orden: str = "mercado"  # "mercado" o "limite"
    precio_entrada: float = 0.0


class ParcialRequest(BaseModel):
    volumen: float


class ConfigRequest(BaseModel):
    simbolo: str | None = None
    simbolos_permitidos: list[str] | None = None
    estrategia: str | None = None
    parametros: dict | None = None
    activo: bool | None = None
    timeframe_entrada: str | None = None  # ej. "M15" - solo usado por estructura_dinamica
    timeframe_macro: str | None = None    # ej. "H1" - solo usado por estructura_dinamica
    temporalidades_escaneo: list[str] | None = None  # ej. ["M5","M15","H1","H4"] - para est.escanear_multitemporalidad() en código libre
    roles_temporalidad: dict[str, str] | None = None
    zonas_permitidas: list[str] | None = None
    be_activo: bool | None = None         # activa/desactiva el breakeven automático
    be_porcentaje: float | None = None    # 0.8 = mueve a BE al 80% del camino hacia el TP
    be_cascada_buffer_pct: float | None = None  # % de la distancia al SL que debe avanzar antes de proteger en cascada (evita "muy pronto")
    be_dinamico_activo: bool | None = None      # activa/desactiva el BE combinado (fibo / nueva estructura / pérdida de alineación)
    be_dinamico_fibo_nivel: float | None = None  # 0.5 = nivel 50% de Fibonacci del tramo de entrada
    grupos_temporalidad: dict[str, str] | None = None  # ej. {"M15":"macro"} - sobreescribe la clasificación micro/intermedia/macro
    filtro_sesgo_macro: bool | None = None
    aviso_cambio_direccion: bool | None = None
    proteger_cambio_direccion: bool | None = None
    tp_por_liquidez: bool | None = None   # True = operaciones múltiples usan TP en liquidez
    max_operaciones_por_simbolo: int | None = None  # 0 = sin límite; ej. 2 = máximo 2 abiertas a la vez por símbolo
    modo_riesgo: str | None = None        # "lotaje_fijo" | "porcentaje" | "monto_fijo"
    riesgo_valor: float | None = None
    volumen_operacion: float | None = None  # lotes fijos, cuando modo_riesgo == "lotaje_fijo"
    sl_operacion: float | None = None
    tp_operacion: float | None = None
    circuito_perdida_diaria_activo: bool | None = None
    circuito_perdida_diaria_pct: float | None = None
    spread_maximo_puntos: float | None = None
    bloqueo_noticias_activo: bool | None = None
    bloqueo_noticias_minutos_antes: int | None = None
    noticia_be_activo: bool | None = None
    riesgo_total_activo: bool | None = None
    riesgo_total_pct: float | None = None
    bitacora_auto_activa: bool | None = None


class Condicion(BaseModel):
    indicador: str
    comparador: str
    valor: str | float


class EstrategiaTextoRequest(BaseModel):
    condicion_compra: str = ""
    condicion_venta: str = ""


class BloqueCondicion(BaseModel):
    bloque: str
    parametros: dict = {}


class EstrategiaBloquesRequest(BaseModel):
    condiciones_compra: list[BloqueCondicion] = []
    operador_compra: str = "and"
    condiciones_venta: list[BloqueCondicion] = []
    operador_venta: str = "and"


class EstrategiaGuardadaRequest(BaseModel):
    nombre: str
    explicacion: str = ""
    codigo: str = ""
    volumen: float = 0.01
    sl: float = 0.0
    tp: float = 0.0
    # --- Gestión de riesgo de esta estrategia ---
    # modo_riesgo: "lotaje_fijo" (usa "volumen" tal cual), "porcentaje" (riesgo_valor
    # = % del balance) o "monto_fijo" (riesgo_valor = dinero fijo). En estos dos
    # últimos, el bot calcula el lotaje solo en cada operación según el SL.
    modo_riesgo: str = "lotaje_fijo"
    riesgo_valor: float = 1.0
    # --- Multi-temporalidad opcional para código libre ---
    usa_multi_temporalidad: bool = False
    timeframe_entrada: str = "M15"
    timeframe_macro: str = "H1"
    # Temporalidades para est.escanear_multitemporalidad() en código libre
    # (ej. ["M5", "M15", "H1", "H4"]). Vacío = no se usa escaneo automático.
    temporalidades_escaneo: list[str] = []
    roles_temporalidad: dict[str, str] = {}
    zonas_permitidas: list[str] = []  # vacío = automático (todas las zonas)
    parametros: dict = {}
    # --- Activos en los que puede operar ESTA estrategia ---
    # Lista vacía = usa todos los "activos permitidos" globales (Control del
    # bot). Si trae símbolos, el bot solo la aplica en esos, aunque el global
    # tenga más activos permitidos.
    activos: list[str] = []
    # Si es una estrategia de motor dinámico (SpotPlus Estructura): al
    # activarla se reactiva el motor real en vez de tratar "codigo" como
    # código libre. Se preserva al editar/guardar desde el frontend.
    es_estructura_dinamica: bool = False


# ---------- Endpoints de estado ----------
@app.get("/api/estado")
def get_estado():
    return {**estado, "config": config_bot, "cuenta_id": CUENTA_ID or "default", "nombre_cuenta": NOMBRE_CUENTA}


@app.get("/api/cuenta")
def get_cuenta():
    info = obtener_info_cuenta()
    if info is None:
        return {"error": "No se pudo obtener info de cuenta"}
    return info


# ---------- Multi-cuenta (registro de hasta 3 cuentas MT5) ----------
# Nota: esto solo LEE/ESCRIBE cuentas.json (el catalogo de cuentas). No
# arranca ni detiene procesos: eso lo hace backend/multicuenta.py. Despues de
# agregar/quitar una cuenta aqui hay que reiniciar "python -m backend.multicuenta iniciar"
# para que levante (o baje) el proceso correspondiente.
class CuentaRequest(BaseModel):
    id: str
    nombre: str
    login: int
    password: str
    server: str
    terminal_path: str
    activa: bool = True


def _cuenta_sin_password(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "password"}


@app.get("/api/cuentas")
def get_cuentas():
    """Cuentas registradas en ESTE catalogo compartido, sin exponer passwords."""
    return [_cuenta_sin_password(c) for c in cuentas_mod.listar_cuentas()]


@app.post("/api/cuentas")
def post_cuenta(c: CuentaRequest):
    resultado = cuentas_mod.agregar_cuenta(
        c.id, c.nombre, c.login, c.password, c.server, c.terminal_path, c.activa
    )
    if resultado.get("ok") and "cuenta" in resultado:
        resultado["cuenta"] = _cuenta_sin_password(resultado["cuenta"])
    return resultado


@app.delete("/api/cuentas/{id_cuenta}")
def delete_cuenta(id_cuenta: str):
    return cuentas_mod.eliminar_cuenta(id_cuenta)


@app.post("/api/cuentas/{id_cuenta}/activar")
def activar_cuenta_endpoint(id_cuenta: str, activa: bool = True):
    resultado = cuentas_mod.activar_cuenta(id_cuenta, activa)
    if resultado.get("ok") and "cuenta" in resultado:
        resultado["cuenta"] = _cuenta_sin_password(resultado["cuenta"])
    return resultado


@app.get("/api/posiciones")
def get_posiciones():
    # simbolo=None -> todas las posiciones abiertas, sin importar en qué
    # símbolo esté operando el bot en ese momento (antes solo mostraba
    # config_bot["simbolo"], que es el símbolo "principal" y no cambia
    # aunque el bot esté operando otro par de simbolos_permitidos).
    return obtener_posiciones_abiertas(simbolo=None)


@app.get("/api/historial")
def get_historial(dias: int = 7):
    return obtener_historial_cerradas(dias=dias, simbolo=None)


class BacktestRequest(BaseModel):
    desde: str
    hasta: str
    balance_inicial: float = 10000.0
    timeframe_entrada: str | None = None
    timeframe_macro: str | None = None


# ---------- Backtest en background con progreso ----------
# El backtest puede tardar bastante (miles de velas, cálculo vela por vela).
# En vez de bloquear al cliente en un solo POST, aquí se lanza un hilo aparte
# y se guarda el avance en BACKTEST_JOBS[job_id]; la interfaz consulta el
# progreso con polling en /api/backtest/progreso/{job_id} en vez de quedarse
# congelada sin saber cuánto falta.
BACKTEST_JOBS: dict = {}


def _velas_backtest(simbolo, desde, hasta, tf):
    """MT5 primero; si no devuelve nada (p. ej. M1 solo guarda ~100k velas), usa los
    CSV locales exportados de MT5 (backend/historico_csv.py). Devuelve (df, fuente)."""
    d = obtener_velas_historicas(simbolo, desde, hasta, timeframe_str=tf)
    return historico_csv.completar_con_csv(d, simbolo, str(tf).upper(), desde, hasta)


def _correr_backtest_en_hilo(job_id, desde, hasta, balance_inicial, timeframe_entrada=None, timeframe_macro=None):
    job = BACKTEST_JOBS[job_id]
    try:
        simbolo = config_bot["simbolo"]
        # Estas temporalidades aplican solo a esta simulación; no modifican
        # la configuración ni la estrategia que el bot usa en vivo.
        tf_entrada = timeframe_entrada or config_bot.get("timeframe_entrada") or None
        avisos = []
        df, _fuente_entrada = _velas_backtest(simbolo, desde, hasta, tf_entrada)
        if df is None or len(df) == 0:
            job["listo"] = True
            job["resultado"] = {"error": f"No se encontraron velas de {simbolo} en ese rango de fechas. Revisa que MT5 esté conectado y tenga histórico descargado para ese periodo."}
            return

        df_macro = None
        tf_macro = timeframe_macro or config_bot.get("timeframe_macro") or None
        if config_bot.get("estrategia") == "estructura_dinamica" and tf_macro:
            df_macro = obtener_velas_historicas(simbolo, desde, hasta, timeframe_str=tf_macro)

        contrato = obtener_tamano_contrato(simbolo) or 100.0

        # La simulación usa las temporalidades elegidas para ESTE backtest
        # (sin tocar la config del bot en vivo).
        config_sim = dict(config_bot)
        # Ventana de velas por marco SOLO para el backtest. Con 200 (lo que usa el bot en vivo)
        # la ruptura + retroceso de las zonas altas casi nunca cabe en M1/M3/M5 y salen 0 señales.
        # Para volver al comportamiento anterior: pon 200 aquí.
        if config_sim.get("codigo_estrategia") and config_sim.get("codigo_usa_multi_temporalidad"):
            config_sim.setdefault("velas_por_temporalidad_backtest", 3000)
        if tf_entrada:
            config_sim["timeframe_entrada"] = tf_entrada
        if tf_macro:
            config_sim["timeframe_macro"] = tf_macro

        # Estrategias de código libre multi-temporalidad: en vivo reciben velas de
        # TODAS las temporalidades de escaneo. Aquí se descargan también (con un
        # colchón de histórico antes de "desde") para que el backtest vea lo mismo
        # y abra operaciones como lo haría el bot.
        dfs_tf = None
        if config_sim.get("codigo_estrategia") and config_sim.get("codigo_usa_multi_temporalidad"):
            tfs = [str(t).upper() for t in (config_sim.get("temporalidades_escaneo") or [])]
            for extra in (tf_entrada, tf_macro):
                if extra and str(extra).upper() not in tfs:
                    tfs.append(str(extra).upper())
            colchon_dias = {"M1": 5, "M3": 10, "M5": 15, "M15": 30, "M30": 45, "H1": 60, "H2": 90,
                            "H4": 150, "D1": 500, "W1": 2500, "MN1": 6000}
            desde_dt = datetime.strptime(desde, "%Y-%m-%d")
            dfs_tf = {}
            for tf in tfs:
                if tf == str(tf_entrada or "").upper() or tf not in TIMEFRAMES:
                    continue
                desde_tf = (desde_dt - timedelta(days=colchon_dias.get(tf, 60))).strftime("%Y-%m-%d")
                d_tf, fuente_tf = _velas_backtest(simbolo, desde_tf, hasta, tf)
                if d_tf is not None and len(d_tf):
                    dfs_tf[tf] = d_tf
                    if fuente_tf in ("csv", "mt5+csv"):
                        avisos.append(f"{tf} completado con CSV local")

            # Sin el marco más pequeño (normalmente M1) la estrategia nunca puede confirmar
            # entradas y el backtest termina con 0 señales sin explicar por qué. Se corta
            # aquí con un mensaje claro en vez de simular en vacío.
            faltantes_tf = [t for t in tfs if t != str(tf_entrada or "").upper() and t in TIMEFRAMES and t not in dfs_tf]
            if faltantes_tf:
                menor = min(faltantes_tf, key=lambda t: TF_SEGUNDOS.get(t, 10**9))
                if TF_SEGUNDOS.get(menor, 10**9) <= TF_SEGUNDOS.get(str(tf_entrada or "M15").upper(), 900):
                    job["listo"] = True
                    job["resultado"] = {"error": (
                        f"Falta histórico de {', '.join(faltantes_tf)}. MT5 no lo devolvió y no se encontró "
                        f"el CSV {simbolo}_{menor}_*.csv en la carpeta del proyecto. Exporta ese marco desde MT5 "
                        "(Ver > Símbolos > Barras > Exportar), ponlo en la raíz del proyecto y reinicia el servidor."
                    )}
                    return
                avisos.append(f"Sin histórico de: {', '.join(faltantes_tf)}")

            # El marco más pequeño (M1) confirma las entradas: si su histórico empieza
            # DESPUÉS de "desde", antes de esa fecha la estrategia nunca podría operar.
            # Se recorta el inicio del backtest para que todo el rango sea simulable.
            tf_micro = min((t for t in dfs_tf), key=lambda t: TF_SEGUNDOS.get(t, 10**9), default=None)
            if tf_micro:
                inicio_micro = dfs_tf[tf_micro]["time"].iloc[0]
                minimo = (inicio_micro + timedelta(days=colchon_dias.get(tf_micro, 5))).strftime("%Y-%m-%d")
                if minimo > desde:
                    if minimo >= hasta:
                        job["listo"] = True
                        job["resultado"] = {"error": f"El histórico de {tf_micro} empieza el {inicio_micro:%Y-%m-%d}; elige un rango que termine después de {minimo}."}
                        return
                    avisos.append(f"Inicio ajustado de {desde} a {minimo}: el histórico de {tf_micro} empieza el {inicio_micro:%Y-%m-%d}")
                    desde = minimo
                    df, _ = _velas_backtest(simbolo, desde, hasta, tf_entrada)

        def reportar(procesado, total):
            job["procesado"] = procesado
            job["total"] = total

        resultado = simular_backtest(
            df, config_sim, balance_inicial=balance_inicial, contrato=contrato,
            df_macro=df_macro, progreso_callback=reportar, dfs_tf=dfs_tf,
        )
        if "error" not in resultado:
            resultado["simbolo"] = simbolo
            resultado["desde"] = desde
            resultado["hasta"] = hasta
            resultado["velas_usadas"] = len(df)
            resultado["timeframe_entrada"] = tf_entrada
            resultado["timeframe_macro"] = tf_macro
            resultado["aviso"] = " · ".join(avisos) if avisos else None
        job["resultado"] = resultado
    except Exception as exc:  # nunca dejar el job colgado si algo revienta
        job["resultado"] = {"error": f"Error corriendo el backtest: {exc}"}
    finally:
        job["listo"] = True


@app.post("/api/backtest")
def post_backtest(req: BacktestRequest):
    """Arranca el backtest en un hilo aparte y devuelve un job_id de inmediato.
    El resultado (y el % de avance mientras tanto) se consulta con
    GET /api/backtest/progreso/{job_id}."""
    job_id = str(uuid.uuid4())
    BACKTEST_JOBS[job_id] = {"listo": False, "procesado": 0, "total": 0, "resultado": None}
    hilo = threading.Thread(
        target=_correr_backtest_en_hilo,
        args=(job_id, req.desde, req.hasta, req.balance_inicial, req.timeframe_entrada, req.timeframe_macro),
        daemon=True,
    )
    hilo.start()
    return {"job_id": job_id}


@app.get("/api/backtest/progreso/{job_id}")
def get_backtest_progreso(job_id: str):
    job = BACKTEST_JOBS.get(job_id)
    if job is None:
        return {"error": "job_id desconocido (¿se reinició el servidor?)"}
    porcentaje = round(job["procesado"] / job["total"] * 100, 1) if job["total"] else 0.0
    respuesta = {
        "listo": job["listo"],
        "procesado": job["procesado"],
        "total": job["total"],
        "porcentaje": porcentaje,
        "resultado": job["resultado"] if job["listo"] else None,
    }
    if job["listo"]:
        del BACKTEST_JOBS[job_id]  # limpiar una vez entregado
    return respuesta


@app.get("/api/backtest/velas")
def get_velas_replay_backtest(
    desde: str,
    hasta: str,
    timeframe: str,
    simbolo: str | None = None,
):
    """Carga velas OHLC reales de MT5 para cambiar de marco en el replay."""
    timeframe = timeframe.strip().upper()
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Temporalidad no válida: {timeframe}")
    try:
        datetime.strptime(desde, "%Y-%m-%d")
        datetime.strptime(hasta, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Las fechas deben tener formato YYYY-MM-DD") from exc

    simbolo_replay = (simbolo or config_bot["simbolo"]).strip()
    df, _ = _velas_backtest(simbolo_replay, desde, hasta, timeframe)
    if df is None or df.empty:
        return {
            "error": (
                f"MT5 no devolvió velas de {simbolo_replay} en {timeframe} "
                f"entre {desde} y {hasta}. Verifica que el histórico de ese marco "
                "esté descargado en MetaTrader 5."
            )
        }

    df = df.sort_values("time").drop_duplicates(subset="time").reset_index(drop=True)
    tiempos_epoch = timestamps_epoch_segundos(df["time"])
    velas = [
        {
            "time": int(tiempo),
            "open": round(float(apertura), 5),
            "high": round(float(maximo), 5),
            "low": round(float(minimo), 5),
            "close": round(float(cierre), 5),
        }
        for tiempo, apertura, maximo, minimo, cierre in zip(
            tiempos_epoch, df["open"], df["high"], df["low"], df["close"]
        )
    ]
    return {"timeframe": timeframe, "velas": velas}


@app.get("/api/estadisticas")
def get_estadisticas(dias: int = 30):
    """Estadísticas de gestión (winrate, profit factor, racha, etc.) calculadas
    sobre el historial real de operaciones cerradas en MT5 en los últimos N días."""
    deals = obtener_historial_cerradas(dias=dias, simbolo=None)
    return stats.calcular_estadisticas(deals)


@app.get("/api/bitacora")
def get_bitacora():
    return stats.cargar_bitacora()


class NotaBitacoraRequest(BaseModel):
    nota: str
    ticket: int | None = None


@app.post("/api/bitacora")
def post_bitacora(req: NotaBitacoraRequest):
    if not req.nota.strip():
        return {"error": "La nota no puede estar vacía"}
    return stats.agregar_nota(req.nota.strip(), req.ticket)


@app.delete("/api/bitacora/{id_nota}")
def delete_bitacora(id_nota: int):
    ok = stats.borrar_nota(id_nota)
    return {"ok": ok}


@app.delete("/api/bitacora")
def delete_toda_bitacora():
    ok = stats.borrar_toda_la_bitacora()
    return {"ok": ok}


# ---------- Diario de operaciones (bitácora automática) ----------
@app.get("/api/diario")
def get_diario(estado: str | None = None):
    """Fichas de operaciones: 'en_curso' mientras están abiertas y 'cerrada'
    (con resultado TP/SL/BE/Manual) al terminar."""
    _diario_cerrar()  # asegura que lo cerrado desde MT5 o a mano quede al día
    return stats.listar_diario(estado=estado)


@app.get("/api/diario/estadisticas")
def get_diario_estadisticas():
    _diario_cerrar()
    return stats.estadisticas_diario()


class NotasDiarioRequest(BaseModel):
    notas: str


@app.post("/api/diario/{id_ficha}/notas")
def post_notas_diario(id_ficha: int, req: NotasDiarioRequest):
    return {"ok": stats.editar_notas_diario(id_ficha, req.notas.strip())}


@app.delete("/api/diario/{id_ficha}")
def delete_ficha_diario(id_ficha: int):
    return {"ok": stats.borrar_ficha_diario(id_ficha)}


@app.get("/api/simbolos")
def get_simbolos(filtro: str = ""):
    return obtener_simbolos_disponibles(filtro=filtro or None)


@app.get("/api/estrategias")
def get_estrategias():
    return {
        clave: {"nombre": val["nombre"], "parametros": val["parametros"]}
        for clave, val in ESTRATEGIAS.items()
    }


@app.get("/api/fundamentales")
def get_fundamentales(monedas: str = ""):
    """
    Expone al frontend (mobile/web) la semana completa de noticias de alto
    impacto, ya clasificada en 3 grupos para pintar directo en la interfaz:
    'pendientes' (todavía no salen), 'en_proceso' (saliendo justo ahora /
    con retraso de publicación) y 'pasados' (ya con el dato real y si salió
    mejor/peor/en línea vs lo esperado).

    'monedas' es opcional, coma-separado, ej. "USD,EUR".
    """
    from . import fundamentales as fnd

    lista_monedas = [m.strip().upper() for m in monedas.split(",") if m.strip()] or None
    return fnd.calendario_semana_categorizado(lista_monedas)


# ---------- Endpoints de acción ----------
@app.post("/api/orden")
def post_orden(orden: OrdenRequest):
    resultado = enviar_orden_manual(
        tipo=orden.tipo,
        volumen=orden.volumen,
        simbolo=config_bot["simbolo"],
        sl=orden.sl,
        tp=orden.tp,
        tipo_orden=orden.tipo_orden,
        precio_entrada=orden.precio_entrada,
    )
    if resultado.get("ok"):
        # Toda operación manual también queda en la bitácora (en curso → cerrada).
        _diario_abrir(config_bot["simbolo"], orden.tipo, orden.volumen, resultado,
                      orden.sl, orden.tp, origen="manual")
    return resultado


@app.post("/api/cerrar/{ticket}")
def post_cerrar(ticket: int):
    resultado = cerrar_posicion(ticket)
    if resultado.get("ok") and config_bot["telegram_aviso_cierre"]:
        notificar(f"🔴 <b>Posición cerrada</b>\nTicket {ticket} · {config_bot['simbolo']}")
    return resultado


@app.post("/api/parcial/{ticket}")
def post_parcial(ticket: int, parcial: ParcialRequest):
    resultado = cerrar_posicion_parcial(ticket, parcial.volumen)
    if resultado.get("ok") and config_bot["telegram_aviso_parcial"]:
        notificar(f"🟡 <b>Cierre parcial</b>\nTicket {ticket} · {parcial.volumen} lotes · {config_bot['simbolo']}")
    return resultado


@app.post("/api/config")
def post_config(cfg: ConfigRequest):
    if cfg.simbolo is not None:
        config_bot["simbolo"] = cfg.simbolo.strip()
    if cfg.simbolos_permitidos is not None:
        config_bot["simbolos_permitidos"] = [s.strip() for s in cfg.simbolos_permitidos if str(s).strip()]
        if not config_bot["simbolos_permitidos"]:
            config_bot["simbolos_permitidos"] = [config_bot["simbolo"]]
    if cfg.estrategia is not None and cfg.estrategia in ESTRATEGIAS:
        config_bot["estrategia"] = cfg.estrategia
        # al cambiar de estrategia, resetea a sus parámetros por defecto
        config_bot["parametros"] = dict(ESTRATEGIAS[cfg.estrategia]["parametros"])
    if cfg.parametros is not None:
        config_bot["parametros"].update(cfg.parametros)
    if cfg.activo is not None:
        config_bot["activo"] = cfg.activo
    if cfg.timeframe_entrada is not None:
        config_bot["timeframe_entrada"] = _normalizar_timeframe(cfg.timeframe_entrada, "M15")
    if cfg.timeframe_macro is not None:
        config_bot["timeframe_macro"] = _normalizar_timeframe(cfg.timeframe_macro, "H1")
    if cfg.temporalidades_escaneo is not None:
        config_bot["temporalidades_escaneo"] = _normalizar_temporalidades_escaneo(
            cfg.temporalidades_escaneo
        )
    if cfg.roles_temporalidad is not None:
        config_bot["roles_temporalidad"] = dict(cfg.roles_temporalidad)
    if cfg.zonas_permitidas is not None:
        config_bot["zonas_permitidas"] = _limpiar_zonas_permitidas(cfg.zonas_permitidas)
    if cfg.be_activo is not None:
        config_bot["be_activo"] = cfg.be_activo
    if cfg.be_porcentaje is not None:
        config_bot["be_porcentaje"] = cfg.be_porcentaje
    if cfg.be_cascada_buffer_pct is not None:
        config_bot["be_cascada_buffer_pct"] = cfg.be_cascada_buffer_pct
    if cfg.be_dinamico_activo is not None:
        config_bot["be_dinamico_activo"] = cfg.be_dinamico_activo
    if cfg.be_dinamico_fibo_nivel is not None:
        config_bot["be_dinamico_fibo_nivel"] = cfg.be_dinamico_fibo_nivel
    if cfg.grupos_temporalidad is not None:
        config_bot["grupos_temporalidad"] = {
            tf.upper(): grupo for tf, grupo in cfg.grupos_temporalidad.items()
            if grupo in ("micro", "intermedia", "macro", "extra_macro", "no_usar")
        }
    if cfg.filtro_sesgo_macro is not None:
        config_bot["filtro_sesgo_macro"] = cfg.filtro_sesgo_macro
    if cfg.aviso_cambio_direccion is not None:
        config_bot["aviso_cambio_direccion"] = cfg.aviso_cambio_direccion
    if cfg.proteger_cambio_direccion is not None:
        config_bot["proteger_cambio_direccion"] = cfg.proteger_cambio_direccion
    if cfg.tp_por_liquidez is not None:
        config_bot["tp_por_liquidez"] = cfg.tp_por_liquidez
    if cfg.max_operaciones_por_simbolo is not None:
        config_bot["max_operaciones_por_simbolo"] = cfg.max_operaciones_por_simbolo
    if cfg.modo_riesgo is not None and cfg.modo_riesgo in ("lotaje_fijo", "porcentaje", "monto_fijo"):
        config_bot["modo_riesgo"] = cfg.modo_riesgo
    if cfg.riesgo_valor is not None:
        config_bot["riesgo_valor"] = cfg.riesgo_valor
    if cfg.volumen_operacion is not None:
        config_bot["volumen_operacion"] = cfg.volumen_operacion
    if cfg.sl_operacion is not None:
        config_bot["sl_operacion"] = cfg.sl_operacion
    if cfg.tp_operacion is not None:
        config_bot["tp_operacion"] = cfg.tp_operacion
    if cfg.circuito_perdida_diaria_activo is not None:
        config_bot["circuito_perdida_diaria_activo"] = cfg.circuito_perdida_diaria_activo
    if cfg.circuito_perdida_diaria_pct is not None:
        config_bot["circuito_perdida_diaria_pct"] = cfg.circuito_perdida_diaria_pct
    if cfg.spread_maximo_puntos is not None:
        config_bot["spread_maximo_puntos"] = cfg.spread_maximo_puntos
    if cfg.bloqueo_noticias_activo is not None:
        config_bot["bloqueo_noticias_activo"] = cfg.bloqueo_noticias_activo
    if cfg.bloqueo_noticias_minutos_antes is not None:
        config_bot["bloqueo_noticias_minutos_antes"] = cfg.bloqueo_noticias_minutos_antes
    if cfg.noticia_be_activo is not None:
        config_bot["noticia_be_activo"] = cfg.noticia_be_activo
    if cfg.bitacora_auto_activa is not None:
        config_bot["bitacora_auto_activa"] = cfg.bitacora_auto_activa
    if cfg.riesgo_total_activo is not None:
        config_bot["riesgo_total_activo"] = cfg.riesgo_total_activo
    if cfg.riesgo_total_pct is not None:
        config_bot["riesgo_total_pct"] = max(0.01, min(float(cfg.riesgo_total_pct), 100.0))
    guardar_config_bot()
    return config_bot


class CalculoRiesgoRequest(BaseModel):
    precio_entrada: float
    sl: float
    modo_riesgo: str = "porcentaje"  # "porcentaje" o "monto_fijo"
    riesgo_valor: float = 1.0
    simbolo: str | None = None
    tp: float | None = None
    parcial_pct: float | None = None
    parcial_distancia: float | None = None


@app.post("/api/calcular_riesgo")
def post_calcular_riesgo(req: CalculoRiesgoRequest):
    """Previsualiza el lotaje sugerido para un SL dado, sin mandar ninguna orden.
    Lo usa la pantalla de estrategia para mostrar 'lotaje sugerido' mientras
    Vale configura el % o monto a arriesgar."""
    info_cuenta = obtener_info_cuenta()
    balance = info_cuenta["balance"] if info_cuenta else None
    resultado = calculo_completo(
        balance=balance,
        entrada=req.precio_entrada,
        sl=req.sl,
        tp=req.tp or 0.0,
        simbolo=req.simbolo or config_bot["simbolo"],
        modo="porcentaje" if req.modo_riesgo == "porcentaje" else "fijo",
        valor=req.riesgo_valor,
        parcial_pct=req.parcial_pct,
        parcial_distancia=req.parcial_distancia,
    )
    if resultado is None:
        return {"error": "Faltan datos para calcular (revisa balance, precio y SL)"}
    return resultado


# ---------- Notificaciones a Telegram ----------
class TelegramConfigRequest(BaseModel):
    activo: bool | None = None
    bot_token: str | None = None
    chat_id: str | None = None
    aviso_apertura: bool | None = None
    aviso_cierre: bool | None = None
    aviso_sl_tp: bool | None = None
    aviso_parcial: bool | None = None
    aviso_zona: bool | None = None


def _config_telegram_publica():
    """Devuelve la config de Telegram para el frontend. El token se envía tal
    cual: esta app corre local/single-user, no hay multi-usuario que proteger."""
    return {
        "activo": config_bot["telegram_activo"],
        "bot_token": config_bot["telegram_bot_token"],
        "chat_id": config_bot["telegram_chat_id"],
        "aviso_apertura": config_bot["telegram_aviso_apertura"],
        "aviso_cierre": config_bot["telegram_aviso_cierre"],
        "aviso_sl_tp": config_bot.get("telegram_aviso_sl_tp", True),
        "aviso_parcial": config_bot["telegram_aviso_parcial"],
        "aviso_zona": config_bot["telegram_aviso_zona"],
    }


@app.get("/api/telegram")
def get_telegram():
    return _config_telegram_publica()


@app.post("/api/telegram")
def post_telegram(cfg: TelegramConfigRequest):
    if cfg.activo is not None:
        config_bot["telegram_activo"] = cfg.activo
    if cfg.bot_token is not None:
        token = cfg.bot_token.strip()
        if token:
            config_bot["telegram_bot_token"] = token
    if cfg.chat_id is not None:
        chat_id = cfg.chat_id.strip()
        if chat_id:
            config_bot["telegram_chat_id"] = chat_id
    if cfg.aviso_apertura is not None:
        config_bot["telegram_aviso_apertura"] = cfg.aviso_apertura
    if cfg.aviso_cierre is not None:
        config_bot["telegram_aviso_cierre"] = cfg.aviso_cierre
    if cfg.aviso_sl_tp is not None:
        config_bot["telegram_aviso_sl_tp"] = cfg.aviso_sl_tp
    if cfg.aviso_parcial is not None:
        config_bot["telegram_aviso_parcial"] = cfg.aviso_parcial
    if cfg.aviso_zona is not None:
        config_bot["telegram_aviso_zona"] = cfg.aviso_zona
    guardar_config_telegram()
    return _config_telegram_publica()


@app.post("/api/telegram/probar")
def post_telegram_probar():
    """Manda un mensaje de prueba usando el token/chat_id YA guardados,
    sin importar si las notificaciones automáticas están activas o no."""
    resultado = tg_enviar_mensaje(
        config_bot["telegram_bot_token"],
        config_bot["telegram_chat_id"],
        "🤖 SpotPlus Bot conectado correctamente. Aquí llegarán tus avisos.",
    )
    return resultado


@app.post("/api/estrategia_texto")
def post_estrategia_texto(estrategia: EstrategiaTextoRequest):
    config_bot["condicion_compra_texto"] = estrategia.condicion_compra
    config_bot["condicion_venta_texto"] = estrategia.condicion_venta
    config_bot["estrategia"] = None
    guardar_config_bot()
    return {
        "condicion_compra": config_bot["condicion_compra_texto"],
        "condicion_venta": config_bot["condicion_venta_texto"],
    }


@app.get("/api/indicadores_disponibles")
def get_indicadores_disponibles():
    return {
        "indicadores": COLUMNAS_DISPONIBLES,
        "comparadores": ["<", ">", "<=", ">="],
    }


@app.get("/api/bloques_disponibles")
def get_bloques_disponibles():
    """Catálogo de bloques (piezas de comportamiento de mercado) para el
    constructor visual de estrategias, más las columnas que se pueden usar
    dentro de ellos."""
    return {
        "columnas": COLUMNAS_DISPONIBLES,
        "bloques": {
            clave: {
                "nombre": b["nombre"],
                "explicacion": b["explicacion"],
                "parametros": b["parametros"],
            }
            for clave, b in BLOQUES.items()
        },
    }


@app.post("/api/estrategia_bloques")
def post_estrategia_bloques(estrategia: EstrategiaBloquesRequest):
    config_bot["condiciones_compra"] = [c.model_dump() for c in estrategia.condiciones_compra]
    config_bot["operador_compra"] = estrategia.operador_compra
    config_bot["condiciones_venta"] = [c.model_dump() for c in estrategia.condiciones_venta]
    config_bot["operador_venta"] = estrategia.operador_venta
    # al usar el constructor visual, se desactiva la estrategia de texto libre
    config_bot["condicion_compra_texto"] = ""
    config_bot["condicion_venta_texto"] = ""
    config_bot["estrategia"] = None
    return {
        "condiciones_compra": config_bot["condiciones_compra"],
        "operador_compra": config_bot["operador_compra"],
        "condiciones_venta": config_bot["condiciones_venta"],
        "operador_venta": config_bot["operador_venta"],
    }


# ---------- Biblioteca de estrategias (hoja: nombre + explicación + código) ----------
@app.get("/api/estrategias_guardadas")
def get_estrategias_guardadas():
    """Lista todas las estrategias guardadas en la biblioteca, con su nombre y explicación."""
    return biblioteca_estrategias


ZONAS_ESPERA_VALIDAS = ("flip", "ev", "obc", "fractal", "fractal2")


def _limpiar_zonas_permitidas(zonas) -> list[str]:
    """Deja solo nombres de zona válidos (flip/ev/obc/fractal/fractal2), sin repetir.
    Lista vacía = modo automático: el motor acepta todas las zonas que detecte."""
    if not zonas:
        return []
    if isinstance(zonas, str):
        zonas = zonas.split(",")
    limpias: list[str] = []
    for z in zonas:
        z = str(z).strip().lower().replace(" ", "").replace("_", "")
        if z in ZONAS_ESPERA_VALIDAS and z not in limpias:
            limpias.append(z)
    return limpias


def _guardar_estrategia_en_biblioteca(item: dict):
    """Guarda una estrategia en la biblioteca, creando o reemplazando el nombre indicado."""
    nombre = (item.get("nombre") or "").strip()
    if not nombre:
        return {"error": "El nombre no puede estar vacío"}

    nueva = {
        "nombre": nombre,
        "explicacion": (item.get("explicacion") or "").strip(),
        "codigo": (item.get("codigo") or "").strip(),
        "volumen": float(item.get("volumen", 0.01) or 0.01),
        "sl": float(item.get("sl", 0.0) or 0.0),
        "tp": float(item.get("tp", 0.0) or 0.0),
        "modo_riesgo": item.get("modo_riesgo", "lotaje_fijo") if item.get("modo_riesgo") in ("lotaje_fijo", "porcentaje", "monto_fijo") else "lotaje_fijo",
        "riesgo_valor": float(item.get("riesgo_valor", 1.0) or 1.0),
        # Si viene de "estructura_dinamica" (multi-temporalidad), se guarda TODO estructurado
        # (no como comentarios en "codigo") para poder reactivar el motor real al activarla.
        "es_estructura_dinamica": bool(item.get("es_estructura_dinamica", False)),
        "usa_multi_temporalidad": bool(item.get("usa_multi_temporalidad", False)),
        "timeframe_entrada": _normalizar_timeframe(item.get("timeframe_entrada"), "M15"),
        "timeframe_macro": _normalizar_timeframe(item.get("timeframe_macro"), "H1"),
        "temporalidades_escaneo": _normalizar_temporalidades_escaneo(
            item.get("temporalidades_escaneo")
        ),
        "roles_temporalidad": dict(item.get("roles_temporalidad") or {}),
        "zonas_permitidas": _limpiar_zonas_permitidas(item.get("zonas_permitidas")),
        "parametros": item.get("parametros", {}) or {},
        # Lista vacía = todos los activos permitidos globalmente.
        "activos": [str(a).strip().upper() for a in (item.get("activos") or []) if str(a).strip()],
    }

    existentes = [e for e in biblioteca_estrategias if e["nombre"] == nueva["nombre"]]
    if existentes:
        idx = biblioteca_estrategias.index(existentes[0])
        biblioteca_estrategias[idx] = nueva
    else:
        biblioteca_estrategias.append(nueva)

    guardar_biblioteca(biblioteca_estrategias)
    return nueva


@app.post("/api/estrategias_guardadas")
def post_estrategia_guardada(estrategia: EstrategiaGuardadaRequest):
    """Guarda (o actualiza, si el nombre ya existe) una estrategia en la biblioteca.
    No la activa automáticamente: solo queda guardada para usarla cuando quieras."""
    return _guardar_estrategia_en_biblioteca({
        "nombre": estrategia.nombre,
        "explicacion": estrategia.explicacion,
        "codigo": estrategia.codigo,
        "volumen": estrategia.volumen,
        "sl": estrategia.sl,
        "tp": estrategia.tp,
        "modo_riesgo": estrategia.modo_riesgo,
        "riesgo_valor": estrategia.riesgo_valor,
        "usa_multi_temporalidad": estrategia.usa_multi_temporalidad,
        "timeframe_entrada": estrategia.timeframe_entrada,
        "timeframe_macro": estrategia.timeframe_macro,
        "temporalidades_escaneo": estrategia.temporalidades_escaneo,
        "roles_temporalidad": estrategia.roles_temporalidad,
        "zonas_permitidas": estrategia.zonas_permitidas,
        "parametros": estrategia.parametros,
        "activos": estrategia.activos,
        "es_estructura_dinamica": estrategia.es_estructura_dinamica,
    })


@app.post("/api/estrategias_guardadas/desde_actual")
def post_estrategia_guardada_desde_actual(estrategia: EstrategiaGuardadaRequest):
    """Guarda la estrategia activa del backend en la biblioteca, aunque venga de un preset
    de SpotPlus o de otra estrategia no editada como código libre."""
    esencia = config_bot.get("estrategia")
    es_estructura = esencia == "estructura_dinamica"
    if es_estructura:
        params = config_bot.get("parametros", {}) or {}
        lineas_params = "\n".join(
            f"# {clave}={valor}" for clave, valor in params.items()
        )
        # "codigo" queda solo como resumen legible (para "Ver código"); lo que de verdad
        # se usa al activar son los campos estructurados de abajo (parametros, timeframes).
        codigo = (
            "# Estrategia SpotPlus multi-temporalidad (resumen, informativo)\n"
            f"# entrada={config_bot.get('timeframe_entrada', 'M15')}\n"
            f"# macro={config_bot.get('timeframe_macro', 'H1')}\n"
            f"# modo_riesgo={config_bot.get('modo_riesgo', 'lotaje_fijo')}\n"
            f"# volumen={config_bot.get('volumen_operacion', 0.01)}\n"
            + (lineas_params + "\n" if lineas_params else "")
            + "# Al activarla desde la biblioteca se reactiva el motor real (no este texto)."
        )
    else:
        codigo = config_bot.get("codigo_estrategia") or config_bot.get("condicion_compra_texto") or ""

    return _guardar_estrategia_en_biblioteca({
        "nombre": estrategia.nombre,
        "explicacion": estrategia.explicacion or "Estrategia activa guardada desde el backend",
        "codigo": codigo or estrategia.codigo,
        "volumen": estrategia.volumen or config_bot.get("volumen_operacion", 0.01),
        "sl": estrategia.sl or config_bot.get("sl_operacion", 0.0),
        "tp": estrategia.tp or config_bot.get("tp_operacion", 0.0),
        "modo_riesgo": estrategia.modo_riesgo or config_bot.get("modo_riesgo", "lotaje_fijo"),
        "riesgo_valor": estrategia.riesgo_valor or config_bot.get("riesgo_valor", 1.0),
        "es_estructura_dinamica": es_estructura,
        "timeframe_entrada": config_bot.get("timeframe_entrada", "M15"),
        "timeframe_macro": config_bot.get("timeframe_macro", "H1"),
        "parametros": config_bot.get("parametros", {}) or {},
    })


@app.post("/api/estrategias_guardadas/activar/{nombre}")
def post_activar_estrategia_guardada(nombre: str):
    """Activa una estrategia de la biblioteca: el bot pasa a operar SOLO con ella,
    usando el volumen/SL/TP guardados como riesgo por operación."""
    encontrada = next((e for e in biblioteca_estrategias if e["nombre"] == nombre), None)
    if encontrada is None:
        return {"error": "No existe una estrategia guardada con ese nombre"}

    if encontrada.get("es_estructura_dinamica"):
        # Reactiva el motor real multi-temporalidad (vuelve a traer velas de
        # entrada + macro por separado), en vez de tratarla como código libre.
        config_bot["estrategia"] = "estructura_dinamica"
        config_bot["timeframe_entrada"] = _normalizar_timeframe(encontrada.get("timeframe_entrada"), "M15")
        config_bot["timeframe_macro"] = _normalizar_timeframe(encontrada.get("timeframe_macro"), "H1")
        config_bot["parametros"] = dict(encontrada.get("parametros", {}) or {})
        config_bot["codigo_estrategia"] = ""
        config_bot["codigo_usa_multi_temporalidad"] = False
        config_bot["temporalidades_escaneo"] = []
    else:
        config_bot["codigo_estrategia"] = encontrada["codigo"]
        config_bot["estrategia"] = None
        config_bot["codigo_usa_multi_temporalidad"] = bool(encontrada.get("usa_multi_temporalidad", False))
        config_bot["timeframe_entrada"] = _normalizar_timeframe(encontrada.get("timeframe_entrada"), "M15")
        config_bot["timeframe_macro"] = _normalizar_timeframe(encontrada.get("timeframe_macro"), "H1")
        config_bot["temporalidades_escaneo"] = _normalizar_temporalidades_escaneo(
            encontrada.get("temporalidades_escaneo")
        )
        config_bot["roles_temporalidad"] = dict(encontrada.get("roles_temporalidad", {}) or {})
        config_bot["zonas_permitidas"] = _limpiar_zonas_permitidas(encontrada.get("zonas_permitidas"))
        config_bot["parametros"] = dict(encontrada.get("parametros", {}) or {})

    config_bot["volumen_operacion"] = encontrada.get("volumen", 0.01)
    config_bot["sl_operacion"] = encontrada.get("sl", 0.0)
    config_bot["tp_operacion"] = encontrada.get("tp", 0.0)
    config_bot["modo_riesgo"] = encontrada.get("modo_riesgo", "lotaje_fijo")
    config_bot["riesgo_valor"] = encontrada.get("riesgo_valor", 1.0)
    # Si la estrategia trae su propia lista de activos, manda sobre la lista
    # global de "activos permitidos" mientras esté activa. Si viene vacía,
    # se respeta la lista global tal cual estaba.
    activos_propios = encontrada.get("activos") or []
    if activos_propios:
        config_bot["simbolos_permitidos"] = activos_propios
    # al activar una de la biblioteca, se desactivan los otros modos
    config_bot["condicion_compra_texto"] = ""
    config_bot["condicion_venta_texto"] = ""
    config_bot["condiciones_compra"] = []
    config_bot["condiciones_venta"] = []
    config_bot["estrategia_biblioteca_activa"] = nombre
    # se resetea para que, si la señal ya estaba activa antes de cambiar de estrategia,
    # el bot pueda volver a operar con la nueva configuración
    estado["ultima_senal_ejecutada"] = None
    ultima_senal_ejecutada_por_simbolo.clear()
    guardar_config_bot()
    return {"activada": nombre, **encontrada}


@app.post("/api/estrategia/reset_total")
def post_reset_total_estrategia():
    """Apaga CUALQUIER estrategia activa en el bot (biblioteca, multi-temporalidad,
    código libre o bloques) y deja el panel completamente en cero. No borra la
    biblioteca guardada, solo desactiva lo que esté corriendo ahora mismo."""
    config_bot["estrategia"] = None
    config_bot["estrategia_biblioteca_activa"] = None
    config_bot["parametros"] = {}
    config_bot["codigo_estrategia"] = ""
    config_bot["condicion_compra_texto"] = ""
    config_bot["condicion_venta_texto"] = ""
    config_bot["condiciones_compra"] = []
    config_bot["condiciones_venta"] = []
    config_bot["volumen_operacion"] = 0.01
    config_bot["sl_operacion"] = 0.0
    config_bot["tp_operacion"] = 0.0
    config_bot["modo_riesgo"] = "lotaje_fijo"
    config_bot["riesgo_valor"] = 1.0
    estado["ultima_senal_ejecutada"] = None
    ultima_senal_ejecutada_por_simbolo.clear()
    ultima_zona_notificada_por_simbolo.clear()
    estado["estado_zona"] = None
    guardar_config_bot()
    return {"reseteado": True}


@app.delete("/api/estrategias_guardadas")
def delete_todas_estrategias_guardadas():
    """Borra TODA la biblioteca de estrategias guardadas de golpe (backend incluido)
    y además apaga cualquier estrategia activa en el bot ahora mismo."""
    global biblioteca_estrategias
    biblioteca_estrategias = []
    guardar_biblioteca(biblioteca_estrategias)
    post_reset_total_estrategia()
    return {"eliminadas": "todas"}


@app.delete("/api/estrategias_guardadas/{nombre}")
def delete_estrategia_guardada(nombre: str):
    global biblioteca_estrategias
    biblioteca_estrategias = [e for e in biblioteca_estrategias if e["nombre"] != nombre]
    guardar_biblioteca(biblioteca_estrategias)

    # Si la estrategia borrada era la que estaba activa en el bot, se detiene
    # por completo: si no, el bot seguiría operando en memoria con el código
    # de una estrategia que ya no existe en la biblioteca.
    if config_bot.get("estrategia_biblioteca_activa") == nombre:
        config_bot["estrategia_biblioteca_activa"] = None
        config_bot["codigo_estrategia"] = ""
        config_bot["volumen_operacion"] = 0.01
        config_bot["sl_operacion"] = 0.0
        config_bot["tp_operacion"] = 0.0
        config_bot["estrategia"] = None
        estado["ultima_senal_ejecutada"] = None
        ultima_senal_ejecutada_por_simbolo.clear()
        guardar_config_bot()

    return {"eliminada": nombre}


# ---------- Frontend web ----------
# Antes esto servía web/index.html (JS plano, sin tipos) a mano. Ahora el frontend
# web es el MISMO código TypeScript de mobile/App.tsx, exportado como sitio estático
# con:  cd mobile && npx expo export --platform web
# Eso genera mobile/dist/, que es lo que montamos aquí. Este mount va AL FINAL del
# archivo a propósito: así las rutas /api/... de arriba siempre se resuelven primero,
# y StaticFiles solo atrapa lo que no matcheó ninguna ruta de la API.
WEB_DIST = Path(__file__).resolve().parent.parent / "mobile" / "dist"
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:
    @app.get("/")
    def web_no_compilado():
        return {
            "error": (
                "El build web todavía no existe. Corre: "
                "cd mobile && npx expo export --platform web"
            )
        }