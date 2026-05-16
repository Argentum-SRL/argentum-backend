from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.schemas.meta import (
    MetaCreate,
    MetaUpdate,
    MetaRead,
    GoalAnalyticsResponse,
    GoalSummaryResponse
)
from app.schemas.movimiento_meta import (
    MovimientoMetaCreate,
    MovimientoMetaRead
)
from app.services import meta_service

router = APIRouter(tags=["metas"])

@router.get("/", response_model=List[MetaRead])
def listar_metas(
    activas_solo: bool = False,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    return meta_service.obtener_metas(db, usuario.id, activas_solo)

@router.post("/", response_model=MetaRead)
def crear_meta(
    data: MetaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    return meta_service.crear_meta(db, usuario.id, data)

@router.get("/summary", response_model=GoalSummaryResponse)
def obtener_summary(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    return meta_service.obtener_summary(db, usuario.id)

@router.get("/{id}", response_model=MetaRead)
def obtener_meta(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    return meta_service.obtener_meta(db, usuario.id, id)

@router.patch("/{id}", response_model=MetaRead)
def actualizar_meta(
    id: UUID,
    data: MetaUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    return meta_service.actualizar_meta(db, usuario.id, id, data)

@router.delete("/{id}")
def eliminar_meta(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    meta_service.eliminar_meta(db, usuario.id, id)
    return {"detail": "Meta eliminada correctamente"}

@router.post("/{id}/movimientos", response_model=MovimientoMetaRead)
def registrar_movimiento(
    id: UUID,
    data: MovimientoMetaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    return meta_service.registrar_movimiento(db, usuario.id, id, data)

@router.delete("/{id}/movimientos/{movimiento_id}")
def eliminar_movimiento(
    id: UUID,
    movimiento_id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    meta_service.eliminar_movimiento(db, usuario.id, id, movimiento_id)
    return {"detail": "Movimiento eliminado correctamente"}

@router.get("/{id}/analytics", response_model=GoalAnalyticsResponse)
def obtener_analytics(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    return meta_service.obtener_analytics(db, usuario.id, id)
