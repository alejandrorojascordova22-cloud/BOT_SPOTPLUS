import requests

# ============================================================
# NOTIFICACIONES A TELEGRAM - SpotPlus
# ============================================================
# No usa ninguna librería especial de Telegram: solo llama al
# endpoint HTTP sendMessage de la Bot API. Así no agregamos
# dependencias pesadas ni tareas en segundo plano (el bot NO
# necesita "escuchar" nada, solo mandar avisos).
# ============================================================


def enviar_mensaje(token, chat_id, texto):
    """Manda un mensaje de texto al chat/grupo indicado.
    Devuelve {"ok": True} o {"ok": False, "error": "..."}. Nunca lanza excepción:
    si Telegram falla, el bot de trading debe seguir operando normal."""
    if not token or not chat_id:
        return {"ok": False, "error": "Falta el token del bot o el chat_id"}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        respuesta = requests.post(
            url,
            json={"chat_id": chat_id, "text": texto, "parse_mode": "HTML"},
            timeout=10,
        )
        datos = respuesta.json()
        if not datos.get("ok"):
            return {"ok": False, "error": datos.get("description", "Error desconocido de Telegram")}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
