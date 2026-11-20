import React, { useEffect, useState } from 'react'
import { getJson } from '../services/secroverApi.js'
import { CAMPOS_PPM, DEMO_GAS, fechaLocal, mostrarPpm } from '../utils/readings.js'

export default function HistorialGases({ demo }) {
  const [cursores, setCursores] = useState([''])
  const [revision, setRevision] = useState(0)
  const [resultado, setResultado] = useState(null)
  const pagina = cursores.length
  const cursor = cursores[pagina - 1]
  const solicitud = JSON.stringify([pagina, cursor, revision])

  useEffect(() => {
    const controlador = new AbortController()
    async function cargar() {
      try {
        let filas, siguiente
        if (demo) {
          const inicio = (pagina - 1) * 60
          filas = DEMO_GAS.slice().reverse().slice(inicio, inicio + 60)
          siguiente = inicio + 60 < DEMO_GAS.length ? String(inicio + 60) : null
        } else {
          const query = new URLSearchParams({ before: cursor })
          const datos = await getJson('/api/gases/history/page?' + query, { signal: controlador.signal })
          filas = datos.history
          siguiente = datos.next_cursor
        }
        if (!controlador.signal.aborted) setResultado({ solicitud, filas, siguiente })
      } catch (err) {
        if (!controlador.signal.aborted) setResultado({ solicitud, error: err.message || 'Error de conexión' })
      }
    }
    cargar()
    return () => controlador.abort()
  }, [demo, pagina, cursor, solicitud])

  const actual = resultado?.solicitud === solicitud ? resultado : null
  const filas = actual?.filas ?? []
  return <section className="panel roomy">
    <div className="panel-head"><h2>Historial de concentraciones (ppm)</h2>
      <span className="tag">{demo ? 'Demo' : 'MongoDB'}</span></div>
    <div className="table-scroll"><table><thead><tr><th>Fecha / hora</th>
      {CAMPOS_PPM.map(c => <th key={c.key}>{c.nombre} (ppm)</th>)}</tr></thead><tbody>
      {filas.map((r,i) => <tr key={r._id || i}><td>{fechaLocal(r.timestamp ?? r.fecha_recepcion)}</td>
        {CAMPOS_PPM.map(c => <td key={c.key}>{mostrarPpm(r[c.key])}</td>)}</tr>)}
      {!filas.length && <tr><td colSpan={1 + CAMPOS_PPM.length}>{!actual ? 'Cargando registros…' :
        actual.error ? 'Error al consultar MongoDB: ' + actual.error : 'Sin registros.'}</td></tr>}
    </tbody></table></div>
    <div className="gas-day-pages">
      <span aria-live="polite">Página {pagina}{actual?.filas && ' · ' + filas.length + ' registros'}</span>
      <button className="btn btn-outline" disabled={pagina === 1} onClick={() => setCursores(c => c.slice(0,-1))}>← Más recientes</button>
      <button className="btn btn-outline" disabled={!actual?.siguiente} onClick={() => setCursores(c => [...c, actual.siguiente])}>Más antiguos →</button>
      <button className="btn btn-outline" onClick={() => setRevision(r => r + 1)}>Actualizar página</button>
    </div>
  </section>
}
