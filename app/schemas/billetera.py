from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.billetera import EstadoBilletera
from app.models.usuario import Moneda


class BilleteraBase(BaseModel):
    usuario_id: UUID | None = None
    nombre: str
    moneda: Moneda
    saldo_actual: Decimal = Decimal("0")
    saldo_inicial: Decimal = Decimal("0")
    es_principal: bool = False
    es_efectivo: bool = False
    estado: EstadoBilletera = EstadoBilletera.ACTIVA


class BilleteraCreate(BilleteraBase):
    pass


class BilleteraUpdate(BaseModel):
    nombre: str | None = None
    moneda: Moneda | None = None
    saldo_actual: Decimal | None = None
    saldo_inicial: Decimal | None = None
    es_principal: bool | None = None
    es_efectivo: bool | None = None
    estado: EstadoBilletera | None = None


class BilleteraRead(BilleteraBase):
    id: UUID
    fecha_creacion: datetime
    tiene_transacciones: bool = False

    model_config = ConfigDict(from_attributes=True)