"""
app/routers/whatsapp_ia.py — Webhook de WhatsApp para IA conversacional de Argentum con Meta Cloud API.
Recibe webhooks JSON de Meta, los procesa con ai_service y responde vía Graph API.
"""
import hashlib
import hmac
import json
import re
import secrets
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import anyio
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.auth import get_current_admin_user
from app.core.config import settings
from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.core.database import SessionLocal, get_db
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.suscripcion import EstadoSuscripcion, Suscripcion
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import EstadoUsuario, Moneda, Usuario
from app.routers.whatsapp.confirmaciones import _ejecutar_intent
from app.routers.whatsapp.constantes import MESES_ES_GEN
from app.routers.whatsapp.db_lookups import (
    MAX_INTENTOS_VINCULACION_POR_VENTANA,
    MAX_MEDIOS_POR_MINUTO_REGISTRADO,
    MAX_MENSAJES_POR_MINUTO_REGISTRADO,
    PLAZO_DESHACER_CORREGIR_MINUTOS,
    PLAZO_EXPIRACION_ESTADO_MINUTOS,
    VENTANA_RATE_LIMIT_WPP_SEGUNDOS,
    VENTANA_VINCULACION_SEGUNDOS,
    _buscar_meta_activa_por_nombre,
    _buscar_propuesta_baja_suscripcion_pendiente,
    _buscar_propuesta_cambio_precio_pendiente,
    _buscar_propuesta_confirmable_mas_reciente,
    _buscar_propuesta_corregir_pendiente,
    _buscar_propuesta_deshacer_pendiente,
    _buscar_propuesta_pendiente,
    _buscar_propuesta_suscripcion_pendiente,
    _buscar_propuesta_transferencia_pendiente,
    _buscar_slot_filling_activo,
    _buscar_slot_filling_vencido,
    _buscar_suscripcion_activa_por_nombre,
    _buscar_suscripcion_cobrada_periodo_actual,
    _buscar_transaccion_duplicada_reciente,
    _buscar_ultimo_movimiento_whatsapp,
    _buscar_usuario_por_telefono,
    _obtener_billeteras_activas,
    _obtener_cotizacion_referencia_usuario,
    _obtener_fallback_otros,
    _obtener_historial_reciente,
    _obtener_tarjetas_activas,
    _resolver_billetera,
    _resolver_categoria_y_subcategoria,
    _verificar_rate_limit_registrado,
    _verificar_rate_limit_vinculacion,
)
from app.routers.whatsapp.deshacer_corregir import _evaluar_correccion_billetera
from app.routers.whatsapp.detectors import (
    COOLDOWN_MINUTOS_NO_REGISTRADO,
    FRASES_DESHACER,
    PALABRAS_CANCELACION,
    PALABRAS_CONFIRMACION,
    SALUDOS_RIOPLATENSE,
    _debe_responder_no_registrado,
    _detectar_ambiguedad_suscripcion,
    _es_cambio_precio_suscripcion,
    _es_cancelacion,
    _es_confirmacion,
    _es_confirmacion_gasto_aparte,
    _es_confirmacion_lote_ambos,
    _es_confirmacion_lote_uno_solo,
    _es_confirmacion_nuevo_movimiento,
    _es_consulta_suscripciones,
    _es_descarte_duplicado,
    _es_intento_alta_suscripcion,
    _es_pedido_baja_suscripcion,
    _es_pedido_deshacer,
    _es_pedido_pago_resumen,
    _es_pregunta_billetera,
    _es_saludo,
    _es_senial_gasto_suelto,
    _es_senial_suscripcion,
    _parece_intento_correccion,
)
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.routers.whatsapp.gastos import manejar_consulta_gastos
from app.routers.whatsapp.handlers import (
    manejar_aclaracion_cuotas,
    manejar_alta_suscripcion,
    manejar_ambiguedad_suscripcion,
    manejar_baja_suscripcion,
    manejar_cambio_precio_suscripcion,
    manejar_cancelacion,
    manejar_confirmacion,
    manejar_consulta_suscripciones,
    manejar_corregir,
    manejar_deshacer,
    manejar_menu_billetera,
    manejar_menu_tarjeta,
    manejar_numero_aislado,
    manejar_pago_resumen,
    manejar_saludo,
    manejar_transferencias,
    manejar_verificaciones_slot_filling,
)
from app.routers.whatsapp.media import (
    _descargar_medio_meta,
    _extraer_transaccion_de_imagen,
    _obtener_duracion_audio_bytes,
    _transcribir_audio,
)
from app.routers.whatsapp.metas import (
    manejar_aporte_meta,
    manejar_cancelacion_aporte_meta,
    manejar_confirmacion_aporte_meta,
)
from app.routers.whatsapp.parsers import (
    _MESES_RIOPLATENSE,
    _extraer_frecuencia_mencionada,
    _extraer_monto_y_moneda_suscripcion,
    _extraer_nombre_servicio,
    _fmt,
    _formatear_fecha_natural,
    _interpretar_cuotas,
    _nombre_corto_categoria,
    _parsear_monto_argentino,
    _parsear_monto_texto_cuota,
    _resolver_fecha_transaccion,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.propuestas import (
    _construir_propuesta_credito,
    _construir_propuesta_transaccion,
    _resolver_mencion_tarjeta_en_texto,
)
from app.routers.whatsapp.registro import _registrar_directo_si_corresponde
from app.routers.whatsapp.resolvers_cascada import (
    ALIAS_BANCOS_ARGENTINOS,
    ALIAS_BILLETERAS,
    ALIAS_REDES_ARGENTINAS,
    FORMAS_GENERICAS_TARJETA,
    _detectar_duplicados_en_lote,
    _entidades_completas,
    _generar_menu_billeteras,
    _generar_menu_tarjetas,
    _merge_entidades,
    _validar_item_movimiento,
    construir_alias_tarjeta,
    resolver_billetera_cascada,
    resolver_tarjeta_cascada,
)
from app.schemas.suscripcion import ActualizarPrecioRequest, SuscripcionCreate
from app.schemas.transaccion import InfoCuotas, TransaccionCreate, TransaccionUpdate
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import (
    ai_service,
    meta_service,
    presupuesto_service,
    suscripcion_service,
    tarjeta_service,
    transaccion_service,
    transferencia_service,
)
from app.services.evento_service import emitir_evento_actualizacion
from app.services.openai_client import get_openai_client
from app.services.rate_limit_service import verificar_rate_limit
from app.services.tarjeta_service import calcular_primer_vencimiento
from app.services.transaccion_service import (
    actualizar_transaccion,
    deducir_metodo_pago,
    eliminar_transaccion,
)
from app.services.whatsapp_service import (
    buscar_codigo_vinculacion,
    consumir_codigo_vinculacion,
    enviar_whatsapp,
    get_meta_http_client,
    marcar_leido_y_escribiendo,
)
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.telefono import normalizar_telefono_ar
from app.utils.texto import normalizar_texto

import structlog
logger = structlog.get_logger("whatsapp")

router = APIRouter(prefix="/whatsapp", tags=["whatsapp-ia"])

MAX_MOVIMIENTOS_POR_LOTE = 5
MSG_TOPE_MOVIMIENTOS_SUPERADO = (
    "Por seguridad sólo puedo registrar hasta 5 movimientos juntos por mensaje. "
    "Por favor enviame los gastos en tandas de hasta 5 ítems."
)
MSG_NO_MEZCLAR_TRANSFERENCIAS = (
    "Por favor no mezcles transferencias, retiros o movimientos en dólares con otros gastos o ingresos en el mismo mensaje. "
    "Enviámelos por separado para que pueda procesarlos correctamente."
)
TERMINOS_BLOQUEO_MEZCLA = [
    "transferencia", "transferi", "transferí", "transferir", "pase", "pasé",
    "extracción", "extraccion", "retire", "retiré", "cajero", "saque", "saqué",
    "dólares", "dolares", "usd", "dolar", "dólar", "mep", "blue", "cambio", "cambie", "cambié"
]
PATRON_BLOQUEO_MEZCLA = re.compile(
    r"(" + "|".join(TERMINOS_BLOQUEO_MEZCLA) + r")",
    re.IGNORECASE
)

PALABRAS_FUERZAN_CREDITO = (
    "credito", "crédito", "cuota", "cuotas", "visa", "master", "mastercard",
    "amex", "american express", "cabal", "naranja", "tarjeta", "resumen"
)
PALABRAS_FUERZAN_DEBITO = (
    "debito", "débito", "efectivo", "cash", "cuenta", "billetera"
)


async def verify_webhook(request: Request) -> PlainTextResponse:
    """
    Handshake de verificación de webhook de Meta (WhatsApp Business Cloud API).
    Meta envía hub.mode, hub.verify_token y hub.challenge por GET.
    """
    mode = request.query_params.get("hub.mode")
    verify_token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode and verify_token and settings.WHATSAPP_VERIFY_TOKEN:
        if mode == "subscribe" and secrets.compare_digest(verify_token, settings.WHATSAPP_VERIFY_TOKEN):
            logger.info("Webhook de WhatsApp verificado exitosamente.")
            return PlainTextResponse(content=challenge or "", status_code=status.HTTP_200_OK)
        else:
            logger.warning("Fallo en verificación de Webhook: token incorrecto.")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verificación fallida")

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parámetros inválidos")


def _preprocesar_webhook_whatsapp_sync(
    body_bytes: bytes, t_inicio: float
) -> tuple[PlainTextResponse, dict | None]:
    """
    Parte A (rápida): parsea el payload, extrae identificadores y persiste el wamid
    de forma idempotente en su propia sesión de BD. Retorna inmediatamente el 200 OK y
    los datos necesarios para que la Parte B corra en background.
    """
    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception:
        logger.warning("Error al decodificar JSON del webhook")
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

    # Extraer mensajes del payload de Meta
    entries = payload.get("entry", [])
    if not entries:
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

    changes = entries[0].get("changes", [])
    if not changes:
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

    value = changes[0].get("value", {})
    messages = value.get("messages", [])
    if not messages:
        # Eventos de estado (sent, delivered, read, failed, etc.)
        statuses = value.get("statuses")
        logger.info(
            "whatsapp_webhook_status_event",
            statuses=statuses,
            value=value,
        )
        if statuses and isinstance(statuses, list):
            for st in statuses:
                if isinstance(st, dict) and st.get("status") == "failed":
                    errors = st.get("errors", [])
                    if errors and isinstance(errors, list):
                        for err in errors:
                            if isinstance(err, dict):
                                logger.warning(
                                    "whatsapp_message_status_failed",
                                    wamid=st.get("id"),
                                    recipient_id=st.get("recipient_id"),
                                    code=err.get("code"),
                                    title=err.get("title"),
                                    error_details=err,
                                )
                            else:
                                logger.warning(
                                    "whatsapp_message_status_failed",
                                    wamid=st.get("id"),
                                    recipient_id=st.get("recipient_id"),
                                    error_details=err,
                                )
                    else:
                        logger.warning(
                            "whatsapp_message_status_failed",
                            wamid=st.get("id"),
                            recipient_id=st.get("recipient_id"),
                            status_details=st,
                        )
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

    msg = messages[0]
    wamid = msg.get("id")
    from_number = msg.get("from", "")
    msg_type = msg.get("type", "text")

    db = SessionLocal()
    try:
        # Idempotencia: Verificar y persistir wamid ANTES de ejecutar lógica de negocio
        if wamid:
            wamid_existente = db.execute(
                select(MensajeWhatsappProcesado.id).where(MensajeWhatsappProcesado.wamid == wamid)
            ).scalar_one_or_none()

            if wamid_existente:
                logger.info("whatsapp_wamid_duplicado_ignorado", wamid=wamid, from_number=from_number)
                return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

            try:
                registro_wamid = MensajeWhatsappProcesado(
                    wamid=wamid,
                    telefono=from_number,
                    tipo_mensaje=msg_type,
                )
                db.add(registro_wamid)
                db.commit()
            except IntegrityError:
                db.rollback()
                logger.info("whatsapp_wamid_concurrente_duplicado_ignorado", wamid=wamid)
                return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None
            except Exception as e:
                db.rollback()
                logger.error("whatsapp_error_registro_wamid", wamid=wamid, error=str(e))
    finally:
        db.close()

    t_ack = time.perf_counter() - t_inicio
    logger.info("[LATENCIA][WEBHOOK_ACK] Ack HTTP 200 retornado en: %.2fs", t_ack)

    datos_mensaje = {
        "msg": msg,
        "wamid": wamid,
        "from_number": from_number,
        "msg_type": msg_type,
        "t_inicio": t_inicio,
    }
    return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), datos_mensaje


