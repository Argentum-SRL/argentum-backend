"""
app/services/whatsapp_service.py — Servicio de mensajería y verificación por WhatsApp con Meta Cloud API.
"""

import logging
import random
import re
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_meta_http_client: httpx.Client | None = None


def get_meta_http_client() -> httpx.Client:
    global _meta_http_client
    if _meta_http_client is None:
        _meta_http_client = httpx.Client()
    return _meta_http_client


CODIGO_EXPIRACION_SEGUNDOS = 10 * 60  # 10 minutos
MAX_INTENTOS = 3


def _enmascarar_telefono(telefono: str | None) -> str:
    """Enmascara el número de teléfono mostrando solo los últimos 4 dígitos."""
    if not telefono:
        return "****"
    tel_clean = "".join(c for c in str(telefono) if c.isdigit())
    if len(tel_clean) >= 4:
        return f"***{tel_clean[-4:]}"
    return "****"


def _enmascarar_otp_en_mensaje(mensaje: str | None) -> str:
    """Enmascara códigos numéricos de verificación (ej. OTPs) en el cuerpo del mensaje."""
    if not mensaje:
        return ""
    return re.sub(r"\b\d{4,8}\b", "***", mensaje)


from datetime import datetime, timezone, timedelta
from uuid import uuid4
from sqlalchemy import select, update
from app.core.database import SessionLocal
from app.models.codigo_verificacion import CodigoVerificacion

CARACTERES_CODIGO_VINCULACION = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # Sin O, 0, I, 1, L (32 caracteres alfanuméricos)
EXPIRACION_VINCULACION_SEGUNDOS = 15 * 60  # 15 minutos


@dataclass
class EntradaCodigoVinculacion:
    usuario_id: str
    codigo: str
    expiracion: float
    creado_en: float


def generar_codigo_vinculacion(usuario_id: str | int) -> tuple[str, float]:
    """
    Genera un código de 6 caracteres alfanuméricos en mayúscula, sin caracteres ambiguos.
    Invalida cualquier código previo generado por el usuario.
    Persiste en la tabla codigos_verificacion de Postgres.
    Retorna (codigo, expiracion_timestamp).
    """
    uid_str = str(usuario_id)
    ahora = datetime.now(timezone.utc)
    expiracion = ahora + timedelta(seconds=EXPIRACION_VINCULACION_SEGUNDOS)

    with SessionLocal() as db:
        # Invalidar cualquier código previo activo del mismo usuario
        db.execute(
            update(CodigoVerificacion)
            .where(
                CodigoVerificacion.tipo == "vinculacion_whatsapp",
                CodigoVerificacion.identificador == uid_str,
                CodigoVerificacion.consumido == False,
            )
            .values(consumido=True, consumido_en=ahora)
        )

        # Generar código único de 6 caracteres
        for _ in range(20):
            candidato = "".join(random.choices(CARACTERES_CODIGO_VINCULACION, k=6))
            existe = db.execute(
                select(CodigoVerificacion.id)
                .where(
                    CodigoVerificacion.tipo == "vinculacion_whatsapp",
                    CodigoVerificacion.codigo == candidato,
                    CodigoVerificacion.consumido == False,
                    CodigoVerificacion.expiracion > ahora,
                )
            ).first()
            if not existe:
                codigo = candidato
                break
        else:
            codigo = "".join(random.choices(CARACTERES_CODIGO_VINCULACION, k=6))

        nuevo = CodigoVerificacion(
            id=uuid4(),
            tipo="vinculacion_whatsapp",
            identificador=uid_str,
            codigo=codigo,
            expiracion=expiracion,
            intentos_fallidos=0,
            max_intentos=MAX_INTENTOS,
            creado_en=ahora,
            consumido=False,
        )
        db.add(nuevo)
        db.commit()

    return codigo, expiracion.timestamp()


def consumir_codigo_vinculacion(codigo: str) -> None:
    """Invalida inmediatamente el código consumido en Postgres para asegurar uso único."""
    cod = codigo.strip().upper()
    ahora = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.execute(
            update(CodigoVerificacion)
            .where(
                CodigoVerificacion.tipo == "vinculacion_whatsapp",
                CodigoVerificacion.codigo == cod,
                CodigoVerificacion.consumido == False,
            )
            .values(consumido=True, consumido_en=ahora)
        )
        db.commit()


