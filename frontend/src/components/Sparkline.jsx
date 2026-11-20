import React, { useId } from 'react'

const W = 500
const X0 = 55
const X1 = 489
const Y0 = 12
const Y1 = 109

const colorNumero = n =>
  new Intl.NumberFormat('es-MX', {
    maximumFractionDigits: n < 2 ? 2 : 1
  }).format(n)

function techoBonito(maximo) {
  if (!Number.isFinite(maximo) || maximo <= 0) return 10
  const objetivo = maximo * 1.12
  const decada = 10 ** Math.floor(Math.log10(objetivo))
  const pasos = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]
  return (pasos.find(n => n * decada >= objetivo) ?? 10) * decada
}

function etiquetaFecha(t, conFecha = false) {
  if (!Number.isFinite(t)) return '—'
  return new Date(t).toLocaleString('es-MX', conFecha
    ? { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }
    : { hour: '2-digit', minute: '2-digit', hour12: false })
}

export default function Sparkline({
  data = [],
  field = '',
  height = 145,
  scaleMode = 'umbrales',
  fixedMax = 50,
  referenceLines = []
}) {
  const grad = useId().replace(/:/g, '-')
  const samples = data.map((d, i) => ({
    i, v: d[field], t: Date.parse(d.timestamp)
  })).filter(p => typeof p.v === 'number' && Number.isFinite(p.v) && p.v >= 0)

  const referencias = referenceLines.filter(r =>
    Number.isFinite(r.value) && r.value >= 0)

  const maxRaw = samples.reduce((max, p) => Math.max(max, p.v), 0)
  const maxReferencia = referencias.reduce((max, r) => Math.max(max, r.value), 0)

  // En modo Umbrales, la escala depende de los límites,
  // no de lecturas anómalas o fuera de rango.
  const maxY = scaleMode === 'umbrales'
    ? techoBonito(Math.max(1, maxReferencia))
    : scaleMode === 'completo'
      ? techoBonito(maxRaw)
      : Math.max(1, Number(fixedMax) || 50)

  const clipping = samples.filter(p => p.v > maxY)
  const tiempos = samples.map(p => p.t).filter(Number.isFinite)
  const tMin = tiempos.length ? Math.min(...tiempos) : NaN
  const tMax = tiempos.length ? Math.max(...tiempos) : NaN
  const hayFechas = samples.length >= 2 &&
    samples.every(p => Number.isFinite(p.t)) &&
    Number.isFinite(tMin) && Number.isFinite(tMax) && tMax > tMin

  const x = p => X0 + (
    hayFechas
      ? (p.t - tMin) / (tMax - tMin)
      : samples.length === 1
        ? 0.5
        : p.i / Math.max(1, data.length - 1)
  ) * (X1 - X0)

  const y = v => Y1 - (Math.min(maxY, Math.max(0, v)) / maxY) * (Y1 - Y0)

  const line = samples.map((p, i) =>
    `${i === 0 ? 'M' : 'L'}${x(p).toFixed(2)} ${y(p.v).toFixed(2)}`
  ).join(' ')

  const area = samples.length >= 2
    ? `${line} L ${x(samples.at(-1)).toFixed(2)} ${Y1} L ${x(samples[0]).toFixed(2)} ${Y1} Z`
    : ''

  const color = field === 'h2s_ppm_aprox' ? '#c3affc' : '#35d6ee'
  const ultimo = samples.at(-1)
  const fechasDistintas = hayFechas &&
    new Date(tMin).toDateString() !== new Date(tMax).toDateString()

  return <div className="gas-chart-wrap">
    <svg
      className="sparkline sparkline-rangos"
      viewBox={`0 0 ${W} 150`}
      preserveAspectRatio="none"
      style={{ height, width: '100%' }}
      role="img"
      aria-label={`Gráfica de ${field}, escala 0 a ${colorNumero(maxY)} ppm, con referencias académicas de bajo, medio y alto`}
    >
      <defs>
        <linearGradient id={grad} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity=".24"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient>
      </defs>

      {[0, 0.5, 1].map(fraccion => {
        const valor = maxY * (1 - fraccion)
        const yy = Y0 + fraccion * (Y1 - Y0)
        return <g key={fraccion}>
          <line x1={X0} x2={X1} y1={yy} y2={yy}
            stroke="rgba(182,210,228,.15)" strokeDasharray="3 6"/>
          <text x={X0 - 8} y={yy + 4} fill="#91a7bc"
            fontSize="11" textAnchor="end">{colorNumero(valor)}</text>
        </g>
      })}

      {samples.length >= 2 && <>
        <path d={area} fill={`url(#${grad})`}/>
        <path d={line} fill="none" stroke={color} strokeWidth="2.4"
          strokeLinejoin="round" strokeLinecap="round"/>
      </>}

      {referencias.filter(r => r.value <= maxY).map(r => (
        <g key={`${r.label}-${r.value}`}>
          <line x1={X0} x2={X1} y1={y(r.value)} y2={y(r.value)}
            stroke={r.color} strokeWidth="2"
            strokeDasharray={r.value === 0 ? 'none' : '6 4'} opacity=".95"/>
          <text x={X1 - 5} y={Math.max(Y0 + 11, y(r.value) - 5)}
            textAnchor="end" fill={r.color} fontSize="11" fontWeight="bold"
            stroke="#0d1826" strokeWidth="3" paintOrder="stroke">
            {r.label} · {colorNumero(r.value)}
          </text>
        </g>
      ))}

      {clipping.map(p => (
        <path key={`out-${p.i}`}
          d={`M${(x(p)-4).toFixed(1)} 8 L${(x(p)+4).toFixed(1)} 8 L${x(p).toFixed(1)} 1 Z`}
          fill="#ffbe73"/>
      ))}

      {ultimo && <circle cx={x(ultimo)} cy={y(ultimo.v)} r="3.7"
        fill={ultimo.v > maxY ? '#ffbe73' : color}
        stroke="#101825" strokeWidth="1.8"/>}

      {samples.length < 2 && <text x={(X0 + X1) / 2} y="75"
        fill="#8299ae" fontSize="12" textAnchor="middle">
        Sin historial suficiente
      </text>}

      <text x={X0} y="141" fill="#8299ae" fontSize="11" textAnchor="start">
        {hayFechas ? etiquetaFecha(tMin, fechasDistintas) : 'Inicio'}
      </text>
      <text x={X1} y="141" fill="#8299ae" fontSize="11" textAnchor="end">
        {hayFechas ? etiquetaFecha(tMax, fechasDistintas) : 'Última lectura'}
      </text>
    </svg>

    <div style={{
      display: 'flex', gap: 12, flexWrap: 'wrap',
      marginTop: 6, fontSize: 10, color: '#9fb0c2'
    }}>
      {referencias.map(r => <span key={`${r.label}-${r.value}`}>
        <span style={{ color: r.color }}>━ </span>
        {r.label}: {colorNumero(r.value)} ppm
        {r.value > maxY ? ' (fuera de escala)' : ''}
      </span>)}
    </div>

    <div className="gas-chart-caption">
      <span>Eje Y: 0–{colorNumero(maxY)} ppm · Eje X: tiempo</span>
      {clipping.length > 0 && <span className="gas-chart-outlier"
        title={`Máximo registrado: ${colorNumero(maxRaw)} ppm estimadas`}>
        {clipping.length} fuera de escala (máx. {colorNumero(maxRaw)} ppm)
      </span>}
    </div>
  </div>
}
