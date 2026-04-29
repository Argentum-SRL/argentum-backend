from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings


CACHE_TTL_SECONDS = 300


@dataclass
class _Cache:
    data: dict[str, Any] | None = None
    expires_at: datetime | None = None


_cache = _Cache()


def _is_cache_valid() -> bool:
    return _cache.data is not None and _cache.expires_at is not None and _cache.expires_at > datetime.now(timezone.utc)


def _normalizar_nombre(raw: str) -> str:
    value = raw.strip().lower()
    aliases = {
        "oficial": "oficial",
        "blue": "blue",
        "tarjeta": "tarjeta",
        "bolsa": "mep",
        "mep": "mep",
        "bolsa (mep)": "mep",
    }
    return aliases.get(value, value)


def _normalizar_payload(payload: list[dict[str, Any]]) -> dict[str, Any]:
    target = {"oficial", "blue", "tarjeta", "mep"}
    cotizaciones: dict[str, dict[str, Any]] = {}

    for item in payload:
        nombre = _normalizar_nombre(str(item.get("nombre", "")))
        if nombre not in target:
            continue

        compra = item.get("compra")
        venta = item.get("venta")
        promedio = None
        if isinstance(compra, (int, float)) and isinstance(venta, (int, float)):
            promedio = round((float(compra) + float(venta)) / 2, 2)

        cotizaciones[nombre] = {
            "tipo": nombre,
            "nombre": str(item.get("nombre", nombre)).strip() or nombre,
            "compra": float(compra) if isinstance(compra, (int, float)) else None,
            "venta": float(venta) if isinstance(venta, (int, float)) else None,
            "promedio": promedio,
            "moneda": str(item.get("moneda", "ARS")),
            "fecha_actualizacion": item.get("fechaActualizacion") or item.get("fecha_actualizacion"),
        }

    faltantes = [k for k in ("oficial", "blue", "tarjeta", "mep") if k not in cotizaciones]
    if faltantes:
        raise HTTPException(status_code=502, detail=f"Dolar API incompleta. Faltan: {', '.join(faltantes)}")

    return {
        "fuente": "dolarapi.com",
        "actualizado_en": datetime.now(timezone.utc).isoformat(),
        "cotizaciones": cotizaciones,
    }


def get_cotizaciones_dolar() -> dict[str, Any]:
    if _is_cache_valid():
        return _cache.data or {}

    url = f"{settings.DOLAR_API_BASE_URL.rstrip('/')}/v1/dolares"

    try:
        with httpx.Client(timeout=settings.DOLAR_API_TIMEOUT_SECONDS) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="No se pudo consultar Dolar API.")

    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail="Respuesta invalida de Dolar API.")

    normalized = _normalizar_payload(payload)
    _cache.data = normalized
    _cache.expires_at = datetime.now(timezone.utc) + timedelta(seconds=CACHE_TTL_SECONDS)
    return normalized
