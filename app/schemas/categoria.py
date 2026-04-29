from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.categoria import EstadoCategoria, TipoCategoria


class CategoriaBase(BaseModel):
    nombre: str
    tipo: TipoCategoria
    icono: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    es_global: bool = False
    creador_id: UUID | None = None
    estado: EstadoCategoria = EstadoCategoria.ACTIVA


class CategoriaCreate(CategoriaBase):
    pass


class CategoriaUpdate(BaseModel):
    nombre: str | None = None
    tipo: TipoCategoria | None = None
    icono: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    es_global: bool | None = None
    creador_id: UUID | None = None
    estado: EstadoCategoria | None = None


class CategoriaRead(CategoriaBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)