def buscar_codigo_vinculacion(
    mensaje_texto: str,
) -> tuple[str | None, EntradaCodigoVinculacion | None, bool]:
    """
    Busca de manera tolerante un código de vinculación en el mensaje de texto entrante consultando Postgres.
    Tolerancia:
    - Insensible a mayúsculas/minúsculas.
    - Ignora espacios, guiones y signos de puntuación alrededor o dentro del código.
    - Encuentra el código aunque el usuario haya editado el resto del mensaje.

    Retorna: (codigo_detectado, entrada_activa_o_None, es_vencido)
    """
    if not mensaje_texto:
        return None, None, False

    texto_upper = mensaje_texto.upper()
    texto_compacto = re.sub(r"[^A-Z0-9]", "", texto_upper)
    ahora = datetime.now(timezone.utc)
    hace_dos_horas = ahora - timedelta(hours=2)

    with SessionLocal() as db:
        # Códigos no consumidos generados en las últimas 2 horas
        filas = db.execute(
            select(CodigoVerificacion)
            .where(
                CodigoVerificacion.tipo == "vinculacion_whatsapp",
                CodigoVerificacion.consumido == False,
                CodigoVerificacion.creado_en >= hace_dos_horas,
            )
        ).scalars().all()

        # 1. Coincidencia directa con códigos activos o recientemente vencidos
        for entrada in filas:
            if entrada.codigo in texto_compacto:
                if ahora <= entrada.expiracion:
                    ent = EntradaCodigoVinculacion(
                        usuario_id=entrada.identificador,
                        codigo=entrada.codigo,
                        expiracion=entrada.expiracion.timestamp(),
                        creado_en=entrada.creado_en.timestamp(),
                    )
                    return entrada.codigo, ent, False
                return entrada.codigo, None, True

        # 2. Detección por patrón explícito: Codigo / Código / Cod
        match_prefijo = re.search(
            r"(?:codigo|código|cod)\s*[:=]?\s*([A-Z0-9\s\-]{6,12})",
            texto_upper,
        )
        if match_prefijo:
            cand = re.sub(r"[^A-Z0-9]", "", match_prefijo.group(1))[:6]
            if len(cand) == 6:
                for entrada in filas:
                    if entrada.codigo == cand:
                        if ahora <= entrada.expiracion:
                            ent = EntradaCodigoVinculacion(
                                usuario_id=entrada.identificador,
                                codigo=entrada.codigo,
                                expiracion=entrada.expiracion.timestamp(),
                                creado_en=entrada.creado_en.timestamp(),
                            )
                            return cand, ent, False
                        return cand, None, True
                return cand, None, True

    return None, None, False



def formatear_numero_whatsapp(telefono: str) -> str:
    """
    Formatea un número para WhatsApp Meta Cloud API (formato E.164 plano de solo dígitos, sin prefijo whatsapp:).
    - Remueve 'whatsapp:' si existe
    - Remueve espacios, guiones y caracteres no numéricos
    - Si empieza con '0' (formato local ej. 011...): convierte a '549' + número sin el 0
    - Si empieza con '15' (formato local ej. 15...): convierte a '549' + número
    """
    if not telefono:
        return ""

    tel = telefono.strip()
    if tel.startswith("whatsapp:"):
        tel = tel[9:].strip()

    if tel.startswith("+"):
        tel = tel[1:].strip()
    elif tel.startswith("0"):
        tel = f"549{tel[1:]}"
    elif tel.startswith("15"):
        tel = f"549{tel}"

    digitos = "".join(c for c in tel if c.isdigit())
    return digitos


def enviar_whatsapp(numero: str, mensaje: str) -> bool:
    """
    Envía un mensaje por WhatsApp usando Meta WhatsApp Cloud API (Graph API).
    Incluye 3 reintentos con backoff exponencial ante timeouts o errores 5xx de Meta.
    """
    to_whatsapp = formatear_numero_whatsapp(numero)

    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(
            "WhatsApp / Meta API no configurado; mensaje simulado para %s",
            _enmascarar_telefono(numero),
        )
        if settings.ENVIRONMENT == "development":
            mensaje_log = _enmascarar_otp_en_mensaje(mensaje)
            logger.info("[WHATSAPP-DEV] to=%s body=%s", _enmascarar_telefono(to_whatsapp), mensaje_log)
        return True

    url = f"https://graph.facebook.com/v21.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_whatsapp,
        "type": "text",
        "text": {"body": mensaje},
    }

    max_intentos = 3
    backoff = 0.5

    for intento in range(1, max_intentos + 1):
        try:
            logger.debug(
                "Enviando WhatsApp vía Meta (intento %d/%d) a %s",
                intento,
                max_intentos,
                _enmascarar_telefono(to_whatsapp),
            )
            client = get_meta_http_client()
            response = client.post(url, headers=headers, json=payload, timeout=15)

            if response.is_success:
                res_json = response.json()
                msg_id = (
                    res_json.get("messages", [{}])[0].get("id", "N/A")
                    if res_json.get("messages")
                    else "N/A"
                )
                logger.info(
                    "WhatsApp enviado exitosamente a %s vía Meta. Message ID: %s",
                    _enmascarar_telefono(to_whatsapp),
                    msg_id,
                )
                return True

            # Si es error 4xx de cliente (bad request, auth error, etc.), no reintentar
            if 400 <= response.status_code < 500:
                logger.error(
                    "Error de cliente al enviar WhatsApp a %s (HTTP %d): %s",
                    _enmascarar_telefono(to_whatsapp),
                    response.status_code,
                    response.text,
                )
                return False

            # Error 5xx del servidor de Meta
            logger.warning(
                "Error de servidor de Meta al enviar WhatsApp a %s (HTTP %d): %s. Reintentando...",
                _enmascarar_telefono(to_whatsapp),
                response.status_code,
                response.text,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                "Timeout o error de red al enviar WhatsApp a %s (intento %d/%d): %s",
                _enmascarar_telefono(to_whatsapp),
                intento,
                max_intentos,
                exc,
            )
        except Exception as exc:
            logger.error("Error inesperado al enviar WhatsApp a %s: %s", _enmascarar_telefono(to_whatsapp), exc)
            return False

        if intento < max_intentos:
            time.sleep(backoff)
            backoff *= 2

    logger.error("Fallaron todos los intentos (%d) para enviar WhatsApp a %s", max_intentos, _enmascarar_telefono(to_whatsapp))
    return False


