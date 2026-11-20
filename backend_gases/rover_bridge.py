"""Puente SecRover: movimiento local limitado y telemetría de la Quad-MD.

SERIAL_BANCO permite una prueba REAL en la Orange Pi con ruedas suspendidas,
una orden de hasta 2 segundos, sin conducción remota y con consentimiento
explícito en .env. No es un sistema de seguridad certificado: si Python,
Linux o USB se bloquean, la controladora PODRÍA mantener la última velocidad.
SERIAL (producción) exige un watchdog comprobado independiente de Python.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import threading
from contextlib import suppress
from typing import Optional

LOG = logging.getLogger('secrover.rover')
MAX_CM_S = 10              # Límite inicial: 50 mm/s fue probado en suspensión; NO es telemetría
HEARTBEAT_TIMEOUT = 0.65  # Pérdida de señales del navegador: solicitar STOP
BENCH_BURST_SECONDS = 2.0  # Máximo CONTINUO en pruebas locales; no es watchdog de hardware
# Solo se admiten las cuatro direcciones del panel React.
# STOP se gestiona mediante rover.stop() y el tipo de mensaje "stop".
ALLOWED = frozenset({
    'FORWARD', 'BACKWARD', 'STRAFE_LEFT', 'STRAFE_RIGHT',
})

# Orden FÍSICO confirmado: M1=FL, M2=RL, M3=FR, M4=RR.
# Se conservan exactamente los signos previamente probados en el chasis.
# LEFT / RIGHT son desplazamientos LATERALES; NO giros sobre su propio eje.
MIX = {
    'FORWARD': (1, 1, 1, 1),
    'BACKWARD': (-1, -1, -1, -1),
    'STRAFE_LEFT': (1, -1, -1, 1),
    'STRAFE_RIGHT': (-1, 1, 1, -1),
}

# Un solo conjunto de movimientos admitidos, tanto simulados como físicos.
VERIFIED_MOVEMENTS = ALLOWED

# Se conservan EXACTAMENTE las dos cadenas de parada de las pruebas físicas.
# Aunque el segundo comando tiene espacios, no alteramos lo que funcionó.
STOP_COMMANDS = ('$spd:0,0,0,0#', '$pwm: 0, 0, 0,0#')


def motor_values(action: str, percent: float, max_cm_s: float = MAX_CM_S):
    """Valores M1,M2,M3,M4 sin invertir signos ni permutar canales."""
    if action not in MIX:
        raise ValueError('Movimiento desconocido')
    if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError('Porcentaje fuera de 0 a 100')
    mm_s = max_cm_s * 10 * (percent / 100.0)
    return tuple(int(round(sign * mm_s)) for sign in MIX[action])


class SerialMotors:
    """Transmisión real: SOLO para chasis calibrado con STOP verificado."""

    def __init__(self):
        import serial  # no se exige pyserial para el modo simulación
        self.send_lock = threading.Lock()
        self.serial = serial.Serial(
            port=os.getenv('ROVER_SERIAL_PORT', '/dev/ttyUSB0'),
            baudrate=115200, timeout=0.3, write_timeout=0.3, exclusive=True,
        )
        try:
            time.sleep(2.0)  # igual que en la prueba exitosa
            self.stop()
        except BaseException:
            self.serial.close()
            raise

    def send(self, command):
        with getattr(self, 'send_lock', threading.Lock()):
            self.serial.write(command.encode('ascii'))
            self.serial.flush()
        time.sleep(0.05)

    def move(self, action, percent):
        if action not in VERIFIED_MOVEMENTS:
            raise ValueError('Movimiento no permitido: solo arriba, abajo, izquierda y derecha')
        values = motor_values(action, percent)
        self.send('$spd:{},{},{},{}#'.format(*values))
        LOG.info('Quad-MD %s a %s%%: canales=%s', action, percent, values)

    def stop(self):
        # Misma secuencia exacta comprobada en pruebas. Intentar ambos incluso
        # cuando el primer comando falla; no es una parada de hardware.
        failures = []
        for command in STOP_COMMANDS:
            try:
                self.send(command)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise RuntimeError('No se pudo transmitir STOP; cortar alimentación física') from failures[0]

    def close(self):
        try:
            self.stop()
        finally:
            self.serial.close()


class RoverBridge:
    def __init__(self):
        self.mode = 'SIMULACION'
        self.driver: Optional[SerialMotors] = None
        self.error = ''
        self.owner: Optional[str] = None
        self.action: Optional[str] = None
        self.percent = 0
        self.last_heartbeat = 0.0
        self.burst_start = 0.0
        self.requires_release = False  # tras corte de 2s, soltar antes de otro pulso
        self.estop = False
        self.lock = asyncio.Lock()
        self.task: Optional[asyncio.Task] = None

    async def initialize(self):
        requested = os.getenv('ROVER_MODE', 'SIMULACION').upper()
        if requested == 'SERIAL_BANCO':
            # NO es modo de conducción: únicamente ensayo local y limitado.
            if (os.getenv('ROVER_STOP_VERIFIED') != '1' or
                    os.getenv('ROVER_BENCH_ACK') != 'RUEDAS_SUSPENDIDAS'):
                self.mode = 'BLOQUEADO'
                self.estop = True
                self.error = ('SERIAL_BANCO requiere ROVER_STOP_VERIFIED=1 y '
                              'ROVER_BENCH_ACK=RUEDAS_SUSPENDIDAS; nunca en suelo')
            else:
                await self._open_serial('SERIAL_BANCO')
        elif requested == 'SERIAL':
            # Para producción remota se exige firmware/hardware con STOP
            # autónomo frente a falla de la Orange Pi o del enlace USB.
            if (os.getenv('ROVER_STOP_VERIFIED') != '1' or
                    os.getenv('ROVER_HARDWARE_WATCHDOG_VERIFIED') != '1'):
                self.mode = 'BLOQUEADO'
                self.estop = True
                self.error = 'Modo SERIAL bloqueado: watchdog independiente sin verificar'
            else:
                await self._open_serial('SERIAL')
        elif requested != 'SIMULACION':
            self.mode = 'BLOQUEADO'
            self.estop = True
            self.error = 'ROVER_MODE desconocido; arranque bloqueado'
        self.task = asyncio.create_task(self.watchdog())
        LOG.warning('ROVER modo=%s, movimiento físico=%s',
                    self.mode, 'ACTIVO' if self.driver else 'INACTIVO')

    async def _open_serial(self, mode):
        try:
            self.driver = await asyncio.to_thread(SerialMotors)
            self.mode = mode
        except Exception as exc:
            self.mode = 'BLOQUEADO'
            self.error = f'No se pudo abrir el puerto serial: {type(exc).__name__}: {exc}'
            self.estop = True
            LOG.exception(self.error)

    def info(self):
        return {
            'mode': self.mode, 'online': not bool(self.error),
            'max_cm_s': MAX_CM_S, 'estop': self.estop,
            'active': self.action is not None, 'error': self.error,
            'diagonals_verified': False,
            'hardware_watchdog_verified': self.mode == 'SERIAL',
            'local_test_only': self.mode == 'SERIAL_BANCO',
            'max_burst_s': BENCH_BURST_SECONDS if self.mode == 'SERIAL_BANCO' else None,
        }

    async def _stop_locked(self):
        self.action = None
        self.percent = 0
        self.owner = None
        self.last_heartbeat = 0.0
        self.burst_start = 0.0
        if self.driver:
            try:
                await asyncio.to_thread(self.driver.stop)
            except Exception:
                self.estop = True
                self.error = 'Fallo de STOP serial: CORTAR ALIMENTACIÓN FÍSICA'
                LOG.exception(self.error)
                raise
        LOG.info('ROVER: parada solicitada (%s)', self.mode)

    async def move(self, client: str, action: str, percent):
        if action not in ALLOWED:
            raise ValueError('Movimiento no permitido')
        if type(percent) not in (int, float) or not math.isfinite(percent):
            raise ValueError('Porcentaje inválido')
        if not (0 <= percent <= 100):
            raise ValueError('Porcentaje fuera de 0..100')
        percent = int(percent)
        async with self.lock:
            if self.error or self.estop:
                raise RuntimeError(self.error or 'Parada de software activa')
            if self.owner is not None and self.owner != client:
                raise RuntimeError('Otro navegador controla el rover')
            if self.mode == 'SERIAL_BANCO' and self.requires_release:
                raise RuntimeError('Límite de prueba: suelta el control antes de otro pulso')
            if percent == 0:
                await self._stop_locked()
                return
            if self.driver and action not in VERIFIED_MOVEMENTS:
                raise ValueError('Movimiento no permitido en el modo físico')
            if self.mode == 'SERIAL_BANCO':
                now = time.monotonic()
                # El temporizador comienza con el PRIMER comando, no se reinicia
                # cambiando la velocidad o dirección mientras se mantiene presionado.
                if self.action is None:
                    self.burst_start = now
                if now - self.burst_start >= BENCH_BURST_SECONDS:
                    await self._stop_locked()
                    self.requires_release = True
                    raise RuntimeError('Límite de 2 s: suelta el control para repetir')
            # No activar una orden nueva sin cancelación ante fallo.
            try:
                if self.driver:
                    await asyncio.to_thread(self.driver.move, action, percent)
            except Exception:
                self.estop = True
                with suppress(Exception):
                    await self._stop_locked()
                raise
            self.owner = client
            self.action = action
            self.percent = percent
            self.last_heartbeat = time.monotonic()
            LOG.info('ROVER: %s %d%% [%s]', action, percent, self.mode)

    async def heartbeat(self, client: str):
        async with self.lock:
            if self.owner == client and self.action and not self.estop:
                self.last_heartbeat = time.monotonic()

    async def stop(self, client: Optional[str] = None):
        async with self.lock:
            if client is not None and self.owner not in (None, client):
                return
            await self._stop_locked()
            # Una orden explícita de STOP habilita un nuevo pulso del modo banco.
            self.requires_release = False

    async def emergency(self):
        async with self.lock:
            self.estop = True
            await self._stop_locked()
            LOG.warning('PARADA DE SOFTWARE activada. No sustituye un E-STOP físico')

    async def reset(self):
        async with self.lock:
            # No se autoriza el rearme remoto en modo de motores físicos.
            if self.driver or self.mode == 'BLOQUEADO':
                raise RuntimeError('Rearme remoto bloqueado: inspección y reset físico necesarios')
            if self.error:
                raise RuntimeError(self.error)
            if self.action:
                raise RuntimeError('No se rearma durante movimiento')
            self.estop = False

    async def watchdog(self):
        try:
            while True:
                await asyncio.sleep(0.1)
                async with self.lock:
                    if self.action:
                        now = time.monotonic()
                        expired_burst = (self.mode == 'SERIAL_BANCO' and
                                         now - self.burst_start >= BENCH_BURST_SECONDS)
                        expired_heartbeat = now - self.last_heartbeat > HEARTBEAT_TIMEOUT
                        if expired_burst or expired_heartbeat:
                            LOG.warning('ROVER: %s → STOP',
                                        'límite temporal local' if expired_burst else 'heartbeat perdido')
                            if expired_burst:
                                self.requires_release = True
                            with suppress(Exception):
                                await self._stop_locked()
        except asyncio.CancelledError:
            raise

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        with suppress(Exception):
            await self.stop()
        if self.driver:
            with suppress(Exception):
                await asyncio.to_thread(self.driver.close)
