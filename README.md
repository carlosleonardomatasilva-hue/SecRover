# SecRover · Centro de Control React — Fase 5 (reconstrucción)

Este paquete reconstruye el **frontend React** de SecRover a partir de la descripción y el protocolo confirmados de la entrega del 13 de noviembre de 2026. **No es el ZIP binariamente idéntico** al proyecto original de Fase 5, cuyo código completo no estaba disponible para recuperar. Se ha creado el frontend completo y editable, sin el backend Python original.

## Contenido

- `frontend/src/App.jsx`: dashboard, navegación, sección de sensores, bitácora, rondines demo, grabaciones pendientes y configuración.
- `frontend/src/components/`: Sidebar, TopBar, RoverControl, CameraPanel, QuadMDPanel, GasPanel, Sparkline.
- `frontend/src/hooks/useSecRover.js`: WebSockets, parada de emergencia, PTZ, telemetría y consulta de gases.
- `frontend/src/services/secroverApi.js`: llamadas REST y token temporal.
- `frontend/src/utils/readings.js`: normalización de valores y exportación CSV.
- `frontend/src/styles/main.css`: interfaz industrial/glassmorphism y diseño adaptable.

## Iniciar sin backend (vista de demostración)

Necesitas **Node.js 20+** y npm:

```bash
cd frontend
npm install
npm run dev
```

Desde el equipo donde se ejecutó Vite, abre `http://127.0.0.1:5173`. Elige **«Ver demo»** para visualizar estimaciones simuladas de ppm y batería. Todo el contenido de ejemplo está rotulado como demostración. El botón de control de motores permanece inhabilitado en demo.

## Integrar el backend original FastAPI

1. Ejecuta **tu backend FastAPI original**, validado por separado, en `127.0.0.1:8000`.
2. Ejecuta `npm run dev` desde la carpeta `frontend`. Vite reenviará `/api` y `/ws` al servidor FastAPI.
3. En **Configuración**, introduce `SECROVER_WEB_TOKEN` si está configurado en FastAPI.
4. Desactiva demo. La telemetría solo se mostrará si llega del backend; un sensor desconectado no arroja valores inventados.
5. Para compilar: `npm run build`. El resultado está en `frontend/dist/`. El backend anterior podía servir esa carpeta, si conserva esa configuración.

**Rutas esperadas del backend (no incluidas en este ZIP):**

- `WS /ws/rover`: `auth`, `ready`, `telemetry`, `move` (`action`, `percent`), `heartbeat`, `stop`, `emergency`, `reset`.
- `WS /ws/ptz`: `auth`, `ready`, `start`, `heartbeat`, `stop`, `home`, `video_ticket`.
- `GET /api/status`.
- `GET /api/video.mjpeg?ticket=...`.
- `GET /api/gases/latest` y `GET /api/gases/history?limit=60`. Estas rutas **dependen del proxy Python** hacia el servicio de sensores que utiliza MongoDB; React no se conecta directamente a MongoDB.

Si tu backend o documentos MongoDB nombran los campos de otra manera, ajusta `src/utils/readings.js` y la API según corresponda. El backend original de Fase 5 gestionaba los puertos USB, cámara, tokens, modos físicos y seguridad; este ZIP solo recrea la interfaz.

## Importante: seguridad

- **Movimiento físico bloqueado en este frontend reconstruido:** solo se emiten comandos `move` cuando el backend reporta `mode: "SIMULACION"` y existe enlace autenticado. No se está dando por validada la operación real.
- La solicitud STOP/E-STOP desde la web **no sustituye un paro de potencia físico** ni un watchdog certificado.
- Si el enlace se pierde, el navegador deja de enviar heartbeats. El backend debe implementar su propio timeout de seguridad.
- **Nunca publiques el servidor de movimiento físico en Internet** ni sustituyas tu `.env` original por valores de ejemplo.
- La bitácora de eventos y los rondines demo son de la sesión del navegador; **no** están guardados en MongoDB.
- La página **no graba video**. La biblioteca de grabaciones está marcada como integración pendiente.
- El panel de gases muestra **únicamente ppm equivalentes/estimadas** de los 6 compuestos; ADC y voltajes quedan ocultos. Consulta `README_MONGODB_PPM.md` para el servicio de MongoDB.

## Diferencias respecto al paquete original

Esta reconstrucción prioriza un frontend ejecutable y la compatibilidad con el protocolo documentado. No incluye `servidor.py`, `rover_bridge.py`, `quadmd_telemetry.py`, pruebas Python, ni el código de adquisición serial / MongoDB de la entrega original.

## Integración MongoDB - ppm

Se agregó `backend_gases/` con una API FastAPI de solo lectura y se configuró el proxy de Vite para enviar `/api/gases/*` al puerto 8001. Ver `README_MONGODB_PPM.md`.
