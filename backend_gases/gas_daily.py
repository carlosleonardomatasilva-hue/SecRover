"""Consulta diaria de gases por minuto, compatible con MongoDB 7.

No cambia los datos originales: agrega valores estimados para visualizar hasta 24 horas
sin enviar decenas de miles de puntos al navegador.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Callable

from fastapi import APIRouter, HTTPException, Query
from pymongo.errors import PyMongoError

LOG = logging.getLogger('secrover.gases.diario')
CAMPOS = (
    'co_equiv_ppm', 'h2_equiv_ppm', 'nh3_equiv_ppm',
    'etanol_equiv_ppm', 'ch4_equiv_ppm', 'h2s_ppm_aprox'
)


def create_daily_router(get_collection: Callable) -> APIRouter:
    """Recibe la misma función `coleccion()` que usa api_gases.py."""
    router = APIRouter()

    @router.get('/api/gases/day')
    async def gases_dia(
        start: datetime = Query(..., description='Inicio de día, ISO 8601 con zona horaria'),
        end: datetime = Query(..., description='Final exclusivo, ISO 8601 con zona horaria'),
    ):
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(status_code=422, detail='Incluye zona horaria en start y end')
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        span = end_utc - start_utc
        if not timedelta(0) < span <= timedelta(hours=26):
            raise HTTPException(status_code=422, detail='La consulta debe abarcar como máximo un día')

        # fecha_recepcion debe estar almacenada como BSON Date, no texto.
        pipeline = [
            {'$match': {'fecha_recepcion': {'$gte': start_utc, '$lt': end_utc}}},
            {'$group': {
                '_id': {'$dateTrunc': {'date': '$fecha_recepcion', 'unit': 'minute'}},
                'muestras': {'$sum': 1},
                **{key: {'$avg': f'${key}'} for key in CAMPOS},
            }},
            {'$sort': {'_id': 1}},
        ]

        try:
            # PyMongo es síncrono; no bloquear el bucle de FastAPI.
            datos = await asyncio.to_thread(
                lambda: list(get_collection().aggregate(pipeline, maxTimeMS=15000))
            )
        except PyMongoError as exc:
            LOG.exception('No se pudo consultar el historial diario en MongoDB')
            raise HTTPException(status_code=503, detail='No se pudo consultar el día en MongoDB') from exc

        historial = []
        for doc in datos:
            hora = doc.get('_id')
            if not isinstance(hora, datetime):
                continue
            if hora.tzinfo is None:
                hora = hora.replace(tzinfo=timezone.utc)
            item = {
                '_id': f'min-{hora.isoformat()}',
                'fecha_recepcion': hora.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'muestras': int(doc.get('muestras') or 0),
            }
            for key in CAMPOS:
                try:
                    value = doc.get(key)
                    value = float(value) if value is not None else None
                    item[key] = value if value is not None and math.isfinite(value) and value >= 0 else None
                except (TypeError, ValueError, OverflowError):
                    item[key] = None
            historial.append(item)

        return {
            'history': historial,
            'total_readings': sum(r['muestras'] for r in historial),
            'minute_buckets': len(historial),
            'aggregation': 'average_per_minute',
            'start_utc': start_utc.isoformat(),
            'end_utc': end_utc.isoformat(),
        }

    return router
