/** API SAME-ORIGIN. Vite usa proxy /api y /ws en desarrollo. */
export function tokenActual() {
  try { return sessionStorage.getItem('secrover-token') || '' }
  catch { return '' }
}
export function guardarToken(token) {
  if (token) sessionStorage.setItem('secrover-token', token)
  else sessionStorage.removeItem('secrover-token')
}
export function wsUrl(ruta) {
  return `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${ruta}`
}
export async function getJson(ruta, { signal } = {}) {
  const headers = {}
  const token = tokenActual()
  if (token) headers.Authorization = `Bearer ${token}`
  const respuesta = await fetch(ruta, { headers, signal, cache: 'no-store' })
  if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status}: ${ruta}`)
  return respuesta.json()
}
export async function getEstado(signal) {
  return getJson('/api/status', { signal })
}
export async function getUltimosGases(signal) {
  return getJson('/api/gases/latest', { signal })
}
export async function getHistoricoGases(limite = 60, signal) {
  return getJson(`/api/gases/history?limit=${Math.min(240, Math.max(1, Number(limite) || 60))}`, { signal })
}

/** Operaciones autenticadas de rondín y grabación. */
export async function postJson(ruta, cuerpo = {}) {
  const token = tokenActual()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const respuesta = await fetch(ruta, {
    method: 'POST', headers, body: JSON.stringify(cuerpo), cache: 'no-store'
  })
  const data = await respuesta.json().catch(() => ({}))
  if (!respuesta.ok) throw new Error(data.detail || `HTTP ${respuesta.status}`)
  return data
}
export const getRondinEstado = () => getJson('/api/rondines/status')
export const getRondinHistorial = () => getJson('/api/rondines/history?limit=50')
export const iniciarRondin = () => postJson('/api/rondines/start')
export const finalizarRondin = id => postJson('/api/rondines/stop', { id })
export const ticketGrabacion = id => getJson(`/api/rondines/${encodeURIComponent(id)}/ticket`)
export const urlGrabacion = (id, ticket, download = false) =>
  `/api/rondines/${encodeURIComponent(id)}/video?ticket=${encodeURIComponent(ticket)}${download ? '&download=true' : ''}`
