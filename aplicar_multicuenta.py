"""
Aplica automaticamente el sistema de MULTI-CUENTA + control por Telegram
al backend de SpotPlus Bot.

USO (desde la carpeta raiz del proyecto, la que tiene la carpeta backend/):

    python aplicar_multicuenta.py

Que hace:
  1. Crea backend/multicuenta.py (nuevo: registro de cuentas + lanzador +
     escuchador de comandos de Telegram, todo en un solo archivo).
  2. Modifica backend/config.py para soportar arrancar "siendo" una cuenta
     especifica (variable de entorno CUENTA_ID).
  3. Modifica backend/conexion_mt5.py para usar el terminal MT5 de la
     cuenta activa del proceso.
  4. Modifica backend/servidor.py: cada cuenta guarda su propio estado
     (bot_config, telegram_config, bitacora, estrategias guardadas) y
     agrega los endpoints /api/cuentas para administrarlas.
  5. Modifica backend/estadisticas.py para que la bitacora tambien sea
     por cuenta.

Es seguro correrlo mas de una vez: si detecta que un cambio ya existe, lo
salta en vez de duplicarlo. Antes de tocar nada hace un backup .bak de cada
archivo que va a modificar.
"""
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
BACKEND = RAIZ / "backend"


def backup(path):
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  backup -> {bak.name}")


def reemplazar(texto, viejo, nuevo, nombre_cambio):
    if nuevo in texto:
        print(f"  [saltado] {nombre_cambio} (ya estaba aplicado)")
        return texto
    if viejo not in texto:
        print(f"  [!] No se encontro el texto esperado para: {nombre_cambio}")
        print("      Puede que el archivo ya haya cambiado. Revisa a mano este paso.")
        return texto
    texto = texto.replace(viejo, nuevo, 1)
    print(f"  [ok] {nombre_cambio}")
    return texto


# ============================================================
# 1) backend/multicuenta.py (archivo nuevo, contenido completo)
# ============================================================
MULTICUENTA_PY = r'''"""
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
'''


# ============================================================
# 2) backend/config.py (archivo pequeno, se reescribe completo)
# ============================================================
CONFIG_PY = '''import os
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------
# Soporte multi-cuenta:
# Si se define la variable de entorno CUENTA_ID, este proceso se
# arranca "siendo" esa cuenta (login/password/server/terminal/puerto
# se leen de cuentas.json en vez de las variables MT5_* del .env).
# Asi, cada cuenta corre en su propio proceso (ver backend/multicuenta.py),
# cada una con su propio terminal MT5, su propio puerto HTTP y sus
# propios archivos de estado (bot_config, telegram, bitacora, etc).
# Si CUENTA_ID no esta definida, todo funciona exactamente igual que
# antes (una sola cuenta, tomada del .env) para no romper instalaciones
# existentes.
# ------------------------------------------------------------------
CUENTA_ID = os.getenv("CUENTA_ID", "").strip() or None

TERMINAL_PATH = r"C:\\Program Files\\MetaTrader 5\\terminal64.exe"
PUERTO = int(os.getenv("PUERTO", "8000"))

if CUENTA_ID:
    from .multicuenta import obtener_cuenta

    _cuenta = obtener_cuenta(CUENTA_ID)
    if _cuenta is None:
        raise RuntimeError(
            f"CUENTA_ID='{CUENTA_ID}' no existe en cuentas.json. "
            "Revisa backend/cuentas.json o registra la cuenta con "
            "'python -m backend.multicuenta agregar'."
        )
    MT5_LOGIN = int(_cuenta["login"])
    MT5_PASSWORD = _cuenta["password"]
    MT5_SERVER = _cuenta["server"]
    TERMINAL_PATH = _cuenta.get("terminal_path") or TERMINAL_PATH
    PUERTO = int(_cuenta.get("puerto", PUERTO))
    NOMBRE_CUENTA = _cuenta.get("nombre", CUENTA_ID)
else:
    MT5_LOGIN = int(os.getenv("MT5_LOGIN"))
    MT5_PASSWORD = os.getenv("MT5_PASSWORD")
    MT5_SERVER = os.getenv("MT5_SERVER")
    NOMBRE_CUENTA = "default"

SIMBOLO = "XAUUSD"  # Oro - simbolo que funciona confiablemente
TIMEFRAME_STR = os.getenv("MT5_TIMEFRAME", "M5")

MODO = "demo"
'''


