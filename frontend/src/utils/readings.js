/* Datos demostrativos: solo se utilizan cuando se activa expresamente «Ver demo». */
const DEMO_BASE = { co_equiv_ppm: 0.7, h2_equiv_ppm: 1.2, nh3_equiv_ppm: 0.5, etanol_equiv_ppm: 0.8, ch4_equiv_ppm: 0.6, h2s_ppm_aprox: 2.1 }
export const CAMPOS_PPM = [
  { key: 'co_equiv_ppm', nombre: 'CO', detalle: 'Monóxido de carbono · equivalente', sensor: 'MiCS-5524', color: 'cyan' },
  { key: 'h2_equiv_ppm', nombre: 'H₂', detalle: 'Hidrógeno · equivalente', sensor: 'MiCS-5524', color: 'violet' },
  { key: 'nh3_equiv_ppm', nombre: 'NH₃', detalle: 'Amoniaco · equivalente', sensor: 'MiCS-5524', color: 'cyan' },
  { key: 'etanol_equiv_ppm', nombre: 'Etanol', detalle: 'Etanol · equivalente', sensor: 'MiCS-5524', color: 'violet' },
  { key: 'ch4_equiv_ppm', nombre: 'CH₄', detalle: 'Metano · equivalente', sensor: 'MiCS-5524', color: 'cyan' },
  { key: 'h2s_ppm_aprox', nombre: 'H₂S', detalle: 'Sulfuro de hidrógeno · aproximado', sensor: 'MQ136', color: 'violet' }
]
export const DEMO_GAS = Array.from({ length: 36 }, (_, i) => {
  const variation = 1 + 0.13 * Math.sin(i * 0.4) + 0.07 * Math.cos(i * 0.19)
  return {
    _id: `demo-${i}`,
    timestamp: new Date(Date.now() - (35 - i) * 2000).toISOString(),
    ...Object.fromEntries(Object.entries(DEMO_BASE).map(([key, value]) => [key, Math.max(0, +(value * variation).toFixed(2))]))
  }
})
export const DEMO_ROVER = { mode: 'SIMULACION', connected: true, online: true, battery_v: 11.6, motors_cm_s: [0, 0, 0, 0], motor_fresh: true, estop: false }

function numeroSeguro(value) {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}
function isoFecha(value) {
  if (!value) return null
  // Mongo Extended JSON a veces devuelve { "$date": ... }
  const raw = typeof value === 'object' && value.$date ? value.$date : value
  const d = new Date(raw)
  return Number.isFinite(d.getTime()) ? d.toISOString() : null
}
/** Extrae exclusivamente concentraciones ppm del documento MongoDB. Un valor 0 sí es válido. */
export function normalizarLectura(item) {
  if (!item || typeof item !== 'object') return null
  const datos = item.data && typeof item.data === 'object' ? item.data : item
  const lectura = {}
  let cantidad = 0
  for (const { key } of CAMPOS_PPM) {
    lectura[key] = numeroSeguro(datos[key])
    if (lectura[key] !== null) cantidad++
  }
  if (!cantidad) return null
  const id = item._id?.$oid ?? item._id ?? item.id ?? ''
  return {
    _id: String(id),
    timestamp: isoFecha(item.fecha_recepcion ?? item.timestamp ?? item.fecha ?? datos.fecha_recepcion ?? datos.timestamp),
    ...lectura
  }
}
export function extraerLectura(payload) {
  return normalizarLectura(payload?.latest ?? payload?.reading ?? payload?.lectura ?? payload?.data ?? payload)
}
export function extraerHistorico(payload) {
  const raw = Array.isArray(payload) ? payload : (payload?.history ?? payload?.readings ?? payload?.lecturas ?? payload?.data ?? [])
  return (Array.isArray(raw) ? raw : []).map(normalizarLectura).filter(Boolean)
    .sort((a, b) => (Date.parse(a.timestamp || '') || 0) - (Date.parse(b.timestamp || '') || 0))
}
export function fechaLocal(fecha) {
  if (!fecha) return 'Sin fecha'
  const d = new Date(fecha)
  return Number.isFinite(d.getTime()) ? d.toLocaleString('es-MX') : 'Sin fecha'
}
export function mostrarPpm(valor) {
  if (valor === null || valor === undefined || !Number.isFinite(Number(valor))) return '—'
  const n = Number(valor)
  return new Intl.NumberFormat('es-MX', { maximumFractionDigits: 2 }).format(n)
}
/** CSV limitado a las 6 concentraciones ppm y su instante. Nunca exporta ADC ni voltios. */
export function csvGases(lecturas) {
  const keys = CAMPOS_PPM.map(c => c.key)
  const lines = [['fecha_recepcion', ...keys].join(',')]
  for (const l of lecturas) lines.push([l.timestamp || '', ...keys.map(k => l[k] ?? '')].join(','))
  return '\uFEFF' + lines.join('\r\n')
}
export function descargarCsv(lecturas) {
  if (!lecturas.length) return false
  const url = URL.createObjectURL(new Blob([csvGases(lecturas)], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `secrover_ppm_${new Date().toISOString().slice(0, 10)}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
  return true
}
