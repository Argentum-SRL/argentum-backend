import logging
import time
import httpx
from fastapi import HTTPException, status
from jose import jwt
from app.core.config import settings
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from datetime import datetime, timezone
import hashlib
from app.models.usuario import Usuario
from app.models.refresh_token import RefreshToken
from app.core.security import get_password_hash


logger = logging.getLogger(__name__)

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


def _get_google_jwks() -> dict:
    global _GOOGLE_JWKS, _JWKS_LAST_FETCH
    now = time.time()
    
    # If not cached or cached older than 1 hour, refetch
    if not _GOOGLE_JWKS or (now - _JWKS_LAST_FETCH) > _CACHE_TTL:
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
                    raise Exception("Could not retrieve Google certs from server")
        except Exception as exc:
            logger.exception("[Auth][Google][Backend] Error fetching Google certs")
            if not _GOOGLE_JWKS:
                raise HTTPException(status_code=400, detail="No se pudieron obtener las claves de Google") from exc
                
    return _GOOGLE_JWKS


def verify_google_token(token: str) -> dict:
    """
    Valida un ID token de Google localmente usando firmas criptográficas (offline)
    y el conjunto de claves públicas JWKS de Google.
    Cae de vuelta a tokeninfo en caso de cualquier error de descompresión o firma local.
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
        # 1. Obtener la cabecera sin verificar para extraer la Key ID ('kid')
        unverified_headers = jwt.get_unverified_header(token)
        kid = unverified_headers.get("kid")
        if not kid:
            raise Exception("Token does not contain 'kid' in header")
            
        # 2. Obtener las claves públicas de Google (con caché en memoria)
        jwks = _get_google_jwks()
        
        # 3. Encontrar la clave correspondiente al 'kid'
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            raise Exception(f"No matching public key found in Google JWKs for kid: {kid}")
            
        # 4. Decodificar y verificar localmente usando jose
        # Esto valida firma, exp, iat, y aud (settings.GOOGLE_CLIENT_ID)
        token_data = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
            issuer=["accounts.google.com", "https://accounts.google.com"]
        )
        
        logger.debug('[Auth][Google][Backend] Verificación local exitosa para: %s', token_data.get('email'))
        return token_data
        
    except Exception as e:
        logger.warning('[Auth][Google][Backend] Falló verificación local, cayendo en fallback tokeninfo: %s', repr(e))
        
        # Fallback al endpoint tokeninfo para mayor robustez
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
        try:
            response = httpx.get(url, timeout=10)
            if response.status_code != 200:
                logger.warning('[Auth][Google][Backend] Fallback tokeninfo falló: %s', response.text)
                raise HTTPException(status_code=400, detail="Token de Google inválido")
            
            token_data = response.json()
            if "aud" not in token_data or token_data["aud"] != settings.GOOGLE_CLIENT_ID:
                raise HTTPException(status_code=400, detail="Audiencia del token no coincide")
                
            logger.debug('[Auth][Google][Backend] Fallback tokeninfo exitoso para: %s', token_data.get('email'))
            return token_data
        except Exception as exc:
            raise HTTPException(status_code=400, detail="No se pudo validar el token de Google") from exc


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
        
        # Marcar todos los RefreshToken del usuario como revocados
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.usuario_id == usuario.id)
            .values(revocado=True)
        )
        
        db.commit()
        return usuario
    except Exception as e:
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

