from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.tipos import DecimalJSON


class CuotaBase(BaseModel):
    grupo_id: UUID
    transaccion_id: UUID
    numero_cuota: int
    monto_proyectado: DecimalJSON
    monto_real: DecimalJSON | None = None
    fecha_vencimiento: date
    ajustada_manual: bool = False
    pagada: bool = False


class CuotaCreate(CuotaBase):
    pass


class CuotaUpdate(BaseModel):
    numero_cuota: int | None = None
    monto_proyectado: DecimalJSON | None = None
    monto_real: DecimalJSON | None = None
    fecha_vencimiento: date | None = None
    ajustada_manual: bool | None = None
    pagada: bool | None = None


class CuotaRead(CuotaBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)