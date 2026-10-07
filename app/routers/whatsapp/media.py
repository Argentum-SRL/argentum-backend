"""
Procesamiento y descarga de medios (audios e imágenes) para el flujo de WhatsApp IA.
Maneja la descarga vía Meta Cloud API, duración de audio, transcripción con Whisper y análisis visual con Vision.
"""
from __future__ import annotations

import io
import struct
import wave

import structlog

from app.core.config import settings
from app.services.ai_service import get_openai_client
from app.services.whatsapp_service import get_meta_http_client

logger = structlog.get_logger("whatsapp")

def _descargar_medio_meta(media_id: str) -> tuple[bytes | None, str | None]:
    """
    Descarga un archivo multimedia desde Meta WhatsApp Cloud API en dos pasos:
    1. Obtener la URL temporal del medio vía Graph API.
    2. Descargar los bytes del medio usando el Bearer token.
    Retorna (bytes, mime_type) o (None, None) si falla.
    """
    if not settings.WHATSAPP_ACCESS_TOKEN or not media_id:
        logger.warning("No se puede descargar medio de Meta: WHATSAPP_ACCESS_TOKEN o media_id no configurado")
        return None, None

    headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
    try:
        client = get_meta_http_client()
        # Paso 1: Consultar metadata del medio para obtener la URL de descarga
        meta_url = f"https://graph.facebook.com/v21.0/{media_id}"
        res_meta = client.get(meta_url, headers=headers, timeout=30, follow_redirects=True)
        res_meta.raise_for_status()
        data = res_meta.json()
        download_url = data.get("url")
        mime_type = data.get("mime_type")

        if not download_url:
            logger.error("Meta Graph API no devolvió URL de descarga para media_id %s", media_id)
            return None, None

        # Paso 2: Descargar el contenido binario con el Bearer token
        res_media = client.get(download_url, headers=headers, timeout=30, follow_redirects=True)
        res_media.raise_for_status()
        return res_media.content, mime_type
    except Exception as e:
        logger.exception("Error al descargar medio de Meta (media_id=%s): %s", media_id, e)
        return None, None

def _obtener_duracion_audio_bytes(audio_bytes: bytes, content_type: str = "") -> float | None:
    """Intenta calcular la duración en segundos del audio a partir de sus bytes."""
    if not audio_bytes:
        return None
    try:
        if audio_bytes.startswith(b"RIFF") and b"WAVE" in audio_bytes[:16]:
            import wave
            import io
            with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
                framerate = wav.getframerate()
                if framerate > 0:
                    return wav.getnframes() / float(framerate)
        if audio_bytes.startswith(b"OggS"):
            import struct
            idx = audio_bytes.rfind(b"OggS")
            if idx != -1 and idx + 14 <= len(audio_bytes):
                granule_pos = struct.unpack("<Q", audio_bytes[idx + 6:idx + 14])[0]
                if granule_pos > 0:
                    return granule_pos / 48000.0
    except Exception:
        pass
    return None

def _transcribir_audio(
    media_id: str,
    media_content_type: str = "audio/ogg",
    duracion_maxima_segundos: int = 120,
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[str | None, str | None]:
    """
    Descarga un audio de Meta Cloud API en dos pasos y lo transcribe con Whisper.
    Si el tamaño supera max_bytes (8 MB), no llama a Whisper y retorna (None, "TAMANO_EXCEDIDO").
    Si la duración supera duracion_maxima_segundos, no llama a Whisper y retorna (None, "DURACION_EXCEDIDA").
    Retorna (texto_transcripto, None) o (None, motivo_error).
    """
    try:
        audio_bytes, mime = _descargar_medio_meta(media_id)
        if not audio_bytes:
            return None, "DESCARGA_FALLIDA"

        if len(audio_bytes) > max_bytes:
            logger.warning("whatsapp_audio_tamano_bytes_excedido", bytes_length=len(audio_bytes), max_bytes=max_bytes)
            return None, "TAMANO_EXCEDIDO"

        content_type = mime or media_content_type or "audio/ogg"

        duracion_detectada = _obtener_duracion_audio_bytes(audio_bytes, content_type)
        if duracion_detectada is not None and duracion_detectada > duracion_maxima_segundos:
            logger.warning("whatsapp_audio_duracion_bytes_excedida", duracion=duracion_detectada)
            return None, "DURACION_EXCEDIDA"

        # Determinar extensión según content type
        ext_map = {
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".mp4",
            "audio/wav": ".wav",
            "audio/webm": ".webm",
            "audio/amr": ".amr",
            "audio/aac": ".aac",
        }
        ext = ".ogg"
        for k, v in ext_map.items():
            if k in content_type:
                ext = v
                break

        # Transcribir pasando tupla (filename, bytes, content_type) directamente en memoria
        try:
            client_oai = get_openai_client()
            transcripcion = client_oai.audio.transcriptions.create(
                model="whisper-1",
                file=(f"audio{ext}", audio_bytes, content_type),
                language="es",
            )
            return transcripcion.text, None
        except Exception:
            logger.exception("Error al transcribir audio de WhatsApp con OpenAI")
            return None, "ERROR_TRANSCRIPCION"

    except Exception:
        logger.exception("Error al transcribir audio de WhatsApp")
        return None, "ERROR_TRANSCRIPCION"
