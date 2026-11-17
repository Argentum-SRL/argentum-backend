"""Handlers de transferencias y pagos para WhatsApp (pago resumen, transferencias internas)."""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy.orm import Session
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.usuario import Moneda, Usuario
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


def manejar_pago_resumen(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Maneja el pedido determinístico de pago de resumen de tarjeta.
    """
    if not _es_pedido_pago_resumen(mensaje_texto):
        return False

    msg_pago_resumen = "El pago del resumen se carga en miargentum.com, no por acá."
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_pago_resumen,
        intent_detectado="pago_resumen",
        entidades={},
        accion_ejecutada="bloqueado_web",
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_pago_resumen)
    return True


def _despachar_resultado_tr(
    estado_tr: str | None,
    ents_tr: dict | None,
    resp_tr: str | None,
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None,
    conv_activa: ConversacionWpp | None,
) -> bool:
    if estado_tr in ("no_cash", "no_usd", "absurda", "misma_billetera"):
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=resp_tr,
            intent_detectado="transferir_fondos",
            entidades={},
            accion_ejecutada="sin_efecto",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, resp_tr)
        return True

    elif estado_tr == "slot_filling":
        if conv_activa:
            conv_activa.slot_filling_activo = False
            db.flush()
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=resp_tr,
            intent_detectado="transferir_fondos",
            entidades=ents_tr,
            accion_ejecutada=None,
            confianza=Decimal("1.000"),
            slot_filling_activo=True,
            slot_filling_estado=ents_tr,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, resp_tr)
        return True

    elif estado_tr == "propuesta":
        if conv_activa:
            conv_activa.slot_filling_activo = False
            db.flush()
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=resp_tr,
            intent_detectado="transferir_fondos",
            entidades=ents_tr,
            accion_ejecutada=None,
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, resp_tr)
        return True

    return False


def manejar_transferencias(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
    conv_activa: ConversacionWpp | None = None,
    estado_previo: dict | None = None,
) -> bool:
    """
    Detección determinística de transferencias / cajero / dólares.
    """
    import re
    from app.routers.whatsapp.transferencias import _interpretar_transferencia
    from app.utils.texto import normalizar_texto

    # (1) Estado de cotización de dólares -> código de hoy
    if estado_previo and (
        estado_previo.get("tipo_operacion") in ("compra_usd", "venta_usd")
        and "cotizacion" in estado_previo.get("datos_faltantes", [])
    ):
        es_tr, estado_tr, ents_tr, resp_tr = _interpretar_transferencia(
            mensaje_texto, usuario, db, estado_previo=estado_previo
        )
        if es_tr:
            return _despachar_resultado_tr(
                estado_tr, ents_tr, resp_tr, mensaje_texto, usuario, db,
                from_number, wamid, conv_activa
            )

    # (2) Estado del flujo nuevo (intent_origen transferir_fondos con 'paso')
    if (
        estado_previo
        and estado_previo.get("intent_origen") == "transferir_fondos"
        and "paso" in estado_previo
    ):
        from app.routers.whatsapp.transferencias_flujo import continuar_transferencia
        if continuar_transferencia(
            mensaje_texto, usuario, db, from_number, wamid, conv_activa, estado_previo
        ):
            return True

    # (3) Bloquear mezcla en handlers como hoy
    if not estado_previo and _bloquear_mezcla_en_handlers(
        mensaje_texto, usuario, db, from_number, wamid
    ):
        return True

    # (4) Compra o venta con verbo (compré/vendí/cambié/dolaricé + dólares)
    m_norm = normalizar_texto(mensaje_texto)
    tiene_verbo = bool(
        re.search(r"\b(?:compr[eé]|vend[ií]|cambi[eé]|dolaric[eé])\b", m_norm)
    )
    tiene_dolares = bool(re.search(r"\b(?:d[oó]lares|verdes|usd)\b", m_norm))
    es_gasto = any(w in m_norm for w in ["gaste", "pague", "compre una", "compre un"])

    if tiene_verbo and tiene_dolares and not es_gasto:
        es_tr, estado_tr, ents_tr, resp_tr = _interpretar_transferencia(
            mensaje_texto, usuario, db, estado_previo=estado_previo
        )
        if es_tr:
            return _despachar_resultado_tr(
                estado_tr, ents_tr, resp_tr, mensaje_texto, usuario, db,
                from_number, wamid, conv_activa
            )

    # (5) Manejar texto transferencia
    from app.routers.whatsapp.transferencias_flujo import manejar_texto_transferencia
    if manejar_texto_transferencia(mensaje_texto, usuario, db, from_number, wamid):
        return True

    return False
