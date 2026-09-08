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


CARACTERES_CODIGO_VINCULACION = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # Sin O, 0, I, 1, L (32 caracteres alfanuméricos)
EXPIRACION_VINCULACION_SEGUNDOS = 15 * 60  # 15 minutos


@dataclass
class EntradaCodigoVinculacion:
    usuario_id: str
    codigo: str
    expiracion: float
    creado_en: float


_codigos_vinculacion: dict[str, EntradaCodigoVinculacion] = {}
_usuario_a_codigo_vinculacion: dict[str, str] = {}
_codigos_vencidos_recientes: dict[str, float] = {}


def _limpiar_codigos_vinculacion_expirados() -> None:
    ahora = time.time()
    expirados = [c for c, v in _codigos_vinculacion.items() if v.expiracion <= ahora]
    for c in expirados:
        entrada = _codigos_vinculacion.pop(c, None)
        if entrada:
            _codigos_vencidos_recientes[c] = ahora
            if _usuario_a_codigo_vinculacion.get(entrada.usuario_id) == c:
                _usuario_a_codigo_vinculacion.pop(entrada.usuario_id, None)

    # Purgar códigos vencidos registrados hace más de 1 hora
    antiguos = [c for c, t in _codigos_vencidos_recientes.items() if ahora - t > 3600]
    for c in antiguos:
        _codigos_vencidos_recientes.pop(c, None)


def generar_codigo_vinculacion(usuario_id: str | int) -> tuple[str, float]:
    """
    Genera un código de 6 caracteres alfanuméricos en mayúscula, sin caracteres ambiguos.
    Invalida cualquier código previo generado por el usuario.
    Retorna (codigo, expiracion_timestamp).
    """
    _limpiar_codigos_vinculacion_expirados()
    uid_str = str(usuario_id)

    # Invalidar código previo del mismo usuario si existe
    codigo_previo = _usuario_a_codigo_vinculacion.get(uid_str)
    if codigo_previo:
        _codigos_vinculacion.pop(codigo_previo, None)
        _usuario_a_codigo_vinculacion.pop(uid_str, None)

    # Generar código único de 6 caracteres
    for _ in range(20):
        candidato = "".join(random.choices(CARACTERES_CODIGO_VINCULACION, k=6))
        if candidato not in _codigos_vinculacion:
            codigo = candidato
            break
    else:
        codigo = "".join(random.choices(CARACTERES_CODIGO_VINCULACION, k=6))

    expiracion = time.time() + EXPIRACION_VINCULACION_SEGUNDOS
    entrada = EntradaCodigoVinculacion(
        usuario_id=uid_str,
        codigo=codigo,
        expiracion=expiracion,
        creado_en=time.time(),
    )
    _codigos_vinculacion[codigo] = entrada
    _usuario_a_codigo_vinculacion[uid_str] = codigo
    return codigo, expiracion


def consumir_codigo_vinculacion(codigo: str) -> None:
    """Invalida inmediatamente el código consumido para asegurar uso único."""
    cod = codigo.strip().upper()
    entrada = _codigos_vinculacion.pop(cod, None)
    if entrada and _usuario_a_codigo_vinculacion.get(entrada.usuario_id) == cod:
        _usuario_a_codigo_vinculacion.pop(entrada.usuario_id, None)
    _codigos_vencidos_recientes.pop(cod, None)


def buscar_codigo_vinculacion(
    mensaje_texto: str,
) -> tuple[str | None, EntradaCodigoVinculacion | None, bool]:
    """
    Busca de manera tolerante un código de vinculación en el mensaje de texto entrante.
    Tolerancia:
    - Insensible a mayúsculas/minúsculas.
    - Ignora espacios, guiones y signos de puntuación alrededor o dentro del código.
    - Encuentra el código aunque el usuario haya editado el resto del mensaje.

    Retorna: (codigo_detectado, entrada_activa_o_None, es_vencido)
    """
    if not mensaje_texto:
        return None, None, False

    _limpiar_codigos_vinculacion_expirados()

    texto_upper = mensaje_texto.upper()
    texto_compacto = re.sub(r"[^A-Z0-9]", "", texto_upper)

    # 1. Coincidencia directa con códigos activos en memoria
    for codigo_activo, entrada in list(_codigos_vinculacion.items()):
        if codigo_activo in texto_compacto:
            if time.time() <= entrada.expiracion:
                return codigo_activo, entrada, False
            return codigo_activo, None, True

    # 2. Coincidencia con códigos vencidos recientemente
    for codigo_vencido in list(_codigos_vencidos_recientes.keys()):
        if codigo_vencido in texto_compacto:
            return codigo_vencido, None, True

    # 3. Detección por patrón explícito: Codigo / Código / Cod
    match_prefijo = re.search(
        r"(?:codigo|código|cod)\s*[:=]?\s*([A-Z0-9\s\-]{6,12})",
        texto_upper,
    )
    if match_prefijo:
        cand = re.sub(r"[^A-Z0-9]", "", match_prefijo.group(1))[:6]
        if len(cand) == 6:
            if cand in _codigos_vinculacion:
                ent = _codigos_vinculacion[cand]
                if time.time() <= ent.expiracion:
                    return cand, ent, False
                return cand, None, True
            if cand in _codigos_vencidos_recientes:
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
            with httpx.Client(timeout=15) as client:
                response = client.post(url, headers=headers, json=payload)

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
