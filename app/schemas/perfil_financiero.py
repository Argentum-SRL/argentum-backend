from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PerfilFinancieroBase(BaseModel):
    usuario_id: UUID | None = None
    tasa_ahorro: Decimal | None = None
    score_impulsividad: int | None = None
    ratio_cuotas: Decimal | None = None
    cumplimiento_presupuesto: Decimal | None = None
    consistencia_registro: Decimal | None = None
    porcentaje_suscripciones: Decimal | None = None
    ultima_actualizacion: datetime | None = None


class PerfilFinancieroCreate(PerfilFinancieroBase):
    pass


class PerfilFinancieroUpdate(BaseModel):
    tasa_ahorro: Decimal | None = None
    score_impulsividad: int | None = None
    ratio_cuotas: Decimal | None = None
    cumplimiento_presupuesto: Decimal | None = None
    consistencia_registro: Decimal | None = None
    porcentaje_suscripciones: Decimal | None = None
    ultima_actualizacion: datetime | None = None


class PerfilFinancieroRead(PerfilFinancieroBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)