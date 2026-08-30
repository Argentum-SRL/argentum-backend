from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.usuario import Moneda


class HistorialSuscripcionBase(BaseModel):
    suscripcion_id: UUID
    monto: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    moneda: Moneda
    vigente_desde: date

    @field_validator('monto')
    @classmethod
    def validar_monto(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El monto debe ser mayor a cero.")
        if v.as_tuple().exponent < -2:
            raise ValueError("El monto no puede tener más de 2 decimales.")
        return v


class HistorialSuscripcionCreate(HistorialSuscripcionBase):
    pass


class HistorialSuscripcionUpdate(BaseModel):
    monto: Decimal | None = Field(None, gt=0, max_digits=15, decimal_places=2)
    moneda: Moneda | None = None
    vigente_desde: date | None = None

    @field_validator('monto')
    @classmethod
    def validar_monto_update(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v <= 0:
                raise ValueError("El monto debe ser mayor a cero.")
            if v.as_tuple().exponent < -2:
                raise ValueError("El monto no puede tener más de 2 decimales.")
        return v


class HistorialSuscripcionRead(HistorialSuscripcionBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)