def enviar_mensaje_whatsapp(telefono: str, mensaje: str) -> bool:
    """Alias de compatibilidad."""
    return enviar_whatsapp(telefono, mensaje)


def enviar_whatsapp_template(
    numero: str,
    template_name: str,
    language_code: str,
    componentes: list | None = None,
) -> bool:
    """
    Envía un mensaje de plantilla por WhatsApp usando Meta WhatsApp Cloud API (Graph API).
    Incluye 3 reintentos con backoff exponencial ante timeouts o errores 5xx de Meta.
    """
    to_whatsapp = formatear_numero_whatsapp(numero)

    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(
            "WhatsApp / Meta API no configurado; mensaje template simulado para %s",
            _enmascarar_telefono(numero),
        )
        if settings.ENVIRONMENT == "development":
            logger.info(
                "[WHATSAPP-DEV] to=%s template=%s lang=%s",
                _enmascarar_telefono(to_whatsapp),
                template_name,
                language_code,
            )
        return True

    url = f"https://graph.facebook.com/v21.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_whatsapp,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": componentes or [],
        },
    }

    max_intentos = 3
    backoff = 0.5

    for intento in range(1, max_intentos + 1):
        try:
            logger.debug(
                "Enviando WhatsApp template vía Meta (intento %d/%d) a %s (template: %s)",
                intento,
                max_intentos,
                _enmascarar_telefono(to_whatsapp),
                template_name,
            )
            client = get_meta_http_client()
            response = client.post(url, headers=headers, json=payload, timeout=15)

            if response.is_success:
                res_json = response.json()
                msg_id = (
                    res_json.get("messages", [{}])[0].get("id", "N/A")
                    if res_json.get("messages")
                    else "N/A"
                )
                logger.info(
                    "WhatsApp template enviado exitosamente a %s vía Meta. Message ID: %s",
                    _enmascarar_telefono(to_whatsapp),
                    msg_id,
                )
                return True

            # Si es error 4xx de cliente (bad request, auth error, template no aprobado, etc.), no reintentar
            if 400 <= response.status_code < 500:
                logger.error(
                    "Error de cliente al enviar WhatsApp template a %s (HTTP %d): %s",
                    _enmascarar_telefono(to_whatsapp),
                    response.status_code,
                    response.text,
                )
                return False

            # Error 5xx del servidor de Meta
            logger.warning(
                "Error de servidor de Meta al enviar WhatsApp template a %s (HTTP %d): %s. Reintentando...",
                _enmascarar_telefono(to_whatsapp),
                response.status_code,
                response.text,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                "Timeout o error de red al enviar WhatsApp template a %s (intento %d/%d): %s",
                _enmascarar_telefono(to_whatsapp),
                intento,
                max_intentos,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Error inesperado al enviar WhatsApp template a %s: %s",
                _enmascarar_telefono(to_whatsapp),
                exc,
            )
            return False

        if intento < max_intentos:
            time.sleep(backoff)
            backoff *= 2

    logger.error(
        "Fallaron todos los intentos (%d) para enviar WhatsApp template a %s",
        max_intentos,
        _enmascarar_telefono(to_whatsapp),
    )
    return False



def marcar_leido_y_escribiendo(wamid: str) -> bool:
    """
    Marca un mensaje como leído y activa el indicador de escribiendo en Meta WhatsApp Cloud API.
    """
    if not wamid or not isinstance(wamid, str) or not wamid.startswith("wamid."):
        return False
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        return False

    url = f"https://graph.facebook.com/v21.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": wamid,
        "typing_indicator": {"type": "text"},
    }

    try:
        client = get_meta_http_client()
        res = client.post(url, headers=headers, json=payload, timeout=3)
        return res.is_success
    except Exception as exc:
        logger.warning("Error al marcar mensaje %s como leído y escribiendo: %s", wamid, exc)
        return False

