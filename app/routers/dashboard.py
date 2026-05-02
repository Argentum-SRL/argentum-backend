from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Any

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@router.get("/resumen")
def get_resumen(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna el resumen del dashboard para el usuario autenticado.
    """
    return dashboard_service.get_dashboard_resumen(db, current_user)

@router.get("/cotizacion")
def get_cotizacion(
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna la cotizacion del dolar segun la preferencia del usuario.
    """
    return dashboard_service.get_cotizacion_usuario(current_user)
