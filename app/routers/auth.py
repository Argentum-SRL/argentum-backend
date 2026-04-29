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
from datetime import datetime, timezone

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
    EnviarCodigoRequest,
    GoogleLoginRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    RecuperarPasswordRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    VerificarCodigoRequest,
    VerificarRecuperacionRequest,
)
from app.schemas.usuario import UsuarioRead
from app.services.auth_service import verify_google_token
from app.services.email_service import (
    enviar_email_recuperacion,
    generar_codigo_recuperacion,
    guardar_codigo_recuperacion,
    verificar_codigo_recuperacion,
)
from app.services.sms_service import enviar_sms, generar_codigo, guardar_codigo, verificar_codigo

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

# ---------------------------------------------------------------------------
# Registro / Login con email
# ---------------------------------------------------------------------------


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
        estado="activo",
        onboarding_completo=False,
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
def login(user_in: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login con email y password."""
    user = db.execute(select(Usuario).where(Usuario.email == user_in.email)).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    if user.auth_provider != AuthProvider.EMAIL:
        raise HTTPException(status_code=401, detail="Esta cuenta usa otro método de login")

    if not user.password_hash or not verify_password(user_in.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    # Actualizar último acceso
    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()

    access_token = crear_access_token(user.id)
    refresh_token = crear_refresh_token(user.id, db, device_info=_device_info_from_request(request))

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        usuario=UsuarioRead.model_validate(user),
    )


@router.post("/recuperar-password")
def recuperar_password(body: RecuperarPasswordRequest, db: Session = Depends(get_db)):
    """
    Inicia el flujo de recuperación de contraseña enviando un código por email.
    Si el email no existe, no revela información (devuelve 200).
    """
    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()

    if user:
        codigo = generar_codigo_recuperacion()
        guardar_codigo_recuperacion(body.email, codigo)
        enviar_email_recuperacion(body.email, codigo)

    return {"detail": "Si el email existe, se ha enviado un código de recuperación."}


@router.post("/recuperar-password/verificar")
def verificar_recuperacion(body: VerificarRecuperacionRequest, db: Session = Depends(get_db)):
    """
    Verifica el código de recuperación y actualiza la contraseña.
    """
    if not verificar_codigo_recuperacion(body.email, body.codigo):
        raise HTTPException(status_code=400, detail="Código incorrecto o expirado")

    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if not user:
        # Caso improbable si el código fue verificado, pero por seguridad:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Validar password (misma lógica que en el registro, idealmente debería estar centralizada)
    if len(body.nueva_password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")

    user.password_hash = get_password_hash(body.nueva_password)
    db.commit()

    return {"detail": "Contraseña actualizada correctamente"}


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
# Login con teléfono (verificación por SMS)
# ---------------------------------------------------------------------------

@router.post("/telefono/enviar-codigo")
def enviar_codigo_telefono(body: EnviarCodigoRequest):
    """
    Envía un código de verificación de 6 dígitos al número de teléfono dado.
    El código expira en 10 minutos. Si Twilio no está configurado, el código
    se imprime en la consola del servidor (modo desarrollo).
    """
    codigo = generar_codigo()
    guardar_codigo(body.telefono, codigo)

    enviado = enviar_sms(body.telefono, codigo)
    if not enviado:
        raise HTTPException(
            status_code=500,
            detail="No se pudo enviar el SMS. Intentá de nuevo más tarde.",
        )

    return {"detail": "Código de verificación enviado", "telefono": body.telefono}


@router.post("/telefono/verificar", response_model=LoginResponse)
def verificar_codigo_telefono(
    body: VerificarCodigoRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Verifica el código SMS recibido.
    - Si es correcto y el teléfono ya existe → login (devuelve JWT).
    - Si es correcto y el teléfono no existe → crea usuario con
      auth_provider='telefono', onboarding_completo=False, y devuelve JWT.
    - Si el código es incorrecto o expiró → 400.
    """
    if not verificar_codigo(body.telefono, body.codigo):
        raise HTTPException(
            status_code=400,
            detail="Código incorrecto o expirado",
        )

    # Buscar usuario por teléfono
    user = db.execute(
        select(Usuario).where(Usuario.telefono == body.telefono)
    ).scalar_one_or_none()

    if not user:
        # Crear usuario nuevo — nombre y apellido se completan en el onboarding
        user = Usuario(
            nombre="",
            apellido="",
            telefono=body.telefono,
            auth_provider=AuthProvider.TELEFONO,
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
