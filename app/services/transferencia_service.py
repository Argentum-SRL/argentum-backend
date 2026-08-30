import logging
from decimal import Decimal
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.transferencia_interna import TransferenciaInterna
from app.schemas.transferencia_interna import TransferenciaInternaCreate

logger = logging.getLogger(__name__)


def obtener_transferencias(db: Session, usuario_id: UUID):
    return db.execute(
        select(TransferenciaInterna)
        .where(TransferenciaInterna.usuario_id == usuario_id)
        .order_by(desc(TransferenciaInterna.fecha), desc(TransferenciaInterna.fecha_creacion))
    ).scalars().all()


def obtener_transferencia(db: Session, usuario_id: UUID, transferencia_id: UUID) -> TransferenciaInterna:
    tr = db.execute(
        select(TransferenciaInterna).where(
            TransferenciaInterna.id == transferencia_id, 
            TransferenciaInterna.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not tr:
        raise HTTPException(status_code=404, detail="Transferencia no encontrada")
    return tr


def crear_transferencia(db: Session, usuario_id: UUID, data: TransferenciaInternaCreate) -> TransferenciaInterna:
    # 1. Validar billeteras
    b_origen = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_origen_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()
    
    b_destino = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_destino_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not b_origen:
        raise HTTPException(status_code=404, detail="No encontramos la billetera de origen.")
    if not b_destino:
        raise HTTPException(status_code=404, detail="No encontramos la billetera de destino.")

    # Validar que las billeteras estén activas
    if b_origen.estado != EstadoBilletera.ACTIVA:
        raise HTTPException(
            status_code=400,
            detail=f"La billetera de origen '{b_origen.nombre}' está archivada y no puede usarse para transferencias."
        )
    if b_destino.estado != EstadoBilletera.ACTIVA:
        raise HTTPException(
            status_code=400,
            detail=f"La billetera de destino '{b_destino.nombre}' está archivada y no puede usarse para transferencias."
        )

    from app.services.transaccion_service import _validar_moneda_coincide
    _validar_moneda_coincide(data.moneda, b_origen)
    if b_origen.moneda != b_destino.moneda:
        raise HTTPException(
            status_code=400,
            detail="No se permiten transferencias entre billeteras de distinta moneda."
        )
    _validar_moneda_coincide(data.moneda, b_destino)

    # Validar que el saldo sea suficiente
    if b_origen.saldo_actual < data.monto:
        raise HTTPException(
            status_code=400,
            detail=f"Saldo insuficiente en {b_origen.nombre}. Disponible: {b_origen.saldo_actual}, Solicitado: {data.monto}."
        )

    # 2. Crear registro
    nueva_tr = TransferenciaInterna(
        **data.model_dump(exclude={"usuario_id"}),
        usuario_id=usuario_id
    )

    # 3. Impactar saldos
    b_origen.saldo_actual -= data.monto
    b_destino.saldo_actual += data.monto

    db.add(nueva_tr)
    db.commit()
    db.refresh(nueva_tr)
    return nueva_tr


def eliminar_transferencia(db: Session, usuario_id: UUID, transferencia_id: UUID):
    tr = obtener_transferencia(db, usuario_id, transferencia_id)

    # Revertir impactos
    b_origen = db.get(Billetera, tr.billetera_origen_id)
    b_destino = db.get(Billetera, tr.billetera_destino_id)

    if not b_origen:
        raise HTTPException(
            status_code=500,
            detail=f"Error crítico: No se encontró la billetera de origen para revertir la transferencia {tr.id}."
        )
    if not b_destino:
        raise HTTPException(
            status_code=500,
            detail=f"Error crítico: No se encontró la billetera de destino para revertir la transferencia {tr.id}."
        )

    try:
        from app.services.transaccion_service import _validar_moneda_coincide
        _validar_moneda_coincide(tr.moneda, b_origen)
        _validar_moneda_coincide(tr.moneda, b_destino)
    except HTTPException as e:
        logger.critical(f"Error crítico: Inconsistencia de moneda detectada al revertir transferencia {tr.id}: {e.detail}")
        raise

    # Revertir saldos
    b_origen.saldo_actual += tr.monto
    b_destino.saldo_actual -= tr.monto

    db.delete(tr)
    db.commit()
    return {"detail": "Transferencia eliminada exitosamente"}
