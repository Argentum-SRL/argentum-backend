from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.tipos import DecimalJSON


class PeriodoPresupuestoBase(BaseModel):
    presupuesto_id: UUID
    fecha_inicio: date
    fecha_fin: date
    monto_limite: DecimalJSON
    monto_usado: DecimalJSON = Decimal("0")
    superado: bool = False


class PeriodoPresupuestoCreate(PeriodoPresupuestoBase):
    pass


class PeriodoPresupuestoUpdate(BaseModel):
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    monto_limite: DecimalJSON | None = None
    monto_usado: DecimalJSON | None = None
    superado: bool | None = None


class PeriodoPresupuestoRead(PeriodoPresupuestoBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)