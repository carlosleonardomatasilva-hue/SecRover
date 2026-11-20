import React, { useMemo, useState } from 'react'
import Sparkline from './Sparkline.jsx'
import { CAMPOS_PPM, fechaLocal, mostrarPpm } from '../utils/readings.js'

// Son rangos de PRESENTACIÓN, no umbrales de riesgo ni mediciones certificadas.
// CH4: 2.000 ppm es una elección visual, NO un máximo calibrado del MiCS-5524.
const RANGOS_VISUALES = {
  co_equiv_ppm: { detalle: 10, referencia: 1000, nominal: 1000 },
  h2_equiv_ppm: { detalle: 10, referencia: 1000, nominal: 1000 },
  nh3_equiv_ppm: { detalle: 10, referencia: 500, nominal: 500 },
  etanol_equiv_ppm: { detalle: 20, referencia: 500, nominal: 500 },
  ch4_equiv_ppm: { detalle: 20, referencia: 2000, nominal: null },
  h2s_ppm_aprox: { detalle: 50, referencia: 200, nominal: 200 },
}

// Bandas didácticas del informe SecRover, aplicadas al dato mostrado.
const BANDAS = {
  co_equiv_ppm: [25, 200, 1, '25 ppm: promedio de 8 h. 200 ppm: techo NIOSH.'],
  h2_equiv_ppm: [4000, 10000, 1, 'Referencias de inflamabilidad: 10 % y 25 % del LEL.'],
  nh3_equiv_ppm: [25, 35, 1, 'Referencias de exposición: 25 ppm en 8 h y 35 ppm en 15 min.'],
  etanol_equiv_ppm: [1000, 3300, 10, '1,000 ppm: exposición. 3,300 ppm: 10 % del LEL.'],
  ch4_equiv_ppm: [5000, 12500, 1000, 'Referencias de inflamabilidad: 10 % y 25 % del LEL.'],
  h2s_ppm_aprox: [1, 5, 1, 'Referencias de exposición: 1 ppm en 8 h y 5 ppm en 15 min.'],
}

function nivelGas(key, valor, calibrado) {
  if (!Number.isFinite(valor) || valor < 0) return 'Sin dato válido'
  if (key === 'h2s_ppm_aprox' && calibrado === false) return 'Revisar calibración'
  const [medio, alto, minimo] = BANDAS[key]
  if (key === 'ch4_equiv_ppm') return 'Sin rango cuantitativo validado'
  if (valor < minimo) return 'Por debajo del rango nominal'
  if (valor > RANGOS_VISUALES[key].nominal) return 'Fuera del rango nominal'
  return valor >= alto ? 'Alto' : valor >= medio ? 'Medio' : 'Bajo'
}

function filtrarTiempo(historial, minutos) {
  if (minutos === 'todo' || !historial.length) return historial
  const timestamps = historial.map(f => Date.parse(f.timestamp)).filter(Number.isFinite)
  if (!timestamps.length) return historial
  // Anclar al registro más reciente (no a Date.now): también permite explorar muestras históricas.
  const ultimo = Math.max(...timestamps)
  const desde = ultimo - Number(minutos) * 60_000
  return historial.filter(f => {
    const t = Date.parse(f.timestamp)
    return Number.isFinite(t) && t >= desde && t <= ultimo
  })
}

