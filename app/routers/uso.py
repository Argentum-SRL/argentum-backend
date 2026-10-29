# -*- coding: utf-8 -*-
"""
app/routers/uso.py — Endpoints para registro de eventos de uso de la aplicación web.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.services.uso_service import registrar_evento

router = APIRouter(prefix="/uso", tags=["uso"])


@router.post("/visita", status_code=status.HTTP_204_NO_CONTENT)
def registrar_visita(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Registra el evento de visita del usuario en el día actual (zona horaria argentina).
    Siempre responde con status HTTP 204 No Content.
    """
    registrar_evento(db, current_user.id, "visita")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

