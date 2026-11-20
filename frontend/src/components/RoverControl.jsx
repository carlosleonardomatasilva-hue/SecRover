import React, { useEffect, useState } from 'react'
import { controlRoverPermitido } from '../utils/roverPolicy.js'
const DIRECTIONS = [{k:'FORWARD', icon:'↑',label:'Avanzar', cls:'up'}, {k:'STRAFE_LEFT', icon:'←',label:'Izquierda',cls:'left'}, {k:'STOP', icon:'■',label:'Detener',cls:'center'}, {k:'STRAFE_RIGHT',icon:'→',label:'Derecha',cls:'right'},{k:'BACKWARD',icon:'↓',label:'Retroceder',cls:'down'}]
export default function RoverControl({ demo, rover, roverEnlace, mover, detener, emergencia, rearmar }) {
  const [speed, setSpeed] = useState(40)
  const [active, setActive] = useState(null)
  const permiso = controlRoverPermitido({ demo, rover, roverEnlace })
  const operativo = permiso.operativo
  useEffect(() => {
    const stop = () => { setActive(null); detener() }
    const keys = { w:'FORWARD', ArrowUp:'FORWARD', s:'BACKWARD', ArrowDown:'BACKWARD', a:'STRAFE_LEFT', ArrowLeft:'STRAFE_LEFT', d:'STRAFE_RIGHT', ArrowRight:'STRAFE_RIGHT' }
    const down = (e) => {
      if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '')) return
      if (e.code === 'Space') { e.preventDefault(); stop(); return }
      const k = keys[e.key]
      if (!k || e.repeat) return
      e.preventDefault()
      if (operativo && mover(k, speed)) setActive(k)
    }
    const up = (e) => { if (keys[e.key]) stop() }
    window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', stop)
    return () => { window.removeEventListener('keydown', down);window.removeEventListener('keyup', up);window.removeEventListener('blur',stop);stop() }
  }, [operativo, mover, detener, speed])
  const press = (action, e) => {
    if (action === 'STOP') { detener(); setActive(null); return }
    e.currentTarget.setPointerCapture?.(e.pointerId)
    if (mover(action, speed)) setActive(action)
  }
  const stop = () => { setActive(null); detener() }
  return <section className="panel rover-panel"><div className="panel-head"><div><span className="section-kicker">CONTROL MANUAL</span><h2>Movimiento del rover</h2></div><span className={`pill ${operativo ? 'pill-green' : 'pill-off'}`}><span className="tiny-dot"/>{permiso.etiqueta}</span></div>
    <div className="rover-state"><span className="muted">ESTADO ACTUAL</span><strong>{rover.estop ? 'PARADA DE EMERGENCIA' : active ? 'MOVIMIENTO SOLICITADO' : 'SIN MOVIMIENTO'}</strong></div>
    <div className="movement-pad">{DIRECTIONS.map(item => <button key={item.k} className={`${item.cls} ${active===item.k?'pressed':''} ${item.k==='STOP'?'stop-key':''}`} aria-label={item.label} disabled={item.k!=='STOP' && !operativo} onPointerDown={e=>press(item.k,e)} onPointerUp={stop} onPointerCancel={stop} onLostPointerCapture={stop}>{item.icon}</button>)}</div>
    <div className="speed-control"><div className="speed-label"><span>Velocidad solicitada</span><strong>{speed}%</strong></div><input type="range" value={speed} min="10" max="100" step="5" onChange={e=>setSpeed(+e.target.value)}/><div className="speed-scale"><span>10%</span><span>50%</span><span>100%</span></div></div>
    <div className="help-note">Mantén presionada una dirección para solicitar movimiento. WASD / flechas; suelta para detener. Espacio = STOP. {rover.mode === 'SERIAL_BANCO' ? 'Prueba local con ruedas suspendidas: máximo 2 s por pulsación; no usar sobre el suelo.' : rover.mode === 'SERIAL' ? 'Movimiento físico: requiere paro de potencia y watchdog independiente comprobados.' : ''}</div>
    <button className="btn emergency-btn" onClick={emergencia}>■ PARADA DE EMERGENCIA</button>
    {rover.estop && rover.mode==='SIMULACION' && !demo && <button className="btn btn-outline" onClick={rearmar}>Solicitar rearme de simulación</button>}
  </section>
}
