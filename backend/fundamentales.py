"""
FUNDAMENTALES — SpotPlus
============================================================
Segundo pilar de la metodología: NFP, IPC/CPI, tasas de interés y demás
noticias de alto impacto. Este módulo NO opera nada por sí mismo, solo
responde dos preguntas:

  1. ANTES de la noticia: ¿hay un evento de alto impacto próximo?
     -> se usa para BLOQUEAR/avisar antes de abrir una operación nueva.

  2. DESPUÉS de la noticia: ¿ya salió el dato? ¿cómo salió vs lo esperado?
     -> se usa para leer el resultado (actual vs forecast/previous), como
     información — no dispara ni cierra operaciones automáticamente.

Fuente de datos — DOS fuentes combinadas:

1. ESTRUCTURA de la semana (qué evento, cuándo, forecast/previous): feed
   semanal gratuito de ForexFactory (no requiere cuenta ni API key):
       https://nfs.faireconomy.media/ff_calendar_thisweek.json
   Este feed NUNCA manda el dato 'actual' (lo real publicado) — solo trae
   forecast y previous, sin importar cuánto tiempo pase.

2. DATO REAL ('actual'): Financial Modeling Prep (FMP), endpoint de
   calendario económico. Requiere una API key gratis (plan Basic, 250
   requests/día — de sobra para esto). Se define en el .env:
       FMP_API_KEY=tu_api_key_aca
   Este módulo cachea la respuesta de FMP (backend/calendario_fmp.json) y
   solo la vuelve a pedir si tiene más de FMP_CACHE_HORAS de antigüedad,
   para no gastar el límite diario de 250 calls. Si no hay FMP_API_KEY
   configurada, o falla la llamada, simplemente no hay 'actual' de esta
   fuente y el evento sigue las mismas reglas de siempre (queda "en
   proceso" hasta el tope y después "sin dato").

IMPORTANTE — límite de descargas del feed de FF: bloquea la IP si se
piden más de ~2 descargas cada 5 minutos. Por eso este módulo cachea el
calendario en un archivo local (backend/calendario_semanal.json) y solo
vuelve a descargar si el caché tiene más de CACHE_HORAS de antigüedad.
Nunca lo descargues en cada tick del bot — descárgalo 1 vez al día.
============================================================
"""
import json
import os
from datetime import datetime, timedelta, timezone

import requests

URL_CALENDARIO = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
RUTA_CACHE = os.path.join(os.path.dirname(__file__), "calendario_semanal.json")
CACHE_HORAS = 12  # no volver a descargar si el caché tiene menos de esto

# FMP: fuente del dato 'actual' real. FMP_API_KEY sale del .env (agregala
# vos ahí, este archivo no la trae hardcodeada por seguridad).
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
URL_FMP_CALENDARIO = "https://financialmodelingprep.com/stable/economic-calendar"
RUTA_CACHE_FMP = os.path.join(os.path.dirname(__file__), "calendario_fmp.json")
FMP_CACHE_HORAS = 3  # ~8 calls/día, muy por debajo del límite de 250/día
RUTA_INTENTO_FMP = os.path.join(os.path.dirname(__file__), "fmp_ultimo_intento.txt")

# FMP usa nombre de país completo o código de moneda distinto según el
# evento; este mapeo cubre los que más aparecen en el feed de FF que ya
# filtramos (impact == High). Si aparece un país que no está acá, el
# emparejamiento para ese evento puntual simplemente no encuentra 'actual'
# de FMP (no rompe nada, se comporta como si FMP no lo tuviera).
FMP_PAIS_A_MONEDA = {
    "united states": "USD", "us": "USD",
    "euro area": "EUR", "european union": "EUR", "eur": "EUR",
    "united kingdom": "GBP", "uk": "GBP",
    "japan": "JPY", "australia": "AUD", "canada": "CAD",
    "new zealand": "NZD", "switzerland": "CHF", "china": "CNY",
}

# Palabras clave para reconocer los eventos "grandes" que mencionaste
# (NFP, IPC, tasas) dentro del título del evento, sin depender de que
# ForexFactory los marque siempre como "High" (a veces PIB o discursos de
# banqueros centrales también mueven fuerte el mercado).
EVENTOS_CLAVE = [
    "non-farm", "nonfarm", "nfp", "employment change",
    "cpi", "consumer price index", "core cpi",
    "interest rate", "fed funds", "rate decision",
    "gdp", "pib",
    "fomc", "powell", "lagarde", "boe", "boj", "ecb",
]


