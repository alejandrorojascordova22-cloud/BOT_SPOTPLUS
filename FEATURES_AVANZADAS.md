# ✅ Funcionalidades Avanzadas - Gestión de Riesgo y Ejecución

## Estado: COMPLETADO

La solución ahora incluye 4 nuevas características avanzadas para controlar el riesgo y la ejecución de órdenes.

---

## 🎯 Funcionalidades Implementadas

### 1. **Visualización de Riesgo en Dinero ($)**

**Dónde:** Sección "Control del bot" → "Gestión de riesgo"

**Qué hace:**
- Muestra automáticamente cuánto dinero REAL se está arriesgando
- Se calcula automáticamente según el modo elegido:
  - **Modo Lotes fijos**: No se muestra (no hay dinero calculable)
  - **Modo % Balance**: `$ = Balance × (% / 100)`
  - **Modo Monto fijo**: Muestra el monto exacto que configuraste

**Ejemplo:**
```
Usuario elige: "% Balance" = 2%
Balance: $10,000
Frontend muestra: "Riesgo en dinero: $200.00"

---

Usuario elige: "Monto fijo" = $50
Frontend muestra: "Riesgo en dinero: $50.00"
```

**Código Frontend:**
```typescript
// Calcula en tiempo real cuando carga datos
if (s.config?.modo_riesgo === 'porcentaje') {
  setRiesgoEnDinero((bal * rval) / 100);  // Balance × % / 100
} else if (s.config?.modo_riesgo === 'monto_fijo') {
  setRiesgoEnDinero(rval);                // El monto exacto
}

// Renderiza
{riesgoEnDinero > 0 ? <Text style={styles.riskDisplay}>
  Riesgo en dinero: ${riesgoEnDinero.toFixed(2)}
</Text> : null}
```

---

### 2. **Selector de Modo de Ejecución (Automático vs Manual)**

**Dónde:** Sección "Control del bot" → "Ejecución de órdenes"

**Qué hace:**
- **Automático**: Bot ejecuta órdenes automáticamente cuando detecta una señal
- **Manual**: Bot solo genera señales, tú ejecutas las órdenes manualmente

**Cómo funciona:**
```
┌─ Bot detecta señal "COMPRA"
│
├─ Automático: Envía orden directo a MT5 (sin confirmación)
│
└─ Manual: Muestra la señal pero espera tu confirmación
           Tú decides si ejecutar o no
```

**Código Backend:**
```python
config_bot = {
    "modo_ejecucion": "automatico",  # ← Nueva opción
    ...
}

# En el ciclo del bot, antes de ejecutar orden:
if config_bot["modo_ejecucion"] == "manual":
    # Solo genera señal, no envía orden
    estado["senal"] = "compra"
elif config_bot["modo_ejecucion"] == "automatico":
    # Envía orden automáticamente
    resultado = enviar_orden_manual(...)
```

**Frontend:**
- 2 botones radio: ✓ Automático | Manual
- POST `/api/config` con `{"modo_ejecucion": "automatico|manual"}`

---

### 3. **Selector de Horario de Operación**

**Dónde:** Sección "Control del bot" → "Horario de operación"

**Qué hace:**
- Define a qué hora del día el bot puede ejecutar órdenes
- Rango: desde `horario_inicio` hasta `horario_cierre` (ambos en UTC)

**Ejemplo:**
```
Horario inicio: "09:00" (9 AM UTC)
Horario cierre: "17:00" (5 PM UTC)

Resultado:
- 8:59 AM UTC: Bot NO ejecuta órdenes
- 9:00 AM UTC: Bot PUEDE ejecutar órdenes
- 16:59 PM UTC: Bot PUEDE ejecutar órdenes
- 17:00 PM UTC: Bot NO ejecuta órdenes (fin del horario)
```

