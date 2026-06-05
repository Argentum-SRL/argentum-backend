import time
import httpx
from fastapi import HTTPException
from jose import jwt
from app.core.config import settings

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
            print("[Auth][Google][Backend] Fetching Google public JWKs...")
            response = httpx.get(url, timeout=5)
            if response.status_code == 200:
                _GOOGLE_JWKS = response.json()
                _JWKS_LAST_FETCH = now
                print("[Auth][Google][Backend] Google JWKs cached successfully.")
            else:
                print(f"[Auth][Google][Backend] Failed to fetch JWKs, status: {response.status_code}")
                if not _GOOGLE_JWKS:
                    raise Exception("Could not retrieve Google certs from server")
        except Exception as exc:
            print(f"[Auth][Google][Backend] Error fetching Google certs: {repr(exc)}")
            if not _GOOGLE_JWKS:
                raise HTTPException(status_code=400, detail="No se pudieron obtener las claves de Google") from exc
                
    return _GOOGLE_JWKS


def verify_google_token(token: str) -> dict:
    """
    Valida un ID token de Google localmente usando firmas criptográficas (offline)
    y el conjunto de claves públicas JWKS de Google.
    Cae de vuelta a tokeninfo en caso de cualquier error de descompresión o firma local.
    """
    print('[Auth][Google][Backend] Verificando token localmente', {
        'tokenPresent': bool(token),
        'tokenLength': len(token),
        'tokenPrefix': _mask(token),
        'googleClientIdPresent': bool(settings.GOOGLE_CLIENT_ID),
        'googleClientIdPrefix': _mask(settings.GOOGLE_CLIENT_ID),
    })
    
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
        
        print('[Auth][Google][Backend] Verificación local exitosa para:', token_data.get('email'))
        return token_data
        
    except Exception as e:
        print('[Auth][Google][Backend] Falló verificación local, cayendo en fallback tokeninfo:', repr(e))
        
        # Fallback al endpoint tokeninfo para mayor robustez
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
        try:
            response = httpx.get(url, timeout=10)
            if response.status_code != 200:
                print('[Auth][Google][Backend] Fallback tokeninfo falló:', response.text)
                raise HTTPException(status_code=400, detail="Token de Google inválido")
            
            token_data = response.json()
            if "aud" not in token_data or token_data["aud"] != settings.GOOGLE_CLIENT_ID:
                raise HTTPException(status_code=400, detail="Audiencia del token no coincide")
                
            print('[Auth][Google][Backend] Fallback tokeninfo exitoso para:', token_data.get('email'))
            return token_data
        except Exception as exc:
            raise HTTPException(status_code=400, detail="No se pudo validar el token de Google") from exc
