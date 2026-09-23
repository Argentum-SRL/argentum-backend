from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.rendimiento_billetera import RendimientoBilletera
from app.schemas.billetera import RendimientoEstimadoResponse

logger = logging.getLogger(__name__)


def calcular_rendimiento_estimado(
    db: Session, usuario_id: UUID, billetera_id: UUID | str
) -> RendimientoEstimadoResponse:
    """
    Calcula el rendimiento estimado devengado al vuelo.
    Fórmula: saldo_actual * (tna / 100) / 365 * días transcurridos desde fecha_ultimo_rendimiento.
    El resultado nunca se persiste en saldo hasta que el usuario lo confirma explícitamente.
    Si la billetera no tiene TNA cargada, devuelve null con tiene_tna=False.
    """
    billetera = db.get(Billetera, billetera_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    # Si no tiene TNA cargada, devolver estado explícito con null (no calcular tna=0 disfrazado)
    if billetera.tna is None:
        return RendimientoEstimadoResponse(
            billetera_id=billetera.id,
            tiene_tna=False,
            tna=None,
            saldo_actual=billetera.saldo_actual,
            dias_transcurridos=None,
            fecha_ultimo_rendimiento=billetera.fecha_ultimo_rendimiento,
            rendimiento_estimado=None,
        )

    # Cálculo de días transcurridos
    if billetera.fecha_ultimo_rendimiento is None:
        dias_transcurridos = 0
    else:
        fecha_ref = billetera.fecha_ultimo_rendimiento
        if fecha_ref.tzinfo is None:
            fecha_ref = fecha_ref.replace(tzinfo=timezone.utc)
        ahora = datetime.now(timezone.utc)
        dias_transcurridos = max(0, (ahora.date() - fecha_ref.date()).days)

    # Fórmula: saldo_actual * (tna / 100) / 365 * días transcurridos
    # Decimal puro con redondeo estándar a 2 decimales ROUND_HALF_UP
    if dias_transcurridos > 0 and billetera.saldo_actual > Decimal("0") and billetera.tna > Decimal("0"):
        dias_dec = Decimal(dias_transcurridos)
        rendimiento = (
            billetera.saldo_actual * (billetera.tna / Decimal("100")) / Decimal("365") * dias_dec
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        rendimiento = Decimal("0.00")

    return RendimientoEstimadoResponse(
        billetera_id=billetera.id,
        tiene_tna=True,
        tna=billetera.tna,
        saldo_actual=billetera.saldo_actual,
        dias_transcurridos=dias_transcurridos,
        fecha_ultimo_rendimiento=billetera.fecha_ultimo_rendimiento,
        rendimiento_estimado=rendimiento,
    )


def confirmar_rendimiento(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID | str,
    monto: Decimal,
    fecha: datetime | None = None,
) -> Billetera:
    """
    Confirma un rendimiento manual:
    - Crea la fila en rendimientos_billetera.
    - Suma el monto a billetera.saldo_actual.
    - Actualiza fecha_ultimo_rendimiento a ahora.
    - NO toca la tabla transacciones.
    """
    billetera = db.get(Billetera, billetera_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    if billetera.estado != EstadoBilletera.ACTIVA:
        raise HTTPException(
            status_code=400,
            detail="No se pueden registrar rendimientos en una billetera archivada."
        )

    if monto <= Decimal("0"):
        raise HTTPException(
            status_code=400,
            detail="El monto del rendimiento debe ser mayor a 0."
        )

    ahora_dt = datetime.now(timezone.utc)
    fecha_operacion = fecha if fecha is not None else ahora_dt

    rendimiento = RendimientoBilletera(
        billetera_id=billetera.id,
        monto=monto,
        fecha=fecha_operacion,
        fecha_creacion=ahora_dt,
    )
    db.add(rendimiento)

    billetera.saldo_actual += monto
    billetera.fecha_ultimo_rendimiento = ahora_dt

    db.commit()
    db.refresh(billetera)
    return billetera
