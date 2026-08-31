from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict

from app.models.usuario import AuthProvider

class UsuarioAdminListResponse(BaseModel):
    id: UUID
    nombre: str | None
    apellido: str | None = None
    email: str | None
    telefono: str | None
    auth_provider: AuthProvider | None = None
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
    total: int = Field(ge=0, description="Total de usuarios encontrados")
    page: int = Field(ge=1, description="Número de página actual")
    limit: int = Field(ge=1, le=100, description="Límite de elementos por página")
    pages: int = Field(ge=0, description="Cantidad total de páginas")
    usuarios: list[UsuarioAdminListResponse]

class CambiarEstadoRequest(BaseModel):
    is_active: bool = Field(description="Nuevo estado activo o suspendido de la cuenta")

class CambiarRolAdminRequest(BaseModel):
    is_admin: bool = Field(description="Indica si el usuario tendrá rol de administrador")

class EliminarCuentaAdminRequest(BaseModel):
    email_confirmacion: str = Field(description="Email del usuario para confirmar la eliminación irreversible")

class ResetearOnboardingRequest(BaseModel):
    confirmar: bool = Field(description="Confirmación requerida para reiniciar la configuración inicial")

class AdminStatsResponse(BaseModel):
    total: int = Field(ge=0, description="Total de usuarios registrados")
    activos: int = Field(ge=0, description="Total de cuentas activas")
    onboarding_completo: int = Field(ge=0, description="Total de usuarios con configuración completa")
    whatsapp_vinculados: int = Field(ge=0, description="Total de usuarios con WhatsApp vinculado")
    nuevos_hoy: int = Field(ge=0, description="Usuarios registrados hoy")
    nuevos_7_dias: int = Field(ge=0, description="Usuarios registrados en los últimos 7 días")
    activos_7_dias: int = Field(ge=0, description="Usuarios activos en los últimos 7 días")
    admins_total: int = Field(ge=0, description="Total de administradores")
    por_proveedor: dict[str, int] = Field(default_factory=dict, description="Desglose por método de autenticación")
