import React, { useEffect, useState } from 'react'
export default function TopBar({ pagina, demo, enlace, setDemo }) {
  const [hora, setHora] = useState(() => new Date())
  useEffect(() => { const id = setInterval(() => setHora(new Date()), 1000); return () => clearInterval(id) }, [])
  return <header className="topbar">
    <div className="breadcrumbs">SISTEMA <span>/</span> <b>{pagina.toUpperCase()}</b></div>
    <div className="top-right"><span className="top-date">{hora.toLocaleDateString('es-MX', { day:'2-digit', month:'short', year:'numeric' })}</span><span className="clock">{hora.toLocaleTimeString('es-MX', { hour12:false })}</span><span className={`pill ${demo ? 'pill-amber' : enlace === 'conectado' ? 'pill-green' : 'pill-off'}`}><span className="tiny-dot" />{demo ? 'MODO DEMO' : enlace === 'conectado' ? 'SISTEMA EN LÍNEA' : 'SIN ENLACE'}</span><button className="btn btn-outline btn-compact" onClick={() => setDemo(!demo)}>{demo ? 'Salir de demo' : 'Ver demo'}</button></div>
  </header>
}
