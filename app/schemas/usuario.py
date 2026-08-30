from __future__ import annotations

from datetime import datetime, date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
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
    nombre: str = Field(..., min_length=1, max_length=100)
    apellido: str = Field(..., min_length=1, max_length=100)
    fecha_nacimiento: date | None = None
    sexo: Sexo | None = None

    @model_validator(mode="after")
    def validar_datos_personales(self) -> Self:
        if not self.nombre.strip():
            raise ValueError("El nombre no puede estar vacío.")
        if not self.apellido.strip():
            raise ValueError("El apellido no puede estar vacío.")
        if self.fecha_nacimiento is not None:
            from app.utils.fecha import hoy_argentina
            hoy = hoy_argentina()
            if self.fecha_nacimiento > hoy:
                raise ValueError("La fecha de nacimiento no puede ser futura.")
            edad = hoy.year - self.fecha_nacimiento.year - ((hoy.month, hoy.day) < (self.fecha_nacimiento.month, self.fecha_nacimiento.day))
            if edad < 18:
                raise ValueError("Tenés que ser mayor de 18 años para usar Argentum.")
            if edad > 120:
                raise ValueError("La fecha de nacimiento no es válida.")
        return self


class EditarEmail(BaseModel):
    email_nuevo: str = Field(..., min_length=5, max_length=255)
    password_actual: str | None = None

    @model_validator(mode="after")
    def validar_email(self) -> Self:
        email_limpio = self.email_nuevo.strip().lower()
        if not email_limpio or "@" not in email_limpio or "." not in email_limpio.split("@")[-1]:
            raise ValueError("Ingresá un formato de email válido.")
        self.email_nuevo = email_limpio
        return self


class EditarPassword(BaseModel):
    password_actual: str | None = None
    password_nueva: str = Field(..., min_length=8, max_length=128)
    password_nueva_confirmacion: str = Field(..., min_length=8, max_length=128)

    @model_validator(mode="after")
    def validar_passwords(self) -> Self:
        if self.password_nueva != self.password_nueva_confirmacion:
            raise ValueError("Las contraseñas no coinciden.")
        pw = self.password_nueva
        if not any(c.isupper() for c in pw) or not any(c.islower() for c in pw) or not any(c.isdigit() for c in pw):
            raise ValueError("La contraseña debe tener al menos 8 caracteres, una mayúscula, una minúscula y un número.")
        return self


class EditarTelefono(BaseModel):
    telefono_nuevo: str = Field(..., min_length=8, max_length=30)
    password_actual: str | None = None

    @model_validator(mode="after")
    def validar_telefono(self) -> Self:
        tel = self.telefono_nuevo.strip()
        if not tel:
            raise ValueError("El número de teléfono es obligatorio.")
        self.telefono_nuevo = tel
        return self


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

    @model_validator(mode="after")
    def validar_moneda(self) -> Self:
        if self.moneda_principal == Moneda.USD or self.moneda_secundaria_activa:
            if not self.tipo_dolar:
                raise ValueError("El tipo de dólar de referencia es obligatorio.")
            tipo = self.tipo_dolar.lower().strip()
            if tipo == "bolsa":
                tipo = "mep"
            valid_dolares = {"oficial", "blue", "tarjeta", "mep"}
            if tipo not in valid_dolares:
                raise ValueError("El tipo de dólar seleccionado no es válido.")
            self.tipo_dolar = tipo
        else:
            if self.tipo_dolar:
                tipo = self.tipo_dolar.lower().strip()
                if tipo == "bolsa":
                    tipo = "mep"
                self.tipo_dolar = tipo
        return self


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