function GraficaBandas({ historial, campo, nombre, medio, alto }) {
  const izquierda = 58, derecha = 352, arriba = 14, base = 178
  const maxY = alto * 1.35
  const y = ppm => base - Math.min(ppm / maxY, 1) * (base - arriba)
  const filas = historial.map(r => ({ t: Date.parse(r.timestamp), v: r[campo] }))
    .filter(p => Number.isFinite(p.t)).sort((a, b) => a.t - b.t)
  const inicio = filas[0]?.t, fin = filas.at(-1)?.t
  const x = t => inicio === fin ? (izquierda + derecha) / 2 :
    izquierda + (t - inicio) / (fin - inicio) * (derecha - izquierda)
  const valido = p => Number.isFinite(p.v) && p.v >= 0
  const puntos = filas.filter(valido)
  const picos = puntos.filter(p => p.v > maxY)
  const ultimo = puntos.at(-1)
  const linea = filas.map((p, i) => {
    if (!valido(p)) return ''
    return (i && valido(filas[i - 1]) ? 'L' : 'M') + x(p.t) + ',' + y(p.v)
  }).join(' ')
  const bandas = [
    { label: 'BAJO', min: 0, max: medio, color: '#188143', fondo: '#e4f3e8' },
    { label: 'MEDIO', min: medio, max: alto, color: '#995b00', fondo: '#fff3df' },
    { label: 'ALTO', min: alto, max: maxY, color: '#bb343b', fondo: '#fae9eb' },
  ]
  const tiempo = t => new Date(t).toLocaleTimeString('es-MX', {
    hour: '2-digit', minute: '2-digit', ...(fin - inicio < 120000 ? { second: '2-digit' } : {}), hour12: false
  })
  const marcas = inicio === undefined ? [] : inicio === fin ? [inicio] : [inicio, (inicio + fin) / 2, fin]
  const numero = v => v.toLocaleString('es-MX', { maximumFractionDigits: 2 })
  return <figure style={{ margin: '10px 0', padding: '8px', background: '#f6f6f5', color: '#202629', borderRadius: 10 }}>
    <figcaption style={{ fontSize: 12 }}>Concentración estimada (ppm)</figcaption>
    <svg viewBox="0 0 418 209" style={{ width: '100%', height: 'auto', display: 'block' }}
      role="img" aria-label={nombre + ', historial en ppm. Nivel medio desde ' + medio + ', alto desde ' + alto}>
      {bandas.map(b => <rect key={b.label} x={izquierda} y={y(b.max)} width={derecha - izquierda}
        height={y(b.min) - y(b.max)} fill={b.fondo}/>)}
      {bandas.map(b => <g key={b.label}>
        <line x1={izquierda} x2={derecha} y1={y(b.min)} y2={y(b.min)}
          stroke={b.color} strokeWidth="1.7" strokeDasharray="6 4"/>
        <text x={izquierda - 8} y={y(b.min) + 4} textAnchor="end" fill="#474f55" fontSize="13">{numero(b.min)}</text>
        <text x={derecha + 6} y={y(b.min) + 4} fill={b.color} fontSize="13" fontWeight="700">{b.label}</text>
      </g>)}
      <path d={linea} fill="none" stroke="#1089ba" strokeWidth="2.8" strokeLinejoin="round" strokeLinecap="round"/>
      {ultimo && <circle cx={x(ultimo.t)} cy={y(ultimo.v)} r="3" fill="#1089ba"/>}
      {picos.map((p,i) => <path key={i} d={`M${x(p.t)},${arriba-5} l-3,5 h6 z`} fill="#a24900"/>)}
      {!ultimo && <text x={(izquierda + derecha)/2} y="90" textAnchor="middle" fontSize="14" fill="#37414a">Sin lecturas para este periodo</text>}
      {marcas.map((t,i) => <text key={i} x={x(t)} y="202" textAnchor={marcas.length === 1 || i === 1 ? 'middle' : i === 0 ? 'start' : 'end'}
        fill="#515b63" fontSize="12">{tiempo(t)}</text>)}
    </svg>
    {picos.length > 0 && <small>▲ {picos.length} fuera de escala · máximo {numero(picos.reduce((m,p) => Math.max(m,p.v),0))} ppm</small>}
  </figure>
}

