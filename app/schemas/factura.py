from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.usuario import Moneda
from app.schemas.tipos import DecimalJSON


class FacturaRead(BaseModel):
    id: UUID
    descripcion: str
    monto: DecimalJSON
    moneda: Moneda
    fecha_vencimiento: date
    estado: str
    pagada_automaticamente: bool
    transaccion_id: UUID | None = None
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    categoria_nombre: str | None = None
    subcategoria_nombre: str | None = None
    vencida: bool

    model_config = ConfigDict(from_attributes=True)
