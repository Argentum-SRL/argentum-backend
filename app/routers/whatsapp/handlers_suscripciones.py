"""Handlers de suscripciones para WhatsApp (consulta, baja, cambio precio, ambigüedad, alta)."""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy.orm import Session
from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
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
from app.core.catalogo_suscripciones import buscar_servicio_por_texto
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina
from app.utils.formato import formatear_monto


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


def manejar_alta_suscripcion(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Detección determinística de alta de suscripción.
    """
    from app.routers.whatsapp.constantes import MESES_ES_GEN

    if _es_intento_alta_suscripcion(mensaje_texto):
        srv_nom = _extraer_nombre_servicio(mensaje_texto)
        monto_sub, mon_sub = _extraer_monto_y_moneda_suscripcion(mensaje_texto)
        if srv_nom:
            # Verificar si ya tiene suscripción activa a este servicio (Tarea 4.9)
            sub_act = _buscar_suscripcion_activa_por_nombre(usuario.id, srv_nom, db)
            if sub_act:
                pv = suscripcion_service.obtener_precio_vigente(db, sub_act.id)
                m_val = pv.monto if pv else Decimal("0")
                mon_val = pv.moneda if pv else "ARS"
                mon_enum = Moneda.USD if mon_val == "USD" else Moneda.ARS
                m_fmt = formatear_monto(float(m_val), mon_enum)
                frec_str = sub_act.frecuencia.value if hasattr(sub_act.frecuencia, "value") else str(sub_act.frecuencia)
                msg_aviso = f"Ya tenés una suscripción activa a {sub_act.nombre} por {m_fmt} {frec_str}. ¿Querés registrar otra igual o te referías a la existente?"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg_aviso,
                    intent_detectado="agregar_suscripcion",
                    entidades={
                        "servicio": srv_nom,
                        "monto": float(monto_sub) if monto_sub else None,
                        "moneda": mon_sub,
                    },
                    accion_ejecutada=None,
                    confianza=Decimal("1.000"),
                    slot_filling_activo=True,
                    slot_filling_estado={
                        "tipo_flujo": "duplicado_suscripcion_existente",
                        "servicio": srv_nom,
                        "monto": float(monto_sub) if monto_sub else None,
                        "moneda": mon_sub,
                    },
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_aviso)
                return True

            # Verificar frecuencia (Tarea 4.3)
            frecuencia = _extraer_frecuencia_mencionada(mensaje_texto)
            if not frecuencia:
                srv_cat = buscar_servicio_por_texto(srv_nom)
                if srv_cat and srv_cat.get("frecuencia_sugerida"):
                    frec_tipica = srv_cat["frecuencia_sugerida"]
                    msg_frec = f"¿Con qué frecuencia se paga {srv_nom}? (lo habitual es {frec_tipica}: mensual, bimestral, trimestral, semestral o anual)"
                else:
                    msg_frec = f"¿Con qué frecuencia se paga {srv_nom}? (mensual, bimestral, trimestral, semestral o anual)"

                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg_frec,
                    intent_detectado="agregar_suscripcion",
                    entidades={
                        "servicio": srv_nom,
                        "monto": float(monto_sub) if monto_sub else None,
                        "moneda": mon_sub,
                    },
                    accion_ejecutada=None,
                    confianza=Decimal("1.000"),
                    slot_filling_activo=True,
                    slot_filling_estado={
                        "tipo_flujo": "crear_suscripcion_frecuencia",
                        "servicio": srv_nom,
                        "monto": float(monto_sub) if monto_sub else None,
                        "moneda": mon_sub,
                    },
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_frec)
                return True

            if monto_sub is not None:
                # Todo listo para proponer suscripción (Tarea 4.6)
                billetera_obj = db.query(Billetera).filter(Billetera.usuario_id == usuario.id, Billetera.es_principal == True).first()
                if not billetera_obj:
                    billetera_obj = db.query(Billetera).filter(Billetera.usuario_id == usuario.id).first()

                proximo_cobro = suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), frecuencia)
                fecha_fmt = f"{proximo_cobro.day} de {MESES_ES_GEN[proximo_cobro.month - 1]}"
                mon_enum = Moneda.USD if mon_sub == "USD" else Moneda.ARS
                monto_fmt = formatear_monto(float(monto_sub), mon_enum)
                medio_pago_txt = f"desde {billetera_obj.nombre}" if billetera_obj else ""
                propuesta_msg = f"Voy a programar la suscripción a {srv_nom}: {monto_fmt} {frecuencia} {medio_pago_txt}, primer cobro el {fecha_fmt}. ¿Confirmás?"

                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=propuesta_msg,
                    intent_detectado="agregar_suscripcion",
                    entidades={
                        "servicio": srv_nom,
                        "monto": float(monto_sub),
                        "moneda": mon_sub,
                        "frecuencia": frecuencia,
                        "billetera_id": str(billetera_obj.id) if billetera_obj else None,
                        "medio_pago_txt": medio_pago_txt,
                        "proximo_cobro": proximo_cobro.isoformat(),
                    },
                    accion_ejecutada=None,
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, propuesta_msg)
                return True

    return False
