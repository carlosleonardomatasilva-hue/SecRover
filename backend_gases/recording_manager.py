"""Grabaciones RTSP de rondines en la Orange Pi.

Un solo FFmpeg por rondín; no envía órdenes de movimiento al rover.
Los MP4 terminados y sus metadatos persisten en disco local.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import time
from typing import Any, Callable

LOG = logging.getLogger('secrover.grabaciones')
ID_PATTERN = re.compile(r'^rondin_\d{8}T\d{6}Z_[0-9a-f]{12}$')


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


class RecordingError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class RecordingManager:
    def __init__(self, folder: Path, rtsp_builder: Callable[[], str],
                 max_minutes: int = 0, min_free_mb: int = 512):
        self.folder = Path(folder).resolve()
        self.rtsp_builder = rtsp_builder
        # 0 = sin tope de duración; sigue vigente la protección de espacio en disco.
        if max_minutes < 0:
            raise ValueError('max_minutes debe ser >= 0 (0 significa sin límite)')
        self.max_seconds = max_minutes * 60 if max_minutes > 0 else None
        self.min_free_bytes = max(128, min_free_mb) * 1024 * 1024
        self.lock = asyncio.Lock()
        self.active: dict[str, Any] | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.monitor_task: asyncio.Task | None = None
        self.tickets: dict[str, tuple[str, float]] = {}
        self.last_error = ''
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Un archivo .json con estado grabando tras un reinicio indica grabación interrumpida.
        for path in self.folder.glob('rondin_*.json'):
            try:
                info = json.loads(path.read_text(encoding='utf-8'))
                if info.get('status') == 'grabando':
                    info.update(status='interrumpido', ended_at=utc_now(),
                                error='Servidor reiniciado; revisa el archivo parcial')
                    self._save(info)
            except (OSError, json.JSONDecodeError):
                LOG.warning('Metadatos ilegibles en %s', path.name)

    def _save(self, info: dict[str, Any]) -> None:
        path = self.folder / f"{info['id']}.json"
        tmp = self.folder / f"{info['id']}.json.tmp"
        data = json.dumps(info, ensure_ascii=False, indent=2)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(data)
        tmp.replace(path)

    def _free_ok(self) -> bool:
        return shutil.disk_usage(self.folder).free >= self.min_free_bytes

    def current(self) -> dict[str, Any]:
        # Llamada informativa: no bloquea, ni afirma tener fotogramas capturados.
        if not self.active:
            return {'active': None, 'recording': False, 'last_error': self.last_error}
        return {'active': dict(self.active), 'recording': True, 'last_error': self.last_error}

    def list_records(self, limit: int = 50) -> list[dict[str, Any]]:
        files = sorted(self.folder.glob('rondin_*.json'), reverse=True)
        results = []
        for file in files:
            try:
                info = json.loads(file.read_text(encoding='utf-8'))
                if ID_PATTERN.fullmatch(str(info.get('id', ''))):
                    results.append(info)
            except (OSError, json.JSONDecodeError):
                continue
            if len(results) >= limit:
                break
        return results

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        if not ID_PATTERN.fullmatch(record_id):
            return None
        path = self.folder / f'{record_id}.json'
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None

    async def start(self) -> dict[str, Any]:
        async with self.lock:
            if self.active:
                raise RecordingError('Ya existe un rondín grabándose')
            if not shutil.which('ffmpeg'):
                raise RecordingError('Falta FFmpeg: sudo apt install ffmpeg', 503)
            if not self._free_ok():
                raise RecordingError('Poco espacio libre para grabar', 507)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            record_id = f'rondin_{stamp}_{secrets.token_hex(6)}'
            output = self.folder / f'{record_id}.partial.mp4'
            info = {
                'id': record_id, 'started_at': utc_now(), 'ended_at': None,
                'duration_seconds': None, 'status': 'grabando',
                'filename': None, 'size_bytes': 0, 'error': None,
            }
            try:
                # La opción copy conserva la calidad original y consume muy poca CPU.
                # Si la IMOU emite H.265 y no es posible configurarla en H.264,
                # RECORDING_VIDEO_MODE=h264 codifica archivos compatibles con navegador.
                # Ojo: puede aumentar mucho el uso de CPU en la Orange Pi.
                video_mode = os.getenv('RECORDING_VIDEO_MODE', 'copy').strip().lower()
                if video_mode not in ('copy', 'h264'):
                    raise RecordingError('RECORDING_VIDEO_MODE debe ser copy o h264', 503)
                video_encoder = (['-c:v', 'copy'] if video_mode == 'copy' else [
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26',
                    '-pix_fmt', 'yuv420p',
                ])
                cmd = [
                    'ffmpeg', '-hide_banner', '-loglevel', 'error',
                    '-rtsp_transport', 'tcp', '-i', self.rtsp_builder(),
                    '-map', '0:v:0', '-an', '-sn', '-dn',
                    *video_encoder, '-movflags', '+faststart',
                    '-f', 'mp4', str(output),
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except (OSError, ValueError) as exc:
                raise RecordingError(f'No se pudo iniciar FFmpeg: {type(exc).__name__}', 503) from exc
            self.proc = proc
            self.active = info
            self.last_error = ''
            self._save(info)
            # Comprobar que FFmpeg no haya fallado de inmediato (RTSP/códec/credenciales).
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.monitor_task = asyncio.create_task(self._monitor(record_id))
                LOG.info('Rondín %s: grabación solicitada (%s)', record_id, self.folder)
                return dict(info)
            await self._finalize('interrumpido', 'FFmpeg terminó al iniciar: revisa RTSP y códec')
            raise RecordingError('No comenzó la grabación. Revisa RTSP, credenciales y FFmpeg', 503)

    async def stop(self, expected_id: str | None = None, reason: str = '') -> dict[str, Any]:
        async with self.lock:
            if not self.active:
                raise RecordingError('No hay rondín en grabación')
            if expected_id and self.active['id'] != expected_id:
                raise RecordingError('El rondín activo cambió; actualiza la página')
            proc = self.proc
            if proc and proc.returncode is None:
                # 'q' solicita cierre normal y escribe el índice final del MP4.
                try:
                    if proc.stdin:
                        proc.stdin.write(b'q\n')
                        await proc.stdin.drain()
                    await asyncio.wait_for(proc.wait(), timeout=12)
                except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError, OSError):
                    if proc.returncode is None:
                        with suppress(ProcessLookupError):
                            proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            with suppress(ProcessLookupError):
                                proc.kill()
                            await proc.wait()
            return await self._finalize('interrumpido' if reason else 'finalizado', reason)

    async def _finalize(self, outcome: str, reason: str = '') -> dict[str, Any]:
        info = self.active
        if not info:
            raise RecordingError('No existe una grabación activa')
        proc = self.proc
        record_id = info['id']
        partial = self.folder / f'{record_id}.partial.mp4'
        final = self.folder / f'{record_id}.mp4'
        start = datetime.fromisoformat(info['started_at'].replace('Z', '+00:00'))
        info['ended_at'] = utc_now()
        info['duration_seconds'] = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
        good = proc is not None and proc.returncode == 0 and partial.exists() and partial.stat().st_size > 1024
        if good:
            partial.replace(final)
            info['filename'] = final.name
            info['size_bytes'] = final.stat().st_size
            info['status'] = outcome
            info['error'] = reason or None
            LOG.info('Grabación %s guardada: %s (%s bytes)', record_id, final, info['size_bytes'])
        else:
            info['status'] = 'interrumpido'
            info['error'] = reason or 'FFmpeg no finalizó MP4 correctamente; revisa RTSP y archivo parcial'
            if partial.exists():
                info['size_bytes'] = partial.stat().st_size
            self.last_error = info['error']
            LOG.warning('Rondín %s no generó MP4 válido: %s', record_id, info['error'])
        self._save(info)
        self.active = None
        self.proc = None
        return dict(info)

    async def _monitor(self, record_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(2)
                async with self.lock:
                    if not self.active or self.active['id'] != record_id:
                        return
                    if self.proc and self.proc.returncode is not None:
                        await self._finalize('interrumpido', 'FFmpeg terminó inesperadamente')
                        return
                    start = datetime.fromisoformat(self.active['started_at'].replace('Z', '+00:00'))
                    duration = (datetime.now(timezone.utc) - start).total_seconds()
                    partial = self.folder / f'{record_id}.partial.mp4'
                    if duration > 25 and (not partial.exists() or partial.stat().st_size < 128):
                        reason = 'Sin datos de vídeo tras 25 segundos; comprueba RTSP'
                    elif self.max_seconds is not None and duration >= self.max_seconds:
                        # Evitar deadlock: stop() adquiere el mismo lock.
                        reason = 'Duración máxima alcanzada'
                    elif not self._free_ok():
                        reason = 'Poco espacio disponible'
                    else:
                        continue
                with suppress(RecordingError):
                    await self.stop(record_id, reason=reason)
                return
        except asyncio.CancelledError:
            return

    def issue_ticket(self, record_id: str) -> str:
        info = self.get_record(record_id)
        if not info or not info.get('filename'):
            raise RecordingError('El vídeo todavía no está disponible', 404)
        now = time.monotonic()
        self.tickets = {k: value for k, value in self.tickets.items() if value[1] > now}
        ticket = secrets.token_urlsafe(28)
        self.tickets[ticket] = (record_id, now + 1200)  # 20 minutos
        return ticket

    def video_file(self, record_id: str, ticket: str) -> Path | None:
        entry = self.tickets.get(ticket)
        if not entry or entry[0] != record_id or entry[1] <= time.monotonic():
            return None
        info = self.get_record(record_id)
        if not info or not info.get('filename'):
            return None
        expected = f'{record_id}.mp4'
        if info['filename'] != expected:
            return None
        file = self.folder / expected
        return file if file.is_file() else None

    async def shutdown(self) -> None:
        if self.active:
            with suppress(RecordingError):
                await self.stop(self.active['id'], reason='Servidor detenido')
        if self.monitor_task:
            self.monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor_task
