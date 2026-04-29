from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.usuario import Moneda


class HistorialSuscripcionBase(BaseModel):
    suscripcion_id: UUID
    monto: Decimal
    moneda: Moneda
    vigente_desde: date


class HistorialSuscripcionCreate(HistorialSuscripcionBase):
    pass


class HistorialSuscripcionUpdate(BaseModel):
    monto: Decimal | None = None
    moneda: Moneda | None = None
    vigente_desde: date | None = None


class HistorialSuscripcionRead(HistorialSuscripcionBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)