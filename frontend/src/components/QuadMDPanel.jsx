import React from 'react'
export default function QuadMDPanel({ rover, demo }) {
  const motors = rover?.motors_cm_s ?? [null,null,null,null]
  return <section className="panel quad-panel"><div className="panel-head"><div><span className="section-kicker">DRIVER / QUAD-MD</span><h2>Telemetría de motores</h2></div><span className="muted small">{demo ? 'VALORES DEMO' : rover?.connected && rover?.motor_fresh ? 'LECTURA ACTUAL' : 'SIN TELEMETRÍA'}</span></div><div className="quad-grid">{[0,1,2,3].map(i=><div className="motor-cell" key={i}><small>M{i+1}</small><strong>{typeof motors[i]==='number'?motors[i].toFixed(1):'—'}</strong><span>cm/s</span></div>)}</div><div className="quad-foot"><span>Velocidad de las ruedas</span><span>{!demo && !rover?.motor_fresh ? 'Dato no reciente' : 'Estado nominal en demo'}</span></div></section>
}
