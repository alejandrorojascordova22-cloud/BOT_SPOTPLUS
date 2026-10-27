import { StatusBar } from 'expo-status-bar';
import DateTimePicker from '@react-native-community/datetimepicker';
import { createElement, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Modal, Platform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { WebView } from 'react-native-webview';
import { z } from 'zod';

const API_URL = process.env.EXPO_PUBLIC_API_URL || (Platform.OS === 'android' ? 'http://10.0.2.2:8000' : 'http://localhost:8000');
// Debe ser el mismo valor que API_KEY en el .env del backend. Se mete como
// variable de build (EXPO_PUBLIC_API_KEY) para no dejar el token hardcodeado
// en el repo.
const API_KEY = process.env.EXPO_PUBLIC_API_KEY || '';
const BACKTEST_TIMEFRAMES = ['M1', 'M5', 'M15', 'H1'] as const;
// Mismo mapeo que TEMPORALIDADES en backend/estructura.py — se muestra en el
// panel para que quede claro qué temporalidad escrita cae en cada grupo.
const GRUPO_TEMPORALIDAD: Record<string, 'micro' | 'intermedia' | 'macro' | 'extra_macro'> = {
  M1: 'micro', M3: 'micro', M5: 'micro',
  M15: 'intermedia', M30: 'intermedia', H1: 'intermedia',
  H2: 'macro', H4: 'macro', D1: 'macro',
  W1: 'extra_macro', MN1: 'extra_macro',
};
const TEMPORALIDADES_ESCANEO = Object.keys(GRUPO_TEMPORALIDAD);
// El texto se guarda como "M15:macro, H1:intermedia" — estas dos funciones
// van y vuelven entre ese texto y un objeto {tf: grupo} fácil de usar.
function parseGruposTexto(texto: string): Record<string, string> {
  const out: Record<string, string> = {};
  texto.split(',').map((par) => par.trim()).filter(Boolean).forEach((par) => {
    const [tf, grupo] = par.split(':').map((x) => x.trim());
    if (tf && grupo) out[tf.toUpperCase()] = grupo.toLowerCase();
  });
  return out;
}
function serializeGruposTexto(obj: Record<string, string>): string {
  return Object.entries(obj).map(([tf, grupo]) => `${tf}:${grupo}`).join(', ');
}

// --- Schemas: fuente única de verdad para la forma de los datos que manda el backend. ---
// Los `type` se derivan de estos schemas con z.infer, así que nunca quedan desincronizados
// entre sí. Si servidor.py cambia la forma de una respuesta y el schema no se actualiza,
// api() revienta con un error legible en vez de dejar pasar datos con forma incorrecta.
const ParamSchema = z.object({ nombre: z.string(), etiqueta: z.string(), tipo: z.string(), default: z.union([z.string(), z.number()]) });
const BlockSchema = z.object({ nombre: z.string(), explicacion: z.string(), parametros: z.array(ParamSchema) });
const EstadoZonaSchema = z.object({
  estado: z.enum(['sin_zona', 'esperando_confirmacion', 'confirmada']),
  tipo: z.string().optional(),
  bloque: z.string().optional(),
  zona_precio: z.number().optional(),
  precio_entrada: z.number().optional(),
  sl: z.number().optional(),
  tp: z.number().optional(),
}).nullable();
const ContextoTemporalidadSchema = z.object({
  grupo: z.string(),
  sesgo: z.enum(['alcista', 'bajista']).nullable(),
  ultimo_maximo: z.object({ precio: z.number(), clase: z.string().nullable() }).nullable(),
  ultimo_minimo: z.object({ precio: z.number(), clase: z.string().nullable() }).nullable(),
  acumulacion: z.object({
    techo: z.number(),
    piso: z.number(),
    indice_inicio: z.number(),
    indice_fin: z.number(),
    toques: z.number(),
  }).nullable(),
  liquidez_externa: z.array(z.object({
    tipo: z.string(),
    precio: z.number(),
    barrida: z.boolean(),
  })),
});
const ContextoEstructuraSchema = z.object({
  temporalidades: z.record(z.string(), ContextoTemporalidadSchema),
  conflictos: z.array(z.object({
    mayor: z.string(),
    sesgo_mayor: z.string(),
    menor: z.string(),
    sesgo_menor: z.string(),
  })),
}).nullable().optional();
const UltimaOrdenSchema = z.object({ ok: z.boolean().optional(), ticket: z.number().optional(), precio: z.number().optional(), error: z.string().optional() }).nullable().optional();
const StateSchema = z.object({
  conectado: z.boolean(),
  precio: z.object({ bid: z.number(), ask: z.number() }).nullable(),
  senal: z.string().nullable(),
  ultima_actualizacion: z.string().nullable(),
  simbolo_actual: z.string().nullable().optional(),
  error: z.string().nullable(),
  sesgo_macro: z.enum(['alcista', 'bajista']).nullable().optional(),
  estado_zona: EstadoZonaSchema.optional(),
  contexto_estructura: ContextoEstructuraSchema,
  ultima_orden: UltimaOrdenSchema,
  circuito_disparado: z.boolean().optional(),
  config: z.object({ simbolo: z.string(), simbolos_permitidos: z.array(z.string()).optional(), activo: z.boolean(), estrategia: z.string().nullable().optional(), estrategia_biblioteca_activa: z.string().nullable().optional(), timeframe_entrada: z.string().optional(), timeframe_macro: z.string().optional(), modo_riesgo: z.string().optional(), riesgo_valor: z.number().optional(), volumen_operacion: z.number().optional(), circuito_perdida_diaria_activo: z.boolean().optional(), circuito_perdida_diaria_pct: z.number().optional(), spread_maximo_puntos: z.number().optional(), max_operaciones_por_simbolo: z.number().optional(), filtro_sesgo_macro: z.boolean().optional(), aviso_cambio_direccion: z.boolean().optional(), proteger_cambio_direccion: z.boolean().optional(), noticia_be_activo: z.boolean().optional(), bloqueo_noticias_activo: z.boolean().optional(), riesgo_total_activo: z.boolean().optional(), riesgo_total_pct: z.number().optional(), be_activo: z.boolean().optional(), be_porcentaje: z.number().optional(), be_cascada_buffer_pct: z.number().optional(), be_dinamico_activo: z.boolean().optional(), be_dinamico_fibo_nivel: z.number().optional(), grupos_temporalidad: z.record(z.string(), z.string()).optional(), temporalidades_escaneo: z.array(z.string()).optional(), parametros: z.record(z.string(), z.union([z.string(), z.number(), z.boolean(), z.array(z.number())])).optional() }).optional(),
});
const AccountSchema = z.object({ cuenta: z.number(), servidor: z.string(), balance: z.number(), equity: z.number(), profit: z.number(), apalancamiento: z.number(), moneda: z.string(), es_real: z.boolean() });
const AccountResponseSchema = z.union([AccountSchema, z.object({ error: z.string() })]);
const PositionSchema = z.object({ ticket: z.number(), simbolo: z.string(), tipo: z.string(), volumen: z.number(), precio_apertura: z.number(), precio_actual: z.number(), profit: z.number(), hora_apertura: z.number().optional() });

// Formatea un timestamp (epoch en segundos) a hora local del dispositivo en
// formato 12 horas (AM/PM), sin importar el idioma/región del teléfono.
function formatearHora12(epochSegundos?: number | null): string {
  if (!epochSegundos) return 'sin dato';
  const fecha = new Date(epochSegundos * 1000);
  const fechaTxt = fecha.toLocaleDateString();
  const horaTxt = fecha.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', hour12: true });
  return `${fechaTxt} ${horaTxt}`;
}
const SavedSchema = z.object({ nombre: z.string(), explicacion: z.string(), codigo: z.string(), volumen: z.number(), sl: z.number(), tp: z.number(), modo_riesgo: z.string().optional(), riesgo_valor: z.number().optional(), usa_multi_temporalidad: z.boolean().optional(), timeframe_entrada: z.string().optional(), timeframe_macro: z.string().optional(), temporalidades_escaneo: z.array(z.string()).optional(), roles_temporalidad: z.record(z.string(), z.string()).optional(), zonas_permitidas: z.array(z.string()).optional(), es_estructura_dinamica: z.boolean().optional(), parametros: z.record(z.string(), z.union([z.string(), z.number(), z.boolean(), z.array(z.number())])).optional() });
const CalculoRiesgoSchema = z.union([
  z.object({ lotaje: z.number(), dinero_riesgo: z.number(), distancia_sl: z.number(), advertencia: z.string().nullable(), perdida_estimada: z.number().optional(), ganancia_estimada: z.number().optional(), ratio_rr: z.number().optional(), riesgo_real_pct: z.number().nullable().optional(), oportunidades: z.number().optional() }),
  z.object({ error: z.string() }),
]);
const TelegramConfigSchema = z.object({ activo: z.boolean(), bot_token: z.string(), chat_id: z.string(), aviso_apertura: z.boolean(), aviso_cierre: z.boolean(), aviso_sl_tp: z.boolean(), aviso_parcial: z.boolean(), aviso_zona: z.boolean() });
const TelegramProbarSchema = z.object({ ok: z.boolean(), error: z.string().optional() });
const HistoryItemSchema = z.object({ ticket: z.number(), simbolo: z.string(), tipo: z.string(), volumen: z.number(), profit: z.number(), hora: z.number() });
const EstadisticasSchema = z.object({ total_operaciones: z.number(), ganadoras: z.number(), perdedoras: z.number(), winrate_pct: z.number(), ganancia_total: z.number(), mejor_operacion: z.number(), peor_operacion: z.number(), profit_factor: z.number().nullable(), racha_actual: z.number() });
const DiarioSchema = z.object({ id: z.number(), ticket: z.number(), estado: z.string(), simbolo: z.string(), tipo: z.string(), volumen: z.number(), entrada: z.number().nullable(), sl: z.number().nullable(), tp: z.number().nullable(), origen: z.string().optional(), perdida_estimada: z.number().nullable().optional(), ganancia_estimada: z.number().nullable().optional(), ratio_rr: z.number().nullable().optional(), hora_apertura: z.number(), hora_cierre: z.number().nullable(), resultado: z.string().nullable(), profit: z.number().nullable(), precio_cierre: z.number().nullable(), duracion_seg: z.number().nullable(), notas: z.string().optional() });
const ResumenGrupoSchema = z.object({ operaciones: z.number(), winrate_pct: z.number(), profit: z.number() });
const DiarioStatsSchema = z.object({ total: ResumenGrupoSchema, por_resultado: z.record(z.string(), ResumenGrupoSchema), por_simbolo: z.record(z.string(), ResumenGrupoSchema), por_origen: z.record(z.string(), ResumenGrupoSchema) });
const NotaBitacoraSchema = z.object({ id: z.number(), hora: z.number(), ticket: z.number().nullable(), nota: z.string() });
const BloquesDisponiblesSchema = z.object({ bloques: z.record(z.string(), BlockSchema) });
const RunResultSchema = z.object({ ok: z.boolean().optional(), ticket: z.number().optional(), precio: z.number().optional(), error: z.string().optional() });
const FundamentalEventoSchema = z.object({ title: z.string(), country: z.string().optional(), date: z.string(), impact: z.string().optional(), forecast: z.union([z.string(), z.number()]).optional(), previous: z.union([z.string(), z.number()]).optional(), actual: z.union([z.string(), z.number()]).optional(), sorpresa: z.string().nullable().optional(), sin_dato: z.boolean().optional() }).passthrough();
const FundamentalesSchema = z.object({ pendientes: z.array(FundamentalEventoSchema), en_proceso: z.array(FundamentalEventoSchema), pasados: z.array(FundamentalEventoSchema) });

// --- Backtesting ---
const TradeSchema = z.object({ tipo: z.string(), entrada: z.number(), sl: z.number(), tp: z.number(), salida: z.number(), volumen: z.number(), profit: z.number(), hora_apertura: z.string(), hora_cierre: z.string(), motivo_cierre: z.enum(['sl', 'tp', 'be']).optional(), be_aplicado: z.boolean().optional() });
const EquityPointSchema = z.object({ tiempo: z.string(), balance: z.number() });
const VelaReplaySchema = z.object({ time: z.number(), open: z.number(), high: z.number(), low: z.number(), close: z.number() });
const BacktestDiagnosticSchema = z.object({
  estrategia_activa: z.string(),
  tipo_estrategia: z.string(),
  timeframes_configurados: z.array(z.string()),
  timeframes_recibidos: z.array(z.string()),
  timeframes_faltantes: z.array(z.string()),
  evaluaciones_estrategia: z.number(),
  senales_detectadas: z.number(),
  oportunidades_detectadas: z.number(),
  entradas_ejecutadas: z.number(),
  senales_ignoradas_misma_direccion: z.number(),
});
const ReplayVelasResponseSchema = z.union([
  z.object({ timeframe: z.string(), velas: z.array(VelaReplaySchema) }),
  z.object({ error: z.string() }),
]);
const BacktestResultSchema = z.union([
  z.object({
    balance_inicial: z.number(),
    balance_final: z.number(),
    profit_total: z.number(),
    profit_flotante_al_final: z.number(),
    total_operaciones: z.number(),
    ganadoras: z.number(),
    perdedoras: z.number(),
    tasa_acierto: z.number(),
    max_drawdown: z.number(),
    posicion_abierta_al_final: z.object({
      tipo: z.string(),
      entrada: z.number(),
      sl: z.number(),
      tp: z.number(),
      volumen: z.number(),
      hora_apertura: z.string(),
      be_aplicado: z.boolean().optional(),
    }).nullable().optional(),
    operaciones: z.array(TradeSchema),
    curva_equity: z.array(EquityPointSchema),
    simbolo: z.string(),
    desde: z.string(),
    hasta: z.string(),
    velas_usadas: z.number(),
    velas: z.array(VelaReplaySchema),
    indice_inicio_replay: z.number().optional(),
    timeframe_entrada: z.string().nullable().optional(),
    timeframe_macro: z.string().nullable().optional(),
    diagnostico: BacktestDiagnosticSchema.optional(),
  }),
  z.object({ error: z.string() }),
]);
const BacktestStartSchema = z.object({ job_id: z.string() });
const BacktestProgresoSchema = z.object({
  listo: z.boolean(),
  procesado: z.number(),
  total: z.number(),
  porcentaje: z.number(),
  resultado: BacktestResultSchema.nullable(),
}).or(z.object({ error: z.string() }));

type Param = z.infer<typeof ParamSchema>;
type Block = z.infer<typeof BlockSchema>;
type Condition = { bloque: string; parametros: Record<string, string | number> }; // esto se ENVÍA, no se recibe: no necesita schema
type State = z.infer<typeof StateSchema>;
type Account = z.infer<typeof AccountSchema>;
type Position = z.infer<typeof PositionSchema>;
type Saved = z.infer<typeof SavedSchema>;
type AccountResponse = z.infer<typeof AccountResponseSchema>;
type HistoryItem = z.infer<typeof HistoryItemSchema>;
type BacktestResult = z.infer<typeof BacktestResultSchema>;
type VelaReplay = z.infer<typeof VelaReplaySchema>;

// `api()` exige un schema y valida la respuesta real contra él antes de devolverla.
async function api<T>(path: string, schema: z.ZodType<T>, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { ...(options?.headers || {}), 'X-API-Key': API_KEY },
  });
  if (!response.ok) throw new Error(`Servidor: ${response.status}`);
  const json = await response.json();
  const result = schema.safeParse(json);
  if (!result.success) {
    console.error(`Respuesta inesperada de ${path}:`, result.error.format());
    const issue = result.error.issues[0];
    const campo = issue?.path.length ? issue.path.join('.') : 'respuesta';
    throw new Error(`Respuesta incompatible de ${path}: ${campo} — ${issue?.message || 'formato inesperado'}. Reinicia el backend si está usando código anterior.`);
  }
  return result.data;
}
const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
// --- Replay visual del backtest (estilo TradingView Replay / FX Replay) ---
// hora_apertura/hora_cierre vienen como "YYYY-MM-DD HH:MM:SS" (str() de un
// Timestamp de pandas creado con pd.to_datetime(..., unit="s"), es decir, ya
// representan el mismo instante UTC que las velas). Se parsean a mano con
// Date.UTC en vez de Date.parse() porque ese formato con espacio no es
// consistente entre motores JS (algunos lo interpretan como hora local).
function epochUTC(fechaStr: string): number {
  const [fecha, hora] = fechaStr.split(' ');
  const [y, m, d] = (fecha || '').split('-').map(Number);
  const [hh, mm, ss] = (hora || '0:0:0').split(':').map(Number);
  return Math.floor(Date.UTC(y || 1970, (m || 1) - 1, d || 1, hh || 0, mm || 0, ss || 0) / 1000);
}

// Tamaño de contrato aproximado para estimar el P/L de las operaciones manuales
// (mismo cálculo que el backtest: diferencia de precio * lotes * contrato).
function contratoDe(simbolo: string): number {
  const s = (simbolo || '').toUpperCase();
  if (s.startsWith('XAU')) return 100;
  if (s.startsWith('XAG')) return 5000;
  return 100000;
}

