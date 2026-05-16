from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from typing import Optional
from pydantic import BaseModel, ConfigDict

from app.models.movimiento_meta import TipoMovimientoMeta
from app.models.usuario import Moneda
from app.schemas.billetera import BilleteraRead


class MovimientoMetaBase(BaseModel):
    tipo: TipoMovimientoMeta
    monto: Decimal
    moneda_movimiento: Moneda
    cotizacion_usada: Decimal | None = None
    tipo_dolar_usado: str | None = None
    billetera_id: UUID
    fecha: date


class MovimientoMetaCreate(MovimientoMetaBase):
    pass


class MovimientoMetaUpdate(BaseModel):
    tipo: TipoMovimientoMeta | None = None
    monto: Decimal | None = None
    moneda_movimiento: Moneda | None = None
    cotizacion_usada: Decimal | None = None
    tipo_dolar_usado: str | None = None
    billetera_id: UUID | None = None
    fecha: date | None = None


class MovimientoMetaRead(MovimientoMetaBase):
    id: UUID
    meta_id: UUID
    fecha_creacion: datetime
    billetera: Optional[BilleteraRead] = None

    model_config = ConfigDict(from_attributes=True)