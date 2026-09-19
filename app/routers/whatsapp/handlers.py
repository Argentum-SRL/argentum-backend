"""
Handlers de flujo determinístico para WhatsApp IA (Fase 2).
Cada handler encapsula la detección, procesamiento, persistencia en ConversacionWpp y envío de respuesta.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.tarjeta_credito import TarjetaCredito
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
from app.routers.whatsapp.resolvers_cascada import (
    _detectar_duplicados_en_lote,
    _generar_menu_billeteras,
    _generar_menu_tarjetas,
    resolver_billetera_cascada,
    resolver_tarjeta_cascada,
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
)
from app.routers.whatsapp.parsers import (
    _nombre_corto_categoria,
    _resolver_y_validar_fecha,
    _extraer_frecuencia_mencionada,
)
from app.services import suscripcion_service, whatsapp_service
from app.services.tarjeta_service import calcular_primer_vencimiento
from app.services.evento_service import emitir_evento_actualizacion
from app.core.catalogo_suscripciones import buscar_servicio_por_texto
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto


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


def manejar_menu_tarjeta(mensaje_texto: str, usuario: Usuario, db: Session, from_number: str, wamid: str | None=None, conv_activa: ConversacionWpp | None=None) -> bool:
    """Maneja la selección de tarjeta de crédito durante el slot filling de un gasto.
Si no aplica, retorna False. Si aplica, persiste ConversacionWpp, envía la respuesta y retorna True."""
    if not (conv_activa and conv_activa.slot_filling_estado and any(('tarjeta' in d for d in conv_activa.slot_filling_estado.get('datos_faltantes', [])))):
        return False
    from app.routers.whatsapp_ia import _construir_propuesta_credito
    estado_prev_tarj = dict(conv_activa.slot_filling_estado)
    tarjetas_activas = _obtener_tarjetas_activas(usuario.id, db)
    cands_ids = estado_prev_tarj.get('candidatas_tarjetas_ids', [])
    if cands_ids:
        tarjetas_opciones = [t for t in tarjetas_activas if str(t.id) in cands_ids]
    else:
        tarjetas_opciones = tarjetas_activas
    mensaje_limpio = mensaje_texto.strip()
    tarjeta_elegida = None
    es_sel = False
    if mensaje_limpio.isdigit():
        num = int(mensaje_limpio)
        if 1 <= num <= len(tarjetas_opciones):
            tarjeta_elegida = tarjetas_opciones[num - 1]
            es_sel = True
        else:
            whatsapp_service.enviar_whatsapp(from_number, f'Opción inválida. Elegí un número del 1 al {len(tarjetas_opciones)}.')
            return True
    else:
        t_match, cands = resolver_tarjeta_cascada(mensaje_limpio, tarjetas_opciones)
        if t_match:
            tarjeta_elegida = t_match
            es_sel = True
        elif len(cands) > 1:
            menu = _generar_menu_tarjetas(cands)
            whatsapp_service.enviar_whatsapp(from_number, f'¿A cuál te referís?\n{menu}')
            return True
        else:
            menu = _generar_menu_tarjetas(tarjetas_opciones)
            whatsapp_service.enviar_whatsapp(from_number, f'No encontré esa tarjeta entre las tuyas.\n\n{menu}')
            return True
    if es_sel and tarjeta_elegida:
        cant_cuotas = int(estado_prev_tarj.get('cantidad_cuotas', 1))
        monto_total = Decimal(str(estado_prev_tarj.get('monto_total', estado_prev_tarj['monto'])))
        monto_cuota = Decimal(str(estado_prev_tarj.get('monto_cuota', monto_total / Decimal(str(cant_cuotas)))))
        fecha_obj, _ = _resolver_y_validar_fecha(estado_prev_tarj.get('fecha'))
        primer_v = calcular_primer_vencimiento(fecha_obj, tarjeta_elegida.dia_cierre, tarjeta_elegida.dia_vencimiento, False)
        estado_prev_tarj['tarjeta_id'] = str(tarjeta_elegida.id)
        estado_prev_tarj['tarjeta_nombre'] = tarjeta_elegida.nombre
        estado_prev_tarj['tarjeta_billetera_id'] = str(tarjeta_elegida.billetera_id)
        estado_prev_tarj['cantidad_cuotas'] = cant_cuotas
        estado_prev_tarj['monto_cuota'] = float(monto_cuota)
        estado_prev_tarj['monto_total'] = float(monto_total)
        estado_prev_tarj['monto'] = float(monto_total)
        if 'datos_faltantes' in estado_prev_tarj:
            estado_prev_tarj['datos_faltantes'] = [d for d in estado_prev_tarj['datos_faltantes'] if 'tarjeta' not in d]
        conv_activa.slot_filling_activo = False
        db.flush()
        propuesta_msg = _construir_propuesta_credito(estado_prev_tarj, tarjeta_elegida, cant_cuotas, monto_cuota, monto_total, primer_v, se_asumio_tarjeta=False)
        nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=propuesta_msg, intent_detectado='registrar_transaccion', entidades=estado_prev_tarj, accion_ejecutada=None, confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, propuesta_msg)
        return True
    return False


def manejar_aclaracion_cuotas(mensaje_texto: str, usuario: Usuario, db: Session, from_number: str, wamid: str | None=None, conv_activa: ConversacionWpp | None=None) -> bool:
    """Maneja la aclaración de si el monto de una compra en cuotas es el total o por cuota.
