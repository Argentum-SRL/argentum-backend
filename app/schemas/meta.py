from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.meta import EstadoMeta
from app.models.usuario import Moneda


from app.schemas.movimiento_meta import MovimientoMetaRead


class MetaBase(BaseModel):
    usuario_id: UUID | None = None
    nombre: str
    monto_objetivo: Decimal
    moneda: Moneda
    monto_actual: Decimal = Decimal("0")
    fecha_limite: date | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    nota: str | None = None
    estado: EstadoMeta = EstadoMeta.ACTIVA


class MetaCreate(MetaBase):
    pass


class MetaUpdate(BaseModel):
    nombre: str | None = None
    monto_objetivo: Decimal | None = None
    moneda: Moneda | None = None
    monto_actual: Decimal | None = None
    fecha_limite: date | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    nota: str | None = None
    estado: EstadoMeta | None = None


class MetaRead(MetaBase):
    id: UUID
    fecha_creacion: datetime
    movimientos: List[MovimientoMetaRead] = []

    model_config = ConfigDict(from_attributes=True)


class GoalAnalyticsResponse(BaseModel):
    chart_data: List[Dict[str, Any]]
    velocidad_mensual: float
    meses_restantes: float | None
    fecha_estimada_finalizacion: date | None
    porcentaje_progreso: float
    monto_faltante: float


class GoalSummaryResponse(BaseModel):
    total_metas: int
    completadas: int
    proximo_vencimiento: date | None