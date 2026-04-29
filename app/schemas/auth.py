from __future__ import annotations

from pydantic import BaseModel
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
