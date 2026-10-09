"""
app/routers/patrones.py — Endpoints para gestión de patrones repetidos (Lo que se repite) y sus decisiones.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_admin_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.schemas.patrones import (
    DecisionPatronCreate,
    DeshacerDecisionRequest,
    PatronesResumenResponse,
)
from app.services import patrones_service

router = APIRouter(
    prefix="/patrones",
    tags=["patrones"],
    dependencies=[Depends(get_current_admin_user)],
)


@router.get("", response_model=PatronesResumenResponse)
def listar_patrones(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
):
    """
    Retorna la lista de las cuatro cajas de patrones repetidos:
    ingresos habituales, fijos, costumbre y día a día.
    """
    return patrones_service.armar_lo_que_se_repite(db, current_user)


@router.post("/decisiones", response_model=PatronesResumenResponse)
def registrar_decision(
    data: DecisionPatronCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
):
    """
    Registra o actualiza la decisión del usuario sobre un ítem (confirmar, descartar o mover).
    """
    return patrones_service.registrar_decision(
        db,
        current_user,
        clave_item=data.clave_item,
        decision=data.decision,
        caja_destino=data.caja_destino,
        commit=True,
    )


@router.post("/decisiones/deshacer", response_model=PatronesResumenResponse)
def deshacer_decision(
    data: DeshacerDecisionRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
):
    """
    Deshace la decisión tomada sobre un ítem.
    """
    return patrones_service.deshacer_decision(
        db,
        current_user,
        clave_item=data.clave_item,
        commit=True,
    )