def _es_evento_clave(titulo):
    t = titulo.lower()
    return any(clave in t for clave in EVENTOS_CLAVE)


def descargar_calendario(forzar=False):
    """
    Descarga el calendario semanal y lo guarda en caché local.
    Si el caché existe y tiene menos de CACHE_HORAS de antigüedad, NO
    vuelve a descargar (evita el bloqueo por límite de peticiones),
    salvo que forzar=True.

    Devuelve la lista de eventos (la misma que quedó cacheada).
    """
    if not forzar and os.path.exists(RUTA_CACHE):
        edad = datetime.now(timezone.utc) - datetime.fromtimestamp(
            os.path.getmtime(RUTA_CACHE), tz=timezone.utc
        )
        if edad < timedelta(hours=CACHE_HORAS):
            return cargar_calendario()

    resp = requests.get(URL_CALENDARIO, timeout=15)
    resp.raise_for_status()
    eventos = resp.json()

    with open(RUTA_CACHE, "w", encoding="utf-8") as f:
        json.dump(eventos, f, ensure_ascii=False, indent=2)

    return eventos


def _normalizar_titulo(titulo):
    """Para poder emparejar el mismo evento entre el feed de FF y el de
    FMP aunque el nombre no venga escrito exactamente igual entre las dos
    fuentes (mayúsculas, espacios, sufijos como '(MoM)'/'(YoY)', etc.)."""
    t = " ".join((titulo or "").lower().split())
    for sufijo in ("(mom)", "(yoy)", "(qoq)"):
        t = t.replace(sufijo, "").strip()
    return t


def _marcar_intento_fmp(horas_enfriamiento):
    """
    Registra CUÁNDO fue el último intento a FMP y hasta cuándo hay que
    esperar antes de reintentar, sin importar si falló o tuvo éxito. Esto
    es lo que faltaba antes: si la llamada fallaba (ej. 429 por límite
    diario agotado), 'descargar_fmp' no tocaba el caché y por lo tanto
    reintentaba en la SIGUIENTE petición del frontend (cada ~10 seg),
    lo que terminaba de agotar el límite diario en minutos y quedaba
    repitiendo el error para siempre.
    """
    hasta = datetime.now(timezone.utc) + timedelta(hours=horas_enfriamiento)
    with open(RUTA_INTENTO_FMP, "w", encoding="utf-8") as f:
        f.write(hasta.isoformat())


def _en_enfriamiento_fmp():
    """True si todavía no pasó el tiempo de espera del último intento
    (exitoso o fallido) a FMP."""
    if not os.path.exists(RUTA_INTENTO_FMP):
        return False
    try:
        with open(RUTA_INTENTO_FMP, "r", encoding="utf-8") as f:
            hasta = datetime.fromisoformat(f.read().strip())
    except (ValueError, OSError):
        return False
    return datetime.now(timezone.utc) < hasta


