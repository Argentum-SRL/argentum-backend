import json
import logging
from pathlib import Path
from openai import OpenAI
from app.core.config import settings

logger = logging.getLogger(__name__)

MODULE_MODEL = "gpt-4o-mini"


def llamar_openai(payload: dict) -> dict:
    """
    Realiza la llamada al modelo gpt-4o-mini de OpenAI con el prompt del sistema y el
    payload financiero provisto.
    """
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY no configurada")

    # Determinar ruta dinámica del prompt
    prompt_path = Path(__file__).parent.parent / "prompts" / "sistema_financiero.txt"
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Error al leer el system prompt desde {prompt_path}: {e}")
        raise RuntimeError(f"No se pudo cargar el system prompt del archivo: {e}") from e

    # Inicializar cliente
    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=60.0)

    # Serializar el payload para el mensaje de usuario
    user_message = json.dumps(payload, indent=2, ensure_ascii=False)

    try:
        response = client.chat.completions.create(
            model=MODULE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,
            max_tokens=2000
        )
    except Exception as e:
        # Extraer detalles específicos si los hay
        status_code = getattr(e, "status_code", "N/A")
        message = getattr(e, "message", str(e))
        err_msg = f"Error en llamada a OpenAI API (status_code={status_code}): {message}"
        logger.error(err_msg, exc_info=True)
        raise RuntimeError(err_msg) from e

    choice = response.choices[0]
    content = choice.message.content

    input_tokens = response.usage.prompt_tokens
    output_tokens = response.usage.completion_tokens

    logger.info(
        f"Llamada a OpenAI completada con éxito. Modelo: {MODULE_MODEL}, "
        f"Input Tokens: {input_tokens}, Output Tokens: {output_tokens}"
    )

    return {
        "contenido": content,
        "modelo": MODULE_MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens
    }
