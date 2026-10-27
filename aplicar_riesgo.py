"""
Aplica automaticamente la gestion de riesgo (lotaje dinamico) al backend
de SpotPlus Bot.

USO (desde la carpeta raiz del proyecto, la que tiene la carpeta backend/):

    python aplicar_riesgo.py

Que hace:
  1. Crea backend/riesgo.py (nuevo).
  2. Agrega obtener_limites_volumen() a backend/conexion_mt5.py.
  3. Modifica backend/servidor.py: import, config_bot, estado, ConfigRequest,
     post_config, y el envio de orden automatica en ciclo_bot().

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


def reemplazar(path, viejo, nuevo, nombre_cambio):
    texto = path.read_text(encoding="utf-8")
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


RIESGO_PY = '''import math

from .conexion_mt5 import obtener_tamano_contrato, obtener_limites_volumen

# ============================================================
# GESTION DE RIESGO - SpotPlus
# ============================================================
# Regla: el % o monto a arriesgar por operacion se mantiene fijo.
# A menor distancia de SL, mayor lotaje. A mayor distancia, menor lotaje.
# ============================================================


def calcular_lotaje(balance, precio_entrada, sl, simbolo, modo="porcentaje", valor=1.0):
    """
    Calcula el lotaje a operar segun el riesgo configurado.

    modo: "porcentaje" (valor = % del balance) o "fijo" (valor = monto fijo
    en la moneda de la cuenta).

    Devuelve dict {"lotaje", "dinero_riesgo", "distancia_sl", "advertencia"}
    o None si faltan datos esenciales (sin SL no hay riesgo real que calcular).
    """
    if balance is None or balance <= 0:
        return None
    if precio_entrada is None or sl is None or sl == 0:
        return None

    distancia_sl = abs(precio_entrada - sl)
    if distancia_sl <= 0:
        return None

    if modo == "porcentaje":
        dinero_riesgo = balance * (valor / 100)
    elif modo == "fijo":
        dinero_riesgo = valor
    else:
        return None

    tamano_contrato = obtener_tamano_contrato(simbolo)
    valor_por_lote = distancia_sl * tamano_contrato
    if valor_por_lote <= 0:
        return None

    lotaje_crudo = dinero_riesgo / valor_por_lote

    limites = obtener_limites_volumen(simbolo)
    lotaje = _ajustar_a_paso(lotaje_crudo, limites["paso"])

    advertencia = None
    if lotaje < limites["minimo"]:
        advertencia = (
            f"El riesgo definido da un lotaje ({lotaje_crudo:.4f}) menor al minimo "
            f"permitido ({limites['minimo']}). Se usara el minimo del broker, lo cual "
            f"arriesga MAS dinero del configurado."
        )
        lotaje = limites["minimo"]
    elif lotaje > limites["maximo"]:
        advertencia = (
            f"El riesgo definido da un lotaje ({lotaje_crudo:.4f}) mayor al maximo "
            f"permitido ({limites['maximo']}). Se usara el maximo del broker, lo cual "
            f"arriesga MENOS dinero del configurado."
        )
        lotaje = limites["maximo"]

    return {
        "lotaje": lotaje,
        "dinero_riesgo": dinero_riesgo,
        "distancia_sl": distancia_sl,
        "advertencia": advertencia,
    }


def _ajustar_a_paso(lotaje, paso):
    """Redondea el lotaje hacia ABAJO al multiplo de 'paso' mas cercano.
    Redondear hacia abajo nunca arriesga mas de lo configurado."""
    if paso <= 0:
        return round(lotaje, 2)
    pasos = math.floor(lotaje / paso)
    resultado = pasos * paso
    decimales = len(str(paso).split(".")[-1]) if "." in str(paso) else 0
    return round(resultado, decimales)
'''


def main():
    if not BACKEND.exists():
        print(f"No encuentro la carpeta 'backend' dentro de {RAIZ}")
        print("Corre este script desde la carpeta raiz de tu proyecto (spotplus-bot).")
        sys.exit(1)

    conexion = BACKEND / "conexion_mt5.py"
    servidor = BACKEND / "servidor.py"
    riesgo = BACKEND / "riesgo.py"

    for f in (conexion, servidor):
        if not f.exists():
            print(f"No encuentro {f}")
            sys.exit(1)

    print("1) Creando backend/riesgo.py")
    if riesgo.exists():
        print("  [saltado] backend/riesgo.py ya existe, no lo piso")
    else:
        riesgo.write_text(RIESGO_PY, encoding="utf-8")
        print("  [ok] creado")

    print("2) Editando backend/conexion_mt5.py")
    backup(conexion)
    texto = conexion.read_text(encoding="utf-8-sig")
    nueva_funcion = (
        '\n\n\n@_con_candado\n'
        'def obtener_limites_volumen(simbolo=SIMBOLO):\n'
        '    """Minimo, maximo y paso de lote que acepta el broker para este simbolo."""\n'
        '    info = mt5.symbol_info(simbolo)\n'
        '    if info is None:\n'
        '        return {"minimo": 0.01, "maximo": 100.0, "paso": 0.01}\n'
        '    return {\n'
        '        "minimo": float(getattr(info, "volume_min", 0.01) or 0.01),\n'
        '        "maximo": float(getattr(info, "volume_max", 100.0) or 100.0),\n'
        '        "paso": float(getattr(info, "volume_step", 0.01) or 0.01),\n'
        '    }\n'
    )
    if "def obtener_limites_volumen" in texto:
        print("  [saltado] obtener_limites_volumen (ya estaba aplicado)")
    else:
        texto = texto.rstrip("\n") + nueva_funcion
        print("  [ok] obtener_limites_volumen() agregada al final del archivo")
    conexion.write_text(texto, encoding="utf-8")

    print("3) Editando backend/servidor.py")
    backup(servidor)
    texto = servidor.read_text(encoding="utf-8-sig")

    texto = reemplazar(
        servidor, texto := texto,
        texto, "placeholder",
    ) if False else texto  # no-op, mantenemos 'texto' como variable de trabajo

    def rp(viejo, nuevo, nombre):
        nonlocal texto
        if nuevo in texto:
            print(f"  [saltado] {nombre} (ya aplicado)")
            return
        if viejo not in texto:
            print(f"  [!] No encontrado: {nombre} (revisalo a mano)")
            return
        texto = texto.replace(viejo, nuevo, 1)
        print(f"  [ok] {nombre}")

    rp(
        "from .conexion_mt5 import (\n"
        "    conectar, desconectar, obtener_velas, obtener_velas_multi, obtener_precio_actual,\n"
        "    obtener_info_cuenta, obtener_posiciones_abiertas, obtener_historial_cerradas,\n"
        "    enviar_orden_manual, cerrar_posicion, cerrar_posicion_parcial, obtener_simbolos_disponibles,\n"
        "    modificar_sl,\n"
        ")\n",
        "from .conexion_mt5 import (\n"
        "    conectar, desconectar, obtener_velas, obtener_velas_multi, obtener_precio_actual,\n"
        "    obtener_info_cuenta, obtener_posiciones_abiertas, obtener_historial_cerradas,\n"
        "    enviar_orden_manual, cerrar_posicion, cerrar_posicion_parcial, obtener_simbolos_disponibles,\n"
        "    modificar_sl,\n"
        ")\n"
        "from .riesgo import calcular_lotaje\n",
        "import calcular_lotaje",
    )

    rp(
        '    "volumen_operacion": 0.01,\n'
        '    "sl_operacion": 0.0,\n'
        '    "tp_operacion": 0.0,\n',
        '    "volumen_operacion": 0.01,\n'
        '    "sl_operacion": 0.0,\n'
        '    "tp_operacion": 0.0,\n'
        '    "modo_riesgo": "fijo_lotes",\n'
        '    "riesgo_valor": 1.0,\n',
        "config_bot: modo_riesgo / riesgo_valor",
    )

    rp(
        '    "ultima_senal_ejecutada": None,\n'
        '    "ultima_orden": None,\n'
        '}',
        '    "ultima_senal_ejecutada": None,\n'
        '    "ultima_orden": None,\n'
        '    "ultimo_calculo_riesgo": None,\n'
        '}',
        "estado: ultimo_calculo_riesgo",
    )

    import re as _re
    if "modo_riesgo: str | None = None" in texto:
        print("  [saltado] ConfigRequest: modo_riesgo / riesgo_valor (ya aplicado)")
    else:
        patron = _re.compile(
            r'(    be_porcentaje: float \| None = None[^\n]*\n)'
        )
        if patron.search(texto):
            texto = patron.sub(
                r'\1'
                '    modo_riesgo: str | None = None        # "fijo_lotes" | "porcentaje" | "monto_fijo"\n'
                '    riesgo_valor: float | None = None      # % del balance o monto fijo\n',
                texto, count=1,
            )
            print("  [ok] ConfigRequest: modo_riesgo / riesgo_valor")
        else:
            print("  [!] No encontrado: ConfigRequest: modo_riesgo / riesgo_valor (revisalo a mano)")

    rp(
        '    if cfg.be_porcentaje is not None:\n'
        '        config_bot["be_porcentaje"] = cfg.be_porcentaje\n'
        '    return config_bot',
        '    if cfg.be_porcentaje is not None:\n'
        '        config_bot["be_porcentaje"] = cfg.be_porcentaje\n'
        '    if cfg.modo_riesgo is not None and cfg.modo_riesgo in ("fijo_lotes", "porcentaje", "monto_fijo"):\n'
        '        config_bot["modo_riesgo"] = cfg.modo_riesgo\n'
        '    if cfg.riesgo_valor is not None:\n'
        '        config_bot["riesgo_valor"] = cfg.riesgo_valor\n'
        '    return config_bot',
        "post_config: guarda modo_riesgo / riesgo_valor",
    )

    rp(
        '                resultado = enviar_orden_manual(\n'
        '                    tipo=senal,\n'
        '                    volumen=config_bot["volumen_operacion"],\n'
        '                    simbolo=simbolo,\n'
        '                    sl=sl_envio,\n'
        '                    tp=tp_envio,\n'
        '                )\n'
        '                estado["ultima_orden"] = resultado\n'
        '                estado["ultima_senal_ejecutada"] = senal',
        '                volumen_envio = config_bot["volumen_operacion"]\n'
        '                if config_bot["modo_riesgo"] in ("porcentaje", "monto_fijo") and sl_envio:\n'
        '                    info_cuenta = obtener_info_cuenta()\n'
        '                    precio_ref = precio["ask"] if senal == "compra" else precio["bid"] if precio else None\n'
        '                    if info_cuenta and precio_ref:\n'
        '                        modo = "porcentaje" if config_bot["modo_riesgo"] == "porcentaje" else "fijo"\n'
        '                        calculo = calcular_lotaje(\n'
        '                            balance=info_cuenta["balance"],\n'
        '                            precio_entrada=precio_ref,\n'
        '                            sl=sl_envio,\n'
        '                            simbolo=simbolo,\n'
        '                            modo=modo,\n'
        '                            valor=config_bot["riesgo_valor"],\n'
        '                        )\n'
        '                        if calculo is not None:\n'
        '                            volumen_envio = calculo["lotaje"]\n'
        '                            estado["ultimo_calculo_riesgo"] = calculo\n'
        '\n'
        '                resultado = enviar_orden_manual(\n'
        '                    tipo=senal,\n'
        '                    volumen=volumen_envio,\n'
        '                    simbolo=simbolo,\n'
        '                    sl=sl_envio,\n'
        '                    tp=tp_envio,\n'
        '                )\n'
        '                estado["ultima_orden"] = resultado\n'
        '                estado["ultima_senal_ejecutada"] = senal',
        "ciclo_bot: lotaje dinamico antes de enviar_orden_manual",
    )

    servidor.write_text(texto, encoding="utf-8")

    print("\nListo. Revisa los mensajes [!] si alguno salio - significa que ese")
    print("archivo ya no coincide exactamente con lo esperado y hay que aplicar")
    print("ese paso a mano. Los backups quedaron como *.bak junto a cada archivo.")
    print("\nAhora reinicia el servidor (uvicorn) para que tome los cambios.")


if __name__ == "__main__":
    main()
