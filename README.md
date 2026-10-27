# SpotPlus Bot

Bot de trading con backend FastAPI, conexión a MetaTrader 5 y cliente Expo para móvil y web.

## Estructura

- `backend/`: API FastAPI y lógica del bot.
- `web/`: interfaz HTML original y alternativa de emergencia servida por FastAPI.
- `mobile/App.tsx`: pantalla principal que ve el usuario, hecha con React Native + TypeScript + Expo.
- `mobile/index.ts`: entrada de Expo que registra `App.tsx`.
- `.env`: credenciales locales. No se incluye en el ZIP de respaldo.

## Ejecutar el backend

Desde la raíz del proyecto:

```powershell
.\venv\Scripts\python.exe -m uvicorn backend.servidor:app --reload --port 8000
```

Los archivos con extension `.bak` dentro de `backend/` son copias de respaldo y no se ejecutan ni se importan como parte del backend activo. El archivo de entrada de la API es `backend/servidor.py`.

La interfaz HTML alternativa queda disponible en `http://localhost:8000`.

## Ejecutar Expo Web

```powershell
npm --prefix .\mobile run web
```

La app Expo queda normalmente en `http://localhost:8081`.

Para ver la pantalla TypeScript debes abrir la dirección de Expo (`8081`), no la interfaz HTML (`8000`).

Si la API no está en el mismo equipo, define `EXPO_PUBLIC_API_URL` antes de iniciar Expo.