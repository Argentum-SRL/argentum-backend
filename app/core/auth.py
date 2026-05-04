"""
app/core/auth.py — Lógica central de autenticación JWT para Argentum.

ESTRATEGIA DE SESIÓN:
    - Access token:  JWT firmado, duración 15 minutos.
    - Refresh token: token opaco con token_id indexable + secreto firmado con HMAC-SHA256.
    - Rotation:      Cada vez que se usa el refresh token se revoca y se genera uno nuevo.
    - Logout:        Único mecanismo para cerrar sesión; revoca el refresh token en BD.

FLUJO ESPERADO EN EL FRONTEND:
    1. Al hacer login, guardar access_token + refresh_token (ej: localStorage / httpOnly cookie).
    2. Incluir el access_token en cada request: Authorization: Bearer <access_token>.
    3. Si el server devuelve 401 → llamar POST /auth/refresh con { "refresh_token": "..." }.
    4. Guardar los nuevos tokens devueltos por /auth/refresh.
    5. Reintentar el request original con el nuevo access_token.
    6. Si /auth/refresh también devuelve 401 → redirigir al login.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.models.refresh_token import RefreshToken
from app.models.usuario import RolUsuario, Usuario

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def _refresh_secret_hash(secret: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _parse_refresh_token(refresh_token_plain: str) -> tuple[str | None, str | None]:
    if "." not in refresh_token_plain:
        return None, None
    token_id, secret = refresh_token_plain.split(".", 1)
    if not token_id or not secret:
        return None, None
    return token_id, secret


def _refresh_token_matches(secret: str, token_hash: str) -> bool:
    return hmac.compare_digest(_refresh_secret_hash(secret), token_hash)


# ---------------------------------------------------------------------------
# Access token (JWT firmado)
# ---------------------------------------------------------------------------

def crear_access_token(usuario_id: UUID | str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(usuario_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verificar_access_token(token: str) -> str:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token de acceso inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        usuario_id: str | None = payload.get("sub")
        token_type: str | None = payload.get("type")
        if usuario_id is None or token_type != "access":
            raise credentials_exc
        return usuario_id
    except JWTError:
        raise credentials_exc


# ---------------------------------------------------------------------------
# Refresh token (token_id indexable + secreto firmado)
# ---------------------------------------------------------------------------

def crear_refresh_token(usuario_id: UUID | str, db: Session, device_info: str | None = None) -> str:
    token_id = secrets.token_urlsafe(16)
    token_secret = secrets.token_urlsafe(48)

    token_plain = f"{token_id}.{token_secret}"
    token_hash = _refresh_secret_hash(token_secret)

    expiracion = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    db_token = RefreshToken(
        usuario_id=usuario_id if isinstance(usuario_id, UUID) else UUID(str(usuario_id)),
        token_id=token_id,
        token_hash=token_hash,
        fecha_expiracion=expiracion,
        device_info=device_info,
    )

    db.add(db_token)
    db.commit()

    return token_plain


def _buscar_refresh_token(token_plain: str, db: Session) -> RefreshToken:
    ahora = datetime.now(timezone.utc)
    token_id, secret = _parse_refresh_token(token_plain)

    if not token_id or not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido",
        )

    rt = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_id == token_id,
            RefreshToken.revocado == False,
            RefreshToken.fecha_expiracion > ahora,
        )
    ).scalar_one_or_none()

    if rt and _refresh_token_matches(secret, rt.token_hash):
        return rt

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token inválido, expirado o ya utilizado",
        headers={"WWW-Authenticate": "Bearer"},
    )


def renovar_tokens(
    refresh_token_plain: str,
    db: Session,
    device_info: str | None = None,
) -> dict:
    rt = _buscar_refresh_token(refresh_token_plain, db)

    rt.revocado = True
    db.flush()

    usuario_id = rt.usuario_id

    nuevo_access = crear_access_token(usuario_id)
    nuevo_refresh = crear_refresh_token(usuario_id, db, device_info=device_info or rt.device_info)

    return {
        "access_token": nuevo_access,
        "refresh_token": nuevo_refresh,
        "token_type": "bearer",
    }


def revocar_refresh_token(token_plain: str, db: Session) -> None:
    ahora = datetime.now(timezone.utc)
    token_id, secret = _parse_refresh_token(token_plain)

    if not token_id or not secret:
        return

    rt = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_id == token_id,
            RefreshToken.revocado == False,
            RefreshToken.fecha_expiracion > ahora,
        )
    ).scalar_one_or_none()

    if rt and _refresh_token_matches(secret, rt.token_hash):
        rt.revocado = True
        db.commit()


def revocar_todos_los_tokens(usuario_id: UUID | str, db: Session) -> int:
    uid = usuario_id if isinstance(usuario_id, UUID) else UUID(str(usuario_id))
    ahora = datetime.now(timezone.utc)

    tokens = db.execute(
        select(RefreshToken).where(
            RefreshToken.usuario_id == uid,
            RefreshToken.revocado == False,
            RefreshToken.fecha_expiracion > ahora,
        )
    ).scalars().all()

    count = len(tokens)
    for rt in tokens:
        rt.revocado = True

    db.commit()
    return count


def limpiar_tokens_expirados(db: Session) -> int:
    ahora = datetime.now(timezone.utc)

    expirados = db.execute(
        select(RefreshToken).where(
            (RefreshToken.fecha_expiracion <= ahora) | (RefreshToken.revocado == True)
        )
    ).scalars().all()

    count = len(expirados)
    for rt in expirados:
        db.delete(rt)

    db.commit()
    return count


# ---------------------------------------------------------------------------
# Dependencies de FastAPI
# ---------------------------------------------------------------------------

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    usuario_id_str = verificar_access_token(token)

    usuario = db.execute(
        select(Usuario).where(Usuario.id == UUID(usuario_id_str))
    ).scalar_one_or_none()

    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return usuario


def get_current_admin(
    current_user: Usuario = Depends(get_current_user),
) -> Usuario:
    if current_user.rol != RolUsuario.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Permisos insuficientes: se requiere rol admin",
        )
    return current_user


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Usuario | None:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        usuario_id_str: str | None = payload.get("sub")
        token_type: str | None = payload.get("type")

        if not usuario_id_str or token_type != "access":
            return None

        usuario = db.execute(
            select(Usuario).where(Usuario.id == UUID(usuario_id_str))
        ).scalar_one_or_none()

        return usuario

    except (JWTError, Exception):
        return None