"""
app/services/duplicados_service.py — Detector y buscador de duplicados.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda
from app.services.memoria_comercio_service import clave_comercio


@dataclass
class Coincidencia:
    transaccion: Transaccion
    dias: int
    misma_descripcion: bool


def buscar_coincidencias(
    db: Session,
    usuario_id: UUID,
    monto: Decimal,
    moneda: str | Moneda,
    fecha: date,
    tipo: str | TipoTransaccion,
    billetera_id: UUID | None = None,
    tarjeta_id: UUID | None = None,
    descripcion: str | None = None,
    excluir_ids: Sequence[UUID] = (),
) -> list[Coincidencia]:
    """
    Busca transacciones existentes que coincidan con un candidato.
    Una transacción coincide si:
    - Es del mismo usuario, tipo, moneda y monto exacto.
    - Está confirmada y no es cuota hija ni padre de cuotas.
    - Tiene la misma billetera o tarjeta que el candidato, si el candidato trae alguna.
    - La distancia en días es de 10 o menos.
    - Y además: la distancia es de 3 días o menos, o ambas tienen la misma clave_comercio.
    Orden: primero las de misma descripción, después por menor diferencia de días.
    """
    tipo_str = tipo.value if hasattr(tipo, "value") else str(tipo).lower()
    tipo_enum = TipoTransaccion.INGRESO if tipo_str == "ingreso" else TipoTransaccion.EGRESO

    moneda_str = moneda.value if hasattr(moneda, "value") else str(moneda).upper()
    moneda_enum = Moneda.USD if moneda_str == "USD" else Moneda.ARS

    monto_dec = Decimal(str(monto))
    fecha_min = fecha - timedelta(days=10)
    fecha_max = fecha + timedelta(days=10)

    stmt = (
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.tipo == tipo_enum,
            Transaccion.moneda == moneda_enum,
            Transaccion.monto == monto_dec,
            Transaccion.es_cuota_hija.is_(False),
            Transaccion.es_padre_cuotas.is_(False),
            or_(
                Transaccion.estado_verificacion.is_(None),
                Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE,
            ),
            Transaccion.fecha >= fecha_min,
            Transaccion.fecha <= fecha_max,
        )
    )

    if billetera_id is not None:
        stmt = stmt.where(Transaccion.billetera_id == billetera_id)
    if tarjeta_id is not None:
        stmt = stmt.where(Transaccion.tarjeta_id == tarjeta_id)
    if excluir_ids:
        stmt = stmt.where(Transaccion.id.notin_(list(excluir_ids)))

    candidatas = db.execute(stmt).scalars().all()

    clave_cand = clave_comercio(descripcion)
    coincidencias: list[Coincidencia] = []

    for tx in candidatas:
        dias = abs((tx.fecha - fecha).days)
        if dias > 10:
            continue

        clave_tx = clave_comercio(tx.descripcion)
        misma_desc = (clave_cand is not None and clave_tx is not None and clave_cand == clave_tx)

        if dias <= 3 or misma_desc:
            coincidencias.append(
                Coincidencia(
                    transaccion=tx,
                    dias=dias,
                    misma_descripcion=misma_desc,
                )
            )

    # Orden: primero las de misma descripción (misma_descripcion=True primero), después por menor días
    return sorted(coincidencias, key=lambda c: (not c.misma_descripcion, c.dias))


def pares_en_billetera(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID,
    desde: date,
    hasta: date,
) -> list[dict]:
    """
    Egresos confirmados de la billetera, no cuota, agrupados por mismo día y mismo monto, con 2 o más.
    """
    stmt = (
        select(
            Transaccion.fecha,
            Transaccion.monto,
            func.count(Transaccion.id).label("cantidad"),
        )
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.billetera_id == billetera_id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            (Transaccion.metodo_pago != MetodoPago.CREDITO) | (Transaccion.metodo_pago.is_(None)),
            Transaccion.es_padre_cuotas.is_(False),
            Transaccion.es_cuota_hija.is_(False),
            (Transaccion.estado_verificacion.is_(None)) | (Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE),
            Transaccion.fecha >= desde,
            Transaccion.fecha <= hasta,
        )
        .group_by(Transaccion.fecha, Transaccion.monto)
        .having(func.count(Transaccion.id) >= 2)
        .order_by(func.count(Transaccion.id).desc(), Transaccion.fecha.desc())
        .limit(3)
    )

    grupos = db.execute(stmt).all()
    duplicados = []
    for g_fecha, g_monto, g_cant in grupos:
        stmt_desc = (
            select(Transaccion.descripcion)
            .where(
                Transaccion.usuario_id == usuario_id,
                Transaccion.billetera_id == billetera_id,
                Transaccion.tipo == TipoTransaccion.EGRESO,
                (Transaccion.metodo_pago != MetodoPago.CREDITO) | (Transaccion.metodo_pago.is_(None)),
                Transaccion.es_padre_cuotas.is_(False),
                Transaccion.es_cuota_hija.is_(False),
                (Transaccion.estado_verificacion.is_(None)) | (Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE),
                Transaccion.fecha == g_fecha,
                Transaccion.monto == g_monto,
            )
            .order_by(Transaccion.fecha_creacion.asc())
            .limit(2)
        )
        descs = [r[0] for r in db.execute(stmt_desc).fetchall()]
        duplicados.append({
            "fecha": g_fecha,
            "monto": g_monto,
            "cantidad": g_cant,
            "descripciones": descs,
        })
    return duplicados
