from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.usuario import Moneda


class TransferenciaInternaBase(BaseModel):
    usuario_id: UUID | None = None
    billetera_origen_id: UUID
    billetera_destino_id: UUID
    monto: Decimal
    moneda: Moneda
    fecha: date
    notas: str | None = None


class TransferenciaInternaCreate(TransferenciaInternaBase):
    pass


class TransferenciaInternaUpdate(BaseModel):
    billetera_origen_id: UUID | None = None
    billetera_destino_id: UUID | None = None
    monto: Decimal | None = None
    moneda: Moneda | None = None
    fecha: date | None = None
    notas: str | None = None


class TransferenciaInternaRead(TransferenciaInternaBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)