"""
app/services/turnstile_service.py — Verificación de Cloudflare Turnstile token.
"""
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verificar_turnstile_token(token: str | None, remote_ip: str | None = None) -> bool:
    """
    Verifica un token de Cloudflare Turnstile contra su API oficial.
    Nunca lanza excepción: si hay error de red, timeout o respuesta inesperada,
    retorna False (trata el token como inválido).
    """
    if not token or not token.strip():
        logger.warning("Turnstile token ausente o vacio")
        return False

    secret_key = getattr(settings, "TURNSTILE_SECRET_KEY", "") or ""
    if not secret_key:
        logger.error("TURNSTILE_SECRET_KEY no esta configurada")
        return False

    try:
        data = {
            "secret": secret_key,
            "response": token.strip(),
        }
        if remote_ip:
            data["remoteip"] = remote_ip

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(TURNSTILE_VERIFY_URL, data=data)
            if resp.status_code != 200:
                logger.warning(
                    "Cloudflare Turnstile respondio con status %s: %s",
                    resp.status_code,
                    resp.text,
                )
                return False

            resultado = resp.json()
            es_valido = bool(resultado.get("success", False))
            if not es_valido:
                logger.warning(
                    "Cloudflare Turnstile rechazo el token: %s",
                    resultado.get("error-codes"),
                )
            return es_valido

    except Exception as e:
        logger.warning("Error de red o excepcion al verificar Turnstile token: %s", e)
        return False
