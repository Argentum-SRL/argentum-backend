from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator


class PresupuestoCategoriaBase(BaseModel):
    presupuesto_id: UUID
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None

    @model_validator(mode="after")
    def validate_categoria_or_subcategoria(self) -> "PresupuestoCategoriaBase":
        if self.categoria_id is None and self.subcategoria_id is None:
            raise ValueError("Debe informar categoria_id o subcategoria_id")
        return self


class PresupuestoCategoriaCreate(PresupuestoCategoriaBase):
    pass


class PresupuestoCategoriaUpdate(BaseModel):
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None

    @model_validator(mode="after")
    def validate_categoria_or_subcategoria(self) -> "PresupuestoCategoriaUpdate":
        if self.categoria_id is None and self.subcategoria_id is None:
            raise ValueError("Debe informar categoria_id o subcategoria_id")
        return self


class PresupuestoCategoriaRead(PresupuestoCategoriaBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)