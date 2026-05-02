from uuid import UUID
from datetime import date
from fastapi import HTTPException
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.transferencia_interna import TransferenciaInterna
from app.schemas.transferencia_interna import TransferenciaInternaCreate


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
    if data.billetera_origen_id == data.billetera_destino_id:
        raise HTTPException(status_code=400, detail="La billetera de origen y destino no pueden ser la misma")

    # 1. Validar billeteras
    b_origen = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_origen_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()
    
    b_destino = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_destino_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not b_origen or not b_destino:
        raise HTTPException(status_code=404, detail="Una o ambas billeteras no existen")

    # 2. Crear registro
    nueva_tr = TransferenciaInterna(
        **data.model_dump(exclude={"usuario_id"}),
        usuario_id=usuario_id
    )

    # 3. Impactar saldos
    # Se permite distinta moneda. Se impacta el "monto" (en la moneda de la transferencia) 
    # en ambas billeteras. El usuario es responsable del tipo de cambio si las billeteras difieren.
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

    if b_origen:
        b_origen.saldo_actual += tr.monto
    if b_destino:
        b_destino.saldo_actual -= tr.monto

    db.delete(tr)
    db.commit()
    return {"detail": "Transferencia eliminada exitosamente"}