function construirHtmlReplay(velas: VelaReplay[], operaciones: { tipo: string; entrada: number; sl: number; tp: number; salida: number; profit: number; hora_apertura: string; hora_cierre: string; motivo_cierre?: 'sl' | 'tp' | 'be'; be_aplicado?: boolean; volumen?: number }[], contrato: number, estadoInicial: string): string {
  const ops = operaciones.map((op) => ({
    tipo: op.tipo,
    entrada: op.entrada,
    salida: op.salida,
    volumen: op.volumen,
    sl: op.sl,
    tp: op.tp,
    profit: op.profit,
    motivoCierre: op.motivo_cierre || (op.be_aplicado && op.salida === op.entrada ? 'be' : op.salida === op.sl ? 'sl' : op.salida === op.tp ? 'tp' : 'cierre'),
    aperturaEpoch: epochUTC(op.hora_apertura),
    cierreEpoch: epochUTC(op.hora_cierre),
  }));
  // El estado manual (dibujos + operaciones tomadas a mano) se guarda en epoch/precio,
  // así sobrevive al cambio de temporalidad (que recarga el iframe).
  const estadoSeguro = (estadoInicial || '{}').replace(/</g, '\\u003c');
  return `<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
<style>
html,body{margin:0;padding:0;width:100%;height:100%;background:#0b0f14;overflow:hidden;font-family:system-ui,Arial,sans-serif;}
#wrap{position:relative;width:100%;height:100%;}
#chart{position:absolute;left:0;top:0;right:0;bottom:0;}
#cv{position:absolute;left:0;top:0;z-index:5;pointer-events:none;}
.panel{position:absolute;z-index:10;background:rgba(21,27,35,.93);border:1px solid #2b3643;border-radius:8px;color:#e1e7ed;font-size:12px;}
#tools{left:6px;top:6px;padding:3px;display:flex;flex-wrap:wrap;gap:3px;max-width:60%;align-items:center;}
.grip{cursor:grab;touch-action:none;user-select:none;-webkit-user-select:none;padding:0 6px;height:28px;display:flex;align-items:center;color:#8893a0;font-size:15px;letter-spacing:-3px;border-radius:5px;}
.grip:active{cursor:grabbing;color:#e4b75a;}
.panel.arrastrando{opacity:.85;box-shadow:0 6px 18px rgba(0,0,0,.5);}
#tools.mini > *:not(.keep){display:none;}
#tools.mini{flex-wrap:nowrap;max-width:none;}
#tools button{min-width:30px;height:28px;padding:0 5px;border:1px solid #33404d;border-radius:5px;background:#1d2530;color:#dce2e8;font-size:13px;cursor:pointer;}
#tools button.on{background:#b88b35;color:#0b0f14;border-color:#b88b35;}
#sep{width:1px;background:#33404d;margin:0 2px;}
#trade{right:68px;top:6px;padding:6px;width:236px;max-height:calc(100% - 90px);overflow-y:auto;}
#trade.mini{width:auto;overflow:visible;}
#tHead{display:flex;align-items:center;gap:4px;}
#tHead .grip{flex:none;}
#trade .fila{display:flex;gap:5px;align-items:center;margin-bottom:5px;}
#trade button{cursor:pointer;border:0;border-radius:5px;color:#fff;font-weight:800;font-size:12px;padding:7px 4px;flex:1;}
#bV{background:#b91c1c;} #bC{background:#15803d;}
#trade input{width:100%;box-sizing:border-box;background:#0e141b;border:1px solid #33404d;border-radius:5px;color:#f4f1e8;padding:5px;font-size:12px;}
#trade label{flex:1;display:flex;flex-direction:column;gap:2px;color:#8893a0;font-size:10px;}
#cuerpo.oculto{display:none;}
#bTog{flex:1;width:auto;background:#1d2530;color:#dce2e8;}
#resumen{color:#8893a0;font-size:11px;margin:4px 0;}
.op{border-top:1px solid #26303b;padding:5px 0;display:flex;flex-wrap:wrap;gap:4px;align-items:center;}
.op small{color:#8893a0;width:100%;font-size:10px;}
.op button{flex:none;padding:3px 6px;background:#303b48;font-size:11px;}
#borrarOps{background:#3a2024;margin-top:4px;width:100%;}
#trade label.chk{flex-direction:row;align-items:center;gap:5px;color:#c9d1d9;font-size:11px;}
#trade .chk input{width:auto;}
#drawer{left:6px;right:68px;bottom:32px;max-height:50%;display:flex;flex-direction:column;}
#dBar{display:flex;align-items:center;gap:8px;padding:4px 6px;font-size:11px;color:#c9d1d9;flex-wrap:wrap;}
#dBar button{border:1px solid #33404d;background:#1d2530;color:#dce2e8;border-radius:5px;font-size:11px;padding:3px 8px;cursor:pointer;}
#dBar button.on{background:#b88b35;color:#0b0f14;border-color:#b88b35;}
#dBody{overflow:auto;border-top:1px solid #26303b;}
#dBody.oculto{display:none;}
#tbl{width:100%;border-collapse:collapse;font-size:11px;}
#tbl th{position:sticky;top:0;background:#151b23;color:#8893a0;text-align:left;padding:3px 6px;font-weight:600;}
#tbl td{padding:3px 6px;border-top:1px solid #1f2933;white-space:nowrap;}
#toast{position:absolute;z-index:20;left:50%;bottom:34px;transform:translateX(-50%);background:#111827;border:1px solid #33404d;color:#f4f1e8;padding:7px 12px;border-radius:6px;font-size:12px;display:none;white-space:nowrap;}
</style>
</head><body>
<div id="wrap">
<div id="chart"></div>
<canvas id="cv"></canvas>
<div id="tools" class="panel">
  <span class="grip keep" id="gTools" title="Arrastra para mover">&#8942;&#8942;</span>
  <button id="tMin" class="keep" title="Minimizar / expandir herramientas">&#8211;</button>
  <button data-m="cursor" title="Cursor / seleccionar">&#10011;</button>
  <button data-m="h" title="Linea horizontal">&#9472;</button>
  <button data-m="tl" title="Linea de tendencia">&#9585;</button>
  <button data-m="ray" title="Rayo">&#8599;</button>
  <button data-m="rect" title="Rectangulo">&#9645;</button>
  <button data-m="fib" title="Fibonacci">Fib</button>
  <button data-m="medir" title="Medir">&#128207;</button>
  <div id="sep"></div>
  <button id="bColor" title="Color">&#9679;</button>
  <button id="bUndo" title="Deshacer ultimo dibujo">&#8630;</button>
  <button id="bDel" title="Borrar el dibujo seleccionado">&#128465;</button>
  <button id="bClear" title="Borrar todos los dibujos">&#10005;</button>
</div>
<div id="trade" class="panel">
  <div id="tHead"><span class="grip" id="gTrade" title="Arrastra para mover">&#8942;&#8942;</span><button id="bTog" title="Minimizar / expandir">&#9662; Operar</button></div>
  <div id="cuerpo">
    <div class="fila" style="margin-top:6px"><button id="bV">VENDER</button><button id="bC">COMPRAR</button></div>
    <div class="fila">
      <label>Lote<input id="iLote" inputmode="decimal" /></label>
      <label>SL (dist.)<input id="iSL" inputmode="decimal" /></label>
      <label>TP (dist.)<input id="iTP" inputmode="decimal" /></label>
    </div>
    <div class="fila"><label class="chk"><input type="checkbox" id="cGraf" /> Poner SL/TP tocando el grafico</label></div>
    <div id="resumen"></div>
    <div id="pos"></div>
    <button id="borrarOps">Borrar operaciones manuales</button>
  </div>
</div>
<div id="drawer" class="panel">
  <div id="dBar">
    <button id="dTog">&#9652; Operaciones</button>
    <button data-tab="ab" class="on">Abiertas</button>
    <button data-tab="hi">Historial</button>
    <span id="dRes"></span>
  </div>
  <div id="dBody" class="oculto"></div>
</div>
<div id="toast"></div>
</div>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
(function () {
  var TODAS_VELAS = ${JSON.stringify(velas)};
  var TODAS_OPS = ${JSON.stringify(ops)};
  var ESTADO_INICIAL = ${estadoSeguro};
  var CONTRATO = ${contrato};
  var V = TODAS_VELAS;
  var N = V.length;

  // ---------- estado manual ----------
  var estado = ESTADO_INICIAL || {};
  if (!estado.dibujos) estado.dibujos = [];
  if (!estado.trades) estado.trades = [];
  if (!estado.sig) estado.sig = 1;
  if (!estado.cfg) estado.cfg = {};
  var cfg = estado.cfg;
  var COLORES = ['#f5c542', '#38bdf8', '#f472b6', '#a78bfa', '#ffffff', '#fb923c'];
  var refPrecio = N ? V[N - 1].close : 1;
  var DEC = refPrecio > 500 ? 2 : (refPrecio > 50 ? 3 : 5);
  function fmt(p) { return Number(p).toFixed(DEC); }
  function sg(x) { return (x >= 0 ? '+' : '') + x.toFixed(2); }
  if (!cfg.lote) cfg.lote = 0.01;
  if (!cfg.color) cfg.color = COLORES[0];
  if (!cfg.sl) cfg.sl = Number((refPrecio * 0.001).toFixed(DEC));
  if (!cfg.tp) cfg.tp = Number((refPrecio * 0.002).toFixed(DEC));

  function guardar() {
    var copia = {
      dibujos: estado.dibujos,
      trades: estado.trades.map(function (t) { var c = {}; for (var k in t) { if (k !== '_res') c[k] = t[k]; } return c; }),
      sig: estado.sig,
      cfg: cfg,
    };
    var s = JSON.stringify(copia);
    try {
      if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'estadoReplay', estado: s }));
      else window.parent.postMessage({ type: 'estadoReplay', estado: s }, '*');
    } catch (e) {}
  }

  // ---------- grafico ----------
  var chart = LightweightCharts.createChart(document.getElementById('chart'), {
    layout: { background: { color: '#0b0f14' }, textColor: '#d1d8e0' },
    grid: { vertLines: { color: '#1a2129' }, horzLines: { color: '#1a2129' } },
    timeScale: { timeVisible: true, secondsVisible: false },
    width: window.innerWidth,
    height: window.innerHeight,
  });
  var serie = chart.addCandlestickSeries({
    upColor: '#22c55e', downColor: '#ef4444', borderVisible: false,
    wickUpColor: '#22c55e', wickDownColor: '#ef4444',
  });
  var lineasOperacion = [];
  var lineasManual = [];

  function limpiarLineasOperacion() {
    for (var k = 0; k < lineasOperacion.length; k++) serie.removePriceLine(lineasOperacion[k]);
    lineasOperacion = [];
  }
  function linea(precio, color, texto, estilo) {
    if (!precio) return;
    lineasOperacion.push(serie.createPriceLine({ price: precio, color: color, lineWidth: 2, lineStyle: estilo || 0, axisLabelVisible: true, title: texto }));
  }
  function lineaManual(precio, color, texto, estilo) {
    if (!precio) return;
    lineasManual.push(serie.createPriceLine({ price: precio, color: color, lineWidth: 1, lineStyle: estilo || 0, axisLabelVisible: true, title: texto }));
  }

  // ---------- tiempo <-> indice logico (permite dibujar hacia el futuro) ----------
  function idxHasta(t) {
    var lo = 0, hi = N - 1, r = -1;
    while (lo <= hi) {
      var m = (lo + hi) >> 1;
      if (V[m].time <= t) { r = m; lo = m + 1; } else { hi = m - 1; }
    }
    return r;
  }
  var paso = (function () {
    var d = [];
    var lim = Math.min(N - 1, 300);
    for (var i = 0; i < lim; i++) { var x = V[i + 1].time - V[i].time; if (x > 0) d.push(x); }
    d.sort(function (a, b) { return a - b; });
    return d.length ? d[d.length >> 1] : 60;
  })();
  function tToL(t) {
    if (!N) return 0;
    var i = idxHasta(t);
    if (i < 0) return (t - V[0].time) / paso;
    if (i >= N - 1) return (N - 1) + (t - V[N - 1].time) / paso;
    return i + (t - V[i].time) / (V[i + 1].time - V[i].time);
  }
  function lToT(l) {
    if (!N) return 0;
    if (l <= 0) return V[0].time + l * paso;
    if (l >= N - 1) return V[N - 1].time + (l - (N - 1)) * paso;
    var i = Math.floor(l);
    return V[i].time + (l - i) * (V[i + 1].time - V[i].time);
  }
  function X(t) { return chart.timeScale().logicalToCoordinate(tToL(t)); }
  function Y(p) { return serie.priceToCoordinate(p); }
  function pt(o) { var x = X(o.t), y = Y(o.p); if (x === null || y === null) return null; return { x: x, y: y }; }
  function puntoDe(x, y) {
    var l = chart.timeScale().coordinateToLogical(x);
    var p = serie.coordinateToPrice(y);
    if (l === null || p === null) return null;
    return { t: Math.round(lToT(l)), p: p };
  }

  // ---------- operaciones manuales ----------
  function nivelesEn(tr, t) {
    var h = tr.hist, r = h[0];
    for (var i = 1; i < h.length; i++) { if (h[i].t <= t) r = h[i]; }
    return r;
  }
  function resolver(tr) {
    if (tr._res !== undefined) return tr._res;
    if (tr.fijo) {
      tr._res = { idx: Math.max(idxHasta(tr.t), idxHasta(tr.fijo.t)), precio: tr.fijo.precio, motivo: tr.fijo.motivo };
      return tr._res;
    }
    var i0 = idxHasta(tr.t);
    var res = null;
    if (i0 < 0) { tr._res = null; return null; }
    var dir = tr.tipo === 'compra' ? 1 : -1;
    var hi = 0;
    for (var i = i0 + 1; i < N; i++) {
      var v = V[i];
      if (tr.cierre && v.time > tr.cierre.t) break;
      while (hi + 1 < tr.hist.length && tr.hist[hi + 1].t < v.time) hi++;
      var lv = tr.hist[hi];
      if (dir === 1) {
        if (lv.sl && v.low <= lv.sl) { res = { idx: i, precio: Math.min(lv.sl, v.open), motivo: 'sl' }; break; }
        if (lv.tp && v.high >= lv.tp) { res = { idx: i, precio: lv.tp, motivo: 'tp' }; break; }
      } else {
        if (lv.sl && v.high >= lv.sl) { res = { idx: i, precio: Math.max(lv.sl, v.open), motivo: 'sl' }; break; }
        if (lv.tp && v.low <= lv.tp) { res = { idx: i, precio: lv.tp, motivo: 'tp' }; break; }
      }
    }
    if (!res && tr.cierre) res = { idx: Math.max(i0, idxHasta(tr.cierre.t)), precio: tr.cierre.precio, motivo: 'manual' };
    tr._res = res;
    return res;
  }
  function plDe(tr, precio) {
    var dir = tr.tipo === 'compra' ? 1 : -1;
    return (precio - tr.entrada) * dir * tr.lote * CONTRATO;
  }
  function estadoTrade(tr, n) {
    var i0 = idxHasta(tr.t);
    if (n < 1 || i0 < 0 || n - 1 < i0) return { e: 'futuro', pl: 0 };
    var res = resolver(tr);
    if (res && res.idx <= n - 1) return { e: 'cerrada', res: res, pl: tr.fijo ? tr.fijo.profit : plDe(tr, res.precio) };
    return { e: 'abierta', pl: plDe(tr, V[n - 1].close) };
  }

  var botTrades = TODAS_OPS.map(function (op, i) {
    return {
      id: 'B' + (i + 1), origen: 'bot', tipo: op.tipo, t: op.aperturaEpoch, entrada: op.entrada,
      lote: op.volumen > 0 ? op.volumen : 0.01,
      hist: [{ t: op.aperturaEpoch, sl: op.sl, tp: op.tp }],
      fijo: { t: op.cierreEpoch, precio: op.salida, motivo: op.motivoCierre, profit: op.profit },
    };
  });
  function todas() { return botTrades.concat(estado.trades); }
  function esBot(tr) { return tr.origen === 'bot'; }
  function dosd(x) { return (x < 10 ? '0' : '') + x; }
  function hora(t) {
    var d = new Date(t * 1000);
    return d.getUTCFullYear() + '.' + dosd(d.getUTCMonth() + 1) + '.' + dosd(d.getUTCDate()) + ' ' + dosd(d.getUTCHours()) + ':' + dosd(d.getUTCMinutes());
  }

  var toastTimer = null;
  function toast(msg) {
    var el = document.getElementById('toast');
    el.textContent = msg;
    el.style.display = 'block';
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.style.display = 'none'; }, 2600);
  }

  function leerCfg() {
    var l = parseFloat(String(document.getElementById('iLote').value).replace(',', '.'));
    var s = parseFloat(String(document.getElementById('iSL').value).replace(',', '.'));
    var t = parseFloat(String(document.getElementById('iTP').value).replace(',', '.'));
    if (l > 0) cfg.lote = l;
    cfg.sl = s > 0 ? s : 0;
    cfg.tp = t > 0 ? t : 0;
    cfg.graf = document.getElementById('cGraf').checked;
  }

  function operar(tipo) {
    var n = window.indiceActual || 0;
    if (!n) return;
    leerCfg();
    var v = V[n - 1];
    var dir = tipo === 'compra' ? 1 : -1;
    var entrada = v.close;
    var sl = !cfg.graf && cfg.sl > 0 ? Number((entrada - dir * cfg.sl).toFixed(DEC)) : 0;
    var tp = !cfg.graf && cfg.tp > 0 ? Number((entrada + dir * cfg.tp).toFixed(DEC)) : 0;
    var tr = { id: estado.sig++, origen: 'manual', tipo: tipo, t: v.time, entrada: entrada, lote: cfg.lote, hist: [{ t: v.time, sl: sl, tp: tp }] };
    estado.trades.push(tr);
    guardar();
    refrescar(null);
    if (cfg.graf) {
      ajuste = { id: tr.id, que: 'sl', cadena: true };
      setModo('ajuste');
      toast((tipo === 'compra' ? 'COMPRA' : 'VENTA') + ' abierta. Toca el grafico donde va el SL (toca el cursor para cancelar)');
    } else {
      toast((tipo === 'compra' ? 'COMPRA' : 'VENTA') + ' ' + cfg.lote + ' @ ' + fmt(entrada));
    }
  }

  function cerrarManual(id) {
    var n = window.indiceActual || 0;
    for (var i = 0; i < estado.trades.length; i++) {
      var tr = estado.trades[i];
      if (tr.id === id && n) {
        tr.cierre = { t: V[n - 1].time, precio: V[n - 1].close };
        tr._res = undefined;
        guardar();
        refrescar(null);
        toast('Cerrada ' + sg(plDe(tr, tr.cierre.precio)));
      }
    }
  }

  var ajuste = null;
  function aplicarAjuste(precio) {
    var n = window.indiceActual || 0;
    if (!n || !ajuste) return;
    var tr = null;
    for (var i = 0; i < estado.trades.length; i++) { if (estado.trades[i].id === ajuste.id) tr = estado.trades[i]; }
    if (!tr) { setModo('cursor'); return; }
    var cierre = V[n - 1].close;
    var dir = tr.tipo === 'compra' ? 1 : -1;
    var p = Number(precio.toFixed(DEC));
    if (ajuste.que === 'sl' && (p - cierre) * dir >= 0) { toast('El SL tiene que quedar del lado de la perdida'); return; }
    if (ajuste.que === 'tp' && (p - cierre) * dir <= 0) { toast('El TP tiene que quedar del lado de la ganancia'); return; }
    var lv = nivelesEn(tr, V[n - 1].time);
    var nuevo = { t: V[n - 1].time, sl: lv.sl, tp: lv.tp };
    nuevo[ajuste.que] = p;
    var ult = tr.hist[tr.hist.length - 1];
    if (ult.t === nuevo.t) tr.hist[tr.hist.length - 1] = nuevo; else tr.hist.push(nuevo);
    tr._res = undefined;
    var q = ajuste.que, cad = ajuste.cadena;
    guardar();
    refrescar(null);
    if (q === 'sl' && cad) {
      ajuste = { id: tr.id, que: 'tp', cadena: false };
      setModo('ajuste');
      toast('SL en ' + fmt(p) + '. Ahora toca donde va el TP');
    } else {
      ajuste = null;
      setModo('cursor');
      toast(q.toUpperCase() + ' en ' + fmt(p));
    }
  }

  var cerradasPrev = {};
  function marcadoresManuales(n) {
    var m = [];
    if (!n) return m;
    var tAct = V[n - 1].time;
    for (var i = 0; i < estado.trades.length; i++) {
      var tr = estado.trades[i];
      var i0 = idxHasta(tr.t);
      if (i0 < 0 || V[i0].time > tAct) continue;
      m.push({ time: V[i0].time, position: tr.tipo === 'compra' ? 'belowBar' : 'aboveBar', color: '#60a5fa', shape: tr.tipo === 'compra' ? 'arrowUp' : 'arrowDown', text: 'YO ' + (tr.tipo === 'compra' ? 'C' : 'V') });
      var est = estadoTrade(tr, n);
      if (est.e === 'cerrada') {
        m.push({ time: V[est.res.idx].time, position: tr.tipo === 'compra' ? 'aboveBar' : 'belowBar', color: est.pl >= 0 ? '#22c55e' : '#ef4444', shape: 'circle', text: est.res.motivo.toUpperCase() + ' ' + sg(est.pl) });
      }
    }
    return m;
  }

  function marcadoresHasta(i) {
    if (!i) return [];
    var tiempoActual = TODAS_VELAS[i - 1].time;
    var marcadores = [];
    for (var j = 0; j < TODAS_OPS.length; j++) {
      var op = TODAS_OPS[j];
      var tiempoEntrada = tiempoDeVelaAnterior(op.aperturaEpoch);
      var tiempoSalida = tiempoDeVelaAnterior(op.cierreEpoch);
      if (tiempoEntrada !== null && tiempoEntrada <= tiempoActual) {
        marcadores.push({ time: tiempoEntrada, position: 'belowBar', color: op.tipo === 'compra' ? '#22c55e' : '#ef4444', shape: op.tipo === 'compra' ? 'arrowUp' : 'arrowDown', text: op.tipo === 'compra' ? 'COMPRA' : 'VENTA' });
      }
      if (tiempoSalida !== null && tiempoSalida <= tiempoActual) {
        var colorCierre = op.motivoCierre === 'tp' || (op.motivoCierre === 'be' && op.profit >= 0) ? '#22c55e' : '#ef4444';
        marcadores.push({ time: tiempoSalida, position: 'aboveBar', color: colorCierre, shape: 'circle', text: op.motivoCierre.toUpperCase() + ' ' + (op.profit >= 0 ? '+' : '') + op.profit.toFixed(2) });
      }
    }
    return marcadores;
  }

  function tiempoDeVelaAnterior(epoch) {
    var r = idxHasta(epoch);
    return r < 0 ? null : V[r].time;
  }

  var drawerAbierto = false, tabDrawer = 'ab', ultAb = [], ultHi = [];
  function celdaPl(x) { return '<td style="color:' + (x >= 0 ? '#4ade80' : '#f87171') + ';font-weight:700">' + sg(x) + '</td>'; }
  function pintarTabla() {
    var body = document.getElementById('dBody');
    body.className = drawerAbierto ? '' : 'oculto';
    if (!drawerAbierto) return;
    var n = window.indiceActual || 0;
    var h = '';
    if (tabDrawer === 'ab') {
      h = '<table id="tbl"><tr><th>Origen</th><th>Tipo</th><th>Lote</th><th>Apertura</th><th>Entrada</th><th>S/L</th><th>T/P</th><th>Precio</th><th>Beneficio</th></tr>';
      for (var i = 0; i < ultAb.length; i++) {
        var tr = ultAb[i].tr, lv = nivelesEn(tr, V[n - 1].time);
        h += '<tr><td>' + (esBot(tr) ? 'Bot' : 'Manual') + '</td><td style="color:' + (tr.tipo === 'compra' ? '#4ade80' : '#f87171') + '">' + (tr.tipo === 'compra' ? 'buy' : 'sell') + '</td><td>' + tr.lote + '</td><td>' + hora(tr.t) + '</td><td>' + fmt(tr.entrada) + '</td><td>' + (lv.sl ? fmt(lv.sl) : '-') + '</td><td>' + (lv.tp ? fmt(lv.tp) : '-') + '</td><td>' + fmt(V[n - 1].close) + '</td>' + celdaPl(ultAb[i].est.pl) + '</tr>';
      }
      if (!ultAb.length) h += '<tr><td colspan="9" style="color:#8893a0">Sin operaciones abiertas</td></tr>';
    } else {
      ultHi.sort(function (a, b) { return b.est.res.idx - a.est.res.idx; });
      h = '<table id="tbl"><tr><th>Origen</th><th>Tipo</th><th>Lote</th><th>Apertura</th><th>Entrada</th><th>Salida</th><th>Motivo</th><th>Beneficio</th></tr>';
      var lim = Math.min(ultHi.length, 40);
      for (var j = 0; j < lim; j++) {
        var t2 = ultHi[j].tr, e2 = ultHi[j].est;
        h += '<tr><td>' + (esBot(t2) ? 'Bot' : 'Manual') + '</td><td style="color:' + (t2.tipo === 'compra' ? '#4ade80' : '#f87171') + '">' + (t2.tipo === 'compra' ? 'buy' : 'sell') + '</td><td>' + t2.lote + '</td><td>' + hora(t2.t) + '</td><td>' + fmt(t2.entrada) + '</td><td>' + fmt(e2.res.precio) + '</td><td>' + e2.res.motivo.toUpperCase() + '</td>' + celdaPl(e2.pl) + '</tr>';
      }
      if (!ultHi.length) h += '<tr><td colspan="8" style="color:#8893a0">Todavia no hay operaciones cerradas</td></tr>';
      if (ultHi.length > lim) h += '<tr><td colspan="8" style="color:#8893a0">... y ' + (ultHi.length - lim) + ' mas antiguas</td></tr>';
    }
    body.innerHTML = h + '</table>';
  }

  // Panel de posiciones + lineas de precio + marcadores + tabla. previo != null => avance de replay (permite avisos TP/SL).
  function refrescar(previo) {
    var n = window.indiceActual || 0;
    for (var k = 0; k < lineasManual.length; k++) serie.removePriceLine(lineasManual[k]);
    lineasManual = [];
    var html = '';
    var cT = 0, nT = 0, fT = 0, cM = 0, gM = 0, nM = 0, fM = 0;
    var abiertas = [], hist = [];
    var lista = todas();
    for (var i = 0; i < lista.length; i++) {
      var tr = lista[i];
      var est = estadoTrade(tr, n);
      var man = !esBot(tr);
      if (est.e === 'cerrada') {
        cT++; nT += est.pl; hist.push({ tr: tr, est: est });
        if (man) { cM++; nM += est.pl; if (est.pl > 0) gM++; }
        if (previo !== null && n > previo && n - previo <= 50 && !cerradasPrev[tr.id]) {
          toast((man ? '' : 'BOT ') + est.res.motivo.toUpperCase() + ' alcanzado ' + sg(est.pl));
        }
      }
      cerradasPrev[tr.id] = est.e === 'cerrada';
      if (est.e !== 'abierta') continue;
      fT += est.pl; if (man) fM += est.pl;
      abiertas.push({ tr: tr, est: est });
      var lv = nivelesEn(tr, V[n - 1].time);
      var pre = man ? '' : 'BOT ';
      lineaManual(tr.entrada, '#3b82f6', pre + (tr.tipo === 'compra' ? 'BUY' : 'SELL'), 0);
      lineaManual(lv.sl, '#ef4444', pre + 'SL', 2);
      lineaManual(lv.tp, '#22c55e', pre + 'TP', 2);
      if (man) {
        html += '<div class="op"><b style="color:' + (tr.tipo === 'compra' ? '#4ade80' : '#f87171') + '">' + (tr.tipo === 'compra' ? 'COMPRA' : 'VENTA') + '</b> ' + tr.lote +
          ' <b style="color:' + (est.pl >= 0 ? '#4ade80' : '#f87171') + '">' + sg(est.pl) + '</b>' +
          '<button data-a="sl" data-id="' + tr.id + '">SL</button><button data-a="tp" data-id="' + tr.id + '">TP</button><button data-a="x" data-id="' + tr.id + '">Cerrar</button>' +
          '<small>Entrada ' + fmt(tr.entrada) + ' | SL ' + (lv.sl ? fmt(lv.sl) : '-') + ' | TP ' + (lv.tp ? fmt(lv.tp) : '-') + '</small></div>';
      }
    }
    ultAb = abiertas; ultHi = hist;
    document.getElementById('pos').innerHTML = html;
    document.getElementById('resumen').textContent = 'Manual: cerradas ' + cM + (cM ? ' (' + gM + ' ganadas)' : '') + ' | Neto ' + sg(nM) + ' | Flotante ' + sg(fM);
    document.getElementById('dRes').textContent = abiertas.length + ' abiertas | Flotante ' + sg(fT) + ' | Cerradas ' + cT + ' | Neto ' + sg(nT);
    pintarTabla();
    var marc = marcadoresHasta(n).concat(marcadoresManuales(n));
    marc.sort(function (a, b) { return a.time - b.time; });
    serie.setMarkers(marc);
  }

  // ---------- dibujos ----------
  var modo = 'cursor';
  var pendiente = null;
  var cursorPt = null;
  var seleccion = null;
  var NIVELES_FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

  function setModo(m) {
    modo = m;
    pendiente = null;
    var bs = document.querySelectorAll('#tools button[data-m]');
    for (var i = 0; i < bs.length; i++) {
      if (bs[i].getAttribute('data-m') === m) bs[i].className = 'on'; else bs[i].className = '';
    }
  }
  function agregar(d) {
    d.id = estado.sig++;
    d.color = cfg.color;
    if (d.tipo === 'medir') {
      estado.dibujos = estado.dibujos.filter(function (x) { return x.tipo !== 'medir'; });
    }
    estado.dibujos.push(d);
    seleccion = d.id;
    guardar();
  }
  function rgba(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }
  function puntaRayo(a, b) {
    var dx = b.x - a.x;
    if (Math.abs(dx) < 0.5) return { x: a.x, y: b.y > a.y ? 100000 : -100000 };
    var xf = dx > 0 ? 100000 : -100000;
    var k = (xf - a.x) / dx;
    return { x: xf, y: a.y + k * (b.y - a.y) };
  }
  function dist(px, py, ax, ay, bx, by) {
    var dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    var t = l2 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0;
    t = Math.max(0, Math.min(1, t));
    var cx = ax + t * dx, cy = ay + t * dy;
    return Math.sqrt((px - cx) * (px - cx) + (py - cy) * (py - cy));
  }
  function golpe(x, y) {
    for (var i = estado.dibujos.length - 1; i >= 0; i--) {
      var d = estado.dibujos[i];
      var a = pt(d.p1);
      var b = d.p2 ? pt(d.p2) : null;
      if (!a) continue;
      if (d.tipo === 'h') { if (Math.abs(y - a.y) < 10) return d.id; continue; }
      if (!b) continue;
      if (d.tipo === 'tl') { if (dist(x, y, a.x, a.y, b.x, b.y) < 10) return d.id; }
      else if (d.tipo === 'ray') { var f = puntaRayo(a, b); if (dist(x, y, a.x, a.y, f.x, f.y) < 10) return d.id; }
      else if (d.tipo === 'rect' || d.tipo === 'medir') {
        if (x >= Math.min(a.x, b.x) - 6 && x <= Math.max(a.x, b.x) + 6 && y >= Math.min(a.y, b.y) - 6 && y <= Math.max(a.y, b.y) + 6) return d.id;
      } else if (d.tipo === 'fib') {
        var x1 = Math.min(a.x, b.x) - 6, x2 = Math.max(a.x, b.x) + 6;
        if (x >= x1 && x <= x2) {
          for (var j = 0; j < NIVELES_FIB.length; j++) {
            var yy = Y(d.p2.p + (d.p1.p - d.p2.p) * NIVELES_FIB[j]);
            if (yy !== null && Math.abs(y - yy) < 8) return d.id;
          }
        }
      }
    }
    return null;
  }

  var cv = document.getElementById('cv');
  var ctx = cv.getContext('2d');
  var anchoEscala = 60, altoEscala = 28;
  function medidas() {
    try { var w = chart.priceScale('right').width(); if (w > 0) anchoEscala = w; } catch (e) {}
    try { var h = chart.timeScale().height(); if (h > 0) altoEscala = h; } catch (e) {}
  }
  function ajustarCanvas() {
    var dpr = window.devicePixelRatio || 1;
    cv.width = Math.floor(window.innerWidth * dpr);
    cv.height = Math.floor(window.innerHeight * dpr);
    cv.style.width = window.innerWidth + 'px';
    cv.style.height = window.innerHeight + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function trazo(ax, ay, bx, by, color, ancho, dash) {
    ctx.beginPath();
    ctx.setLineDash(dash || []);
    ctx.strokeStyle = color;
    ctx.lineWidth = ancho;
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  function etiqueta(txt, x, y, color, fondo, alinea) {
    ctx.font = '11px system-ui,Arial';
    var w = ctx.measureText(txt).width + 8;
    var xx = alinea === 'right' ? x - w : (alinea === 'center' ? x - w / 2 : x);
    ctx.fillStyle = fondo;
    ctx.fillRect(xx, y - 8, w, 16);
    ctx.fillStyle = color;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.fillText(txt, xx + 4, y);
  }
  function punto(x, y, c) {
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#0b0f14';
    ctx.fill();
    ctx.strokeStyle = c;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  function dibujarUno(d, sel, pw) {
    var a = pt(d.p1);
    if (!a) return;
    var b = d.p2 ? pt(d.p2) : null;
    var c = d.color || '#f5c542';
    var ancho = sel ? 3 : 1.6;
    if (d.tipo === 'h') {
      trazo(0, a.y, pw, a.y, c, ancho);
      etiqueta(fmt(d.p1.p), pw - 2, a.y - 10, '#0b0f14', c, 'right');
      if (sel) punto(Math.min(pw - 20, Math.max(20, a.x)), a.y, c);
      return;
    }
    if (!b) return;
    if (d.tipo === 'tl') {
      trazo(a.x, a.y, b.x, b.y, c, ancho);
    } else if (d.tipo === 'ray') {
      var f = puntaRayo(a, b);
      trazo(a.x, a.y, f.x, f.y, c, ancho);
    } else if (d.tipo === 'rect') {
      ctx.fillStyle = rgba(c, 0.15);
      ctx.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
      ctx.strokeStyle = c;
      ctx.lineWidth = ancho;
      ctx.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
    } else if (d.tipo === 'fib') {
      var x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x);
      if (x2 - x1 < 40) x2 = x1 + 40;
      trazo(a.x, a.y, b.x, b.y, c, 1, [4, 4]);
      var prev = null;
      for (var i = 0; i < NIVELES_FIB.length; i++) {
        var pr = d.p2.p + (d.p1.p - d.p2.p) * NIVELES_FIB[i];
        var yy = Y(pr);
        if (yy === null) continue;
        if (prev !== null) {
          ctx.fillStyle = rgba(c, i % 2 ? 0.05 : 0.09);
          ctx.fillRect(x1, Math.min(prev, yy), x2 - x1, Math.abs(yy - prev));
        }
        trazo(x1, yy, x2, yy, c, sel ? 2 : 1);
        etiqueta((NIVELES_FIB[i] * 100).toFixed(1) + '%  ' + fmt(pr), x1 + 2, yy - 9, '#0b0f14', c, 'left');
        prev = yy;
      }
    } else if (d.tipo === 'medir') {
      var sube = d.p2.p >= d.p1.p;
      var col = sube ? '#22c55e' : '#ef4444';
      ctx.fillStyle = rgba(col, 0.15);
      ctx.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
      trazo(a.x, a.y, b.x, b.y, col, 1.5, [4, 3]);
      var diff = d.p2.p - d.p1.p;
      var pct = diff / d.p1.p * 100;
      var nv = Math.round(tToL(d.p2.t) - tToL(d.p1.t));
      etiqueta((diff >= 0 ? '+' : '') + fmt(diff) + '  (' + pct.toFixed(2) + '%)  ' + Math.abs(nv) + ' velas', (a.x + b.x) / 2, sube ? Math.min(a.y, b.y) - 14 : Math.max(a.y, b.y) + 14, '#ffffff', col, 'center');
    }
    if (sel) { punto(a.x, a.y, c); punto(b.x, b.y, c); }
  }

  function dibujarTrades(pw) {
    var n = window.indiceActual || 0;
    if (!n) return;
    var tAct = V[n - 1].time;
    var xAct = X(tAct);
    var rg = chart.timeScale().getVisibleLogicalRange();
    var lista = todas();
    for (var i = 0; i < lista.length; i++) {
      var tr = lista[i];
      var est = estadoTrade(tr, n);
      if (est.e === 'futuro') continue;
      var i0 = idxHasta(tr.t);
      if (i0 < 0) continue;
      var cerrada = est.e === 'cerrada';
      var iFin = cerrada ? est.res.idx : n - 1;
      if (rg && (iFin < rg.from - 2 || i0 > rg.to + 2)) continue;
      var xe = X(V[i0].time);
      var xf = cerrada ? X(V[est.res.idx].time) : xAct;
      if (xe === null || xf === null) continue;
      if (!cerrada) xf += 30;
      var lv = nivelesEn(tr, cerrada ? V[est.res.idx].time : tAct);
      var ye = Y(tr.entrada);
      if (ye === null) continue;
      var ys = lv.sl ? Y(lv.sl) : null;
      var yt = lv.tp ? Y(lv.tp) : null;
      var alfa = cerrada ? 0.07 : 0.17;
      if (yt !== null) { ctx.fillStyle = rgba('#22c55e', alfa); ctx.fillRect(xe, Math.min(ye, yt), xf - xe, Math.abs(yt - ye)); }
      if (ys !== null) { ctx.fillStyle = rgba('#ef4444', alfa); ctx.fillRect(xe, Math.min(ye, ys), xf - xe, Math.abs(ys - ye)); }
      var pre = esBot(tr) ? 'BOT ' : '';
      if (cerrada) {
        trazo(xe, ye, xf, ye, '#3b82f6', 1);
        etiqueta(pre + est.res.motivo.toUpperCase() + ' ' + sg(est.pl), xf, ye - 12, '#ffffff', est.pl >= 0 ? '#15803d' : '#b91c1c', 'right');
      } else {
        // etiquetas al estilo MT5, pegadas a la izquierda, con el beneficio en vivo
        etiqueta(pre + (tr.tipo === 'compra' ? 'BUY ' : 'SELL ') + tr.lote + ' @ ' + fmt(tr.entrada) + '   ' + sg(est.pl), 6, ye, '#ffffff', est.pl >= 0 ? '#15803d' : '#b91c1c', 'left');
        if (lv.sl && ys !== null) etiqueta('SL ' + fmt(lv.sl) + '   ' + sg(plDe(tr, lv.sl)), 6, ys, '#ffffff', '#7f1d1d', 'left');
        if (lv.tp && yt !== null) etiqueta('TP ' + fmt(lv.tp) + '   ' + sg(plDe(tr, lv.tp)), 6, yt, '#ffffff', '#14532d', 'left');
      }
    }
  }

  function dibujar() {
    medidas();
    var W = window.innerWidth, H = window.innerHeight;
    var pw = W - anchoEscala, ph = H - altoEscala;
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, pw, ph);
    ctx.clip();
    dibujarTrades(pw);
    for (var i = 0; i < estado.dibujos.length; i++) dibujarUno(estado.dibujos[i], estado.dibujos[i].id === seleccion, pw);
    if (modo !== 'cursor' && modo !== 'ajuste' && pendiente && cursorPt) {
      dibujarUno({ tipo: modo, p1: pendiente, p2: cursorPt, color: cfg.color }, false, pw);
    }
    if (modo === 'ajuste' && cursorPt) {
      var yc = Y(cursorPt.p);
      if (yc !== null) { trazo(0, yc, pw, yc, ajuste && ajuste.que === 'sl' ? '#ef4444' : '#22c55e', 1, [5, 4]); etiqueta(fmt(cursorPt.p), pw - 2, yc - 10, '#fff', '#334155', 'right'); }
    }
    ctx.restore();
    requestAnimationFrame(dibujar);
  }

  // ---------- interaccion (clicks del grafico: pan y zoom siguen funcionando) ----------
  // Los toques se detectan a mano: el grafico descarta un 2do clic que llegue en menos de ~0.5 s
  // (lo toma como doble clic), y para dibujar con 2 puntos eso pasaria seguido en el celular.
  function alTocar(x, y) {
    var W = window.innerWidth, H = window.innerHeight;
    if (x > W - anchoEscala || y > H - altoEscala) return;
    var p = puntoDe(x, y);
    if (!p) return;
    if (modo === 'cursor') { seleccion = golpe(x, y); return; }
    if (modo === 'ajuste') { aplicarAjuste(p.p); return; }
    if (modo === 'h') { agregar({ tipo: 'h', p1: p }); setModo('cursor'); return; }
    if (!pendiente) { pendiente = p; cursorPt = p; return; }
    agregar({ tipo: modo, p1: pendiente, p2: p });
    setModo('cursor');
  }
  var elChart = document.getElementById('chart');
  var tapIni = null;
  elChart.addEventListener('pointerdown', function (e) {
    var r = elChart.getBoundingClientRect();
    tapIni = { x: e.clientX - r.left, y: e.clientY - r.top, t: Date.now() };
  }, true);
  elChart.addEventListener('pointerup', function (e) {
    if (!tapIni) return;
    var r = elChart.getBoundingClientRect();
    var x = e.clientX - r.left, y = e.clientY - r.top;
    var dx = x - tapIni.x, dy = y - tapIni.y, dt = Date.now() - tapIni.t;
    tapIni = null;
    if (dx * dx + dy * dy > 64 || dt > 600) return;
    alTocar(x, y);
  }, true);
  chart.subscribeCrosshairMove(function (param) {
    if (param && param.point) cursorPt = puntoDe(param.point.x, param.point.y);
  });

  var bsTool = document.querySelectorAll('#tools button[data-m]');
  for (var bi = 0; bi < bsTool.length; bi++) {
    bsTool[bi].addEventListener('click', function (e) {
      var m = e.currentTarget.getAttribute('data-m');
      if (modo === 'ajuste') ajuste = null;
      setModo(modo === m && m !== 'cursor' ? 'cursor' : m);
    });
  }
  var bColor = document.getElementById('bColor');
  function pintarColor() { bColor.style.color = cfg.color; }
  bColor.addEventListener('click', function () {
    var i = COLORES.indexOf(cfg.color);
    cfg.color = COLORES[(i + 1) % COLORES.length];
    if (seleccion !== null) {
      for (var k = 0; k < estado.dibujos.length; k++) { if (estado.dibujos[k].id === seleccion) estado.dibujos[k].color = cfg.color; }
    }
    pintarColor();
    guardar();
  });
  document.getElementById('bUndo').addEventListener('click', function () { estado.dibujos.pop(); seleccion = null; guardar(); });
  function borrarSeleccion() {
    if (seleccion === null) { toast('Toca un dibujo (con el cursor) para seleccionarlo'); return; }
    estado.dibujos = estado.dibujos.filter(function (d) { return d.id !== seleccion; });
    seleccion = null;
    guardar();
  }
  document.getElementById('bDel').addEventListener('click', borrarSeleccion);
  var limpiarTimer = null;
  var bClear = document.getElementById('bClear');
  bClear.addEventListener('click', function () {
    if (!limpiarTimer) {
      toast('Toca otra vez para borrar TODOS los dibujos');
      limpiarTimer = setTimeout(function () { limpiarTimer = null; }, 2500);
      return;
    }
    clearTimeout(limpiarTimer); limpiarTimer = null;
    estado.dibujos = []; seleccion = null; guardar();
  });
  window.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { ajuste = null; setModo('cursor'); }
    if ((e.key === 'Delete' || e.key === 'Backspace') && document.activeElement && document.activeElement.tagName !== 'INPUT') borrarSeleccion();
  });

  document.getElementById('bC').addEventListener('click', function () { operar('compra'); });
  document.getElementById('bV').addEventListener('click', function () { operar('venta'); });
  var iLote = document.getElementById('iLote'), iSL = document.getElementById('iSL'), iTP = document.getElementById('iTP');
  if (cfg.graf === undefined) cfg.graf = true;
  document.getElementById('cGraf').checked = !!cfg.graf;
  iLote.value = cfg.lote; iSL.value = cfg.sl; iTP.value = cfg.tp;
  [iLote, iSL, iTP, document.getElementById('cGraf')].forEach(function (el) { el.addEventListener('change', function () { leerCfg(); guardar(); }); });
  document.getElementById('pos').addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('button') : null;
    if (!b) return;
    var id = Number(b.getAttribute('data-id'));
    var a = b.getAttribute('data-a');
    if (a === 'x') cerrarManual(id);
    else { ajuste = { id: id, que: a }; setModo('ajuste'); toast('Toca el grafico donde quieres el ' + a.toUpperCase() + ' (Esc cancela)'); }
  });
  var borrarTimer = null;
  document.getElementById('borrarOps').addEventListener('click', function () {
    if (!borrarTimer) {
      toast('Toca otra vez para borrar todas las operaciones manuales');
      borrarTimer = setTimeout(function () { borrarTimer = null; }, 2500);
      return;
    }
    clearTimeout(borrarTimer); borrarTimer = null;
    estado.trades = []; cerradasPrev = {}; guardar(); refrescar(null);
  });
  document.getElementById('dTog').addEventListener('click', function () {
    drawerAbierto = !drawerAbierto;
    document.getElementById('dTog').innerHTML = (drawerAbierto ? '&#9662; ' : '&#9652; ') + 'Operaciones';
    pintarTabla();
  });
  var tabsBtn = document.querySelectorAll('#dBar button[data-tab]');
  for (var ti = 0; ti < tabsBtn.length; ti++) {
    tabsBtn[ti].addEventListener('click', function (e) {
      tabDrawer = e.currentTarget.getAttribute('data-tab');
      for (var q = 0; q < tabsBtn.length; q++) tabsBtn[q].className = tabsBtn[q] === e.currentTarget ? 'on' : '';
      drawerAbierto = true;
      document.getElementById('dTog').innerHTML = '&#9662; Operaciones';
      pintarTabla();
    });
  }
  if (!cfg.pos) cfg.pos = {};
  var cuerpoAbierto = cfg.tradeAbierto === undefined ? window.innerWidth > 700 : !!cfg.tradeAbierto;
  var panelTrade = document.getElementById('trade');
  var panelTools = document.getElementById('tools');
  function pintarPanel() {
    document.getElementById('cuerpo').className = cuerpoAbierto ? '' : 'oculto';
    document.getElementById('bTog').innerHTML = (cuerpoAbierto ? '&#9662; ' : '&#9656; ') + 'Operar';
    panelTrade.className = 'panel' + (cuerpoAbierto ? '' : ' mini');
    cfg.tradeAbierto = cuerpoAbierto;
  }
  document.getElementById('bTog').addEventListener('click', function () { cuerpoAbierto = !cuerpoAbierto; pintarPanel(); ponerEnLimites(panelTrade); guardar(); });
  pintarPanel();

  // ---------- paneles flotantes: arrastrables (mouse y tactil) y minimizables ----------
  function ponerEnLimites(el) {
    if (!el || !el.style.left) return;
    var W = window.innerWidth, H = window.innerHeight;
    var l = parseFloat(el.style.left) || 0, t = parseFloat(el.style.top) || 0;
    l = Math.max(0, Math.min(l, W - Math.min(el.offsetWidth, W) - 2));
    t = Math.max(0, Math.min(t, H - Math.min(el.offsetHeight, H) - 2));
    el.style.left = l + 'px'; el.style.top = t + 'px';
  }
  function hacerArrastrable(el, grip, clave) {
    var p = cfg.pos[clave];
    if (p && isFinite(p.l) && isFinite(p.t)) {
      el.style.left = p.l + 'px'; el.style.top = p.t + 'px'; el.style.right = 'auto'; el.style.bottom = 'auto';
      ponerEnLimites(el);
    }
    var ini = null;
    grip.addEventListener('pointerdown', function (e) {
      var r = el.getBoundingClientRect();
      ini = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      el.style.left = r.left + 'px'; el.style.top = r.top + 'px'; el.style.right = 'auto'; el.style.bottom = 'auto';
      el.classList.add('arrastrando');
      try { grip.setPointerCapture(e.pointerId); } catch (x) {}
      e.preventDefault();
    });
    grip.addEventListener('pointermove', function (e) {
      if (!ini) return;
      el.style.left = (e.clientX - ini.dx) + 'px';
      el.style.top = (e.clientY - ini.dy) + 'px';
      ponerEnLimites(el);
    });
    function soltar(e) {
      if (!ini) return;
      ini = null;
      el.classList.remove('arrastrando');
      try { grip.releasePointerCapture(e.pointerId); } catch (x) {}
      cfg.pos[clave] = { l: parseFloat(el.style.left) || 0, t: parseFloat(el.style.top) || 0 };
      guardar();
    }
    grip.addEventListener('pointerup', soltar);
    grip.addEventListener('pointercancel', soltar);
  }
  hacerArrastrable(panelTrade, document.getElementById('gTrade'), 'trade');
  hacerArrastrable(panelTools, document.getElementById('gTools'), 'tools');
  function pintarTools() {
    panelTools.className = 'panel' + (cfg.toolsMin ? ' mini' : '');
    document.getElementById('tMin').innerHTML = cfg.toolsMin ? '&#9998;' : '&#8211;';
    ponerEnLimites(panelTools);
  }
  document.getElementById('tMin').addEventListener('click', function () { cfg.toolsMin = !cfg.toolsMin; pintarTools(); guardar(); });
  pintarTools();
  window.addEventListener('resize', function () { ponerEnLimites(panelTrade); ponerEnLimites(panelTools); });
  pintarColor();
  setModo('cursor');

  // ---------- indice del replay ----------
  var rangoInicial = false;
  window.setIndice = function (i) {
    var indice = Math.max(0, Math.min(TODAS_VELAS.length, Math.floor(Number(i) || 0)));
    var previo = window.indiceActual === undefined ? null : window.indiceActual;
    window.indiceActual = indice;
    var velasVisibles = TODAS_VELAS.slice(0, indice);
    serie.setData(velasVisibles);
    if (!rangoInicial && velasVisibles.length) {
      rangoInicial = true;
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, velasVisibles.length - 140),
        to: velasVisibles.length + 8,
      });
    }
    refrescar(previo);
  };
  window.addEventListener('message', function (event) {
    if (event.data && event.data.type === 'setIndice') window.setIndice(event.data.indice);
  });
  window.addEventListener('resize', function () {
    chart.applyOptions({ width: window.innerWidth, height: window.innerHeight });
    ajustarCanvas();
  });

  ajustarCanvas();
  window.indiceActual = Math.min(50, TODAS_VELAS.length);
  window.setIndice(window.indiceActual);
  requestAnimationFrame(dibujar);
})();
</script>
</body></html>`;
}

