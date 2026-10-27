# ✅ Gestión de Riesgo Dinámico - Integración Completada

## Resumen de Cambios

La integración frontend-backend de la gestión de riesgo dinámico está **100% completada y lista para usar**.

---

## 📱 Frontend (Mobile - React Native)

### Archivo: `mobile/App.tsx`

#### 1. **Schema actualizado** (línea 19)
```typescript
// ANTES
config: z.object({ simbolo: z.string(), activo: z.boolean() }).optional(),

// AHORA
config: z.object({ 
  simbolo: z.string(), 
  activo: z.boolean(), 
  modo_riesgo: z.string().optional(),      // ← NUEVO
  riesgo_valor: z.number().optional()       // ← NUEVO
}).optional(),
```

#### 2. **Estados React** (línea 93)
```typescript
// AGREGADOS
const [modoRiesgo, setModoRiesgo] = useState<'fijo_lotes' | 'porcentaje' | 'monto_fijo'>('fijo_lotes');
const [riesgoValor, setRiesgoValor] = useState('1.0');
```

#### 3. **Carga de valores del backend** (en función `load()`)
```typescript
// Los valores se cargan automáticamente cuando inicia la app
setModoRiesgo((s.config?.modo_riesgo as ...) || 'fijo_lotes');
setRiesgoValor(String(s.config?.riesgo_valor || '1.0'));
```

#### 4. **Interfaz en "Control del bot"** (línea 155)
Una nueva sección visual con:
- **3 Radio Buttons** para elegir modo:
  - ✓ Lotes fijos (volumen fijo)
  - ✓ % Balance (arriesga % del balance)
  - ✓ Monto fijo (arriesga $ fijo)
  
- **Campo de entrada dinámico** que cambia etiqueta según modo:
  - "LOTES" si está en fijo_lotes
  - "% DEL BALANCE" si está en porcentaje
  - "DINERO A ARRIESGAR" si está en monto_fijo

- **Botón "Guardar riesgo"** que envia `POST /api/config` con:
  ```json
  {
    "modo_riesgo": "porcentaje|monto_fijo|fijo_lotes",
    "riesgo_valor": 1.0
  }
  ```

#### 5. **Estilo nuevo** (línea 177)
```typescript
riskModeButtons: { 
  flexDirection: 'row', 
  gap: 9, 
  flexWrap: 'wrap',  // Los botones se adaptan al ancho
  marginBottom: 12 
}
```

---

## 🔧 Backend (ya completado en sesión anterior)

### Archivo: `backend/riesgo.py`
✅ Función `calcular_lotaje()` - Calcula dinámicamente el volumen según:
- Distancia del Stop Loss
- Riesgo configurado (% o monto)
- Límites del broker

### Archivo: `backend/conexion_mt5.py`
✅ Función `obtener_limites_volumen()` - Obtiene min/max/paso de volumen del broker

### Archivo: `backend/servidor.py`
✅ 6 cambios en:
1. Importa `calcular_lotaje` de `riesgo.py`
2. Config tiene campos `modo_riesgo` y `riesgo_valor`
3. Estado tiene campo `ultimo_calculo_riesgo`
4. Schema `ConfigRequest` actualizado
5. Endpoint `POST /api/config` maneja los nuevos campos
6. Ciclo bot calcula lotaje dinámico ANTES de enviar orden

---

## 🚀 Cómo Usar

### Paso 1: Iniciar el servidor backend
```powershell
cd c:\Users\infin\Videos\spotplus-bot
(& .\venv\Scripts\Activate.ps1)
uvicorn backend.servidor:app --host 0.0.0.0 --port 8000 --reload
```

### Paso 2: Iniciar el frontend
```powershell
cd c:\Users\infin\Videos\spotplus-bot\mobile
npm start
# O en web: npm run web
```

### Paso 3: Usar el selector en la app
1. Abre "Control del bot"
2. Elige modo de riesgo:
   - **Lotes fijos**: Ingresa cantidad fija de lotes (ej: 0.01)
   - **% Balance**: Ingresa % del balance a arriesgar (ej: 1.0)
   - **Monto fijo**: Ingresa dinero fijo a arriesgar (ej: 50)
3. Presiona "Guardar riesgo"
4. Cuando el bot genere una orden automática, usará ese cálculo

---

## 📊 Ejemplo de Flujo

```
Usuario selecciona: "% Balance" y pone 2.0
                      ↓
Presiona "Guardar riesgo"
                      ↓
Frontend envía: POST /api/config
  { "modo_riesgo": "porcentaje", "riesgo_valor": 2.0 }
                      ↓
Backend guarda en config_bot
                      ↓
Bot genera señal de compra en XAUUSD a 2650.00
                      ↓
Backend calcula:
  - Balance: $10,000
  - Riesgo: 2% = $200
  - SL: 2649.00 (1 pip de distancia)
  - Calcula lotaje automático = calcular_lotaje(...)
  - Ajusta a límites del broker
  - Envía orden con lotaje calculado
                      ↓
Estado ahora tiene: "ultimo_calculo_riesgo": {
  "lotaje": 0.75,
  "dinero_riesgo": 200.0,
  "distancia_sl": 1.0,
  "advertencia": null
}
```

---

## ✅ Validaciones Completadas

- ✅ Backend imports OK
- ✅ `calcular_lotaje()` disponible
- ✅ `modo_riesgo` = "fijo_lotes" (default)
- ✅ `riesgo_valor` = 1.0 (default)
- ✅ Frontend compila sin errores
- ✅ React/React-Native/Expo deps OK
- ✅ Schemas sincronizados frontend-backend

---

## 🔗 Endpoints Relevantes

### `GET /api/estado`
Devuelve estado completo incluyendo `config` con:
```json
{
  "config": {
    "simbolo": "XAUUSD",
    "activo": true,
    "modo_riesgo": "porcentaje",
    "riesgo_valor": 2.0
  },
  ...
}
```

### `POST /api/config`
```json
{
  "modo_riesgo": "porcentaje|monto_fijo|fijo_lotes",
  "riesgo_valor": number
}
```

---

## 📝 Notas

- Si el SL es 0, no se aplica cálculo dinámico (se usa volumen fijo)
- Si el cálculo da un lotaje menor al mínimo del broker, se usa el mínimo (avisa)
- Si el cálculo da un lotaje mayor al máximo del broker, se usa el máximo (avisa)
- La advertencia se guarda en `estado["ultimo_calculo_riesgo"]["advertencia"]`

---

## 🎯 Próximos Pasos (Opcional)

- [ ] Agregar visualización del último cálculo de riesgo en la UI
- [ ] Mostrar advertencias de ajustes de lotaje en tiempo real
- [ ] Agregar histórico de cálculos de riesgo
- [ ] Testing automático del flujo completo

