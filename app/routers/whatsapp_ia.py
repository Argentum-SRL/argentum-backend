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
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.etapa_entrada import (
    procesar_entrada_medios_y_texto,
    procesar_vinculacion_o_desconocido,
)
from app.routers.whatsapp.etapa_handlers import procesar_estados_y_handlers_deterministicos
from app.routers.whatsapp.etapa_ia import procesar_llamada_ia_y_normalizacion
from app.routers.whatsapp.etapa_resolucion import procesar_resolucion_billetera_y_propuestas
from app.routers.whatsapp.etapa_despacho import procesar_despacho_y_respuesta
from app.routers.whatsapp.constantes import MESES_ES_GEN, logger
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
    _debe_bloquear_mezcla_lote,
)
from app.routers.whatsapp.marcas import ajustar_categoria_marcas
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.routers.whatsapp.gastos import manejar_consulta_gastos
from app.routers.whatsapp.handlers_confirmaciones import (
    manejar_cancelacion,
    manejar_confirmacion,
    manejar_saludo,
)
from app.routers.whatsapp.handlers_deshacer import (
    manejar_corregir,
    manejar_deshacer,
)
from app.routers.whatsapp.handlers_menus import (
    manejar_aclaracion_cuotas,
    manejar_menu_billetera,
    manejar_menu_tarjeta,
    manejar_numero_aislado,
    manejar_verificaciones_slot_filling,
)
from app.routers.whatsapp.handlers_suscripciones import (
    manejar_alta_suscripcion,
    manejar_ambiguedad_suscripcion,
    manejar_baja_suscripcion,
    manejar_cambio_precio_suscripcion,
    manejar_consulta_suscripciones,
)
from app.routers.whatsapp.handlers_transferencias import (
    manejar_pago_resumen,
    manejar_transferencias,
)
from app.routers.whatsapp.media import (
    _descargar_medio_meta,
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
    montos_de_dinero_en_texto,
    parsear_monto_marca,
    propagar_fechas_lote,
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
    whatsapp_service,
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
    get_meta_http_client,
    marcar_leido_y_escribiendo,
)
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.telefono import normalizar_telefono_ar
from app.utils.texto import normalizar_texto


router = APIRouter(prefix="/whatsapp", tags=["whatsapp-ia"])

MAX_MOVIMIENTOS_POR_LOTE = 10
MSG_TOPE_MOVIMIENTOS_SUPERADO = (
    "El límite es de 10 movimientos por mensaje. "
    "Por favor mandalos en tandas más chicas o usá la importación desde la web de Argentum."
)
MSG_NO_MEZCLAR_TRANSFERENCIAS = (
    "Las transferencias, extracciones de cajero y compra de dólares deben registrarse "
    "en mensajes separados de los gastos o ingresos. Por favor mandalas por separado."
)

TERMINOS_BLOQUEO_MEZCLA = (
    "transferi", "transferir", "transferencia", "pase a", "pasé a",
    "extraje", "extraccion", "extracción", "cajero",
    "compre dolares", "compré dólares", "vendi dolares", "vendí dólares",
    "comprar dolares", "comprar dólares", "vender dolares", "vender dólares",
)
PATRON_BLOQUEO_MEZCLA = re.compile(
    rf"\b(?:{'|'.join(re.escape(p) for p in TERMINOS_BLOQUEO_MEZCLA)})\b"
)

PALABRAS_FUERZAN_CREDITO = [
    "credito", "crédito", "cuota", "cuotas", "en cuotas",
    "visa", "master", "mastercard", "amex", "american express", "american",
    "naranja", "cabal", "tarje", "la tarje", "la de credito", "la de crédito",
    "la credi", "tarjeta de credito", "tarjeta de crédito"
]

PALABRAS_FUERZAN_DEBITO = [
    "debito", "débito", "tarjeta de debito", "tarjeta de débito",
    "debito automatico", "débito automático"
]


