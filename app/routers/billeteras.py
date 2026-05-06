from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update, delete, exists
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.schemas.billetera import BilleteraRead, BilleteraUpdate
from app.services import usuario_service

router = APIRouter(prefix="/billeteras", tags=["billeteras"])


class CrearBilleteraRequest(BaseModel):
    nombre: str
    moneda: Moneda
    saldo_inicial: float = 0.0
    es_principal: bool = False
    es_efectivo: bool = False


@router.get("", response_model=List[BilleteraRead])
def list_billeteras(
    db: Session = Depends(get_db), current_user: Usuario = Depends(get_current_user)
):
    """Devuelve las billeteras del usuario autenticado."""
    # Failsafe: asegurar que tenga las billeteras de efectivo default
    usuario_service.crear_billeteras_efectivo_default(db, current_user.id)
    
    # Optimización N+1: Usar subqueries correlacionadas para verificar transacciones y transferencias
    exists_tx = exists().where(Transaccion.billetera_id == Billetera.id)
    exists_tr = exists().where(
        (TransferenciaInterna.billetera_origen_id == Billetera.id) | 
        (TransferenciaInterna.billetera_destino_id == Billetera.id)
    )
    
    stmt = select(Billetera, (exists_tx | exists_tr).label("has_tx")).where(Billetera.usuario_id == current_user.id)
    rows = db.execute(stmt).all()
    
    results = []
    for b, has_tx in rows:
        b_read = BilleteraRead.model_validate(b)
        b_read.tiene_transacciones = has_tx
        results.append(b_read)

    return results


@router.get("/{billetera_id}", response_model=BilleteraRead)
def get_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Obtiene una billetera específica por ID."""
    from sqlalchemy import or_
    
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")
    
    # Verificamos transacciones y transferencias por separado para mayor seguridad
    has_tx = db.query(exists().where(Transaccion.billetera_id == billetera_id)).scalar()
    has_tr = db.query(exists().where(or_(
        TransferenciaInterna.billetera_origen_id == billetera_id,
        TransferenciaInterna.billetera_destino_id == billetera_id
    ))).scalar()
    
    b_read = BilleteraRead.model_validate(billetera)
    b_read.tiene_transacciones = bool(has_tx or has_tr)
    return b_read


@router.post("", response_model=BilleteraRead, status_code=status.HTTP_201_CREATED)
def create_billetera(
    body: CrearBilleteraRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    if body.es_principal:
        db.execute(
            update(Billetera).where(Billetera.usuario_id == current_user.id).values(es_principal=False)
        )

    b = Billetera(
        usuario_id=current_user.id,
        nombre=body.nombre,
        moneda=body.moneda,
        saldo_inicial=body.saldo_inicial,
        saldo_actual=body.saldo_inicial,
        es_principal=body.es_principal,
        es_efectivo=body.es_efectivo,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@router.put("/{billetera_id}", response_model=BilleteraRead)
def update_billetera(
    billetera_id: str,
    body: BilleteraUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")

    if body.es_principal:
        db.execute(
            update(Billetera).where(Billetera.usuario_id == current_user.id).values(es_principal=False)
        )

    if billetera.es_efectivo:
        # Solo se permite cambiar el nombre y el estado
        if body.nombre is not None:
            billetera.nombre = body.nombre
        if body.estado is not None:
            billetera.estado = body.estado
    else:
        for attr in ('nombre', 'moneda', 'saldo_actual', 'saldo_inicial', 'es_principal', 'es_efectivo', 'estado'):
            val = getattr(body, attr, None)
            if val is not None:
                setattr(billetera, attr, val)

    db.commit()
    db.refresh(billetera)
    return billetera


@router.delete("/{billetera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")

    if billetera.es_efectivo:
        raise HTTPException(status_code=400, detail="Las billeteras de efectivo (ARS/USD) no pueden eliminarse")

    # VERIFICACIÓN DE INTEGRIDAD (Optimizada: 1 query para ambos chequeos)
    exists_tx = exists().where(Transaccion.billetera_id == billetera_id)
    exists_tr = exists().where(
        (TransferenciaInterna.billetera_origen_id == billetera_id) | 
        (TransferenciaInterna.billetera_destino_id == billetera_id)
    )
    
    check_stmt = select(exists_tx.label("has_tx"), exists_tr.label("has_tr"))
    check_res = db.execute(check_stmt).one()
    
    if check_res.has_tx:
        raise HTTPException(
            status_code=400, 
            detail="No se puede eliminar la billetera porque tiene transacciones asociadas. Por favor, archivala para mantener el historial."
        )

    if check_res.has_tr:
        raise HTTPException(
            status_code=400, 
            detail="No se puede eliminar la billetera porque tiene transferencias internas asociadas. Por favor, archivala."
        )

    res = db.execute(delete(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id))
    db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")
    return


@router.post("/{billetera_id}/archivar", response_model=BilleteraRead)
def archivar_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")
    billetera.estado = EstadoBilletera.ARCHIVADA
    db.commit()
    db.refresh(billetera)
    return billetera


@router.post("/{billetera_id}/desarchivar", response_model=BilleteraRead)
def desarchivar_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")
    billetera.estado = EstadoBilletera.ACTIVA
    db.commit()
    db.refresh(billetera)
    return billetera
