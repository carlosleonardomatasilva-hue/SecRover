"""SecRover: API integrada IMOU (RTSP/ONVIF) + MongoDB (solo ppm).

Una sola app en localhost:8000 para React. El control físico del rover sigue inhabilitado.
No calibra sensores ni constituye un detector de seguridad certificado.
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from typing import Any
import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import inspect
import os
from pathlib import Path
import secrets
import shutil
import time
from urllib.parse import quote
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')
LOG = logging.getLogger('secrover.integrado')

CAMERA_IP = os.getenv('CAMERA_IP', '192.168.137.200').strip()
CAMERA_PORT = int(os.getenv('CAMERA_PORT', '80'))  # ONVIF
CAMERA_RTSP_PORT = int(os.getenv('CAMERA_RTSP_PORT', '554'))
CAMERA_USER = os.getenv('CAMERA_USER', 'admin')
CAMERA_PASSWORD = os.getenv('CAMERA_PASSWORD', '')
CAMERA_RTSP_PATH = os.getenv('CAMERA_RTSP_PATH', '/cam/realmonitor?channel=1&subtype=0')
WEB_TOKEN = os.getenv('SECROVER_WEB_TOKEN', '')
# Docker publica MongoDB 7 en el HOST: localhost:27018 -> contenedor:27017.
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://127.0.0.1:27018/')
MONGO_DB = os.getenv('MONGO_DB', 'secrover')
MONGO_COLLECTION = os.getenv('MONGO_COLLECTION', 'lecturas_gases')
CAMPOS_PPM = (
    'co_equiv_ppm', 'h2_equiv_ppm', 'nh3_equiv_ppm',
    'etanol_equiv_ppm', 'ch4_equiv_ppm', 'h2s_ppm_aprox',
)
_mongo_client: MongoClient | None = None


def coleccion():
    """Conecta perezosamente para que la cámara funcione aunque MongoDB no esté activo."""
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000, tz_aware=True)
    return _mongo_client[MONGO_DB][MONGO_COLLECTION]


def serializar(doc: dict[str, Any] | None):
    """Respeta el JSON anterior; no publica ADC, voltajes ni claves internas."""
    if doc is None:
        return None
    fecha = doc.get('fecha_recepcion')
    if isinstance(fecha, datetime):
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        fecha = fecha.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    elif fecha is not None:
        fecha = str(fecha)
    lectura = {'_id': str(doc.get('_id', '')), 'fecha_recepcion': fecha}
    for campo in CAMPOS_PPM:
        valor = doc.get(campo)
        try:
            numero = float(valor) if valor is not None else None
            lectura[campo] = numero if numero is not None and math.isfinite(numero) and numero >= 0 else None
        except (TypeError, ValueError, OverflowError):
            lectura[campo] = None
    return lectura

PTZ_SPEED = max(.10, min(.65, float(os.getenv('PTZ_SPEED', '.35'))))
VIDEO_FPS = max(2, min(15, int(os.getenv('VIDEO_FPS', '8'))))
VIDEO_WIDTH = max(320, min(1280, int(os.getenv('VIDEO_WIDTH', '854'))))
DEADMAN_SECONDS = 1.5
RENEW_SECONDS = .85
ALLOWED_ORIGINS = set(filter(None, (
    x.strip() for x in os.getenv('ALLOWED_ORIGINS',
        'http://127.0.0.1:5173,http://localhost:5173,'
        'http://127.0.0.1:8001,http://localhost:8001').split(',')
)))

# Las rutas RTSP deben ser rutas locales de la cámara, no URLs externas.
if not CAMERA_RTSP_PATH.startswith('/') or any(c in CAMERA_RTSP_PATH for c in '\r\n'):
    raise ValueError('CAMERA_RTSP_PATH debe comenzar por / y no contener saltos de línea')


def build_rtsp_url() -> str:
    """Ruta privada para FFmpeg. Nunca devolver esto al navegador."""
    user = quote(CAMERA_USER, safe='')
    password = quote(CAMERA_PASSWORD, safe='')
    return (f'rtsp://{user}:{password}@{CAMERA_IP}:'
            f'{CAMERA_RTSP_PORT}{CAMERA_RTSP_PATH}')


def translate_action(action: str) -> tuple[float, float]:
    """Admite los nombres de React y los nombres del antiguo panel HTML."""
    aliases = {
        'UP': (0, 1), 'DOWN': (0, -1), 'LEFT': (-1, 0), 'RIGHT': (1, 0),
        'UP_LEFT': (-1, 1), 'UP_RIGHT': (1, 1),
        'DOWN_LEFT': (-1, -1), 'DOWN_RIGHT': (1, -1),
        'TILT_UP': (0, 1), 'TILT_DOWN': (0, -1),
        'PAN_LEFT': (-1, 0), 'PAN_RIGHT': (1, 0),
    }
    if action not in aliases:
        raise ValueError('Dirección PTZ no reconocida')
    x, y = aliases[action]
    # Velocidad limitada también al mover en diagonal.
    factor = 2 ** -.5 if x and y else 1.0
    return (round(x * PTZ_SPEED * factor, 5),
            round(y * PTZ_SPEED * factor, 5))


class PTZBridge:
    def __init__(self) -> None:
        self.cam = None
        self.ptz = None
        self.profile_token: Optional[str] = None
        self.ready = False
        self.error = 'Iniciando cámara ONVIF'
        self.lock = asyncio.Lock()
        self.owner: Optional[str] = None
        self.action: Optional[str] = None
        self.last_heartbeat = 0.0
        self.last_move = 0.0
        self.watchdog_task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        if not CAMERA_PASSWORD:
            self.error = 'Falta CAMERA_PASSWORD en backend_camara/.env'
            LOG.warning(self.error)
            return
        try:
            from onvif import ONVIFCamera
            self.cam = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASSWORD)
            await asyncio.wait_for(self.cam.update_xaddrs(), timeout=12)
            # En versiones recientes, crear el servicio puede ser síncrono;
            # las OPERACIONES SOAP (GetProfiles, ContinuousMove) sí son async.
            media = self.cam.create_media_service()
            if inspect.isawaitable(media):
                media = await asyncio.wait_for(media, timeout=8)
            self.ptz = self.cam.create_ptz_service()
            if inspect.isawaitable(self.ptz):
                self.ptz = await asyncio.wait_for(self.ptz, timeout=8)
            profiles = await asyncio.wait_for(media.GetProfiles(), timeout=10)
            profile = next((p for p in profiles if getattr(p, 'PTZConfiguration', None)), None)
            if profile is None:
                raise RuntimeError('La cámara no anuncia un perfil ONVIF PTZ')
            self.profile_token = profile.token
            self.ready = True
            self.error = ''
            LOG.info('Cámara ONVIF conectada a %s:%s (perfil %s)',
                     CAMERA_IP, CAMERA_PORT, getattr(profile, 'Name', 'sin nombre'))
            self.watchdog_task = asyncio.create_task(self.watchdog())
        except Exception as exc:
            self.ready = False
            self.error = f'ONVIF no disponible: {type(exc).__name__} (revisa la terminal)'
            LOG.exception('No se pudo iniciar ONVIF')
            if self.cam:
                with suppress(Exception):
                    await self.cam.close()
            self.cam = None
            self.ptz = None

    async def _move(self, action: str) -> None:
        x, y = translate_action(action)
        await asyncio.wait_for(self.ptz.ContinuousMove({
            'ProfileToken': self.profile_token,
            'Velocity': {'PanTilt': {'x': x, 'y': y}},
            'Timeout': 'PT2S',
        }), timeout=5)
        self.last_move = time.monotonic()

    async def _stop_locked(self) -> None:
        previous = self.action
        self.action = None
        self.owner = None
        self.last_heartbeat = 0.0
        if previous and self.ready:
            await asyncio.wait_for(self.ptz.Stop({
                'ProfileToken': self.profile_token,
                'PanTilt': True, 'Zoom': False,
            }), timeout=5)
            LOG.info('Parada PTZ solicitada')

    async def start(self, client_id: str, action: str) -> None:
        translate_action(action)  # valida antes de llamar a la cámara
        async with self.lock:
            if not self.ready:
                raise RuntimeError(self.error)
            if self.owner and self.owner != client_id:
                raise RuntimeError('Otro navegador controla la cámara')
            if self.action == action and self.owner == client_id:
                self.last_heartbeat = time.monotonic()
                return
            if self.action:
                await self._stop_locked()
            await self._move(action)
            self.action = action
            self.owner = client_id
            self.last_heartbeat = time.monotonic()
            LOG.info('PTZ: %s', action)

    async def heartbeat(self, client_id: str) -> None:
        async with self.lock:
            if self.owner == client_id and self.action:
                self.last_heartbeat = time.monotonic()

    async def stop(self, client_id: Optional[str] = None) -> None:
        async with self.lock:
            if client_id is not None and self.owner not in (None, client_id):
                return
            await self._stop_locked()

    async def home(self, client_id: str) -> None:
        async with self.lock:
            if not self.ready:
                raise RuntimeError(self.error)
            if self.owner and self.owner != client_id:
                raise RuntimeError('Otro navegador controla la cámara')
            await self._stop_locked()
            # Algunas cámaras no implementan Home; en ese caso se reporta el error.
            await asyncio.wait_for(self.ptz.GotoHomePosition({
                'ProfileToken': self.profile_token
            }), timeout=5)

    async def watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(.15)
                async with self.lock:
                    if not self.action or not self.ready:
                        continue
                    now = time.monotonic()
                    if now - self.last_heartbeat > DEADMAN_SECONDS:
                        LOG.warning('Sin heartbeat: deteniendo PTZ')
                        try:
                            await self._stop_locked()
                        except Exception:
                            LOG.exception('Falló STOP automático de cámara')
                    elif now - self.last_move > RENEW_SECONDS:
                        try:
                            await self._move(self.action)
                        except Exception:
                            LOG.exception('Fallo renovando movimiento de cámara')
                            with suppress(Exception):
                                await self._stop_locked()
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self.watchdog_task:
            self.watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.watchdog_task
        with suppress(Exception):
            await self.stop()
        if self.cam:
            with suppress(Exception):
                await self.cam.close()


bridge = PTZBridge()
# Solo válidos mientras dure la conexión WebSocket autenticada.
video_tickets: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bridge.initialize()
    try:
        yield
    finally:
        await bridge.close()
        global _mongo_client
        if _mongo_client is not None:
            _mongo_client.close()
            _mongo_client = None


app = FastAPI(title='SecRover | IMOU y MongoDB', docs_url=None,
              redoc_url=None, lifespan=lifespan)


@app.get('/api/status')
async def status():
    return {
        'online': bridge.ready,
        'camera_online': bridge.ready,
        'camera_ip': CAMERA_IP,
        'error': bridge.error,
        'ptz_active': bridge.action is not None,
        'ffmpeg_disponible': bool(shutil.which('ffmpeg')),
        'video': 'RTSP convertido a MJPEG mediante FFmpeg',
        'rover': {
            'online': False, 'connected': False,
            'mode': 'SOLO_CAMARA', 'estop': False, 'local_test_only': True
        },
        'telemetria_usb': {
            'connected': False, 'motor_fresh': False,
            'battery_v': None, 'motors_cm_s': None
        },
        'gases': {'database': MONGO_DB, 'collection': MONGO_COLLECTION, 'endpoint': '/api/gases/latest'},
        'note': 'Backend unificado: cámara IMOU y MongoDB. Motores deshabilitados.'
    }


@app.get('/api/gases/health')
@app.get('/api/health')
def health():
    try:
        col = coleccion()
        col.database.client.admin.command('ping')
        return {
            'connected': True, 'database': MONGO_DB, 'collection': MONGO_COLLECTION,
            'documents': col.count_documents({}),
        }
    except PyMongoError:
        LOG.exception('No se pudo consultar el estado de MongoDB')
        raise HTTPException(status_code=503, detail='MongoDB no disponible')


@app.get('/api/gases/latest')
@app.get('/api/latest')
def latest():
    try:
        doc = coleccion().find_one(sort=[('fecha_recepcion', DESCENDING), ('_id', DESCENDING)])
        return {'latest': serializar(doc)}
    except PyMongoError:
        LOG.exception('No se pudo leer la ultima muestra de MongoDB')
        raise HTTPException(status_code=503, detail='No se pudo leer MongoDB')


@app.get('/api/gases/history')
@app.get('/api/history')
def history(limit: int = Query(60, ge=1, le=240)):
    try:
        rows = coleccion().find().sort([('fecha_recepcion', DESCENDING), ('_id', DESCENDING)]).limit(limit)
        return {'history': [serializar(row) for row in rows]}
    except PyMongoError:
        LOG.exception('No se pudo leer el historial MongoDB')
        raise HTTPException(status_code=503, detail='No se pudo consultar historial en MongoDB')


@app.get('/api/video.mjpeg')
async def video(request: Request, ticket: str = ''):
    if not ticket or ticket not in video_tickets:
        raise HTTPException(status_code=403, detail='Video requiere ticket de sesión PTZ')
    if not CAMERA_PASSWORD:
        raise HTTPException(status_code=503, detail='Falta CAMERA_PASSWORD')
    if not shutil.which('ffmpeg'):
        raise HTTPException(status_code=503, detail='Instala FFmpeg: sudo apt install ffmpeg')

    async def video_stream():
        proc = None
        try:
            # FFmpeg utiliza una URL con credenciales en los argumentos del proceso;
            # ejecutar en la Orange Pi y proteger el acceso local.
            proc = await asyncio.create_subprocess_exec(
                'ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error',
                '-rtsp_transport', 'tcp',
                '-fflags', 'nobuffer', '-flags', 'low_delay',
                '-i', build_rtsp_url(), '-an', '-sn',
                '-vf', f'fps={VIDEO_FPS},scale={VIDEO_WIDTH}:-2',
                '-c:v', 'mjpeg', '-q:v', '6', '-f', 'mpjpeg', 'pipe:1',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            while ticket in video_tickets:
                # Sin fotogramas -> cortar, para que el navegador pueda reintentar.
                try:
                    data = await asyncio.wait_for(proc.stdout.read(16384), timeout=12)
                except asyncio.TimeoutError:
                    LOG.warning('RTSP no entregó datos en 12 segundos')
                    break
                if not data:
                    LOG.warning('FFmpeg terminó: comprueba RTSP, IP y contraseña')
                    break
                yield data
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Error iniciando conversión RTSP->MJPEG')
        finally:
            if proc and proc.returncode is None:
                proc.kill()
                with suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=4)

    return StreamingResponse(
        video_stream(), media_type='multipart/x-mixed-replace; boundary=ffmpeg',
        headers={
            'Cache-Control': 'no-store, no-cache, must-revalidate',
            'Pragma': 'no-cache',
            'X-Content-Type-Options': 'nosniff',
            'X-Accel-Buffering': 'no',
            'Referrer-Policy': 'no-referrer',
        }
    )


def origin_permitido(ws: WebSocket) -> bool:
    # Vite hace proxy WS conservando Origin (p.ej. 127.0.0.1:5173).
    return ws.headers.get('origin', '') in ALLOWED_ORIGINS


async def autenticar_ws(ws: WebSocket) -> bool:
    if not origin_permitido(ws):
        await ws.close(code=1008)
        return False
    if not WEB_TOKEN and ws.client and ws.client.host not in ('127.0.0.1', '::1'):
        await ws.close(code=1008)
        return False
    try:
        data = await asyncio.wait_for(ws.receive_json(), timeout=15)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await ws.close(code=1008)
        return False
    if not isinstance(data, dict) or data.get('type') != 'auth':
        await ws.close(code=1008)
        return False
    if WEB_TOKEN and not secrets.compare_digest(str(data.get('token', '')), WEB_TOKEN):
        await ws.send_json({'type': 'auth_error', 'message': 'Clave SecRover incorrecta'})
        await ws.close(code=1008)
        return False
    return True


@app.websocket('/ws/ptz')
async def ptz_ws(ws: WebSocket):
    await ws.accept()
    if not await autenticar_ws(ws):
        return
    client_id = secrets.token_hex(12)
    ticket = secrets.token_urlsafe(24)
    video_tickets.add(ticket)
    try:
        # React reconstruido espera DOS mensajes: ready y video_ticket.
        # El backend HTML antiguo recibía video_ticket en ready.
        await ws.send_json({
            'type': 'ready', 'online': bridge.ready,
            'camera_online': bridge.ready, 'video_ticket': ticket,
            'message': 'ONVIF conectado' if bridge.ready else bridge.error,
        })
        await ws.send_json({'type': 'video_ticket', 'ticket': ticket})
        while True:
            data = await ws.receive_json()
            if not isinstance(data, dict):
                continue
            typ = data.get('type')
            try:
                if typ == 'start':
                    action = str(data.get('action', ''))
                    await bridge.start(client_id, action)
                    await ws.send_json({'type': 'moving', 'action': action})
                elif typ == 'heartbeat':
                    await bridge.heartbeat(client_id)
                elif typ == 'stop':
                    await bridge.stop(client_id)
                    await ws.send_json({'type': 'stopped'})
                elif typ == 'home':
                    await bridge.home(client_id)
                    await ws.send_json({'type': 'home', 'message': 'Home solicitado'})
                elif typ == 'ping':
                    await ws.send_json({'type': 'pong', 'online': bridge.ready})
                else:
                    await ws.send_json({'type': 'error', 'message': 'Comando PTZ desconocido'})
            except (ValueError, RuntimeError) as exc:
                await ws.send_json({'type': 'error', 'message': str(exc)})
            except Exception as exc:
                LOG.exception('Error en cámara PTZ: %s', type(exc).__name__)
                await ws.send_json({'type': 'error', 'message': 'Cámara rechazó el comando ONVIF'})
    except WebSocketDisconnect:
        pass
    finally:
        video_tickets.discard(ticket)
        with suppress(Exception):
            await bridge.stop(client_id)


@app.websocket('/ws/rover')
async def rover_no_disponible(ws: WebSocket):
    """Compatibilidad de interfaz; no abre USB, serial ni controla motores."""
    await ws.accept()
    if not await autenticar_ws(ws):
        return
    try:
        await ws.send_json({
            'type': 'ready', 'online': False,
            'mode': 'SOLO_CAMARA', 'connected': False,
            'estop': False, 'local_test_only': True, 'max_cm_s': 0,
        })
        await ws.send_json({'type': 'telemetry', 'connected': False,
                            'motor_fresh': False, 'motors_cm_s': None,
                            'battery_v': None})
        while True:
            data = await ws.receive_json()
            if isinstance(data, dict) and data.get('type') == 'ping':
                await ws.send_json({'type': 'pong', 'online': False,
                                    'mode': 'SOLO_CAMARA'})
            elif isinstance(data, dict) and data.get('type') not in ('stop', 'heartbeat'):
                await ws.send_json({'type': 'error', 'message':
                                    'Backend solo de cámara: movimiento del rover deshabilitado'})
    except WebSocketDisconnect:
        return


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SecRover · cámara IMOU y gases MongoDB')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8001, type=int)
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not WEB_TOKEN:
        parser.error('Acceso por red requiere SECROVER_WEB_TOKEN')
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, workers=1)
