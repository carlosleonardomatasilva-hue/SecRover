import React, { useEffect, useMemo, useState } from 'react'
import GasPanel from './GasPanel.jsx'
import { getJson } from '../services/secroverApi.js'
import { CAMPOS_PPM, DEMO_GAS, csvGases, extraerHistorico, fechaLocal, mostrarPpm } from '../utils/readings.js'

// Construye la fecha en hora LOCAL para evitar que UTC cambie el día elegido.
function fechaLocalISO(fecha) {
  const year = fecha.getFullYear()
  const month = String(fecha.getMonth() + 1).padStart(2, '0')
  const day = String(fecha.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
function desplazarDia(dia, dias) {
  const [y, m, d] = dia.split('-').map(Number)
  return fechaLocalISO(new Date(y, m - 1, d + dias))
}
function ventanaUTC(dia) {
  const [y, m, d] = dia.split('-').map(Number)
  const inicio = new Date(y, m - 1, d)
  const fin = new Date(y, m - 1, d + 1)
  if (!Number.isFinite(inicio.getTime()) || !Number.isFinite(fin.getTime())) throw new Error('Fecha incorrecta')
  return { start: inicio.toISOString(), end: fin.toISOString() }
}
function bajarCsvDia(rows, dia) {
  if (!rows.length) return
  const url = URL.createObjectURL(new Blob([csvGases(rows)], { type: 'text/csv;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `secrover_promedio_minuto_${dia}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

/** Única vista de sensores: el selector de día controla gráficas, tabla y exportación. */
export default function DailyGasPanel({ demo = false, tokenVersion = 0 }) {
  const [dia, setDia] = useState(() => fechaLocalISO(new Date()))
  const [resultado, setResultado] = useState({ history: [], total_readings: 0, minute_buckets: 0 })
  const [cargando, setCargando] = useState(true)
  const [paginaTabla, setPaginaTabla] = useState(0)
  const [error, setError] = useState('')
  const hoy = fechaLocalISO(new Date())
  const seleccionarDia = nuevoDia => { setPaginaTabla(0); setDia(nuevoDia) }

  useEffect(() => {
    const controller = new AbortController()
    let vivo = true
    async function cargarDia() {
      if (demo) {
        const datos = DEMO_GAS.filter(d => fechaLocalISO(new Date(d.timestamp)) === dia)
        if (vivo) {
          setResultado({ history: datos, total_readings: datos.length, minute_buckets: datos.length })
          setCargando(false); setError('')
        }
        return
      }
      if (vivo) setCargando(true)
      try {
        const { start, end } = ventanaUTC(dia)
        const query = new URLSearchParams({ start, end }).toString()
        const response = await getJson(`/api/gases/day?${query}`, { signal: controller.signal })
        if (!vivo) return
        setResultado({
          history: extraerHistorico(response),
          total_readings: response.total_readings ?? 0,
          minute_buckets: response.minute_buckets ?? 0,
        })
        setError('')
      } catch (err) {
        if (!vivo || controller.signal.aborted) return
        setResultado({ history: [], total_readings: 0, minute_buckets: 0 })
        setError(err.message || 'No se pudo obtener el día seleccionado')
      } finally { if (vivo) setCargando(false) }
    }
    cargarDia()
    // El día actual se actualiza; los días anteriores son históricos e inmutables.
    const timer = setInterval(() => {
      if (dia === fechaLocalISO(new Date())) cargarDia()
    }, 30000)
    return () => { vivo = false; controller.abort(); clearInterval(timer) }
  }, [dia, demo, tokenVersion])

  const historial = resultado.history
  const lectura = historial.length ? historial[historial.length - 1] : null
  const data = useMemo(() => ({ latest: lectura, history: historial }), [historial, lectura])
  const permitePosterior = dia < hoy
  const paginasTabla = Math.max(1, Math.ceil(historial.length / 50))
  const filasTabla = useMemo(() => historial.slice().reverse().slice(paginaTabla * 50, (paginaTabla + 1) * 50), [historial, paginaTabla])

  return <div className="gas-day-view">
    <div className="sensor-heading gas-day-heading">
      <div>
        <span className="section-kicker">HISTORIAL POR FECHA</span>
        <p className="muted">Selecciona un día para consultar sus datos en MongoDB. Hora local de este dispositivo.</p>
      </div>
      <div className="gas-day-controls">
        <button type="button" className="btn btn-outline btn-compact" onClick={() => seleccionarDia(desplazarDia(dia, -1))} aria-label="Día anterior">← Anterior</button>
        <label className="gas-day-picker">Día
          <input type="date" aria-label="Seleccionar día de las gráficas" max={hoy} value={dia} onChange={e => e.target.value && seleccionarDia(e.target.value)}/>
        </label>
        <button type="button" className="btn btn-outline btn-compact" disabled={!permitePosterior} onClick={() => seleccionarDia(desplazarDia(dia, 1))} aria-label="Día siguiente">Siguiente →</button>
        <button type="button" className="btn btn-outline btn-compact" disabled={dia === hoy} onClick={() => seleccionarDia(hoy)}>Hoy</button>
        <button type="button" className="btn btn-outline btn-compact" disabled={!historial.length || cargando} onClick={() => bajarCsvDia(historial, dia)}>↓ Exportar CSV del día</button>
      </div>
    </div>
    {cargando && <p className="muted" role="status">Consultando lecturas del {dia}...</p>}
    {error && <p className="error-inline" role="alert">Error al consultar el día: {error}. Verifica que el backend tenga /api/gases/day.</p>}
    {!cargando && !error && <p className="gas-day-summary">
      <b>{dia}</b> · {resultado.total_readings.toLocaleString('es-MX')} lecturas · {resultado.minute_buckets.toLocaleString('es-MX')} intervalos de un minuto.
      {historial.length === 0 && ' No hay registros para esta fecha.'}
    </p>}
    <GasPanel data={data} demo={demo}/>
    <section className="panel roomy">
      <div className="panel-head"><div><span className="section-kicker">DATOS DE MONGO / {dia}</span><h2>Promedios por minuto (ppm estimadas)</h2></div><span className="tag">{demo ? 'DEMO' : 'Solo lectura'}</span></div>
      <p className="small muted">La consulta abarca todo el día seleccionado. Para evitar saturar la Orange Pi, cada punto es el promedio de las lecturas de un minuto; los datos originales permanecen en MongoDB.</p>
      <div className="table-scroll"><table><thead><tr><th>Fecha / hora</th>{CAMPOS_PPM.map(c => <th key={c.key}>{c.nombre} (ppm)</th>)}</tr></thead>
        <tbody>{historial.length ? filasTabla.map((r,i) => <tr key={r._id || i}><td>{fechaLocal(r.timestamp)}</td>{CAMPOS_PPM.map(c => <td key={c.key}>{mostrarPpm(r[c.key])}</td>)}</tr>) : <tr><td colSpan={CAMPOS_PPM.length + 1} className="muted">No hay lecturas del día seleccionado.</td></tr>}</tbody></table></div>
      {historial.length > 50 && <div className="gas-day-pages">
        <span className="small muted">Página {paginaTabla + 1} de {paginasTabla} · 50 minutos por página. El CSV incluye el día completo.</span>
        <button type="button" className="btn btn-outline btn-compact" disabled={paginaTabla === 0} onClick={() => setPaginaTabla(p => p - 1)}>← Más recientes</button>
        <button type="button" className="btn btn-outline btn-compact" disabled={paginaTabla + 1 >= paginasTabla} onClick={() => setPaginaTabla(p => p + 1)}>Más antiguos →</button>
      </div>}
    </section>
  </div>
}