export default function GasPanel({ data, demo, compact = false }) {
  const [escala, setEscala] = useState('bandas')
  const [ventana, setVentana] = useState('15')
  const latest = data?.latest ?? null
  const hist = data?.history ?? []
  const histVisible = useMemo(() => filtrarTiempo(hist, ventana), [hist, ventana])
  const fecha = latest?.timestamp ? Date.parse(latest.timestamp) : NaN
  const reciente = Number.isFinite(fecha) && Date.now() >= fecha && Date.now() - fecha <= 20000
  const estado = demo ? 'DEMO' : !latest ? 'SIN DATOS' : reciente ? 'ACTUALIZADO' : 'ÚLTIMA LECTURA'

  return <section className={`panel gas-panel ${compact ? 'gas-compact' : ''}`}>
    <div className="panel-head gas-panel-head">
      <div><span className="section-kicker">MONITOREO / CONCENTRACIONES</span><h2>Gases en ppm estimadas</h2></div>
      <div className="gas-panel-tools">
        {!compact && <>
          <label className="gas-chart-select">Escala
            <select aria-label="Escala vertical de los gráficos" value={escala} onChange={e => setEscala(e.target.value)}>
              <option value="bandas">Bajo / Medio / Alto</option>
              <option value="detalle">Detalle</option>
              <option value="referencia">Rango del sensor</option>
              <option value="completo">Completa</option>
            </select>
          </label>
          <label className="gas-chart-select">Periodo
            <select aria-label="Periodo de historial para los gráficos" value={ventana} onChange={e => setVentana(e.target.value)}>
              <option value="5">Últimos 5 min</option>
              <option value="15">Últimos 15 min</option>
              <option value="60">Última hora</option>
              <option value="todo">Todo el historial</option>
            </select>
          </label>
        </>}
        <span className={`pill ${demo ? 'pill-amber' : reciente ? 'pill-green' : 'pill-off'}`}><span className="tiny-dot"/>{estado}</span>
      </div>
    </div>
    <div className="gas-sensors gas-ppm-grid">{CAMPOS_PPM.map(c => {
      const rango = RANGOS_VISUALES[c.key]
      const [medio, alto, , criterio] = BANDAS[c.key]
      const nivel = nivelGas(c.key, latest?.[c.key], latest?.mq136_calibrado)
      const colorNivel = { Bajo: '#88d9ff', Medio: '#ffd479', Alto: '#ff9299' }[nivel]
      const fmt = n => n.toLocaleString('es-MX')
      const fueraNominal = rango.nominal !== null && histVisible.some(r => Number.isFinite(r[c.key]) && r[c.key] > rango.nominal)
      return <div className="gas-card" key={c.key}>
        <div className="sensor-card-heading"><span className={`sensor-bullet ${c.color}`}/><div><strong>{c.nombre}</strong><span>{c.detalle}</span></div><span className="tag">ppm</span></div>
        <div className={`gas-value ${c.color}`}>{mostrarPpm(latest?.[c.key])} <span>ppm</span></div>
        <div style={{ color: colorNivel, fontWeight: 600 }}>Referencia didáctica · {nivel}</div>
        <div className="gas-measure-source">Bajo &lt; {fmt(medio)} · Medio {fmt(medio)} a &lt; {fmt(alto)} · Alto ≥ {fmt(alto)} ppm</div>
        {!compact && <>
          <div className="gas-measure-source">{criterio}</div>
          {escala === 'bandas' ? <GraficaBandas historial={histVisible} campo={c.key} nombre={c.nombre} medio={medio} alto={alto}/> :
            <Sparkline data={histVisible} field={c.key} height={123} scaleMode={escala} fixedMax={rango[escala] ?? rango.detalle}/>}
          {fueraNominal && <div className="gas-range-warning" role="note">Lecturas estimadas fuera del rango nominal del sensor: revisar calibración.</div>}
        </>}
        <div className="gas-measure-source">{c.sensor} · {c.key === 'h2s_ppm_aprox' ? 'Valor estimado' : 'Equivalente estimado'}</div>
      </div>
    })}</div>
    <div className="panel-sub"><span>Registro: <b>{fechaLocal(latest?.timestamp)}</b>{!demo && latest && !reciente ? ' · muestra histórica, no en tiempo real' : ''}</span>
      <span>La banda compara el valor mostrado. Evaluar exposición requiere promedios temporales y mediciones validadas. El MiCS-5524 responde a varios gases y sus equivalentes comparten una señal.</span></div>
  </section>
}