def _procesar_mensaje_whatsapp_background(datos_mensaje: dict) -> None:
    """
    Parte B (background): ejecutada vía BackgroundTasks con su propia sesión de BD.
    Incluye feedback visual (leído + reacción ⏳), transcripción/visión, IA, guardado y envío.
    """
    msg = datos_mensaje["msg"]
    wamid = datos_mensaje.get("wamid")
    from_number = datos_mensaje.get("from_number", "")
    msg_type = datos_mensaje.get("msg_type", "text")
    t_inicio = datos_mensaje.get("t_inicio", time.perf_counter())

    # Feedback visual temprano: marcar como leído y mostrar escribiendo
    if wamid:
        marcar_leido_y_escribiendo(wamid)

    db = SessionLocal()
    try:
        try:
            usuario = _buscar_usuario_por_telefono(from_number, db)

            # 2.1 Detección del código de vinculación: solo para remitentes no vinculados
            if msg_type == "text" and not usuario:
                tel_identificador = normalizar_telefono_ar(from_number) or from_number
                if not _verificar_rate_limit_vinculacion(tel_identificador, db=db):
                    logger.warning(
                        "whatsapp_rate_limit_vinculacion_superado",
                        from_number=from_number,
                    )
                    return

                texto_candidato = msg.get("text", {}).get("body", "").strip()
                logger.info("whatsapp_webhook_mensaje_recibido", from_number=from_number, texto=texto_candidato, msg_type=msg_type)
                codigo_vinc, entrada_vinc, es_vencido = buscar_codigo_vinculacion(texto_candidato)
                if codigo_vinc:
                    if es_vencido or not entrada_vinc:
                        enviar_whatsapp(
                            from_number,
                            "El código de vinculación expiró. Por favor solicitá un código nuevo desde la app.",
                        )
                        return

                    # Código activo encontrado -> Consumir para asegurar un solo uso
                    consumir_codigo_vinculacion(codigo_vinc)

                    # 3.1 Normalizar teléfono del payload de Meta
                    tel_norm = normalizar_telefono_ar(from_number)
                    tel_guardar = f"+{from_number.lstrip('+')}"

                    usuario_dueno = db.execute(
                        select(Usuario).where(Usuario.id == entrada_vinc.usuario_id)
                    ).scalar_one_or_none()

                    if not usuario_dueno:
                        logger.error("whatsapp_vinculacion_usuario_inexistente", usuario_id=entrada_vinc.usuario_id)
                        return

                    # 3.2 Si el número ya pertenece a otro usuario: no vincular, responder y salir
                    otro_usuario = db.execute(
                        select(Usuario).where(
                            (Usuario.telefono == tel_guardar) |
                            (Usuario.telefono == from_number) |
                            (Usuario.telefono_normalizado == tel_norm),
                            Usuario.id != usuario_dueno.id,
                        )
                    ).scalar_one_or_none()

                    if otro_usuario:
                        logger.warning(
                            "whatsapp_vinculacion_telefono_duplicado",
                            from_number=from_number,
                            dueno_actual=str(otro_usuario.id),
                        )
                        enviar_whatsapp(
                            from_number,
                            "Ese número de teléfono ya está asociado a otra cuenta de Argentum.",
                        )
                        return

                    # 3.3 Si ya tenía otro número verificado, avisar al número viejo por WhatsApp
                    tel_viejo = usuario_dueno.telefono
                    if tel_viejo and usuario_dueno.telefono_verificado and normalizar_telefono_ar(tel_viejo) != tel_norm:
                        try:
                            enviar_whatsapp(
                                tel_viejo,
                                "Tu cuenta de Argentum fue desvinculada de este número porque se asoció a un nuevo número de WhatsApp. Si no fuiste vos, contactanos inmediatamente.",
                            )
                        except Exception as e:
                            logger.warning("No se pudo enviar aviso de desvinculación a número anterior: %s", e)

                    # 3.4 Guardar telefono y telefono_verificado=True en una sola transacción
                    usuario_dueno.telefono = tel_guardar
                    usuario_dueno.telefono_normalizado = tel_norm
                    usuario_dueno.telefono_verificado = True

                    # 3.7 Emitir evento de actualización
                    emitir_evento_actualizacion(db, usuario_dueno.id, "usuario")
                    db.commit()

                    # 3.5 Responder por WhatsApp confirmando vinculación
                    enviar_whatsapp(
                        from_number,
                        "¡Tu cuenta de Argentum fue vinculada con éxito!\n"
                        "A partir de ahora podés registrar tus gastos e ingresos directamente desde acá. "
                        "Probá mandarme un mensaje o audio como: *Almuerzo $3500 con Galicia*.",
                    )

                    # 3.6 Mandar mail de aviso
                    try:
                        from app.services.email_service import enviar_email_telefono_vinculado
                        enviar_email_telefono_vinculado(
                            destinatario=usuario_dueno.email,
                            telefono=tel_guardar,
                            nombre=usuario_dueno.nombre,
                        )
                    except Exception as e:
                        logger.error("Error al enviar email de confirmación de vinculación: %s", e)

                    logger.info("whatsapp_vinculacion_exitosa", usuario_id=str(usuario_dueno.id), telefono=from_number)
                    return
            if not usuario:
                telefono_norm = normalizar_telefono_ar(from_number)
                debe_responder = _debe_responder_no_registrado(telefono_norm)
                logger.warning(
                    "whatsapp_usuario_no_encontrado",
                    telefono_ultimos_4=telefono_norm[-4:] if telefono_norm else None,
                    respondido=debe_responder,
                )
                if debe_responder:
                    enviar_whatsapp(from_number, "No encontramos tu cuenta. Registrate en miargentum.com")
                return

            # Rate limit para usuario registrado (evaluado antes de llamar a Whisper, GPT-4o Vision o ai_service)
            es_medio = (msg_type in ("audio", "image"))
            tel_usuario = usuario.telefono_normalizado or normalizar_telefono_ar(from_number) or from_number
            permitido, motivo_rate_limit = _verificar_rate_limit_registrado(tel_usuario, es_medio=es_medio)
            if not permitido:
                logger.warning(
                    "whatsapp_rate_limit_registrado_superado",
                    usuario_id=str(usuario.id),
                    tipo_mensaje=msg_type,
                )
                if motivo_rate_limit:
                    enviar_whatsapp(from_number, motivo_rate_limit)
                return

            logger.info(
                "whatsapp_mensaje_recibido",
                usuario_id=str(usuario.id),
                tipo_mensaje=msg_type,
            )

            mensaje_texto = ""
            transcripcion = None
            es_imagen = False
            es_credito = False
            es_lote = False
            caption_imagen = ""

            if msg_type == "text":
                mensaje_texto = msg.get("text", {}).get("body", "").strip()

            elif msg_type == "audio":
                audio_obj = msg.get("audio", {})
                media_id = audio_obj.get("id")
                mime_type = audio_obj.get("mime_type", "audio/ogg")

                if media_id:
                    t_media_start = time.perf_counter()
                    transcripcion, error_audio = _transcribir_audio(media_id, mime_type, duracion_maxima_segundos=120)
                    t_media_end = time.perf_counter()
                    logger.info(
                        "[LATENCIA][MEDIA-AUDIO] Transcripción Whisper: %.2fs",
                        t_media_end - t_media_start,
                    )

                    if error_audio == "DURACION_EXCEDIDA":
                        enviar_whatsapp(
                            from_number,
                            "El audio es muy largo (máximo 2 minutos). Por favor mandá un audio más corto o escribí el gasto en texto.",
                        )
                        return

                    if error_audio == "TAMANO_EXCEDIDO":
                        enviar_whatsapp(
                            from_number,
                            "El audio es muy pesado (máximo 8 MB). Por favor mandá un audio más corto o escribí el gasto en texto.",
                        )
                        return

                    if transcripcion:
                        mensaje_texto = transcripcion
                        if settings.ENVIRONMENT == "production":
                            logger.info("Audio transcripto exitosamente (longitud: %d caracteres)", len(transcripcion))
                        else:
                            logger.info("Audio transcripto: '%s'", transcripcion[:100])
                    else:
                        enviar_whatsapp(
                            from_number, "No pude escuchar el audio. Mandame el mensaje en texto."
                        )
                        return
                else:
                    enviar_whatsapp(
                        from_number, "No pude escuchar el audio. Mandame el mensaje en texto."
                    )
                    return

            elif msg_type == "image":
                image_obj = msg.get("image", {})
                caption = image_obj.get("caption") or ""
                media_id = image_obj.get("id")
                mime_type = image_obj.get("mime_type", "image/jpeg")
                nombre_usuario = f"{usuario.nombre or ''} {usuario.apellido or ''}".strip()

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
                        enviar_whatsapp(
                            from_number,
                            "La imagen es muy pesada (máximo 5 MB). Por favor mandá una foto más liviana.",
                        )
                        return

                    if descripcion_imagen:
                        es_imagen = True
                        caption_imagen = caption
                        mensaje_texto = descripcion_imagen
                        if settings.ENVIRONMENT == "production":
                            logger.info("Imagen analizada exitosamente (longitud: %d caracteres)", len(descripcion_imagen))
                        else:
                            logger.info("Imagen analizada: '%s'", descripcion_imagen[:100])
                    else:
                        enviar_whatsapp(
                            from_number, "No pude leer el comprobante. Mandame los datos en texto."
                        )
                        return
                else:
                    enviar_whatsapp(
                        from_number, "No pude leer el comprobante. Mandame los datos en texto."
                    )
                    return

            if not mensaje_texto:
                enviar_whatsapp(
                    from_number,
                    "No entendí bien lo que quisiste decir. Podés contarme qué gastaste, por ejemplo: *Almuerzo $1.500*",
                )
                return

            if len(mensaje_texto) > 1500:
                logger.warning("whatsapp_texto_limite_caracteres_superado", longitud=len(mensaje_texto))
                enviar_whatsapp(
                    from_number,
                    "El mensaje es muy largo (máximo 1500 caracteres). Por favor mandalo más resumido.",
                )
                return
            # 0. Chequeo determinístico de pedido de pago de resumen de tarjeta (Tarea 7)
            if manejar_pago_resumen(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 1. Chequeo determinístico de saludo rioplatense (Tarea 4)
            if manejar_saludo(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 2. Si hay una propuesta pendiente y el usuario menciona una billetera, corregirla determinísticamente
            # Evaluado antes de cancelación para que "no, fue en Mercado Pago" o "no fue en galicia" corrijan y no cancelen
            propuesta_pendiente = _buscar_propuesta_pendiente(usuario.id, db)
            conv_activa = _buscar_slot_filling_activo(usuario.id, db)
            if propuesta_pendiente and not conv_activa:
                es_corr, b_nueva, cands, err_moneda = _evaluar_correccion_billetera(
                    mensaje_texto, usuario.id, propuesta_pendiente, db
                )
                if es_corr:
                    if err_moneda:
                        enviar_whatsapp(from_number, err_moneda)
                        return
                    if len(cands) > 1:
                        tipo_prop = propuesta_pendiente.entidades.get("tipo", "egreso")
                        menu = _generar_menu_billeteras(cands, tipo=tipo_prop)
                        propuesta_pendiente.slot_filling_activo = True
                        clave_bill = "billetera_destino" if tipo_prop == "ingreso" else "billetera_origen"
                        propuesta_pendiente.slot_filling_estado = {
                            **propuesta_pendiente.entidades,
                            "datos_faltantes": [clave_bill],
                        }
                        db.commit()
                        enviar_whatsapp(from_number, f"¿A cuál te referís?\n{menu}")
                        return
                    if b_nueva:
                        tipo_prop = propuesta_pendiente.entidades.get("tipo", "egreso")
                        clave_bill = "billetera_destino" if tipo_prop == "ingreso" else "billetera_origen"
                        clave_otra = "billetera_origen" if tipo_prop == "ingreso" else "billetera_destino"

                        entidades_nuevas = dict(propuesta_pendiente.entidades)
                        entidades_nuevas[clave_bill] = b_nueva.nombre
                        entidades_nuevas.pop(clave_otra, None)

                        nuevo_msg = _construir_propuesta_transaccion(
                            entidades_nuevas, b_nueva.nombre, se_asumio_principal=False, billetera_moneda=b_nueva.moneda
                        )

                        propuesta_pendiente.entidades = entidades_nuevas
                        propuesta_pendiente.mensaje_bot = nuevo_msg
                        propuesta_pendiente.fecha = datetime.now(timezone.utc)

                        nueva_conv = ConversacionWpp(
                            usuario_id=usuario.id,
                            wamid=wamid,
                            mensaje_usuario=mensaje_texto,
                            tipo_mensaje=TipoMensajeWpp.TEXTO,
                            transcripcion=None,
                            mensaje_bot=nuevo_msg,
                            intent_detectado="registrar_transaccion",
                            entidades=entidades_nuevas,
                            accion_ejecutada=None,
                            confianza=Decimal("1.000"),
                            slot_filling_activo=False,
                            slot_filling_estado=None,
                        )
                        db.add(nueva_conv)
                        db.commit()
                        enviar_whatsapp(from_number, nuevo_msg)
                        return

            # Chequeo determinístico de cancelación para aporte a meta
            if manejar_cancelacion_aporte_meta(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 3. Chequeo determinístico de cancelación (Tarea 5)
            if manejar_cancelacion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 3. Chequeo de respuesta a verificación de duplicado o lote pendiente
            if manejar_verificaciones_slot_filling(
                mensaje_texto, usuario, db, from_number, wamid=wamid
            ):
                return

            # Chequeo determinístico de confirmación para aporte a meta
            if manejar_confirmacion_aporte_meta(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 4. Chequeo determinístico de confirmación con bloqueo de concurrencia (Tarea 2)
            if manejar_confirmacion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 4. Buscar conversación activa previa con slot_filling dentro del plazo (Tarea 2)
            conv_activa = _buscar_slot_filling_activo(usuario.id, db)
            conv_vencida = _buscar_slot_filling_vencido(usuario.id, db) if not conv_activa else None

            # Si hay una conversación vencida, apagarla en base
            if conv_vencida:
                conv_vencida.slot_filling_activo = False
                conv_vencida.accion_ejecutada = "vencida"
                db.flush()
                # Si el usuario mandó una respuesta numérica o intenta responder al menú vencido
                if mensaje_texto.strip().isdigit() or _es_pregunta_billetera(conv_vencida):
                    msg_vencida = "Esa operación ya venció. Podés volver a mandarla."
                    nueva_conv = ConversacionWpp(
                        usuario_id=usuario.id,
                        wamid=wamid,
                        mensaje_usuario=mensaje_texto,
                        tipo_mensaje=TipoMensajeWpp.TEXTO,
                        transcripcion=None,
                        mensaje_bot=msg_vencida,
                        intent_detectado="slot_filling",
                        entidades={},
                        accion_ejecutada=None,
                        confianza=Decimal("1.000"),
                        slot_filling_activo=False,
                        slot_filling_estado=None,
                    )
                    db.add(nueva_conv)
                    db.commit()
                    enviar_whatsapp(from_number, msg_vencida)
                    return

            estado_previo = (
                dict(conv_activa.slot_filling_estado)
                if conv_activa and conv_activa.slot_filling_estado
                else None
            )

            # 5. Si hay pregunta de billetera pendiente activa, resolver selección numérica o por nombre
            if manejar_menu_billetera(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
            ):
                return

            # 5.1 Si hay pregunta de tarjeta pendiente activa
            if manejar_menu_tarjeta(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa
            ):
                return
            # 5.2 Si hay aclaración de cuotas pendiente activa ("¿Los $80.000 son el total o el valor de cada cuota?")
            if manejar_aclaracion_cuotas(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa
            ):
                return
            # 7. Si el mensaje es únicamente un número sin pregunta pendiente
            if manejar_numero_aislado(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
            ):
                return

            # 7.5 Detección determinística de deshacer (Tarea 2)
            if manejar_deshacer(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.6 Detección determinística de corregir (Tarea 3)
            if manejar_corregir(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return
            # 7.7 Detección determinística de transferencias / cajero / dólares (Punto 9B)
            if manejar_transferencias(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
            ):
                return

            # 7.7b Detección determinística de aporte a meta
            if manejar_aporte_meta(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
            ):
                return

            # 7.8 Detección determinística de consultas de suscripciones (Tarea 7)
            if manejar_consulta_suscripciones(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.9 Detección determinística de baja de suscripciones (Tarea 5)
            if manejar_baja_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.10 Detección determinística de cambio de precio de suscripción (Tarea 6)
            if manejar_cambio_precio_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.11 Detección determinística de ambigüedad suscripción vs gasto suelto (Tarea 3.4)
            if manejar_ambiguedad_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.12 Detección determinística de alta de suscripción (Tarea 4)
            if manejar_alta_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            if manejar_consulta_gastos(mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa):
                return

            # 8. Procesamiento normal de IA
            t_ia_start = time.perf_counter()
            resultado_ia = ai_service.procesar_mensaje(
                mensaje=mensaje_texto,
                usuario=usuario,
                db=db,
                historial=_obtener_historial_reciente(usuario.id, db),
                estado_previo=estado_previo,
            )
            t_ia_end = time.perf_counter()
            logger.info("[LATENCIA][IA] Procesamiento: %.2fs", t_ia_end - t_ia_start)

            # Si el intent es desconocido o la confianza es baja (< 0.60), dar respuesta clara con ejemplo
            intent_ia_raw = resultado_ia.get("intent")
            confianza_ia_raw = float(resultado_ia.get("confianza", 1.0))

            # Salvaguarda: si la IA clasifica como deshacer pero el mensaje contiene monto y no es frase de deshacer
            if intent_ia_raw == "deshacer" and not _es_pedido_deshacer(mensaje_texto) and resultado_ia.get("entidades", {}).get("monto"):
                resultado_ia["intent"] = "registrar_transaccion"
                intent_ia_raw = "registrar_transaccion"

            if intent_ia_raw == "desconocido" or confianza_ia_raw < 0.60:
                resp_ia = resultado_ia.get("respuesta_usuario") or ""
                if any(k in resp_ia.lower() for k in ["límite", "limite", "tandas"]):
                    resultado_ia["respuesta_usuario"] = MSG_TOPE_MOVIMIENTOS_SUPERADO
                    resultado_ia["intent"] = "tope_superado"
                    resultado_ia["slot_filling"] = False
                elif any(k in resp_ia.lower() for k in ["separad", "transferenc"]):
                    resultado_ia["respuesta_usuario"] = MSG_NO_MEZCLAR_TRANSFERENCIAS
                    resultado_ia["intent"] = "no_mezclar_transferencias"
                    resultado_ia["slot_filling"] = False
                else:
                    msg_desc = (
                        "No entendí ese mensaje. Por ahora puedo registrar gastos e ingresos, "
                        "o consultar tus saldos y proyecciones. Por ejemplo: 'gasté 5000 en el kiosco' o 'cuánta plata tengo'."
                    )
                    resultado_ia["respuesta_usuario"] = msg_desc
                    resultado_ia["intent"] = "desconocido"
                    resultado_ia["slot_filling"] = False

            # Detección de cambio de tema con nueva operación (Tarea 3 / Cierre Punto 4)
            aviso_cambio_tema = None
            entidades_ia = resultado_ia.get("entidades") or {}
            if resultado_ia.get("intent") in ("registrar_transaccion", "slot_filling"):
                monto_ia = entidades_ia.get("monto")
                cat_ia = entidades_ia.get("categoria")
                if conv_activa and conv_activa.slot_filling_estado:
                    monto_prev = conv_activa.slot_filling_estado.get("monto")
                    cat_prev = conv_activa.slot_filling_estado.get("categoria")
                    if monto_ia is not None and (monto_ia != monto_prev or (cat_ia and cat_ia != cat_prev)):
                        # Es una nueva operación: descartar estado previo y preparar aviso
                        conv_activa.slot_filling_activo = False
                        conv_activa.accion_ejecutada = "descartada_por_nueva_operacion"
                        db.flush()
                        estado_previo = None

                        mon_prev = conv_activa.slot_filling_estado.get("moneda", "ARS")
                        mon_prev_enum = Moneda.USD if mon_prev == "USD" else Moneda.ARS
                        cat_prev_disp = _nombre_corto_categoria(cat_prev) if cat_prev else ""
                        monto_prev_fmt = formatear_monto(float(monto_prev), mon_prev_enum) if monto_prev is not None else ""

                        mon_nuevo = entidades_ia.get("moneda", "ARS")
                        mon_nuevo_enum = Moneda.USD if mon_nuevo == "USD" else Moneda.ARS
                        cat_nuevo_disp = _nombre_corto_categoria(cat_ia) if cat_ia else ""
                        monto_nuevo_fmt = formatear_monto(float(monto_ia), mon_nuevo_enum) if monto_ia is not None else ""

                        if cat_prev_disp and cat_nuevo_disp:
                            aviso_cambio_tema = f"Descarté la de {monto_prev_fmt} en {cat_prev_disp}. Para los {monto_nuevo_fmt} en {cat_nuevo_disp}:"
                        elif cat_prev_disp:
                            aviso_cambio_tema = f"Descarté la de {monto_prev_fmt} en {cat_prev_disp}. Para los {monto_nuevo_fmt}:"
                        else:
                            aviso_cambio_tema = f"Descarté la operación anterior de {monto_prev_fmt}."

            # Mergear determinísticamente entidades para no perder campos de turnos previos si corresponde
            if estado_previo:
                resultado_ia["entidades"] = _merge_entidades(
                    estado_previo,
                    resultado_ia.get("entidades", {}),
                    intent_nuevo=resultado_ia.get("intent"),
                )

            if conv_activa and conv_activa.slot_filling_estado and isinstance(resultado_ia.get("entidades"), dict):
                for marca in ("origen_imagen", "confianza_baja"):
                    if conv_activa.slot_filling_estado.get(marca):
                        resultado_ia["entidades"][marca] = True

            if confianza_ia_raw < 0.85 and isinstance(resultado_ia.get("entidades"), dict):
                resultado_ia["entidades"]["confianza_baja"] = True

            entidades_actuales = resultado_ia.get("entidades", {})

            # 5. Resolución determinística de billetera para movimientos (Tareas 2, 3, 4, 8)
            tipo_act = entidades_actuales.get("tipo") or "egreso"
            clave_bill = "billetera_destino" if tipo_act == "ingreso" else "billetera_origen"
            clave_otra = "billetera_origen" if tipo_act == "ingreso" else "billetera_destino"

            if resultado_ia.get("intent") in ("registrar_transaccion", "slot_filling") or entidades_actuales.get("monto") is not None:
                tarjetas_usuario = _obtener_tarjetas_activas(usuario.id, db)
                m_norm = normalizar_texto(mensaje_texto)

                adicionales = entidades_actuales.get("transacciones_adicionales")
                es_lote = bool(adicionales and isinstance(adicionales, list) and len(adicionales) > 0)

                if es_lote:
                    total_movimientos = 1 + len(adicionales)
                    if total_movimientos > MAX_MOVIMIENTOS_POR_LOTE:
                        resultado_ia["intent"] = "tope_superado"
                        resultado_ia["slot_filling"] = False
                        resultado_ia["respuesta_usuario"] = MSG_TOPE_MOVIMIENTOS_SUPERADO
                        resultado_ia["entidades"] = {}
                    elif PATRON_BLOQUEO_MEZCLA.search(m_norm):
                        resultado_ia["intent"] = "mezcla_transferencia_invalida"
                        resultado_ia["slot_filling"] = False
                        resultado_ia["respuesta_usuario"] = MSG_NO_MEZCLAR_TRANSFERENCIAS
                        resultado_ia["entidades"] = {}
                    else:
                        billeteras_todas = _obtener_billeteras_activas(usuario.id, db)
                        operaciones = []

                        op_0 = {
                            "id": 0,
                            "monto": entidades_actuales.get("monto"),
                            "tipo": entidades_actuales.get("tipo", "egreso"),
                            "moneda": entidades_actuales.get("moneda", "ARS"),
                            "categoria": entidades_actuales.get("categoria"),
                            "descripcion": entidades_actuales.get("descripcion"),
                            "fecha": entidades_actuales.get("fecha"),
                            "billetera": entidades_actuales.get("billetera") or entidades_actuales.get("billetera_origen") or entidades_actuales.get("billetera_destino"),
                            "tarjeta": entidades_actuales.get("tarjeta"),
                            "resuelta": False,
                        }
                        operaciones.append(op_0)

                        for idx, ad in enumerate(adicionales, 1):
                            if isinstance(ad, dict):
                                op_ad = {
                                    "id": idx,
                                    "monto": ad.get("monto"),
                                    "tipo": ad.get("tipo", "egreso"),
                                    "moneda": ad.get("moneda", "ARS"),
                                    "categoria": ad.get("categoria"),
                                    "descripcion": ad.get("descripcion"),
                                    "fecha": ad.get("fecha"),
                                    "billetera": ad.get("billetera") or ad.get("billetera_origen") or ad.get("billetera_destino"),
                                    "tarjeta": ad.get("tarjeta"),
                                    "resuelta": False,
                                }
                                operaciones.append(op_ad)

                        for op in operaciones:
                            if op.get("tarjeta") and tarjetas_usuario:
                                t_match, _ = resolver_tarjeta_cascada(op["tarjeta"], tarjetas_usuario)
                                if t_match:
                                    op["tarjeta_id"] = str(t_match.id)
                                    op["tarjeta"] = t_match.nombre
                                    op["resuelta"] = True
                                    continue

                            mon_op = Moneda.USD if op.get("moneda") == "USD" else Moneda.ARS
                            bills_mon = [b for b in billeteras_todas if b.moneda == mon_op]

                            if op.get("billetera"):
                                b_match, _ = resolver_billetera_cascada(op["billetera"], bills_mon)
                                if b_match:
                                    op["billetera_id"] = str(b_match.id)
                                    op["billetera"] = b_match.nombre
                                    op["resuelta"] = True
                                    continue

                            b_ppal = next((b for b in bills_mon if b.es_principal), None)
                            if b_ppal:
                                op["billetera_id"] = str(b_ppal.id)
                                op["billetera"] = b_ppal.nombre
                                op["se_asumio_principal"] = True
                                op["resuelta"] = True
                            elif len(bills_mon) == 1:
                                op["billetera_id"] = str(bills_mon[0].id)
                                op["billetera"] = bills_mon[0].nombre
                                op["resuelta"] = True
                            else:
                                op["resuelta"] = False

                        unresolved = [
                            op for op in operaciones
                            if not op.get("resuelta") and len([b for b in billeteras_todas if b.moneda == (Moneda.USD if op.get("moneda") == "USD" else Moneda.ARS)]) > 1
                        ]

                        if unresolved:
                            mon_r = unresolved[0].get("moneda", "ARS")
                            tipo_r = unresolved[0].get("tipo", "egreso")
                            todas_iguales = all(op.get("moneda", "ARS") == mon_r and op.get("tipo", "egreso") == tipo_r for op in unresolved)
                            bills_r = [b for b in billeteras_todas if b.moneda == (Moneda.USD if mon_r == "USD" else Moneda.ARS)]

                            if todas_iguales:
                                enc = "¿A qué billetera entraron los ingresos?" if tipo_r == "ingreso" else "¿Desde qué billetera salieron los gastos?"
                                pregunta = _generar_menu_billeteras(bills_r, tipo=tipo_r, encabezado=enc)
                                pend_ids = [op["id"] for op in unresolved]
                            else:
                                op_u = unresolved[0]
                                m_fmt = formatear_monto(float(op_u["monto"]), Moneda.USD if mon_r == "USD" else Moneda.ARS)
                                c_disp = _nombre_corto_categoria(op_u.get("categoria"))
                                if tipo_r == "ingreso":
                                    enc = f"¿A qué billetera entró el ingreso de {m_fmt} en {c_disp}?"
                                else:
                                    enc = f"¿Desde qué billetera salió el gasto de {m_fmt} en {c_disp}?"
                                pregunta = _generar_menu_billeteras(bills_r, tipo=tipo_r, encabezado=enc)
                                pend_ids = [op_u["id"]]

                            resultado_ia["intent"] = "slot_filling"
                            resultado_ia["slot_filling"] = True
                            resultado_ia["datos_faltantes"] = ["billetera_lote"]
                            resultado_ia["respuesta_usuario"] = pregunta
                            entidades_actuales["tipo_flujo"] = "lote_slot_filling"
                            entidades_actuales["datos_faltantes"] = ["billetera_lote"]
                            entidades_actuales["operaciones"] = operaciones
                            entidades_actuales["ops_pendientes_ids"] = pend_ids
                            entidades_actuales["moneda"] = mon_r
                            entidades_actuales["tipo"] = tipo_r
                        else:
                            entidades_actuales["billetera"] = operaciones[0].get("billetera")
                            entidades_actuales["tarjeta"] = operaciones[0].get("tarjeta")
                            entidades_actuales["transacciones_adicionales"] = [dict(o) for o in operaciones[1:]]

                            hay_lote_dup, m_dup, mon_dup, cat_dup = _detectar_duplicados_en_lote(entidades_actuales)
                            if hay_lote_dup:
                                b_nom_dup = operaciones[0].get("billetera") or "tu billetera"
                                m_dup_fmt = formatear_monto(float(m_dup), Moneda.USD if mon_dup == "USD" else Moneda.ARS)
                                pregunta_lote = f"Mandaste 2 movimientos iguales de {m_dup_fmt} en {cat_dup} desde {b_nom_dup}. ¿Son dos gastos distintos o se te repitió?"
                                resultado_ia["intent"] = "verificar_lote_duplicado"
                                resultado_ia["slot_filling"] = True
                                resultado_ia["datos_faltantes"] = ["confirmar_lote"]
                                entidades_actuales["tipo_flujo"] = "verificacion_lote_duplicado"
                                entidades_actuales["billetera_resuelta_nombre"] = b_nom_dup
                                resultado_ia["respuesta_usuario"] = pregunta_lote
                            else:
                                limite_rec = datetime.now(timezone.utc) - timedelta(hours=1)
                                txs_hist = db.execute(
                                    select(Transaccion).where(
                                        Transaccion.usuario_id == usuario.id,
                                        Transaccion.fecha_creacion >= limite_rec,
                                        Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                                        Transaccion.es_cuota_hija == False,
                                        Transaccion.es_padre_cuotas == False,
                                        Transaccion.suscripcion_id.is_(None),
                                        Transaccion.pago_origen_id.is_(None),
                                        Transaccion.pago_resumen_vencimiento.is_(None),
                                    ).order_by(Transaccion.fecha_creacion.desc(), Transaccion.id.desc())
                                ).scalars().all()

                                tx_dup_found = None
                                op_dup_found = None
                                for op in operaciones:
                                    cat_id_chk, _ = _resolver_categoria_y_subcategoria(
                                        op.get("categoria"), usuario.id, db, tipo=op.get("tipo", "egreso")
                                    )
                                    mon_chk = Moneda.USD if op.get("moneda") == "USD" else Moneda.ARS
                                    m_chk = Decimal(str(op["monto"]))
                                    for th in txs_hist:
                                        if th.monto == m_chk and th.moneda == mon_chk and th.categoria_id == cat_id_chk:
                                            tx_dup_found = th
                                            op_dup_found = op
                                            break
                                    if tx_dup_found:
                                        break

                                if tx_dup_found and op_dup_found:
                                    hora_dup = tx_dup_found.fecha_creacion.astimezone(TZ_ARGENTINA).strftime("%H:%M")
                                    cat_disp = _nombre_corto_categoria(op_dup_found.get("categoria"))
                                    m_fmt = formatear_monto(float(op_dup_found["monto"]), Moneda.USD if op_dup_found.get("moneda") == "USD" else Moneda.ARS)
                                    pregunta_dup = f"A las {hora_dup} ya registraste {m_fmt} en {cat_disp}. ¿Es un movimiento nuevo o se te repitió?"
                                    resultado_ia["intent"] = "verificar_duplicado"
                                    resultado_ia["slot_filling"] = True
                                    resultado_ia["datos_faltantes"] = ["confirmar_duplicado"]
                                    entidades_actuales["tipo_flujo"] = "verificacion_duplicado"
                                    entidades_actuales["billetera_resuelta_nombre"] = op_dup_found.get("billetera") or "tu billetera"
                                    entidades_actuales["hora_anterior"] = hora_dup
                                    resultado_ia["respuesta_usuario"] = pregunta_dup
                                else:
                                    resultado_ia["intent"] = "registrar_transaccion"
                                    resultado_ia["slot_filling"] = False
                                    if confianza_ia_raw < 0.85:
                                        entidades_actuales["confianza_baja"] = True
                                    resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                                    resultado_ia["_asumio_principal"] = bool(operaciones[0].get("se_asumio_principal", False))
                                    resultado_ia["respuesta_usuario"] = _construir_propuesta_transaccion(
                                        entidades_actuales,
                                        billetera_nombre=operaciones[0].get("billetera"),
                                        se_asumio_principal=operaciones[0].get("se_asumio_principal", False),
                                        billeteras_usuario=billeteras_todas,
                                    )
                                    if "No se puede registrar ningún movimiento." in resultado_ia["respuesta_usuario"]:
                                        resultado_ia["intent"] = "desconocido"
                                        resultado_ia["slot_filling"] = False
                else:
                    # Chequear si fuerza débito (Tarea 4.4)
                    es_debito_explicito = any(d in m_norm for d in PALABRAS_FUERZAN_DEBITO)

                    # Chequear intención de crédito (Tareas 3 y 4)
                    tiene_cuotas = any(c in m_norm for c in ["cuota", "cuotas", "en cuotas", "pagos"])
                    tiene_palabras_credito = any(w in m_norm for w in PALABRAS_FUERZAN_CREDITO)
                    tiene_mencion_tarjeta = any(g in m_norm for g in FORMAS_GENERICAS_TARJETA)

                    # Tarjetas candidatas específicas por mención en el mensaje
                    if not es_debito_explicito and tarjetas_usuario:
                        tarjeta_match_mencion, tarjeta_mencionada_cands = _resolver_mencion_tarjeta_en_texto(
                            mensaje_texto, tarjetas_usuario
                        )
                    else:
                        tarjeta_match_mencion, tarjeta_mencionada_cands = None, []

                    # Evaluar colisión con billeteras (Tarea 3)
                    billeteras_todas = _obtener_billeteras_activas(usuario.id, db)
                    bill_raw_cands = []
                    for b in billeteras_todas:
                        b_nom_norm = normalizar_texto(b.nombre)
                        if b_nom_norm in m_norm:
                            bill_raw_cands.append(b)

                    # Si matchea a la vez billetera y tarjeta sin palabra que fuerce crédito (Tarea 3.2)
                    hay_colision_sin_credito = (
                        not es_debito_explicito
                        and not tiene_cuotas
                        and not tiene_palabras_credito
                        and len(bill_raw_cands) == 1
                        and len(tarjeta_mencionada_cands) == 1
                        and normalizar_texto(bill_raw_cands[0].nombre) == normalizar_texto(tarjeta_mencionada_cands[0].nombre)
                    )

                    if hay_colision_sin_credito:
                        b_col = bill_raw_cands[0]
                        t_col = tarjeta_mencionada_cands[0]
                        resultado_ia["intent"] = "slot_filling"
                        resultado_ia["slot_filling"] = True
                        resultado_ia["datos_faltantes"] = ["billetera_o_tarjeta"]
                        entidades_actuales["datos_faltantes"] = ["billetera_o_tarjeta"]
                        resultado_ia["respuesta_usuario"] = (
                            f"¿Te referís a la billetera o a la tarjeta de crédito?\n"
                            f"1. Billetera {b_col.nombre}\n"
                            f"2. Tarjeta de crédito {t_col.nombre}"
                        )
                        es_credito = False
                    else:
                        es_credito = (not es_debito_explicito) and (
                            tiene_cuotas
                            or tiene_palabras_credito
                            or tiene_mencion_tarjeta
                            or len(tarjeta_mencionada_cands) > 0
                            or entidades_actuales.get("tarjeta_id") is not None
                            or entidades_actuales.get("tarjeta") is not None
                        )

                    if hay_colision_sin_credito:
                        pass
                    elif es_credito:
                        # 1. Si no tiene ninguna tarjeta cargada (Tarea 4.3)
                        if not tarjetas_usuario:
                            resultado_ia["respuesta_usuario"] = (
                                "No tenés ninguna tarjeta de crédito cargada en Argentum. "
                                "Podés agregarla desde la web, o registrar este movimiento como un gasto común con alguna de tus billeteras."
                            )
                            resultado_ia["intent"] = "sin_tarjetas"
                            resultado_ia["slot_filling"] = False
                            resultado_ia["datos_faltantes"] = []
                        elif entidades_actuales.get("monto") is not None:
                            monto_actual_val = Decimal(str(entidades_actuales["monto"]))
                            cant_cuotas, monto_cuota, monto_total, es_ambiguo, err_msg = _interpretar_cuotas(
                                mensaje_texto, monto_actual_val
                            )

                            if err_msg:
                                resultado_ia["respuesta_usuario"] = err_msg
                                resultado_ia["intent"] = "error_cuotas"
                                resultado_ia["slot_filling"] = False
                                resultado_ia["datos_faltantes"] = []
                            elif es_ambiguo:
                                m_fmt = formatear_monto(float(monto_actual_val), Moneda.ARS)
                                resultado_ia["respuesta_usuario"] = f"¿Los {m_fmt} son el total o el valor de cada cuota?"
                                resultado_ia["intent"] = "slot_filling"
                                resultado_ia["slot_filling"] = True
                                resultado_ia["datos_faltantes"] = ["aclarar_cuotas"]
                                entidades_actuales["datos_faltantes"] = ["aclarar_cuotas"]
                                entidades_actuales["cantidad_cuotas"] = cant_cuotas
                                entidades_actuales["monto"] = float(monto_actual_val)
                            else:
                                # Resolver tarjeta (Tareas 2.4, 2.5)
                                t_match = None
                                cands_res = []
                                se_asumio_tarjeta = False

                                if tarjeta_match_mencion:
                                    t_match = tarjeta_match_mencion
                                    cands_res = [tarjeta_match_mencion]
                                elif len(tarjeta_mencionada_cands) > 1:
                                    t_match = None
                                    cands_res = tarjeta_mencionada_cands
                                else:
                                    # No nombró tarjeta específica: verificar si dijo forma genérica ("con la tarjeta")
                                    if tiene_mencion_tarjeta or "tarjeta" in m_norm:
                                        if len(tarjetas_usuario) == 1:
                                            t_match = tarjetas_usuario[0]
                                            cands_res = tarjetas_usuario
                                        else:
                                            t_match = None
                                            cands_res = tarjetas_usuario
                                    else:
                                        # No nombró tarjeta en absoluto (ej. "compré una tele en 12 cuotas de 80000")
                                        if len(tarjetas_usuario) == 1:
                                            t_match = tarjetas_usuario[0]
                                            cands_res = tarjetas_usuario
                                            se_asumio_tarjeta = False
                                        else:
                                            t_match = next((t for t in tarjetas_usuario if t.billetera and t.billetera.es_principal), tarjetas_usuario[0])
                                            cands_res = [t_match]
                                            se_asumio_tarjeta = True

                                if len(cands_res) > 1 and not t_match:
                                    resultado_ia["intent"] = "slot_filling"
                                    resultado_ia["slot_filling"] = True
                                    resultado_ia["datos_faltantes"] = ["tarjeta"]
                                    entidades_actuales["datos_faltantes"] = ["tarjeta"]
                                    entidades_actuales["candidatas_tarjetas_ids"] = [str(c.id) for c in cands_res]
                                    entidades_actuales["cantidad_cuotas"] = cant_cuotas
                                    entidades_actuales["monto_cuota"] = float(monto_cuota)
                                    entidades_actuales["monto_total"] = float(monto_total)
                                    entidades_actuales["monto"] = float(monto_total)
                                    resultado_ia["respuesta_usuario"] = _generar_menu_tarjetas(cands_res)
                                elif t_match:
                                    fecha_obj, _ = _resolver_y_validar_fecha(entidades_actuales.get("fecha"))
                                    primer_v = calcular_primer_vencimiento(fecha_obj, t_match.dia_cierre, t_match.dia_vencimiento, False)
                                    entidades_actuales["tarjeta_id"] = str(t_match.id)
                                    entidades_actuales["tarjeta_nombre"] = t_match.nombre
                                    entidades_actuales["tarjeta_billetera_id"] = str(t_match.billetera_id)
                                    entidades_actuales["cantidad_cuotas"] = cant_cuotas
                                    entidades_actuales["monto_cuota"] = float(monto_cuota)
                                    entidades_actuales["monto_total"] = float(monto_total)
                                    entidades_actuales["monto"] = float(monto_total)
                                    if "datos_faltantes" in entidades_actuales:
                                        entidades_actuales["datos_faltantes"] = [d for d in entidades_actuales["datos_faltantes"] if "tarjeta" not in d]

                                    propuesta_cred = _construir_propuesta_credito(
                                        entidades_actuales, t_match, cant_cuotas, monto_cuota, monto_total, primer_v, se_asumio_tarjeta=se_asumio_tarjeta
                                    )
                                    resultado_ia["intent"] = "registrar_transaccion"
                                    resultado_ia["slot_filling"] = False
                                    if confianza_ia_raw < 0.85:
                                        entidades_actuales["confianza_baja"] = True
                                    resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                                    resultado_ia["respuesta_usuario"] = propuesta_cred
                    else:
                        moneda_sol_str = entidades_actuales.get("moneda")
                        moneda_sol = Moneda.USD if moneda_sol_str == "USD" else Moneda.ARS

                        billeteras_moneda = [b for b in billeteras_todas if b.moneda == moneda_sol]
                        if not billeteras_moneda:
                            nom_moneda = "dólares" if moneda_sol == Moneda.USD else "pesos"
                            resultado_ia["respuesta_usuario"] = f"No tenés ninguna billetera en {nom_moneda} para registrar este movimiento. Podés crear una desde la web de Argentum."
                            resultado_ia["intent"] = "sin_billetera_moneda"
                            resultado_ia["slot_filling"] = False
                            resultado_ia["datos_faltantes"] = []
                        elif entidades_actuales.get("monto") is not None:
                            billetera_raw = entidades_actuales.get(clave_bill) or entidades_actuales.get(clave_otra)
                            billetera_final = None
                            se_asumio_principal = False

                            if billetera_raw:
                                b_match, cands = resolver_billetera_cascada(billetera_raw, billeteras_moneda)
                                if b_match:
                                    billetera_final = b_match
                                elif len(cands) > 1:
                                    resultado_ia["intent"] = "slot_filling"
                                    resultado_ia["slot_filling"] = True
                                    resultado_ia["datos_faltantes"] = [clave_bill]
                                    entidades_actuales["datos_faltantes"] = [clave_bill]
                                    entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                                    entidades_actuales[clave_bill] = None
                                    resultado_ia["respuesta_usuario"] = f"¿A cuál te referís?\n{_generar_menu_billeteras(cands, tipo=tipo_act)}"
                                else:
                                    # Verificar si nombró billetera de otra moneda
                                    todas = billeteras_todas
                                    b_otra, _ = resolver_billetera_cascada(billetera_raw, todas)
                                    if b_otra and b_otra.moneda != moneda_sol:
                                        nom_otra = "dólares" if b_otra.moneda == Moneda.USD else "pesos"
                                        nom_mov = "pesos" if moneda_sol == Moneda.ARS else "dólares"
                                        resultado_ia["intent"] = "slot_filling"
                                        resultado_ia["slot_filling"] = True
                                        resultado_ia["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                                        entidades_actuales[clave_bill] = None
                                        menu = _generar_menu_billeteras(billeteras_moneda, tipo=tipo_act)
                                        resultado_ia["respuesta_usuario"] = f"No podés usar una billetera en {nom_otra} para un movimiento en {nom_mov}.\n{menu}"
                                    else:
                                        resultado_ia["intent"] = "slot_filling"
                                        resultado_ia["slot_filling"] = True
                                        resultado_ia["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                                        entidades_actuales[clave_bill] = None
                                        menu = _generar_menu_billeteras(billeteras_moneda, tipo=tipo_act)
                                        resultado_ia["respuesta_usuario"] = f"No encontré esa billetera entre las tuyas.\n\n{menu}"
                            else:
                                # Usuario NO nombró billetera
                                if len(billeteras_moneda) == 1:
                                    # Exactamente una activa en esa moneda: usarla sin preguntar (2.3)
                                    billetera_final = billeteras_moneda[0]
                                    se_asumio_principal = False
                                else:
                                    # Buscar principal en esa moneda (2.2)
                                    b_ppal = next((b for b in billeteras_moneda if b.es_principal), None)
                                    if b_ppal:
                                        billetera_final = b_ppal
                                        se_asumio_principal = True
                                    else:
                                        # NUNCA ELEGIR POR DESCARTE: mostrar menú (2.1, 2.2)
                                        resultado_ia["intent"] = "slot_filling"
                                        resultado_ia["slot_filling"] = True
                                        resultado_ia["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                                        entidades_actuales[clave_bill] = None
                                        resultado_ia["respuesta_usuario"] = _generar_menu_billeteras(billeteras_moneda, tipo=tipo_act)

                            if billetera_final:
                                entidades_actuales[clave_bill] = billetera_final.nombre
                                entidades_actuales.pop(clave_otra, None)
                                if "datos_faltantes" in entidades_actuales:
                                    entidades_actuales["datos_faltantes"] = [
                                        d for d in entidades_actuales["datos_faltantes"]
                                        if d not in ("billetera_origen", "billetera_destino", "billetera")
                                    ]

                                # Chequeo 1: Lote con movimientos idénticos (Tarea 4)
                                hay_lote_dup, m_dup, mon_dup, cat_dup = _detectar_duplicados_en_lote(entidades_actuales)
                                if hay_lote_dup:
                                    m_dup_fmt = formatear_monto(float(m_dup), Moneda.USD if mon_dup == "USD" else Moneda.ARS)
                                    pregunta_lote = f"Mandaste 2 movimientos iguales de {m_dup_fmt} en {cat_dup} desde {billetera_final.nombre}. ¿Son dos gastos distintos o se te repitió?"
                                    resultado_ia["intent"] = "verificar_lote_duplicado"
                                    resultado_ia["slot_filling"] = True
                                    resultado_ia["datos_faltantes"] = ["confirmar_lote"]
                                    entidades_actuales["tipo_flujo"] = "verificacion_lote_duplicado"
                                    entidades_actuales["billetera_resuelta_nombre"] = billetera_final.nombre
                                    resultado_ia["respuesta_usuario"] = pregunta_lote
                                else:
                                    sub_cobrada, tx_cobrada = (None, None)
                                    if tipo_act == "egreso":
                                        sub_cobrada, tx_cobrada = _buscar_suscripcion_cobrada_periodo_actual(
                                            usuario_id=usuario.id,
                                            monto=Decimal(str(entidades_actuales["monto"])),
                                            nombre_servicio_o_concepto=entidades_actuales.get("servicio") or entidades_actuales.get("descripcion") or entidades_actuales.get("concepto") or mensaje_texto,
                                            db=db,
                                        )
                                    if sub_cobrada and tx_cobrada:
                                        m_fmt = formatear_monto(float(tx_cobrada.monto), tx_cobrada.moneda)
                                        aviso_dup_sub = f"Aviso: ya se cobró automáticamente {m_fmt} de {sub_cobrada.nombre} este período. ¿Es un gasto aparte o querés cancelarlo?"
                                        resultado_ia["intent"] = "verificar_duplicado_suscripcion"
                                        resultado_ia["slot_filling"] = True
                                        resultado_ia["datos_faltantes"] = ["confirmar_gasto_aparte"]
                                        entidades_actuales["tipo_flujo"] = "verificacion_duplicado_suscripcion"
                                        entidades_actuales["suscripcion_id"] = str(sub_cobrada.id)
                                        entidades_actuales["servicio_nombre"] = sub_cobrada.nombre
                                        entidades_actuales["billetera_resuelta_nombre"] = billetera_final.nombre
                                        resultado_ia["respuesta_usuario"] = aviso_dup_sub
                                    else:
                                        # Chequeo 2: Transacción previa en la última hora (Tarea 3)
                                        cat_id_chk, _ = _resolver_categoria_y_subcategoria(
                                            entidades_actuales.get("categoria"), usuario.id, db, tipo=tipo_act
                                        )
                                        tx_dup = _buscar_transaccion_duplicada_reciente(
                                            usuario_id=usuario.id,
                                            monto=Decimal(str(entidades_actuales["monto"])),
                                            moneda=moneda_sol,
                                            categoria_id=cat_id_chk,
                                            db=db,
                                        )
                                        if tx_dup:
                                            hora_dup = tx_dup.fecha_creacion.astimezone(TZ_ARGENTINA).strftime("%H:%M")
                                            cat_disp = _nombre_corto_categoria(entidades_actuales.get("categoria"))
                                            m_fmt = formatear_monto(float(entidades_actuales["monto"]), moneda_sol)
                                            pregunta_dup = f"A las {hora_dup} ya registraste {m_fmt} en {cat_disp}. ¿Es un movimiento nuevo o se te repitió?"
                                            resultado_ia["intent"] = "verificar_duplicado"
                                            resultado_ia["slot_filling"] = True
                                            resultado_ia["datos_faltantes"] = ["confirmar_duplicado"]
                                            entidades_actuales["tipo_flujo"] = "verificacion_duplicado"
                                            entidades_actuales["billetera_resuelta_nombre"] = billetera_final.nombre
                                            entidades_actuales["hora_anterior"] = hora_dup
                                            resultado_ia["respuesta_usuario"] = pregunta_dup
                                        else:
                                            resultado_ia["intent"] = "registrar_transaccion"
                                            resultado_ia["slot_filling"] = False
                                            if confianza_ia_raw < 0.85:
                                                entidades_actuales["confianza_baja"] = True
                                            resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                                            resultado_ia["_asumio_principal"] = se_asumio_principal
                                            resultado_ia["respuesta_usuario"] = _construir_propuesta_transaccion(
                                                entidades_actuales,
                                                billetera_final.nombre,
                                                se_asumio_principal=se_asumio_principal,
                                                billetera_moneda=billetera_final.moneda,
                                            )
                                            if "No se puede registrar ningún movimiento." in resultado_ia["respuesta_usuario"]:
                                                resultado_ia["intent"] = "desconocido"
                                                resultado_ia["slot_filling"] = False

            # Enriquecer respuesta con datos reales para intents de consulta
            intent_detectado = resultado_ia.get("intent")

            intent_detectado = enriquecer_respuesta_por_intent(
                intent_detectado, resultado_ia, mensaje_texto, usuario, db
            )

            if intent_detectado == "aportar_meta":
                entidades_ia = resultado_ia.get("entidades") or {}
                monto_ia = entidades_ia.get("monto")
                meta_ia = entidades_ia.get("meta") or entidades_ia.get("descripcion")
                bill_ia = entidades_ia.get("billetera_origen") or entidades_ia.get("billetera")
                monto_val = Decimal(str(monto_ia)) if monto_ia else None
                meta_obj, estado_meta, cands = _buscar_meta_activa_por_nombre(usuario.id, meta_ia, db)
                if estado_meta == "no_metas":
                    resultado_ia["respuesta_usuario"] = "No tenés metas activas."
                    resultado_ia["slot_filling"] = False
                elif estado_meta == "no_encontrada":
                    nombre_disp = meta_ia or "esa"
                    resultado_ia["respuesta_usuario"] = f"No encontré ninguna meta con el nombre '{nombre_disp}'. Podés consultar tus metas con 'mis metas'."
                    resultado_ia["slot_filling"] = False
                elif estado_meta == "ambigua":
                    nombres = ", ".join(f"'{m.nombre}'" for m in cands)
                    resultado_ia["respuesta_usuario"] = f"Encontré más de una meta parecida: {nombres}. ¿A cuál querés aportar?"
                    resultado_ia["slot_filling"] = True
                    resultado_ia["entidades"]["intent_origen"] = "aportar_meta"
                    resultado_ia["entidades"]["meta_ambigua_cands"] = [str(m.id) for m in cands]
                    if monto_val:
                        resultado_ia["entidades"]["monto"] = float(monto_val)
                elif meta_obj and monto_val:
                    bills_mon = _obtener_billeteras_activas(usuario.id, db, moneda=meta_obj.moneda)
                    billetera_obj = None
                    if bill_ia:
                        billetera_obj, _ = resolver_billetera_cascada(bill_ia, bills_mon)
                    if not billetera_obj:
                        billetera_obj = next((b for b in bills_mon if b.es_principal), None) or (bills_mon[0] if len(bills_mon) == 1 else None)
                    if not billetera_obj:
                        enc = f"¿Desde qué billetera querés aportar a '{meta_obj.nombre}'?"
                        resultado_ia["respuesta_usuario"] = _generar_menu_billeteras(bills_mon, tipo="egreso", encabezado=enc)
                        resultado_ia["slot_filling"] = True
                    elif billetera_obj.saldo_actual < monto_val:
                        monto_disp = formatear_monto(float(billetera_obj.saldo_actual), billetera_obj.moneda)
                        resultado_ia["respuesta_usuario"] = f"Saldo insuficiente en la billetera '{billetera_obj.nombre}'. Tenés {monto_disp}."
                        resultado_ia["slot_filling"] = False
                    else:
                        monto_fmt = formatear_monto(float(monto_val), meta_obj.moneda)
                        resultado_ia["respuesta_usuario"] = f"Voy a aportar {monto_fmt} a tu meta '{meta_obj.nombre}' desde {billetera_obj.nombre}. ¿Confirmás?"
                        resultado_ia["entidades"] = {
                            "meta_id": str(meta_obj.id),
                            "meta_nombre": meta_obj.nombre,
                            "billetera_id": str(billetera_obj.id),
                            "billetera_nombre": billetera_obj.nombre,
                            "monto": float(monto_val),
                            "moneda": meta_obj.moneda.value,
                        }
                        resultado_ia["slot_filling"] = False

            transaccion_id = _ejecutar_intent(resultado_ia, usuario, db)

            # Si se confirmó o intentó confirmar, usar el mensaje del gestor de confirmación
            if intent_detectado == "confirmar" and resultado_ia.get("_mensaje_confirmacion_directo"):
                resultado_ia["respuesta_usuario"] = resultado_ia["_mensaje_confirmacion_directo"]

            # Si se canceló, asegurar tono rioplatense
            if intent_detectado == "cancelar":
                resultado_ia["respuesta_usuario"] = "Listo, cancelado."

            # Si hubo descarte por cambio de tema, anteponer aviso en una línea (Cierre Punto 4)
            if aviso_cambio_tema and resultado_ia.get("respuesta_usuario"):
                resp_actual = resultado_ia["respuesta_usuario"]
                if resp_actual.startswith("¿"):
                    resultado_ia["respuesta_usuario"] = f"{aviso_cambio_tema}\n\n{resp_actual}"
                else:
                    resultado_ia["respuesta_usuario"] = f"{aviso_cambio_tema}\n{resp_actual}"

            slot_activo = resultado_ia.get("slot_filling", False)
            confianza_val = resultado_ia.get("confianza", 0.0)
            try:
                confianza_float = max(0.0, min(1.0, float(confianza_val)))
                confianza_dec = Decimal(f"{confianza_float:.3f}")
            except (ValueError, TypeError):
                confianza_dec = Decimal("0.000")

            if es_imagen:
                tipo_msg_guardar = TipoMensajeWpp.IMAGEN
                mensaje_usuario_guardar = caption_imagen
                transcripcion_guardar = mensaje_texto
                if isinstance(resultado_ia.get("entidades"), dict):
                    resultado_ia["entidades"]["origen_imagen"] = True
            elif transcripcion:
                tipo_msg_guardar = TipoMensajeWpp.AUDIO
                mensaje_usuario_guardar = transcripcion
                transcripcion_guardar = transcripcion
            else:
                tipo_msg_guardar = TipoMensajeWpp.TEXTO
                mensaje_usuario_guardar = mensaje_texto
                transcripcion_guardar = None

            nueva_conv = ConversacionWpp(
                usuario_id=usuario.id,
                wamid=wamid,
                mensaje_usuario=mensaje_usuario_guardar,
                tipo_mensaje=tipo_msg_guardar,
                transcripcion=transcripcion_guardar,
                mensaje_bot=resultado_ia["respuesta_usuario"],
                intent_detectado=resultado_ia.get("intent"),
                entidades=resultado_ia.get("entidades"),
                accion_ejecutada=str(transaccion_id) if transaccion_id else None,
                confianza=confianza_dec,
                slot_filling_activo=slot_activo,
                slot_filling_estado=resultado_ia.get("entidades") if slot_activo else None,
            )
            db.add(nueva_conv)
            db.flush()

            es_dup = bool(resultado_ia.get("intent") in ("verificar_duplicado", "verificar_lote_duplicado", "verificar_duplicado_suscripcion"))
            asumio_ppal = bool(resultado_ia.get("_asumio_principal", False))
            registrado_dir, msg_dir = _registrar_directo_si_corresponde(
                usuario,
                db,
                nueva_conv,
                es_credito=es_credito,
                es_imagen=es_imagen,
                es_duplicado=es_dup,
                se_asumio_principal=asumio_ppal,
            )
            if registrado_dir or nueva_conv.accion_ejecutada == "error":
                resultado_ia["respuesta_usuario"] = msg_dir
            else:
                db.commit()

            # Envío saliente vía Meta Graph API
            t_envio_start = time.perf_counter()
            enviar_whatsapp(from_number, resultado_ia["respuesta_usuario"])
            t_envio_end = time.perf_counter()
            logger.info("[LATENCIA][ENVIO_META] Envío de mensaje: %.2fs", t_envio_end - t_envio_start)

            t_total = time.perf_counter() - t_inicio
            logger.info(
                "[LATENCIA][TOTAL][TIPO=%s] Duración total del webhook: %.2fs",
                msg_type.upper(),
                t_total,
            )

            return

        except Exception as e:
            db.rollback()
            logger.error("whatsapp_webhook_error", error=str(e), exc_info=True)
            try:
                if from_number:
                    enviar_whatsapp(
                        from_number, "Hubo un problema al procesar tu mensaje. Intentá de nuevo."
                    )
            except Exception:
                logger.exception("Error al enviar mensaje de fallback")
            return

    finally:
        db.close()


def _procesar_webhook_whatsapp_sync(body_bytes: bytes, t_inicio: float) -> PlainTextResponse:
    """
    Wrapper de compatibilidad para suites de regresión que ejecutan de forma síncrona.
    """
    resp, datos_mensaje = _preprocesar_webhook_whatsapp_sync(body_bytes, t_inicio)
    if datos_mensaje:
        _procesar_mensaje_whatsapp_background(datos_mensaje)
    return resp


async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> PlainTextResponse:
    """
    Webhook de WhatsApp Cloud API (Meta Graph API).
    Valida firma HMAC-SHA256 en el event loop, ejecuta la deduplicación de forma síncrona
    en thread worker (Parte A) respondiendo inmediatamente HTTP 200, y delega el procesamiento
    pesado de IA, transcripción y envío (Parte B) a BackgroundTasks.
    """
    t_inicio = time.perf_counter()
    body_bytes = await request.body()

    # Validación de firma Meta HMAC-SHA256 obligatoria (Fail-Closed)
    if not settings.WHATSAPP_APP_SECRET:
        logger.error("WHATSAPP_APP_SECRET no configurado en el servidor")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma inválida o no configurada",
        )

    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Falta o es inválido el header X-Hub-Signature-256")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma inválida",
        )

    expected_sig = signature_header.split("sha256=", 1)[1]
    calculated_sig = hmac.new(
        settings.WHATSAPP_APP_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, calculated_sig):
        logger.warning("Firma Meta HMAC-SHA256 no coincide")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma inválida",
        )

    resp, datos_mensaje = await anyio.to_thread.run_sync(
        _preprocesar_webhook_whatsapp_sync, body_bytes, t_inicio
    )
    if datos_mensaje:
        background_tasks.add_task(_procesar_mensaje_whatsapp_background, datos_mensaje)
    return resp


class TestIAMessageRequest(BaseModel):
    mensaje: str



def test_ia(
    body: TestIAMessageRequest | None = None,
    mensaje: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
) -> dict:
    texto = (body.mensaje if body else None) or mensaje
    if not texto:
        raise HTTPException(status_code=400, detail="Debe ingresar un mensaje para probar la IA.")
    return ai_service.procesar_mensaje(
        mensaje=texto,
        usuario=current_user,
        db=db,
        historial=None,
        estado_previo=None,
    )
