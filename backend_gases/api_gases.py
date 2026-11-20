"""SecRover: backend único FastAPI para IMOU, MongoDB y Yahboom Quad-MD.

Control físico deshabilitado por defecto (ROVER_MODE=SIMULACION).
SERIAL_BANCO solo para ensayos locales con ruedas suspendidas;
la parada de software no sustituye el corte físico de potencia.
Las ppm son estimaciones y no equivalen a un detector certificado.
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
from fastapi.responses import FileResponse, StreamingResponse
from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError
from rover_bridge import RoverBridge
from quadmd_telemetry import QuadMDTelemetry
from recording_manager import RecordingError, RecordingManager

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

# Archivos MP4 de rondines guardados en la Orange Pi; no en el navegador.
RECORDINGS_DIR = Path(os.getenv('RECORDINGS_DIR', str(BASE / 'grabaciones')))


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
        'http://127.0.0.1:8000,http://localhost:8000').split(',')
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
    """Comandos de la cámara IMOU compatibles con React (sin alias antiguos)."""
    aliases = {
        'UP': (0, 1), 'DOWN': (0, -1), 'LEFT': (-1, 0), 'RIGHT': (1, 0),
        'UP_LEFT': (-1, 1), 'UP_RIGHT': (1, 1),
        'DOWN_LEFT': (-1, -1), 'DOWN_RIGHT': (1, -1),
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
            self.error = 'Falta CAMERA_PASSWORD en .env'
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
rover = RoverBridge()
telemetry: QuadMDTelemetry | None = None
# Solo válidos mientras dure la conexión WebSocket autenticada.
video_tickets: set[str] = set()

recordings = RecordingManager(
    RECORDINGS_DIR, build_rtsp_url,
    max_minutes=int(os.getenv('RECORDING_MAX_MINUTES', '0')),
    min_free_mb=int(os.getenv('RECORDING_MIN_FREE_MB', '512')),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telemetry, _mongo_client
    # Solo una instancia por controlador y un único lector de la Quad-MD.
    # MongoDB se conecta por demanda; un fallo de sensores no bloquea la cámara.
    await rover.initialize()
    telemetry = QuadMDTelemetry(
        existing_serial=rover.driver.serial if rover.driver else None,
        send_lock=rover.driver.send_lock if rover.driver else None,
    )
    telemetry.start()
    try:
        await bridge.initialize()
        yield
    finally:
        # Cerrar MP4 limpiamente antes de apagar FastAPI.
        await recordings.shutdown()
        # Cerrar primero el lector USB que comparte la conexión con motores.
        if telemetry is not None:
            await asyncio.to_thread(telemetry.close)
            telemetry = None
        await rover.close()
        await bridge.close()
        if _mongo_client is not None:
            _mongo_client.close()
            _mongo_client = None


app = FastAPI(title='SecRover | IMOU, MongoDB y Quad-MD', docs_url=None,
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
        'rondin': recordings.current(),
        'rover': rover.info(),
        'telemetria_usb': telemetry.snapshot() if telemetry else {
            'connected': False, 'motor_fresh': False, 'battery_v': None, 'motors_cm_s': None
        },
        'gases': {'database': MONGO_DB, 'collection': MONGO_COLLECTION, 'endpoint': '/api/gases/latest'},
        'note': 'Quad-MD real para lectura; movimiento en simulación por defecto.'
    }


def _auth_recordings(request: Request) -> None:
    """Grabaciones necesitan token incluso si se accede por proxy local."""
    if not WEB_TOKEN:
        raise HTTPException(status_code=503, detail='Configura SECROVER_WEB_TOKEN en .env para grabaciones')
    header = request.headers.get('authorization', '')
    scheme, _, supplied = header.partition(' ')
    if scheme.lower() != 'bearer' or not secrets.compare_digest(supplied, WEB_TOKEN):
        raise HTTPException(status_code=401, detail='Token SecRover ausente o incorrecto')


def _recording_error(exc: RecordingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@app.get('/api/rondines/status')
async def rondines_status(request: Request):
    _auth_recordings(request)
    return recordings.current()


@app.get('/api/rondines/history')
async def rondines_history(request: Request, limit: int = Query(30, ge=1, le=100)):
    _auth_recordings(request)
    return {'rondines': recordings.list_records(limit)}


@app.post('/api/rondines/start')
async def rondines_start(request: Request):
    _auth_recordings(request)
    if not CAMERA_PASSWORD:
        raise HTTPException(status_code=503, detail='Configura CAMERA_PASSWORD para RTSP')
    try:
        info = await recordings.start()
        return {'active': info, 'recording': True}
    except RecordingError as exc:
        raise _recording_error(exc) from exc


@app.post('/api/rondines/stop')
async def rondines_stop(request: Request):
    _auth_recordings(request)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail='Se esperaba JSON con el ID del rondín')
    record_id = payload.get('id') if isinstance(payload, dict) else None
    if not record_id or not isinstance(record_id, str):
        raise HTTPException(status_code=400, detail='Debes indicar el ID del rondín activo')
    try:
        info = await recordings.stop(record_id)
        return {'rondin': info, 'recording': False}
    except RecordingError as exc:
        raise _recording_error(exc) from exc


@app.get('/api/rondines/{record_id}/ticket')
async def rondin_video_ticket(record_id: str, request: Request):
    _auth_recordings(request)
    try:
        return {'ticket': recordings.issue_ticket(record_id), 'ttl_seconds': 1200}
    except RecordingError as exc:
        raise _recording_error(exc) from exc


@app.get('/api/rondines/{record_id}/video')
async def rondin_video(record_id: str, ticket: str = '', download: bool = False):
    file = recordings.video_file(record_id, ticket)
    if file is None:
        raise HTTPException(status_code=403, detail='Vídeo no disponible o ticket expirado')
    headers = {'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
               'X-Content-Type-Options': 'nosniff'}
    kwargs = {'filename': file.name, 'content_disposition_type': 'attachment'} if download else {}
    return FileResponse(file, media_type='video/mp4', headers=headers, **kwargs)


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


@app.get('/api/gases/history/page')
def pagina_historial(before: str = ''):
    from bson import ObjectId

    filtro = {}
    if before:
        if not ObjectId.is_valid(before):
            raise HTTPException(status_code=400, detail='Cursor de historial inválido')
        filtro['_id'] = {'$lt': ObjectId(before)}
    try:
        # El índice _id ya existe. El registro 61 indica si hay otra página.
        docs = list(coleccion().find(filtro).sort('_id', DESCENDING).limit(61))
        return {
            'history': [serializar(doc) for doc in docs[:60]],
            'next_cursor': str(docs[59]['_id']) if len(docs) > 60 else None,
        }
    except PyMongoError:
        LOG.exception('Error al consultar la página de gases')
        raise HTTPException(status_code=503, detail='No se pudo consultar el historial')


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
async def rover_ws(ws: WebSocket):
    """Control de 4 direcciones y telemetría Quad-MD, con autenticación.

    En SERIAL_BANCO solo se permiten clientes locales y lecturas recientes
    de encoders. React sigue bloqueando la conducción física por diseño.
    """
    await ws.accept()
    if not await autenticar_ws(ws):
        return

    client_id = secrets.token_hex(12)
    remote = (ws.client.host if ws.client else '') not in ('127.0.0.1', '::1')
    # No permitir prueba motriz de banco desde dispositivos remotos.
    # Un proxy local no debe exponerse fuera del equipo en ese modo.
    if rover.mode == 'SERIAL_BANCO' and (
        remote or ws.headers.get('origin', '') not in (
            'http://127.0.0.1:5173', 'http://localhost:5173',
            'http://127.0.0.1:8000', 'http://localhost:8000'
        )
    ):
        await ws.send_json({'type': 'auth_error', 'message':
                            'SERIAL_BANCO solo en navegador local de la Orange Pi'})
        await ws.close(code=1008)
        return

    send_lock = asyncio.Lock()
    telemetry_task: asyncio.Task | None = None

    async def safe_send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def publish_telemetry() -> None:
        try:
            while True:
                snap = telemetry.snapshot() if telemetry else {
                    'connected': False, 'motor_fresh': False,
                    'motors_cm_s': None, 'battery_v': None,
                    'error': 'Telemetría USB sin iniciar', 'units': 'cm/s'
                }
                await safe_send({'type': 'telemetry', **snap})
                await asyncio.sleep(.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Se cerró el canal de telemetría Quad-MD')

    try:
        await safe_send({'type': 'ready', **rover.info()})
        telemetry_task = asyncio.create_task(publish_telemetry())
        while True:
            data = await ws.receive_json()
            if not isinstance(data, dict):
                continue
            typ = data.get('type')
            try:
                if typ == 'move':
                    # No se permite ningún movimiento, ni siquiera simulado,
                    # que no esté en rover_bridge.ALLOWED.
                    if rover.mode in ('SERIAL_BANCO', 'SERIAL') and not (
                        telemetry and telemetry.snapshot().get('motor_fresh')
                    ):
                        raise RuntimeError('Sin encoders recientes; movimiento físico bloqueado')
                    await rover.move(client_id, data.get('action'), data.get('percent'))
                    await safe_send({'type': 'moving', 'action': rover.action,
                                     'percent': rover.percent, 'mode': rover.mode})
                elif typ == 'heartbeat':
                    await rover.heartbeat(client_id)
                elif typ == 'stop':
                    await rover.stop(client_id)
                    await safe_send({'type': 'stopped', **rover.info()})
                elif typ == 'emergency':
                    await rover.emergency()
                    await safe_send({'type': 'emergency', **rover.info()})
                elif typ == 'reset':
                    await rover.reset()  # permitido solo en simulación
                    await safe_send({'type': 'rearmed', **rover.info()})
                elif typ == 'ping':
                    await safe_send({'type': 'pong', **rover.info()})
                else:
                    await safe_send({'type': 'error',
                                     'message': 'Comando de rover desconocido'})
            except Exception as exc:
                LOG.warning('ROVER %s: %s', type(exc).__name__, exc)
                await safe_send({'type': 'error', 'message': str(exc),
                                 'estop': rover.estop})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if telemetry_task is not None:
            telemetry_task.cancel()
            with suppress(asyncio.CancelledError):
                await telemetry_task
        # Solicita STOP cuando el operador desconecta el navegador.
        with suppress(Exception):
            await rover.stop(client_id)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SecRover: servidor único FastAPI')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not WEB_TOKEN:
        parser.error('Para exponer la API en red configura SECROVER_WEB_TOKEN')
    if os.getenv('ROVER_MODE', 'SIMULACION').upper() == 'SERIAL_BANCO' and args.host not in (
        '127.0.0.1', 'localhost', '::1'
    ):
        parser.error('SERIAL_BANCO solo puede escucharse desde localhost')
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level='info')