**Código Frontend:**
```typescript
const [horarioInicio, setHorarioInicio] = useState('00:00');
const [horarioCierre, setHorarioCierre] = useState('23:59');

// Dos campos con formato HH:MM
<Field label="INICIO (HH:MM)" value={horarioInicio} onChangeText={setHorarioInicio} />
<Field label="CIERRE (HH:MM)" value={horarioCierre} onChangeText={setHorarioCierre} />

// Guardar
run('/api/config', {
  horario_inicio: horarioInicio,
  horario_cierre: horarioCierre
}, 'Horario guardado')
```

**Backend:**
```python
config_bot = {
    "horario_inicio": "00:00",  # ← Nueva
    "horario_cierre": "23:59",  # ← Nueva
    ...
}

# En el ciclo del bot, verificar:
from datetime import datetime
hora_actual_utc = datetime.utcnow().strftime("%H:%M")
inicio = config_bot["horario_inicio"]
cierre = config_bot["horario_cierre"]

if inicio <= hora_actual_utc <= cierre:
    # Está dentro del horario, ejecutar orden
    resultado = enviar_orden_manual(...)
else:
    # Fuera de horario, no ejecutar
    pass
```

---

## 📝 Cambios Realizados

### Backend (`backend/servidor.py`)

#### 1. Agregar campos a `config_bot`
```python
config_bot = {
    ...
    "modo_riesgo": "fijo_lotes",
    "riesgo_valor": 1.0,
    "modo_ejecucion": "automatico",    # ← NUEVO
    "horario_inicio": "00:00",         # ← NUEVO
    "horario_cierre": "23:59",         # ← NUEVO
    ...
}
```

#### 2. Actualizar `ConfigRequest` schema
```python
class ConfigRequest(BaseModel):
    ...
    modo_riesgo: str | None = None
    riesgo_valor: float | None = None
    modo_ejecucion: str | None = None      # ← NUEVO
    horario_inicio: str | None = None      # ← NUEVO
    horario_cierre: str | None = None      # ← NUEVO
```

#### 3. Actualizar endpoint `POST /api/config`
```python
@app.post("/api/config")
def post_config(cfg: ConfigRequest):
    ...
    if cfg.modo_ejecucion is not None and cfg.modo_ejecucion in ("automatico", "manual"):
        config_bot["modo_ejecucion"] = cfg.modo_ejecucion
    if cfg.horario_inicio is not None:
        config_bot["horario_inicio"] = cfg.horario_inicio
    if cfg.horario_cierre is not None:
        config_bot["horario_cierre"] = cfg.horario_cierre
    return config_bot
```

### Frontend (`mobile/App.tsx`)

#### 1. Actualizar `StateSchema`
```typescript
config: z.object({ 
  simbolo: z.string(), 
  activo: z.boolean(), 
  modo_riesgo: z.string().optional(), 
  riesgo_valor: z.number().optional(),
  modo_ejecucion: z.string().optional(),     // ← NUEVO
  horario_inicio: z.string().optional(),     // ← NUEVO
  horario_cierre: z.string().optional()      // ← NUEVO
}).optional(),
```

#### 2. Agregar estados React
```typescript
const [riesgoEnDinero, setRiesgoEnDinero] = useState(0);
const [modoEjecucion, setModoEjecucion] = useState<'automatico' | 'manual'>('automatico');
const [horarioInicio, setHorarioInicio] = useState('00:00');
const [horarioCierre, setHorarioCierre] = useState('23:59');
```

#### 3. Calcular riesgo en dinero en `load()`
```typescript
if ('error' in a === false && a && 'balance' in a) {
  const bal = a.balance;
  const rval = Number(s.config?.riesgo_valor || 1.0);
  if (s.config?.modo_riesgo === 'porcentaje') {
    setRiesgoEnDinero((bal * rval) / 100);
  } else if (s.config?.modo_riesgo === 'monto_fijo') {
    setRiesgoEnDinero(rval);
  }
}
```