function ReplayBacktest({ velas, operaciones, diagnostico, simbolo, desde, hasta, indiceInicio, timeframeEntrada, timeframeMacro, onClose }: { velas: VelaReplay[]; operaciones: { tipo: string; entrada: number; sl: number; tp: number; salida: number; profit: number; hora_apertura: string; hora_cierre: string; motivo_cierre?: 'sl' | 'tp' | 'be'; be_aplicado?: boolean }[]; diagnostico?: z.infer<typeof BacktestDiagnosticSchema>; simbolo: string; desde: string; hasta: string; indiceInicio?: number; timeframeEntrada?: string | null; timeframeMacro?: string | null; onClose: () => void }) {
  const webviewRef = useRef<WebView>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const timeframeBase = timeframeEntrada || 'M15';
  const [velasActivas, setVelasActivas] = useState(velas);
  const indiceInicioSeguro = Math.min(indiceInicio ?? Math.min(50, velas.length), velas.length);
  const [indice, setIndice] = useState(indiceInicioSeguro);
  const [indiceInicioActivo, setIndiceInicioActivo] = useState(indiceInicioSeguro);
  const [reproduciendo, setReproduciendo] = useState(false);
  const [velocidad, setVelocidad] = useState(1);
  const [timeframe, setTimeframe] = useState(timeframeBase);
  const [cargandoVelas, setCargandoVelas] = useState(false);
  const [errorVelas, setErrorVelas] = useState('');
  // Pantalla completa estilo MT5 / TradingView: oculta cabecera y botones, deja solo el gráfico
  // + una barra mínima (play/pausa, velocidad y salir). En web también usa la Fullscreen API.
  const [pantallaCompleta, setPantallaCompleta] = useState(false);
  function alternarPantallaCompleta() {
    const siguiente = !pantallaCompleta;
    setPantallaCompleta(siguiente);
    if (Platform.OS === 'web' && typeof document !== 'undefined') {
      try {
        if (siguiente && !document.fullscreenElement) document.documentElement.requestFullscreen?.();
        else if (!siguiente && document.fullscreenElement) document.exitFullscreen?.();
      } catch { /* el navegador puede bloquear la API; el modo sin cabecera sigue funcionando */ }
    }
  }
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') return;
    const alCambiar = () => { if (!document.fullscreenElement) setPantallaCompleta(false); };
    document.addEventListener('fullscreenchange', alCambiar);
    return () => document.removeEventListener('fullscreenchange', alCambiar);
  }, []);
  const tiempoReplayRef = useRef<number | null>(velas[indiceInicioSeguro - 1]?.time ?? null);
  const tiempoInicioRef = useRef<number | null>(velas[indiceInicioSeguro - 1]?.time ?? null);
  // Dibujos y operaciones manuales viven en el iframe/webview; aquí guardamos la última copia
  // para reinyectarla cuando el HTML se regenera (cambio de temporalidad).
  const estadoManualRef = useRef<string>('');
  const html = useMemo(() => construirHtmlReplay(velasActivas, operaciones, contratoDe(simbolo), estadoManualRef.current), [velasActivas, operaciones, simbolo]);
  const velocidades = [1, 2, 5, 10, 20, 50];
  const timeframesReplay = ['M1', 'M3', 'M5', 'M15', 'M30', 'H1', 'H2', 'H4', 'D1', 'W1', 'MN1'];

  function contarVelasHasta(datos: VelaReplay[], tiempo: number | null): number {
    if (tiempo === null) return 0;
    let low = 0;
    let high = datos.length;
    while (low < high) {
      const medio = Math.floor((low + high) / 2);
      if (datos[medio].time <= tiempo) low = medio + 1;
      else high = medio;
    }
    return low;
  }

  // Salta a la vela donde el bot abrió la operación siguiente/anterior (para verla correr).
  function irAOperacionBot(direccion: 1 | -1) {
    setReproduciendo(false);
    const posiciones = operaciones
      .map((op) => contarVelasHasta(velasActivas, epochUTC(op.hora_apertura)))
      .filter((c) => c > 0)
      .sort((x, y) => x - y);
    const destino = direccion === 1 ? posiciones.find((c) => c > indice) : [...posiciones].reverse().find((c) => c < indice);
    if (destino) setIndice(Math.min(velasActivas.length, destino));
  }

  useEffect(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined') return;
    const alMensaje = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as { type?: string; estado?: unknown } | null;
      if (data && data.type === 'estadoReplay' && typeof data.estado === 'string') estadoManualRef.current = data.estado;
    };
    window.addEventListener('message', alMensaje);
    return () => window.removeEventListener('message', alMensaje);
  }, []);

  useEffect(() => {
    if (indice > 0 && velasActivas[indice - 1]) tiempoReplayRef.current = velasActivas[indice - 1].time;
  }, [indice, velasActivas]);

  useEffect(() => {
    let cancelado = false;
    async function cargarVelas() {
      setErrorVelas('');
      if (timeframe === timeframeBase) {
        setVelasActivas(velas);
        setIndiceInicioActivo(indiceInicioSeguro);
        setIndice(Math.max(1, contarVelasHasta(velas, tiempoReplayRef.current)));
        setCargandoVelas(false);
        return;
      }

      setCargandoVelas(true);
      try {
        const query = new URLSearchParams({ desde, hasta, timeframe, simbolo });
        const resultado = await api(`/api/backtest/velas?${query.toString()}`, ReplayVelasResponseSchema);
        if ('error' in resultado) throw new Error(resultado.error);
        if (!cancelado) {
          const velasNuevas = resultado.velas;
          const indiceInicioNuevo = contarVelasHasta(velasNuevas, tiempoInicioRef.current);
          setVelasActivas(velasNuevas);
          setIndiceInicioActivo(indiceInicioNuevo);
          setIndice(Math.max(1, contarVelasHasta(velasNuevas, tiempoReplayRef.current)));
        }
      } catch (error) {
        if (!cancelado) setErrorVelas(error instanceof Error ? error.message : `No se pudieron cargar las velas de ${timeframe}`);
      } finally {
        if (!cancelado) setCargandoVelas(false);
      }
    }
    void cargarVelas();
    return () => { cancelado = true; };
  }, [timeframe, timeframeBase, velas, desde, hasta, simbolo, indiceInicioSeguro]);

  useEffect(() => {
    if (Platform.OS === 'web') {
      iframeRef.current?.contentWindow?.postMessage({ type: 'setIndice', indice }, '*');
    } else {
      webviewRef.current?.injectJavaScript(`window.setIndice(${indice}); true;`);
    }
  }, [indice]);

  useEffect(() => {
    if (!reproduciendo) return;
    const intervalo = setInterval(() => {
      setIndice((i) => {
        if (i >= velasActivas.length) { setReproduciendo(false); return i; }
        return Math.min(velasActivas.length, i + velocidad);
      });
    }, 250);
    return () => clearInterval(intervalo);
  }, [reproduciendo, velocidad, velasActivas.length]);

  return (
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: '#0b0f14' }}>
      {!pantallaCompleta ? <View style={[styles.replayHeader, { paddingTop: 8, paddingBottom: 6, flexDirection: 'row', alignItems: 'center', gap: 10 }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.replayTitle}>{simbolo} · {timeframe} · Replay del backtest</Text>
          <Text style={[styles.replayHint, { marginTop: 2 }]}>MT5 · {desde} — {hasta} · Azul: entrada · Rojo: SL · Verde: TP</Text>
          <Text style={[styles.replayHint, { marginTop: 2 }]}>
            {operaciones.length} operaciones generadas{diagnostico ? ` · Estrategia ${diagnostico.estrategia_activa} · ${diagnostico.senales_detectadas} señales` : ''}
          </Text>
          {diagnostico?.timeframes_faltantes.length ? <Text style={[styles.replayHint, styles.red, { marginTop: 2 }]}>
            Falta histórico de: {diagnostico.timeframes_faltantes.join(', ')}
          </Text> : null}
          {diagnostico && diagnostico.senales_detectadas === 0 ? <Text style={[styles.replayHint, { marginTop: 2 }]}>
            No hubo señales en este backtest. Cierra el replay y vuelve a ejecutarlo para revisar el diagnóstico completo.
          </Text> : null}
          {cargandoVelas ? <Text style={[styles.foot, { marginTop: 2 }]}>Cargando histórico MT5 para {timeframe}...</Text> : null}
          {errorVelas ? <Text style={styles.error}>{errorVelas}</Text> : null}
        </View>
        <View style={{ width: 96 }}>
          <Button compact label="⛶ Pantalla completa" onPress={alternarPantallaCompleta} color="#3a4656" />
          <Button compact label="✕ Cerrar" onPress={onClose} color="#8a2c2c" />
        </View>
      </View> : null}
        {Platform.OS === 'web'
          ? createElement('iframe', {
              ref: iframeRef,
              srcDoc: html,
              title: 'Replay visual del backtest',
              onLoad: () => { iframeRef.current?.contentWindow?.postMessage({ type: 'setIndice', indice }, '*'); },
              style: { flex: 1, width: '100%', border: 'none', backgroundColor: '#0b0f14' },
            })
          : <WebView
              ref={webviewRef}
              originWhitelist={['*']}
              source={{ html }}
              style={{ flex: 1 }}
              javaScriptEnabled
              onMessage={(event) => {
                try {
                  const msg = JSON.parse(event.nativeEvent.data) as { type?: string; estado?: unknown };
                  if (msg.type === 'estadoReplay' && typeof msg.estado === 'string') estadoManualRef.current = msg.estado;
                } catch { /* mensaje ajeno al replay */ }
              }}
              onLoadEnd={() => webviewRef.current?.injectJavaScript(`window.setIndice(${indice}); true;`)}
            />}
        {pantallaCompleta ? (
          <View style={[styles.replayBar, { padding: 6, paddingBottom: 8, flexDirection: 'row', alignItems: 'center', gap: 6 }]}>
            <Button compact label={reproduciendo ? '⏸' : '▶'} onPress={() => { if (!cargandoVelas && !errorVelas) setReproduciendo((r) => !r); }} color="#237a4b" />
            <Button compact label="-1" onPress={() => { setReproduciendo(false); setIndice((i) => Math.max(1, i - 1)); }} />
            <Button compact label="+1" onPress={() => { setReproduciendo(false); setIndice((i) => Math.min(velasActivas.length, i + 1)); }} />
            <Button compact label={`${velocidad}x`} onPress={() => setVelocidad((v) => velocidades[(velocidades.indexOf(v) + 1) % velocidades.length])} />
            <Text style={[styles.foot, { marginTop: 0, flex: 1, textAlign: 'center' }]}>{timeframe} · Vela {indice} / {velasActivas.length}</Text>
            <Button compact label="⛶ Salir" onPress={alternarPantallaCompleta} color="#b88b35" />
          </View>
        ) : (
        <View style={[styles.replayBar, { padding: 8, paddingBottom: 10 }]}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <Text style={[styles.foot, { marginTop: 0 }]}>Vela {indice} / {velasActivas.length}{reproduciendo ? ` · ${velocidad}x` : ''}</Text>
            <View style={[styles.progressTrack, { flex: 1, height: 5, marginBottom: 0 }]}><View style={[styles.progressFill, { width: `${((indice - indiceInicioActivo) / Math.max(1, velasActivas.length - indiceInicioActivo)) * 100}%` }]} /></View>
          </View>
          <View style={[styles.rowButtons, { gap: 5, marginBottom: 5 }]}>
            <Button compact label="⏮" onPress={() => { setReproduciendo(false); setIndice(Math.min(indiceInicioActivo, velasActivas.length)); }} />
            <Button compact label="-1" onPress={() => { setReproduciendo(false); setIndice((i) => Math.max(1, i - 1)); }} />
            <Button compact label="-10" onPress={() => { setReproduciendo(false); setIndice((i) => Math.max(1, i - 10)); }} />
            <Button compact label={reproduciendo ? '⏸ Pausa' : '▶ Play'} onPress={() => { if (!cargandoVelas && !errorVelas) setReproduciendo((r) => !r); }} color="#237a4b" />
            <Button compact label="+1" onPress={() => { setReproduciendo(false); setIndice((i) => Math.min(velasActivas.length, i + 1)); }} />
            <Button compact label="+10" onPress={() => { setReproduciendo(false); setIndice((i) => Math.min(velasActivas.length, i + 10)); }} />
            <Button compact label={`${velocidad}x`} onPress={() => setVelocidad((v) => velocidades[(velocidades.indexOf(v) + 1) % velocidades.length])} />
          </View>
          <View style={[styles.rowButtons, { gap: 5 }]}>
            {operaciones.length ? <Button compact label="◀ Op. bot" onPress={() => irAOperacionBot(-1)} color="#3a4656" /> : null}
            {operaciones.length ? <Button compact label="Op. bot ▶" onPress={() => irAOperacionBot(1)} color="#3a4656" /> : null}
            {timeframesReplay.map((item) => <Button key={item} compact label={item} onPress={() => { setReproduciendo(false); setTimeframe(item); }} color={timeframe === item ? '#b88b35' : '#303b48'} />)}
          </View>
        </View>
        )}
      </View>
    </Modal>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) { return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>; }
