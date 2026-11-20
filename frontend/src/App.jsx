import HistorialGases from './components/HistorialGases.jsx'
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import Sidebar from './components/Sidebar.jsx'
import TopBar from './components/TopBar.jsx'
import CameraPanel from './components/CameraPanel.jsx'
import RoverControl from './components/RoverControl.jsx'
import QuadMDPanel from './components/QuadMDPanel.jsx'
import GasPanel from './components/GasPanel.jsx'
import { DEMO_GAS, DEMO_ROVER, descargarCsv, fechaLocal } from './utils/readings.js'
import { guardarToken, tokenActual } from './services/secroverApi.js'
import useSecRover from './hooks/useSecRover.js'

const TEXTOS = {
  operacion: ['Centro de operaciones','Supervisión y control de la unidad SecRover en un solo lugar.'],
  sensores: ['Monitoreo de sensores','Concentraciones estimadas en ppm e historial desde MongoDB.'],
  rondines: ['Rondines de inspección','Registro local de los recorridos de demostración.'],
  grabaciones: ['Grabaciones','Espacio de consulta: integración de grabación pendiente.'],
  eventos: ['Bitácora de eventos','Acciones e incidencias de esta sesión de navegador.'],
  configuracion: ['Configuración','Credenciales de la sesión y parámetros de visualización.']
}
function Metric({ label, value, unit, note, glyph, hue = 'cyan' }) {
  return <div className="metric panel"><div className="metric-head"><span>{label}</span><span className={`metric-glyph ${hue}`}>{glyph}</span></div><div className="metric-number">{value} {unit && <small>{unit}</small>}</div><div className="metric-note">{note}</div></div>
}
function Eventos({ eventos, demo }) {
  return <section className="panel event-list"><div className="panel-head"><div><span className="section-kicker">AUDITORÍA / SESIÓN</span><h2>Actividad reciente</h2></div><span className="tag">{eventos.length} eventos</span></div><div className="event-items">{eventos.length ? eventos.map(ev=><div className="event-item" key={ev.id}><span className={`event-icon ${ev.type==='alerta'?'warn':''}`}>{ev.type==='alerta'?'!':'↗'}</span><div><strong>{ev.text}</strong><small>{fechaLocal(ev.at)} · {ev.type}</small></div></div>) : <p className="empty-text">No hay eventos en esta sesión.</p>}</div><div className="footnote">{demo ? 'Registros de prueba.' : 'Esta bitácora es local al navegador; todavía no persiste eventos en MongoDB.'}</div></section>
}
function Rondines({ demo, registrar }) {
  const [iniciado, setIniciado] = useState(null)
  const [secs, setSecs] = useState(0)
  const [rondas, setRondas] = useState([])
  useEffect(() => { if (!iniciado) return; const timer = setInterval(()=>setSecs(Math.floor((Date.now()-iniciado)/1000)),500); return ()=>clearInterval(timer) }, [iniciado])
  const format = sec => `${String(Math.floor(sec/60)).padStart(2,'0')}:${String(sec%60).padStart(2,'0')}`
  const toggle = () => {
    if (!demo) return
    if (iniciado) { const next = {duracion:secs, at:new Date().toISOString()}; setRondas(prev=>[next,...prev]); setIniciado(null); registrar('Rondín de demostración finalizado','rondín') }
    else {setSecs(0);setIniciado(Date.now());registrar('Rondín de demostración iniciado','rondín')}
  }
  return <section className="panel roomy"><div className="panel-head"><div><span className="section-kicker">INSPECCIÓN</span><h2>Rondines locales</h2></div><span className="tag">Solo demostración</span></div><div className="ronda-clock">{format(secs)}</div><p className="muted">El cronómetro registra sesiones de ejemplo. No manda rutas ni órdenes al rover.</p><button className="btn btn-primary" disabled={!demo} onClick={toggle}>{iniciado?'Finalizar rondín demo':'Iniciar rondín demo'}</button>{!demo&&<p className="small muted">Activa «Ver demo» para probar esta sección.</p>}<h3>Últimos rondines de esta sesión</h3>{rondas.length ? rondas.map((r,i)=><div className="simple-row" key={i}><span>{fechaLocal(r.at)}</span><strong>{format(r.duracion)}</strong></div>) : <p className="empty-text">No hay rondines registrados.</p>}</section>
}
function Grabaciones() {
  return <section className="panel roomy"><div className="panel-head"><div><span className="section-kicker">ARCHIVO AUDIOVISUAL</span><h2>Biblioteca de grabaciones</h2></div><span className="tag">Fase pendiente</span></div><div className="empty-large"><div className="empty-camera">▣</div><h3>Sin grabaciones disponibles</h3><p>La transmisión MJPEG y la grabación son servicios distintos. Esta reconstrucción todavía no incluye grabación, almacenamiento de videos ni listado de archivos del backend.</p></div></section>
}
function Configuracion({ onUpdate, demo, setDemo, rover, roverEnlace }) {
  const [token, setToken] = useState(() => tokenActual())
  const [mostrar, setMostrar] = useState(false)
  const [saved, setSaved] = useState(false)
  const save = e => {e.preventDefault(); guardarToken(token.trim());setSaved(true);onUpdate(); setTimeout(()=>setSaved(false),2500)}
  return <div className="layout-two"><section className="panel roomy"><div className="panel-head"><div><span className="section-kicker">SESIÓN SEGURA</span><h2>Acceso a FastAPI</h2></div></div><p className="muted">Utiliza la clave <code>SECROVER_WEB_TOKEN</code> configurada en tu backend. Se guarda únicamente en esta pestaña mediante <code>sessionStorage</code>.</p><form onSubmit={save}><label className="field-label" htmlFor="sec-token">Token del servidor</label><div className="input-wrap"><input id="sec-token" type={mostrar?'text':'password'} value={token} onChange={e=>setToken(e.target.value)} placeholder="Introduce la clave de la sesión" autoComplete="off"/><button type="button" className="btn btn-outline btn-compact" onClick={()=>setMostrar(!mostrar)}>{mostrar?'Ocultar':'Mostrar'}</button></div><button type="submit" className="btn btn-primary">Guardar y reconectar</button>{saved && <span className="save-feedback">Configuración aplicada a esta pestaña</span>}</form></section><section className="panel roomy"><div className="panel-head"><div><span className="section-kicker">ENTORNO</span><h2>Estado del sistema</h2></div></div><div className="simple-row"><span>WebSocket del rover</span><strong>{demo?'DEMO':roverEnlace}</strong></div><div className="simple-row"><span>Modo informado</span><strong>{demo?'DEMO':rover.mode}</strong></div><div className="simple-row"><span>Control físico</span><strong>Deshabilitado en esta reconstrucción</strong></div><div className="simple-row"><span>HTTP / WS</span><strong>Mismo origen / proxy Vite</strong></div><button className="btn btn-outline" onClick={()=>setDemo(!demo)}>{demo?'Desactivar demostración':'Activar demostración'}</button></section><section className="panel roomy full-span"><span className="section-kicker">SEGURIDAD</span><h2>Antes de operar el hardware real</h2><p>En modo <b>SIMULACION</b>, la interfaz transmite órdenes para verificar el protocolo. En modos de motores reales el movimiento queda bloqueado por este frontend reconstruido. El botón E-STOP envía la solicitud al servidor, pero <b>no sustituye un paro eléctrico físico</b> ni un watchdog independiente validado.</p></section></div>
}
export default function App() {
  const [pagina, setPagina] = useState('operacion')
  const [demo, setDemo] = useState(false)
  const [tokenVersion, setTokenVersion] = useState(0)
  const [eventos, setEventos] = useState([])
  const registrar = useCallback((text, type = 'sistema') => setEventos(prev => [{id:`${Date.now()}-${Math.random()}`,at:new Date().toISOString(), text, type},...prev].slice(0,100)), [])
  const api = useSecRover({ demo, onEvent: registrar, tokenVersion })
  const rover = demo ? DEMO_ROVER : api.rover
  const gas = demo ? {latest:DEMO_GAS[DEMO_GAS.length-1],history:DEMO_GAS,error:null,actualizada:new Date().toISOString()} : api.gas
  const { roverEnlace, ptzEnlace, ptzInfo } = api
  const [titulo, subtitulo] = TEXTOS[pagina] || TEXTOS.operacion
  const gasFresh = demo || !!(gas.latest?.timestamp && Date.now() >= Date.parse(gas.latest.timestamp) && Date.now()-Date.parse(gas.latest.timestamp)<20000)
  const porcentaje = useMemo(()=>{
    if (!rover.battery_v || !Number.isFinite(+rover.battery_v)) return null
    return (+rover.battery_v).toFixed(1)
  }, [rover.battery_v])

  return <div className="app-frame"><Sidebar pagina={pagina} setPagina={setPagina} demo={demo} enlace={roverEnlace}/><div className="app-main"><TopBar pagina={pagina} demo={demo} enlace={roverEnlace} setDemo={setDemo}/><main className="main-content"><div className="page-heading"><div><div className="section-kicker">SECROVER <span className="slash">/</span> UNIDAD 001</div><h1>{titulo}</h1><p>{subtitulo}</p></div><div className="page-actions"><span className={`pill ${demo?'pill-amber':roverEnlace==='conectado'?'pill-green':'pill-off'}`}><span className="tiny-dot"/>{demo?'DATOS SIMULADOS':roverEnlace==='conectado'?'SISTEMA CONECTADO':'DATOS NO DISPONIBLES'}</span><button className="btn btn-stop" onClick={api.emergencia}>■ E-STOP</button></div></div>
  {api.alerta && !demo && <div className="alert-strip" role="alert">{api.alerta}</div>}
  {pagina==='operacion' && <>
    <div className="metrics-row"><Metric label="ESTADO DEL ROVER" value={demo?'DEMO':roverEnlace==='conectado'?'ENLACE OK':'OFFLINE'} note={demo?'Simulación de interfaz':roverEnlace==='conectado'?'Canal WS autenticado':'Conexión no disponible'} glyph="⌘"/><Metric label="VOLTAJE DE BATERÍA" value={porcentaje??'—'} unit={porcentaje?'V':''} note={demo?'Dato de ejemplo':'Telemetría real si está disponible'} glyph="ϟ" hue="violet"/><Metric label="LECTURA DE GASES" value={gasFresh?'RECIENTE':'—'} note={demo?'Valores simulados':gasFresh?'Datos del servicio de sensores':'No hay muestras recientes'} glyph="⌁" hue="green"/><Metric label="CÁMARA PTZ" value={demo?'DEMO':ptzInfo.online?'ONLINE':'OFFLINE'} note={demo?'Vista conceptual':ptzEnlace==='conectado'?'Conectada a WebSocket':'Sin transmisión'} glyph="▣" hue="amber"/></div>
    <div className="layout-main"><CameraPanel demo={demo} ptzEnlace={ptzEnlace} ptzInfo={ptzInfo} moverPTZ={api.moverPTZ} detenerPTZ={api.detenerPTZ}/><RoverControl demo={demo} rover={rover} roverEnlace={roverEnlace} mover={api.mover} detener={api.detener} emergencia={api.emergencia} rearmar={api.rearmar}/></div>
    <div className="layout-main bottom-panels"><GasPanel data={gas} demo={demo} compact/><QuadMDPanel rover={rover} demo={demo}/></div>
    <Eventos eventos={eventos.slice(0,4)} demo={demo}/>
  </>}
  {pagina==='sensores' && <><div className="sensor-heading"><div><span className="muted">Historial de ppm: <b>{gas.history.length}</b> muestras</span>{gas.error && !demo && <p className="error-inline">No se pudo consultar el servicio de gases: {gas.error}</p>}</div><button className="btn btn-outline" disabled={!gas.history.length} onClick={()=>descargarCsv(gas.history)}>↓ Exportar CSV</button></div><GasPanel data={gas} demo={demo}/><HistorialGases key={String(demo) + tokenVersion} demo={demo}/></>}
  {pagina==='rondines' && <Rondines demo={demo} registrar={registrar}/>}
  {pagina==='grabaciones' && <Grabaciones/>}
  {pagina==='eventos' && <Eventos eventos={eventos} demo={demo}/>}
  {pagina==='configuracion' && <Configuracion demo={demo} setDemo={setDemo} rover={rover} roverEnlace={roverEnlace} onUpdate={()=>setTokenVersion(v=>v+1)}/>}
  </main><footer className="footer"><span><b>SECROVER</b> <span className="muted">Monitoreo y vigilancia remota</span></span><span>{demo?'Vista de demostración · Sin hardware':'Integración React/FastAPI · Fase 5 reconstruida'}</span></footer></div></div>
}
