# -*- coding: utf-8 -*-
"""
Consulta del costo real de OpenAI por día mediante la API oficial de organización/costos.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

URL_COSTOS = "https://api.openai.com/v1/organization/costs"
TIMEOUT_SEGUNDOS = 8
DURACION_CACHE = timedelta(minutes=15)
MAX_PAGINAS = 5

# Cache en memoria del módulo por (desde, hasta): guarda (expira_en_utc, {fecha: costo_usd}).
_cache: dict[tuple[date, date], tuple[datetime, dict[date, Decimal]]] = {}


def limpiar_cache() -> None:
    """Limpia la cache en memoria de costos de OpenAI (para pruebas)."""
    _cache.clear()


def _normalizar_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def costos_por_dia(
    desde: date,
    hasta: date,
    ahora: datetime,
) -> dict[date, Decimal] | None:
    """
    Consulta los costos diarios en USD en OpenAI para el rango [desde, hasta] inclusive.
    Se agrupa por proyecto en vez de filtrar con project_ids porque hay reportes de que
    ese filtro no funciona bien; los días son de UTC.
    """
    admin_key = settings.OPENAI_ADMIN_KEY.strip()
    if not admin_key:
        return None

    ahora_utc = _normalizar_utc(ahora)
    clave_cache = (desde, hasta)
    entrada = _cache.get(clave_cache)
    if entrada is not None:
        expira_en, datos_cacheados = entrada
        if ahora_utc < expira_en:
            return dict(datos_cacheados)

    start_dt = datetime(desde.year, desde.month, desde.day, 0, 0, 0, tzinfo=timezone.utc)
    hasta_fin = hasta + timedelta(days=1)
    end_dt = datetime(hasta_fin.year, hasta_fin.month, hasta_fin.day, 0, 0, 0, tzinfo=timezone.utc)

    base_params: dict[str, Any] = {
        "start_time": int(start_dt.timestamp()),
        "end_time": int(end_dt.timestamp()),
        "bucket_width": "1d",
        "group_by": "project_id",
        "limit": 180,
    }
    headers = {"Authorization": f"Bearer {admin_key}"}
    project_id = settings.OPENAI_PROJECT_ID.strip()

    resultado: dict[date, Decimal] = {}
    next_page: str | None = None

    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as client:
            for _ in range(MAX_PAGINAS):
                params = dict(base_params)
                if next_page:
                    params["page"] = next_page

                resp = client.get(URL_COSTOS, params=params, headers=headers)
                if resp.status_code != 200:
                    logger.warning("No se pudo consultar el costo de OpenAI.")
                    return None

                payload = resp.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    logger.warning("No se pudo consultar el costo de OpenAI.")
                    return None

                for bucket in payload["data"]:
                    if not isinstance(bucket, dict) or "start_time" not in bucket:
                        logger.warning("No se pudo consultar el costo de OpenAI.")
                        return None
                    dia = datetime.fromtimestamp(int(bucket["start_time"]), tz=timezone.utc).date()
                    total_dia = resultado.get(dia, Decimal("0"))
                    results = bucket.get("results") or []
                    if not isinstance(results, list):
                        logger.warning("No se pudo consultar el costo de OpenAI.")
                        return None

                    for item in results:
                        if not isinstance(item, dict):
                            continue
                        if project_id and item.get("project_id") != project_id:
                            continue
                        amount = item.get("amount") or {}
                        if not isinstance(amount, dict):
                            continue
                        currency = str(amount.get("currency", "")).lower()
                        if currency != "usd":
                            logger.warning("OpenAI devolvió un costo en otra moneda.")
                            continue
                        val = amount.get("value")
                        if val is not None:
                            total_dia += Decimal(str(val))

                    resultado[dia] = total_dia

                has_more = bool(payload.get("has_more"))
                next_page_val = payload.get("next_page")
                if not has_more or not next_page_val:
                    break
                next_page = str(next_page_val)
    except Exception:
        logger.warning("No se pudo consultar el costo de OpenAI.")
        return None

    _cache[clave_cache] = (ahora_utc + DURACION_CACHE, dict(resultado))
    return resultado


def estado_openai(resultado: dict[date, Decimal] | None) -> Literal["ok", "sin_conexion", "error"]:
    """Indica el estado de la consulta de costos reales contra OpenAI."""
    if not settings.OPENAI_ADMIN_KEY.strip():
        return "sin_conexion"
    if resultado is None:
        return "error"
    return "ok"
