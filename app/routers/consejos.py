# -*- coding: utf-8 -*-
"""
app/routers/consejos.py
Rutas del motor de consejos (tarjeta 'Un dato para vos' del inicio).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.schemas.consejo import (
    ConsejoActualResponse,
    RespuestaConsejoRequest,
    RespuestaConsejoResponse,
)
from app.services import consejos_service
from app.services.consejos_reglas import TEXTO_ACCION
from app.services.consejos_service import (
    consejo_activo,
    consejo_cumplido,
    resolver_cumplidos,
    responder,
)

router = APIRouter(prefix="/consejos", tags=["consejos"])


@router.get("/actual", response_model=ConsejoActualResponse)
def get_consejo_actual(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Retorna el consejo activo guardado del usuario, sin recalcular."""
    hoy = consejos_service.hoy_argentina()
    activo = consejo_activo(db, current_user.id, hoy=hoy)
    if not activo:
        return {"consejo": None}

    if consejo_cumplido(db, current_user, activo, hoy):
        resolver_cumplidos(db, current_user, hoy, commit=True)
        return {"consejo": None}

    accion_texto = TEXTO_ACCION.get(activo.accion) if activo.accion else None
    return {
        "consejo": {
            "id": activo.id,
            "tipo": activo.tipo,
            "texto": activo.texto,
            "accion": activo.accion,
            "accion_texto": accion_texto,
            "accion_datos": activo.accion_datos,
        }
    }


@router.post("/{consejo_id}/respuesta", response_model=RespuestaConsejoResponse)
def post_respuesta_consejo(
    consejo_id: UUID,
    body: RespuestaConsejoRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Registra la respuesta del usuario a un consejo emitido."""
    if body.respuesta not in {"sirvio", "no_sirve", "no_avisar"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Respuesta no válida.",
        )

    try:
        responder(db, current_user.id, consejo_id, body.respuesta)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consejo no encontrado.",
        )

    return {"ok": True}

