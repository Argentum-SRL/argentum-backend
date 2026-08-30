from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.movimiento_meta import TipoMovimientoMeta
from app.models.usuario import Moneda
from app.schemas.billetera import BilleteraRead


class MovimientoMetaBase(BaseModel):
    tipo: TipoMovimientoMeta
    monto: Decimal = Field(..., gt=Decimal("0"), le=Decimal("9999999999999.99"))
    moneda_movimiento: Moneda
    cotizacion_usada: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999999.9999"))
    tipo_dolar_usado: str | None = Field(default=None, max_length=30)
    billetera_id: UUID
    fecha: date

    @field_validator("monto")
    @classmethod
    def validar_monto(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("El monto del movimiento tiene que ser mayor a cero.")
        if v.as_tuple().exponent < -2:
            raise ValueError("El monto no puede tener más de 2 decimales.")
        return v

    @field_validator("cotizacion_usada")
    @classmethod
    def validar_cotizacion(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v <= Decimal("0"):
                raise ValueError("La cotización debe ser mayor a cero.")
            if v.as_tuple().exponent < -4:
                raise ValueError("La cotización no puede tener más de 4 decimales.")
        return v

    @field_validator("tipo_dolar_usado")
    @classmethod
    def validar_tipo_dolar(cls, v: str | None) -> str | None:
        if v is not None:
            s = v.strip()
            return s if s else None
        return None


class MovimientoMetaCreate(MovimientoMetaBase):
    pass


class MovimientoMetaUpdate(BaseModel):
    tipo: TipoMovimientoMeta | None = None
    monto: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999999999999.99"))
    moneda_movimiento: Moneda | None = None
    cotizacion_usada: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999999.9999"))
    tipo_dolar_usado: str | None = Field(default=None, max_length=30)
    billetera_id: UUID | None = None
    fecha: date | None = None

    @field_validator("monto")
    @classmethod
    def validar_monto(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v <= Decimal("0"):
                raise ValueError("El monto del movimiento tiene que ser mayor a cero.")
            if v.as_tuple().exponent < -2:
                raise ValueError("El monto no puede tener más de 2 decimales.")
        return v

    @field_validator("cotizacion_usada")
    @classmethod
    def validar_cotizacion(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v <= Decimal("0"):
                raise ValueError("La cotización debe ser mayor a cero.")
            if v.as_tuple().exponent < -4:
                raise ValueError("La cotización no puede tener más de 4 decimales.")
        return v


class MovimientoMetaRead(MovimientoMetaBase):
    id: UUID
    meta_id: UUID
    fecha_creacion: datetime
    billetera: Optional[BilleteraRead] = None

    model_config = ConfigDict(from_attributes=True)