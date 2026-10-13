"""
app/routers/salud.py — Endpoint de verificacion de salud (healthcheck) para Railway y monitoreo.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["salud"])


@router.get("/salud")
def verificar_salud(db: Session = Depends(get_db)):
    """
    Ruta de comprobacion de salud para Railway y monitoreo del backend.
    Sin autenticacion y sin limite de peticiones (rate limit).
    Ejecuta SELECT 1 en la base de datos:
    - 200 {"estado": "ok"} si la base de datos responde.
    - 503 {"estado": "error"} si la conexion o la consulta fallan (sin detalles en la respuesta).
    """
    try:
        db.execute(text("SELECT 1"))
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"estado": "ok"},
        )
    except Exception as exc:
        logger.error("Error al verificar la conexion a la base de datos en /salud: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"estado": "error"},
        )
