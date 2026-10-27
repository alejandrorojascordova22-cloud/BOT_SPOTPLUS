"""
ESTADISTICAS Y BITACORA - SpotPlus bot
=======================================
- calcular_estadisticas(deals): toma el historial real de operaciones cerradas
  (tal como lo devuelve conexion_mt5.obtener_historial_cerradas) y calcula
  métricas de gestión: winrate, profit factor, racha, mejor/peor operación.
- Bitácora: notas del usuario ligadas (opcionalmente) a un ticket de MT5,
  persistidas en un JSON junto a la biblioteca de estrategias.
"""

import json
import os
from pathlib import Path

# Con multi-cuenta (CUENTA_ID definido en el proceso), cada cuenta guarda su
# propia bitacora para no mezclar notas de una cuenta con las de otra.
_SUFIJO_CUENTA = f"_{os.getenv('CUENTA_ID', '').strip()}" if os.getenv("CUENTA_ID", "").strip() else ""
ARCHIVO_BITACORA = Path(__file__).resolve().parent / f"bitacora{_SUFIJO_CUENTA}.json"


def calcular_estadisticas(deals):
    """
    deals: lista de dicts como los que regresa obtener_historial_cerradas()
    (cada uno con al menos "profit").

    Devuelve dict con: total_operaciones, ganadoras, perdedoras, winrate_pct,
    ganancia_total, mejor_operacion, peor_operacion, profit_factor,
    racha_actual (positiva = ganadoras seguidas, negativa = perdedoras seguidas).
    """
    if not deals:
        return {
            "total_operaciones": 0, "ganadoras": 0, "perdedoras": 0,
            "winrate_pct": 0.0, "ganancia_total": 0.0,
            "mejor_operacion": 0.0, "peor_operacion": 0.0,
            "profit_factor": None, "racha_actual": 0,
        }

    ganadoras = [d for d in deals if d["profit"] > 0]
    perdedoras = [d for d in deals if d["profit"] < 0]
    ganancia_bruta = sum(d["profit"] for d in ganadoras)
    perdida_bruta = abs(sum(d["profit"] for d in perdedoras))

    # deals viene ordenado más reciente primero (ver obtener_historial_cerradas)
    racha = 0
    for d in deals:
        signo = 1 if d["profit"] > 0 else (-1 if d["profit"] < 0 else 0)
        if signo == 0:
            break
        if racha == 0:
            racha = signo
        elif (racha > 0) == (signo > 0):
            racha += signo
        else:
            break

    return {
        "total_operaciones": len(deals),
        "ganadoras": len(ganadoras),
        "perdedoras": len(perdedoras),
        "winrate_pct": round(len(ganadoras) / len(deals) * 100, 1) if deals else 0.0,
        "ganancia_total": round(sum(d["profit"] for d in deals), 2),
        "mejor_operacion": round(max((d["profit"] for d in deals), default=0.0), 2),
        "peor_operacion": round(min((d["profit"] for d in deals), default=0.0), 2),
        "profit_factor": round(ganancia_bruta / perdida_bruta, 2) if perdida_bruta > 0 else None,
        "racha_actual": racha,
        **_metricas_extra(deals, ganadoras, perdedoras),
    }


def _metricas_extra(deals, ganadoras, perdedoras):
    """Métricas extra: promedios, expectativa, drawdown máximo y rachas máximas."""
    prom_g = sum(d["profit"] for d in ganadoras) / len(ganadoras) if ganadoras else 0.0
    prom_p = sum(d["profit"] for d in perdedoras) / len(perdedoras) if perdedoras else 0.0
    # deals viene del más reciente al más antiguo: se recorre en orden cronológico
    acumulado = pico = max_dd = 0.0
    racha_g = racha_p = max_g = max_p = 0
    for d in reversed(deals):
        p = d["profit"]
        acumulado += p
        pico = max(pico, acumulado)
        max_dd = max(max_dd, pico - acumulado)
        if p > 0:
            racha_g, racha_p = racha_g + 1, 0
        elif p < 0:
            racha_p, racha_g = racha_p + 1, 0
        max_g, max_p = max(max_g, racha_g), max(max_p, racha_p)
    return {
        "ganancia_promedio": round(prom_g, 2),
        "perdida_promedio": round(prom_p, 2),
        "ratio_ganancia_perdida": round(prom_g / abs(prom_p), 2) if prom_p else None,
        "expectativa": round(sum(d["profit"] for d in deals) / len(deals), 2) if deals else 0.0,
        "max_drawdown": round(max_dd, 2),
        "racha_max_ganadora": max_g,
        "racha_max_perdedora": max_p,
    }


