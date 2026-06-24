from __future__ import annotations
import logging
from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user, get_current_admin_user
from app.models.usuario import Usuario
from app.schemas.analisis_ia import AnalisisIACreate, AnalisisIAResponse, ExportacionResponse
from app.services import analisis_ia_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analisis-ia", tags=["Análisis IA"])


@router.post("/generar", response_model=AnalisisIAResponse, status_code=status.HTTP_201_CREATED)
def generar(
    body: AnalisisIACreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user)
):
    """
    Genera un nuevo análisis financiero basado en IA para el usuario actual (solo administradores).
    """
    # TODO: cuando se abra a usuarios premium, reemplazar get_current_admin_user por get_current_user y agregar validación de límite de uso por ciclo
    try:
        return analisis_ia_service.generar_analisis(
            db=db,
            usuario_id=current_user.id,
            tipo_analisis=body.tipo_analisis,
            ciclos=body.ciclos
        )
    except ValueError as e:
        if "DATOS_INSUFICIENTES" in str(e):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "DATOS_INSUFICIENTES", "message": str(e)}
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BAD_REQUEST", "message": str(e)}
        )
    except Exception as e:
        logger.exception("Error al generar el análisis")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "ERROR_GENERACION", "message": "Error al generar el análisis"}
        )


@router.get("/historial", response_model=List[AnalisisIAResponse], status_code=status.HTTP_200_OK)
def historial(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user)
):
    """
    Obtiene el historial de análisis financieros del usuario actual (solo administradores).
    """
    return analisis_ia_service.obtener_historial(db=db, usuario_id=current_user.id, limit=limit)


@router.get("/exportar/texto", response_model=ExportacionResponse, status_code=status.HTTP_200_OK)
def exportar_texto(
    ciclos: int = Query(default=3, ge=2, le=6),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Genera una versión en texto plano legible del análisis financiero para exportar a IA externa.
    """
    try:
        return analisis_ia_service.generar_texto_exportacion(db=db, usuario_id=current_user.id, ciclos=ciclos)
    except ValueError as e:
        if "DATOS_INSUFICIENTES" in str(e):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "DATOS_INSUFICIENTES", "message": str(e)}
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BAD_REQUEST", "message": str(e)}
        )
    except Exception as e:
        logger.exception("Error al generar el texto de exportación")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "ERROR_EXPORTACION", "message": "Error al exportar los datos"}
        )


@router.get("/{analisis_id}", response_model=AnalisisIAResponse, status_code=status.HTTP_200_OK)
def obtener_detalle(
    analisis_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user)
):
    """
    Obtiene un análisis financiero específico del usuario actual por ID (solo administradores).
    """
    try:
        a_id = UUID(analisis_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_ID", "message": "ID de análisis inválido."}
        )
    
    res = analisis_ia_service.obtener_por_id(db=db, usuario_id=current_user.id, analisis_id=a_id)
    if res is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ANALISIS_NOT_FOUND", "message": "Análisis no encontrado"}
        )
    return res
