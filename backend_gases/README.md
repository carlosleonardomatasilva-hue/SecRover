# SecRover integrado compatible con React existente

Este proyecto conserva todas las rutas del backend adjunto. La diferencia es el arranque de **una sola aplicación FastAPI** en **dos puertos simultáneos** para no tocar tu React ni `vite.config.js`:

- `127.0.0.1:8000` → cámara IMOU, ONVIF, MJPEG, `GET /api/status`, WebSockets `/ws/ptz`, `/ws/rover` (rover deshabilitado).
- `127.0.0.1:8001` → rutas `/api/gases/latest`, `/api/gases/history`, `/api/gases/health` (también responde a las demás rutas, por ser la misma aplicación).
- MongoDB Docker publica `127.0.0.1:27018` en la Orange Pi. **No cambies su contenedor**.

## Instalar y ejecutar

```bash
cd ~/Downloads/SecRover_Backend_Compatible_React
sudo apt install -y python3-venv ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.ejemplo .env
nano .env
python api_gases.py
```

**Importante:** Configura en `.env` la contraseña **real** de IMOU y los nombres **reales** de base/colección (los valores de ejemplo podrían no coincidir).

No uses `uvicorn api_gases:app` si quieres abrir ambos puertos con este archivo; ejecuta `python api_gases.py`, porque es el código de arranque el que abre los dos sockets. Mantén la terminal abierta. No ejecutes otro servidor en 8000/8001 ni el puente `socat` anterior.

## Comprobar

```bash
sudo ss -ltnp | grep -E ':(8000|8001)\b'
curl -i http://127.0.0.1:8000/api/status
curl -i http://127.0.0.1:8001/api/gases/health
curl -i http://127.0.0.1:8001/api/gases/latest
curl -i http://127.0.0.1:5173/api/gases/latest
```

Si sale `{"latest":null}`, la API llega a MongoDB pero la colección configurada está vacía o equivocada. Si algún puerto está ocupado, identifica al proceso con `sudo ss -ltnp | grep -E ':(8000|8001)\b'` y detén el servicio anterior sin borrar datos. Si aparece WebSocket `ECONNRESET`, mira el registro de la terminal Python y que `uvicorn[standard]` esté instalado.

## Redes

- La cámara IMOU debe seguir accesible desde `end0` en la subred local `192.168.137.0/24`, sin ruta a Internet desde la cámara.
- No se configuran ni se habilitan motores reales del rover.
- La URL RTSP contiene credenciales en el proceso FFmpeg; no compartas capturas de procesos ni tu `.env`.
- El `SECROVER_WEB_TOKEN` protege el acceso remoto si expresamente habilitas un host diferente de `127.0.0.1`.
