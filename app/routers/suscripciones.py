from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.models.suscripcion import EstadoSuscripcion
from app.schemas.suscripcion import (
    SuscripcionCreate, 
    SuscripcionUpdate, 
    SuscripcionResponse, 
    ActualizarPrecioRequest,
    HistorialSuscripcionResponse
)
from app.services import suscripcion_service

router = APIRouter(prefix="/suscripciones", tags=["suscripciones"])

@router.get("", response_model=List[SuscripcionResponse])
def get_suscripciones(
    estado: Optional[str] = Query(None, regex="^(activa|pausada|cancelada)$"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return suscripcion_service.obtener_suscripciones(db, current_user.id, estado)

@router.get("/total-mensual")
def get_total_mensual(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return suscripcion_service.obtener_total_mensual(db, current_user.id)

@router.get("/{id}", response_model=SuscripcionResponse)
def get_suscripcion(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return suscripcion_service.obtener_suscripcion_detalle(db, current_user.id, id)

@router.post("", response_model=SuscripcionResponse, status_code=status.HTTP_201_CREATED)
def create_suscripcion(
    data: SuscripcionCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    s = suscripcion_service.crear_suscripcion(db, current_user.id, data)
    return suscripcion_service.obtener_suscripcion_detalle(db, current_user.id, s.id)

@router.put("/{id}", response_model=SuscripcionResponse)
def update_suscripcion(
    id: UUID,
    data: SuscripcionUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    suscripcion_service.actualizar_suscripcion(db, current_user.id, id, data)
    return suscripcion_service.obtener_suscripcion_detalle(db, current_user.id, id)

@router.post("/{id}/precio", response_model=HistorialSuscripcionResponse)
def add_precio(
    id: UUID,
    data: ActualizarPrecioRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return suscripcion_service.actualizar_precio(db, current_user.id, id, data)

@router.post("/{id}/pausar", response_model=SuscripcionResponse)
def pausar_suscripcion(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    suscripcion_service.cambiar_estado(db, current_user.id, id, EstadoSuscripcion.PAUSADA)
    return suscripcion_service.obtener_suscripcion_detalle(db, current_user.id, id)

@router.post("/{id}/reactivar", response_model=SuscripcionResponse)
def reactivar_suscripcion(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    suscripcion_service.cambiar_estado(db, current_user.id, id, EstadoSuscripcion.ACTIVA)
    return suscripcion_service.obtener_suscripcion_detalle(db, current_user.id, id)

@router.post("/{id}/cancelar", response_model=SuscripcionResponse)
def cancelar_suscripcion(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    suscripcion_service.cambiar_estado(db, current_user.id, id, EstadoSuscripcion.CANCELADA)
    return suscripcion_service.obtener_suscripcion_detalle(db, current_user.id, id)

@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_suscripcion(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    suscripcion_service.eliminar_suscripcion(db, current_user.id, id)
    return None
