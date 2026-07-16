from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
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