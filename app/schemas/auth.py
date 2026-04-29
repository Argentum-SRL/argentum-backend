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


class VerificarCodigoRequest(BaseModel):
    telefono: str
    codigo: str


class RecuperarPasswordRequest(BaseModel):
    email: str


class VerificarRecuperacionRequest(BaseModel):
    email: str
    codigo: str
    nueva_password: str


class RegisterRequest(BaseModel):
    name: str
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

class LoginResponse(BaseModel):
    """
    Respuesta estándar para todos los métodos de login
    (email, Google, teléfono). El frontend debe guardar
    access_token y refresh_token al recibirla.
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    usuario: UsuarioRead


class TokenResponse(BaseModel):
    """Respuesta de /auth/refresh con los nuevos tokens."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    """Respuesta de /auth/me."""
    usuario: UsuarioRead
