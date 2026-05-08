from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from typing import List, Optional

class HistorialSuscripcionResponse(BaseModel):
    id: UUID
    monto: Decimal
    moneda: str
    vigente_desde: date
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)

class SuscripcionBase(BaseModel):
    nombre: str
    categoria_id: Optional[UUID] = None
    frecuencia: str                        # mensual|bimestral|trimestral|semestral|anual
    proximo_cobro: date
    billetera_id: Optional[UUID] = None
    tarjeta_id: Optional[UUID] = None

class SuscripcionCreate(SuscripcionBase):
    monto: Decimal                         # precio inicial — va a HistorialSuscripcion
    moneda: str = 'ARS'
    vigente_desde: Optional[date] = None      # default: hoy

class SuscripcionUpdate(BaseModel):
    nombre: Optional[str] = None
    categoria_id: Optional[UUID] = None
    frecuencia: Optional[str] = None
    proximo_cobro: Optional[date] = None
    billetera_id: Optional[UUID] = None
    tarjeta_id: Optional[UUID] = None
    estado: Optional[str] = None

class ActualizarPrecioRequest(BaseModel):
    monto: Decimal
    moneda: str
    vigente_desde: date                    # puede ser futura

class SuscripcionResponse(SuscripcionBase):
    id: UUID
    usuario_id: UUID
    estado: str
    fecha_creacion: datetime
    precio_actual: Optional[HistorialSuscripcionResponse] = None
    historial_precios: List[HistorialSuscripcionResponse] = []
    costo_mensual_equivalente: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)