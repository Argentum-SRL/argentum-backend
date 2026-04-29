from __future__ import annotations

from pydantic import BaseModel, field_validator
from app.schemas.usuario import UsuarioRead


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class GoogleLoginRequest(BaseModel):
    token: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class EnviarCodigoRequest(BaseModel):
    telefono: str


class VerificarCodigoTelefonoRequest(BaseModel):
    telefono: str
    codigo: str


class EnviarCodigoEmailRequest(BaseModel):
    email: str


class VerificarCodigoEmailRequest(BaseModel):
    email: str
    codigo: str


class CompletarPerfilRequest(BaseModel):
    nombre: str
    apellido: str
    email: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        import re
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")
        if not re.search(r"[A-Z]", v):
            raise ValueError("La contraseña debe incluir al menos una letra mayúscula.")
        if not re.search(r"[a-z]", v):
            raise ValueError("La contraseña debe incluir al menos una letra minúscula.")
        if not re.search(r"[0-9]", v):
            raise ValueError("La contraseña debe incluir al menos un número.")
        return v


class RecuperarPasswordRequest(BaseModel):
    email: str


class VerificarRecuperacionRequest(BaseModel):
    email: str
    codigo: str
    nueva_password: str


class RegisterRequest(BaseModel):
    nombre: str
    apellido: str
    email: str
    telefono: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        import re
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")
        if not re.search(r"[A-Z]", v):
            raise ValueError("La contraseña debe incluir al menos una letra mayúscula.")
        if not re.search(r"[a-z]", v):
            raise ValueError("La contraseña debe incluir al menos una letra minúscula.")
        if not re.search(r"[0-9]", v):
            raise ValueError("La contraseña debe incluir al menos un número.")
        return v


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class AuthResponse(BaseModel):
    """
    Respuesta estándar para todos los endpoints de autenticación.
    Los tokens son null cuando el usuario aún no completó la verificación.
    El frontend debe leer los flags y redirigir según corresponda.
    """
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    usuario: UsuarioRead | None = None
    requiere_telefono: bool = False
    requiere_datos: bool = False
    requiere_verificacion_email: bool = False
    requiere_verificacion_telefono: bool = False
    requiere_onboarding: bool = False


class TokenResponse(BaseModel):
    """Respuesta de /auth/refresh con los nuevos tokens."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    """Respuesta de /auth/me."""
    usuario: UsuarioRead
