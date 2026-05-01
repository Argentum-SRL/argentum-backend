from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update, delete
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.schemas.billetera import BilleteraRead, BilleteraUpdate

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
    results = db.execute(select(Billetera).where(Billetera.usuario_id == current_user.id)).scalars().all()
    return results


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