Si no aplica, retorna False. Si aplica, persiste ConversacionWpp, envía la respuesta y retorna True."""
    if not (conv_activa and conv_activa.slot_filling_estado and any(('aclarar_cuotas' in d for d in conv_activa.slot_filling_estado.get('datos_faltantes', [])))):
        return False
    from app.routers.whatsapp_ia import _construir_propuesta_credito
    estado_prev_cuotas = dict(conv_activa.slot_filling_estado)
    m_txt_norm = normalizar_texto(mensaje_texto)
    es_total = any((w in m_txt_norm for w in ['total', 'el total', 'en total', 'es el total', 'los dos', 'todo']))
    es_por_cuota = any((w in m_txt_norm for w in ['cuota', 'cada cuota', 'por cuota', 'cada una', 'de cada cuota', 'por mes', 'cada mes']))
    if not es_total and (not es_por_cuota):
        whatsapp_service.enviar_whatsapp(from_number, "Por favor decime si ese monto es 'el total' o 'por cuota'.")
        return True
    monto_base = Decimal(str(estado_prev_cuotas['monto']))
    cant_cuotas = int(estado_prev_cuotas.get('cantidad_cuotas', 1))
    if es_total:
        monto_total = monto_base
        monto_cuota = round(monto_total / Decimal(str(cant_cuotas)), 2)
    else:
        monto_cuota = monto_base
        monto_total = Decimal(str(cant_cuotas)) * monto_cuota
    estado_prev_cuotas['cantidad_cuotas'] = cant_cuotas
    estado_prev_cuotas['monto_cuota'] = float(monto_cuota)
    estado_prev_cuotas['monto_total'] = float(monto_total)
    estado_prev_cuotas['monto'] = float(monto_total)
    if 'datos_faltantes' in estado_prev_cuotas:
        estado_prev_cuotas['datos_faltantes'] = [d for d in estado_prev_cuotas['datos_faltantes'] if 'aclarar_cuotas' not in d]
    tarjetas_activas = _obtener_tarjetas_activas(usuario.id, db)
    t_id_prev = estado_prev_cuotas.get('tarjeta_id')
    tarjeta_obj = db.get(TarjetaCredito, UUID(t_id_prev)) if t_id_prev else None
    if not tarjeta_obj:
        if len(tarjetas_activas) == 1:
            tarjeta_obj = tarjetas_activas[0]
        elif len(tarjetas_activas) > 1:
            tarjeta_obj = next((t for t in tarjetas_activas if t.billetera and t.billetera.es_principal), tarjetas_activas[0])
    if tarjeta_obj:
        fecha_obj, _ = _resolver_y_validar_fecha(estado_prev_cuotas.get('fecha'))
        primer_v = calcular_primer_vencimiento(fecha_obj, tarjeta_obj.dia_cierre, tarjeta_obj.dia_vencimiento, False)
        estado_prev_cuotas['tarjeta_id'] = str(tarjeta_obj.id)
        estado_prev_cuotas['tarjeta_nombre'] = tarjeta_obj.nombre
        estado_prev_cuotas['tarjeta_billetera_id'] = str(tarjeta_obj.billetera_id)
        conv_activa.slot_filling_activo = False
        db.flush()
        propuesta_msg = _construir_propuesta_credito(estado_prev_cuotas, tarjeta_obj, cant_cuotas, monto_cuota, monto_total, primer_v, se_asumio_tarjeta=False)
        nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=propuesta_msg, intent_detectado='registrar_transaccion', entidades=estado_prev_cuotas, accion_ejecutada=None, confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, propuesta_msg)
        return True
    return False


def manejar_menu_billetera(mensaje_texto: str, usuario: Usuario, db: Session, from_number: str, wamid: str | None=None, conv_activa: ConversacionWpp | None=None, estado_previo: dict | None=None) -> bool:
    """Maneja la selección de billetera en slot filling (movimientos simples y lotes).