function Button({ label, onPress, color = '#303b48', compact = false }: { label: string; onPress: () => void; color?: string; compact?: boolean }) { return <Pressable style={[styles.button, { backgroundColor: color }, compact ? { minWidth: 40, paddingVertical: 7, paddingHorizontal: 4, marginBottom: 0 } : null]} onPress={onPress}><Text style={styles.buttonText}>{label}</Text></Pressable>; }
// height/maxHeight + overflow explícito: sin esto, en el navegador (react-native-web)
// un <textarea> con mucho texto (ej. una estrategia guardada con el código fuente
// completo, cientos de líneas) puede desbordarse visualmente por fuera de su caja
// y quedar flotando encima de los botones/campos que vienen después, en vez de
// quedarse adentro con scroll. overflow:'hidden' + overflowY:'scroll' fuerza a que
// SIEMPRE se recorte dentro del editor y se pueda ver el resto haciendo scroll
// adentro del cuadro. "resize:'none'" evita que el navegador deje agrandar el
// cuadro arrastrando la esquina (eso también podía descuadrar el layout).
const codeEditorStyle = {
  height: 220,
  minHeight: 220,
  maxHeight: 220,
  overflowY: 'auto' as any,
  overflowX: 'hidden' as any,
  resize: 'none' as any,
  textAlignVertical: 'top' as const,
  fontFamily: 'monospace',
  fontSize: 13,
  lineHeight: 19,
  position: 'relative' as any,
  isolation: 'isolate' as any,
  contain: 'layout paint style' as any,
  width: '100%' as any,
  boxSizing: 'border-box' as any,
};
const descriptionEditorStyle = {
  height: 90,
  minHeight: 90,
  maxHeight: 140,
  overflowY: 'auto' as any,
  resize: 'none' as any,
  position: 'relative' as any,
  isolation: 'isolate' as any,
  contain: 'layout paint style' as any,
  width: '100%' as any,
  boxSizing: 'border-box' as any,
};
function Field({ label, value, onChangeText, multiline = false, numeric = false, code = false, placeholder }: { label: string; value: string; onChangeText: (value: string) => void; multiline?: boolean; numeric?: boolean; code?: boolean; placeholder?: string }) {
  const extraStyle = multiline ? (code ? codeEditorStyle : descriptionEditorStyle) : null;
  return <View style={[styles.field, multiline && { position: 'relative' as any, zIndex: 1, isolation: 'isolate' as any }]}><Text style={styles.label}>{label}</Text><TextInput value={value} onChangeText={onChangeText} placeholder={placeholder} multiline={multiline} scrollEnabled={multiline} numberOfLines={multiline ? (code ? 16 : 4) : 1} keyboardType={numeric ? 'decimal-pad' : 'default'} placeholderTextColor="#687481" style={[styles.input, multiline && styles.codeInput, extraStyle]} /></View>;
}
function fechaComoDate(value: string) {
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  if (year && month && day) date.setFullYear(year, month - 1, day);
  return date;
}
function dateComoFecha(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}
function DateField({ label, value, onChangeText }: { label: string; value: string; onChangeText: (value: string) => void }) {
  const [visible, setVisible] = useState(false);
  if (Platform.OS === 'web') return <View style={styles.field}>
    <Text style={styles.label}>{label}</Text>
    {createElement('input', {
      type: 'date',
      value,
      onChange: (event) => onChangeText(event.currentTarget.value),
      style: {
        width: '100%', minHeight: 42, boxSizing: 'border-box', padding: 11,
        color: '#f4f1e8', backgroundColor: '#0e141b', border: '1px solid #33404d', borderRadius: 6,
        fontSize: 14, colorScheme: 'dark',
      },
    })}
  </View>;
  return <View style={styles.field}>
    <Text style={styles.label}>{label}</Text>
    <Pressable style={styles.input} onPress={() => setVisible(true)}>
      <Text style={styles.dateValue}>{value || 'Elegir fecha'}</Text>
    </Pressable>
    <Modal visible={visible} transparent animationType="fade" onRequestClose={() => setVisible(false)}>
      <Pressable style={styles.dateModalBackdrop} onPress={() => setVisible(false)}>
        <Pressable style={styles.dateModal} onPress={(event) => event.stopPropagation()}>
          <DateTimePicker value={fechaComoDate(value)} mode="date" display="calendar" onChange={(event, selectedDate) => {
            if (selectedDate) onChangeText(dateComoFecha(selectedDate));
            setVisible(false);
          }} />
          <Button label="Cerrar" onPress={() => setVisible(false)} />
        </Pressable>
      </Pressable>
    </Modal>
  </View>;
}
function TimeframeSelector({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <View style={styles.field}>
    <Text style={styles.label}>{label}</Text>
    <View style={styles.timeframeButtons}>
      {BACKTEST_TIMEFRAMES.map((timeframe) => <Pressable key={timeframe} onPress={() => onChange(timeframe)} style={[styles.timeframeOption, value === timeframe && styles.timeframeOptionActive]}><Text style={[styles.timeframeOptionText, value === timeframe && styles.timeframeOptionTextActive]}>{timeframe}</Text></Pressable>)}
    </View>
  </View>;
}

export default function App() {
  const [state, setState] = useState<State | null>(null); const [account, setAccount] = useState<Account | null>(null); const [positions, setPositions] = useState<Position[]>([]); const [history, setHistory] = useState<HistoryItem[]>([]); const [message, setMessage] = useState(''); const [loading, setLoading] = useState(true); const [refreshing, setRefreshing] = useState(false);
  const [symbol, setSymbol] = useState(''); const [simbolosPermitidos, setSimbolosPermitidos] = useState(''); const [volume, setVolume] = useState('0.01'); const [sl, setSl] = useState(''); const [tp, setTp] = useState(''); const [orderType, setOrderType] = useState<'mercado' | 'limite'>('mercado'); const [entryPrice, setEntryPrice] = useState(''); const [blocks, setBlocks] = useState<Record<string, Block>>({}); const [keys, setKeys] = useState<string[]>([]); const [buy, setBuy] = useState<Condition[]>([]); const [sell, setSell] = useState<Condition[]>([]); const [buyOp, setBuyOp] = useState<'and' | 'or'>('and'); const [sellOp, setSellOp] = useState<'and' | 'or'>('and'); const [sendingOrder, setSendingOrder] = useState(false); const [perdidaDiariaPct, setPerdidaDiariaPct] = useState('5'); const [spreadMaximo, setSpreadMaximo] = useState('0');
  // --- Parámetros de "estructura_dinamica" (SpotPlus) — antes fijos en el código, ahora editables ---
  const [pPivotLen, setPPivotLen] = useState('5');
  const [pPivotLenMacro, setPPivotLenMacro] = useState('15');
  const [pPorcentajeTp, setPPorcentajeTp] = useState('0.8');
  const [pSlBufferPct, setPSlBufferPct] = useState('0.1');
  const [pMinimoRatioTp, setPMinimoRatioTp] = useState('0');
  const [pMaxVelasDesdeZona, setPMaxVelasDesdeZona] = useState('0');
  const [pRupturaCuerpoAtr, setPRupturaCuerpoAtr] = useState('1');
  const [pRupturaCierreAtr, setPRupturaCierreAtr] = useState('0.1');
  const [pExtensionesLiquidez, setPExtensionesLiquidez] = useState('1.1,1.27,1.38');
  const [saved, setSaved] = useState<Saved[]>([]); const [name, setName] = useState(''); const [explanation, setExplanation] = useState(''); const [code, setCode] = useState(''); const [savedVolume, setSavedVolume] = useState(''); const [savedSl, setSavedSl] = useState(''); const [savedTp, setSavedTp] = useState('');
  const [savedUsaMultiTf, setSavedUsaMultiTf] = useState(false); const [savedTfEntrada, setSavedTfEntrada] = useState(''); const [savedTfMacro, setSavedTfMacro] = useState('');
  // Temporalidades del ESCANEO multi-timeframe (est.escanear_multitemporalidad):
  // varias a la vez, separadas por coma (ej. "M5, M15, H1, H4") — el bot decide
  // solo cuál es la "principal" para cada señal, en vez de una macro fija.
  const [savedTemporalidadesEscaneo, setSavedTemporalidadesEscaneo] = useState('');
  // Rol de cada grupo en el escaneo multi-temporalidad: dónde se BUSCA la
  // zona, cuál se usa solo para CONFIRMAR el rompimiento, cuál es solo
  // CONTEXTO (filtra/veta a favor o en contra sin abrir operación ahí,
  // pensado para extra_macro semanal/mensual), o NO_USAR (se ignora).
  type RolGrupo = 'buscar' | 'confirmar' | 'contexto' | 'no_usar';
  // Vacío = sin rol elegido (el backend usa su comportamiento por defecto).
  const [rolesTemporalidad, setRolesTemporalidad] = useState<Record<'micro' | 'intermedia' | 'macro' | 'extra_macro', RolGrupo | ''>>({
    micro: '', intermedia: '', macro: '', extra_macro: '',
  });
  // Zonas de espera que esta estrategia puede operar. Vacío = AUTOMÁTICO (el motor
  // acepta todas las que detecte). Si eliges algunas, SOLO opera esas.
  const ZONAS_ESPERA: { id: string; nombre: string; ayuda: string }[] = [
    { id: 'flip', nombre: 'Flip', ayuda: 'cambio de tendencia: primer retroceso tras romper la estructura contraria' },
    { id: 'ev', nombre: 'EV', ayuda: '1er o 2do retroceso normal desde el último Flip' },
    { id: 'obc', nombre: 'OBC', ayuda: 'continuación: 3er retroceso en adelante, o ruptura débil' },
    { id: 'fractal', nombre: 'Fractal', ayuda: 'retroceso Fibonacci 27% / 38% / 50% del impulso' },
    { id: 'fractal2', nombre: 'Fractal 2', ayuda: 'extensión 110-138% mientras la liquidez externa siga sin barrer' },
  ];
  const [zonasPermitidas, setZonasPermitidas] = useState<string[]>([]);
  // El formulario de estrategia está oculto hasta pulsar "Agregar estrategia nueva" (o Editar).
  const [formAbierto, setFormAbierto] = useState(false);
  // --- Gestión de riesgo de la estrategia guardada ---
  // 'lotaje_fijo': usa savedVolume tal cual. 'porcentaje'/'monto_fijo': el bot calcula
  // el lotaje solo en cada operación según savedRiesgoValor y la distancia del SL.
  const [savedModoRiesgo, setSavedModoRiesgo] = useState<'lotaje_fijo' | 'porcentaje' | 'monto_fijo'>('lotaje_fijo');
  const [savedRiesgoValor, setSavedRiesgoValor] = useState('');
  const [lotajeSugerido, setLotajeSugerido] = useState<{ lotaje: number; dinero_riesgo: number; advertencia: string | null } | null>(null);
  const [savedEntryPreview, setSavedEntryPreview] = useState('');
  const [posicionSeleccionada, setPosicionSeleccionada] = useState<Position | null>(null);
  const [btFrom, setBtFrom] = useState('2026-01-01'); const [btTo, setBtTo] = useState('2026-06-01'); const [btBalance, setBtBalance] = useState('10000'); const [btTfEntrada, setBtTfEntrada] = useState('M15'); const [btTfMacro, setBtTfMacro] = useState('H1'); const [btRunning, setBtRunning] = useState(false); const [btResult, setBtResult] = useState<BacktestResult | null>(null); const [btProgress, setBtProgress] = useState(0); const [btShowReplay, setBtShowReplay] = useState(false);
  const [tab, setTab] = useState<'mercado' | 'estrategia' | 'operar' | 'calculadora' | 'historial' | 'fundamentales' | 'telegram'>('mercado');
  const [calcEntrada, setCalcEntrada] = useState(''); const [calcSl, setCalcSl] = useState(''); const [calcTp, setCalcTp] = useState('');
  const [calcModo, setCalcModo] = useState<'porcentaje' | 'fijo'>('porcentaje'); const [calcValor, setCalcValor] = useState('1');
  const [maxOperaciones, setMaxOperaciones] = useState('1');
  const [riesgoTotalPct, setRiesgoTotalPct] = useState('1');
  const [beCascadaBufferPct, setBeCascadaBufferPct] = useState('20');
  const [beDinamicoFiboNivel, setBeDinamicoFiboNivel] = useState('0.5');
  const [gruposTemporalidadTexto, setGruposTemporalidadTexto] = useState('');
  // Escaneo por temporalidades: Automático (grupos y roles por defecto) o Manual (tú eliges).
  const [escaneoManual, setEscaneoManual] = useState(false);
  const [calcResultado, setCalcResultado] = useState<{ lotaje: number; dinero_riesgo: number; distancia_sl: number; advertencia: string | null; perdida_estimada?: number; ganancia_estimada?: number; ratio_rr?: number; riesgo_real_pct?: number | null; oportunidades?: number } | null>(null);
  const [estadisticas, setEstadisticas] = useState<z.infer<typeof EstadisticasSchema> | null>(null);
  const [bitacora, setBitacora] = useState<z.infer<typeof NotaBitacoraSchema>[]>([]);
  const [notaNueva, setNotaNueva] = useState('');
  const [diario, setDiario] = useState<z.infer<typeof DiarioSchema>[]>([]); const [diarioStats, setDiarioStats] = useState<z.infer<typeof DiarioStatsSchema> | null>(null);
  function usarCalculoEnOperar() {
    if (!calcResultado || 'error' in calcResultado) return;
    setVolume(String(calcResultado.lotaje)); setSl(calcSl); setTp(calcTp);
    if (calcEntrada) setEntryPrice(calcEntrada);
    setTab('operar'); setMessage(`Lote ${calcResultado.lotaje} cargado desde la calculadora. Revisa y confirma la orden.`);
  }
  async function calcularRiesgoManual() {
    const entrada = Number(calcEntrada); const sl = Number(calcSl);
    if (!entrada || !sl) { setMessage('Pon precio de entrada y SL para calcular'); return; }
    const result = await api('/api/calcular_riesgo', CalculoRiesgoSchema, post({ precio_entrada: entrada, sl, tp: Number(calcTp) || undefined, modo_riesgo: calcModo, riesgo_valor: Number(calcValor) || 1 }));
    if ('error' in result) { setMessage(result.error); setCalcResultado(null); } else { setCalcResultado(result); }
  }
  async function cargarEstadisticasYBitacora() {
    try { setEstadisticas(await api('/api/estadisticas?dias=30', EstadisticasSchema, {})); } catch { }
    try { setBitacora(await api('/api/bitacora', z.array(NotaBitacoraSchema), {})); } catch { }
    try { setDiario(await api('/api/diario', z.array(DiarioSchema), {})); } catch { }
    try { setDiarioStats(await api('/api/diario/estadisticas', DiarioStatsSchema, {})); } catch { }
  }
  async function agregarNota() {
    if (!notaNueva.trim()) return;
    await api('/api/bitacora', NotaBitacoraSchema, post({ nota: notaNueva.trim() }));
    setNotaNueva('');
    cargarEstadisticasYBitacora();
  }
  async function borrarNota(id: number) {
    await api(`/api/bitacora/${id}`, z.object({ ok: z.boolean() }), { method: 'DELETE' });
    cargarEstadisticasYBitacora();
  }
  async function borrarTodaLaBitacora() {
    await api('/api/bitacora', z.object({ ok: z.boolean() }), { method: 'DELETE' });
    cargarEstadisticasYBitacora();
  }
  // --- Telegram ---
  const [tgActivo, setTgActivo] = useState(false); const [tgToken, setTgToken] = useState(''); const [tgChatId, setTgChatId] = useState('');
  const [tgAvisoApertura, setTgAvisoApertura] = useState(true); const [tgAvisoCierre, setTgAvisoCierre] = useState(true);
  const [tgAvisoSlTp, setTgAvisoSlTp] = useState(true); const [tgAvisoParcial, setTgAvisoParcial] = useState(true); const [tgAvisoZona, setTgAvisoZona] = useState(true);
  const [tgProbando, setTgProbando] = useState(false);
  // --- Fundamentales (NFP, IPC, tasas...) ---
  const [fundamentales, setFundamentales] = useState<z.infer<typeof FundamentalesSchema>>({ pendientes: [], en_proceso: [], pasados: [] });
  async function cargarFundamentales() {
    try { setFundamentales(await api('/api/fundamentales', FundamentalesSchema, {})); } catch { }
  }
  function formatearHoraEvento(iso: string): string {
    try { return new Date(iso).toLocaleString(undefined, { hour: 'numeric', minute: '2-digit', hour12: true, day: '2-digit', month: 'short' }); } catch { return iso; }
  }
  const load = useCallback(async () => { try { const [s, a, p, h, catalog, list, tg] = await Promise.all([api('/api/estado', StateSchema), api('/api/cuenta', AccountResponseSchema), api('/api/posiciones', z.array(PositionSchema)), api('/api/historial?dias=7', z.array(HistoryItemSchema)), api('/api/bloques_disponibles', BloquesDisponiblesSchema), api('/api/estrategias_guardadas', z.array(SavedSchema)), api('/api/telegram', TelegramConfigSchema)]); setState(s); setAccount('error' in a ? null : a); setPositions(p); setHistory(h); setBlocks(catalog.bloques); setKeys(Object.keys(catalog.bloques)); setSaved(list); setSymbol(s.config?.simbolo || ''); setPerdidaDiariaPct(String(s.config?.circuito_perdida_diaria_pct ?? 5)); setSpreadMaximo(String(s.config?.spread_maximo_puntos ?? 0)); setMaxOperaciones(String(s.config?.max_operaciones_por_simbolo ?? 1)); setRiesgoTotalPct(String(s.config?.riesgo_total_pct ?? 1));
      setBeCascadaBufferPct(String(s.config?.be_cascada_buffer_pct ?? 20));
      setBeDinamicoFiboNivel(String(s.config?.be_dinamico_fibo_nivel ?? 0.5));
      setGruposTemporalidadTexto(Object.entries(s.config?.grupos_temporalidad || {}).map(([tf, grupo]) => `${tf}:${grupo}`).join(', '));
      const pEst = s.config?.parametros || {};
      setPPivotLen(String(pEst.pivot_len ?? 5));
      setPPivotLenMacro(String(pEst.pivot_len_macro ?? 15));
      setPPorcentajeTp(String(pEst.porcentaje_tp ?? 0.8));
      setPSlBufferPct(String(pEst.sl_buffer_pct ?? 0.1));
      setPMinimoRatioTp(String(pEst.minimo_ratio_tp ?? 0));
      setPMaxVelasDesdeZona(String(pEst.max_velas_desde_zona ?? 0));
      setPRupturaCuerpoAtr(String(pEst.ruptura_cuerpo_atr ?? 1));
      setPRupturaCierreAtr(String(pEst.ruptura_cierre_atr ?? 0.1));
      const extensionesLiquidez = pEst.extensiones_liquidez;
      setPExtensionesLiquidez(String((Array.isArray(extensionesLiquidez) ? extensionesLiquidez : [1.1, 1.27, 1.38]).join(',')));
      setTgActivo(tg.activo); setTgToken(tg.bot_token); setTgChatId(tg.chat_id); setTgAvisoApertura(tg.aviso_apertura); setTgAvisoCierre(tg.aviso_cierre); setTgAvisoSlTp(tg.aviso_sl_tp); setTgAvisoParcial(tg.aviso_parcial); setTgAvisoZona(tg.aviso_zona); setMessage(''); } catch (error) { setMessage(error instanceof Error ? error.message : 'No se pudo conectar al backend'); } finally { setLoading(false); setRefreshing(false); } }, []);
  useEffect(() => { load(); cargarFundamentales(); const timer = setInterval(() => { load(); cargarFundamentales(); }, 10000); return () => clearInterval(timer); }, [load]);
  // Precio en vivo: el backend refresca el tick cada 1 s, así que el panel consulta solo
  // /api/estado cada 1 s (ligero) en vez de esperar al refresco completo de 10 s.
  useEffect(() => {
    let activo = true; let enCurso = false;
    const timer = setInterval(async () => {
      if (enCurso || (typeof document !== 'undefined' && document.hidden)) return;
      enCurso = true;
      try { const st = await api('/api/estado', StateSchema); if (activo) setState(st); } catch { } finally { enCurso = false; }
    }, 1000);
    return () => { activo = false; clearInterval(timer); };
  }, []);
  useEffect(() => { if (tab !== 'historial') return; cargarEstadisticasYBitacora(); const t = setInterval(cargarEstadisticasYBitacora, 15000); return () => clearInterval(t); }, [tab]);
  async function run(path: string, body: unknown, success: string, method = 'POST') {
    try {
      const options = method === 'DELETE' 
        ? { method: 'DELETE', headers: { 'Content-Type': 'application/json' } } 
        : post(body);
      const result = await api(path, RunResultSchema, options);
      setMessage(result.error || (result.ticket ? `${success}. Ticket ${result.ticket}` : success));
      await load();
      setMessage(result.error || (result.ticket ? `${success}. Ticket ${result.ticket}` : success));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'La operación falló');
    }
  }
  function add(type: 'buy' | 'sell') { if (!keys.length) return; const key = keys[0]; const item = { bloque: key, parametros: Object.fromEntries(blocks[key].parametros.map((p) => [p.nombre, p.default])) }; type === 'buy' ? setBuy([...buy, item]) : setSell([...sell, item]); }
  function change(type: 'buy' | 'sell', index: number) { const list = type === 'buy' ? [...buy] : [...sell]; const key = keys[(keys.indexOf(list[index].bloque) + 1) % keys.length]; list[index] = { bloque: key, parametros: Object.fromEntries(blocks[key].parametros.map((p) => [p.nombre, p.default])) }; type === 'buy' ? setBuy(list) : setSell(list); }
  function edit(type: 'buy' | 'sell', index: number, parameter: string, value: string) { const list = type === 'buy' ? [...buy] : [...sell]; list[index] = { ...list[index], parametros: { ...list[index].parametros, [parameter]: value } }; type === 'buy' ? setBuy(list) : setSell(list); }
  function renderConditions(type: 'buy' | 'sell', list: Condition[]) { return list.map((item, index) => { const block = blocks[item.bloque]; return <View style={styles.condition} key={`${type}-${index}`}><View style={styles.conditionHeader}><Text style={styles.conditionTitle}>{block?.nombre}</Text><Pressable onPress={() => type === 'buy' ? setBuy(buy.filter((_, i) => i !== index)) : setSell(sell.filter((_, i) => i !== index))}><Text style={styles.remove}>Quitar</Text></Pressable></View><Text style={styles.explanation}>{block?.explicacion}</Text><Button label="Cambiar bloque" onPress={() => change(type, index)} color="#273644" />{block?.parametros.map((p) => <Field key={p.nombre} label={p.etiqueta} value={String(item.parametros[p.nombre] ?? '')} onChangeText={(value) => edit(type, index, p.nombre, value)} numeric={p.tipo === 'numero'} />)}</View>; }); }
  async function exportarGuardada(item: Saved) {
    const texto = item.codigo || 'Sin código guardado.';
    try {
      if (Platform.OS === 'web' && typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(texto);
        setMessage(`Código de "${item.nombre}" copiado al portapapeles`);
        return;
      }
    } catch { }
    Alert.alert(`Código: ${item.nombre}`, texto, [{ text: 'Cerrar', style: 'cancel' }]);
    setMessage(`Mostrando el código de "${item.nombre}"`);
  }
  function saveBlocks() { if (!buy.length && !sell.length) { Alert.alert('Faltan condiciones', 'Agrega una condición.'); return; } run('/api/estrategia_bloques', { condiciones_compra: buy, operador_compra: buyOp, condiciones_venta: sell, operador_venta: sellOp }, 'Estrategia visual guardada'); }
  async function guardarTelegram() {
    try {
      const payload: Record<string, unknown> = {
        activo: tgActivo,
        aviso_apertura: tgAvisoApertura,
        aviso_cierre: tgAvisoCierre,
        aviso_sl_tp: tgAvisoSlTp,
        aviso_parcial: tgAvisoParcial,
        aviso_zona: tgAvisoZona,
      };
      const token = tgToken.trim();
      const chatId = tgChatId.trim();
      if (token) payload.bot_token = token;
      if (chatId) payload.chat_id = chatId;
      const tg = await api('/api/telegram', TelegramConfigSchema, post(payload));
      setTgActivo(tg.activo);
      setTgToken(tg.bot_token);
      setTgChatId(tg.chat_id);
      setMessage('Configuración de Telegram guardada');
    } catch (error) { setMessage(error instanceof Error ? error.message : 'No se pudo guardar la configuración de Telegram'); }
  }
  async function probarTelegram() {
    setTgProbando(true);
    try {
      await guardarTelegram();
      const resultado = await api('/api/telegram/probar', TelegramProbarSchema, post({}));
      setMessage(resultado.ok ? 'Mensaje de prueba enviado a Telegram' : `Telegram rechazó el mensaje: ${resultado.error || 'sin detalle'}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : 'No se pudo mandar el mensaje de prueba'); }
    setTgProbando(false);
  }
  function rolesElegidos() {
    return Object.fromEntries(Object.entries(rolesTemporalidad).filter(([, v]) => v)) as Record<string, string>;
  }
  function cambiarTemporalidadEscaneo(timeframe: string) {
    const seleccionadas = new Set(
      savedTemporalidadesEscaneo.split(/[,\s]+/).map((tf) => tf.trim().toUpperCase()).filter(Boolean),
    );
    if (seleccionadas.has(timeframe)) seleccionadas.delete(timeframe);
    else seleccionadas.add(timeframe);
    setSavedTemporalidadesEscaneo(
      TEMPORALIDADES_ESCANEO.filter((tf) => seleccionadas.has(tf)).join(', '),
    );
  }
  function guardarTemporalidadesActivas(temporalidades: string[]) {
    const seleccionadas = new Set(temporalidades.map((tf) => tf.toUpperCase()));
    const ordenadas = TEMPORALIDADES_ESCANEO.filter((tf) => seleccionadas.has(tf));
    run(
      '/api/config',
      { temporalidades_escaneo: ordenadas },
      ordenadas.length ? 'Temporalidades activas actualizadas' : 'Escaneo multi-temporalidad desactivado',
    );
  }
  // Deja el formulario "Agregar estrategia" totalmente en blanco (como la primera vez),
  // para que una estrategia nueva NUNCA herede valores de la anterior.
  function resetFormularioEstrategia() {
    setName(''); setExplanation(''); setCode(''); setEditando(null);
    setSavedSl(''); setSavedTp(''); setSavedVolume(''); setSavedRiesgoValor(''); setSavedEntryPreview('');
    setSavedModoRiesgo('lotaje_fijo');
    setSavedUsaMultiTf(false); setSavedTfEntrada(''); setSavedTfMacro('');
    setSavedTemporalidadesEscaneo('');
    setRolesTemporalidad({ micro: '', intermedia: '', macro: '', extra_macro: '' });
    setZonasPermitidas([]);
    setLotajeSugerido(null);
    setFormAbierto(false);
  }
  // Formulario vacío y visible (para empezar una estrategia nueva).
  function nuevaEstrategiaEnBlanco() { resetFormularioEstrategia(); setFormAbierto(true); }
  function saveLibrary() {
    const nombre = name.trim();
    if (!nombre || !code.trim()) { Alert.alert('Faltan datos', 'Escribe nombre y código.'); return; }
    const yaExiste = saved.some((item) => item.nombre.toLowerCase() === nombre.toLowerCase());
    if (yaExiste && !editando) {
      Alert.alert(
        'Estrategia existente',
        `Ya existe "${nombre}". Se actualizará la versión guardada.`,
        [
          { text: 'Cancelar', style: 'cancel' },
          { text: 'Actualizar', onPress: () => {
              setEditando(nombre);
              run('/api/estrategias_guardadas', { nombre, explicacion: explanation, codigo: code, volumen: Number(savedVolume) || 0.01, sl: Number(savedSl) || 0, tp: Number(savedTp) || 0, modo_riesgo: savedModoRiesgo, riesgo_valor: Number(savedRiesgoValor) || 1.0, usa_multi_temporalidad: savedUsaMultiTf, timeframe_entrada: savedTfEntrada, timeframe_macro: savedTfMacro, temporalidades_escaneo: savedTemporalidadesEscaneo.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean), roles_temporalidad: rolesElegidos(), zonas_permitidas: zonasPermitidas }, 'Estrategia actualizada');
              resetFormularioEstrategia();
            } },
        ],
      );
      return;
    }
    run('/api/estrategias_guardadas', { nombre, explicacion: explanation, codigo: code, volumen: Number(savedVolume) || 0.01, sl: Number(savedSl) || 0, tp: Number(savedTp) || 0, modo_riesgo: savedModoRiesgo, riesgo_valor: Number(savedRiesgoValor) || 1.0, usa_multi_temporalidad: savedUsaMultiTf, timeframe_entrada: savedTfEntrada, timeframe_macro: savedTfMacro, temporalidades_escaneo: savedTemporalidadesEscaneo.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean), roles_temporalidad: rolesElegidos(), zonas_permitidas: zonasPermitidas }, 'Estrategia guardada');
    resetFormularioEstrategia();
  }
  async function guardarEstrategiaActual() {
    const nombre = name.trim() || `${state?.config?.estrategia === 'estructura_dinamica' ? 'SpotPlus' : 'Estrategia'} actual`;
    const explicacion = explanation.trim() || `Snapshot de la estrategia activa en el backend (${state?.config?.estrategia || 'manual'}).`;
    const codigo = code.trim() || (
      state?.config?.estrategia === 'estructura_dinamica'
        ? `# Snapshot de SpotPlus\n# entrada=${state.config.timeframe_entrada || 'M15'}\n# macro=${state.config.timeframe_macro || 'H1'}\n# riesgo=${state.config.modo_riesgo || 'lotaje_fijo'}\n`
        : '# Estrategia guardada desde el backend\n'
    );
    try {
      const yaExiste = saved.some((item) => item.nombre.toLowerCase() === nombre.toLowerCase());
      if (yaExiste && !editando) {
        Alert.alert(
          'Estrategia existente',
          `Ya existe "${nombre}". Se sobrescribirá la estrategia guardada actual.`,
          [
            { text: 'Cancelar', style: 'cancel' },
            { text: 'Sobrescribir', onPress: async () => {
                const result = await api('/api/estrategias_guardadas/desde_actual', RunResultSchema, post({ nombre, explicacion, codigo, volumen: Number(savedVolume) || 0.01, sl: Number(savedSl) || 0, tp: Number(savedTp) || 0, modo_riesgo: savedModoRiesgo, riesgo_valor: Number(savedRiesgoValor) || 1.0 }));
                setMessage(result.error || 'Estrategia activa actualizada en la biblioteca');
                await load();
                resetFormularioEstrategia();
              } },
          ],
        );
        return;
      }
      const result = await api('/api/estrategias_guardadas/desde_actual', RunResultSchema, post({ nombre, explicacion, codigo, volumen: Number(savedVolume) || 0.01, sl: Number(savedSl) || 0, tp: Number(savedTp) || 0, modo_riesgo: savedModoRiesgo, riesgo_valor: Number(savedRiesgoValor) || 1.0 }));
      setMessage(result.error || 'Estrategia activa guardada en la biblioteca');
      await load();
      resetFormularioEstrategia();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'No se pudo guardar la estrategia activa');
    }
  }
  const [editando, setEditando] = useState<string | null>(null);
  function editarGuardada(item: Saved) {
    setName(item.nombre);
    setExplanation(item.explicacion);
    setCode(item.codigo);
    setSavedSl(String(item.sl));
    setSavedTp(String(item.tp));
    setSavedUsaMultiTf(!!item.usa_multi_temporalidad);
    setSavedTfEntrada(item.timeframe_entrada || '');
    setSavedTfMacro(item.timeframe_macro || '');
    setSavedTemporalidadesEscaneo((item.temporalidades_escaneo || []).join(', '));
    const rolesGuardados = (item.roles_temporalidad || {}) as Record<string, string>;
    setEscaneoManual(Object.values(rolesGuardados).some(Boolean));
    setRolesTemporalidad({
      micro: (rolesGuardados.micro as any) || '',
      intermedia: (rolesGuardados.intermedia as any) || '',
      macro: (rolesGuardados.macro as any) || '',
      extra_macro: (rolesGuardados.extra_macro as any) || '',
    });
    setZonasPermitidas((item.zonas_permitidas || []).filter((z) => ZONAS_ESPERA.some((e) => e.id === z)));
    const modo = (item.modo_riesgo === 'porcentaje' || item.modo_riesgo === 'monto_fijo') ? item.modo_riesgo : 'lotaje_fijo';
    setSavedModoRiesgo(modo);
    if (modo === 'lotaje_fijo') { setSavedVolume(String(item.volumen)); } else { setSavedRiesgoValor(String(item.riesgo_valor ?? 1.0)); }
    setEditando(item.nombre);
    setFormAbierto(true);
    setMessage(`Editando "${item.nombre}" — sus datos se cargaron en el formulario de arriba.`);
  }
  useEffect(() => {
    if (savedModoRiesgo === 'lotaje_fijo') { setLotajeSugerido(null); return; }
    const entrada = Number(savedEntryPreview) || (state?.precio?.bid ?? 0);
    const sl = Number(savedSl);
    const valor = Number(savedRiesgoValor);
    if (!entrada || !sl || !valor) { setLotajeSugerido(null); return; }
    const timer = setTimeout(async () => {
      try {
        const result = await api('/api/calcular_riesgo', CalculoRiesgoSchema, post({ precio_entrada: entrada, sl, modo_riesgo: savedModoRiesgo, riesgo_valor: valor }));
        setLotajeSugerido('error' in result ? null : result);
      } catch { setLotajeSugerido(null); }
    }, 500);
    return () => clearTimeout(timer);
  }, [savedModoRiesgo, savedRiesgoValor, savedSl, savedEntryPreview, state?.precio?.bid]);
  function order(type: 'compra' | 'venta') {
    const amount = Number(volume);
    if (!amount || amount <= 0) { Alert.alert('Volumen inválido', 'Debe ser mayor que cero.'); return; }
    const label = type === 'venta' ? 'VENDER' : 'COMPRAR';
    if (orderType === 'limite' && (!Number(entryPrice) || Number(entryPrice) <= 0)) { Alert.alert('Falta el precio', 'Indica el precio de entrada de la orden límite.'); return; }
    const mensaje = `${label} ${amount} lotes de ${symbol || 'el símbolo activo'}\nTipo: ${orderType === 'limite' ? `Límite a ${entryPrice}` : 'Mercado'}\n\nEsta acción enviará una orden a MetaTrader 5.`;
    // En web, Alert.alert de React Native NO soporta botones con callbacks
    // (Cancelar/Confirmar) de forma confiable: el clic no dispara onPress y
    // por eso parecía que el botón "no hacía nada". Ahí usamos
    // window.confirm, que sí bloquea y devuelve true/false. En app nativa
    // (iOS/Android) sí funciona Alert.alert con botones normalmente.
    if (Platform.OS === 'web') {
      if (typeof window !== 'undefined' && window.confirm(`Confirmar operación\n\n${mensaje}`)) {
        confirmOrder(type);
      }
      return;
    }
    Alert.alert(
      'Confirmar operación',
      mensaje,
      [
        { text: 'Cancelar', style: 'cancel' },
        { text: 'Confirmar', style: type === 'venta' ? 'destructive' : 'default', onPress: () => confirmOrder(type) },
      ],
    );
  }
  async function confirmOrder(type: 'compra' | 'venta') {
    setSendingOrder(true); setMessage(`Enviando ${type === 'venta' ? 'vender' : 'comprar'}...`);
    await run('/api/orden', { tipo: type, volumen: Number(volume), sl: Number(sl) || 0, tp: Number(tp) || 0, tipo_orden: orderType, precio_entrada: Number(entryPrice) || 0 }, `Orden de ${type} ${orderType} enviada`);
    setSendingOrder(false);
  }
  // El backend arranca el backtest en un hilo aparte y devuelve un job_id al
  // instante. Acá se hace polling del % de avance en vez de quedarse
  // congelado en "Simulando..." sin saber cuánto falta.
  async function runBacktest() {
    if (!btFrom.trim() || !btTo.trim()) { Alert.alert('Faltan fechas', 'Indica desde y hasta (YYYY-MM-DD).'); return; }
    setBtRunning(true); setBtResult(null); setBtProgress(0);
    try {
      const { job_id } = await api('/api/backtest', BacktestStartSchema, post({ desde: btFrom.trim(), hasta: btTo.trim(), balance_inicial: Number(btBalance) || 10000, timeframe_entrada: btTfEntrada, timeframe_macro: btTfMacro }));
      // eslint-disable-next-line no-constant-condition
      while (true) {
        await new Promise((r) => setTimeout(r, 800));
        const progreso = await api(`/api/backtest/progreso/${job_id}`, BacktestProgresoSchema);
        if ('error' in progreso) { setBtResult({ error: progreso.error }); break; }
        setBtProgress(progreso.porcentaje);
        if (progreso.listo) {
          setBtResult(progreso.resultado || { error: 'El backtest terminó sin devolver resultados. Revisa la conexión con MT5 y el rango de fechas.' });
          break;
        }
      }
    } catch (error) {
      setBtResult({ error: error instanceof Error ? error.message : 'El backtest falló' });
    } finally {
      setBtRunning(false); setBtProgress(0);
    }
  }
  if (loading) return <View style={styles.center}><ActivityIndicator color="#e4b75a" size="large" /><Text style={styles.muted}>Conectando con SpotPlus...</Text></View>;
  return <View style={styles.screen}><StatusBar style="light" /><ScrollView contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor="#e4b75a" />}><View style={styles.header}><View><Text style={styles.kicker}>TRADING CONTROL</Text><Text style={styles.title}>SpotPlus</Text></View><View style={[styles.connection, state?.conectado ? styles.online : styles.offline]}><View style={styles.dot} /><Text style={styles.connectionText}>{state?.conectado ? 'MT5 en línea' : 'MT5 sin conexión'}</Text></View></View>{message ? <Text style={styles.message}>{message}</Text> : null}{state?.error ? <Text style={styles.error}>{state.error}</Text> : null}
  {state?.ultima_orden ? (
    state.ultima_orden.ok
      ? <Text style={[styles.message, { backgroundColor: '#183b2a', color: '#70e0a3' }]}>Última orden del bot: EJECUTADA · ticket {state.ultima_orden.ticket} · precio {state.ultima_orden.precio}</Text>
      : <Text style={styles.error}>Última orden del bot RECHAZADA por MT5: {state.ultima_orden.error || 'sin detalle'}</Text>
  ) : null}
  <View style={styles.tabBar}>
    {([['mercado', 'Mercado'], ['estrategia', 'Estrategia'], ['operar', 'Operar'], ['calculadora', 'Calculadora'], ['historial', 'Historial'], ['fundamentales', 'Fundamentales'], ['telegram', 'Telegram']] as const).map(([key, label]) => (
      <Pressable key={key} onPress={() => setTab(key)} style={[styles.tabButton, tab === key && styles.tabButtonActive]}>
        <Text style={[styles.tabButtonText, tab === key && styles.tabButtonTextActive]}>{label}</Text>
      </Pressable>
    ))}
  </View>
  {tab === 'mercado' && <>
  <Section title="Mercado">
  {(() => {
    const activo = state?.config?.activo ?? true;
    const nombreEstrategia = state?.config?.estrategia_biblioteca_activa || (state?.config?.estrategia === 'estructura_dinamica' ? 'SpotPlus (estructura dinámica)' : state?.config?.estrategia);
    const simboloActivo = state?.simbolo_actual || state?.config?.simbolo || '--';
    if (activo && nombreEstrategia) {
      return <View style={[styles.connection, styles.online, { alignSelf: 'flex-start', marginBottom: 14 }]}><View style={styles.dot} /><Text style={styles.connectionText}>Operando "{nombreEstrategia}" en {simboloActivo}</Text></View>;
    }
    if (!activo) {
      return <View style={[styles.connection, styles.offline, { alignSelf: 'flex-start', marginBottom: 14 }]}><View style={[styles.dot, { backgroundColor: '#ff9999' }]} /><Text style={styles.connectionText}>Bot en pausa · sin operar {simboloActivo}</Text></View>;
    }
    return <View style={[styles.connection, styles.offline, { alignSelf: 'flex-start', marginBottom: 14 }]}><View style={[styles.dot, { backgroundColor: '#e4b75a' }]} /><Text style={styles.connectionText}>Sin estrategia activa en {simboloActivo}</Text></View>;
  })()}
  <View style={styles.market}><View><Text style={styles.muted}>SÍMBOLO ACTIVO</Text><Text style={styles.symbol}>{state?.simbolo_actual || state?.config?.simbolo || '--'}</Text></View><View style={styles.right}><Text style={styles.muted}>SEÑAL</Text><Text style={styles.signal}>{(state?.senal || 'sin señal').toUpperCase()}</Text></View></View><View style={styles.metrics}><Text style={styles.metric}>{state?.precio ? `${state.precio.bid} / ${state.precio.ask}` : '--'}</Text><Text style={styles.metric}>{state?.ultima_actualizacion || '--'}</Text></View></Section>
  <Section title="Cuenta">{account ? <>{[['Balance', `${account.balance} ${account.moneda}`], ['Equidad', `${account.equity} ${account.moneda}`], ['Beneficio flotante', `${account.profit} ${account.moneda}`], ['Apalancamiento', `1:${account.apalancamiento}`]].map(([label, value]) => <View style={styles.row} key={label}><Text style={styles.label}>{label}</Text><Text style={styles.value}>{value}</Text></View>)}<Text style={styles.foot}>Cuenta {account.cuenta} · {account.servidor} · {account.es_real ? 'REAL' : 'DEMO'}</Text></> : <Text style={styles.muted}>Cuenta no disponible</Text>}</Section>
  </>}
  {tab === 'estrategia' && <>
  <Section title="Control del bot"><Field label="SÍMBOLO ACTIVO" value={symbol} onChangeText={setSymbol} /><Field label="ACTIVOS PERMITIDOS (separados por coma)" value={simbolosPermitidos} onChangeText={setSimbolosPermitidos} /><View style={styles.rowButtons}><Button label="Guardar símbolo" onPress={() => run('/api/config', { simbolo: symbol.trim().toUpperCase(), simbolos_permitidos: simbolosPermitidos.split(',').map((s: string) => s.trim()).filter(Boolean).map((s: string) => s.toUpperCase()) }, 'Símbolo y activos guardados')} color="#b88b35" /><Button label={state?.config?.activo ? 'Pausar bot' : 'Reanudar bot'} onPress={() => run('/api/config', { activo: !(state?.config?.activo ?? true) }, 'Estado actualizado')} /></View></Section>
  <Section title="Dirección y protección multi-temporalidad">
    <Text style={styles.explanation}>En modo escáner, los marcos seleccionados se clasifican como micro (confirma), intermedia/macro (buscan zonas) y extra macro (contexto). En modo manual se usa la entrada y macro configuradas. Si cambia el sesgo macro, recibirás un aviso; las posiciones ganadoras en contra pueden protegerse moviendo el SL a BE.</Text>
    <Text style={styles.codeSummary}>Sesgo del marco alto: {state?.sesgo_macro || 'sin confirmar'} · {state?.config?.temporalidades_escaneo?.length ? `Escaneo ${state.config.temporalidades_escaneo.join(', ')}` : `Entrada ${state?.config?.timeframe_entrada || 'M15'} · Macro ${state?.config?.timeframe_macro || 'H1'}`}</Text>
    <Text style={styles.codeSummary}>Estructura por marco: {Object.entries(state?.contexto_estructura?.temporalidades || {}).map(([tf, contexto]) => `${tf} ${contexto.sesgo || 'sin sesgo'}${contexto.acumulacion ? ' · acumulación' : ''}`).join(' · ') || 'esperando velas'}</Text>
    <Text style={[styles.subTitle, { marginTop: 14 }]}>Temporalidades del escáner activo</Text>
    <Text style={styles.explanation}>Para una estrategia guardada que use el escáner SpotPlus, se consultan exclusivamente los marcos seleccionados. En cada marco se buscan todas las oportunidades válidas; los roles micro/intermedia/macro determinan si confirma, busca zona o aporta contexto. Si desactivas el escáner, se usa el modo manual de entrada/macro. El motor clásico "estructura dinámica" siempre usa esos dos marcos manuales.</Text>
    <View style={styles.rowButtons}>
      <Button
        label="Escanear todas"
        onPress={() => guardarTemporalidadesActivas(TEMPORALIDADES_ESCANEO)}
        color={new Set((state?.config?.temporalidades_escaneo || []).map((tf) => tf.toUpperCase())).size === TEMPORALIDADES_ESCANEO.length ? '#237a4b' : '#303b48'}
      />
      <Button
        label="Modo manual"
        onPress={() => guardarTemporalidadesActivas([])}
        color={!state?.config?.temporalidades_escaneo?.length ? '#b88b35' : '#303b48'}
      />
    </View>
    <View style={styles.rowButtons}>
      {TEMPORALIDADES_ESCANEO.map((timeframe) => {
        const actuales = (state?.config?.temporalidades_escaneo || []).map((tf) => tf.toUpperCase());
        const activada = actuales.includes(timeframe);
        return (
          <Button
            key={timeframe}
            label={timeframe}
            onPress={() => {
              guardarTemporalidadesActivas(
                actuales.includes(timeframe)
                  ? actuales.filter((tf) => tf !== timeframe)
                  : [...actuales, timeframe],
              );
            }}
            color={activada ? '#b88b35' : '#303b48'}
          />
        );
      })}
    </View>
    {state?.contexto_estructura?.conflictos.map((conflicto) => <Text key={`${conflicto.mayor}-${conflicto.menor}`} style={styles.explanation}>Conflicto: {conflicto.mayor} {conflicto.sesgo_mayor} vs {conflicto.menor} {conflicto.sesgo_menor}; el escáner prioriza el marco mayor.</Text>)}
    <View style={styles.rowButtons}>
      <Button label={state?.config?.filtro_sesgo_macro ? 'Filtro macro: ACTIVADO' : 'Filtro macro: desactivado'} onPress={() => run('/api/config', { filtro_sesgo_macro: !(state?.config?.filtro_sesgo_macro ?? false) }, 'Filtro de sesgo actualizado')} color={state?.config?.filtro_sesgo_macro ? '#237a4b' : '#303b48'} />
      <Button label={state?.config?.aviso_cambio_direccion ? 'Avisos: ACTIVADOS' : 'Avisos: desactivados'} onPress={() => run('/api/config', { aviso_cambio_direccion: !(state?.config?.aviso_cambio_direccion ?? true) }, 'Avisos de dirección actualizados')} color={state?.config?.aviso_cambio_direccion ? '#237a4b' : '#303b48'} />
      <Button label={state?.config?.proteger_cambio_direccion ? 'BE por cambio: ACTIVADO' : 'BE por cambio: desactivado'} onPress={() => run('/api/config', { proteger_cambio_direccion: !(state?.config?.proteger_cambio_direccion ?? true) }, 'Protección por dirección actualizada')} color={state?.config?.proteger_cambio_direccion ? '#237a4b' : '#303b48'} />
    </View>
    <Text style={[styles.subTitle, { marginTop: 14 }]}>BE dinámico (Fibo + nueva estructura + alineación)</Text>
    <Text style={styles.explanation}>Protege la operación a BE si se cumple CUALQUIERA de estas tres: el precio llega al nivel de Fibonacci del tramo de entrada, se confirma una nueva estructura a favor en una temporalidad micro, o se pierde la alineación entre la macro y la intermedia que sostenía la operación.</Text>
    <View style={styles.rowButtons}>
      <Button label={state?.config?.be_dinamico_activo ? 'BE dinámico: ACTIVADO' : 'BE dinámico: desactivado'} onPress={() => run('/api/config', { be_dinamico_activo: !(state?.config?.be_dinamico_activo ?? false) }, 'BE dinámico actualizado')} color={state?.config?.be_dinamico_activo ? '#237a4b' : '#303b48'} />
    </View>
    <View style={styles.rowButtons}>
      <Field label="NIVEL DE FIBONACCI (ej. 0.5 = 50%)" value={beDinamicoFiboNivel} onChangeText={setBeDinamicoFiboNivel} numeric />
      <Field label="COLCHÓN BE EN CASCADA (% de la distancia al SL)" value={beCascadaBufferPct} onChangeText={setBeCascadaBufferPct} numeric />
    </View>
    <Field label="GRUPOS DE TEMPORALIDAD (ej. M15:macro, H1:intermedia, M5:micro)" value={gruposTemporalidadTexto} onChangeText={setGruposTemporalidadTexto} />
    <Text style={styles.explanation}>Es el mismo ajuste que puedes cambiar con botones en "Agregar estrategia → Rol de cada grupo en el escaneo"; aquí puedes escribirlo directo si prefieres. Por defecto: M1/M3/M5 = micro, M15/M30/H1 = intermedia, H2/H4/D1 = macro. Escribe aquí solo las que quieras cambiar; el resto se queda como está.</Text>
    <Button label="Guardar BE dinámico y grupos" onPress={() => {
      const grupos: Record<string, string> = {};
      gruposTemporalidadTexto.split(',').map((par) => par.trim()).filter(Boolean).forEach((par) => {
        const [tf, grupo] = par.split(':').map((x) => x.trim());
        if (tf && grupo) grupos[tf.toUpperCase()] = grupo.toLowerCase();
      });
      run('/api/config', {
        be_dinamico_fibo_nivel: Number(beDinamicoFiboNivel) || 0.5,
        be_cascada_buffer_pct: Number(beCascadaBufferPct) || 0,
        grupos_temporalidad: grupos,
      }, 'BE dinámico y grupos guardados');
    }} color="#b88b35" />
  </Section>
  <Section title="Circuit breaker y spread">
    <Text style={styles.explanation}>Si la pérdida del día llega al % configurado, el bot deja de abrir operaciones nuevas hasta mañana (las que ya están abiertas siguen con su SL/TP normal). El límite de spread evita que el bot abra justo cuando el spread se dispara por una noticia.</Text>
    {state?.circuito_disparado ? <Text style={styles.error}>🛑 Circuit breaker activado: el bot no abrirá operaciones nuevas hoy.</Text> : null}
    <View style={styles.rowButtons}><Button label={state?.config?.circuito_perdida_diaria_activo ? 'Circuit breaker: ACTIVADO' : 'Circuit breaker: desactivado'} onPress={() => run('/api/config', { circuito_perdida_diaria_activo: !(state?.config?.circuito_perdida_diaria_activo ?? false) }, 'Circuit breaker actualizado')} color={state?.config?.circuito_perdida_diaria_activo ? '#237a4b' : '#303b48'} /></View>
    <View style={styles.rowButtons}>
      <Field label="PÉRDIDA DIARIA MÁXIMA (% del balance)" value={perdidaDiariaPct} onChangeText={setPerdidaDiariaPct} numeric />
      <Field label="SPREAD MÁXIMO (puntos, 0 = sin límite)" value={spreadMaximo} onChangeText={setSpreadMaximo} numeric />
    </View>
    <Field label="MÁX. OPERACIONES ABIERTAS POR SÍMBOLO (0 = ilimitadas)" value={maxOperaciones} onChangeText={setMaxOperaciones} numeric />
    <Text style={styles.explanation}>Por defecto es 1: evita que una misma señal abra varias operaciones seguidas. La temporalidad de entrada y la macro de la estrategia activa se mantienen aunque MT5 muestre otro gráfico.</Text>
    <Text style={[styles.label, { marginTop: 10 }]}>RIESGO TOTAL (se reparte entre activos)</Text>
    <Text style={styles.explanation}>Activado: el riesgo total (ej. 1% del balance) se divide entre los activos que analiza el bot y sus operaciones máximas abiertas, así nunca te pasas del total aunque agregues más activos. Manda sobre el riesgo de cada estrategia.</Text>
    <View style={styles.rowButtons}>
      <Button label={state?.config?.riesgo_total_activo ? 'Riesgo total: ACTIVADO' : 'Riesgo total: desactivado'} onPress={() => run('/api/config', { riesgo_total_activo: !state?.config?.riesgo_total_activo }, 'Riesgo total actualizado')} color={state?.config?.riesgo_total_activo ? '#237a4b' : '#303b48'} />
    </View>
    <Field label="RIESGO TOTAL (% del balance)" value={riesgoTotalPct} onChangeText={setRiesgoTotalPct} numeric />
    <Text style={styles.muted}>Ahora: {state?.config?.simbolos_permitidos?.length || 1} activo(s) × {state?.config?.max_operaciones_por_simbolo || 2} operaciones = {((Number(riesgoTotalPct) || 1) / ((state?.config?.simbolos_permitidos?.length || 1) * (state?.config?.max_operaciones_por_simbolo || 2))).toFixed(3)}% por operación.</Text>
    <Button label="Guardar riesgo total" onPress={() => run('/api/config', { riesgo_total_pct: Number(riesgoTotalPct) || 1 }, 'Riesgo total guardado')} color="#b88b35" />
    <Button label="Guardar límites" onPress={() => run('/api/config', { circuito_perdida_diaria_pct: Number(perdidaDiariaPct) || 0, spread_maximo_puntos: Number(spreadMaximo) || 0, max_operaciones_por_simbolo: Math.max(0, Math.floor(Number(maxOperaciones) || 0)) }, 'Límites guardados')} color="#b88b35" />
  </Section>
  <Section title="Estrategias guardadas">
    {!formAbierto ? <Button label="+ Agregar estrategia nueva" onPress={nuevaEstrategiaEnBlanco} color="#2563eb" /> : null}
    <Modal visible={formAbierto} animationType="slide" onRequestClose={resetFormularioEstrategia}>
      <View style={{ flex: 1, backgroundColor: '#0d1117' }}>
        <ScrollView contentContainerStyle={styles.content}>
          <Section title={editando ? `Editando estrategia: ${editando}` : 'Nueva estrategia'}>
            {editando ? (
              <View style={styles.saved}>
                <Text style={[styles.explanation, { color: '#e4b75a' }]}>Estás EDITANDO "{editando}". Los datos de abajo son de esa estrategia y, al guardar, se sobrescribe. Para crear una estrategia distinta, empieza una nueva en blanco.</Text>
                <Button label="Nueva estrategia en blanco" onPress={nuevaEstrategiaEnBlanco} color="#2563eb" />
              </View>
            ) : null}
    <Text style={styles.explanation}>Escribe aquí el script Python de cada estrategia. El código debe terminar asignando la variable `senal` con "compra", "venta" o None.</Text>
    <Field label="NOMBRE" value={name} onChangeText={setName} />
    <Field label="DESCRIPCIÓN" value={explanation} onChangeText={setExplanation} multiline />
    <Text style={[styles.label, { marginTop: 4 }]}>TEMPORALIDADES</Text>
    {savedTemporalidadesEscaneo.trim() ? (
      <Text style={[styles.explanation, { color: '#e4b75a' }]}>MODO ESCANEO: el código analiza las temporalidades que escribiste en "Temporalidades a escanear" y el bot decide cuál es la principal de cada señal. La entrada y la macro manuales no se usan. Para volver al modo manual, vacía ese campo.</Text>
    ) : (
      <>
        <Text style={styles.explanation}>MODO MANUAL: el script usará la temporalidad de entrada elegida. Activa la macro si tu código también necesita recibir `df_macro`. Si prefieres que el código use varias temporalidades a la vez, escríbelas en "Temporalidades a escanear" más abajo.</Text>
        <View style={styles.rowButtons}>
          <Field label="TEMPORALIDAD DE ENTRADA (ej. M15)" value={savedTfEntrada} onChangeText={setSavedTfEntrada} placeholder="M15" />
          <Field label="TEMPORALIDAD MACRO (ej. H1)" value={savedTfMacro} onChangeText={setSavedTfMacro} placeholder="H1" />
        </View>
        <View style={styles.rowButtons}>
          <Button label={savedUsaMultiTf ? 'Usar macro: ACTIVADO' : 'Usar macro: desactivado'} onPress={() => setSavedUsaMultiTf(!savedUsaMultiTf)} color={savedUsaMultiTf ? '#237a4b' : '#303b48'} />
        </View>
      </>
    )}
    <Text style={[styles.label, { marginTop: 10 }]}>ESCANEO MULTI-TEMPORALIDAD (avanzado)</Text>
    <Text style={styles.explanation}>Elige uno o varios marcos para escanear todas las oportunidades disponibles en ellos. El bot solo consulta y analiza los marcos seleccionados. "Todas" revisa las 11 temporalidades; "Manual" deja vacía la selección y usa la entrada/macro indicadas arriba.</Text>
    <View style={styles.rowButtons}>
      <Button
        label="Escanear todas"
        onPress={() => setSavedTemporalidadesEscaneo(TEMPORALIDADES_ESCANEO.join(', '))}
        color={new Set(savedTemporalidadesEscaneo.split(/[,\s]+/).map((tf) => tf.trim().toUpperCase()).filter(Boolean)).size === TEMPORALIDADES_ESCANEO.length ? '#237a4b' : '#303b48'}
      />
      <Button
        label="Modo manual"
        onPress={() => setSavedTemporalidadesEscaneo('')}
        color={!savedTemporalidadesEscaneo.trim() ? '#b88b35' : '#303b48'}
      />
    </View>
    <View style={styles.rowButtons}>
      {TEMPORALIDADES_ESCANEO.map((timeframe) => {
        const activa = savedTemporalidadesEscaneo
          .split(/[,\s]+/)
          .some((seleccionada) => seleccionada.trim().toUpperCase() === timeframe);
        return (
          <Button
            key={timeframe}
            label={timeframe}
            onPress={() => cambiarTemporalidadEscaneo(timeframe)}
            color={activa ? '#b88b35' : '#303b48'}
          />
        );
      })}
    </View>
    <Field label="TEMPORALIDADES A ESCANEAR (separadas por coma)" value={savedTemporalidadesEscaneo} onChangeText={setSavedTemporalidadesEscaneo} />
    <Text style={[styles.label, { marginTop: 10 }]}>ROL DE CADA GRUPO EN EL ESCANEO</Text>
    <View style={styles.rowButtons}>
      <Button label="Automático" onPress={() => {
        setEscaneoManual(false);
        setRolesTemporalidad({ micro: '', intermedia: '', macro: '', extra_macro: '' });
        setGruposTemporalidadTexto('');
        run('/api/config', { grupos_temporalidad: {} }, 'Escaneo automático: grupos por defecto');
      }} color={!escaneoManual ? '#237a4b' : '#303b48'} />
      <Button label="Manual" onPress={() => setEscaneoManual(true)} color={escaneoManual ? '#b88b35' : '#303b48'} />
    </View>
    <Text style={styles.explanation}>{escaneoManual ? 'Manual: tú eliges el rol de cada grupo y puedes mover una temporalidad a otro grupo.' : 'Automático: M1-M5 = micro (confirma), M15-H1 = intermedia (busca), H2-D1 = macro (busca), W1/MN1 = extra macro (contexto). Sin ajustes.'}</Text>
    <Text style={styles.explanation}>"Buscar" = ahí se detecta la zona y se puede abrir operación. "Confirmar" = solo valida el rompimiento (no abre su propia operación). "Contexto" = solo permite o bloquea según su dirección, nunca opera ahí (ideal para semanal/mensual). "No usar" = se ignora por completo.</Text>
    <Text style={styles.explanation}>Cada temporalidad cae por defecto en un grupo (M1-M5 = micro, M15-H1 = intermedia, H2-D1 = macro, W1/MN1 = extra macro). Si quieres que una de las tuyas se analice en OTRO grupo — por ejemplo, que M15 se trate como macro y no como intermedia — tócala abajo y cambia su grupo antes de asignarle el rol.</Text>
    {escaneoManual ? (() => {
      const escritas = Array.from(new Set(savedTemporalidadesEscaneo.split(/[,\s]+/).map((t) => t.trim().toUpperCase()).filter(Boolean)));
      const overrides = parseGruposTexto(gruposTemporalidadTexto);
      const noReconocidas = escritas.filter((t) => !GRUPO_TEMPORALIDAD[t] && !overrides[t]);
      const grupoDe = (tf: string) => overrides[tf] || GRUPO_TEMPORALIDAD[tf];
      const porGrupo: Record<string, string[]> = { micro: [], intermedia: [], macro: [], extra_macro: [] };
      escritas.forEach((t) => { const g = grupoDe(t); if (g && porGrupo[g]) porGrupo[g].push(t); });
      const cambiarGrupo = (tf: string, grupo: string) => {
        const esElDefecto = GRUPO_TEMPORALIDAD[tf] === grupo;
        const nuevo = { ...overrides };
        if (esElDefecto) delete nuevo[tf]; else nuevo[tf] = grupo;
        const texto = serializeGruposTexto(nuevo);
        setGruposTemporalidadTexto(texto);
        run('/api/config', { grupos_temporalidad: nuevo }, `${tf} ahora se analiza como ${grupo}`);
      };
      return (
        <>
          {noReconocidas.length ? <Text style={styles.error}>No reconozco: {noReconocidas.join(', ')} — revisa que estén separadas por coma y sean M1, M3, M5, M15, M30, H1, H2, H4, D1, W1 o MN1.</Text> : null}
          {(['micro', 'intermedia', 'macro', 'extra_macro'] as const).map((grupo) => (
            <View key={grupo} style={{ marginBottom: 10 }}>
              <Text style={styles.label}>{grupo.toUpperCase().replace('_', ' ')}{grupo === 'micro' ? ' (1-5m)' : grupo === 'intermedia' ? ' (15m-1h)' : grupo === 'macro' ? ' (2h-D1)' : ' (semanal/mensual)'}</Text>
              {porGrupo[grupo].length ? porGrupo[grupo].map((tf) => (
                <View key={tf} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                  <Text style={[styles.codeSummary, { marginBottom: 0, minWidth: 40 }]}>{tf}{overrides[tf] ? ' (cambiada)' : ''}</Text>
                  {(['micro', 'intermedia', 'macro', 'extra_macro'] as const).map((opcion) => (
                    <Button
                      key={opcion}
                      label={opcion === 'extra_macro' ? 'Extra macro' : opcion.charAt(0).toUpperCase() + opcion.slice(1)}
                      onPress={() => cambiarGrupo(tf, opcion)}
                      color={grupoDe(tf) === opcion ? '#b88b35' : '#303b48'}
                    />
                  ))}
                </View>
              )) : <Text style={styles.muted}>No escribiste ninguna temporalidad de este grupo</Text>}
              <View style={styles.rowButtons}>
                {(['buscar', 'confirmar', 'contexto', 'no_usar'] as const).map((rol) => (
                  <Button
                    key={rol}
                    label={rol === 'no_usar' ? 'No usar' : rol.charAt(0).toUpperCase() + rol.slice(1)}
                    onPress={() => setRolesTemporalidad({ ...rolesTemporalidad, [grupo]: rolesTemporalidad[grupo] === rol ? '' : rol })}
                    color={rolesTemporalidad[grupo] === rol ? '#b88b35' : '#303b48'}
                  />
                ))}
              </View>
            </View>
          ))}
        </>
      );
    })() : null}
    <Text style={[styles.label, { marginTop: 10 }]}>ZONAS DE ESPERA PERMITIDAS</Text>
    <Text style={styles.explanation}>
      {zonasPermitidas.length
        ? `Esta estrategia SOLO abre operación en las zonas ${ZONAS_ESPERA.filter((z) => zonasPermitidas.includes(z.id)).map((z) => z.nombre).join(', ')}. Las demás se ignoran.`
        : 'Automático: esta estrategia acepta cualquier zona que detecte. Selecciona una o varias para limitarla; esta elección es independiente de las demás estrategias.'}
    </Text>
    <View style={styles.rowButtons}>
      <Button label="Automático" onPress={() => setZonasPermitidas([])} color={zonasPermitidas.length === 0 ? '#237a4b' : '#303b48'} />
      {ZONAS_ESPERA.map((z) => (
        <Button
          key={z.id}
          label={z.nombre}
          onPress={() => setZonasPermitidas(zonasPermitidas.includes(z.id) ? zonasPermitidas.filter((x) => x !== z.id) : [...zonasPermitidas, z.id])}
          color={zonasPermitidas.includes(z.id) ? '#b88b35' : '#303b48'}
        />
      ))}
    </View>
    {ZONAS_ESPERA.map((z) => <Text key={z.id} style={styles.muted}>{z.nombre}: {z.ayuda}</Text>)}
    <Field label="CÓDIGO PYTHON" value={code} onChangeText={setCode} multiline code />
    <View style={styles.rowButtons}><Field label="STOP LOSS (puntos de precio)" value={savedSl} onChangeText={setSavedSl} numeric /><Field label="TAKE PROFIT" value={savedTp} onChangeText={setSavedTp} numeric /></View>
    <Text style={[styles.label, { marginTop: 4 }]}>GESTIÓN DE RIESGO</Text>
    <View style={styles.rowButtons}>
      <Button label="Lotaje fijo" onPress={() => setSavedModoRiesgo('lotaje_fijo')} color={savedModoRiesgo === 'lotaje_fijo' ? '#b88b35' : '#303b48'} />
      <Button label="% del balance" onPress={() => setSavedModoRiesgo('porcentaje')} color={savedModoRiesgo === 'porcentaje' ? '#b88b35' : '#303b48'} />
      <Button label="Monto fijo" onPress={() => setSavedModoRiesgo('monto_fijo')} color={savedModoRiesgo === 'monto_fijo' ? '#b88b35' : '#303b48'} />
    </View>
    {savedModoRiesgo === 'lotaje_fijo'
      ? <Field label="VOLUMEN (lotes)" value={savedVolume} onChangeText={setSavedVolume} numeric placeholder="0.01" />
      : <>
          <View style={styles.rowButtons}>
            <Field label={savedModoRiesgo === 'porcentaje' ? '% DEL BALANCE A ARRIESGAR' : 'DINERO A ARRIESGAR'} value={savedRiesgoValor} onChangeText={setSavedRiesgoValor} numeric />
            <Field label="PRECIO DE ENTRADA (para previsualizar)" value={savedEntryPreview} onChangeText={setSavedEntryPreview} numeric />
          </View>
          <Text style={styles.explanation}>El lotaje NO se fija a mano: el bot lo calcula solo en cada operación según {savedModoRiesgo === 'porcentaje' ? 'el % del balance' : 'el monto'} configurado y la distancia real del SL. A menor distancia de SL, mayor lotaje; a mayor distancia, menor lotaje.</Text>
          {lotajeSugerido ? <View style={styles.saved}><Text style={styles.savedTitle}>Lotaje sugerido: {lotajeSugerido.lotaje}</Text><Text style={styles.explanation}>Arriesgarías ~{lotajeSugerido.dinero_riesgo} en esta operación.</Text>{lotajeSugerido.advertencia ? <Text style={styles.error}>{lotajeSugerido.advertencia}</Text> : null}</View> : <Text style={styles.muted}>Completa precio de entrada, SL y el % o monto para ver el lotaje sugerido.</Text>}
        </>
    }
    {editando ? <Text style={[styles.explanation, { color: '#e4b75a' }]}>Editando "{editando}" — al guardar se sobrescribe (mismo nombre).</Text> : null}
    <Button label={editando ? 'Guardar cambios' : 'Guardar estrategia'} onPress={saveLibrary} color="#2563eb" />
    <Button label="Cancelar / cerrar formulario" onPress={resetFormularioEstrategia} color="#303b48" />
          </Section>
        </ScrollView>
      </View>
    </Modal>
    {saved.length ? <Button label="Borrar TODAS las estrategias" onPress={() => { if (typeof window !== 'undefined' && window.confirm('Esto borra TODAS las estrategias guardadas, incluidas las del backend. ¿Seguro?')) { run('/api/estrategias_guardadas', {}, 'Todas las estrategias borradas', 'DELETE'); } }} color="#8a2c2c" /> : null}
    {saved.map((item) => {
      const estaActiva = state?.config?.estrategia_biblioteca_activa === item.nombre;
      return <View style={styles.saved} key={item.nombre}><Text style={styles.savedTitle}>{item.nombre}</Text><Text style={styles.explanation}>{item.explicacion || 'Sin descripción'}</Text><Text style={styles.codeSummary}>Riesgo: {item.modo_riesgo === 'porcentaje' ? `${item.riesgo_valor}% del balance` : item.modo_riesgo === 'monto_fijo' ? `${item.riesgo_valor} fijo por operación` : `${item.volumen} lotes fijos`} · SL {item.sl} · TP {item.tp}</Text><Text style={styles.codeSummary}>Entrada: {item.timeframe_entrada || 'M15'} · Macro: {item.timeframe_macro || 'H1'} · Usa macro: {item.usa_multi_temporalidad ? 'sí' : 'no'}</Text><View style={styles.rowButtons}><Button label={estaActiva ? 'Desactivar' : 'Activar'} onPress={() => estaActiva ? run('/api/estrategia/reset_total', {}, `${item.nombre} desactivada`) : run(`/api/estrategias_guardadas/activar/${encodeURIComponent(item.nombre)}`, {}, `${item.nombre} activada`)} color={estaActiva ? '#8a2c2c' : '#237a4b'} /><Button label="Ver código" onPress={() => exportarGuardada(item)} color="#2563eb" /><Button label="Editar" onPress={() => editarGuardada(item)} color="#b88b35" /><Button label="Borrar" onPress={() => run(`/api/estrategias_guardadas/${encodeURIComponent(item.nombre)}`, {}, `${item.nombre} borrada`, 'DELETE')} /></View></View>;
    })}
    {state?.config?.estrategia_biblioteca_activa ? <Text style={[styles.foot, { marginTop: 8 }]}>Activa: {state.config.estrategia_biblioteca_activa}</Text> : null}
  </Section>
  <Section title="Parámetros de la estrategia SpotPlus (estructura dinámica)">
    <Text style={styles.explanation}>Aplica al motor de estructura y a la estrategia SpotPlus multitemporalidad. La ruptura fuerte se mide en ATR; la ruptura débil se clasifica como OBC. Los marcos micro confirman y la entrada se espera en el retroceso a la zona.</Text>
    <View style={styles.rowButtons}>
      <Field label="PIVOT LEN (entrada)" value={pPivotLen} onChangeText={setPPivotLen} numeric />
      <Field label="PIVOT LEN MACRO" value={pPivotLenMacro} onChangeText={setPPivotLenMacro} numeric />
    </View>
    <View style={styles.rowButtons}>
      <Field label="% DEL MOVIMIENTO MACRO PARA EL TP" value={pPorcentajeTp} onChangeText={setPPorcentajeTp} numeric />
      <Field label="COLCHÓN DE SL (% sobre la zona)" value={pSlBufferPct} onChangeText={setPSlBufferPct} numeric />
    </View>
    <View style={styles.rowButtons}>
      <Field label="RATIO TP MÍNIMO (0 = sin filtro)" value={pMinimoRatioTp} onChangeText={setPMinimoRatioTp} numeric />
      <Field label="MÁX. VELAS DESDE LA ZONA (0 = sin límite)" value={pMaxVelasDesdeZona} onChangeText={setPMaxVelasDesdeZona} numeric />
      <Field label="EXTENSIONES DE LIQUIDEZ 110%, 127%, 138%" value={pExtensionesLiquidez} onChangeText={setPExtensionesLiquidez} />
    </View>
    <View style={styles.rowButtons}>
      <Field label="CUERPO MÍNIMO DE RUPTURA FUERTE (ATR)" value={pRupturaCuerpoAtr} onChangeText={setPRupturaCuerpoAtr} numeric />
      <Field label="CIERRE MÍNIMO MÁS ALLÁ DEL NIVEL (ATR)" value={pRupturaCierreAtr} onChangeText={setPRupturaCierreAtr} numeric />
    </View>
    <Text style={styles.explanation}>El ratio TP mínimo es el que descarta señales cuyo TP calculado valga menos de X veces el riesgo (ej. 1.5 = solo entra si el TP potencial es al menos 1.5R). En 0, no filtra nada — deja pasar cualquier señal, aunque el TP valga muy poco frente al riesgo.</Text>
    <Button label="Guardar parámetros" onPress={() => run('/api/config', { parametros: {
      ...(state?.config?.parametros || {}),
      pivot_len: Number(pPivotLen) || 5,
      pivot_len_macro: Number(pPivotLenMacro) || 15,
      porcentaje_tp: Number(pPorcentajeTp) || 0.8,
      sl_buffer_pct: Number(pSlBufferPct) || 0.1,
      minimo_ratio_tp: Number(pMinimoRatioTp) || 0,
      max_velas_desde_zona: Number(pMaxVelasDesdeZona) || 0,
      ruptura_cuerpo_atr: Number(pRupturaCuerpoAtr) || 1,
      ruptura_cierre_atr: Number(pRupturaCierreAtr) || 0.1,
    } }, 'Parámetros de la estrategia guardados')} color="#b88b35" />
  </Section>
  <Section title="Backtesting"><Text style={styles.explanation}>Simula, vela por vela y sin mirar al futuro, cómo se habría comportado la estrategia ACTIVA del bot en un rango de fechas pasado, usando velas reales de MT5.</Text><View style={styles.rowButtons}><DateField label="DESDE" value={btFrom} onChangeText={setBtFrom} /><DateField label="HASTA" value={btTo} onChangeText={setBtTo} /></View><View style={styles.rowButtons}><TimeframeSelector label="MICRO / ENTRADA" value={btTfEntrada} onChange={setBtTfEntrada} /><TimeframeSelector label="MACRO" value={btTfMacro} onChange={setBtTfMacro} /></View><Text style={styles.explanation}>Micro define las velas donde se buscan entradas; Macro se usa para el movimiento de fondo y el TP. Solo afecta este backtest.</Text><Field label="BALANCE INICIAL" value={btBalance} onChangeText={setBtBalance} numeric /><Button label={btRunning ? `Simulando... ${btProgress.toFixed(0)}%` : 'Correr backtest'} onPress={runBacktest} color="#2563eb" />
  {btRunning ? <View style={styles.progressTrack}><View style={[styles.progressFill, { width: `${btProgress}%` }]} /></View> : null}
  {btResult && 'error' in btResult ? <Text style={styles.error}>{btResult.error}</Text> : null}
  {btResult && !('error' in btResult) ? <View style={styles.saved}>
    <Text style={styles.savedTitle}>{btResult.simbolo} · {btResult.desde} → {btResult.hasta}</Text>
    <View style={styles.row}><Text style={styles.label}>Balance final</Text><Text style={styles.value}>{btResult.balance_final} (inicial {btResult.balance_inicial})</Text></View>
    <View style={styles.row}><Text style={styles.label}>Ganancia total</Text><Text style={[styles.value, btResult.profit_total >= 0 ? styles.green : styles.red]}>{btResult.profit_total}</Text></View>
    <View style={styles.row}><Text style={styles.label}>Operaciones</Text><Text style={styles.value}>{btResult.total_operaciones} ({btResult.ganadoras} ganadoras / {btResult.perdedoras} perdedoras)</Text></View>
    <View style={styles.row}><Text style={styles.label}>Tasa de acierto</Text><Text style={styles.value}>{btResult.tasa_acierto}%</Text></View>
    <View style={styles.row}><Text style={styles.label}>Drawdown máximo</Text><Text style={styles.value}>{btResult.max_drawdown}</Text></View>
    {btResult.posicion_abierta_al_final ? <View style={styles.position}><Text style={styles.value}>POSICIÓN ABIERTA AL FINAL DEL PERIODO · {btResult.posicion_abierta_al_final.tipo.toUpperCase()} · {btResult.posicion_abierta_al_final.entrada}</Text><Text style={styles.muted}>No cuenta como operación cerrada</Text></View> : null}
    <Text style={styles.foot}>{btResult.velas_usadas} velas simuladas · Entrada {btResult.timeframe_entrada || btTfEntrada} · Macro {btResult.timeframe_macro || btTfMacro}</Text>
    {btResult.diagnostico ? <View style={styles.position}>
      <Text style={styles.subTitle}>DIAGNÓSTICO DE ESTRATEGIA</Text>
      <Text style={styles.muted}>Activa: {btResult.diagnostico.estrategia_activa} · {btResult.diagnostico.tipo_estrategia}</Text>
      <Text style={styles.muted}>Marcos configurados: {btResult.diagnostico.timeframes_configurados.join(', ') || 'ninguno'}</Text>
      <Text style={styles.muted}>Marcos recibidos: {btResult.diagnostico.timeframes_recibidos.join(', ') || 'ninguno'}</Text>
      {btResult.diagnostico.timeframes_faltantes.length ? <Text style={styles.red}>Falta histórico para: {btResult.diagnostico.timeframes_faltantes.join(', ')}</Text> : null}
      <Text style={styles.muted}>Evaluaciones: {btResult.diagnostico.evaluaciones_estrategia} · Señales: {btResult.diagnostico.senales_detectadas} · Oportunidades: {btResult.diagnostico.oportunidades_detectadas}</Text>
      <Text style={styles.muted}>Entradas ejecutadas: {btResult.diagnostico.entradas_ejecutadas} · Señales repetidas ignoradas: {btResult.diagnostico.senales_ignoradas_misma_direccion}</Text>
      {btResult.diagnostico.senales_detectadas === 0 ? <Text style={styles.foot}>{btResult.diagnostico.tipo_estrategia === 'ninguna' ? 'No hay una estrategia activa configurada para este backtest.' : `La estrategia se evaluó ${btResult.diagnostico.evaluaciones_estrategia} veces, pero no emitió señales de compra/venta en este rango.`}</Text> : null}
    </View> : null}
    <Button label="▶ Ver replay visual" onPress={() => setBtShowReplay(true)} color="#b88b35" />
    {btResult.operaciones.length ? <Text style={[styles.subTitle, { marginTop: 14 }]}>ÚLTIMAS OPERACIONES</Text> : null}
    {btResult.operaciones.slice(-15).reverse().map((t, i) => {
      const motivo = t.motivo_cierre || (t.be_aplicado && t.salida === t.entrada ? 'be' : t.salida === t.sl ? 'sl' : t.salida === t.tp ? 'tp' : 'cierre');
      const etiquetaMotivo = motivo === 'sl' ? 'STOP LOSS' : motivo === 'tp' ? 'TAKE PROFIT' : motivo === 'be' ? 'BREAKEVEN' : 'CIERRE';
      return <View style={styles.position} key={`${t.hora_cierre}-${i}`}><Text style={styles.value}>{t.tipo.toUpperCase()} · {t.entrada} → {t.salida} · {t.volumen} lotes</Text><Text style={[styles.muted, motivo === 'tp' || motivo === 'be' ? styles.green : styles.red]}>{etiquetaMotivo} · {t.profit >= 0 ? '+' : ''}{t.profit} · {t.hora_cierre}</Text></View>;
    })}
  </View> : null}
  </Section>
  {btShowReplay && btResult && !('error' in btResult) ? (
    <ReplayBacktest velas={btResult.velas} operaciones={btResult.operaciones} diagnostico={btResult.diagnostico} simbolo={btResult.simbolo} desde={btResult.desde} hasta={btResult.hasta} indiceInicio={btResult.indice_inicio_replay} timeframeEntrada={btResult.timeframe_entrada || btTfEntrada} timeframeMacro={btResult.timeframe_macro || btTfMacro} onClose={() => setBtShowReplay(false)} />
  ) : null}
  </>}
  {tab === 'operar' && <>
  <Section title="Operar manualmente"><Text style={styles.label}>TIPO DE ORDEN</Text><View style={styles.rowButtons}><Button label="Mercado" onPress={() => setOrderType('mercado')} color={orderType === 'mercado' ? '#b88b35' : '#303b48'} /><Button label="Límite" onPress={() => setOrderType('limite')} color={orderType === 'limite' ? '#b88b35' : '#303b48'} /></View>{orderType === 'limite' ? <Field label="PRECIO DE ENTRADA" value={entryPrice} onChangeText={setEntryPrice} numeric /> : null}<View style={styles.rowButtons}><Field label="VOLUMEN" value={volume} onChangeText={setVolume} numeric /><Field label="STOP LOSS" value={sl} onChangeText={setSl} numeric /></View><Field label="TAKE PROFIT" value={tp} onChangeText={setTp} numeric /><View style={styles.rowButtons}><Button label={sendingOrder ? 'Enviando...' : 'Comprar'} onPress={() => order('compra')} color="#237a4b" /><Button label={sendingOrder ? 'Enviando...' : 'Vender'} onPress={() => order('venta')} color="#a84343" /></View><Text style={styles.orderHint}>La orden requiere confirmación y se envía a MetaTrader 5.</Text></Section>
  <Section title={`Posiciones abiertas (${positions.length})`}>{positions.length ? positions.map((item) => <Pressable style={styles.position} key={item.ticket} onPress={() => setPosicionSeleccionada(item)}><View style={styles.positionHeader}><Text style={[styles.badge, item.tipo === 'compra' ? styles.buyBadge : styles.sellBadge]}>{item.tipo.toUpperCase()}</Text><Text style={styles.positionName}>{item.simbolo} · {item.volumen} lotes</Text><Pressable onPress={() => run(`/api/cerrar/${item.ticket}`, {}, 'Posición cerrada')}><Text style={styles.close}>Cerrar</Text></Pressable></View><Text style={styles.muted}>Apertura {item.precio_apertura} · Actual {item.precio_actual} · P/L real: {item.profit} {account?.moneda || ''}</Text><Text style={styles.foot}>Toca para ver el motivo y contexto</Text></Pressable>) : <Text style={styles.muted}>Sin posiciones abiertas. Si el bot dice "CONFIRMADA — YA EJECUTADA" y aquí no aparece nada, revisa el mensaje de "Última orden del bot" arriba — seguro MT5 la rechazó (volumen mínimo, stops muy cerca, símbolo no visible, etc).</Text>}</Section>
  {posicionSeleccionada ? <Modal visible transparent animationType="slide" onRequestClose={() => setPosicionSeleccionada(null)}><Pressable style={{ flex: 1, backgroundColor: 'rgba(0, 0, 0, 0.7)', justifyContent: 'center', padding: 20 }} onPress={() => setPosicionSeleccionada(null)}><Pressable style={{ backgroundColor: '#151b23', borderWidth: 1, borderColor: '#33404d', borderRadius: 8, padding: 18, maxWidth: 600, width: '100%', alignSelf: 'center' }} onPress={(event) => event.stopPropagation()}><Text style={styles.savedTitle}>Detalle de operación · {posicionSeleccionada.ticket}</Text><Text style={styles.explanation}>MT5 informa que esta operación está abierta. Entrada: {posicionSeleccionada.tipo.toUpperCase()} a {posicionSeleccionada.precio_apertura} · Precio actual: {posicionSeleccionada.precio_actual} · P/L: {posicionSeleccionada.profit} {account?.moneda || ''}</Text><Text style={styles.muted}>Abierta: {formatearHora12(posicionSeleccionada.hora_apertura)}</Text><Text style={styles.label}>CONTEXTO DE LA ESTRATEGIA</Text><Text style={styles.explanation}>Micro / entrada: {state?.config?.timeframe_entrada || 'no disponible'} · Macro: {state?.config?.timeframe_macro || 'no disponible'}</Text>{state?.estado_zona?.estado === 'esperando_confirmacion' ? <Text style={{ color: '#e4b75a', backgroundColor: '#3c3020', borderRadius: 6, padding: 10, marginVertical: 10 }}>Ahora hay una zona de espera tocada ({state.estado_zona.tipo || 'sin tipo'}), pero todavía falta confirmación BOS.</Text> : state?.estado_zona?.estado === 'confirmada' ? <Text style={styles.message}>Ahora hay una estructura confirmada: {state.estado_zona.tipo || 'señal'}.</Text> : <Text style={styles.muted}>Ahora no hay una zona de espera activa. El estado actual no sustituye al historial de la entrada.</Text>}{state?.ultima_orden && !state.ultima_orden.ok ? <Text style={styles.error}>Último intento rechazado por MT5: {state.ultima_orden.error || 'sin detalle'}</Text> : null}<Button label="Cerrar detalle" onPress={() => setPosicionSeleccionada(null)} color="#8a2c2c" /></Pressable></Pressable></Modal> : null}
  </>}
  {tab === 'calculadora' && <>
  <Section title="Calculadora de riesgo">
    <Text style={styles.explanation}>Calcula el lotaje según tu balance real, sin necesidad de guardar ninguna estrategia. Usa el mismo motor que el bot para operar.</Text>
    <View style={styles.rowButtons}><Field label="PRECIO DE ENTRADA" value={calcEntrada} onChangeText={setCalcEntrada} numeric /><Field label="STOP LOSS" value={calcSl} onChangeText={setCalcSl} numeric /></View>
    <Field label="TAKE PROFIT (opcional, para ver ganancia estimada)" value={calcTp} onChangeText={setCalcTp} numeric />
    <View style={styles.rowButtons}><Button label="% del balance" onPress={() => setCalcModo('porcentaje')} color={calcModo === 'porcentaje' ? '#b88b35' : '#303b48'} /><Button label="Monto fijo" onPress={() => setCalcModo('fijo')} color={calcModo === 'fijo' ? '#b88b35' : '#303b48'} /></View>
    <Field label={calcModo === 'porcentaje' ? '% A ARRIESGAR' : 'MONTO FIJO A ARRIESGAR'} value={calcValor} onChangeText={setCalcValor} numeric />
    <Button label="Calcular" onPress={calcularRiesgoManual} color="#237a4b" />
    {calcResultado ? <View style={[styles.saved, { marginTop: 12 }]}>
      <Text style={styles.savedTitle}>Lotaje sugerido: {calcResultado.lotaje}</Text>
      <Text style={styles.explanation}>Arriesgas ~{calcResultado.dinero_riesgo} {account?.moneda || ''} · distancia al SL: {calcResultado.distancia_sl}</Text>
      {calcResultado.perdida_estimada !== undefined ? <Text style={styles.explanation}>Pérdida estimada: {calcResultado.perdida_estimada} {account?.moneda || ''}{calcResultado.riesgo_real_pct != null ? ` (${calcResultado.riesgo_real_pct}% del balance)` : ''}</Text> : null}
      {calcResultado.ganancia_estimada ? <Text style={styles.explanation}>Ganancia estimada al TP: {calcResultado.ganancia_estimada} {account?.moneda || ''} · R:R 1:{calcResultado.ratio_rr}</Text> : null}
      {calcResultado.oportunidades ? <Text style={styles.muted}>Oportunidades con este riesgo: {calcResultado.oportunidades}</Text> : null}
      {calcResultado.advertencia ? <Text style={styles.error}>{calcResultado.advertencia}</Text> : null}
      <Button label="Usar este lote en Operar" onPress={usarCalculoEnOperar} color="#2563eb" />
    </View> : null}
  </Section>
  </>}
  {tab === 'historial' && <>
  <Section title="Estadísticas (últimos 30 días)">{estadisticas ? <>
    {[['Operaciones totales', String(estadisticas.total_operaciones)], ['Ganadoras', String(estadisticas.ganadoras)], ['Perdedoras', String(estadisticas.perdedoras)], ['Winrate', `${estadisticas.winrate_pct}%`], ['Ganancia total', `${estadisticas.ganancia_total} ${account?.moneda || ''}`], ['Mejor operación', `${estadisticas.mejor_operacion} ${account?.moneda || ''}`], ['Peor operación', `${estadisticas.peor_operacion} ${account?.moneda || ''}`], ['Profit factor', estadisticas.profit_factor != null ? String(estadisticas.profit_factor) : '—'], ['Racha actual', estadisticas.racha_actual > 0 ? `${estadisticas.racha_actual} ganadoras seguidas` : estadisticas.racha_actual < 0 ? `${Math.abs(estadisticas.racha_actual)} perdedoras seguidas` : 'sin racha']].map(([label, value]) => <View style={styles.row} key={label}><Text style={styles.label}>{label}</Text><Text style={styles.value}>{value}</Text></View>)}
  </> : <Text style={styles.muted}>Cargando estadísticas...</Text>}</Section>
  <Section title="Bitácora de operaciones (automática)">
    <Text style={styles.explanation}>Cada operación (del bot o manual) se registra sola: queda EN CURSO mientras está abierta y, al cerrar, se completa con el resultado final (TP, SL, BE o manual).</Text>
    {diarioStats && diarioStats.total.operaciones > 0 ? <View style={[styles.saved, { marginBottom: 10 }]}>
      <Text style={styles.savedTitle}>{diarioStats.total.operaciones} cerradas · winrate {diarioStats.total.winrate_pct}% · {diarioStats.total.profit} {account?.moneda || ''}</Text>
      <Text style={styles.explanation}>{Object.entries(diarioStats.por_resultado).map(([k, v]) => `${k}: ${v.operaciones} (${v.profit})`).join('  ·  ')}</Text>
      <Text style={styles.muted}>{Object.entries(diarioStats.por_origen).map(([k, v]) => `${k}: ${v.operaciones} ops, ${v.winrate_pct}%`).join('  ·  ')}</Text>
    </View> : null}
    {diario.length === 0 ? <Text style={styles.muted}>Todavía no hay operaciones registradas.</Text> : null}
    {diario.slice(0, 40).map((o) => { const enCurso = o.estado === 'en_curso'; const gano = (o.profit ?? 0) > 0; return <View style={styles.position} key={o.id}>
      <View style={styles.positionHeader}><Text style={[styles.badge, o.tipo === 'compra' ? styles.buyBadge : styles.sellBadge]}>{o.tipo.toUpperCase()}</Text><Text style={styles.positionName}>{o.simbolo} · {o.volumen} lotes</Text><Text style={{ color: enCurso ? '#e4b75a' : gano ? '#4cc38a' : '#e5646b', fontWeight: '700' }}>{enCurso ? 'EN CURSO' : `${o.resultado}${o.profit != null ? ` ${o.profit > 0 ? '+' : ''}${o.profit}` : ''}`}</Text></View>
      <Text style={styles.muted}>Entrada {o.entrada ?? '—'} · SL {o.sl ?? '—'} · TP {o.tp ?? '—'}{o.origen ? ` · ${o.origen}` : ''}</Text>
      <Text style={styles.muted}>Estimado: {o.perdida_estimada != null ? `-${o.perdida_estimada}` : '—'} / {o.ganancia_estimada != null ? `+${o.ganancia_estimada}` : '—'}{o.ratio_rr ? ` · R:R 1:${o.ratio_rr}` : ''}</Text>
      {!enCurso ? <Text style={styles.muted}>Cierre {o.precio_cierre ?? '—'} · {formatearHora12(o.hora_cierre ?? 0)}{o.duracion_seg != null ? ` · duró ${Math.round(o.duracion_seg / 60)} min` : ''}</Text> : <Text style={styles.muted}>Abierta {formatearHora12(o.hora_apertura)} · ticket {o.ticket}</Text>}
    </View>; })}
  </Section>
  <Section title="Notas personales">
    <Text style={styles.explanation}>Anota tus razones al entrar/salir de una operación, aprendizajes o errores a evitar.</Text>
    {bitacora.length > 0 && <Pressable onPress={borrarTodaLaBitacora} style={{ alignSelf: 'flex-end', marginBottom: 8 }}><Text style={styles.close}>Borrar todo</Text></Pressable>}
    <Field label="NUEVA NOTA" value={notaNueva} onChangeText={setNotaNueva} multiline />
    <Button label="Agregar a la bitácora" onPress={agregarNota} color="#2563eb" />
    {bitacora.map((item) => <View style={styles.position} key={item.id}><Text style={styles.value}>{item.nota}</Text><View style={styles.positionHeader}><Text style={styles.muted}>{formatearHora12(item.hora)}{item.ticket ? ` · ticket ${item.ticket}` : ''}</Text><Pressable onPress={() => borrarNota(item.id)}><Text style={styles.close}>Borrar</Text></Pressable></View></View>)}
  </Section>
  <Section title="Historial reciente">{history.slice(0, 20).map((item) => <View style={styles.position} key={item.ticket}><Text style={styles.value}>{item.tipo.toUpperCase()} · {item.simbolo} · {item.volumen} lotes</Text><Text style={styles.muted}>{formatearHora12(item.hora)} · {item.profit}</Text></View>)}</Section>
  </>}
  {tab === 'fundamentales' && <>
  <Section title="Protección en noticias">
    <Text style={styles.explanation}>Durante una noticia de alto impacto (saliendo ahora o dentro de los minutos configurados) el bot no abre operaciones nuevas y mueve a BE las abiertas que van en ganancia. Las que van en pérdida no se tocan.</Text>
    <View style={styles.rowButtons}>
      <Button label={state?.config?.bloqueo_noticias_activo !== false ? 'No abrir en noticias: ACTIVADO' : 'No abrir en noticias: desactivado'} onPress={() => run('/api/config', { bloqueo_noticias_activo: state?.config?.bloqueo_noticias_activo === false }, 'Bloqueo por noticias actualizado')} color={state?.config?.bloqueo_noticias_activo !== false ? '#237a4b' : '#303b48'} />
      <Button label={state?.config?.noticia_be_activo !== false ? 'BE en noticias: ACTIVADO' : 'BE en noticias: desactivado'} onPress={() => run('/api/config', { noticia_be_activo: state?.config?.noticia_be_activo === false }, 'BE en noticias actualizado')} color={state?.config?.noticia_be_activo !== false ? '#237a4b' : '#303b48'} />
    </View>
  </Section>
  <Section title="Fundamentales">
    <Text style={styles.explanation}>Calendario de la semana: NFP, IPC, tasas y demás noticias de alto impacto.</Text>
    <Text style={[styles.subTitle, { marginTop: 4 }]}>EN PROCESO</Text>
    {fundamentales.en_proceso.length ? fundamentales.en_proceso.map((ev, i) => (
      <View key={`proc-${i}`} style={{ backgroundColor: '#3c3020', borderRadius: 6, padding: 10, marginBottom: 8 }}>
        <Text style={{ color: '#e4b75a', fontWeight: '800' }}>🟡 {ev.title} ({ev.country || '--'})</Text>
        <Text style={styles.muted}>{formatearHoraEvento(ev.date)} · Publicándose ahora / esperando dato</Text>
      </View>
    )) : <Text style={styles.muted}>Ninguna saliendo en este momento.</Text>}

    <Text style={[styles.subTitle, { marginTop: 14 }]}>PENDIENTES ESTA SEMANA</Text>
    {fundamentales.pendientes.length ? fundamentales.pendientes.map((ev, i) => (
      <View key={`pend-${i}`} style={styles.position}>
        <Text style={styles.value}>{ev.title} ({ev.country || '--'})</Text>
        <Text style={styles.muted}>{formatearHoraEvento(ev.date)} · Alto impacto</Text>
      </View>
    )) : <Text style={styles.muted}>Sin más noticias de alto impacto pendientes esta semana.</Text>}

    <Text style={[styles.subTitle, { marginTop: 14 }]}>YA PUBLICADAS</Text>
    {fundamentales.pasados.length ? fundamentales.pasados.map((ev, i) => {
      if (ev.sin_dato) {
        return (
          <View key={`pas-${i}`} style={styles.position}>
            <Text style={styles.value}>{ev.title} ({ev.country || '--'})</Text>
            <Text style={styles.muted}>Sin dato publicado por la fuente</Text>
            <Text style={styles.foot}>{formatearHoraEvento(ev.date)}</Text>
          </View>
        );
      }
      const color = ev.sorpresa === 'mejor' ? styles.green : ev.sorpresa === 'peor' ? styles.red : styles.muted;
      const etiqueta = ev.sorpresa === 'mejor' ? 'Mejor de lo esperado' : ev.sorpresa === 'peor' ? 'Peor de lo esperado' : ev.sorpresa === 'en_linea' ? 'En línea' : 'Sin comparación';
      return (
        <View key={`pas-${i}`} style={styles.position}>
          <Text style={styles.value}>{ev.title} ({ev.country || '--'})</Text>
          <Text style={color as any}>{etiqueta} · Actual {String(ev.actual ?? '--')} / Pronóstico {String(ev.forecast ?? '--')}</Text>
          <Text style={styles.foot}>{formatearHoraEvento(ev.date)}</Text>
        </View>
      );
    }) : <Text style={styles.muted}>Todavía no hay noticias publicadas esta semana.</Text>}
  </Section>
  </>}
  {tab === 'telegram' && <>
  <Section title="Cómo crear tu bot de Telegram">
    <Text style={styles.explanation}>1. En Telegram, busca a <Text style={{ fontWeight: '800', color: '#e1e7ed' }}>@BotFather</Text> y mándale /newbot.{'\n'}2. Ponle un nombre y un usuario (debe terminar en "bot"). Te va a dar un TOKEN largo — cópialo aquí abajo.{'\n'}3. Abre un chat con el bot que acabas de crear (búscalo por el usuario que le pusiste) y mándale cualquier mensaje, ej. "hola".{'\n'}4. Busca a <Text style={{ fontWeight: '800', color: '#e1e7ed' }}>@userinfobot</Text> y mándale /start: te va a dar tu CHAT ID (un número). Pégalo aquí abajo.{'\n'}5. Guarda y toca "Probar" — deberías recibir un mensaje del bot en Telegram.</Text>
  </Section>
  <Section title="Configuración">
    <Field label="BOT TOKEN" value={tgToken} onChangeText={setTgToken} />
    <Field label="CHAT ID" value={tgChatId} onChangeText={setTgChatId} />
    <View style={styles.rowButtons}>
      <Button label={tgActivo ? 'Notificaciones ACTIVAS' : 'Notificaciones PAUSADAS'} onPress={() => setTgActivo(!tgActivo)} color={tgActivo ? '#237a4b' : '#303b48'} />
    </View>
    <Text style={[styles.label, { marginTop: 4 }]}>QUÉ AVISOS QUIERES RECIBIR</Text>
    <View style={styles.rowButtons}>
      <Button label="Apertura de operación" onPress={() => setTgAvisoApertura(!tgAvisoApertura)} color={tgAvisoApertura ? '#b88b35' : '#303b48'} />
      <Button label="Cierre de operación" onPress={() => setTgAvisoCierre(!tgAvisoCierre)} color={tgAvisoCierre ? '#b88b35' : '#303b48'} />
      <Button label="SL/TP" onPress={() => setTgAvisoSlTp(!tgAvisoSlTp)} color={tgAvisoSlTp ? '#b88b35' : '#303b48'} />
    </View>
    <View style={styles.rowButtons}>
      <Button label="Cierres parciales" onPress={() => setTgAvisoParcial(!tgAvisoParcial)} color={tgAvisoParcial ? '#b88b35' : '#303b48'} />
      <Button label="Zona esperando confirmación" onPress={() => setTgAvisoZona(!tgAvisoZona)} color={tgAvisoZona ? '#b88b35' : '#303b48'} />
    </View>
    <View style={styles.rowButtons}>
      <Button label="Guardar" onPress={guardarTelegram} color="#2563eb" />
      <Button label={tgProbando ? 'Enviando...' : 'Guardar y probar'} onPress={probarTelegram} color="#237a4b" />
    </View>
    <Text style={styles.foot}>Ahora sí puedes elegir avisos por SL/TP: el bot detecta cierres automáticos de MT5 y puede avisarte solo cuando la posición cierra por stop loss o take profit.</Text>
  </Section>
  </>}
  <Text style={styles.footer}>API: {API_URL}</Text></ScrollView></View>;
}

