"""
Etapa de entrada para WhatsApp:
1. Manejo de vinculación de cuentas y usuarios no registrados.
2. Procesamiento de medios (audio Whisper, imagen GPT-4o Vision) y validación de texto de entrada.
"""
from __future__ import annotations

import logging
import time
from uuid import UUID
from sqlalchemy import select
import structlog

from app.core.config import settings
from app.models.usuario import Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.db_lookups import _verificar_rate_limit_vinculacion
from app.routers.whatsapp.detectors import _debe_responder_no_registrado
from app.routers.whatsapp.media import _extraer_transaccion_de_imagen, _transcribir_audio
from app.services import whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion
from app.utils.telefono import normalizar_telefono_ar

logger = structlog.get_logger(__name__)


def procesar_vinculacion_o_desconocido(ctx: ContextoMensaje) -> None:
    """
    Gestiona la vinculación de cuentas para remitentes no registrados o la respuesta de cuenta no encontrada.
    Si procesa una vinculación o descarta el mensaje, marca ctx.terminado = True.
    """
    if ctx.msg_type == "text" and not ctx.usuario:
        tel_identificador = normalizar_telefono_ar(ctx.from_number) or ctx.from_number
        if not _verificar_rate_limit_vinculacion(tel_identificador, db=ctx.db):
            logger.warning(
                "whatsapp_rate_limit_vinculacion_superado",
                from_number=ctx.from_number,
            )
            ctx.terminado = True
            return

        texto_candidato = ctx.msg.get("text", {}).get("body", "").strip()
        logger.info(
            "whatsapp_webhook_mensaje_recibido",
            from_number=whatsapp_service._enmascarar_telefono(ctx.from_number),
            texto=texto_candidato,
            msg_type=ctx.msg_type,
        )
        codigo_vinc, entrada_vinc, es_vencido = whatsapp_service.buscar_codigo_vinculacion(texto_candidato)
        if codigo_vinc:
            if es_vencido or not entrada_vinc:
                whatsapp_service.enviar_whatsapp(
                    ctx.from_number,
                    "El código de vinculación expiró. Por favor solicitá un código nuevo desde la app.",
                )
                ctx.terminado = True
                return

            whatsapp_service.consumir_codigo_vinculacion(codigo_vinc)

            tel_norm = normalizar_telefono_ar(ctx.from_number)
            tel_guardar = f"+{ctx.from_number.lstrip('+')}"

            try:
                uid_busqueda = UUID(str(entrada_vinc.usuario_id))
            except (ValueError, TypeError):
                uid_busqueda = entrada_vinc.usuario_id

            usuario_dueno = ctx.db.execute(
                select(Usuario).where(Usuario.id == uid_busqueda)
            ).scalar_one_or_none()

            if not usuario_dueno:
                logger.error("whatsapp_vinculacion_usuario_inexistente", usuario_id=entrada_vinc.usuario_id)
                ctx.terminado = True
                return

            otro_usuario = ctx.db.execute(
                select(Usuario).where(
                    (Usuario.telefono == tel_guardar)
                    | (Usuario.telefono == ctx.from_number)
                    | (Usuario.telefono_normalizado == tel_norm),
                    Usuario.id != usuario_dueno.id,
                )
            ).scalar_one_or_none()

            if otro_usuario:
                logger.warning(
                    "whatsapp_vinculacion_telefono_duplicado",
                    from_number=ctx.from_number,
                    dueno_actual=str(otro_usuario.id),
                )
                whatsapp_service.enviar_whatsapp(
                    ctx.from_number,
                    "Ese número de teléfono ya está asociado a otra cuenta de Argentum.",
                )
                ctx.terminado = True
                return

            tel_viejo = usuario_dueno.telefono
            if tel_viejo and usuario_dueno.telefono_verificado and normalizar_telefono_ar(tel_viejo) != tel_norm:
                try:
                    whatsapp_service.enviar_whatsapp(
                        tel_viejo,
                        "Tu cuenta de Argentum fue desvinculada de este número porque se asoció a un nuevo número de WhatsApp. Si no fuiste vos, contactanos inmediatamente.",
                    )
                except Exception as e:
                    logger.warning("No se pudo enviar aviso de desvinculación a número anterior: %s", e)

            usuario_dueno.telefono = tel_guardar
            usuario_dueno.telefono_normalizado = tel_norm
            usuario_dueno.telefono_verificado = True

            emitir_evento_actualizacion(ctx.db, usuario_dueno.id, "usuario")
            ctx.db.commit()

            whatsapp_service.enviar_whatsapp(
                ctx.from_number,
                "¡Tu cuenta de Argentum fue vinculada con éxito!\n"
                "A partir de ahora podés registrar tus gastos e ingresos directamente desde acá. "
                "Probá mandarme un mensaje o audio como: *Almuerzo $3500 con Galicia*.",
            )

            try:
                from app.services.email_service import enviar_email_telefono_vinculado
                enviar_email_telefono_vinculado(
                    destinatario=usuario_dueno.email,
                    telefono=tel_guardar,
                    nombre=usuario_dueno.nombre,
                )
            except Exception as e:
                logger.error("Error al enviar email de confirmación de vinculación: %s", e)

            logger.info("whatsapp_vinculacion_exitosa", usuario_id=str(usuario_dueno.id), telefono=ctx.from_number)
            ctx.usuario = usuario_dueno
            ctx.terminado = True
            return

    if not ctx.usuario:
        telefono_norm = normalizar_telefono_ar(ctx.from_number)
        debe_responder = _debe_responder_no_registrado(telefono_norm)
        logger.warning(
            "whatsapp_usuario_no_encontrado",
            telefono_ultimos_4=telefono_norm[-4:] if telefono_norm else None,
            respondido=debe_responder,
        )
        if debe_responder:
            whatsapp_service.enviar_whatsapp(ctx.from_number, "No encontramos tu cuenta. Registrate en miargentum.com")
        ctx.terminado = True
        return


