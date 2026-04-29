from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator


class CategoriaExcluidaBase(BaseModel):
    usuario_id: UUID
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None

    @model_validator(mode="after")
    def validate_categoria_or_subcategoria(self) -> "CategoriaExcluidaBase":
        if self.categoria_id is None and self.subcategoria_id is None:
            raise ValueError("Debe informar categoria_id o subcategoria_id")
        return self


class CategoriaExcluidaCreate(CategoriaExcluidaBase):
    pass


class CategoriaExcluidaUpdate(BaseModel):
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None

    @model_validator(mode="after")
    def validate_categoria_or_subcategoria(self) -> "CategoriaExcluidaUpdate":
        if self.categoria_id is None and self.subcategoria_id is None:
            raise ValueError("Debe informar categoria_id o subcategoria_id")
        return self


class CategoriaExcluidaRead(CategoriaExcluidaBase):
    id: UUID
    fecha_exclusion: datetime

    model_config = ConfigDict(from_attributes=True)