def descargar_fmp(forzar=False):
    """
    Descarga el calendario económico de FMP (con 'actual' real) para la
    semana actual y lo cachea localmente. No hace nada (ni gasta calls)
    si no hay FMP_API_KEY configurada, o si el caché tiene menos de
    FMP_CACHE_HORAS de antigüedad — así conservamos el límite de 250
    calls/día del plan gratis.
    """
    if not FMP_API_KEY:
        return []

    if not forzar and os.path.exists(RUTA_CACHE_FMP):
        edad = datetime.now(timezone.utc) - datetime.fromtimestamp(
            os.path.getmtime(RUTA_CACHE_FMP), tz=timezone.utc
        )
        if edad < timedelta(hours=FMP_CACHE_HORAS):
            return _leer_cache_fmp()

    # Enfriamiento: si el último intento (exitoso o no) fue reciente, no
    # se vuelve a llamar a la red todavía, aunque no haya caché fresco.
    if not forzar and _en_enfriamiento_fmp():
        return _leer_cache_fmp()

    hoy = datetime.now(timezone.utc).date()
    desde = hoy - timedelta(days=3)
    hasta = hoy + timedelta(days=7)

    try:
        resp = requests.get(
            URL_FMP_CALENDARIO,
            params={
                "from": desde.isoformat(),
                "to": hasta.isoformat(),
                "apikey": FMP_API_KEY,
            },
            timeout=15,
        )
        if resp.status_code == 429:
            # Límite diario agotado: no tiene caso reintentar hasta
            # mañana — se espera 24h en vez del enfriamiento normal.
            print(f"[fundamentales] FMP límite alcanzado (429), pausando 24h: {resp.text[:200]}")
            _marcar_intento_fmp(24)
            return _leer_cache_fmp()
        if resp.status_code != 200:
            print(f"[fundamentales] FMP respondió {resp.status_code}: {resp.text[:300]}")
            _marcar_intento_fmp(FMP_CACHE_HORAS)
            return _leer_cache_fmp()
        eventos_fmp = resp.json()
        if not isinstance(eventos_fmp, list):
            # FMP devuelve un dict con "Error Message" cuando la key es
            # inválida o el endpoint no está disponible en tu plan
            print(f"[fundamentales] FMP no devolvió una lista: {eventos_fmp}")
            _marcar_intento_fmp(FMP_CACHE_HORAS)
            return _leer_cache_fmp()
    except (requests.RequestException, ValueError) as err:
        # si falla (sin internet, key inválida, límite diario agotado),
        # seguimos con lo último que teníamos cacheado en vez de romper
        print(f"[fundamentales] Error consultando FMP: {err}")
        _marcar_intento_fmp(FMP_CACHE_HORAS)
        return _leer_cache_fmp()

    with open(RUTA_CACHE_FMP, "w", encoding="utf-8") as f:
        json.dump(eventos_fmp, f, ensure_ascii=False, indent=2)

    return eventos_fmp


def _leer_cache_fmp():
    if not os.path.exists(RUTA_CACHE_FMP):
        return []
    with open(RUTA_CACHE_FMP, "r", encoding="utf-8") as f:
        return json.load(f)