def procesar_entrada_medios_y_texto(ctx: ContextoMensaje) -> None:
    """
    Procesa el medio de entrada (texto, audio o imagen) y realiza las validaciones tempranas de longitud.
    Si ocurre un error o se excede el límite, envía la respuesta de fallback y marca ctx.terminado = True.
    """
    if ctx.msg_type == "text":
        ctx.mensaje_texto = ctx.msg.get("text", {}).get("body", "").strip()

    elif ctx.msg_type == "audio":
        audio_obj = ctx.msg.get("audio", {})
        media_id = audio_obj.get("id")
        mime_type = audio_obj.get("mime_type", "audio/ogg")

        if media_id:
            t_media_start = time.perf_counter()
            ctx.transcripcion, error_audio = _transcribir_audio(media_id, mime_type, duracion_maxima_segundos=120)
            t_media_end = time.perf_counter()
            logger.info(
                "[LATENCIA][MEDIA-AUDIO] Transcripción Whisper: %.2fs",
                t_media_end - t_media_start,
            )

            if error_audio == "DURACION_EXCEDIDA":
                whatsapp_service.enviar_whatsapp(
                    ctx.from_number,
                    "El audio es muy largo (máximo 2 minutos). Por favor mandá un audio más corto o escribí el gasto en texto.",
                )
                ctx.terminado = True
                return

            if error_audio == "TAMANO_EXCEDIDO":
                whatsapp_service.enviar_whatsapp(
                    ctx.from_number,
                    "El audio es muy pesado (máximo 8 MB). Por favor mandá un audio más corto o escribí el gasto en texto.",
                )
                ctx.terminado = True
                return

            if ctx.transcripcion:
                ctx.mensaje_texto = ctx.transcripcion
                if settings.ENVIRONMENT == "production":
                    logger.info("Audio transcripto exitosamente (longitud: %d caracteres)", len(ctx.transcripcion))
                else:
                    logger.info("Audio transcripto: '%s'", ctx.transcripcion[:100])
            else:
                whatsapp_service.enviar_whatsapp(
                    ctx.from_number, "No pude escuchar el audio. Mandame el mensaje en texto."
                )
                ctx.terminado = True
                return
        else:
            whatsapp_service.enviar_whatsapp(
                ctx.from_number, "No pude escuchar el audio. Mandame el mensaje en texto."
            )
            ctx.terminado = True
            return

    elif ctx.msg_type == "image":
        image_obj = ctx.msg.get("image", {})
        caption = image_obj.get("caption") or ""
        media_id = image_obj.get("id")
        mime_type = image_obj.get("mime_type", "image/jpeg")
        nombre_usuario = f"{ctx.usuario.nombre or ''} {ctx.usuario.apellido or ''}".strip() if ctx.usuario else ""

        if media_id:
            t_media_start = time.perf_counter()
            descripcion_imagen, error_img = _extraer_transaccion_de_imagen(
                media_id, mime_type, nombre_usuario, max_bytes=5 * 1024 * 1024
            )
            t_media_end = time.perf_counter()
            logger.info(
                "[LATENCIA][MEDIA-IMAGEN] Análisis GPT-4o Vision: %.2fs",
                t_media_end - t_media_start,
            )

            if error_img == "TAMANO_EXCEDIDO":
                whatsapp_service.enviar_whatsapp(
                    ctx.from_number,
                    "La imagen es muy pesada (máximo 5 MB). Por favor mandá una foto más liviana.",
                )
                ctx.terminado = True
                return

            if descripcion_imagen:
                ctx.es_imagen = True
                ctx.caption_imagen = caption
                ctx.mensaje_texto = descripcion_imagen
                if settings.ENVIRONMENT == "production":
                    logger.info("Imagen analizada exitosamente (longitud: %d caracteres)", len(descripcion_imagen))
                else:
                    logger.info("Imagen analizada: '%s'", descripcion_imagen[:100])
            else:
                whatsapp_service.enviar_whatsapp(
                    ctx.from_number, "No pude leer el comprobante. Mandame los datos en texto."
                )
                ctx.terminado = True
                return
        else:
            whatsapp_service.enviar_whatsapp(
                ctx.from_number, "No pude leer el comprobante. Mandame los datos en texto."
            )
            ctx.terminado = True
            return

    if not ctx.mensaje_texto:
        whatsapp_service.enviar_whatsapp(
            ctx.from_number,
            "No entendí bien lo que quisiste decir. Podés contarme qué gastaste, por ejemplo: *Almuerzo $1.500*",
        )
        ctx.terminado = True
        return

    if len(ctx.mensaje_texto) > 1500:
        logger.warning("whatsapp_texto_limite_caracteres_superado", longitud=len(ctx.mensaje_texto))
        whatsapp_service.enviar_whatsapp(
            ctx.from_number,
            "El mensaje es muy largo (máximo 1500 caracteres). Por favor mandalo más resumido.",
        )
        ctx.terminado = True
        return
