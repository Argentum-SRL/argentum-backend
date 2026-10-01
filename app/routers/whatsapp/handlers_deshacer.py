"""Handlers de deshacer y corregir para WhatsApp."""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy.orm import Session
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transferencia_interna import TransferenciaInterna
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.db_lookups import (
    _buscar_propuesta_confirmable_mas_reciente,
    _buscar_slot_filling_activo,
    _buscar_suscripcion_activa_por_nombre,
    _buscar_suscripcion_cobrada_periodo_actual,
    _buscar_transaccion_duplicada_reciente,
    _buscar_ultimo_movimiento_whatsapp,
    _obtener_billeteras_activas,
    _obtener_tarjetas_activas,
    _resolver_categoria_y_subcategoria,
)
from app.routers.whatsapp.detectors import (
    _detectar_ambiguedad_suscripcion,
    _es_cambio_precio_suscripcion,
    _es_cancelacion,
    _es_confirmacion,
    _es_consulta_suscripciones,
    _es_pedido_baja_suscripcion,
    _es_pedido_deshacer,
    _es_pedido_pago_resumen,
    _es_pregunta_billetera,
    _es_saludo,
    _parece_intento_correccion,
    _es_confirmacion_gasto_aparte,
    _es_confirmacion_lote_ambos,
    _es_confirmacion_lote_uno_solo,
    _es_confirmacion_nuevo_movimiento,
    _es_descarte_duplicado,
    _es_intento_alta_suscripcion,
    _bloquear_mezcla_en_handlers,
)
from app.services import suscripcion_service, whatsapp_service


def manejar_deshacer(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Detección determinística de deshacer.
    """
    if not _es_pedido_deshacer(mensaje_texto):
        return False

    from app.routers.whatsapp.deshacer_corregir import _construir_propuesta_deshacer

    tx_last, motivo_err = _buscar_ultimo_movimiento_whatsapp(usuario.id, db)
    if not tx_last:
        if motivo_err in ("YA_DESHECHO", "YA_BORRADO"):
            msg_undo_resp = "No hay nada para deshacer."
        elif motivo_err == "PLAZO_VENCIDO":
            msg_undo_resp = "El último movimiento fue hace más de 30 minutos. Para eliminarlo, ingresá a la web de Argentum."
        elif motivo_err == "ES_CUOTA":
            msg_undo_resp = "Ese movimiento corresponde a una cuota de tarjeta y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum."
        elif motivo_err == "ES_RESUMEN":
            msg_undo_resp = "Ese movimiento corresponde al pago de un resumen y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum."
        elif motivo_err == "ES_META":
            msg_undo_resp = "Ese movimiento corresponde a una meta de ahorro y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum."
        elif motivo_err == "ES_SUSCRIPCION":
            msg_undo_resp = "Ese movimiento es el cobro automático de una suscripción. Para cambiarlo, entrá a Suscripciones en la web."
        else:
            msg_undo_resp = "No tenés ningún movimiento reciente registrado por WhatsApp para deshacer. Podés gestionarlo desde la web de Argentum."

        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_undo_resp,
            intent_detectado="deshacer",
            entidades={},
            accion_ejecutada="sin_efecto",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_undo_resp)
        return True

    # Hay movimiento para deshacer: armar propuesta de confirmación
    msg_propuesta_undo = _construir_propuesta_deshacer(tx_last, db)
    if isinstance(tx_last, list):
        entidades_undo = {"lote_ids": [str(t.id) for t in tx_last]}
    elif isinstance(tx_last, TransferenciaInterna):
        entidades_undo = {"transferencia_id": str(tx_last.id)}
    else:
        entidades_undo = {"transaccion_id": str(tx_last.id)}
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_propuesta_undo,
        intent_detectado="deshacer",
        entidades=entidades_undo,
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_propuesta_undo)
    return True


def manejar_corregir(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Detección determinística de corregir último movimiento.
    """
    from app.routers.whatsapp.deshacer_corregir import (
        _construir_propuesta_corregir,
        _detectar_correccion_ultimo_movimiento,
    )

    tx_last_corr, motivo_corr = _buscar_ultimo_movimiento_whatsapp(usuario.id, db)
    if tx_last_corr and isinstance(tx_last_corr, Transaccion):
        es_corr, cambios, err_corr = _detectar_correccion_ultimo_movimiento(
            mensaje_texto, usuario.id, db, tx_last_corr
        )
        if es_corr:
            if err_corr:
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=err_corr,
                    intent_detectado="corregir",
                    entidades={},
                    accion_ejecutada="error_moneda",
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, err_corr)
                return True

            if cambios:
                msg_propuesta_corr = _construir_propuesta_corregir(tx_last_corr, cambios, db)
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg_propuesta_corr,
                    intent_detectado="corregir",
                    entidades={"transaccion_id": str(tx_last_corr.id), "cambios": cambios},
                    accion_ejecutada=None,
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_propuesta_corr)
                return True
    else:
        if _parece_intento_correccion(mensaje_texto):
            if motivo_corr == "PLAZO_VENCIDO":
                msg_resp = "El último movimiento fue hace más de 30 minutos. Para modificarlo, ingresá a la web de Argentum."
            elif motivo_corr == "ES_CUOTA":
                msg_resp = "Ese movimiento corresponde a una cuota de tarjeta y no se puede modificar por WhatsApp. Podés gestionarlo desde la web de Argentum."
            else:
                msg_resp = "No tenés ningún movimiento reciente registrado por WhatsApp para corregir. Podés gestionarlo desde la web de Argentum."

            nueva_conv = ConversacionWpp(
                usuario_id=usuario.id,
                wamid=wamid,
                mensaje_usuario=mensaje_texto,
                tipo_mensaje=TipoMensajeWpp.TEXTO,
                transcripcion=None,
                mensaje_bot=msg_resp,
                intent_detectado="corregir",
                entidades={},
                accion_ejecutada="sin_efecto",
                confianza=Decimal("1.000"),
                slot_filling_activo=False,
                slot_filling_estado=None,
            )
            db.add(nueva_conv)
            db.commit()
            whatsapp_service.enviar_whatsapp(from_number, msg_resp)
            return True
    return False
