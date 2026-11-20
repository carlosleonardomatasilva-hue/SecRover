import React from 'react'
const ITEMS = [
  ['operacion','◈','Operación'], ['sensores','⌁','Sensores'], ['rondines','◎','Rondines'],
  ['grabaciones','▣','Grabaciones'], ['eventos','☷','Eventos'], ['configuracion','⚙','Configuración']
]
export default function Sidebar({ pagina, setPagina, demo, enlace }) {
  return <aside className="sidebar">
    <div className="identity" onClick={() => setPagina('operacion')} role="button" tabIndex={0} onKeyDown={e => e.key === 'Enter' && setPagina('operacion')}>
      <div className="identity-icon"><span>SR</span></div><div><strong>SECROVER</strong><small>CONTROL PLATFORM <i>v5</i></small></div>
    </div>
    <div className="sidebar-heading">PLATAFORMA</div>
    <nav className="nav-list" aria-label="Secciones SecRover">
      {ITEMS.map(([id, ico, label]) => <button key={id} className={`nav-item ${pagina === id ? 'active' : ''}`} onClick={() => setPagina(id)}><span className="nav-icon">{ico}</span>{label}{pagina === id && <span className="nav-arrow">›</span>}</button>)}
    </nav>
    <div className="sidebar-bottom">
      <div className="unit-card"><div className="mini-unit"><div className="mini-unit-top"/><div className="mini-unit-lens"/><div className="mini-unit-wheel l"/><div className="mini-unit-wheel r"/></div><span className="muted eyebrow">UNIDAD VINCULADA</span><strong>SecRover / 001</strong><span className="little"><span className={`dot ${demo ? 'amber' : enlace === 'conectado' ? 'green' : 'grey'}`} />{demo ? 'Demostración' : enlace === 'conectado' ? 'Enlace autenticado' : 'Sin conexión'}</span></div>
      <div className="side-foot">SECROVER <span>© 2026</span></div>
    </div>
  </aside>
}
