from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.categoria import EstadoCategoria, TipoCategoria


class CategoriaBase(BaseModel):
    nombre: str
    tipo: TipoCategoria
    icono: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    estado: EstadoCategoria = EstadoCategoria.ACTIVA


class CategoriaRead(CategoriaBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)