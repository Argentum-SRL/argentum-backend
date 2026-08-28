from __future__ import annotations

from datetime import datetime, date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from typing_extensions import Self

from app.models.usuario import AuthProvider, CicloTipo, CicloRegla, EstadoUsuario, Moneda, RolUsuario, Sexo, CicloAjusteDireccion



class UsuarioBase(BaseModel):
    nombre: str | None = None
    apellido: str | None = None
    email: str | None = None
    telefono: str | None = None
    telefono_normalizado: str | None = None
    foto_url: str | None = None
    password_configurada: bool = False
    auth_provider: AuthProvider
    rol: RolUsuario = RolUsuario.USUARIO
    estado: EstadoUsuario = EstadoUsuario.ACTIVO
    moneda_principal: Moneda | None = None
    moneda_secundaria_activa: bool = False
    tipo_dolar: str = "blue"
    ciclo_tipo: CicloTipo | None = None
    ciclo_valor: str | None = None
    ciclo_ajuste_direccion: CicloAjusteDireccion | None = None
    onboarding_completo: bool = False
    email_verificado: bool = False
    telefono_verificado: bool = False
    ultimo_acceso: datetime | None = None
    fecha_nacimiento: date | None = None
    sexo: str | None = None


class UsuarioCreate(UsuarioBase):
    password_hash: str | None = None
    fecha_nacimiento: date | None = None
    sexo: str | None = None


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
    ciclo_ajuste_direccion: CicloAjusteDireccion | None = None
    onboarding_completo: bool | None = None
    email_verificado: bool | None = None
    telefono_verificado: bool | None = None
    ultimo_acceso: datetime | None = None
    fecha_nacimiento: date | None = None
    sexo: str | None = None


class UsuarioRead(UsuarioBase):
    id: UUID
    fecha_registro: datetime

    model_config = ConfigDict(from_attributes=True)


class EditarDatosPersonales(BaseModel):
    nombre: str
    apellido: str
    fecha_nacimiento: date | None = None
    sexo: Sexo | None = None


class EditarEmail(BaseModel):
    email_nuevo: str
    password_actual: str


class EditarPassword(BaseModel):
    password_actual: str | None = None
    password_nueva: str
    password_nueva_confirmacion: str


class EditarTelefono(BaseModel):
    telefono_nuevo: str
    password_actual: str | None = None


class EditarCicloFinanciero(BaseModel):
    ciclo_tipo: CicloTipo
    ciclo_valor: str
    ciclo_ajuste_direccion: CicloAjusteDireccion | None = None

    @model_validator(mode="after")
    def validar_ciclo_polimorfico(self) -> Self:
        if self.ciclo_tipo == CicloTipo.DIA_FIJO:
            try:
                dia = int(self.ciclo_valor)
                if not (1 <= dia <= 31):
                    raise ValueError("El día fijo debe ser un número entero entre 1 y 31.")
            except ValueError:
                raise ValueError("El día fijo debe ser un número entero entre 1 y 31.")
        elif self.ciclo_tipo == CicloTipo.REGLA:
            reglas_validas = {e.value for e in CicloRegla}
            if self.ciclo_valor not in reglas_validas:
                raise ValueError(f"Regla de ciclo no válida. Opciones válidas: {', '.join(sorted(reglas_validas))}")
        return self



class EditarMoneda(BaseModel):
    moneda_principal: Moneda
    moneda_secundaria_activa: bool
    tipo_dolar: str | None = None


class MetodosLoginResponse(BaseModel):
    email_password: bool
    telefono: bool
    google: bool
    puede_agregar_password: bool
    puede_agregar_email: bool
    puede_agregar_telefono: bool


class UsuarioResponse(UsuarioRead):
    fecha_nacimiento: date | None = None
    sexo: str | None = None
