import httpx
from fastapi import HTTPException
from app.core.config import settings


def _mask(value: str | None, visible: int = 12) -> str:
    if not value:
        return ''
    if len(value) <= visible:
        return value
    return f"{value[:visible]}..."


def verify_google_token(token: str) -> dict:
    """
    Valida un ID token de Google contra la API pública de Google.
    Devuelve el payload del token si es válido.
    Lanza HTTPException 400 si el token es inválido o la audiencia no coincide.
    """
    print('[Auth][Google][Backend] Verificando token', {
        'tokenPresent': bool(token),
        'tokenLength': len(token),
        'tokenPrefix': _mask(token),
        'googleClientIdPresent': bool(settings.GOOGLE_CLIENT_ID),
        'googleClientIdPrefix': _mask(settings.GOOGLE_CLIENT_ID),
    })

    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
    try:
        response = httpx.get(url, timeout=10)
    except Exception as exc:
        print('[Auth][Google][Backend] Error llamando a Google tokeninfo', repr(exc))
        raise HTTPException(status_code=400, detail='No se pudo validar el token de Google') from exc

    print('[Auth][Google][Backend] Respuesta tokeninfo', {
        'status_code': response.status_code,
        'bodyPrefix': response.text[:500],
    })

    if response.status_code != 200:
        print('[Auth][Google][Backend] Error de Google API:', response.text)
        raise HTTPException(status_code=400, detail="Token de Google inválido")

    token_data = response.json()

    print('[Auth][Google][Backend] Payload tokeninfo', {
        'email': token_data.get('email'),
        'email_verified': token_data.get('email_verified'),
        'aud': token_data.get('aud'),
        'iss': token_data.get('iss'),
        'subPrefix': _mask(token_data.get('sub')),
    })

    if "aud" not in token_data or token_data["aud"] != settings.GOOGLE_CLIENT_ID:
        print('[Auth][Google][Backend] Error de aud:', token_data.get('aud'), 'Esperado:', settings.GOOGLE_CLIENT_ID)
        raise HTTPException(status_code=400, detail="Audiencia del token no coincide")

    return token_data
