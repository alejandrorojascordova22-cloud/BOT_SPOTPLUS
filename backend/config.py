import os
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

# Ruta al terminal64.exe que el bot debe usar. ANTES estaba fija en el código
# ("C:\Program Files\MetaTrader 5\terminal64.exe"), que es la ruta de la
# instalación GENERICA de MetaTrader 5 (la que se baja de metatrader5.com).
# La mayoria de los brokers instalan su MT5 en una carpeta con su propio
# nombre (ej. "C:\Program Files\NombreBroker MT5\terminal64.exe"). Si tu
# terminal real esta en otra carpeta y esta variable seguia apuntando a la
# generica, el bot podia conectarse a ESA otra instalacion (si existe en tu
# PC) y colocar las operaciones ahi -> retcode DONE, ticket real, pero en
# una terminal que nunca ves en pantalla. Ahora se puede sobreescribir con
# MT5_TERMINAL_PATH en el .env; si no la defines, se usa la ruta generica
# de siempre (no rompe instalaciones existentes que ya funcionaban bien).
TERMINAL_PATH = os.getenv("MT5_TERMINAL_PATH", "").strip() or r"C:\Program Files\MetaTrader 5\terminal64.exe"
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

# API_KEY: token que el frontend (o cualquiera) debe mandar en el header
# "X-API-Key" para poder usar el servidor. Sin esto, cualquiera que llegue a
# la IP:puerto del bot podía abrir/cerrar operaciones o cambiar el riesgo sin
# pedir nada. Se define en .env (API_KEY=...). Si no está definida, el
# servidor NO arranca (falla rápido en vez de quedar abierto sin querer).
API_KEY = os.getenv("API_KEY", "").strip()

SIMBOLO = os.getenv("MT5_SIMBOLO", "XAUUSD").strip() or "XAUUSD"
TIMEFRAME_STR = os.getenv("MT5_TIMEFRAME", "M5")

MODO = "demo"