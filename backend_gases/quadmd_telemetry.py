"""Lectura real de telemetría Quad-MD para SecRover.

En modo SIMULACION el servidor abre /dev/ttyUSB0 SOLO PARA LEER: los únicos
comandos transmitidos por este módulo son $upload:0,0,1#, $upload:0,0,0#
y $read_vol#. Nunca envía órdenes $spd o $pwm.

Si se permite modo SERIAL físico en el futuro, comparte el puerto YA ABIERTO
por SerialMotors. No se abren dos conexiones sobre /dev/ttyUSB0.
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from typing import Any

LOG = logging.getLogger('secrover.telemetria')
FRAME = re.compile(rb'\$[^$#\r\n]{1,128}#')
MSPD = re.compile(rb'^\$MSPD:([^#]+)#$')
BATTERY = re.compile(rb'^\$Battery:\s*([0-9]+(?:\.[0-9]+)?)V#$', re.I)


class QuadMDTelemetry:
    def __init__(self, *, port: str | None = None, baudrate: int = 115200,
                 existing_serial=None, send_lock=None, serial_factory=None,
                 reconnect_seconds: float = 3.0):
        self.port = port or os.getenv('ROVER_SERIAL_PORT', '/dev/ttyUSB0')
        self.baudrate = baudrate
        self.existing_serial = existing_serial
        self.send_lock = send_lock or threading.Lock()
        self.serial_factory = serial_factory
        self.reconnect_seconds = reconnect_seconds
        self._thread: threading.Thread | None = None
        self._terminate = threading.Event()
        self._values: tuple[float, float, float, float] | None = None
        self._voltage: float | None = None
        self._last_mspd = 0.0
        self._last_voltage = 0.0
        self._last_error = 'Iniciando lectura USB...'
        self._connected = False
        self._frames = 0
        self._state_lock = threading.Lock()

    @staticmethod
    def decode(frame: bytes) -> tuple[str, Any] | None:
        match = MSPD.fullmatch(frame)
        if match:
            chunks = match.group(1).split(b',')
            if len(chunks) != 4:
                return None
            try:
                values = tuple(float(c.strip()) for c in chunks)
            except ValueError:
                return None
            # Quad-MD reporta velocidades mm/s, normalizadas a cm/s para el panel.
            if any(not math.isfinite(v) or abs(v) > 100_000 for v in values):
                return None
            return 'motor', tuple(round(v / 10, 2) for v in values)
        match = BATTERY.fullmatch(frame)
        if match:
            voltage = float(match.group(1))
            if math.isfinite(voltage) and 0 <= voltage <= 100:
                return 'battery', voltage
        return None

    def _accept(self, frame: bytes):
        parsed = self.decode(frame)
        if not parsed:
            return
        now = time.monotonic()
        with self._state_lock:
            if parsed[0] == 'motor':
                self._values = parsed[1]
                self._last_mspd = now
                self._frames += 1
            elif parsed[0] == 'battery':
                self._voltage = parsed[1]
                self._last_voltage = now

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._state_lock:
            motor_fresh = self._connected and bool(self._last_mspd) and now - self._last_mspd <= 2.5
            battery_fresh = self._connected and bool(self._last_voltage) and now - self._last_voltage <= 35
            return {
                'connected': self._connected,
                'motor_fresh': motor_fresh,
                'motors_cm_s': list(self._values) if motor_fresh else None,
                'battery_v': self._voltage if battery_fresh else None,
                'frames': self._frames,
                'error': '' if self._connected else self._last_error,
                'port': self.port,
                'units': 'cm/s',
            }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._terminate.clear()
        self._thread = threading.Thread(target=self._run, name='quadmd-telemetry', daemon=True)
        self._thread.start()

    def _write(self, serial_port, command: bytes):
        with self.send_lock:
            serial_port.write(command)
            serial_port.flush()

    def _run(self):
        # Reconecta de manera automática si se desconecta el USB. Nunca envía movimiento.
        while not self._terminate.is_set():
            port = None
            owned = self.existing_serial is None
            try:
                if owned:
                    if self.serial_factory:
                        port = self.serial_factory(port=self.port, baudrate=self.baudrate,
                                                   timeout=0.15, write_timeout=0.5)
                    else:
                        import serial
                        port = serial.Serial(port=self.port, baudrate=self.baudrate,
                                             timeout=0.15, write_timeout=0.5,
                                             exclusive=True)
                    # Tiempo de estabilización USB equivalente a la prueba anterior.
                    if self._terminate.wait(1.0):
                        return
                    with self._state_lock:
                        self._frames = 0
                else:
                    port = self.existing_serial
                self._write(port, b'$upload:0,0,1#')
                with self._state_lock:
                    self._connected = True
                    self._last_error = ''
                LOG.info('Telemetría Quad-MD conectada a %s (puerto compartido=%s)', self.port, not owned)
                pending = bytearray()
                next_battery = 0.0
                while not self._terminate.is_set():
                    if time.monotonic() >= next_battery:
                        self._write(port, b'$read_vol#')
                        next_battery = time.monotonic() + 10.0
                    chunk = port.read(256)
                    if not chunk:
                        continue
                    pending.extend(chunk)
                    # La controladora usa $...# y puede enviar fragmentos parciales o concatenados.
                    while True:
                        start = pending.find(b'$')
                        if start < 0:
                            pending.clear()
                            break
                        if start:
                            del pending[:start]
                        end = pending.find(b'#', 1)
                        if end < 0:
                            if len(pending) > 512:
                                pending.clear()
                            break
                        frame = bytes(pending[:end + 1])
                        del pending[:end + 1]
                        if FRAME.fullmatch(frame):
                            self._accept(frame)
            except Exception as exc:
                with self._state_lock:
                    self._connected = False
                    self._last_error = f'{type(exc).__name__}: {exc}'[:180]
                LOG.warning('Telemetría Quad-MD sin conexión: %s', self._last_error)
            finally:
                with self._state_lock:
                    self._connected = False
                    self._last_mspd = 0
                    self._last_voltage = 0
                if port is not None:
                    try:
                        self._write(port, b'$upload:0,0,0#')
                    except Exception:
                        pass
                    if owned:
                        try:
                            port.close()
                        except Exception:
                            pass
            if not owned:
                # La conexión compartida pertenece al driver del rover.
                return
            self._terminate.wait(self.reconnect_seconds)

    def close(self):
        self._terminate.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                LOG.warning('Hilo de telemetría no respondió aún al cierre')
            self._thread = None