Si no aplica o el mensaje no corresponde a una billetera, retorna False.
Si aplica y procesa la selección o error de rango/moneda, persiste y retorna True."""
    if not (_es_pregunta_billetera(conv_activa) and conv_activa.intent_detectado != 'transferir_fondos' and (not (estado_previo and (estado_previo.get('intent_origen') == 'transferir_fondos' or estado_previo.get('tipo_operacion') in ('transferencia', 'extraccion', 'compra_usd', 'venta_usd'))))):
        return False
    from app.routers.whatsapp_ia import _construir_propuesta_transaccion
    estado_previo_bill = dict(conv_activa.slot_filling_estado) if conv_activa.slot_filling_estado else {}
    tipo_mov = estado_previo_bill.get('tipo', 'egreso')
    moneda_str = estado_previo_bill.get('moneda', 'ARS')
    moneda_sel = Moneda.USD if moneda_str == 'USD' else Moneda.ARS
    billeteras_activas = _obtener_billeteras_activas(usuario.id, db, moneda=moneda_sel)
    max_opciones = min(len(billeteras_activas), 8)
    mensaje_limpio = mensaje_texto.strip()
    billetera_elegida = None
    es_seleccion = False
    if mensaje_limpio.isdigit():
        numero = int(mensaje_limpio)
        if 1 <= numero <= max_opciones:
            billetera_elegida = billeteras_activas[numero - 1]
            es_seleccion = True
        else:
            whatsapp_service.enviar_whatsapp(from_number, f'Opción inválida. Elegí un número del 1 al {max_opciones}.')
            return True
    else:
        b_match, cands = resolver_billetera_cascada(mensaje_limpio, billeteras_activas)
        if b_match:
            billetera_elegida = b_match
            es_seleccion = True
        elif len(cands) > 1:
            menu_acotado = _generar_menu_billeteras(cands, tipo=tipo_mov)
            whatsapp_service.enviar_whatsapp(from_number, f'¿A cuál te referís?\n{menu_acotado}')
            return True
        else:
            todas = _obtener_billeteras_activas(usuario.id, db)
            b_otra, _ = resolver_billetera_cascada(mensaje_limpio, todas)
            if b_otra and b_otra.moneda != moneda_sel:
                nom_otra = 'dólares' if b_otra.moneda == Moneda.USD else 'pesos'
                nom_mov = 'pesos' if moneda_sel == Moneda.ARS else 'dólares'
                menu = _generar_menu_billeteras(billeteras_activas, tipo=tipo_mov)
                whatsapp_service.enviar_whatsapp(from_number, f'No podés usar una billetera en {nom_otra} para un movimiento en {nom_mov}.\n{menu}')
                return True
    if es_seleccion and billetera_elegida:
        if estado_previo_bill.get('tipo_flujo') == 'lote_slot_filling':
            for op_idx in estado_previo_bill.get('ops_pendientes_ids', []):
                for op in estado_previo_bill.get('operaciones', []):
                    if op.get('id') == op_idx:
                        op['billetera'] = billetera_elegida.nombre
                        op['billetera_id'] = str(billetera_elegida.id)
                        op['resuelta'] = True
            ops_restantes = [op for op in estado_previo_bill.get('operaciones', []) if not op.get('resuelta')]
            if ops_restantes:
                mon_r = ops_restantes[0].get('moneda', 'ARS')
                tipo_r = ops_restantes[0].get('tipo', 'egreso')
                todas_igual = all((op.get('moneda', 'ARS') == mon_r and op.get('tipo', 'egreso') == tipo_r for op in ops_restantes))
                bills_mon = _obtener_billeteras_activas(usuario.id, db, moneda=Moneda.USD if mon_r == 'USD' else Moneda.ARS)
                if todas_igual:
                    enc = '¿A qué billetera entraron los ingresos?' if tipo_r == 'ingreso' else '¿Desde qué billetera salieron los gastos?'
                    pregunta_sgte = _generar_menu_billeteras(bills_mon, tipo=tipo_r, encabezado=enc)
                    nuevas_pend = [op['id'] for op in ops_restantes]
                else:
                    op_sgte = ops_restantes[0]
                    m_fmt = formatear_monto(float(op_sgte['monto']), Moneda.USD if mon_r == 'USD' else Moneda.ARS)
                    c_disp = _nombre_corto_categoria(op_sgte.get('categoria'))
                    if tipo_r == 'ingreso':
                        enc = f'¿A qué billetera entró el ingreso de {m_fmt} en {c_disp}?'
                    else:
                        enc = f'¿Desde qué billetera salió el gasto de {m_fmt} en {c_disp}?'
                    pregunta_sgte = _generar_menu_billeteras(bills_mon, tipo=tipo_r, encabezado=enc)
                    nuevas_pend = [op_sgte['id']]
                estado_previo_bill['ops_pendientes_ids'] = nuevas_pend
                estado_previo_bill['moneda'] = mon_r
                estado_previo_bill['tipo'] = tipo_r
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=pregunta_sgte, intent_detectado='slot_filling', entidades=estado_previo_bill, accion_ejecutada=None, confianza=Decimal('1.000'), slot_filling_activo=True, slot_filling_estado=estado_previo_bill)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, pregunta_sgte)
                return True
            ops_todas = estado_previo_bill.get('operaciones', [])
            entidades_lote = dict(ops_todas[0])
            entidades_lote['transacciones_adicionales'] = [dict(o) for o in ops_todas[1:]]
            hay_lote_dup, m_dup, mon_dup, cat_dup = _detectar_duplicados_en_lote(entidades_lote)
            if hay_lote_dup:
                m_dup_fmt = formatear_monto(float(m_dup), Moneda.USD if mon_dup == 'USD' else Moneda.ARS)
                pregunta_lote = f'Mandaste 2 movimientos iguales de {m_dup_fmt} en {cat_dup} desde {billetera_elegida.nombre}. ¿Son dos gastos distintos o se te repitió?'
                intent_val = 'verificar_lote_duplicado'
                slot_activo_val = True
                slot_estado_val = {**entidades_lote, 'tipo_flujo': 'verificacion_lote_duplicado', 'billetera_resuelta_nombre': billetera_elegida.nombre, 'datos_faltantes': ['confirmar_lote']}
                propuesta_msg = pregunta_lote
            else:
                propuesta_msg = _construir_propuesta_transaccion(entidades_lote, billetera_nombre=billetera_elegida.nombre, se_asumio_principal=False, billeteras_usuario=_obtener_billeteras_activas(usuario.id, db))
                intent_val = 'registrar_transaccion'
                slot_activo_val = False
                slot_estado_val = None
            nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=propuesta_msg, intent_detectado=intent_val, entidades=entidades_lote, accion_ejecutada=None, confianza=Decimal('1.000'), slot_filling_activo=slot_activo_val, slot_filling_estado=slot_estado_val)
            db.add(nueva_conv)
            db.commit()
            whatsapp_service.enviar_whatsapp(from_number, propuesta_msg)
            return True
        clave_bill = 'billetera_destino' if tipo_mov == 'ingreso' else 'billetera_origen'
        clave_otra = 'billetera_origen' if tipo_mov == 'ingreso' else 'billetera_destino'
        estado_previo_bill[clave_bill] = billetera_elegida.nombre
        estado_previo_bill.pop(clave_otra, None)
        if 'datos_faltantes' in estado_previo_bill:
            estado_previo_bill['datos_faltantes'] = [d for d in estado_previo_bill['datos_faltantes'] if d not in ('billetera_origen', 'billetera_destino', 'billetera')]
        conv_activa.slot_filling_activo = False
        db.flush()
        sub_cobrada, tx_cobrada = (None, None)
        if tipo_mov == 'egreso':
            sub_cobrada, tx_cobrada = _buscar_suscripcion_cobrada_periodo_actual(usuario.id, Decimal(str(estado_previo_bill['monto'])), estado_previo_bill.get('servicio') or estado_previo_bill.get('descripcion') or estado_previo_bill.get('concepto') or mensaje_texto, db)
        if sub_cobrada and tx_cobrada:
            m_fmt = formatear_monto(float(tx_cobrada.monto), tx_cobrada.moneda)
            propuesta_msg = f'Aviso: ya se cobró automáticamente {m_fmt} de {sub_cobrada.nombre} este período. ¿Es un gasto aparte o querés cancelarlo?'
            intent_val = 'verificar_duplicado_suscripcion'
            slot_activo_val = True
            slot_estado_val = {**estado_previo_bill, 'tipo_flujo': 'verificacion_duplicado_suscripcion', 'suscripcion_id': str(sub_cobrada.id), 'servicio_nombre': sub_cobrada.nombre, 'billetera_resuelta_nombre': billetera_elegida.nombre, 'datos_faltantes': ['confirmar_gasto_aparte']}
        else:
            cat_id_chk, _ = _resolver_categoria_y_subcategoria(estado_previo_bill.get('categoria'), usuario.id, db, tipo=tipo_mov)
            tx_dup = _buscar_transaccion_duplicada_reciente(usuario.id, Decimal(str(estado_previo_bill['monto'])), moneda_sel, cat_id_chk, db)
            if tx_dup:
                hora_dup = tx_dup.fecha_creacion.astimezone(TZ_ARGENTINA).strftime('%H:%M')
                cat_disp = _nombre_corto_categoria(estado_previo_bill.get('categoria'))
                m_fmt = formatear_monto(float(estado_previo_bill['monto']), moneda_sel)
                propuesta_msg = f'A las {hora_dup} ya registraste {m_fmt} en {cat_disp}. ¿Es un movimiento nuevo o se te repitió?'
                intent_val = 'verificar_duplicado'
                slot_activo_val = True
                slot_estado_val = {**estado_previo_bill, 'tipo_flujo': 'verificacion_duplicado', 'billetera_resuelta_nombre': billetera_elegida.nombre, 'hora_anterior': hora_dup, 'datos_faltantes': ['confirmar_duplicado']}
            else:
                propuesta_msg = _construir_propuesta_transaccion(estado_previo_bill, billetera_elegida.nombre, se_asumio_principal=False, billetera_moneda=billetera_elegida.moneda)
                intent_val = 'registrar_transaccion'
                slot_activo_val = False
                slot_estado_val = None
        nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=propuesta_msg, intent_detectado=intent_val, entidades=estado_previo_bill, accion_ejecutada=None, confianza=Decimal('1.000'), slot_filling_activo=slot_activo_val, slot_filling_estado=slot_estado_val)
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, propuesta_msg)
        return True
    return False


def manejar_verificaciones_slot_filling(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """Maneja las respuestas a verificaciones de slot filling pendientes:
    duplicados simples, lotes duplicados, duplicados de suscripción, ambigüedad y alta de suscripción.
    Si procesa o cancela el flujo, persiste y retorna True.
    Si no aplica o el usuario ignora la pregunta con otro mensaje, retorna False.
    """
    from app.routers.whatsapp_ia import _registrar_movimiento_directo, MESES_ES_GEN

    conv_activa_dup = _buscar_slot_filling_activo(usuario.id, db)
    if conv_activa_dup and conv_activa_dup.slot_filling_estado:
        tipo_flujo = conv_activa_dup.slot_filling_estado.get('tipo_flujo')
        if tipo_flujo == 'verificacion_duplicado':
            if _es_confirmacion_nuevo_movimiento(mensaje_texto):
                entidades_pend = conv_activa_dup.slot_filling_estado
                tx_creada, msg_confirm = _registrar_movimiento_directo(usuario, entidades_pend, db)
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = str(tx_creada.id) if tx_creada else 'error'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=msg_confirm, intent_detectado='confirmar_duplicado', entidades=entidades_pend, accion_ejecutada=str(tx_creada.id) if tx_creada else None, confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
                return True
            elif _es_descarte_duplicado(mensaje_texto):
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = 'descartado_por_duplicado'
                msg_desc = 'Listo, no anoto nada.'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=msg_desc, intent_detectado='cancelar', entidades={}, accion_ejecutada='descartado_por_duplicado', confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_desc)
                return True
        elif tipo_flujo == 'verificacion_lote_duplicado':
            if _es_confirmacion_lote_ambos(mensaje_texto):
                entidades_pend = conv_activa_dup.slot_filling_estado
                tx_creada, msg_confirm = _registrar_movimiento_directo(usuario, entidades_pend, db, registrar_adicionales=True)
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = str(tx_creada.id) if tx_creada else 'error'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=msg_confirm, intent_detectado='confirmar_lote', entidades=entidades_pend, accion_ejecutada=str(tx_creada.id) if tx_creada else None, confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
                return True
            elif _es_confirmacion_lote_uno_solo(mensaje_texto):
                entidades_pend = dict(conv_activa_dup.slot_filling_estado)
                entidades_pend['transacciones_adicionales'] = []
                tx_creada, msg_confirm = _registrar_movimiento_directo(usuario, entidades_pend, db, registrar_adicionales=False)
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = str(tx_creada.id) if tx_creada else 'error'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=msg_confirm, intent_detectado='confirmar_lote_uno', entidades=entidades_pend, accion_ejecutada=str(tx_creada.id) if tx_creada else None, confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
                return True
            elif _es_descarte_duplicado(mensaje_texto):
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = 'descartado_por_duplicado'
                msg_desc = 'Listo, no anoto nada.'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=msg_desc, intent_detectado='cancelar', entidades={}, accion_ejecutada='descartado_por_duplicado', confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_desc)
                return True
        elif tipo_flujo == 'verificacion_duplicado_suscripcion':
            if _es_confirmacion_gasto_aparte(mensaje_texto):
                entidades_pend = conv_activa_dup.slot_filling_estado
                tx_creada, msg_confirm = _registrar_movimiento_directo(usuario, entidades_pend, db)
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = str(tx_creada.id) if tx_creada else 'error'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=msg_confirm, intent_detectado='confirmar_gasto_aparte', entidades=entidades_pend, accion_ejecutada=str(tx_creada.id) if tx_creada else None, confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
                return True
            elif _es_cancelacion(mensaje_texto) or _es_descarte_duplicado(mensaje_texto):
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = 'descartado_por_duplicado'
                msg_desc = 'Listo, cancelado.'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=msg_desc, intent_detectado='cancelar', entidades={}, accion_ejecutada='cancelada', confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_desc)
                return True
        elif tipo_flujo == 'ambiguedad_suscripcion':
            norm_amb = normalizar_texto(mensaje_texto)
            srv_nom = conv_activa_dup.slot_filling_estado.get('servicio')
            if any((w in norm_amb for w in ['suscripcion', 'suscripción', 'me suscribi', 'me suscribí', 'es una suscripcion', 'es suscripcion', 'abono'])):
                srv_cat = buscar_servicio_por_texto(srv_nom) if srv_nom else None
                if srv_cat and srv_cat.get('frecuencia_sugerida'):
                    frec_tipica = srv_cat['frecuencia_sugerida']
                    msg_frec = f'¿Con qué frecuencia se paga {srv_nom}? (lo habitual es {frec_tipica}: mensual, bimestral, trimestral, semestral o anual)'
                else:
                    msg_frec = f'¿Con qué frecuencia se paga {srv_nom}? (mensual, bimestral, trimestral, semestral o anual)'
                conv_activa_dup.slot_filling_estado = {'tipo_flujo': 'crear_suscripcion_frecuencia', 'servicio': srv_nom, 'monto': conv_activa_dup.slot_filling_estado.get('monto'), 'moneda': conv_activa_dup.slot_filling_estado.get('moneda', 'ARS')}
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_frec)
                return True
            elif any((w in norm_amb for w in ['gasto', 'unico', 'único', 'gasto unico', 'es un gasto'])):
                msg_monto = f'¿Cuánto gastaste en {srv_nom}?'
                conv_activa_dup.slot_filling_estado = {'tipo_flujo': 'pedir_monto_gasto', 'concepto': srv_nom, 'categoria': 'Entretenimiento'}
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_monto)
                return True
        elif tipo_flujo == 'crear_suscripcion_frecuencia':
            frec = _extraer_frecuencia_mencionada(mensaje_texto)
            if frec:
                srv_nom = conv_activa_dup.slot_filling_estado.get('servicio')
                monto_val = conv_activa_dup.slot_filling_estado.get('monto')
                moneda_val = conv_activa_dup.slot_filling_estado.get('moneda', 'ARS')
                if monto_val is not None:
                    billetera_obj = db.query(Billetera).filter(Billetera.usuario_id == usuario.id, Billetera.es_principal == True).first()
                    if not billetera_obj:
                        billetera_obj = db.query(Billetera).filter(Billetera.usuario_id == usuario.id).first()
                    proximo_cobro = suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), frec)
                    fecha_fmt = f'{proximo_cobro.day} de {MESES_ES_GEN[proximo_cobro.month - 1]}'
                    mon_enum = Moneda.USD if moneda_val == 'USD' else Moneda.ARS
                    monto_fmt = formatear_monto(float(monto_val), mon_enum)
                    medio_pago_txt = f'desde {billetera_obj.nombre}' if billetera_obj else ''
                    propuesta_msg = f'Voy a programar la suscripción a {srv_nom}: {monto_fmt} {frec} {medio_pago_txt}, primer cobro el {fecha_fmt}. ¿Confirmás?'
                    conv_activa_dup.slot_filling_activo = False
                    conv_activa_dup.accion_ejecutada = 'propuesta_creada'
                    db.flush()
                    srv_cat = buscar_servicio_por_texto(srv_nom) if srv_nom else None
                    cat_sugerida = srv_cat.get('categoria_sugerida') if srv_cat else 'Entretenimiento'
                    nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=propuesta_msg, intent_detectado='agregar_suscripcion', entidades={'servicio': srv_nom, 'monto': float(monto_val), 'moneda': moneda_val, 'frecuencia': frec, 'billetera_id': str(billetera_obj.id) if billetera_obj else None, 'medio_pago_txt': medio_pago_txt, 'proximo_cobro': proximo_cobro.isoformat(), 'categoria': cat_sugerida}, accion_ejecutada=None, confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                    db.add(nueva_conv)
                    db.commit()
                    whatsapp_service.enviar_whatsapp(from_number, propuesta_msg)
                    return True
                else:
                    msg_monto = f'¿Cuánto pagás por {srv_nom}?'
                    conv_activa_dup.slot_filling_estado['frecuencia'] = frec
                    conv_activa_dup.slot_filling_estado['tipo_flujo'] = 'crear_suscripcion_monto'
                    db.commit()
                    whatsapp_service.enviar_whatsapp(from_number, msg_monto)
                    return True
            else:
                whatsapp_service.enviar_whatsapp(from_number, 'Por favor elegí una frecuencia: mensual, bimestral, trimestral, semestral o anual.')
                return True
        elif tipo_flujo == 'duplicado_suscripcion_existente':
            norm_dup = normalizar_texto(mensaje_texto)
            if any((w in norm_dup for w in ['otra', 'otra igual', 'crear otra', 'si', 'sí', 'registrar otra'])):
                srv_nom = conv_activa_dup.slot_filling_estado.get('servicio')
                monto_val = conv_activa_dup.slot_filling_estado.get('monto')
                moneda_val = conv_activa_dup.slot_filling_estado.get('moneda', 'ARS')
                frec = conv_activa_dup.slot_filling_estado.get('frecuencia') or 'mensual'
                billetera_obj = db.query(Billetera).filter(Billetera.usuario_id == usuario.id, Billetera.es_principal == True).first()
                if not billetera_obj:
                    billetera_obj = db.query(Billetera).filter(Billetera.usuario_id == usuario.id).first()
                proximo_cobro = suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), frec)
                fecha_fmt = f'{proximo_cobro.day} de {MESES_ES_GEN[proximo_cobro.month - 1]}'
                mon_enum = Moneda.USD if moneda_val == 'USD' else Moneda.ARS
                monto_fmt = formatear_monto(float(monto_val), mon_enum)
                medio_pago_txt = f'desde {billetera_obj.nombre}' if billetera_obj else ''
                propuesta_msg = f'Voy a programar la suscripción a {srv_nom}: {monto_fmt} {frec} {medio_pago_txt}, primer cobro el {fecha_fmt}. ¿Confirmás?'
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = 'propuesta_creada'
                db.flush()
                srv_cat = buscar_servicio_por_texto(srv_nom) if srv_nom else None
                cat_sugerida = srv_cat.get('categoria_sugerida') if srv_cat else 'Entretenimiento'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=propuesta_msg, intent_detectado='agregar_suscripcion', entidades={'servicio': srv_nom, 'monto': float(monto_val), 'moneda': moneda_val, 'frecuencia': frec, 'billetera_id': str(billetera_obj.id) if billetera_obj else None, 'medio_pago_txt': medio_pago_txt, 'proximo_cobro': proximo_cobro.isoformat(), 'categoria': cat_sugerida}, accion_ejecutada=None, confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, propuesta_msg)
                return True
            else:
                conv_activa_dup.slot_filling_activo = False
                conv_activa_dup.accion_ejecutada = 'cancelada'
                msg_desc = 'Listo, cancelado.'
                nueva_conv = ConversacionWpp(usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO, transcripcion=None, mensaje_bot=msg_desc, intent_detectado='cancelar', entidades={}, accion_ejecutada='cancelada', confianza=Decimal('1.000'), slot_filling_activo=False, slot_filling_estado=None)
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_desc)
                return True

    return False
