from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion


class SuscripcionBase(BaseModel):
    usuario_id: UUID
    nombre: str
    categoria_id: UUID | None = None
    frecuencia: FrecuenciaSuscripcion
    proximo_cobro: date
    estado: EstadoSuscripcion = EstadoSuscripcion.ACTIVA


class SuscripcionCreate(SuscripcionBase):
    pass


class SuscripcionUpdate(BaseModel):
    nombre: str | None = None
    categoria_id: UUID | None = None
    frecuencia: FrecuenciaSuscripcion | None = None
    proximo_cobro: date | None = None
    estado: EstadoSuscripcion | None = None


class SuscripcionRead(SuscripcionBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)