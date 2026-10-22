"""
app/services/fecha_saldo_service.py — Previsualización y aplicación del cambio de fecha de saldo.

Permite retroceder la fecha del punto de partida de una billetera, sumando los movimientos
comprendidos en el intervalo [fecha_nueva, fecha_actual) tanto al saldo_actual como al saldo_inicial,
manteniendo invariable la diferencia con el saldo teórico.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.transaccion import Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.evento_service import emitir_evento_actualizacion
from app.services.saldo_billetera_service import (
    fecha_creacion_billetera,
    mover_fecha_saldo,
)
from app.services.transaccion_service import _afecta_saldo
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


def _validar_y_obtener_billetera(
    db: Session,
    usuario_id: UUID | str,
    billetera_id: UUID | str,
    fecha_nueva: date,
    for_update: bool = False,
) -> tuple[Billetera, date]:
    """
    Valida las condiciones de negocio para el cambio de fecha de saldo (422 en orden estricto)
    y retorna la billetera y su fecha efectiva actual.
    """
    u_id = UUID(str(usuario_id)) if not isinstance(usuario_id, UUID) else usuario_id
    b_id = UUID(str(billetera_id)) if not isinstance(billetera_id, UUID) else billetera_id

    stmt = select(Billetera).where(Billetera.id == b_id, Billetera.usuario_id == u_id)
    if for_update:
        stmt = stmt.with_for_update()

    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No encontramos esa billetera.",
        )

    # 1. Billetera sin punto de partida
    if not getattr(billetera, "tiene_punto_partida", True):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "detail": "Esta billetera no tiene saldo inicial.",
                "error": {
                    "code": "UNPROCESSABLE",
                    "message": "Esta billetera no tiene saldo inicial.",
                },
            },
        )

    # 1b. Control de saldo registrado (decisión 4)
    if billetera.fecha_ultimo_control is not None:
        fecha_ctrl_str = billetera.fecha_ultimo_control.strftime("%d/%m/%Y")
        msg = f"Este saldo ya lo actualizaste el {fecha_ctrl_str}: los movimientos anteriores no lo cambian."
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "detail": msg,
                "error": {
                    "code": "UNPROCESSABLE",
                    "message": msg,
                },
            },
        )

    hoy = hoy_argentina()

    # 2. Fecha futura
    if fecha_nueva > hoy:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "detail": "La fecha del saldo no puede ser futura.",
                "error": {
                    "code": "UNPROCESSABLE",
                    "message": "La fecha del saldo no puede ser futura.",
                },
            },
        )

    # 3. Fecha de hace más de 730 días (más de 2 años)
    if (hoy - fecha_nueva).days > 730:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "detail": "La fecha del saldo no puede ser de hace más de 2 años.",
                "error": {
                    "code": "UNPROCESSABLE",
                    "message": "La fecha del saldo no puede ser de hace más de 2 años.",
                },
            },
        )

    # 4. Fecha igual o posterior a la efectiva actual
    fecha_actual = fecha_creacion_billetera(billetera)
    if fecha_nueva >= fecha_actual:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "detail": "Solo se puede mover la fecha hacia atrás.",
                "error": {
                    "code": "UNPROCESSABLE",
                    "message": "Solo se puede mover la fecha hacia atrás.",
                },
            },
        )

    return billetera, fecha_actual


def _calcular_delta_movimientos(
    db: Session,
    billetera_id: UUID,
    fecha_nueva: date,
    fecha_actual: date,
) -> tuple[Decimal, int]:
    """
    Calcula el impacto monetario neto (delta) y la cantidad de movimientos
    en el intervalo [fecha_nueva, fecha_actual).

    Se suman SOLO:
    - Transacciones de esa billetera que cumplen _afecta_saldo (+monto si ingreso, -monto si egreso).
    - Transferencias internas (origen: -monto_origen, destino: +monto_destino).
    NO se suman rendimientos ni ajustes.
    """
    d_prime = fecha_nueva
    d = fecha_actual

    # 1. Transacciones
    stmt_tx = select(Transaccion).where(
        Transaccion.billetera_id == billetera_id,
        Transaccion.fecha >= d_prime,
        Transaccion.fecha < d,
    )
    txs = db.execute(stmt_tx).scalars().all()

    delta = Decimal("0.00")
    cantidad = 0

    for tx in txs:
        if _afecta_saldo(tx):
            cantidad += 1
            tipo_val = tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo)
            monto_dec = Decimal(str(tx.monto))
            if tipo_val.lower() == "ingreso":
                delta += monto_dec
            else:
                delta -= monto_dec

    # 2. Transferencias internas
    stmt_tr = select(TransferenciaInterna).where(
        or_(
            TransferenciaInterna.billetera_origen_id == billetera_id,
            TransferenciaInterna.billetera_destino_id == billetera_id,
        ),
        TransferenciaInterna.fecha >= d_prime,
        TransferenciaInterna.fecha < d,
    )
    trs = db.execute(stmt_tr).scalars().all()

    for tr in trs:
        contada = False
        if tr.billetera_origen_id == billetera_id:
            delta -= Decimal(str(tr.monto_origen))
            contada = True
        if tr.billetera_destino_id == billetera_id:
            delta += Decimal(str(tr.monto_destino))
            contada = True
        if contada:
            cantidad += 1

    # 3. Rendimientos (con fecha de Argentina en [fecha_nueva, fecha_actual))
    stmt_rend = select(RendimientoBilletera).where(
        RendimientoBilletera.billetera_id == billetera_id,
    )
    rends = db.execute(stmt_rend).scalars().all()

    for r in rends:
        f_dt = r.fecha
        if isinstance(f_dt, datetime):
            if f_dt.tzinfo is None:
                f_dt = f_dt.replace(tzinfo=timezone.utc)
            f_arg = f_dt.astimezone(TZ_ARGENTINA).date()
        else:
            f_arg = f_dt
        if d_prime <= f_arg < d:
            cantidad += 1
            delta += Decimal(str(r.monto))

    return delta, cantidad


def previsualizar_cambio_fecha(
    db: Session,
    usuario_id: UUID | str,
    billetera_id: UUID | str,
    fecha_nueva: date,
) -> dict[str, Any]:
    """
    Calcula el impacto de cambiar la fecha del saldo sin escribir nada en la base de datos.
    Retorna {saldo_actual, saldo_nuevo, cantidad_movimientos, fecha_actual, fecha_nueva}.
    """
    billetera, fecha_actual = _validar_y_obtener_billetera(
        db, usuario_id, billetera_id, fecha_nueva, for_update=False
    )

    delta, cantidad = _calcular_delta_movimientos(
        db, billetera.id, fecha_nueva, fecha_actual
    )

    saldo_actual = billetera.saldo_actual or Decimal("0.00")
    saldo_nuevo = saldo_actual + delta

    return {
        "saldo_actual": saldo_actual,
        "saldo_nuevo": saldo_nuevo,
        "cantidad_movimientos": cantidad,
        "fecha_actual": fecha_actual,
        "fecha_nueva": fecha_nueva,
    }


def cambiar_fecha_saldo(
    db: Session,
    usuario_id: UUID | str,
    billetera_id: UUID | str,
    fecha_nueva: date,
) -> Billetera:
    """
    Aplica el cambio de fecha de saldo hacia atrás sobre la billetera.
    Usa with_for_update en una transacción.
    Verifica que la diferencia entre saldo_actual y calcular_saldo_teorico sea la misma
    antes y después; si no coincide, hace rollback y lanza 409.
    Emite evento de actualización 'billeteras' al finalizar.
    """
    billetera, fecha_actual = _validar_y_obtener_billetera(
        db, usuario_id, billetera_id, fecha_nueva, for_update=True
    )

    hoy = hoy_argentina()

    # Cálculo previo a la modificación
    saldo_teorico_antes = calcular_saldo_teorico(db, billetera.id, hasta=hoy)
    saldo_act_antes = billetera.saldo_actual or Decimal("0.00")
    diferencia_antes = saldo_act_antes - saldo_teorico_antes

    delta, _ = _calcular_delta_movimientos(
        db, billetera.id, fecha_nueva, fecha_actual
    )

    # Aplicar cambios vía la única puerta autorizada
    mover_fecha_saldo(billetera, fecha_nueva, delta)
    db.flush()

    # Verificación de invariante
    saldo_teorico_despues = calcular_saldo_teorico(db, billetera.id, hasta=hoy)
    diferencia_despues = billetera.saldo_actual - saldo_teorico_despues

    if diferencia_antes != diferencia_despues:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "No se pudo cambiar la fecha: el saldo no cierra.",
                "error": {
                    "code": "CONFLICT",
                    "message": "No se pudo cambiar la fecha: el saldo no cierra.",
                },
            },
        )

    u_id = UUID(str(usuario_id)) if not isinstance(usuario_id, UUID) else usuario_id
    emitir_evento_actualizacion(db, u_id, "billeteras")
    db.commit()
    db.refresh(billetera)

    return billetera
