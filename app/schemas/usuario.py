from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario


class UsuarioBase(BaseModel):
    nombre: str | None = None
    apellido: str | None = None
    email: str | None = None
    telefono: str | None = None
    foto_url: str | None = None
    auth_provider: AuthProvider
    rol: RolUsuario = RolUsuario.USUARIO
    estado: EstadoUsuario = EstadoUsuario.ACTIVO
    moneda_principal: Moneda | None = None
    moneda_secundaria_activa: bool = False
    tipo_dolar: str = "blue"
    ciclo_tipo: CicloTipo | None = None
    ciclo_valor: str | None = None
    onboarding_completo: bool = False
    email_verificado: bool = False
    telefono_verificado: bool = False
    ultimo_acceso: datetime | None = None


class UsuarioCreate(UsuarioBase):
    password_hash: str | None = None


class UsuarioUpdate(BaseModel):
    nombre: str | None = None
    apellido: str | None = None
    email: str | None = None
    telefono: str | None = None
    foto_url: str | None = None
    password_hash: str | None = None
    auth_provider: AuthProvider | None = None
    rol: RolUsuario | None = None
    estado: EstadoUsuario | None = None
    moneda_principal: Moneda | None = None
    moneda_secundaria_activa: bool | None = None
    tipo_dolar: str | None = None
    ciclo_tipo: CicloTipo | None = None
    ciclo_valor: str | None = None
    onboarding_completo: bool | None = None
    email_verificado: bool | None = None
    telefono_verificado: bool | None = None
    ultimo_acceso: datetime | None = None


class UsuarioRead(UsuarioBase):
    id: UUID
    fecha_registro: datetime

    model_config = ConfigDict(from_attributes=True)