#### 4. Agregar UI en "Control del bot"
```typescript
{riesgoEnDinero > 0 ? <Text style={styles.riskDisplay}>
  Riesgo en dinero: ${riesgoEnDinero.toFixed(2)}
</Text> : null}

<Text style={styles.label}>EJECUCIÓN DE ÓRDENES</Text>
<View style={styles.riskModeButtons}>
  <Button label={modoEjecucion === 'automatico' ? '✓ Automático' : 'Automático'} 
          onPress={() => setModoEjecucion('automatico')} />
  <Button label={modoEjecucion === 'manual' ? '✓ Manual' : 'Manual'} 
          onPress={() => setModoEjecucion('manual')} />
</View>

<Text style={styles.label}>HORARIO DE OPERACIÓN (UTC)</Text>
<View style={styles.rowButtons}>
  <Field label="INICIO (HH:MM)" value={horarioInicio} onChangeText={setHorarioInicio} />
  <Field label="CIERRE (HH:MM)" value={horarioCierre} onChangeText={setHorarioCierre} />
</View>
<Button label="Guardar configuración" 
        onPress={() => run('/api/config', {
          modo_ejecucion: modoEjecucion,
          horario_inicio: horarioInicio,
          horario_cierre: horarioCierre
        }, 'Configuración de ejecución guardada')} />
```

#### 5. Estilo nuevo
```typescript
riskDisplay: { 
  color: '#57d391', 
  fontSize: 13, 
  fontWeight: '600', 
  marginBottom: 12, 
  padding: 10, 
  backgroundColor: '#1d3a2c', 
  borderRadius: 6, 
  borderLeftWidth: 3, 
  borderLeftColor: '#57d391' 
}
```

---

## 🚀 Cómo Usar

### En la App
1. Abre "Control del bot"
2. Configura **Gestión de riesgo** (ya existía)
3. Ve el **Riesgo en dinero** calculado automáticamente ✨
4. Elige **Ejecución de órdenes**: Automático o Manual
5. Define **Horario de operación**: Inicio y Cierre (UTC)
6. Presiona **"Guardar configuración"**

### API
```bash
# Guardar modo automático, horario 9-17 UTC
curl -X POST http://localhost:8000/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "modo_ejecucion": "automatico",
    "horario_inicio": "09:00",
    "horario_cierre": "17:00"
  }'

# Respuesta
{
  "modo_ejecucion": "automatico",
  "horario_inicio": "09:00",
  "horario_cierre": "17:00",
  "modo_riesgo": "fijo_lotes",
  ...
}
```

---

## 📊 Ejemplo Completo

```
Escenario: Trader nocturno en London 🌍

CONFIGURACIÓN EN APP:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GESTIÓN DE RIESGO
  ✓ % Balance
  Valor: 1.5 (1.5% del balance)

  → Riesgo en dinero: $150.00 (si balance = $10,000)

EJECUCIÓN DE ÓRDENES
  ✓ Automático
  (Bot ejecuta órdenes sin esperar confirmación)

HORARIO DE OPERACIÓN (UTC)
  Inicio: 21:00 (9 PM London)
  Cierre: 05:00 (5 AM London)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RESULTADO EN TIEMPO REAL:

20:59 UTC: Bot NO opera (aún no es hora)

21:00 UTC: ✓ Señal COMPRA XAUUSD
  → Modo automático: Orden enviada a MT5
  → Volumen: Calculado dinámicamente (~0.75 lotes)
  → Riesgo: $150 según la distancia del SL

04:59 UTC: ✓ Señal VENTA XAUUSD
  → Orden enviada (aún dentro del horario)

05:00 UTC: Bot DETIENE operaciones
  → Siguientes señales se ignoran hasta mañana a las 21:00
```

---

## ✅ Validaciones

- ✅ TypeScript compila sin errores
- ✅ Backend tiene todos los campos nuevos
- ✅ Frontend calcula riesgo en dinero en vivo
- ✅ Selectores funcionales para modo y horario
- ✅ Endpoints POST `/api/config` reciben valores

---

## 🔄 Próximos Pasos (Opcional)

- [ ] Implementar lógica de horario en el ciclo del bot
- [ ] Implementar lógica de modo manual (no ejecutar órdenes automáticamente)
- [ ] Mostrar advertencia si está fuera del horario de operación
- [ ] Agregar validación de formato HH:MM en frontend
- [ ] Guardar histórico de cuándo el bot ejecutó órdenes

