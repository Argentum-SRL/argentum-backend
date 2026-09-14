"""
app/services/openai_client.py — Cliente de OpenAI compartido para Argentum.
"""
from openai import OpenAI
from app.core.config import settings

_openai_client: OpenAI | None = None


def get_openai_client() -> OpenAI:
    """
    Retorna el cliente compartido de OpenAI inicializado con la API key de configuración.
    
    Retorna:
        OpenAI: El cliente de OpenAI.
    """
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=20.0,
            max_retries=0,
        )
    return _openai_client
