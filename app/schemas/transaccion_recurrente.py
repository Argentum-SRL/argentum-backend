from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.transaccion_recurrente import (
    EstadoTransaccionRecurrente,
    FrecuenciaTransaccionRecurrente,
    TipoTransaccionRecurrente,
)
from app.models.usuario import Moneda


class TransaccionRecurrenteBase(BaseModel):
    tipo: TipoTransaccionRecurrente
    monto: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2, description="Monto mayor a 0")
    moneda: Moneda
    descripcion: str = Field(default="", max_length=300)
    categoria_id: UUID
    subcategoria_id: UUID | None = None
    billetera_id: UUID
    frecuencia: FrecuenciaTransaccionRecurrente
    dia_registro: int
    estado: EstadoTransaccionRecurrente = EstadoTransaccionRecurrente.ACTIVA


class TransaccionRecurrenteCreate(TransaccionRecurrenteBase):
    pass


class TransaccionRecurrenteUpdate(BaseModel):
    tipo: TipoTransaccionRecurrente | None = None
    monto: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
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
    usuario_id: UUID
    categoria_id: UUID | None = None
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)