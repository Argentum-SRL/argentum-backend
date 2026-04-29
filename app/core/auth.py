"""
app/core/auth.py — Lógica central de autenticación JWT para Argentum.

ESTRATEGIA DE SESIÓN:
  - Access token:  JWT firmado, duración 15 minutos.
  - Refresh token: UUID aleatorio hasheado con bcrypt, guardado en BD, duración 365 días.
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


# ---------------------------------------------------------------------------
# Access token (JWT firmado)
# ---------------------------------------------------------------------------

def crear_access_token(usuario_id: UUID | str) -> str:
    """Crea un JWT de corta duración (15 minutos) con el ID del usuario como 'sub'."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(usuario_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verificar_access_token(token: str) -> str:
    """
    Decodifica y valida el JWT.
    Devuelve el usuario_id (str) si es válido.
    Lanza HTTPException 401 si está expirado o es inválido.
    """
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
# Refresh token (UUID aleatorio, hasheado en BD)
# ---------------------------------------------------------------------------

def crear_refresh_token(usuario_id: UUID | str, db: Session, device_info: str | None = None) -> str:
    """
    Genera un refresh token aleatorio seguro, lo guarda hasheado en la BD
    con expiración de 365 días y devuelve el token en texto plano
    (solo este momento se conoce el valor; luego solo el hash está en BD).
    """
    token_plain = secrets.token_urlsafe(64)
    token_hash = pwd_context.hash(token_plain)

    expiracion = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    db_token = RefreshToken(
        usuario_id=usuario_id if isinstance(usuario_id, UUID) else UUID(str(usuario_id)),
        token=token_hash,
        fecha_expiracion=expiracion,
        device_info=device_info,
    )
    db.add(db_token)
    db.commit()

    return token_plain


def _buscar_refresh_token(token_plain: str, db: Session) -> RefreshToken:
    """
    Busca un refresh token válido en la BD comparando el hash bcrypt.
    Lanza 401 si no existe, está revocado o expirado.
    """
    # Bcrypt no permite búsqueda directa; hay que recuperar candidatos recientes
    # y comparar hash. Para mantener la BD indexada usamos una ventana temporal amplia.
    ahora = datetime.now(timezone.utc)

    candidatos = db.execute(
        select(RefreshToken).where(
            RefreshToken.revocado == False,
            RefreshToken.fecha_expiracion > ahora,
        )
    ).scalars().all()

    for rt in candidatos:
        if pwd_context.verify(token_plain, rt.token):
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
    """
    Rotation strategy:
      1. Verifica que el refresh token exista, no esté revocado y no haya expirado.
      2. Revoca el token actual.
      3. Genera un nuevo access token y un nuevo refresh token.
      4. Devuelve ambos tokens nuevos.

    Devuelve: {"access_token": str, "refresh_token": str, "token_type": "bearer"}
    """
    rt = _buscar_refresh_token(refresh_token_plain, db)

    # Revocar el token usado (rotation)
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
    """Marca el refresh token como revocado. Silencioso si no existe."""
    ahora = datetime.now(timezone.utc)
    candidatos = db.execute(
        select(RefreshToken).where(
            RefreshToken.revocado == False,
            RefreshToken.fecha_expiracion > ahora,
        )
    ).scalars().all()

    for rt in candidatos:
        if pwd_context.verify(token_plain, rt.token):
            rt.revocado = True
            db.commit()
            return


def revocar_todos_los_tokens(usuario_id: UUID | str, db: Session) -> int:
    """
    Revoca TODOS los refresh tokens activos de un usuario.
    Devuelve la cantidad de tokens revocados.
    """
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
    """
    Elimina físicamente los refresh tokens expirados o revocados.
    Diseñado para ser llamado por APScheduler periódicamente.
    Devuelve la cantidad de registros eliminados.
    """
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
    """
    Dependency que extrae y valida el access token JWT del header
    Authorization: Bearer <token>.

    Si el token expiró devuelve 401 con mensaje claro para que el frontend
    sepa que debe intentar renovar con /auth/refresh.
    """
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
    """
    Dependency que además de autenticar, verifica que el usuario sea admin.
    """
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
    """
    Dependency opcional: devuelve el usuario si hay un Bearer token válido,
    o None si no hay token o el token es inválido. No lanza excepciones.
    Útil en endpoints que se comportan diferente según si el usuario está autenticado.
    """
    from jose import JWTError

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