const styles = StyleSheet.create({ screen: { flex: 1, backgroundColor: '#0d1117' }, content: { padding: 22, paddingTop: 58, maxWidth: 760, width: '100%', alignSelf: 'center' }, center: { flex: 1, backgroundColor: '#0d1117', alignItems: 'center', justifyContent: 'center', gap: 14 }, header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 26 }, kicker: { color: '#e4b75a', fontSize: 11, letterSpacing: 2, fontWeight: '700' }, title: { color: '#f4f1e8', fontSize: 36, fontWeight: '800' }, connection: { flexDirection: 'row', alignItems: 'center', padding: 10, borderRadius: 20 }, online: { backgroundColor: '#183b2a' }, offline: { backgroundColor: '#3c2023' }, dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#57d391', marginRight: 7 }, connectionText: { color: '#d7e3db', fontSize: 12, fontWeight: '700' }, section: { backgroundColor: '#151b23', borderWidth: 1, borderColor: '#26303b', borderRadius: 8, padding: 18, marginBottom: 14 }, sectionTitle: { color: '#f4f1e8', fontSize: 14, fontWeight: '800', letterSpacing: 1, marginBottom: 16 }, market: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 24 }, right: { alignItems: 'flex-end' }, symbol: { color: '#f4f1e8', fontSize: 28, fontWeight: '800', marginTop: 5 }, signal: { color: '#e4b75a', fontSize: 15, fontWeight: '800', marginTop: 6 }, metrics: { flexDirection: 'row', justifyContent: 'space-between', borderTopWidth: 1, borderTopColor: '#26303b', paddingTop: 16 }, muted: { color: '#8893a0', fontSize: 11, letterSpacing: 0.5 }, metric: { color: '#dce2e8', fontSize: 15, fontWeight: '700' }, green: { color: '#57d391' }, red: { color: '#ff7d7d' }, row: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#26303b' }, label: { color: '#8893a0', fontSize: 11, letterSpacing: 0.6, marginBottom: 6 }, value: { color: '#e1e7ed', fontSize: 14, fontWeight: '700' }, foot: { color: '#687481', fontSize: 11, marginTop: 13 }, field: { flex: 1, marginBottom: 10, minWidth: 120 }, input: { backgroundColor: '#0e141b', borderWidth: 1, borderColor: '#33404d', borderRadius: 6, color: '#f4f1e8', padding: 11, minHeight: 42 }, codeInput: { minHeight: 110, textAlignVertical: 'top', fontFamily: Platform.OS === 'web' ? 'monospace' : undefined }, rowButtons: { flexDirection: 'row', gap: 9, flexWrap: 'wrap' }, button: { flex: 1, borderRadius: 6, padding: 13, alignItems: 'center', marginBottom: 9, minWidth: 120 }, buttonText: { color: '#fff', fontSize: 13, fontWeight: '800' }, subTitle: { color: '#f4f1e8', fontSize: 13, fontWeight: '800', marginTop: 12, marginBottom: 8 }, explanation: { color: '#8893a0', fontSize: 12, lineHeight: 18, marginBottom: 10 }, condition: { backgroundColor: '#0e141b', borderWidth: 1, borderColor: '#33404d', borderRadius: 7, padding: 12, marginBottom: 10 }, conditionHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 }, conditionTitle: { color: '#e1e7ed', fontSize: 13, fontWeight: '700', flex: 1 }, remove: { color: '#ff7d7d', fontSize: 12 }, saved: { borderTopWidth: 1, borderTopColor: '#33404d', paddingTop: 12, marginTop: 12 }, savedTitle: { color: '#f4f1e8', fontSize: 15, fontWeight: '800', marginBottom: 5 }, codeSummary: { color: '#687481', fontSize: 11, marginBottom: 9 }, position: { paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#26303b' }, positionHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: 8 }, badge: { paddingHorizontal: 7, paddingVertical: 4, borderRadius: 4, fontSize: 10, fontWeight: '800' }, buyBadge: { backgroundColor: '#194b31', color: '#70e0a3' }, sellBadge: { backgroundColor: '#57292c', color: '#ff9999' }, positionName: { color: '#dce2e8', fontSize: 13, flex: 1, marginLeft: 8 }, close: { color: '#e4b75a', fontSize: 12, fontWeight: '800' }, message: { color: '#e4b75a', backgroundColor: '#322915', padding: 11, borderRadius: 6, marginBottom: 14 }, error: { color: '#ff9999', backgroundColor: '#3a2024', padding: 11, borderRadius: 6, marginBottom: 14 }, orderHint: { color: '#687481', fontSize: 11, marginTop: 2 }, footer: { color: '#596572', fontSize: 10, textAlign: 'center', marginVertical: 18 }, tabBar: { flexDirection: 'row', gap: 6, marginBottom: 16 }, tabButton: { flex: 1, paddingVertical: 10, borderRadius: 6, alignItems: 'center', backgroundColor: '#151b23', borderWidth: 1, borderColor: '#26303b' }, tabButtonActive: { backgroundColor: '#b88b35', borderColor: '#b88b35' }, tabButtonText: { color: '#8893a0', fontSize: 12, fontWeight: '700' }, tabButtonTextActive: { color: '#0d1117' }, progressTrack: { height: 8, backgroundColor: '#0e141b', borderRadius: 4, overflow: 'hidden', marginBottom: 12, borderWidth: 1, borderColor: '#33404d' }, progressFill: { height: '100%', backgroundColor: '#e4b75a', borderRadius: 4 }, replayBar: { padding: 16, paddingBottom: 30, backgroundColor: '#151b23', borderTopWidth: 1, borderTopColor: '#26303b' },
  dateValue: { color: '#e1e7ed', fontSize: 14 }, dateModalBackdrop: { flex: 1, backgroundColor: 'rgba(0, 0, 0, 0.65)', justifyContent: 'center', alignItems: 'center', padding: 20 }, dateModal: { backgroundColor: '#151b23', borderRadius: 8, padding: 18, alignItems: 'center' }, timeframeButtons: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 }, timeframeOption: { flexGrow: 1, flexBasis: '22%', minHeight: 40, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 7, borderWidth: 1, borderColor: '#33404d', borderRadius: 6, backgroundColor: '#0e141b' }, timeframeOptionActive: { backgroundColor: '#b88b35', borderColor: '#b88b35' }, timeframeOptionText: { color: '#8893a0', fontSize: 12, fontWeight: '800' }, timeframeOptionTextActive: { color: '#0d1117' }, replayHeader: { paddingHorizontal: 16, paddingTop: 14, paddingBottom: 10, backgroundColor: '#151b23', borderBottomWidth: 1, borderBottomColor: '#26303b' }, replayTitle: { color: '#f4f1e8', fontSize: 15, fontWeight: '800' }, replayHint: { color: '#8893a0', fontSize: 11, marginTop: 5 },
});