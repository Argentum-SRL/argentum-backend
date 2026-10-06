"""
app/schemas/memoria_comercio.py — Schemas Pydantic para memoria de comercios.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tipos import DecimalJSON


class MemoriaSugerenciaResponse(BaseModel):
    clave: Optional[str] = None
    memoria_id: Optional[UUID] = None
    categoria_id: Optional[UUID] = None
    subcategoria_id: Optional[UUID] = None


class MemoriaComercioCreate(BaseModel):
    descripcion: str = Field(min_length=1, max_length=300)
    tipo: str = Field(pattern="^(egreso|ingreso)$")
    categoria_id: UUID
    subcategoria_id: Optional[UUID] = None


class TransaccionAnteriorItem(BaseModel):
    id: UUID
    fecha: date
    monto: DecimalJSON
    moneda: str
    descripcion: Optional[str] = None
    categoria_nombre: str
    subcategoria_nombre: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class MemoriaComercioGuardarResponse(BaseModel):
    memoria_id: UUID
    clave: str
    categoria_id: UUID
    subcategoria_id: Optional[UUID] = None
    cantidad_anteriores: int
    anteriores: List[TransaccionAnteriorItem]


class MemoriaAplicarRequest(BaseModel):
    memoria_id: UUID
    transaccion_ids: List[UUID]


class MemoriaAplicarResponse(BaseModel):
    actualizadas: int
    omitidas: int


class MemoriaComercioRead(BaseModel):
    id: UUID
    usuario_id: UUID
    clave: str
    tipo: str
    categoria_id: UUID
    categoria_nombre: str
    subcategoria_id: Optional[UUID] = None
    subcategoria_nombre: Optional[str] = None
    fecha_creacion: datetime
    fecha_actualizacion: datetime

    model_config = ConfigDict(from_attributes=True)
