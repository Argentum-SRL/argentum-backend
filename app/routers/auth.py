"""
app/routers/auth.py — Endpoints de autenticación para Argentum.

Todos los métodos de login devuelven el mismo formato LoginResponse:
{
    "access_token": "...",   # JWT, dura 15 min
    "refresh_token": "...",  # UUID hasheado en BD, dura 365 días
    "token_type": "bearer",
    "usuario": { ... }       # objeto UsuarioRead completo
}

FLUJO DE SESIÓN (documentación para el frontend):
  1. Guardar access_token + refresh_token al hacer login.
  2. Incluir Access token en cada request: Authorization: Bearer <access_token>.
  3. Si el server devuelve 401 → POST /auth/refresh con { "refresh_token": "..." }.
  4. Guardar los nuevos tokens devueltos.
  5. Reintentar el request original con el nuevo access_token.
  6. Si /auth/refresh devuelve 401 → redirigir al login (sesión terminada).
"""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import (
    crear_access_token,
    crear_refresh_token,
    get_current_user,
    renovar_tokens,
    revocar_refresh_token,
    revocar_todos_los_tokens,
)
from app.core.database import get_db
from app.core.security import get_password_hash, verify_password
from app.models.usuario import AuthProvider, Usuario
from app.schemas.auth import (
    GoogleLoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)
from app.schemas.usuario import UsuarioRead
from app.services.auth_service import verify_google_token

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _device_info_from_request(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:200] if ua else None


# ---------------------------------------------------------------------------
# Registro / Login con email
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name: str
    apellido: str
    email: str
    telefono: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")
        if not re.search(r"[A-Z]", v):
            raise ValueError("La contraseña debe incluir al menos una letra mayúscula.")
        if not re.search(r"[a-z]", v):
            raise ValueError("La contraseña debe incluir al menos una letra minúscula.")
        if not re.search(r"[0-9]", v):
            raise ValueError("La contraseña debe incluir al menos un número.")
        return v


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(user_in: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Registra un nuevo usuario con email/password y devuelve tokens."""
    if db.execute(select(Usuario).where(Usuario.email == user_in.email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Ese mail ya está registrado en otra cuenta.")

    if db.execute(select(Usuario).where(Usuario.telefono == user_in.telefono)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Ese número de teléfono ya está registrado en otra cuenta.")

    new_user = Usuario(
        nombre=user_in.name,
        apellido=user_in.apellido,
        email=user_in.email,
        telefono=user_in.telefono,
        password_hash=get_password_hash(user_in.password),
        auth_provider=AuthProvider.EMAIL,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    access_token = crear_access_token(new_user.id)
    refresh_token = crear_refresh_token(new_user.id, db, device_info=_device_info_from_request(request))

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        usuario=UsuarioRead.model_validate(new_user),
    )


@router.post("/login", response_model=LoginResponse)
def login(user_in: "LoginEmailRequest", request: Request, db: Session = Depends(get_db)):
    """Login con email y password."""
    user = db.execute(select(Usuario).where(Usuario.email == user_in.email)).scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(user_in.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    access_token = crear_access_token(user.id)
    refresh_token = crear_refresh_token(user.id, db, device_info=_device_info_from_request(request))

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        usuario=UsuarioRead.model_validate(user),
    )


class LoginEmailRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Login con Google
# ---------------------------------------------------------------------------

@router.post("/google", response_model=LoginResponse)
def login_google(request_body: GoogleLoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login / registro con Google OAuth2 ID token."""
    token_info = verify_google_token(request_body.token)
    email = token_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="El token de Google no contiene un email válido")

    user = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()

    if not user:
        nombre = token_info.get("given_name", "")
        apellido = token_info.get("family_name", "")
        if not nombre and "name" in token_info:
            parts = token_info["name"].split(" ", 1)
            nombre = parts[0]
            apellido = parts[1] if len(parts) > 1 else ""

        foto_url = token_info.get("picture")
        # Teléfono dummy requerido por la BD (max 20 chars)
        telefono_dummy = "g_" + str(uuid.uuid4())[:18]

        user = Usuario(
            nombre=nombre,
            apellido=apellido,
            email=email,
            telefono=telefono_dummy,
            foto_url=foto_url,
            auth_provider=AuthProvider.GOOGLE,
            onboarding_completo=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = crear_access_token(user.id)
    refresh_token = crear_refresh_token(user.id, db, device_info=_device_info_from_request(request))

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        usuario=UsuarioRead.model_validate(user),
    )


# ---------------------------------------------------------------------------
# Refresh / Logout / Me
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    """
    Renueva los tokens usando un refresh token válido.
    Implementa rotation: el token usado se revoca y se emite uno nuevo.
    Si el refresh token es inválido, expirado o revocado devuelve 401.
    """
    nuevos = renovar_tokens(
        body.refresh_token,
        db,
        device_info=_device_info_from_request(request),
    )
    return TokenResponse(**nuevos)


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(
    body: LogoutRequest,
    db: Session = Depends(get_db),
):
    """
    Cierra la sesión del dispositivo actual revocando el refresh token.
    No requiere access token: el refresh token es suficiente para identificar la sesión.
    Esto permite hacer logout incluso si el access token ya expiró.
    """
    revocar_refresh_token(body.refresh_token, db)
    return {"detail": "Sesión cerrada correctamente"}


@router.post("/logout-all", status_code=status.HTTP_200_OK)
def logout_all(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Cierra sesión en TODOS los dispositivos revocando todos los refresh tokens
    activos del usuario. Útil ante sospecha de acceso no autorizado.
    Requiere access token válido.
    """
    count = revocar_todos_los_tokens(current_user.id, db)
    return {"detail": f"Sesión cerrada en {count} dispositivo(s)"}


@router.get("/me", response_model=MeResponse)
def me(current_user: Usuario = Depends(get_current_user)):
    """Devuelve el perfil del usuario autenticado actual."""
    return MeResponse(usuario=UsuarioRead.model_validate(current_user))
