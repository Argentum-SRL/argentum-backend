from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
)
from app.models.usuario import Moneda


class InfoCuotas(BaseModel):
    cantidad_cuotas: int
    tiene_interes: bool = False
    tasa_interes: Decimal | None = None
    monto_total: Decimal # El monto base sin interes (o el total si no hay interes)


class TransaccionBase(BaseModel):
    usuario_id: UUID | None = None
    tipo: TipoTransaccion
    monto: Decimal
    moneda: Moneda
    fecha: date
    descripcion: str
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    metodo_pago: MetodoPago | None = None
    billetera_id: UUID
    es_recurrente: bool = False
    recurrente_id: UUID | None = None
    es_cuota_hija: bool = False
    es_padre_cuotas: bool = False
    grupo_cuotas_id: UUID | None = None
    origen: OrigenTransaccion
    estado_verificacion: EstadoVerificacionTransaccion | None = None


class TransaccionCreate(TransaccionBase):
    info_cuotas: InfoCuotas | None = None


class TransaccionUpdate(BaseModel):
    tipo: TipoTransaccion | None = None
    monto: Decimal | None = None
    moneda: Moneda | None = None
    fecha: date | None = None
    descripcion: str | None = None
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    metodo_pago: MetodoPago | None = None
    billetera_id: UUID | None = None
    es_recurrente: bool | None = None
    recurrente_id: UUID | None = None
    es_cuota_hija: bool | None = None
    es_padre_cuotas: bool | None = None
    grupo_cuotas_id: UUID | None = None
    origen: OrigenTransaccion | None = None
    estado_verificacion: EstadoVerificacionTransaccion | None = None


class TransaccionRead(TransaccionBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)