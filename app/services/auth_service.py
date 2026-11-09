import logging
import time
import httpx
from fastapi import HTTPException, status
import jwt
from app.core.config import settings
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from datetime import datetime, timezone
import hashlib
from app.models.usuario import Usuario
from app.models.refresh_token import RefreshToken
from app.core.security import get_password_hash


logger = logging.getLogger(__name__)

TEXTO_GOOGLE_INVALIDO = "No pudimos validar tu cuenta de Google. Probá de nuevo."
EMISORES_GOOGLE = ("accounts.google.com", "https://accounts.google.com")
_REFETCH_MIN_SEGUNDOS = 300

# Global variables for caching Google's JWKS
_GOOGLE_JWKS = None
_JWKS_LAST_FETCH = 0
_CACHE_TTL = 3600  # Cache keys for 1 hour

def _mask(value: str | None, visible: int = 12) -> str:
    if not value:
        return ''
    if len(value) <= visible:
        return value
    return f"{value[:visible]}..."


def _get_google_jwks(forzar: bool = False) -> dict:
    global _GOOGLE_JWKS, _JWKS_LAST_FETCH
    now = time.time()
    
    debe_bajar = False
    if not _GOOGLE_JWKS or (now - _JWKS_LAST_FETCH) > _CACHE_TTL:
        debe_bajar = True
    elif forzar and (now - _JWKS_LAST_FETCH) > _REFETCH_MIN_SEGUNDOS:
        debe_bajar = True

    if debe_bajar:
        url = "https://www.googleapis.com/oauth2/v3/certs"
        try:
            logger.debug("[Auth][Google][Backend] Fetching Google public JWKs...")
            response = httpx.get(url, timeout=5)
            if response.status_code == 200:
                _GOOGLE_JWKS = response.json()
                _JWKS_LAST_FETCH = now
                logger.debug("[Auth][Google][Backend] Google JWKs cached successfully.")
            else:
                logger.warning("[Auth][Google][Backend] Failed to fetch JWKs, status: %s", response.status_code)
                if not _GOOGLE_JWKS:
                    raise HTTPException(status_code=400, detail=TEXTO_GOOGLE_INVALIDO)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("[Auth][Google][Backend] Error fetching Google certs")
            if not _GOOGLE_JWKS:
                raise HTTPException(status_code=400, detail=TEXTO_GOOGLE_INVALIDO) from exc
                
    return _GOOGLE_JWKS


def verify_google_token(token: str) -> dict:
    """
    Valida un ID token de Google localmente usando firmas criptográficas (offline)
    y el conjunto de claves públicas JWKS de Google.
    """
    logger.debug(
        '[Auth][Google][Backend] Verificando token localmente tokenPresent=%s tokenLength=%s tokenPrefix=%s googleClientIdPresent=%s googleClientIdPrefix=%s',
        bool(token),
        len(token),
        _mask(token),
        bool(settings.GOOGLE_CLIENT_ID),
        _mask(settings.GOOGLE_CLIENT_ID),
    )
    
    try:
        # 1. kid = jwt.get_unverified_header(token).get("kid"); sin kid → falla.
        unverified_headers = jwt.get_unverified_header(token)
        kid = unverified_headers.get("kid")
        if not kid:
            raise ValueError("Token does not contain 'kid' in header")
            
        # 2. jwks = _get_google_jwks(); clave = la de ese kid; si no está: jwks = _get_google_jwks(forzar=True) y se busca otra vez; si tampoco está → falla.
        jwks = _get_google_jwks()
        clave = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not clave:
            jwks = _get_google_jwks(forzar=True)
            clave = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
            if not clave:
                raise ValueError(f"No matching public key found in Google JWKs for kid: {kid}")
            
        # 3. token_data = jwt.decode(token, jwt.PyJWK(clave).key, algorithms=["RS256"], audience=settings.GOOGLE_CLIENT_ID, issuer=list(EMISORES_GOOGLE), options={"require": ["exp", "iat", "iss", "aud", "sub"]}). Devuelve token_data.
        token_data = jwt.decode(
            token,
            jwt.PyJWK(clave).key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
            issuer=list(EMISORES_GOOGLE),
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        return token_data
        
    except Exception as e:
        logger.warning("[Auth][Google][Backend] Falló la validación del token de Google: %s", type(e).__name__)
        raise HTTPException(status_code=400, detail=TEXTO_GOOGLE_INVALIDO) from e


def validar_reset_token(db: Session, token_raw: str) -> str:
    """
    Verifica si el token de restablecimiento es válido.
    Retorna el nombre del usuario si es válido, de lo contrario lanza TOKEN_INVALIDO.
    """
    if not token_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "TOKEN_INVALIDO",
                    "message": "El enlace para cambiar tu contraseña expiró. Podés pedir uno nuevo desde el inicio de sesión.",
                },
            },
        )
    
    token_hash = hashlib.sha256(token_raw.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    
    usuario = db.execute(
        select(Usuario).where(
            Usuario.reset_token_hash == token_hash,
            Usuario.reset_token_expira_at > now
        )
    ).scalar_one_or_none()
    
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "TOKEN_INVALIDO",
                    "message": "El enlace para cambiar tu contraseña expiró. Podés pedir uno nuevo desde el inicio de sesión.",
                },
            },
        )
    
    return usuario.nombre or "Usuario"


def confirmar_reset_password(db: Session, token_raw: str, nueva_password: str) -> Usuario:
    """
    Valida el token de restablecimiento, actualiza la contraseña del usuario,
    invalida el token y revoca todas sus sesiones activas (incluyendo RefreshTokens).
    """
    if not token_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "TOKEN_INVALIDO",
                    "message": "El enlace para cambiar tu contraseña expiró. Podés pedir uno nuevo desde el inicio de sesión.",
                },
            },
        )
    
    token_hash = hashlib.sha256(token_raw.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    
    usuario = db.execute(
        select(Usuario).where(
            Usuario.reset_token_hash == token_hash,
            Usuario.reset_token_expira_at > now
        )
    ).scalar_one_or_none()
    
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "TOKEN_INVALIDO",
                    "message": "El enlace para cambiar tu contraseña expiró. Podés pedir uno nuevo desde el inicio de sesión.",
                },
            },
        )
        
    # Validar nueva_password: mínimo 8 chars, mayúscula, minúscula, número
    import re
    if len(nueva_password) < 8 or not re.search(r"[A-Z]", nueva_password) or not re.search(r"[a-z]", nueva_password) or not re.search(r"[0-9]", nueva_password):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "success": False,
                "error": {
                    "code": "PASSWORD_INVALIDA",
                    "message": "La contraseña debe tener al menos 8 caracteres, incluir una mayúscula, una minúscula y un número.",
                },
            },
        )
        
    # Cambiar contraseña, invalidar token y revocar todas las sesiones en la misma transacción
    try:
        usuario.password_hash = get_password_hash(nueva_password)
        usuario.password_configurada = True
        usuario.reset_token_hash = None
        usuario.reset_token_expira_at = None
        usuario.tokens_revocados_at = now
        usuario.intentos_fallidos_login = 0
        usuario.ultimo_intento_fallido_at = None
        
        # Marcar todos los RefreshToken del usuario como revocados
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.usuario_id == usuario.id)
            .values(revocado=True)
        )
        
        db.commit()
        return usuario
    except Exception:
        db.rollback()
        logger.exception("Error al confirmar el reset de contraseña del usuario %s", usuario.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Ocurrió un error al guardar la nueva contraseña. Intentá de nuevo.",
                },
            },
        )

