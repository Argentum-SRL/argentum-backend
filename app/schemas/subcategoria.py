from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.subcategoria import EstadoSubcategoria


class SubcategoriaBase(BaseModel):
    categoria_id: UUID
    nombre: str
    orden: int = 0
    estado: EstadoSubcategoria = EstadoSubcategoria.ACTIVA


class SubcategoriaRead(SubcategoriaBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)