def aplicar():
    print("=== 1) backend/multicuenta.py ===")
    destino = BACKEND / "multicuenta.py"
    if destino.exists() and "MULTICUENTA - SpotPlus" in destino.read_text(encoding="utf-8"):
        print("  [saltado] ya existe")
    else:
        destino.write_text(MULTICUENTA_PY, encoding="utf-8")
        print("  [ok] creado")

    print("\n=== 2) backend/config.py ===")
    destino = BACKEND / "config.py"
    backup(destino)
    if "CUENTA_ID" in destino.read_text(encoding="utf-8"):
        print("  [saltado] ya estaba aplicado")
    else:
        destino.write_text(CONFIG_PY, encoding="utf-8")
        print("  [ok] reescrito con soporte multi-cuenta")

    print("\n=== 3) backend/conexion_mt5.py ===")
    destino = BACKEND / "conexion_mt5.py"
    backup(destino)
    texto = destino.read_text(encoding="utf-8")
    texto = reemplazar(
        texto,
        "from .config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SIMBOLO, TIMEFRAME_STR",
        "from .config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SIMBOLO, TIMEFRAME_STR, TERMINAL_PATH, NOMBRE_CUENTA",
        "import de TERMINAL_PATH/NOMBRE_CUENTA",
    )
    texto = reemplazar(
        texto,
        'def conectar():\n    ruta_terminal = r"C:\\Program Files\\MetaTrader 5\\terminal64.exe"\n    if not mt5.initialize(path=ruta_terminal):\n        print("Error al iniciar MT5:", mt5.last_error())\n        return False\n\n    autorizado = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)\n    if not autorizado:\n        print("Error al hacer login:", mt5.last_error())\n        return False\n\n    print(f"Conectado a MT5 - cuenta {MT5_LOGIN}")\n    return True',
        'def conectar():\n    # TERMINAL_PATH viene de config.py: si este proceso arranco con\n    # CUENTA_ID=algo, apunta al terminal MT5 propio de esa cuenta (cada\n    # cuenta necesita su propia instalacion de MT5, ya que un mismo\n    # terminal64.exe solo puede iniciar sesion en una cuenta a la vez).\n    if not mt5.initialize(path=TERMINAL_PATH):\n        print(f"[{NOMBRE_CUENTA}] Error al iniciar MT5:", mt5.last_error())\n        return False\n\n    autorizado = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)\n    if not autorizado:\n        print(f"[{NOMBRE_CUENTA}] Error al hacer login:", mt5.last_error())\n        return False\n\n    print(f"[{NOMBRE_CUENTA}] Conectado a MT5 - cuenta {MT5_LOGIN}")\n    return True',
        "conectar() usa el terminal de la cuenta activa",
    )
    destino.write_text(texto, encoding="utf-8")

    print("\n=== 4) backend/estadisticas.py ===")
    destino = BACKEND / "estadisticas.py"
    backup(destino)
    texto = destino.read_text(encoding="utf-8")
    texto = reemplazar(
        texto,
        'import json\nfrom pathlib import Path\n\nARCHIVO_BITACORA = Path(__file__).resolve().parent / "bitacora.json"',
        'import json\nimport os\nfrom pathlib import Path\n\n# Con multi-cuenta (CUENTA_ID definido en el proceso), cada cuenta guarda su\n# propia bitacora para no mezclar notas de una cuenta con las de otra.\n_SUFIJO_CUENTA = f"_{os.getenv(\'CUENTA_ID\', \'\').strip()}" if os.getenv("CUENTA_ID", "").strip() else ""\nARCHIVO_BITACORA = Path(__file__).resolve().parent / f"bitacora{_SUFIJO_CUENTA}.json"',
        "bitacora por cuenta",
    )
    destino.write_text(texto, encoding="utf-8")

    print("\n=== 5) backend/servidor.py ===")
    destino = BACKEND / "servidor.py"
    backup(destino)
    texto = destino.read_text(encoding="utf-8")

    texto = reemplazar(
        texto,
        "from .config import SIMBOLO as SIMBOLO_INICIAL",
        "from .config import SIMBOLO as SIMBOLO_INICIAL, CUENTA_ID, NOMBRE_CUENTA\nfrom . import multicuenta as cuentas_mod",
        "import de CUENTA_ID/NOMBRE_CUENTA/multicuenta",
    )

    texto = reemplazar(
        texto,
        'ARCHIVO_BIBLIOTECA = Path(__file__).resolve().parent / "estrategias_guardadas.json"',
        '# Con multi-cuenta (CUENTA_ID definido), cada proceso guarda su propio\n'
        '# estado en archivos con sufijo "_<cuenta_id>" para que 2+ cuentas corriendo\n'
        '# en paralelo (procesos distintos) nunca se pisen el bot_config, telegram,\n'
        '# ni la biblioteca de estrategias entre si.\n'
        '_SUFIJO_CUENTA = f"_{CUENTA_ID}" if CUENTA_ID else ""\n\n'
        'ARCHIVO_BIBLIOTECA = Path(__file__).resolve().parent / f"estrategias_guardadas{_SUFIJO_CUENTA}.json"',
        "ARCHIVO_BIBLIOTECA por cuenta",
    )

    texto = reemplazar(
        texto,
        'ARCHIVO_TELEGRAM = Path(__file__).resolve().parent / "telegram_config.json"',
        'ARCHIVO_TELEGRAM = Path(__file__).resolve().parent / f"telegram_config{_SUFIJO_CUENTA}.json"',
        "ARCHIVO_TELEGRAM por cuenta",
    )

    texto = reemplazar(
        texto,
        'ARCHIVO_CONFIG_BOT = Path(__file__).resolve().parent / "bot_config.json"',
        'ARCHIVO_CONFIG_BOT = Path(__file__).resolve().parent / f"bot_config{_SUFIJO_CUENTA}.json"',
        "ARCHIVO_CONFIG_BOT por cuenta",
    )

    texto = reemplazar(
        texto,
        '@app.get("/api/estado")\ndef get_estado():\n    return {**estado, "config": config_bot}',
        '@app.get("/api/estado")\ndef get_estado():\n    return {**estado, "config": config_bot, "cuenta_id": CUENTA_ID or "default", "nombre_cuenta": NOMBRE_CUENTA}',
        "/api/estado incluye cuenta_id",
    )

    texto = reemplazar(
        texto,
        '@app.get("/api/cuenta")\ndef get_cuenta():\n    info = obtener_info_cuenta()\n    if info is None:\n        return {"error": "No se pudo obtener info de cuenta"}\n    return info',
        '@app.get("/api/cuenta")\n'
        'def get_cuenta():\n'
        '    info = obtener_info_cuenta()\n'
        '    if info is None:\n'
        '        return {"error": "No se pudo obtener info de cuenta"}\n'
        '    return info\n\n\n'
        '# ---------- Multi-cuenta (registro de hasta 3 cuentas MT5) ----------\n'
        '# Nota: esto solo LEE/ESCRIBE cuentas.json (el catalogo de cuentas). No\n'
        '# arranca ni detiene procesos: eso lo hace backend/multicuenta.py. Despues de\n'
        '# agregar/quitar una cuenta aqui hay que reiniciar "python -m backend.multicuenta iniciar"\n'
        '# para que levante (o baje) el proceso correspondiente.\n'
        'class CuentaRequest(BaseModel):\n'
        '    id: str\n'
        '    nombre: str\n'
        '    login: int\n'
        '    password: str\n'
        '    server: str\n'
        '    terminal_path: str\n'
        '    activa: bool = True\n\n\n'
        'def _cuenta_sin_password(c: dict) -> dict:\n'
        '    return {k: v for k, v in c.items() if k != "password"}\n\n\n'
        '@app.get("/api/cuentas")\n'
        'def get_cuentas():\n'
        '    """Cuentas registradas en ESTE catalogo compartido, sin exponer passwords."""\n'
        '    return [_cuenta_sin_password(c) for c in cuentas_mod.listar_cuentas()]\n\n\n'
        '@app.post("/api/cuentas")\n'
        'def post_cuenta(c: CuentaRequest):\n'
        '    resultado = cuentas_mod.agregar_cuenta(\n'
        '        c.id, c.nombre, c.login, c.password, c.server, c.terminal_path, c.activa\n'
        '    )\n'
        '    if resultado.get("ok") and "cuenta" in resultado:\n'
        '        resultado["cuenta"] = _cuenta_sin_password(resultado["cuenta"])\n'
        '    return resultado\n\n\n'
        '@app.delete("/api/cuentas/{id_cuenta}")\n'
        'def delete_cuenta(id_cuenta: str):\n'
        '    return cuentas_mod.eliminar_cuenta(id_cuenta)\n\n\n'
        '@app.post("/api/cuentas/{id_cuenta}/activar")\n'
        'def activar_cuenta_endpoint(id_cuenta: str, activa: bool = True):\n'
        '    resultado = cuentas_mod.activar_cuenta(id_cuenta, activa)\n'
        '    if resultado.get("ok") and "cuenta" in resultado:\n'
        '        resultado["cuenta"] = _cuenta_sin_password(resultado["cuenta"])\n'
        '    return resultado',
        "endpoints /api/cuentas",
    )

    destino.write_text(texto, encoding="utf-8")

    print("\nListo. Revisa arriba que todos los pasos digan [ok] o [saltado].")
    print("Si alguno dice [!], avisame y lo reviso a mano (puede que ya hayas tocado ese archivo).")


if __name__ == "__main__":
    if not BACKEND.exists():
        print("No encuentro la carpeta backend/ aqui. Corre este script desde la raiz del proyecto.")
        sys.exit(1)
    aplicar()
