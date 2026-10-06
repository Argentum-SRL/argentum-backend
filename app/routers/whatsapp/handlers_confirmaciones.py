"""Handlers de estados y confirmaciones para WhatsApp (saludo, cancelación, confirmación)."""
from __future__ import annotations

from datetime import timedelta
from app.utils.fecha import ahora_argentina
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
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
from app.routers.whatsapp.parsers import (
    _nombre_corto_categoria,
    _resolver_y_validar_fecha,
    _resolver_fecha_transaccion,
    _extraer_frecuencia_mencionada,
    _extraer_monto_y_moneda_suscripcion,
    _extraer_nombre_servicio,
)
from app.services import suscripcion_service, whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion
from app.utils.formato import formatear_monto


def manejar_saludo(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Maneja el intent determinístico de saludo rioplatense ('hola', 'buenas', etc.).
    Si el usuario tenía una operación a medias (slot filling), la describe y la cancela/desactiva.
    Si no aplica, retorna False. Si aplica, persiste ConversacionWpp, envía la respuesta y retorna True.
    """
    if not _es_saludo(mensaje_texto):
        return False

    conv_activa_saludo = _buscar_slot_filling_activo(usuario.id, db)
    msg_saludo = ""
    if conv_activa_saludo and conv_activa_saludo.slot_filling_estado:
        est_saludo = conv_activa_saludo.slot_filling_estado
        monto_saludo = est_saludo.get("monto")
        cat_saludo = est_saludo.get("categoria")
        mon_saludo = est_saludo.get("moneda", "ARS")
        mon_enum = Moneda.USD if mon_saludo == "USD" else Moneda.ARS
        cat_disp = _nombre_corto_categoria(cat_saludo) if cat_saludo else ""
        if monto_saludo is not None:
            monto_fmt = formatear_monto(float(monto_saludo), mon_enum)
            if cat_disp:
                linea_pend = f"Tenías una operación a medias (anotar {monto_fmt} en {cat_disp}). Podés completarla o empezar de nuevo."
            else:
                linea_pend = f"Tenías una operación a medias de {monto_fmt}. Podés completarla o empezar de nuevo."
        else:
            linea_pend = "Tenías una operación a medias. Podés completarla o empezar de nuevo."
        msg_saludo = f"Hola. {linea_pend}\nTambién podés registrar otro gasto, ingreso o consultar tus saldos."
        # Desactivar para que el saludo no arrastre ni reactive nada
        conv_activa_saludo.slot_filling_activo = False
        conv_activa_saludo.accion_ejecutada = "interrumpida_por_saludo"
        db.flush()
    else:
        msg_saludo = "Hola. Podés registrar gastos, ingresos o consultar tus saldos y proyecciones. Por ejemplo: 'gasté 5000 en el kiosco'."

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_saludo,
        intent_detectado="saludo",
        entidades={},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_saludo)
    return True


def manejar_cancelacion(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Maneja el chequeo determinístico de cancelación.
    Cancela transacciones pendientes IA_WPP, slot filling activo y propuestas pendientes.
    """
    if not _es_cancelacion(mensaje_texto):
        return False

    propuesta_ganadora = _buscar_propuesta_confirmable_mas_reciente(usuario.id, db)
    intent_ganador = propuesta_ganadora.intent_detectado if propuesta_ganadora else None

    txs_pend = db.execute(
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.origen == OrigenTransaccion.IA_WPP,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE,
        )
    ).scalars().all()
    for tx in txs_pend:
        db.delete(tx)
    if txs_pend:
        emitir_evento_actualizacion(db, usuario.id, "transacciones")

    convs_act = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.slot_filling_activo == True,
        )
    ).scalars().all()
    for c in convs_act:
        c.slot_filling_activo = False
        c.accion_ejecutada = "cancelada"

    props_pend = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado.in_([
                "registrar_transaccion", "deshacer", "corregir",
                "agregar_suscripcion", "dar_baja_suscripcion", "cambiar_precio_suscripcion",
                "aportar_meta", "transferir_fondos",
                "memoria_comercio", "memoria_anteriores",
            ]),
            ConversacionWpp.accion_ejecutada.is_(None),
        )
    ).scalars().all()
    for p in props_pend:
        p.accion_ejecutada = "cancelada"

    if intent_ganador == "memoria_comercio":
        msg_cancel = "Listo, solo esta vez."
    elif intent_ganador == "memoria_anteriores":
        msg_cancel = "Listo, quedan como estaban."
    else:
        msg_cancel = "Listo, cancelado."

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_cancel,
        intent_detectado="cancelar",
        entidades={},
        accion_ejecutada="cancelada",
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_cancel)
    return True