def registrar_auto(texto, ticket=None):
    """Entrada automática en la bitácora (la genera el bot, no el usuario)."""
    try:
        return agregar_nota(f"🤖 {texto}", ticket)
    except Exception as e:
        print(f"[bitácora auto] error: {e}")
        return None


def cargar_bitacora():
    if ARCHIVO_BITACORA.exists():
        with ARCHIVO_BITACORA.open("r", encoding="utf-8") as f:
            return json.load(f)
    return []


def guardar_bitacora(lista):
    with ARCHIVO_BITACORA.open("w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)


def agregar_nota(nota, ticket=None):
    """Agrega una entrada nueva a la bitácora. 'ticket' es opcional (puedes
    anotar una reflexión general sin ligarla a una operación específica)."""
    import time
    entradas = cargar_bitacora()
    entrada = {
        "id": int(time.time() * 1000),
        "hora": time.time(),
        "ticket": ticket,
        "nota": nota,
    }
    entradas.insert(0, entrada)
    guardar_bitacora(entradas)
    return entrada


def borrar_nota(id_nota):
    entradas = cargar_bitacora()
    nuevas = [e for e in entradas if e["id"] != id_nota]
    guardar_bitacora(nuevas)
    return len(nuevas) != len(entradas)


def borrar_toda_la_bitacora():
    guardar_bitacora([])
    return True


# ============================================================
# DIARIO DE OPERACIONES (bitácora automática)
# ============================================================
# Una sola ficha por operación. Nace "en_curso" cuando el bot (o tú) abre la
# operación y, cuando se cierra, se COMPLETA la misma ficha con el resultado
# final (TP / SL / BE / Manual), el profit real y la duración. No se crean
# entradas duplicadas de apertura y cierre.
# ============================================================
import threading as _threading
import time as _time

ARCHIVO_DIARIO = Path(__file__).resolve().parent / f"diario_operaciones{_SUFIJO_CUENTA}.json"
_candado_diario = _threading.Lock()
_DIAS_PENDIENTE_MAX = 7  # una orden que nunca abrió posición se marca cancelada


def _cargar_diario():
    if ARCHIVO_DIARIO.exists():
        try:
            with ARCHIVO_DIARIO.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _guardar_diario(lista):
    tmp = ARCHIVO_DIARIO.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ARCHIVO_DIARIO)


def abrir_operacion(ticket, simbolo, tipo, volumen, entrada, sl, tp,
                    origen="", estimacion=None, balance=None, riesgo_txt=""):
    """Crea la ficha 'en_curso'. 'estimacion' = dict de estimar_operacion()."""
    if not ticket:
        return None
    est = estimacion or {}
    with _candado_diario:
        diario = _cargar_diario()
        if any(o["ticket"] == ticket for o in diario):
            return None
        ficha = {
            "id": int(_time.time() * 1000),
            "ticket": ticket,
            "estado": "en_curso",
            "simbolo": simbolo,
            "tipo": tipo,
            "volumen": volumen,
            "entrada": entrada,
            "sl": sl or None,
            "tp": tp or None,
            "origen": origen,
            "balance_apertura": balance,
            "riesgo": riesgo_txt,
            "perdida_estimada": est.get("perdida"),
            "ganancia_estimada": est.get("ganancia"),
            "ratio_rr": est.get("ratio_rr"),
            "hora_apertura": _time.time(),
            "hora_cierre": None,
            "resultado": None,
            "profit": None,
            "precio_cierre": None,
            "duracion_seg": None,
            "notas": "",
        }
        diario.insert(0, ficha)
        _guardar_diario(diario)
        return ficha


def _clasificar_cierre(razon, profit, ficha):
    """TP / SL / BE (SL movido a break even o con ganancia) / Manual."""
    if razon == "tp":
        return "TP"
    if razon == "sl":
        ref = abs(ficha.get("perdida_estimada") or 0)
        if profit >= 0 or (ref and abs(profit) <= ref * 0.1):
            return "BE"
        return "SL"
    return "Manual"


