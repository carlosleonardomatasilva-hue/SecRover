#!/usr/bin/env python3
"""Convierte un MP4 FINALIZADO de SecRover a H.264 compatible con navegador.

Uso:
  python3 reparar_video.py --ultimo
  python3 reparar_video.py --id rondin_20261118T001122Z_aabbccddeeff

Conserva el original como <id>.original.mp4. No toca rondines en curso.
Funciona sin Internet. Conviene ejecutarlo sin otros trabajos intensivos.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
ID = re.compile(r'^rondin_\d{8}T\d{6}Z_[0-9a-f]{12}$')
MB = 1024 * 1024


def _read_env_folder() -> Path:
    # Respetar .env sin imprimir contraseñas ni requerir dependencias extra.
    explicit = os.environ.get('RECORDINGS_DIR')
    if not explicit:
        env_file = BASE / '.env'
        if env_file.exists():
            for raw in env_file.read_text(encoding='utf-8').splitlines():
                line = raw.strip()
                if line.startswith('RECORDINGS_DIR='):
                    explicit = line.split('=', 1)[1].strip().strip('"\'')
                    break
    return Path(explicit).expanduser().resolve() if explicit else BASE / 'grabaciones'


def probe(path: Path) -> dict:
    run = subprocess.run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name,profile,pix_fmt,width,height',
        '-of', 'json', str(path)
    ], capture_output=True, text=True, timeout=30, check=True)
    streams = json.loads(run.stdout).get('streams') or []
    if not streams:
        raise RuntimeError('El MP4 no contiene vídeo reconocible por ffprobe')
    return streams[0]


def convert(record_id: str, folder: Path, force: bool = False) -> str:
    if not ID.fullmatch(record_id):
        raise ValueError('ID de rondín inválido')
    original = folder / (record_id + '.mp4')
    metadata = folder / (record_id + '.json')
    backup = folder / (record_id + '.original.mp4')
    temporary = folder / (record_id + '.web.tmp.mp4')
    if not original.is_file():
        raise FileNotFoundError(f'No existe: {original}')
    if not metadata.is_file():
        raise FileNotFoundError(f'Faltan los metadatos: {metadata}')
    info = json.loads(metadata.read_text(encoding='utf-8'))
    if info.get('id') != record_id or info.get('status') not in ('finalizado', 'interrumpido') or info.get('filename') != original.name:
        raise RuntimeError('Solo se permite convertir grabaciones terminadas y registradas')
    data = probe(original)
    print(f'Archivo: {original.name}')
    print(f"Códec: {data.get('codec_name', '?')} | píxeles: {data.get('pix_fmt', '?')} | tamaño: {data.get('width', '?')}x{data.get('height', '?')}")
    if not force and data.get('codec_name') == 'h264' and data.get('pix_fmt') == 'yuv420p':
        return 'El archivo ya tiene H.264/yuv420p; revisa el acceso HTTP, ticket o cabeceras Range.'
    if backup.exists():
        raise RuntimeError('Ya existe la copia del original. No se sobrescribe para evitar pérdidas')
    if temporary.exists():
        raise RuntimeError('Existe un temporal de conversión; revísalo y elimínalo manualmente si quedó de un fallo anterior')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('Instala ffmpeg: sudo apt install ffmpeg')
    min_free_mb = max(512, int(os.environ.get('RECORDING_MIN_FREE_MB', '512')))
    # Margen estimado para copia transcodificada. Se vigila el disco durante el proceso.
    reserve = min_free_mb * MB
    estimated_needed = int(original.stat().st_size * 1.25) + reserve
    if shutil.disk_usage(folder).free < estimated_needed:
        raise RuntimeError(f'Espacio insuficiente para convertir. Necesitas al menos ~{estimated_needed // MB} MiB libres')
    args = ['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-i', str(original),
            '-map', '0:v:0', '-an', '-sn', '-dn', '-c:v', 'libx264',
            '-preset', 'veryfast', '-crf', '25', '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart', '-f', 'mp4', str(temporary)]
    print('Convirtiendo a H.264. El proceso puede tardar con vídeos largos...')
    try:
        with subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) as p:
            while p.poll() is None:
                time.sleep(2)
                if shutil.disk_usage(folder).free < reserve:
                    p.terminate()
                    try:
                        p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        p.kill()
                    raise RuntimeError('Conversión detenida: disco con poco espacio libre')
            error = p.stderr.read() if p.stderr else ''
            if p.returncode != 0:
                raise RuntimeError('FFmpeg falló: ' + (error.strip()[-1000:] or f'código {p.returncode}'))
        if not temporary.exists() or temporary.stat().st_size < 1024:
            raise RuntimeError('La conversión no generó un MP4 válido')
        check = probe(temporary)
        if check.get('codec_name') != 'h264' or check.get('pix_fmt') != 'yuv420p':
            raise RuntimeError('FFmpeg no generó H.264/yuv420p')
        # Crear respaldo con hardlink: conserva bytes sin duplicar en el momento de crear.
        try:
            os.link(original, backup)
        except OSError:
            # Si el sistema de archivos no soporta hardlinks, comprobar espacio y copiar.
            if shutil.disk_usage(folder).free < original.stat().st_size + reserve:
                raise RuntimeError('Falta espacio para respaldar el MP4 original')
            shutil.copy2(original, backup)
        os.replace(temporary, original)  # cambio atómico de nombre
        info['size_bytes'] = original.stat().st_size
        temp_meta = folder / (record_id + '.json.reparar.tmp')
        temp_meta.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp_meta, metadata)
        return f'Conversión terminada: {original.name}. Original preservado como {backup.name}'
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--ultimo', action='store_true', help='Convertir el MP4 finalizado más reciente')
    group.add_argument('--id', help='ID de la grabación, sin extensión')
    parser.add_argument('--forzar', action='store_true', help='Reconvertir incluso si el archivo es H.264/yuv420p')
    parser.add_argument('--carpeta', type=Path, help='Carpeta de grabaciones (si difiere de RECORDINGS_DIR)')
    args = parser.parse_args()
    folder = args.carpeta.expanduser().resolve() if args.carpeta else _read_env_folder()
    if not folder.is_dir():
        print(f'No existe la carpeta: {folder}', file=sys.stderr)
        return 2
    if args.ultimo:
        valid = [p for p in folder.glob('rondin_*.mp4') if ID.fullmatch(p.stem)]
        if not valid:
            print('No hay grabaciones MP4 finalizadas en esa carpeta', file=sys.stderr)
            return 2
        record_id = max(valid, key=lambda p: p.stat().st_mtime).stem
    else:
        record_id = args.id
    try:
        print(convert(record_id, folder, force=args.forzar))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
