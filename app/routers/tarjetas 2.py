from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.core.database import SessionLocal
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.schemas.tarjeta_credito import (
    TarjetaCreditoCreate, 
    TarjetaCreditoUpdate, 
    TarjetaCreditoResponse,
    ResumenTarjeta
)
from app.services import tarjeta_service

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_model=List[TarjetaCreditoResponse])
def listar_tarjetas(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.obtener_tarjetas(db, current_user.id)

@router.get("/billetera/{billetera_id}", response_model=List[TarjetaCreditoResponse])
def listar_tarjetas_por_billetera(
    billetera_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    tarjetas = tarjeta_service.obtener_tarjetas(db, current_user.id)
    return [t for t in tarjetas if t.billetera_id == billetera_id]

@router.post("/", response_model=TarjetaCreditoResponse, status_code=status.HTTP_201_CREATED)
def crear_tarjeta(
    data: TarjetaCreditoCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.crear_tarjeta(db, current_user.id, data)

@router.put("/{tarjeta_id}", response_model=TarjetaCreditoResponse)
def actualizar_tarjeta(
    tarjeta_id: UUID,
    data: TarjetaCreditoUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.actualizar_tarjeta(db, current_user.id, tarjeta_id, data)

@router.post("/{tarjeta_id}/archivar", response_model=TarjetaCreditoResponse)
def archivar_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.archivar_tarjeta(db, current_user.id, tarjeta_id)

@router.post("/{tarjeta_id}/desarchivar", response_model=TarjetaCreditoResponse)
def desarchivar_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    # Usamos la misma lógica que archivar pero con el estado opuesto
    # (Podríamos agregar desarchivar al service si quisiéramos ser estrictos)
    tarjeta = tarjeta_service.obtener_tarjeta_por_id(db, tarjeta_id, current_user.id)
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    tarjeta.estado = "activa"
    db.commit()
    return tarjeta

@router.delete("/{tarjeta_id}")
def eliminar_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    tarjeta_service.eliminar_tarjeta(db, current_user.id, tarjeta_id)
    return {"detail": "Tarjeta eliminada correctamente"}

@router.get("/{tarjeta_id}/resumen", response_model=ResumenTarjeta)
def obtener_resumen_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    tarjeta = tarjeta_service.obtener_tarjeta_por_id(db, tarjeta_id, current_user.id)
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    return tarjeta_service.calcular_resumen_actual(db, tarjeta)
