from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.models.historial_perfil_financiero import HistorialPerfilFinanciero
from app.schemas.perfil_financiero import PerfilFinancieroRead, HistorialPerfilFinancieroRead
from app.services import perfil_financiero_service


router = APIRouter(prefix="/perfil-financiero", tags=["perfil-financiero"])


class InterpretacionDetalle(BaseModel):
    label: str
    nivel: str  # 'excelente' | 'bien' | 'moderado' | 'bajo' | 'critico' | 'sin_datos'


class PerfilInterpretaciones(BaseModel):
    tasa_ahorro: InterpretacionDetalle
    score_impulsividad: InterpretacionDetalle
    ratio_cuotas: InterpretacionDetalle
    cumplimiento_presupuesto: InterpretacionDetalle
    consistencia_registro: InterpretacionDetalle
    porcentaje_suscripciones: InterpretacionDetalle


class PerfilFinancieroResponse(PerfilFinancieroRead):
    interpretaciones: PerfilInterpretaciones


def construir_interpretaciones(perfil) -> dict:
    from decimal import Decimal

    # 1. Tasa de ahorro
    if perfil.tasa_ahorro is None:
        tasa_ahorro = {"label": "Sin datos de ingreso", "nivel": "sin_datos"}
    elif perfil.tasa_ahorro >= Decimal("0.20"):
        tasa_ahorro = {"label": "Excelente", "nivel": "excelente"}
    elif perfil.tasa_ahorro >= Decimal("0.05"):
        tasa_ahorro = {"label": "Bien", "nivel": "bien"}
    elif perfil.tasa_ahorro >= Decimal("0.00"):
        tasa_ahorro = {"label": "Ajustado", "nivel": "moderado"}
    else:
        tasa_ahorro = {"label": "Déficit", "nivel": "critico"}

    # 2. Impulsividad
    if perfil.score_impulsividad is None:
        score_impulsividad = {"label": "Pocos datos", "nivel": "sin_datos"}
    elif perfil.score_impulsividad <= 30:
        score_impulsividad = {"label": "Disciplinado", "nivel": "excelente"}
    elif perfil.score_impulsividad <= 60:
        score_impulsividad = {"label": "Moderado", "nivel": "moderado"}
    else:
        score_impulsividad = {"label": "Impulsivo", "nivel": "critico"}

    # 3. Ratio cuotas
    if perfil.ratio_cuotas is None:
        ratio_cuotas = {"label": "Sin datos", "nivel": "sin_datos"}
    elif perfil.ratio_cuotas <= Decimal("0.25"):
        ratio_cuotas = {"label": "Manejable", "nivel": "excelente"}
    elif perfil.ratio_cuotas <= Decimal("0.40"):
        ratio_cuotas = {"label": "Moderado", "nivel": "moderado"}
    else:
        ratio_cuotas = {"label": "Elevado", "nivel": "critico"}

    # 4. Cumplimiento presupuesto
    if perfil.cumplimiento_presupuesto is None:
        cumplimiento_presupuesto = {"label": "Sin presupuestos", "nivel": "sin_datos"}
    elif perfil.cumplimiento_presupuesto >= Decimal("0.80"):
        cumplimiento_presupuesto = {"label": "Excelente", "nivel": "excelente"}
    elif perfil.cumplimiento_presupuesto >= Decimal("0.50"):
        cumplimiento_presupuesto = {"label": "Regular", "nivel": "moderado"}
    else:
        cumplimiento_presupuesto = {"label": "Mejorar", "nivel": "critico"}

    # 5. Consistencia registro
    if perfil.consistencia_registro is None:
        consistencia_registro = {"label": "Sin datos", "nivel": "sin_datos"}
    elif perfil.consistencia_registro >= Decimal("0.80"):
        consistencia_registro = {"label": "Constante", "nivel": "excelente"}
    elif perfil.consistencia_registro >= Decimal("0.50"):
        consistencia_registro = {"label": "Irregular", "nivel": "moderado"}
    else:
        consistencia_registro = {"label": "Esporádico", "nivel": "critico"}

    # 6. Porcentaje suscripciones
    if perfil.porcentaje_suscripciones is None:
        porcentaje_suscripciones = {"label": "Sin datos de gasto", "nivel": "sin_datos"}
    elif perfil.porcentaje_suscripciones <= Decimal("0.10"):
        porcentaje_suscripciones = {"label": "Bajo", "nivel": "excelente"}
    elif perfil.porcentaje_suscripciones <= Decimal("0.20"):
        porcentaje_suscripciones = {"label": "Moderado", "nivel": "moderado"}
    else:
        porcentaje_suscripciones = {"label": "Alto", "nivel": "critico"}

    return {
        "tasa_ahorro": tasa_ahorro,
        "score_impulsividad": score_impulsividad,
        "ratio_cuotas": ratio_cuotas,
        "cumplimiento_presupuesto": cumplimiento_presupuesto,
        "consistencia_registro": consistencia_registro,
        "porcentaje_suscripciones": porcentaje_suscripciones,
    }


@router.get("", response_model=PerfilFinancieroResponse)
async def get_perfil_financiero(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    perfil = await perfil_financiero_service.obtener_perfil(db, current_user.id)
    interpretaciones = construir_interpretaciones(perfil)
    
    # Mapear a esquema de respuesta
    response_data = PerfilFinancieroRead.model_validate(perfil)
    return PerfilFinancieroResponse(
        **response_data.model_dump(),
        interpretaciones=PerfilInterpretaciones(**interpretaciones)
    )


@router.post("/recalcular", response_model=PerfilFinancieroResponse)
async def recalcular_perfil_financiero(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    perfil = await perfil_financiero_service.calcular_y_persistir_perfil(db, current_user.id)
    interpretaciones = construir_interpretaciones(perfil)
    
    # Mapear a esquema de respuesta
    response_data = PerfilFinancieroRead.model_validate(perfil)
    return PerfilFinancieroResponse(
        **response_data.model_dump(),
        interpretaciones=PerfilInterpretaciones(**interpretaciones)
    )


@router.get("/historial", response_model=List[HistorialPerfilFinancieroRead])
def get_historial_perfil_financiero(
    limite: int = Query(6, ge=1),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Retorna el historial de snapshots de perfil financiero del usuario.
    """
    query = (
        select(HistorialPerfilFinanciero)
        .where(HistorialPerfilFinanciero.usuario_id == current_user.id)
        .order_by(HistorialPerfilFinanciero.periodo_inicio.desc())
        .limit(limite)
    )
    snapshots = db.execute(query).scalars().all()
    return snapshots

