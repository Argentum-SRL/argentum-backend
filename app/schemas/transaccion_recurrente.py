from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.transaccion_recurrente import (
    EstadoTransaccionRecurrente,
    FrecuenciaTransaccionRecurrente,
    TipoTransaccionRecurrente,
)
from app.models.usuario import Moneda


class TransaccionRecurrenteBase(BaseModel):
    usuario_id: UUID | None = None
    tipo: TipoTransaccionRecurrente
    monto: Decimal
    moneda: Moneda
    descripcion: str
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    billetera_id: UUID
    frecuencia: FrecuenciaTransaccionRecurrente
    dia_registro: int
    estado: EstadoTransaccionRecurrente = EstadoTransaccionRecurrente.ACTIVA


class TransaccionRecurrenteCreate(TransaccionRecurrenteBase):
    pass


class TransaccionRecurrenteUpdate(BaseModel):
    tipo: TipoTransaccionRecurrente | None = None
    monto: Decimal | None = None
    moneda: Moneda | None = None
    descripcion: str | None = None
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    billetera_id: UUID | None = None
    frecuencia: FrecuenciaTransaccionRecurrente | None = None
    dia_registro: int | None = None
    estado: EstadoTransaccionRecurrente | None = None


class TransaccionRecurrenteRead(TransaccionRecurrenteBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)