def _actuales_fmp_por_clave():
    """
    Arma {(moneda, titulo_normalizado, fecha_iso_al_minuto): actual} a
    partir del calendario cacheado de FMP, para completar los eventos del
    feed de FF (que trae la estructura de la semana pero nunca 'actual').
    """
    actuales = {}
    for e in descargar_fmp():
        actual = e.get("actual")
        if actual in (None, ""):
            continue
        pais = (e.get("country") or "").strip().lower()
        moneda = FMP_PAIS_A_MONEDA.get(pais, (e.get("currency") or "").strip().upper())
        if not moneda:
            continue
        try:
            fecha = datetime.fromisoformat(
                e["date"].replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        clave = (
            moneda,
            _normalizar_titulo(e.get("event")),
            fecha.replace(second=0, microsecond=0).isoformat(),
        )
        actuales[clave] = actual

    return actuales


def cargar_calendario():
    """
    Lee el calendario cacheado localmente (sin red hacia FF) y le completa
    el 'actual' con el dato real de FMP cuando está disponible (puede
    disparar UNA descarga a FMP si su caché venció, ver FMP_CACHE_HORAS).
    [] si no existe caché de FF aún.
    """
    if not os.path.exists(RUTA_CACHE):
        return []
    with open(RUTA_CACHE, "r", encoding="utf-8") as f:
        eventos = json.load(f)

    actuales_fmp = _actuales_fmp_por_clave()
    if not actuales_fmp:
        return eventos

    for e in eventos:
        if e.get("actual"):
            continue  # FF ya lo trae (raro, pero por las dudas)
        fecha = _parsear_fecha(e)
        if fecha is None:
            continue
        clave = (
            (e.get("country") or "").strip().upper(),
            _normalizar_titulo(e.get("title")),
            fecha.replace(second=0, microsecond=0).isoformat(),
        )
        if clave in actuales_fmp:
            e["actual"] = actuales_fmp[clave]

    return eventos


def _parsear_fecha(evento):
    """El feed de FF trae 'date' en ISO8601 con offset. Devuelve datetime UTC."""
    try:
        dt = datetime.fromisoformat(evento["date"].replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except (KeyError, ValueError):
        return None


def proximos_eventos_alto_impacto(minutos_antes=60, monedas=None, solo_clave=False):
    """
    PASO 1 — ANTES de la noticia.

    Devuelve la lista de eventos de alto impacto ("High" en el feed de FF)
    que van a salir dentro de los próximos 'minutos_antes' minutos, y que
    todavía NO tienen 'actual' publicado.

    monedas: opcional, lista tipo ["USD", "EUR"] para filtrar solo tus
    pares. Si no se pasa, no filtra por moneda.
    solo_clave: si True, además exige que el título coincida con
    EVENTOS_CLAVE (NFP, CPI, tasas, PIB, FOMC, etc.) en vez de cualquier
    evento marcado "High".

    Devuelve lista de dicts (los eventos originales del feed).
    """
    eventos = cargar_calendario()
    ahora = datetime.now(timezone.utc)
    limite = ahora + timedelta(minutes=minutos_antes)

    proximos = []
    for e in eventos:
        if e.get("impact") != "High":
            continue
        if monedas and e.get("country") not in monedas:
            continue
        if solo_clave and not _es_evento_clave(e.get("title", "")):
            continue
        fecha = _parsear_fecha(e)
        if fecha is None:
            continue
        if ahora <= fecha <= limite and not e.get("actual"):
            proximos.append(e)

    return sorted(proximos, key=_parsear_fecha)


def hay_noticia_alto_impacto_proxima(minutos_antes=60, monedas=None, solo_clave=False):
    """
    Versión simple en booleano de proximos_eventos_alto_impacto(), para
    usar directo como condición antes de abrir una operación:

        if fundamentales.hay_noticia_alto_impacto_proxima(60, ["USD"]):
            return  # no abrir operación nueva, hay NFP/CPI en <60 min
    """
    return len(proximos_eventos_alto_impacto(minutos_antes, monedas, solo_clave)) > 0


def eventos_recien_publicados(minutos_despues=15, monedas=None, solo_clave=False):
    """
    PASO 2 — DESPUÉS de la noticia.

    Devuelve los eventos cuya hora de publicación ya pasó (dentro de los
    últimos 'minutos_despues' minutos) y que YA tienen 'actual' cargado
    (es decir, el dato real ya salió). Requiere haber vuelto a descargar
    el calendario DESPUÉS de la hora del evento (el 'actual' se llena en
    vivo en el feed de FF a la hora exacta de la publicación) — por eso
    conviene forzar una descarga corta después de una noticia esperada:
        fundamentales.descargar_calendario(forzar=True)

    Devuelve lista de dicts con un campo extra 'sorpresa' agregado:
    "mejor" | "peor" | "en_linea" | None (si no se pudo comparar,
    ej. valores no numéricos).
    """
    eventos = cargar_calendario()
    ahora = datetime.now(timezone.utc)
    desde = ahora - timedelta(minutes=minutos_despues)

    recientes = []
    for e in eventos:
        if e.get("impact") != "High":
            continue
        if monedas and e.get("country") not in monedas:
            continue
        if solo_clave and not _es_evento_clave(e.get("title", "")):
            continue
        fecha = _parsear_fecha(e)
        if fecha is None:
            continue
        if desde <= fecha <= ahora and e.get("actual"):
            e = dict(e)
            e["sorpresa"] = _comparar_resultado(e)
            recientes.append(e)

    return sorted(recientes, key=_parsear_fecha)


def calendario_semana_categorizado(monedas=None, solo_clave=False, minutos_en_proceso=15, minutos_maximo_espera=180):
    """
    Vista completa de la SEMANA (todo lo que trae el feed cacheado),
    clasificada en 3 grupos para mostrar directo en la interfaz:

      - "pendientes": eventos de alto impacto que todavía no salen
        (fecha en el futuro), ordenados del más próximo al más lejano.
      - "en_proceso": eventos cuya hora de publicación está DENTRO de
        +/- 'minutos_en_proceso' del momento actual y aún sin 'actual'
        cargado (el dato está saliendo justo ahora / a punto de salir).
      - "pasados": eventos que ya tienen 'actual' cargado (ya se
        publicaron), con 'sorpresa' agregada (mejor/peor/en_linea),
        ordenados del más reciente al más antiguo. También incluye,
        marcados con 'sin_dato': True, los eventos que ya pasaron hace
        más de 'minutos_maximo_espera' y el feed nunca terminó de
        publicar su 'actual' (para no dejarlos colgados en "EN PROCESO"
        para siempre — ver 'minutos_maximo_espera' abajo).

    monedas: opcional, lista tipo ["USD", "EUR"] para filtrar.
    solo_clave: si True, solo NFP/CPI/tasas/PIB/FOMC/etc (ver EVENTOS_CLAVE)
    en vez de todo lo marcado "High".
    minutos_maximo_espera: tope de espera (por defecto 3h) para un evento
    ya vencido sin 'actual'. Pasado ese tope se considera que el proveedor
    del calendario no va a publicar el dato (o llegó con mucho retraso) y
    se manda a "pasados" como 'sin_dato' en vez de quedar "en proceso"
    indefinidamente (bug reportado: noticias de días anteriores seguían
    marcadas como "publicándose ahora / esperando dato").
    """
    eventos = cargar_calendario()
    ahora = datetime.now(timezone.utc)

    pendientes, en_proceso, pasados = [], [], []
    for e in eventos:
        if e.get("impact") != "High":
            continue
        if monedas and e.get("country") not in monedas:
            continue
        if solo_clave and not _es_evento_clave(e.get("title", "")):
            continue
        fecha = _parsear_fecha(e)
        if fecha is None:
            continue

        e = dict(e)
        if e.get("actual"):
            e["sorpresa"] = _comparar_resultado(e)
            pasados.append(e)
        elif abs((fecha - ahora).total_seconds()) <= minutos_en_proceso * 60:
            en_proceso.append(e)
        elif fecha > ahora:
            pendientes.append(e)
        elif (ahora - fecha).total_seconds() <= minutos_maximo_espera * 60:
            # ya pasó su hora pero el feed todavía no cargó 'actual'
            # (retraso normal de publicación) -> lo dejamos en proceso,
            # pero solo mientras el retraso sea razonable
            en_proceso.append(e)
        else:
            # pasó su hora hace rato y nunca llegó el 'actual': no lo
            # dejamos "en proceso" para siempre, lo mandamos a pasados
            # marcado como sin dato disponible
            e["sorpresa"] = None
            e["sin_dato"] = True
            pasados.append(e)

    return {
        "pendientes": sorted(pendientes, key=_parsear_fecha),
        "en_proceso": sorted(en_proceso, key=_parsear_fecha),
        "pasados": sorted(pasados, key=_parsear_fecha, reverse=True),
    }


def _comparar_resultado(evento):
    """
    Compara 'actual' vs 'forecast' para saber si el dato salió mejor,
    peor o en línea con lo esperado. Devuelve "mejor" / "peor" /
    "en_linea" / None si no se pudieron convertir a número (algunos
    eventos traen texto o rangos en vez de un número limpio).
    """
    def _num(valor):
        if valor in (None, ""):
            return None
        try:
            return float(str(valor).replace("%", "").replace("K", "").replace(",", "").strip())
        except ValueError:
            return None

    actual = _num(evento.get("actual"))
    forecast = _num(evento.get("forecast"))
    if actual is None or forecast is None:
        return None
    if actual > forecast:
        return "mejor"
    if actual < forecast:
        return "peor"
    return "en_linea"


# ============================================================
# CONFLUENCIA FUNDAMENTAL — SpotPlus
# ============================================================
# Si una zona de espera de la metodología (Flip/Fractal/EV/OBC) ya está
# formada y ADEMÁS la noticia reciente salió A FAVOR de esa dirección,
# eso cuenta como una confluencia más (igual que fibo, liquidez interna,
# externa o acumulación en estructura.py).
# ============================================================

# Para cada moneda, qué significa "sesgo alcista de la moneda" en
# términos de dirección de PRECIO del símbolo que opera el bot.
# Ej.: XAUUSD se cotiza en USD -> si el USD sale fuerte (sesgo alcista
# del USD), el ORO normalmente cae (INVERSO). Ajusta este mapeo según
# el símbolo real que estés operando.
CORRELACION_INVERSA = {
    "XAUUSD": {"USD"},   # dólar fuerte -> oro baja
}


def sesgo_moneda(evento):
    """
    Traduce la 'sorpresa' de un evento (ver eventos_recien_publicados) al
    sesgo de esa MONEDA: dato mejor de lo esperado normalmente fortalece
    la moneda (sesgo "alcista" para esa moneda); peor la debilita
    ("bajista"). None si no hay sorpresa calculable.
    """
    sorpresa = evento.get("sorpresa")
    if sorpresa == "mejor":
        return "alcista"
    if sorpresa == "peor":
        return "bajista"
    return None


def confluencia_fundamental(zona_tipo, simbolo, minutos_despues=30, monedas=None):
    """
    Revisa si hay una noticia reciente cuya dirección resultante COINCIDE
    con la dirección de la zona técnica (zona_tipo: "compra"/"venta").

    simbolo: el símbolo que opera el bot (ej. "XAUUSD"), para saber si la
    correlación con la moneda de la noticia es directa o inversa (ver
    CORRELACION_INVERSA arriba).

    Devuelve el evento (dict) que confirma la confluencia, o None si no
    hay ninguna noticia reciente a favor.
    """
    recientes = eventos_recien_publicados(minutos_despues, monedas)
    invertidas = CORRELACION_INVERSA.get(simbolo, set())

    for evento in recientes:
        sesgo = sesgo_moneda(evento)
        if sesgo is None:
            continue
        if evento.get("country") in invertidas:
            sesgo = "bajista" if sesgo == "alcista" else "alcista"

        direccion_precio = "compra" if sesgo == "alcista" else "venta"
        if direccion_precio == zona_tipo:
            return evento

    return None

# ============================================================
# BLOQUEO DE ENTRADAS POR NOTICIA — conexión real con el bot (MT5)
# ============================================================
# A diferencia de 'confluencia_fundamental' (informativa, no bloquea
# nada), esto SÍ se usa para impedir que el bot mande una orden nueva a
# MT5 mientras hay una noticia de alto impacto encima.
# ============================================================

# Mapeo de código de moneda ISO a las 3 letras que usa el feed en 'country'
# para reconocer qué monedas le importan a un símbolo dado. Si aparece un
# símbolo con una moneda que no está en esta lista, simplemente no se
# revisa esa parte (no rompe nada, solo no bloquea por esa moneda).
MONEDAS_CONOCIDAS = {
    "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY",
}


def monedas_de_simbolo(simbolo):
    """
    Deriva qué monedas le importan a un símbolo para el filtro de
    noticias. Ej.: "XAUUSD" -> ["USD"] (el oro se cotiza en dólares,
    "XAU" no es una moneda con calendario económico). "EURUSD" ->
    ["EUR", "USD"]. Si el símbolo no matchea ningún código conocido,
    devuelve [] (no se aplica ningún bloqueo por noticias para ese símbolo).
    """
    s = simbolo.upper()
    candidatos = {s[:3], s[-3:]} if len(s) >= 6 else set()
    return [m for m in candidatos if m in MONEDAS_CONOCIDAS]


def bloqueo_por_noticia(simbolo, minutos_antes=30, minutos_en_proceso=15):
    """
    Revisa si HAY QUE BLOQUEAR una entrada nueva en 'simbolo' ahora mismo:

      - Bloquea si hay una noticia de alto impacto "en proceso" (saliendo
        justo ahora) para alguna de las monedas del símbolo.
      - Bloquea si hay una noticia "pendiente" que sale dentro de los
        próximos 'minutos_antes' minutos.

    Devuelve el evento (dict) que causa el bloqueo, o None si no hay
    ningún bloqueo activo (o si el símbolo no tiene monedas reconocidas,
    en cuyo caso nunca bloquea).
    """
    monedas = monedas_de_simbolo(simbolo)
    if not monedas:
        return None

    cat = calendario_semana_categorizado(monedas=monedas, minutos_en_proceso=minutos_en_proceso)

    if cat["en_proceso"]:
        return cat["en_proceso"][0]

    ahora = datetime.now(timezone.utc)
    limite = ahora + timedelta(minutes=minutos_antes)
    for evento in cat["pendientes"]:
        fecha = _parsear_fecha(evento)
        if fecha is not None and fecha <= limite:
            return evento

    return None