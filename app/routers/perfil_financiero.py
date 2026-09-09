from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.models.historial_perfil_financiero import HistorialPerfilFinanciero
from app.schemas.perfil_financiero import PerfilFinancieroRead, HistorialPerfilFinancieroRead, PerfilNuevoRead
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
    perfil_nuevo: PerfilNuevoRead


def construir_interpretaciones(perfil) -> dict:
    """Construye descripciones neutrales sin juicios de valor, umbrales fijos ni inferencias de personalidad."""
    # Tasa de ahorro
    if perfil.tasa_ahorro_ars is None:
        tasa_ahorro_ars = {"label": "Sin datos", "nivel": "sin_datos"}
    else:
        tasa_ahorro_ars = {"label": f"{round(perfil.tasa_ahorro_ars * 100)}% de tu ingreso", "nivel": "moderado"}

    tasa_ahorro_usd = {"label": "Sin datos USD", "nivel": "sin_datos"}

    # Impulsividad (Eliminado)
    score_impulsividad_ars = {"label": "No medido", "nivel": "sin_datos"}
    score_impulsividad_usd = {"label": "No medido", "nivel": "sin_datos"}

    # Gasto comprometido / ratio de cuotas
    if perfil.ratio_cuotas_ars is None:
        ratio_cuotas_ars = {"label": "Sin datos", "nivel": "sin_datos"}
    else:
        ratio_cuotas_ars = {"label": f"{round(perfil.ratio_cuotas_ars * 100)}% de tu ingreso", "nivel": "moderado"}

    ratio_cuotas_usd = {"label": "Sin datos USD", "nivel": "sin_datos"}

    # Suscripciones (subsumidas en compromisos)
    porcentaje_suscripciones_ars = {"label": "En compromisos", "nivel": "sin_datos"}
    porcentaje_suscripciones_usd = {"label": "En compromisos", "nivel": "sin_datos"}

    # Cumplimiento presupuestos (Eliminado del perfil de salud financiera)
    cumplimiento_presupuesto = {"label": "No aplicable", "nivel": "sin_datos"}

    # Cobertura de registro
    if perfil.consistencia_registro is None:
        consistencia_registro = {"label": "Sin datos", "nivel": "sin_datos"}
    else:
        consistencia_registro = {"label": f"{round(perfil.consistencia_registro * 100)}% activo", "nivel": "moderado"}

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
    from app.services.analisis_financiero_service import calcular_perfil_nuevo
    perfil_nuevo = calcular_perfil_nuevo(db, current_user)
    
    # Mapear a esquema de respuesta
    response_data = PerfilFinancieroRead.model_validate(perfil)
    return PerfilFinancieroResponse(
        **response_data.model_dump(),
        interpretaciones=PerfilInterpretaciones(**interpretaciones),
        perfil_nuevo=PerfilNuevoRead(**perfil_nuevo)
    )


@router.post("/recalcular", response_model=PerfilFinancieroResponse)
def recalcular_perfil_financiero(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    perfil = perfil_financiero_service.calcular_y_persistir_perfil(db, current_user.id)
    interpretaciones = construir_interpretaciones(perfil)
    from app.services.analisis_financiero_service import calcular_perfil_nuevo
    perfil_nuevo = calcular_perfil_nuevo(db, current_user)
    
    # Mapear a esquema de respuesta
    response_data = PerfilFinancieroRead.model_validate(perfil)
    return PerfilFinancieroResponse(
        **response_data.model_dump(),
        interpretaciones=PerfilInterpretaciones(**interpretaciones),
        perfil_nuevo=PerfilNuevoRead(**perfil_nuevo)
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

