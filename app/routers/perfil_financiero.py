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
    tasa_ahorro_ars: InterpretacionDetalle
    tasa_ahorro_usd: InterpretacionDetalle
    score_impulsividad_ars: InterpretacionDetalle
    score_impulsividad_usd: InterpretacionDetalle
    ratio_cuotas_ars: InterpretacionDetalle
    ratio_cuotas_usd: InterpretacionDetalle
    cumplimiento_presupuesto: InterpretacionDetalle
    consistencia_registro: InterpretacionDetalle
    porcentaje_suscripciones_ars: InterpretacionDetalle
    porcentaje_suscripciones_usd: InterpretacionDetalle


class PerfilFinancieroResponse(PerfilFinancieroRead):
    interpretaciones: PerfilInterpretaciones


def construir_interpretaciones(perfil) -> dict:
    from decimal import Decimal

    # Helper function for tasa_ahorro
    def interp_tasa_ahorro(val, coin):
        if val is None:
            return {"label": f"Sin datos de ingreso {coin}", "nivel": "sin_datos"}
        elif val >= Decimal("0.20"):
            return {"label": "Excelente", "nivel": "excelente"}
        elif val >= Decimal("0.05"):
            return {"label": "Bien", "nivel": "bien"}
        elif val >= Decimal("0.00"):
            return {"label": "Ajustado", "nivel": "moderado"}
        else:
            return {"label": "Déficit", "nivel": "critico"}

    # Helper function for impulsividad
    def interp_impulsividad(val, coin):
        if val is None:
            return {"label": f"Pocos datos {coin}", "nivel": "sin_datos"}
        elif val <= 30:
            return {"label": "Disciplinado", "nivel": "excelente"}
        elif val <= 60:
            return {"label": "Moderado", "nivel": "moderado"}
        else:
            return {"label": "Impulsivo", "nivel": "critico"}

    # Helper function for ratio_cuotas
    def interp_ratio_cuotas(val, coin):
        if val is None:
            return {"label": f"Sin datos {coin}", "nivel": "sin_datos"}
        elif val <= Decimal("0.25"):
            return {"label": "Manejable", "nivel": "excelente"}
        elif val <= Decimal("0.40"):
            return {"label": "Moderado", "nivel": "moderado"}
        else:
            return {"label": "Elevado", "nivel": "critico"}

    # Helper function for porcentaje_suscripciones
    def interp_porcentaje_suscripciones(val, coin):
        if val is None:
            return {"label": f"Sin datos de gasto {coin}", "nivel": "sin_datos"}
        elif val <= Decimal("0.10"):
            return {"label": "Bajo", "nivel": "excelente"}
        elif val <= Decimal("0.20"):
            return {"label": "Moderado", "nivel": "moderado"}
        else:
            return {"label": "Alto", "nivel": "critico"}

    tasa_ahorro_ars = interp_tasa_ahorro(perfil.tasa_ahorro_ars, "ARS")
    tasa_ahorro_usd = interp_tasa_ahorro(perfil.tasa_ahorro_usd, "USD")

    score_impulsividad_ars = interp_impulsividad(perfil.score_impulsividad_ars, "ARS")
    score_impulsividad_usd = interp_impulsividad(perfil.score_impulsividad_usd, "USD")

    ratio_cuotas_ars = interp_ratio_cuotas(perfil.ratio_cuotas_ars, "ARS")
    ratio_cuotas_usd = interp_ratio_cuotas(perfil.ratio_cuotas_usd, "USD")

    porcentaje_suscripciones_ars = interp_porcentaje_suscripciones(perfil.porcentaje_suscripciones_ars, "ARS")
    porcentaje_suscripciones_usd = interp_porcentaje_suscripciones(perfil.porcentaje_suscripciones_usd, "USD")

    # 4. Cumplimiento presupuesto (global)
    if perfil.cumplimiento_presupuesto is None:
        cumplimiento_presupuesto = {"label": "Sin presupuestos", "nivel": "sin_datos"}
    elif perfil.cumplimiento_presupuesto >= Decimal("0.80"):
        cumplimiento_presupuesto = {"label": "Excelente", "nivel": "excelente"}
    elif perfil.cumplimiento_presupuesto >= Decimal("0.50"):
        cumplimiento_presupuesto = {"label": "Regular", "nivel": "moderado"}
    else:
        cumplimiento_presupuesto = {"label": "Mejorar", "nivel": "critico"}

    # 5. Consistencia registro (global)
    if perfil.consistencia_registro is None:
        consistencia_registro = {"label": "Sin datos", "nivel": "sin_datos"}
    elif perfil.consistencia_registro >= Decimal("0.80"):
        consistencia_registro = {"label": "Constante", "nivel": "excelente"}
    elif perfil.consistencia_registro >= Decimal("0.50"):
        consistencia_registro = {"label": "Irregular", "nivel": "moderado"}
    else:
        consistencia_registro = {"label": "Esporádico", "nivel": "critico"}

    return {
        "tasa_ahorro_ars": tasa_ahorro_ars,
        "tasa_ahorro_usd": tasa_ahorro_usd,
        "score_impulsividad_ars": score_impulsividad_ars,
        "score_impulsividad_usd": score_impulsividad_usd,
        "ratio_cuotas_ars": ratio_cuotas_ars,
        "ratio_cuotas_usd": ratio_cuotas_usd,
        "cumplimiento_presupuesto": cumplimiento_presupuesto,
        "consistencia_registro": consistencia_registro,
        "porcentaje_suscripciones_ars": porcentaje_suscripciones_ars,
        "porcentaje_suscripciones_usd": porcentaje_suscripciones_usd,
    }


@router.get("", response_model=PerfilFinancieroResponse)
def get_perfil_financiero(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    perfil = perfil_financiero_service.obtener_perfil(db, current_user.id)
    interpretaciones = construir_interpretaciones(perfil)
    
    # Mapear a esquema de respuesta
    response_data = PerfilFinancieroRead.model_validate(perfil)
    return PerfilFinancieroResponse(
        **response_data.model_dump(),
        interpretaciones=PerfilInterpretaciones(**interpretaciones)
    )


@router.post("/recalcular", response_model=PerfilFinancieroResponse)
def recalcular_perfil_financiero(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    perfil = perfil_financiero_service.calcular_y_persistir_perfil(db, current_user.id)
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

