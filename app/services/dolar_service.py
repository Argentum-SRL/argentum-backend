from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.cotizacion_dolar import CotizacionDolar
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)

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


def obtener_cotizacion_por_fecha(
    db: Session,
    tipo: str,
    fecha_consulta: date,
) -> CotizacionDolar | None:
    """
    Devuelve la cotización histórica del dólar para un tipo y fecha específicos.

    Orden de resolución:
    1. Busca en la tabla `cotizaciones_dolar` para la fecha exacta y tipo.
    2. Si no existe (ej. fines de semana o feriados), busca la cotización anterior
       más cercana (fecha < fecha_consulta), ordenada descendentemente.
    3. Si no existe ninguna cotización anterior en la base, registra un log de
       advertencia y devuelve None. Nunca inventa un valor.

    Rendimiento: Consulta exclusivamente la base de datos local, jamás realiza
    peticiones HTTP a servicios externos.
    """
    tipo_normalizado = _normalizar_nombre(tipo)

    # 1. Búsqueda exacta
    stmt_exacta = (
        select(CotizacionDolar)
        .where(
            CotizacionDolar.tipo == tipo_normalizado,
            CotizacionDolar.fecha == fecha_consulta,
        )
    )
    cotizacion = db.execute(stmt_exacta).scalar_one_or_none()
    if cotizacion is not None:
        return cotizacion

    # 2. Búsqueda de la cotización anterior más cercana
    stmt_anterior = (
        select(CotizacionDolar)
        .where(
            CotizacionDolar.tipo == tipo_normalizado,
            CotizacionDolar.fecha < fecha_consulta,
        )
        .order_by(desc(CotizacionDolar.fecha))
        .limit(1)
    )
    cotizacion_anterior = db.execute(stmt_anterior).scalar_one_or_none()
    if cotizacion_anterior is not None:
        logger.info(
            "Cotización exacta no encontrada para tipo '%s' en fecha %s. "
            "Usando cotización anterior más cercana del %s.",
            tipo_normalizado,
            fecha_consulta,
            cotizacion_anterior.fecha,
        )
        return cotizacion_anterior

    # 3. No existe ninguna cotización histórica anterior registrada
    logger.warning(
        "No se encontró cotización histórica para tipo '%s' en fecha %s ni anteriores en la tabla.",
        tipo_normalizado,
        fecha_consulta,
    )
    return None


def guardar_cotizaciones_del_dia(db: Session) -> list[CotizacionDolar]:
    """
    Persiste en la tabla `cotizaciones_dolar` las cotizaciones del día actual obtenidas de la API.

    Idempotente: Si ya existe un registro para (fecha, tipo), actualiza sus montos
    (compra, venta, promedio, fecha_registro) en lugar de generar duplicados.
    Utiliza siempre la fecha de Argentina (`hoy_argentina()`) asegurando consistencia horaria.
    """
    fecha_hoy = hoy_argentina()
    payload = get_cotizaciones_dolar()
    cotizaciones_dict = payload.get("cotizaciones", {})

    guardadas: list[CotizacionDolar] = []

    for tipo_key, info in cotizaciones_dict.items():
        tipo_norm = _normalizar_nombre(tipo_key)
        compra_val = info.get("compra")
        venta_val = info.get("venta")
        promedio_val = info.get("promedio")

        compra = Decimal(str(compra_val)) if compra_val is not None else None
        venta = Decimal(str(venta_val)) if venta_val is not None else None
        if promedio_val is not None:
            promedio = Decimal(str(promedio_val))
        elif compra is not None and venta is not None:
            promedio = ((compra + venta) / Decimal("2")).quantize(Decimal("0.0001"))
        else:
            promedio = venta or compra

        if compra is None and venta is None:
            logger.warning("Cotización omitida para '%s': sin valores de compra ni venta.", tipo_norm)
            continue
        if compra is None:
            compra = venta
        if venta is None:
            venta = compra
        if promedio is None:
            promedio = venta

        stmt = select(CotizacionDolar).where(
            CotizacionDolar.fecha == fecha_hoy,
            CotizacionDolar.tipo == tipo_norm,
        )
        existente = db.execute(stmt).scalar_one_or_none()

        if existente is not None:
            existente.compra = compra
            existente.venta = venta
            existente.promedio = promedio
            existente.fecha_registro = datetime.now(timezone.utc)
            guardadas.append(existente)
        else:
            nueva = CotizacionDolar(
                fecha=fecha_hoy,
                tipo=tipo_norm,
                compra=compra,
                venta=venta,
                promedio=promedio,
                fecha_registro=datetime.now(timezone.utc),
            )
            db.add(nueva)
            guardadas.append(nueva)

    db.commit()
    for item in guardadas:
        db.refresh(item)
    return guardadas

