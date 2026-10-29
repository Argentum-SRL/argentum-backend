# -*- coding: utf-8 -*-
"""
pregunta_saldo_service.py - Servicio para selección y postergación de la pregunta de saldo.

Elige la billetera activa más relevante que lleve 30 días o más sin control de saldo
para solicitar una verificación rápida de un toque al usuario.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import MetodoPago, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.services.reporte_uso_service import dia_ar

DIAS_SIN_CONTROL = 30
DIAS_SALTEAR = 30
VENTANA_USO_DIAS = 90


def elegir_billetera_pregunta(db: Session, usuario_id: UUID, hoy: date) -> Billetera | None:
    """
    Selecciona la billetera para la pregunta de saldo:
    - Billeteras del usuario, estado ACTIVA, es_inversion False.
    - pregunta_saldo_pospuesta_hasta es None o <= hoy.
    - referencia = fecha_ultimo_control, si no fecha_saldo, si no dia_ar(fecha_creacion).
    - (hoy − referencia).days >= 30.
    - Uso = cantidad de sus transacciones en [hoy - 90, hoy] con metodo_pago != CREDITO o nulo,
      más transferencias internas (origen o destino) en esa ventana.
    - Solo candidatas con uso >= 1.
    - Gana la de mayor uso; en caso de empate, fecha_creacion más vieja y después nombre.
    """
    stmt = select(Billetera).where(
        Billetera.usuario_id == usuario_id,
        Billetera.estado == EstadoBilletera.ACTIVA,
        Billetera.es_inversion == False,
        or_(
            Billetera.pregunta_saldo_pospuesta_hasta.is_(None),
            Billetera.pregunta_saldo_pospuesta_hasta <= hoy,
        ),
    )
    billeteras = db.execute(stmt).scalars().all()

    candidatas: list[tuple[int, Billetera]] = []
    desde_uso = hoy - timedelta(days=VENTANA_USO_DIAS)

    for b in billeteras:
        ref = b.fecha_ultimo_control
        if ref is None:
            ref = b.fecha_saldo
        if ref is None:
            ref = dia_ar(b.fecha_creacion)

        if ref is None or (hoy - ref).days < DIAS_SIN_CONTROL:
            continue

        # Contar transacciones no crédito en ventana [hoy - 90, hoy]
        stmt_tx = select(func.count(Transaccion.id)).where(
            Transaccion.billetera_id == b.id,
            Transaccion.fecha >= desde_uso,
            Transaccion.fecha <= hoy,
            or_(
                Transaccion.metodo_pago != MetodoPago.CREDITO,
                Transaccion.metodo_pago.is_(None),
            ),
        )
        cant_tx = db.execute(stmt_tx).scalar() or 0

        # Contar transferencias internas en ventana [hoy - 90, hoy]
        stmt_tr = select(func.count(TransferenciaInterna.id)).where(
            TransferenciaInterna.fecha >= desde_uso,
            TransferenciaInterna.fecha <= hoy,
            or_(
                TransferenciaInterna.billetera_origen_id == b.id,
                TransferenciaInterna.billetera_destino_id == b.id,
            ),
        )
        cant_tr = db.execute(stmt_tr).scalar() or 0

        total_uso = cant_tx + cant_tr
        if total_uso >= 1:
            candidatas.append((total_uso, b))

    if not candidatas:
        return None

    # Ordenar por:
    # 1. Uso descendente (-uso)
    # 2. fecha_creacion ascendente (más vieja primero)
    # 3. nombre ascendente
    candidatas.sort(key=lambda item: (-item[0], item[1].fecha_creacion, item[1].nombre))
    return candidatas[0][1]


def saltear_pregunta(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID | str,
    hoy: date,
    commit: bool = True,
) -> None:
    """
    Pospone la pregunta de saldo para una billetera por 30 días.
    Si la billetera no pertenece al usuario, lanza HTTPException 404.
    """
    b_id = UUID(str(billetera_id)) if isinstance(billetera_id, str) else billetera_id
    billetera = db.get(Billetera, b_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    billetera.pregunta_saldo_pospuesta_hasta = hoy + timedelta(days=DIAS_SALTEAR)

    if commit:
        db.commit()
    else:
        db.flush()

