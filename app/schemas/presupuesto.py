from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.presupuesto import EstadoPresupuesto, PeriodoPresupuestoTipo, RenovacionPresupuesto
from app.models.usuario import Moneda


class PresupuestoBase(BaseModel):
    usuario_id: UUID
    nombre: str
    monto: Decimal
    moneda: Moneda
    periodo: PeriodoPresupuestoTipo
    renovacion: RenovacionPresupuesto
    estado: EstadoPresupuesto = EstadoPresupuesto.ACTIVO


class PresupuestoCreate(PresupuestoBase):
    pass


class PresupuestoUpdate(BaseModel):
    nombre: str | None = None
    monto: Decimal | None = None
    moneda: Moneda | None = None
    periodo: PeriodoPresupuestoTipo | None = None
    renovacion: RenovacionPresupuesto | None = None
    estado: EstadoPresupuesto | None = None


class PresupuestoRead(PresupuestoBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)