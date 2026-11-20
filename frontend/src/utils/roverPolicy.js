/** Estado del control manual según el permiso anunciado por FastAPI.
 *  No reemplaza ni verifica el paro eléctrico o watchdog de hardware.
 *  SERIAL_BANCO solo puede ejecutarse en la Orange Pi y con ruedas suspendidas:
 *  el backend debe rechazar conexiones remotas y limitar el tiempo de movimiento.
 */
export const DIRECCIONES_ROVER = new Set([
  'FORWARD', 'BACKWARD', 'STRAFE_LEFT', 'STRAFE_RIGHT'
])

export function controlRoverPermitido({ demo, rover, roverEnlace }) {
  const bloquear = (motivo) => ({ operativo: false, etiqueta: 'BLOQUEADO', motivo })
  if (demo) return bloquear('Sal del modo demostración para controlar el rover.')
  if (roverEnlace !== 'conectado') return bloquear('No hay conexión WebSocket con el rover.')
  if (!rover || rover.estop) return bloquear('Parada de emergencia activa. Requiere revisión y rearme autorizado.')
  if (rover.error || rover.online !== true) return bloquear(rover.error || 'El servidor no autoriza movimientos.')

  if (rover.mode === 'SIMULACION') {
    return { operativo: true, etiqueta: 'SIMULACIÓN ACTIVA', motivo: '' }
  }
  if (rover.mode === 'SERIAL_BANCO') {
    if (rover.local_test_only !== true) return bloquear('Falta confirmación del modo de banco local.')
    if (rover.connected !== true || rover.motor_fresh !== true) {
      return bloquear('Sin telemetría reciente de encoders Quad-MD. Movimiento bloqueado.')
    }
    return { operativo: true, etiqueta: 'MOTORES REALES · BANCO', motivo: '' }
  }
  if (rover.mode === 'SERIAL') {
    if (rover.hardware_watchdog_verified !== true) {
      return bloquear('El backend no informa una verificación del watchdog independiente.')
    }
    if (rover.connected !== true || rover.motor_fresh !== true) {
      return bloquear('Sin telemetría reciente de encoders Quad-MD. Movimiento bloqueado.')
    }
    return { operativo: true, etiqueta: 'CONTROL REAL', motivo: '' }
  }
  return bloquear(`Modo no habilitado: ${rover.mode || 'DESCONOCIDO'}`)
}
