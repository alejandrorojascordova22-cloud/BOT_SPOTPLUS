import time
from .conexion_mt5 import conectar, desconectar, obtener_velas, obtener_precio_actual
from .indicadores import calcular_rsi, calcular_media_movil
from .estrategias import evaluar_estrategia

def ciclo():
    df = obtener_velas()
    if df is None:
        return

    df = calcular_rsi(df)
    df = calcular_media_movil(df)

    señal = evaluar_estrategia(df, "rsi", {"rsi_sobreventa": 30, "rsi_sobrecompra": 70})
    precio = obtener_precio_actual()

    print(f"RSI: {df.iloc[-1]['rsi']:.2f} | Precio: {precio} | Señal: {señal}")

if __name__ == "__main__":
    if conectar():
        try:
            while True:
                ciclo()
                time.sleep(60)
        except KeyboardInterrupt:
            print("Bot detenido manualmente")
        finally:
            desconectar()