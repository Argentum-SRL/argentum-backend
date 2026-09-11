import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

VENTANA_RATE_LIMIT_SEGUNDOS = 15 * 60  # 15 minutos


@dataclass
class EntradaAlerta:
    ultimo_envio: float
    agrupados: int = 0


_alerta_cache: dict[str, EntradaAlerta] = {}
_alerta_lock = threading.Lock()


def enviar_alerta_admin(asunto: str, cuerpo: str, clave: Optional[str] = None) -> bool:
    """
    Envía un correo de alerta al administrador definido en ADMIN_ALERT_EMAIL.
    Aplica rate limit en memoria: máximo 1 correo por tipo de error cada 15 minutos.
    Si se producen más errores durante el período de silencio, se agrupan y se
    informa la cantidad agrupada en el siguiente correo.
    Esta función nunca lanza excepciones: ante cualquier fallo, lo registra y retorna False.
    """
    try:
        admin_email = os.environ.get("ADMIN_ALERT_EMAIL", "").strip()
        if not admin_email:
            logger.warning(
                "ADMIN_ALERT_EMAIL no está configurada. Alerta no enviada: %s",
                asunto,
            )
            return False

        clave_efectiva = clave or asunto
        ahora = time.time()

        with _alerta_lock:
            entrada = _alerta_cache.get(clave_efectiva)
            if entrada is not None:
                tiempo_transcurrido = ahora - entrada.ultimo_envio
                if tiempo_transcurrido < VENTANA_RATE_LIMIT_SEGUNDOS:
                    entrada.agrupados += 1
                    logger.info(
                        "Alerta silenciada por rate limit (clave: '%s'). Eventos agrupados acumulados: %d",
                        clave_efectiva,
                        entrada.agrupados,
                    )
                    return False
                else:
                    conteo_agrupados = entrada.agrupados
                    entrada.ultimo_envio = ahora
                    entrada.agrupados = 0
            else:
                conteo_agrupados = 0
                _alerta_cache[clave_efectiva] = EntradaAlerta(
                    ultimo_envio=ahora,
                    agrupados=0,
                )

        if conteo_agrupados > 0:
            cuerpo_final = (
                f"{cuerpo}\n\n"
                f"--------------------------------------------------\n"
                f"Alerta agrupada: se produjeron {conteo_agrupados} eventos adicionales "
                f"del mismo tipo durante el período de silencio de 15 minutos."
            )
        else:
            cuerpo_final = cuerpo

        from app.services.email_service import _enviar_email

        exito = _enviar_email(
            destinatario=admin_email,
            asunto=asunto,
            cuerpo=cuerpo_final,
        )
        if not exito:
            logger.error(
                "Fallo al enviar alerta administrativa a %s con asunto: %s",
                admin_email,
                asunto,
            )
        return bool(exito)

    except Exception as e:
        logger.exception("Error inesperado en enviar_alerta_admin: %s", e)
        return False


def _reset_alerta_cache():
    """Función utilitaria interna para tests."""
    with _alerta_lock:
        _alerta_cache.clear()
