# SecRover React — Panel exclusivo de ppm

## ¿Qué se verá en Sensores?

- CO equivalente (ppm): `co_equiv_ppm`
- H₂ equivalente (ppm): `h2_equiv_ppm`
- NH₃ equivalente (ppm): `nh3_equiv_ppm`
- Etanol equivalente (ppm): `etanol_equiv_ppm`
- CH₄ equivalente (ppm): `ch4_equiv_ppm`
- H₂S aproximado (ppm): `h2s_ppm_aprox`

**ADC, voltaje y calibración no aparecen en el panel, en las gráficas, ni en el CSV.** Se conservan en MongoDB. Se muestra la fecha/hora como metadato para distinguir lecturas antiguas de recientes. Un `0` válido aparece como `0`, no como «sin datos».

## 1. Configurar MongoDB en Orange Pi 4 Pro

Localiza los nombres que ya usa tu script `arduino_mongodb.py`. Crea el archivo `backend_gases/.env` a partir de `.env.ejemplo`, y cambia `MONGO_DB` y `MONGO_COLLECTION` según corresponda. La API **solo consulta**, no inserta ni modifica documentos. MongoDB debe estar ejecutándose.

```bash
cd backend_gases
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.ejemplo .env
nano .env
python -m uvicorn api_gases:app --host 127.0.0.1 --port 8001
```

Prueba: `curl http://127.0.0.1:8001/api/gases/latest`

Con tu documento, el servicio devolverá H₂S estimado = **33.11 ppm** y los otros cinco valores = **0 ppm**, tal como están almacenados; sin inventar calibraciones.

## 2. Ejecutar React

En una segunda terminal (desde la carpeta `frontend`):

```bash
npm install
npm run dev
```

Visita `http://127.0.0.1:5173`. Desactiva **Ver demo** para leer datos reales. El frontend consulta `/api/gases/latest` y `/api/gases/history?limit=60` cada 3 segundos. El proxy Vite dirige esas rutas al servicio local del puerto 8001. Las demás rutas `/api` y `/ws` siguen dirigidas al backend original de SecRover en el puerto 8000.

El panel de gases **puede funcionar aunque el servidor principal de SecRover (8000) esté apagado**, pero verás rover/cámara desconectados.

## 3. Si compilas para servir desde el backend 8000

`npm run build` ya no usa el proxy de Vite. Tu `servidor.py` original debe implementar o conservar un proxy de solo lectura desde `/api/gases/latest` y `/api/gases/history` hacia `http://127.0.0.1:8001/api/gases/...`. No se incluye ni se modifica el backend original de motores/cámara. También puedes implementar el mismo router de solo lectura dentro de dicho FastAPI, conservando el esquema JSON.

## Seguridad y fiabilidad

Los valores llamados `*_equiv_ppm` son equivalentes estimados; `h2s_ppm_aprox` es un valor aproximado, no una medición certificada. No es apto para alarmas de seguridad ni para declarar que un ambiente es seguro. Si la lectura es antigua, la cifra permanece visible pero está rotulada como **ÚLTIMA LECTURA**, no como tiempo real. Nunca abras MongoDB ni el servicio de gases a Internet sin controles apropiados.
