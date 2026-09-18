"""
Handlers de flujo determinístico para WhatsApp IA (Fase 2).
Cada handler encapsula la detección, procesamiento, persistencia en ConversacionWpp y envío de respuesta.
"""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy import select
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
    _buscar_ultimo_movimiento_whatsapp,
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
    _es_saludo,
    _parece_intento_correccion,
)
from app.routers.whatsapp.parsers import _nombre_corto_categoria
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

    msg_pago_resumen = "El pago del resumen de la tarjeta se gestiona desde la web de Argentum. No se puede realizar por WhatsApp."
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
                "agregar_suscripcion", "dar_baja_suscripcion", "cambiar_precio_suscripcion"
            ]),
            ConversacionWpp.accion_ejecutada.is_(None),
        )
    ).scalars().all()
    for p in props_pend:
        p.accion_ejecutada = "cancelada"

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


def manejar_numero_aislado(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
    conv_activa: ConversacionWpp | None = None,
    estado_previo: dict | None = None,
) -> bool:
    """
    Si el mensaje es únicamente un número sin pregunta pendiente (sin slot filling activo ni estado previo).
    """
    if not (mensaje_texto.strip().isdigit() and not (conv_activa and conv_activa.slot_filling_activo) and not estado_previo):
        return False

    msg_numero_suelto = (
        "Mandaste solo un número. Si querés registrar un movimiento, "
        "escribí el monto y el concepto (por ejemplo: 'gasté 5000 en el kiosco')."
    )
    whatsapp_service.enviar_whatsapp(from_number, msg_numero_suelto)
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_numero_suelto,
        intent_detectado="desconocido",
        entidades={},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    return True


def manejar_consulta_suscripciones(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Detección determinística de consultas de suscripciones activas.
    """
    if not _es_consulta_suscripciones(mensaje_texto):
        return False

    def _procesar_consulta_suscripciones(usuario: Usuario, db: Session) -> str:
        subs = suscripcion_service.obtener_suscripciones(db, usuario.id, estado="activa")
        if not subs:
            return "No tenés suscripciones activas registradas."

        totales = suscripcion_service.obtener_total_mensual(db, usuario.id)
        total_ars = totales["total_ars"]
        total_usd = totales["total_usd"]

        lineas = ["Tus suscripciones activas:"]
        for s in subs:
            monto_p = s.precio_actual.monto if s.precio_actual else Decimal("0")
            moneda_p = s.precio_actual.moneda if s.precio_actual else "ARS"
            mon_enum = Moneda.USD if moneda_p == "USD" else Moneda.ARS
            monto_fmt = formatear_monto(float(monto_p), mon_enum)
            frec_str = s.frecuencia.value if hasattr(s.frecuencia, "value") else str(s.frecuencia)
            lineas.append(f"- {s.nombre}: {monto_fmt} {frec_str}")

        totales_str = []
        if total_ars > 0:
            totales_str.append(formatear_monto(float(total_ars), Moneda.ARS))
        if total_usd > 0:
            totales_str.append(formatear_monto(float(total_usd), Moneda.USD))

        if not totales_str:
            totales_str = ["$0"]

        lineas.append(f"Total mensual estimado: {' y '.join(totales_str)}")
        return "\n".join(lineas)

    msg_resp = _procesar_consulta_suscripciones(usuario, db)
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_resp,
        intent_detectado="consultar_suscripciones",
        entidades={},
        accion_ejecutada="consulta",
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_resp)
    return True


def manejar_baja_suscripcion(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Detección determinística de baja de suscripciones.
    """
    es_baja, srv_baja = _es_pedido_baja_suscripcion(mensaje_texto)
    if not es_baja:
        return False

    sub_candidata = _buscar_suscripcion_activa_por_nombre(usuario.id, srv_baja, db)
    if not sub_candidata:
        srv_display = srv_baja or "ese servicio"
        msg_resp = f"No tenés ninguna suscripción activa a {srv_display}."
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="dar_baja_suscripcion",
            entidades={},
            accion_ejecutada="no_encontrada",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_resp)
        return True

    pv = suscripcion_service.obtener_precio_vigente(db, sub_candidata.id)
    m_val = pv.monto if pv else Decimal("0")
    mon_val = pv.moneda if pv else "ARS"
    frec_str = sub_candidata.frecuencia.value if hasattr(sub_candidata.frecuencia, "value") else str(sub_candidata.frecuencia)
    m_fmt = formatear_monto(float(m_val), Moneda.USD if mon_val == "USD" else Moneda.ARS)
    msg_propuesta = f"¿Confirmás dar de baja la suscripción a {sub_candidata.nombre} ({m_fmt} {frec_str})?"

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_propuesta,
        intent_detectado="dar_baja_suscripcion",
        entidades={"suscripcion_id": str(sub_candidata.id), "nombre": sub_candidata.nombre},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_propuesta)
    return True


