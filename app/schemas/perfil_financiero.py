from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PerfilFinancieroBase(BaseModel):
    usuario_id: UUID | None = None
    tasa_ahorro_ars: Decimal | None = None
    tasa_ahorro_usd: Decimal | None = None
    score_impulsividad_ars: int | None = None
    score_impulsividad_usd: int | None = None
    ratio_cuotas_ars: Decimal | None = None
    ratio_cuotas_usd: Decimal | None = None
    cumplimiento_presupuesto: Decimal | None = None
    consistencia_registro: Decimal | None = None
    porcentaje_suscripciones_ars: Decimal | None = None
    porcentaje_suscripciones_usd: Decimal | None = None
    ultima_actualizacion: datetime | None = None


class PerfilFinancieroCreate(PerfilFinancieroBase):
    pass


class PerfilFinancieroUpdate(BaseModel):
    tasa_ahorro_ars: Decimal | None = None
    tasa_ahorro_usd: Decimal | None = None
    score_impulsividad_ars: int | None = None
    score_impulsividad_usd: int | None = None
    ratio_cuotas_ars: Decimal | None = None
    ratio_cuotas_usd: Decimal | None = None
    cumplimiento_presupuesto: Decimal | None = None
    consistencia_registro: Decimal | None = None
    porcentaje_suscripciones_ars: Decimal | None = None
    porcentaje_suscripciones_usd: Decimal | None = None
    ultima_actualizacion: datetime | None = None


class PerfilFinancieroRead(PerfilFinancieroBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)


class PerfilNuevoRead(BaseModel):
    ciclos_con_datos: int
    ciclos_observados: int
    nivel_confianza: str
    cobertura_registro: Decimal
    ingreso_tipico_ars: Decimal | None = None
    estabilidad_ingreso_mad: Decimal | None = None
    ingreso_actual_percentil: Decimal | None = None
    gasto_comprometido_ars: Decimal
    gasto_comprometido_ratio: Decimal | None = None
    capacidad_ahorro: Decimal | None = None
    capacidad_ahorro_percentil: Decimal | None = None
    runway_meses: Decimal | None = None
    volatilidad_gasto_variable: Decimal | None = None
    gasto_actual_percentil: Decimal | None = None
    consistencia_registro: Decimal | None = None
    metodo: str


class HistorialPerfilFinancieroRead(BaseModel):
    id: UUID
    usuario_id: UUID
    periodo_inicio: date
    periodo_fin: date
    tasa_ahorro_ars: Decimal | None = None
    tasa_ahorro_usd: Decimal | None = None
    score_impulsividad_ars: int | None = None
    score_impulsividad_usd: int | None = None
    ratio_cuotas_ars: Decimal | None = None
    ratio_cuotas_usd: Decimal | None = None
    cumplimiento_presupuesto: Decimal | None = None
    consistencia_registro: Decimal | None = None
    porcentaje_suscripciones_ars: Decimal | None = None
    porcentaje_suscripciones_usd: Decimal | None = None
    fecha_snapshot: datetime

    model_config = ConfigDict(from_attributes=True)
