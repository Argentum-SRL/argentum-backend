from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.usuario import Moneda


class GrupoCuotasBase(BaseModel):
    usuario_id: UUID
    transaccion_padre_id: UUID
    descripcion: str
    monto_total: Decimal
    cantidad_cuotas: int
    tiene_interes: bool = False
    tasa_interes: Decimal | None = None
    total_financiado: Decimal
    moneda: Moneda


class GrupoCuotasCreate(GrupoCuotasBase):
    pass


class GrupoCuotasUpdate(BaseModel):
    descripcion: str | None = None
    monto_total: Decimal | None = None
    cantidad_cuotas: int | None = None
    tiene_interes: bool | None = None
    tasa_interes: Decimal | None = None
    total_financiado: Decimal | None = None
    moneda: Moneda | None = None


class GrupoCuotasRead(GrupoCuotasBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)