from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.schemas.tarjeta_credito import (
    TarjetaCreditoCreate, 
    TarjetaCreditoUpdate, 
    TarjetaCreditoResponse
)
from app.services import tarjeta_service

router = APIRouter()

@router.get("/", response_model=list[TarjetaCreditoResponse])
def listar_tarjetas(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.obtener_tarjetas(db, current_user.id)

@router.get("/billetera/{billetera_id}", response_model=list[TarjetaCreditoResponse])
def listar_tarjetas_por_billetera(
    billetera_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.obtener_tarjetas_por_billetera(db, current_user.id, billetera_id)

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
    return tarjeta_service.desarchivar_tarjeta(db, current_user.id, tarjeta_id)

@router.delete("/{tarjeta_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    tarjeta_service.eliminar_tarjeta(db, current_user.id, tarjeta_id)
    return None
