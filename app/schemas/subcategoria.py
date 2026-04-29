from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.subcategoria import EstadoSubcategoria


class SubcategoriaBase(BaseModel):
    categoria_id: UUID
    nombre: str
    es_global: bool = False
    creador_id: UUID | None = None
    estado: EstadoSubcategoria = EstadoSubcategoria.ACTIVA


class SubcategoriaCreate(SubcategoriaBase):
    pass


class SubcategoriaUpdate(BaseModel):
    categoria_id: UUID | None = None
    nombre: str | None = None
    es_global: bool | None = None
    creador_id: UUID | None = None
    estado: EstadoSubcategoria | None = None


class SubcategoriaRead(SubcategoriaBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)