def reconciliar_diario(posiciones_abiertas, historial):
    """Cierra las fichas en curso cuya posición ya no está abierta.
    posiciones_abiertas: lista de obtener_posiciones_abiertas(simbolo=None).
    historial: lista de obtener_historial_cerradas(...) con position_id.
    Devuelve la lista de fichas que se acaban de cerrar."""
    abiertas = {p["ticket"] for p in posiciones_abiertas}
    por_posicion = {}
    for d in historial:
        clave = d.get("position_id") or d.get("ticket")
        por_posicion.setdefault(clave, []).append(d)
    cerradas = []
    with _candado_diario:
        diario = _cargar_diario()
        cambio = False
        for ficha in diario:
            if ficha["estado"] != "en_curso" or ficha["ticket"] in abiertas:
                continue
            deals = por_posicion.get(ficha["ticket"])
            if deals:
                deals = sorted(deals, key=lambda x: x["hora"])
                profit = round(sum(d["profit"] for d in deals), 2)
                ultimo = deals[-1]
                ficha.update({
                    "estado": "cerrada",
                    "hora_cierre": ultimo["hora"],
                    "precio_cierre": ultimo["precio"],
                    "profit": profit,
                    "resultado": _clasificar_cierre(ultimo.get("reason"), profit, ficha),
                    "duracion_seg": max(0, int(ultimo["hora"] - ficha["hora_apertura"])),
                })
                cerradas.append(dict(ficha))
                cambio = True
            elif _time.time() - ficha["hora_apertura"] > _DIAS_PENDIENTE_MAX * 86400:
                ficha.update({"estado": "cerrada", "resultado": "Cancelada",
                              "hora_cierre": _time.time(), "profit": 0.0})
                cambio = True
        if cambio:
            _guardar_diario(diario)
    return cerradas


def listar_diario(estado=None, limite=200):
    diario = _cargar_diario()
    if estado:
        diario = [o for o in diario if o["estado"] == estado]
    return diario[:limite]


def editar_notas_diario(id_ficha, notas):
    with _candado_diario:
        diario = _cargar_diario()
        for o in diario:
            if o["id"] == id_ficha:
                o["notas"] = notas
                _guardar_diario(diario)
                return True
    return False


def borrar_ficha_diario(id_ficha):
    with _candado_diario:
        diario = _cargar_diario()
        nuevo = [o for o in diario if o["id"] != id_ficha]
        _guardar_diario(nuevo)
        return len(nuevo) != len(diario)


def texto_cierre(ficha):
    """Resumen de una operación cerrada (para Telegram)."""
    icono = {"TP": "✅", "SL": "🛑", "BE": "🔒"}.get(ficha["resultado"], "📕")
    mins = (ficha.get("duracion_seg") or 0) // 60
    est = ""
    if ficha.get("perdida_estimada") is not None and ficha["resultado"] == "SL":
        est = f" (estimado -{ficha['perdida_estimada']:,.2f})"
    if ficha.get("ganancia_estimada") is not None and ficha["resultado"] == "TP":
        est = f" (estimado +{ficha['ganancia_estimada']:,.2f})"
    return (
        f"{icono} <b>Operación cerrada: {ficha['resultado']}</b>\n"
        f"{ficha['simbolo']} · {ficha['tipo'].upper()} · {ficha['volumen']} lotes\n"
        f"Entrada {ficha['entrada']} → Cierre {ficha['precio_cierre']}\n"
        f"Resultado: {ficha['profit']:+,.2f}{est} · Duración {mins} min"
    )


def estadisticas_diario(fichas=None):
    """Estadísticas agrupadas por resultado, símbolo y origen (bloque/zona)."""
    fichas = [o for o in (fichas if fichas is not None else _cargar_diario())
              if o["estado"] == "cerrada" and o["resultado"] != "Cancelada"]
    def resumen(grupo):
        n = len(grupo)
        gan = [o for o in grupo if (o["profit"] or 0) > 0]
        return {
            "operaciones": n,
            "winrate_pct": round(len(gan) / n * 100, 1) if n else 0.0,
            "profit": round(sum(o["profit"] or 0 for o in grupo), 2),
        }
    por_resultado, por_simbolo, por_origen = {}, {}, {}
    for o in fichas:
        por_resultado.setdefault(o["resultado"], []).append(o)
        por_simbolo.setdefault(o["simbolo"], []).append(o)
        por_origen.setdefault(o["origen"] or "manual", []).append(o)
    return {
        "total": resumen(fichas),
        "por_resultado": {k: resumen(v) for k, v in por_resultado.items()},
        "por_simbolo": {k: resumen(v) for k, v in por_simbolo.items()},
        "por_origen": {k: resumen(v) for k, v in por_origen.items()},
    }