def manejar_cambio_precio_suscripcion(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Detección determinística de cambio de precio de suscripción.
    """
    es_cp, srv_cp, nuevo_precio = _es_cambio_precio_suscripcion(mensaje_texto)
    if not (es_cp and nuevo_precio):
        return False

    sub_candidata = _buscar_suscripcion_activa_por_nombre(usuario.id, srv_cp, db)
    if not sub_candidata:
        srv_display = srv_cp or "ese servicio"
        msg_resp = f"No tenés ninguna suscripción activa a {srv_display}."
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="cambiar_precio_suscripcion",
            entidades={},
            accion_ejecutada="no_encontrada",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_resp)
        return True

    pv = suscripcion_service.obtener_precio_vigente(db, sub_candidata.id)
    m_ant = pv.monto if pv else Decimal("0")
    mon_val = pv.moneda if pv else "ARS"
    mon_enum = Moneda.USD if mon_val == "USD" else Moneda.ARS
    m_ant_fmt = formatear_monto(float(m_ant), mon_enum)
    m_nuevo_fmt = formatear_monto(float(nuevo_precio), mon_enum)

    msg_propuesta = f"¿Confirmás actualizar el precio de {sub_candidata.nombre} de {m_ant_fmt} a {m_nuevo_fmt}?"
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_propuesta,
        intent_detectado="cambiar_precio_suscripcion",
        entidades={
            "suscripcion_id": str(sub_candidata.id),
            "nombre": sub_candidata.nombre,
            "nuevo_monto": float(nuevo_precio),
            "moneda": mon_val,
        },
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_propuesta)
    return True


def manejar_ambiguedad_suscripcion(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Detección determinística de ambigüedad suscripción vs gasto suelto.
    """
    es_amb, srv_amb = _detectar_ambiguedad_suscripcion(mensaje_texto)
    if not (es_amb and srv_amb):
        return False

    msg_pregunta = f"¿Es un gasto único o una suscripción a {srv_amb}?"
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_pregunta,
        intent_detectado="ambiguedad_suscripcion",
        entidades={"servicio": srv_amb},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=True,
        slot_filling_estado={"tipo_flujo": "ambiguedad_suscripcion", "servicio": srv_amb},
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_pregunta)
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

    from app.routers.whatsapp_ia import (
        _confirmar_propuesta_baja_suscripcion,
        _confirmar_propuesta_cambio_precio,
        _confirmar_propuesta_corregir,
        _confirmar_propuesta_deshacer,
        _confirmar_propuesta_suscripcion,
        _confirmar_propuesta_transaccion,
        _confirmar_propuesta_transferencia,
    )

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

    from app.routers.whatsapp_ia import _construir_propuesta_deshacer

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
        elif motivo_err == "ES_RECURRENTE":
            msg_undo_resp = "Ese movimiento fue generado automáticamente y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum."
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
    from app.routers.whatsapp_ia import (
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
    from app.routers.whatsapp_ia import _interpretar_transferencia

    es_tr, estado_tr, ents_tr, resp_tr = _interpretar_transferencia(
        mensaje_texto, usuario, db, estado_previo=estado_previo
    )
    if not es_tr:
        return False

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
