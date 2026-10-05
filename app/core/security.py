import logging
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import jwt
import re

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def normalizar_email(email: str | None) -> str:
    """Normaliza un correo electrónico aplicando strip y lower."""
    if not email:
        return ""
    return email.strip().lower()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica una contraseña en texto plano contra su hash bcrypt.
    Verifica contra los primeros 72 bytes UTF-8 y devuelve False de forma segura ante errores."""
    if not plain_password or not hashed_password:
        return False
    try:
        password_bytes = plain_password.encode("utf-8")[:72]
        return pwd_context.verify(password_bytes, hashed_password)
    except Exception as e:
        logging.getLogger(__name__).warning("Excepción en verify_password: %s", type(e).__name__)
        return False


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def validar_reglas_password(v: str) -> str:
    if not v:
        raise ValueError("La contraseña no puede estar vacía.")
    if len(v) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    if len(v.encode("utf-8")) > 72:
        raise ValueError("La contraseña no puede superar los 72 bytes.")
    if len(v) > 128:
        raise ValueError("La contraseña no puede superar los 128 caracteres.")
    if not re.search(r"[A-Z]", v):
        raise ValueError("La contraseña debe incluir al menos una letra mayúscula.")
    if not re.search(r"[a-z]", v):
        raise ValueError("La contraseña debe incluir al menos una letra minúscula.")
    if not re.search(r"[0-9]", v):
        raise ValueError("La contraseña debe incluir al menos un número.")
    return v