def manejar_confirmacion(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Chequeo determinístico de confirmación con bloqueo de concurrencia.
    Despacha a la propuesta confirmable más reciente.
    """
    if not _es_confirmacion(mensaje_texto):
        return False

    from app.routers.whatsapp.confirmaciones import (
        _confirmar_propuesta_baja_suscripcion,
        _confirmar_propuesta_cambio_precio,
        _confirmar_propuesta_suscripcion,
    )
    from app.routers.whatsapp.deshacer_corregir import (
        _confirmar_propuesta_corregir,
        _confirmar_propuesta_deshacer,
    )
    from app.routers.whatsapp.memoria_comercio_wpp import (
        confirmar_anteriores,
        confirmar_memoria,
        preguntar_memoria_tras_correccion,
    )
    from app.routers.whatsapp.registro import _confirmar_propuesta_transaccion
    from app.routers.whatsapp.transferencias import _confirmar_propuesta_transferencia

    propuesta_ganadora = _buscar_propuesta_confirmable_mas_reciente(usuario.id, db)
    intent_ganador = propuesta_ganadora.intent_detectado if propuesta_ganadora else None

    if intent_ganador == "deshacer":
        tx_deshecha, msg_confirm, ya_conf = _confirmar_propuesta_deshacer(usuario, db)
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_confirm,
            intent_detectado="confirmar_deshacer",
            entidades={},
            accion_ejecutada=f"deshecho:{tx_deshecha.id}" if tx_deshecha else ("ya_deshecho" if ya_conf else None),
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
        return True

    elif intent_ganador == "corregir":
        cambios = (propuesta_ganadora.entidades or {}).get("cambios") if propuesta_ganadora else {}
        tx_corregida, msg_confirm, ya_conf = _confirmar_propuesta_corregir(usuario, db)
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_confirm,
            intent_detectado="confirmar_corregir",
            entidades={},
            accion_ejecutada=f"corregido:{tx_corregida.id}" if tx_corregida else ("ya_corregido" if ya_conf else None),
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
        if tx_corregida:
            preguntar_memoria_tras_correccion(usuario, db, from_number, tx_corregida, cambios)
        return True

    elif intent_ganador == "memoria_comercio":
        confirmar_memoria(usuario, db, from_number, propuesta_ganadora)
        return True

    elif intent_ganador == "memoria_anteriores":
        confirmar_anteriores(usuario, db, from_number, propuesta_ganadora)
        return True

    elif intent_ganador == "transferir_fondos":
        tr_creada, msg_confirm, ya_conf = _confirmar_propuesta_transferencia(usuario, db)
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_confirm,
            intent_detectado="confirmar_transferencia",
            entidades={},
            accion_ejecutada=f"transferencia:{tr_creada.id}" if tr_creada else ("ya_confirmada" if ya_conf else None),
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
        return True

    elif intent_ganador == "dar_baja_suscripcion":
        sub_bajada, msg_confirm, ya_conf = _confirmar_propuesta_baja_suscripcion(usuario, db)
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_confirm,
            intent_detectado="confirmar_baja_suscripcion",
            entidades={},
            accion_ejecutada=f"baja:{sub_bajada.id}" if sub_bajada else ("ya_bajada" if ya_conf else None),
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
        return True

    elif intent_ganador == "cambiar_precio_suscripcion":
        hist_cp, msg_confirm, ya_conf = _confirmar_propuesta_cambio_precio(usuario, db)
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_confirm,
            intent_detectado="confirmar_cambio_precio",
            entidades={},
            accion_ejecutada=f"precio:{hist_cp.id}" if hist_cp else ("ya_actualizado" if ya_conf else None),
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
        return True

    elif intent_ganador == "agregar_suscripcion":
        sub_creada, msg_confirm, ya_conf = _confirmar_propuesta_suscripcion(usuario, db)
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_confirm,
            intent_detectado="confirmar_suscripcion",
            entidades={},
            accion_ejecutada=str(sub_creada.id) if sub_creada else ("ya_creada" if ya_conf else None),
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
        return True

    else:
        # intent_ganador == "registrar_transaccion" o None (sin propuesta pendiente)
        limite_30 = ahora_argentina() - timedelta(minutes=30)
        ultima_conv = db.execute(
            select(ConversacionWpp)
            .where(
                ConversacionWpp.usuario_id == usuario.id,
                ConversacionWpp.fecha >= limite_30,
            )
            .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().first()

        es_tras_directo = (
            propuesta_ganadora is None
            and ultima_conv is not None
            and ultima_conv.intent_detectado == "registrar_transaccion"
            and ultima_conv.accion_ejecutada not in (None, "cancelada", "vencida", "error", "descartado_por_duplicado")
            and (
                (ultima_conv.entidades and ultima_conv.entidades.get("registro_directo") is True)
                or (ultima_conv.mensaje_bot and ultima_conv.mensaje_bot.startswith("Listo."))
            )
        )

        if es_tras_directo:
            msg_confirm = "Ya quedó anotado. Si hay algo mal, decime qué corregir."
            nueva_conv = ConversacionWpp(
                usuario_id=usuario.id,
                wamid=wamid,
                mensaje_usuario=mensaje_texto,
                tipo_mensaje=TipoMensajeWpp.TEXTO,
                transcripcion=None,
                mensaje_bot=msg_confirm,
                intent_detectado="confirmar",
                entidades={},
                accion_ejecutada=ultima_conv.accion_ejecutada,
                confianza=Decimal("1.000"),
                slot_filling_activo=False,
                slot_filling_estado=None,
            )
            db.add(nueva_conv)
            db.commit()
            whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
            return True

        tx_creada, msg_confirm, ya_conf = _confirmar_propuesta_transaccion(usuario, db)
        prop_confirmada = db.execute(
            select(ConversacionWpp).where(
                ConversacionWpp.usuario_id == usuario.id,
                ConversacionWpp.intent_detectado == "registrar_transaccion",
                ConversacionWpp.accion_ejecutada.is_not(None),
            ).order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().first()
        accion_final = prop_confirmada.accion_ejecutada if prop_confirmada else (str(tx_creada.id) if tx_creada else ("ya_confirmada" if ya_conf else None))
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_confirm,
            intent_detectado="confirmar",
            entidades={},
            accion_ejecutada=accion_final,
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
        return True
