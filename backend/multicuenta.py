"""
MULTICUENTA - SpotPlus
=======================
Un solo archivo con las 3 piezas de "varias cuentas MT5 + control por
Telegram" para no llenar el proyecto de archivos sueltos:

  1) Registro de cuentas (hasta 3, guardadas en backend/cuentas.json)
  2) Lanzador: levanta un proceso backend por cada cuenta activa
  3) Escuchador de Telegram: recibe tus mensajes y cambia SL/TP/riesgo/on-off

Por que procesos separados por cuenta (y no "un bot con 3 logins"):
La libreria MetaTrader5 de Python solo puede tener una sesion iniciada por
proceso (habla con un unico terminal64.exe). No hay forma soportada de
loguear 3 cuentas distintas dentro del mismo programa. Por eso cada cuenta
necesita su propia instalacion de MT5 (carpeta distinta, ya logueada) y su
propio proceso de Python en su propio puerto. Este archivo levanta esos
procesos y les manda instrucciones por HTTP local (lo mismo que hace la
app movil contra /api/config).

===================== TODO SE MANEJA DESDE TERMINAL =====================

Registrar una cuenta (maximo 3) - modo interactivo, te va preguntando cada dato:
    python -m backend.multicuenta agregar

(o todo en una linea, si prefieres:
    python -m backend.multicuenta agregar demo1 "Cuenta Demo 1" 12345678 miclave BrokerX-Demo "C:\\MT5-cuenta1\\terminal64.exe" )

Ver cuentas registradas:
    python -m backend.multicuenta listar

Quitar una cuenta:
    python -m backend.multicuenta eliminar demo1

Arrancar TODO (un proceso por cada cuenta activa + el escuchador de Telegram):
    python -m backend.multicuenta iniciar

Comandos que le mandas por TELEGRAM al bot (no en esta terminal):
    /cuentas                 -> lista cuentas y si estan operando o pausadas
    /estado demo1            -> SL, TP, riesgo y volumen actuales
    /sl demo1 2350.5         -> cambia el Stop Loss (precio)
    /tp demo1 2410           -> cambia el Take Profit (precio)
    /riesgo demo1 1.5        -> cambia el % de riesgo por operacion
    /lotaje demo1 0.02       -> cambia el volumen fijo (modo lotaje_fijo)
    /on demo1  /  /off demo1 -> activa / pausa el trading automatico
    /ayuda                   -> lista de comandos

Solo tu chat_id (el que ya tienes en backend/telegram_config.json) puede
usar estos comandos; cualquier otro chat se ignora en silencio.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent          # backend/
ARCHIVO_CUENTAS = RAIZ / "cuentas.json"
ARCHIVO_TELEGRAM = RAIZ / "telegram_config.json"
MAX_CUENTAS = 3
PUERTO_BASE = 8000


# ============================================================
# 1) REGISTRO DE CUENTAS
# ============================================================
def cargar_cuentas():
    if not ARCHIVO_CUENTAS.exists():
        return []
    try:
        with open(ARCHIVO_CUENTAS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def guardar_cuentas(cuentas):
    with open(ARCHIVO_CUENTAS, "w", encoding="utf-8") as f:
        json.dump(cuentas, f, indent=2, ensure_ascii=False)


def obtener_cuenta(id_cuenta):
    for c in cargar_cuentas():
        if c["id"] == id_cuenta:
            return c
    return None


def _siguiente_puerto(cuentas):
    usados = {c["puerto"] for c in cuentas}
    puerto = PUERTO_BASE
    while puerto in usados:
        puerto += 1
    return puerto


def agregar_cuenta(id_cuenta, nombre, login, password, server, terminal_path, activa=True):
    cuentas = cargar_cuentas()
    if any(c["id"] == id_cuenta for c in cuentas):
        return {"ok": False, "error": f"Ya existe una cuenta con id '{id_cuenta}'"}
    if len(cuentas) >= MAX_CUENTAS:
        return {"ok": False, "error": f"Maximo {MAX_CUENTAS} cuentas por seguridad. Elimina una para agregar otra."}
    if not terminal_path or not Path(terminal_path).exists():
        return {
            "ok": False,
            "error": (
                "terminal_path no existe. Cada cuenta necesita su PROPIA instalacion "
                "de MetaTrader 5 (carpeta distinta), ya logueada en esa cuenta."
            ),
        }
    nueva = {
        "id": id_cuenta, "nombre": nombre, "login": int(login), "password": password,
        "server": server, "terminal_path": terminal_path,
        "puerto": _siguiente_puerto(cuentas), "activa": bool(activa),
    }
    cuentas.append(nueva)
    guardar_cuentas(cuentas)
    return {"ok": True, "cuenta": nueva}


def eliminar_cuenta(id_cuenta):
    cuentas = cargar_cuentas()
    restantes = [c for c in cuentas if c["id"] != id_cuenta]
    if len(restantes) == len(cuentas):
        return {"ok": False, "error": f"No existe la cuenta '{id_cuenta}'"}
    guardar_cuentas(restantes)
    return {"ok": True}


def activar_cuenta(id_cuenta, activa=True):
    cuentas = cargar_cuentas()
    for c in cuentas:
        if c["id"] == id_cuenta:
            c["activa"] = activa
            guardar_cuentas(cuentas)
            return {"ok": True, "cuenta": c}
    return {"ok": False, "error": f"No existe la cuenta '{id_cuenta}'"}


def listar_cuentas():
    return cargar_cuentas()


# ============================================================
# 2) LANZADOR: un proceso backend por cada cuenta activa
# ============================================================
def _lanzar_procesos():
    cuentas = [c for c in listar_cuentas() if c.get("activa")]
    if not cuentas:
        print("No hay cuentas activas en backend/cuentas.json.")
        print("Registra al menos una con: python -m backend.multicuenta agregar")
        return []

    procesos = []
    for c in cuentas:
        print(f"Iniciando cuenta '{c['id']}' ({c['nombre']}) en el puerto {c['puerto']}...")
        env = {**os.environ, "CUENTA_ID": c["id"], "PUERTO": str(c["puerto"])}
        proceso = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.servidor:app",
             "--host", "0.0.0.0", "--port", str(c["puerto"])],
            cwd=str(RAIZ.parent), env=env,
        )
        procesos.append((c["id"], proceso))
        time.sleep(2)  # evita que 2 terminales MT5 arranquen al mismo tiempo
    return procesos


def iniciar_todo():
    procesos = _lanzar_procesos()
    if not procesos:
        return

    print("Iniciando escuchador de comandos de Telegram...")
    proceso_tg = subprocess.Popen(
        [sys.executable, "-m", "backend.multicuenta", "escuchar_telegram"],
        cwd=str(RAIZ.parent),
    )
    procesos.append(("telegram", proceso_tg))

    print(f"\n{len(procesos) - 1} cuenta(s) corriendo en paralelo + escuchador de Telegram. Ctrl+C para detener todo.\n")
    try:
        while True:
            time.sleep(5)
            for nombre, p in procesos:
                if p.poll() is not None:
                    print(f"AVISO: el proceso de '{nombre}' se detuvo (codigo {p.returncode}).")
    except KeyboardInterrupt:
        print("\nDeteniendo todos los procesos...")
        for _, p in procesos:
            p.terminate()


# ============================================================
# 3) ESCUCHADOR DE COMANDOS DE TELEGRAM
# ============================================================
def _cargar_config_telegram():
    if not ARCHIVO_TELEGRAM.exists():
        return None
    try:
        with open(ARCHIVO_TELEGRAM, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _url_cuenta(id_cuenta):
    for c in listar_cuentas():
        if c["id"] == id_cuenta:
            return f"http://127.0.0.1:{c['puerto']}"
    return None


def _responder(token, chat_id, texto):
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}, timeout=10,
    )


def _texto_ayuda():
    return (
        "<b>Comandos disponibles</b>\n"
        "/cuentas \u2014 lista las cuentas y su estado\n"
        "/estado &lt;id&gt; \u2014 estado de una cuenta\n"
        "/sl &lt;id&gt; &lt;valor&gt; \u2014 cambia el Stop Loss (precio)\n"
        "/tp &lt;id&gt; &lt;valor&gt; \u2014 cambia el Take Profit (precio)\n"
        "/riesgo &lt;id&gt; &lt;valor&gt; \u2014 cambia el % de riesgo\n"
        "/lotaje &lt;id&gt; &lt;valor&gt; \u2014 cambia el volumen fijo\n"
        "/on &lt;id&gt; \u2014 activa el trading automatico\n"
        "/off &lt;id&gt; \u2014 lo desactiva\n"
    )


def _manejar_comando(texto, token, chat_id):
    partes = texto.strip().split()
    if not partes:
        return
    cmd = partes[0].lower()

    if cmd in ("/ayuda", "/help", "/start"):
        _responder(token, chat_id, _texto_ayuda())
        return

    if cmd == "/cuentas":
        cuentas = listar_cuentas()
        if not cuentas:
            _responder(token, chat_id, "No hay cuentas registradas todavia.")
            return
        lineas = []
        for c in cuentas:
            estado_txt = "sin conexion"
            try:
                r = requests.get(f"http://127.0.0.1:{c['puerto']}/api/estado", timeout=3)
                if r.ok:
                    activo = r.json().get("config", {}).get("activo")
                    estado_txt = "operando" if activo else "pausado"
            except requests.RequestException:
                pass
            lineas.append(f"- {c['id']} ({c['nombre']}) - {estado_txt}")
        _responder(token, chat_id, "\n".join(lineas))
        return

    if len(partes) < 2:
        _responder(token, chat_id, f"Falta el id de la cuenta. Usa /cuentas para verlos.\n\n{_texto_ayuda()}")
        return

    id_cuenta = partes[1]
    base_url = _url_cuenta(id_cuenta)
    if base_url is None:
        _responder(token, chat_id, f"No existe la cuenta '{id_cuenta}'. Usa /cuentas para ver los ids validos.")
        return

    try:
        if cmd == "/estado":
            r = requests.get(f"{base_url}/api/estado", timeout=5)
            cfg = r.json().get("config", {})
            _responder(
                token, chat_id,
                f"<b>{id_cuenta}</b>\nActivo: {cfg.get('activo')}\nSL: {cfg.get('sl_operacion')}\n"
                f"TP: {cfg.get('tp_operacion')}\nModo riesgo: {cfg.get('modo_riesgo')}\n"
                f"Riesgo: {cfg.get('riesgo_valor')}\nVolumen: {cfg.get('volumen_operacion')}",
            )
            return

        if cmd in ("/sl", "/tp", "/riesgo", "/lotaje") and len(partes) >= 3:
            valor = float(partes[2])
            campo = {"/sl": "sl_operacion", "/tp": "tp_operacion",
                     "/riesgo": "riesgo_valor", "/lotaje": "volumen_operacion"}[cmd]
            r = requests.post(f"{base_url}/api/config", json={campo: valor}, timeout=5)
            if r.ok:
                _responder(token, chat_id, f"{campo} de '{id_cuenta}' actualizado a {valor}")
            else:
                _responder(token, chat_id, f"Error al actualizar '{id_cuenta}': {r.text}")
            return

        if cmd == "/on":
            requests.post(f"{base_url}/api/config", json={"activo": True}, timeout=5)
            _responder(token, chat_id, f"Trading automatico ACTIVADO en '{id_cuenta}'")
            return

        if cmd == "/off":
            requests.post(f"{base_url}/api/config", json={"activo": False}, timeout=5)
            _responder(token, chat_id, f"Trading automatico PAUSADO en '{id_cuenta}'")
            return

        _responder(token, chat_id, f"Comando no reconocido.\n\n{_texto_ayuda()}")

    except requests.RequestException as e:
        _responder(token, chat_id, f"No se pudo contactar a '{id_cuenta}': {e}")
    except ValueError:
        _responder(token, chat_id, "El valor debe ser un numero. Ejemplo: /sl demo1 2350.5")


def escuchar_telegram():
    """Long-polling de Telegram. Nunca lanza excepcion hacia afuera: si
    Telegram falla o no hay internet, reintenta solo, sin tumbar el proceso."""
    offset = None
    print("Escuchador de comandos de Telegram iniciado.")
    while True:
        cfg = _cargar_config_telegram()
        if not cfg or not cfg.get("telegram_bot_token") or not cfg.get("telegram_chat_id"):
            print("telegram_config.json incompleto (falta token o chat_id). Reintentando en 30s...")
            time.sleep(30)
            continue

        token = cfg["telegram_bot_token"]
        chat_id_autorizado = str(cfg["telegram_chat_id"])

        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=30)
            datos = r.json()
            if not datos.get("ok"):
                time.sleep(5)
                continue

            for update in datos.get("result", []):
                offset = update["update_id"] + 1
                mensaje = update.get("message") or {}
                texto = mensaje.get("text")
                chat_id_origen = str(mensaje.get("chat", {}).get("id", ""))
                if not texto:
                    continue
                # Solo el chat_id fijo autorizado puede mandar comandos;
                # cualquier otro se ignora en silencio.
                if chat_id_origen != chat_id_autorizado:
                    continue
                _manejar_comando(texto, token, chat_id_autorizado)

        except requests.RequestException as e:
            print("Error de red hablando con Telegram:", e)
            time.sleep(5)
        except Exception as e:
            print("Error inesperado en el escuchador de Telegram:", e)
            time.sleep(5)


# ============================================================
# CLI: todo se maneja desde terminal
# ============================================================
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    accion = sys.argv[1]

    if accion == "listar":
        cuentas = listar_cuentas()
        if not cuentas:
            print("No hay cuentas registradas todavia.")
        for c in cuentas:
            estado = "ACTIVA" if c["activa"] else "pausada"
            print(f"- [{c['id']}] {c['nombre']} | login {c['login']} @ {c['server']} | puerto {c['puerto']} | {estado}")

    elif accion == "agregar":
        # Modo interactivo: si no pasaste todos los datos en la linea de
        # comando, te los pregunta uno por uno (la contrasena no se ve en
        # pantalla mientras la escribes).
        if len(sys.argv) == 8:
            _, _, id_cuenta, nombre, login, password, server, terminal_path = sys.argv
        elif len(sys.argv) == 2:
            import getpass
            print("Vamos a enlazar una cuenta nueva con MT5. Solo responde cada dato:\n")
            id_cuenta = input("Id corto para identificarla (ej. demo1): ").strip()
            nombre = input("Nombre para mostrar (ej. Cuenta Demo 1): ").strip()
            login = input("Numero de cuenta MT5 (login): ").strip()
            password = getpass.getpass("Contrasena de MT5 (no se muestra en pantalla): ")
            server = input("Servidor del broker (ej. BrokerX-Demo): ").strip()
            terminal_path = input(r"Ruta del terminal64.exe de ESTA cuenta (ej. C:\MT5-cuenta1\terminal64.exe): ").strip()
        else:
            print("Uso: python -m backend.multicuenta agregar                              (te pregunta los datos uno por uno)")
            print("  o: python -m backend.multicuenta agregar <id> <nombre> <login> <password> <server> <terminal_path>")
            sys.exit(1)
        resultado = agregar_cuenta(id_cuenta, nombre, login, password, server, terminal_path)
        print()
        print(resultado)

    elif accion == "eliminar":
        if len(sys.argv) != 3:
            print("Uso: python -m backend.multicuenta eliminar <id>")
            sys.exit(1)
        print(eliminar_cuenta(sys.argv[2]))

    elif accion == "activar":
        if len(sys.argv) != 3:
            print("Uso: python -m backend.multicuenta activar <id>")
            sys.exit(1)
        print(activar_cuenta(sys.argv[2], True))

    elif accion == "pausar":
        if len(sys.argv) != 3:
            print("Uso: python -m backend.multicuenta pausar <id>")
            sys.exit(1)
        print(activar_cuenta(sys.argv[2], False))

    elif accion == "iniciar":
        iniciar_todo()

    elif accion == "escuchar_telegram":
        escuchar_telegram()

    else:
        print(f"Accion desconocida: {accion}\n")
        print(__doc__)


if __name__ == "__main__":
    main()