@router.get("/webhook", response_class=PlainTextResponse)
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
    Orquestador del procesamiento asíncrono de mensajes de WhatsApp.
    Ejecuta las etapas modularizadas en app/routers/whatsapp manteniendo
    SessionLocal(), _buscar_usuario_por_telefono() y _verificar_rate_limit_registrado()
    directamente en este módulo para compatibilidad con la suite de regresión.
    """
    msg = datos_mensaje.get("msg", {})
    wamid = datos_mensaje.get("wamid")
    from_number = datos_mensaje.get("from_number", "")
    msg_type = datos_mensaje.get("msg_type", "text")
    t_inicio = datos_mensaje.get("t_inicio") or time.perf_counter()

    # Feedback visual temprano: marcar como leído y mostrar escribiendo
    if wamid:
        marcar_leido_y_escribiendo(wamid)

    db = SessionLocal()
    try:
        try:
            usuario = _buscar_usuario_por_telefono(from_number, db)

            ctx = ContextoMensaje(
                datos_mensaje=datos_mensaje,
                msg=msg,
                wamid=wamid,
                from_number=from_number,
                msg_type=msg_type,
                t_inicio=t_inicio,
                db=db,
                usuario=usuario,
            )

            # Etapa 1: Vinculación o manejo de usuario desconocido
            if not usuario:
                procesar_vinculacion_o_desconocido(ctx)
                if ctx.terminado:
                    return

            # Rate limit para usuario registrado
            tel_usuario = usuario.telefono_normalizado or usuario.telefono or from_number
            permitido, motivo_rate_limit = _verificar_rate_limit_registrado(tel_usuario, es_medio=ctx.es_medio)
            if not permitido:
                logger.warning(
                    "whatsapp_rate_limit_registrado_superado",
                    usuario_id=str(usuario.id),
                    telefono=from_number,
                    motivo=motivo_rate_limit,
                )
                whatsapp_service.enviar_whatsapp(
                    from_number,
                    f"Enviaste muchos mensajes en poco tiempo. Esperá un minuto antes de mandar otro ({motivo_rate_limit}).",
                )
                return

            logger.info(
                "whatsapp_webhook_mensaje_recibido",
                from_number=from_number,
                usuario_id=str(usuario.id),
                msg_type=msg_type,
            )

            # Etapa 2: Medios y texto de entrada
            procesar_entrada_medios_y_texto(ctx)
            if ctx.terminado:
                return

            if ctx.extraccion is not None:
                from app.routers.whatsapp.extraccion_documento import a_entidades
                from app.routers.whatsapp.etapa_ia import aplicar_marcas_y_memoria
                from app.routers.whatsapp.lote_documento import (
                    separar_duplicados,
                    armar_resultado_ia_documento,
                    preparar_rendimientos,
                    asignar_billeteras,
                )

                billeteras_todas = _obtener_billeteras_activas(usuario.id, db)
                billeteras_pesos = [b for b in billeteras_todas if b.moneda == Moneda.ARS]

                b_match = None
                if ctx.extraccion.billetera_texto and ctx.extraccion.documento_tipo == "captura_actividad":
                    b_match, _ = resolver_billetera_cascada(ctx.extraccion.billetera_texto, billeteras_pesos)

                if b_match:
                    b_final = b_match
                    se_asumio_defecto = False
                else:
                    b_ppal = next((b for b in billeteras_pesos if b.es_principal), None)
                    if not b_ppal and billeteras_pesos:
                        b_ppal = billeteras_pesos[0]
                    b_final = b_ppal
                    se_asumio_defecto = True

                billetera_nombre = b_final.nombre if b_final else "tu billetera"

                se_asumio_principal = asignar_billeteras(
                    resultado=ctx.extraccion,
                    billeteras_pesos=billeteras_pesos,
                    billetera_defecto_nombre=billetera_nombre,
                    se_asumio_defecto=se_asumio_defecto,
                )

                entidades_raw = a_entidades(ctx.extraccion, billetera_nombre)
                rend_a_anotar, avisos_rend = preparar_rendimientos(
                    db,
                    usuario.id,
                    entidades_raw,
                    ctx.extraccion.billetera_texto,
                    ctx.extraccion.documento_tipo,
                    camino="B",
                )

                tiene_movimientos_comunes = entidades_raw.get("monto") is not None
                tiene_rendimientos_extraidos = bool(getattr(ctx.extraccion, "rendimientos", []))
                omitidos = getattr(ctx.extraccion, "omitidos", [])

                if not tiene_movimientos_comunes and tiene_rendimientos_extraidos:
                    ctx.resultado_ia = armar_resultado_ia_documento(
                        entidades=entidades_raw,
                        duplicados=[],
                        billetera_nombre=billetera_nombre,
                        se_asumio_principal=se_asumio_principal,
                        billeteras_usuario=billeteras_todas,
                        documento_tipo=ctx.extraccion.documento_tipo,
                        total_vistos=ctx.extraccion.total_vistos,
                        rendimientos_a_anotar=rend_a_anotar,
                        avisos_rendimientos=avisos_rend,
                        camino="B",
                        solo_rendimientos=True,
                        omitidos=omitidos,
                        es_pdf=ctx.es_pdf,
                        extraccion=ctx.extraccion,
                    )
                elif not tiene_movimientos_comunes and not tiene_rendimientos_extraidos:
                    ctx.resultado_ia = armar_resultado_ia_documento(
                        entidades=entidades_raw,
                        duplicados=[],
                        billetera_nombre=billetera_nombre,
                        se_asumio_principal=se_asumio_principal,
                        billeteras_usuario=billeteras_todas,
                        documento_tipo=ctx.extraccion.documento_tipo,
                        total_vistos=ctx.extraccion.total_vistos,
                        rendimientos_a_anotar=rend_a_anotar,
                        avisos_rendimientos=avisos_rend,
                        camino="B",
                        solo_rendimientos=False,
                        omitidos=omitidos,
                        es_pdf=ctx.es_pdf,
                        extraccion=ctx.extraccion,
                    )
                else:
                    aplicar_marcas_y_memoria(db, usuario.id, entidades_raw)
                    entidades_sin_dups, dups = separar_duplicados(
                        db, usuario.id, b_final, entidades_raw, billeteras_usuario=billeteras_todas
                    )

                    ctx.resultado_ia = armar_resultado_ia_documento(
                        entidades=entidades_sin_dups,
                        duplicados=dups,
                        billetera_nombre=billetera_nombre,
                        se_asumio_principal=se_asumio_principal,
                        billeteras_usuario=billeteras_todas,
                        documento_tipo=ctx.extraccion.documento_tipo,
                        total_vistos=ctx.extraccion.total_vistos,
                        rendimientos_a_anotar=rend_a_anotar,
                        avisos_rendimientos=avisos_rend,
                        camino="B",
                        solo_rendimientos=False,
                        omitidos=omitidos,
                        es_pdf=ctx.es_pdf,
                        extraccion=ctx.extraccion,
                    )
            else:
                # Etapa 3: Estados pendientes y handlers determinísticos
                procesar_estados_y_handlers_deterministicos(ctx)
                if ctx.terminado:
                    return

                # Etapa 4: Llamada a IA y normalización de entidades
                procesar_llamada_ia_y_normalizacion(ctx)
                if ctx.terminado:
                    return

                # Etapa 5: Resolución de billeteras, cuotas, lotes y propuestas
                procesar_resolucion_billetera_y_propuestas(ctx)
                if ctx.terminado:
                    return

            # Etapa 6: Despacho por intent, ejecución y registro
            procesar_despacho_y_respuesta(ctx)

            # Envío saliente vía Meta Graph API
            if ctx.resultado_ia and "respuesta_usuario" in ctx.resultado_ia:
                t_envio_start = time.perf_counter()
                whatsapp_service.enviar_whatsapp(from_number, ctx.resultado_ia["respuesta_usuario"])
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
                    whatsapp_service.enviar_whatsapp(
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


@router.post("/webhook", response_class=PlainTextResponse)
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



@router.post("/test")
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
