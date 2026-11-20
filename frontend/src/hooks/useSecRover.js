import { useCallback, useEffect, useRef, useState } from 'react'
import { getEstado, getHistoricoGases, getUltimosGases, tokenActual, wsUrl } from '../services/secroverApi.js'
import { extraerHistorico, extraerLectura } from '../utils/readings.js'
import { controlRoverPermitido, DIRECCIONES_ROVER } from '../utils/roverPolicy.js'

const NO_CONECTADO = { connected: false, online: false, mode: 'SIN CONEXIÓN', battery_v: null, motors_cm_s: [null, null, null, null], motor_fresh: false, estop: false, local_test_only: true }

export default function useSecRover({ demo, onEvent, tokenVersion }) {
  const [rover, setRover] = useState(NO_CONECTADO)
  const [roverEnlace, setRoverEnlace] = useState('desconectado')
  const [ptzEnlace, setPtzEnlace] = useState('desconectado')
  const [ptzInfo, setPtzInfo] = useState({ ticket: null, online: false })
  const [status, setStatus] = useState(null)
  const [gas, setGas] = useState({ latest: null, history: [], error: null, actualizada: null })
  const [alerta, setAlerta] = useState('')
  const roverSock = useRef(null)
  const ptzSock = useRef(null)
  const held = useRef(null)
  const ptzHeld = useRef(null)
  const speedRef = useRef(40)
  const emergencyRef = useRef(false)
  const onEventRef = useRef(onEvent)
  useEffect(() => { onEventRef.current = onEvent }, [onEvent])

  const registrar = useCallback((texto, tipo = 'sistema') => onEventRef.current?.(texto, tipo), [])
  const sendRover = useCallback(payload => {
    const socket = roverSock.current
    if (socket?.readyState !== WebSocket.OPEN || demo) return false
    socket.send(JSON.stringify(payload))
    return true
  }, [demo])
  const sendPTZ = useCallback(payload => {
    const socket = ptzSock.current
    if (socket?.readyState !== WebSocket.OPEN || demo) return false
    socket.send(JSON.stringify(payload))
    return true
  }, [demo])

  const detener = useCallback(() => {
    held.current = null
    sendRover({ type: 'stop' })
  }, [sendRover])

  const mover = useCallback((action, percent) => {
    if (demo || roverEnlace !== 'conectado' || emergencyRef.current || rover.estop) return false
    // El backend decide el modo y conserva las protecciones físicas.
    // React solo admite las cuatro direcciones que el usuario eligió.
    if (!DIRECCIONES_ROVER.has(action)) {
      setAlerta('Dirección no permitida: usa únicamente arriba, abajo, izquierda o derecha.')
      return false
    }
    const permiso = controlRoverPermitido({ demo, rover, roverEnlace })
    if (!permiso.operativo) {
      setAlerta(permiso.motivo)
      return false
    }
    const speed = Math.round(Math.max(0, Math.min(100, Number(percent) || 0)))
    if (!speed) return false
    speedRef.current = speed
    held.current = action
    return sendRover({ type: 'move', action, percent: speed })
  }, [demo, roverEnlace, rover.estop, rover.mode, rover.online, rover.error, rover.connected, rover.motor_fresh, rover.local_test_only, rover.hardware_watchdog_verified, sendRover])

  const emergencia = useCallback(() => {
    emergencyRef.current = true
    detener()
    const enviada = sendRover({ type: 'emergency' })
    setAlerta(enviada ? 'E-STOP solicitado. Esperando confirmación del servidor.' : 'SIN CONEXIÓN: corta físicamente la alimentación si hay movimiento.')
    registrar(enviada ? 'Solicitud de parada de emergencia' : 'Parada local; sin confirmación del servidor', 'alerta')
    return enviada
  }, [detener, sendRover, registrar])

  const rearmar = useCallback(() => {
    if (!demo && rover.mode === 'SIMULACION' && roverEnlace === 'conectado') {
      setAlerta('Rearme solicitado; pendiente confirmación del servidor')
      sendRover({ type: 'reset' })
    }
  }, [demo, rover.mode, roverEnlace, sendRover])

  const detenerPTZ = useCallback(() => {
    if (ptzHeld.current) sendPTZ({ type: 'stop' })
    ptzHeld.current = null
  }, [sendPTZ])
  const moverPTZ = useCallback((action) => {
    if (demo || ptzEnlace !== 'conectado' || emergencyRef.current || rover.estop) return false
    if (action === 'PTZ_HOME') return sendPTZ({ type: 'home' })
    ptzHeld.current = action
    return sendPTZ({ type: 'start', action })
  }, [demo, ptzEnlace, rover.estop, sendPTZ])

  useEffect(() => {
    emergencyRef.current = !!rover.estop
  }, [rover.estop])

  useEffect(() => {
    if (demo) {
      setRoverEnlace('demo'); setPtzEnlace('demo'); setRover(NO_CONECTADO)
      setGas({ latest: null, history: [], error: null, actualizada: null })
      return
    }
    let alive = true
    let reconectarRover
    let reconectarPtz
    let roverSocket
    let ptzSocket
    let roverAuthRechazada = false
    let ptzAuthRechazada = false
    let paradaPorFocoEnviada = false

    function abrirRover() {
      if (!alive) return
      setRoverEnlace('conectando')
      roverSocket = new WebSocket(wsUrl('/ws/rover'))
      roverSock.current = roverSocket
      roverSocket.onopen = () => roverSocket.send(JSON.stringify({ type: 'auth', token: tokenActual() }))
      roverSocket.onmessage = event => {
        let msg
        try { msg = JSON.parse(event.data) } catch { return }
        if (msg.type === 'ready') {
          setRoverEnlace('conectado')
          setAlerta('')
          setRover(prev => ({ ...prev, ...msg, connected: msg.online ?? true }))
          registrar(`Enlace del rover disponible (${msg.mode || 'sin modo'})`)
        } else if (msg.type === 'rearmed') {
          setRover(prev => ({ ...prev, ...msg, estop: false }))
          emergencyRef.current = false
          setAlerta('')
        } else if (msg.type === 'telemetry') {
          setRover(prev => ({ ...prev, ...msg, connected: msg.connected ?? prev.connected }))
        } else if (msg.type === 'status' || msg.type === 'state') {
          setRover(prev => ({ ...prev, ...msg }))
        } else if (msg.type === 'emergency' || msg.type === 'estop') {
          setRover(prev => ({ ...prev, estop: msg.estop ?? true }))
          setAlerta('Parada de emergencia confirmada por el servidor')
        } else if (msg.type === 'auth_error') {
          roverAuthRechazada = true
          setAlerta(msg.message || 'Acceso al WebSocket del rover rechazado')
          setRoverEnlace('error')
        } else if (msg.type === 'error') {
          setAlerta(msg.detail || msg.message || 'Error del servidor rover')
        }
      }
      roverSocket.onerror = () => setRoverEnlace('error')
      roverSocket.onclose = (event) => {
        if (!alive) return
        console.warn('[SecRover] /ws/rover cerrado', { codigo: event.code, motivo: event.reason || 'sin motivo', limpio: event.wasClean })
        held.current = null
        setRoverEnlace('desconectado'); setRover(NO_CONECTADO)
        if (event.code === 1008 || roverAuthRechazada) {
          setRoverEnlace('error')
          setAlerta('WebSocket del rover rechazado (1008). Verifica token y ALLOWED_ORIGINS.')
          return
        }
        reconectarRover = setTimeout(abrirRover, 2500)
      }
    }

    function abrirPTZ() {
      if (!alive) return
      ptzSocket = new WebSocket(wsUrl('/ws/ptz'))
      ptzSock.current = ptzSocket
      ptzSocket.onopen = () => ptzSocket.send(JSON.stringify({ type: 'auth', token: tokenActual() }))
      ptzSocket.onmessage = event => {
        let msg
        try { msg = JSON.parse(event.data) } catch { return }
        if (msg.type === 'ready') {
          setPtzEnlace('conectado')
          setPtzInfo(prev => ({ ...prev, online: !!(msg.camera_online ?? msg.online) }))
        } else if (msg.type === 'video_ticket') {
          setPtzInfo(prev => ({ ...prev, ticket: msg.ticket || msg.video_ticket || null }))
        } else if (msg.type === 'status' || msg.type === 'camera_status') {
          setPtzInfo(prev => ({ ...prev, online: !!(msg.camera_online ?? msg.online ?? prev.online) }))
        } else if (msg.type === 'auth_error') {
          ptzAuthRechazada = true
          setPtzEnlace('error')
          setAlerta(msg.message || 'Acceso al WebSocket PTZ rechazado')
        } else if (msg.type === 'error') setAlerta(msg.message || 'Error de cámara PTZ')
      }
      ptzSocket.onerror = () => setPtzEnlace('error')
      ptzSocket.onclose = (event) => {
        if (!alive) return
        console.warn('[SecRover] /ws/ptz cerrado', { codigo: event.code, motivo: event.reason || 'sin motivo', limpio: event.wasClean })
        ptzHeld.current = null
        setPtzInfo({ ticket: null, online: false }); setPtzEnlace('desconectado')
        if (event.code === 1008 || ptzAuthRechazada) {
          setPtzEnlace('error')
          setAlerta('WebSocket PTZ rechazado (1008). Verifica token y ALLOWED_ORIGINS.')
          return
        }
        reconectarPtz = setTimeout(abrirPTZ, 3000)
      }
    }
    abrirRover(); abrirPTZ()
    // Enviar una unica orden STOP cuando la ventana pierde foco.
    // Evita inundar el backend con comandos cada 250 ms si el navegador esta oculto.
    const seguridad = () => {
      if (paradaPorFocoEnviada) return
      paradaPorFocoEnviada = true
      if (roverSock.current?.readyState === WebSocket.OPEN) roverSock.current.send(JSON.stringify({ type: 'stop' }))
      if (ptzSock.current?.readyState === WebSocket.OPEN) ptzSock.current.send(JSON.stringify({ type: 'stop' }))
      held.current = null; ptzHeld.current = null
    }
    const timer = setInterval(() => {
      if (document.hidden || !document.hasFocus()) {
        seguridad()
        return
      }
      paradaPorFocoEnviada = false
      if (held.current && roverSock.current?.readyState === WebSocket.OPEN) roverSock.current.send(JSON.stringify({ type: 'heartbeat' }))
      if (ptzHeld.current && ptzSock.current?.readyState === WebSocket.OPEN) ptzSock.current.send(JSON.stringify({ type: 'heartbeat' }))
    }, 250)
    document.addEventListener('visibilitychange', seguridad)
    window.addEventListener('blur', seguridad)
    window.addEventListener('pagehide', seguridad)
    return () => {
      alive = false
      clearTimeout(reconectarRover); clearTimeout(reconectarPtz); clearInterval(timer)
      document.removeEventListener('visibilitychange', seguridad)
      window.removeEventListener('blur', seguridad)
      window.removeEventListener('pagehide', seguridad)
      seguridad()
      roverSocket?.close(); ptzSocket?.close()
      roverSock.current = null; ptzSock.current = null
    }
  }, [demo, tokenVersion, registrar])

  useEffect(() => {
    if (demo) return
    const controller = new AbortController()
    let timer
    const refrescar = async () => {
      try {
        const info = await getEstado(controller.signal)
        if (!controller.signal.aborted) setStatus(info)
      } catch { if (!controller.signal.aborted) setStatus(null) }
      try {
        const [ultima, historico] = await Promise.all([
          getUltimosGases(controller.signal), getHistoricoGases(60, controller.signal)
        ])
        if (!controller.signal.aborted) {
          setGas({ latest: extraerLectura(ultima), history: extraerHistorico(historico), error: null, actualizada: new Date().toISOString() })
        }
      } catch (err) {
        if (!controller.signal.aborted) setGas(prev => ({ ...prev, latest: null, history: [], error: err.message, actualizada: null }))
      }
    }
    refrescar()
    timer = setInterval(refrescar, 3000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [demo, tokenVersion])

  return { rover, roverEnlace, ptzEnlace, ptzInfo, status, gas, alerta, mover, detener, emergencia, rearmar, moverPTZ, detenerPTZ }
}
