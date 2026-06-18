from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict

class UsuarioAdminListResponse(BaseModel):
    id: UUID
    nombre: str | None
    apellido: str | None = None
    email: str | None
    telefono: str | None
    is_active: bool
    is_admin: bool
    onboarding_completado: bool
    whatsapp_vinculado: bool
    created_at: datetime = Field(validation_alias="fecha_registro")
    ultima_actividad: datetime | None = Field(validation_alias="ultimo_acceso")
    foto_url: str | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class UsuarioAdminResponse(UsuarioAdminListResponse):
    paso_onboarding_actual: str | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class PaginatedUsuariosResponse(BaseModel):
    total: int
    page: int
    limit: int
    pages: int
    usuarios: list[UsuarioAdminListResponse]

class CambiarEstadoRequest(BaseModel):
    is_active: bool

class ResetearOnboardingRequest(BaseModel):
    confirmar: bool
