from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.schemas.tools import InstallmentConvenienceRequest
from app.services import tools_service

router = APIRouter()


@router.get("/ipc/current")
def get_current_ipc_endpoint(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Devuelve el IPC mensual actual de Argentina para pre-cargar en el frontend.
    """
    ipc_cache = tools_service.get_current_ipc(db)
    return {
        "success": True,
        "data": {
            "valor_mensual": ipc_cache.valor_mensual,
            "fecha_dato": ipc_cache.fecha_dato,
            "es_estimado": ipc_cache.es_estimado,
            "fuente": ipc_cache.fuente,
            "ultima_actualizacion": ipc_cache.fecha_actualizacion.isoformat()
        }
    }


@router.post("/installment-convenience")
def calculate_convenience_endpoint(
    body: InstallmentConvenienceRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Calcula si conviene pagar en cuotas o de contado bajo la inflación provista.
    """
    result = tools_service.calcular_conveniencia_cuotas(body)
    return {
        "success": True,
        "data": result
    }
