"""
Etapa de estados pendientes y handlers determinísticos para WhatsApp.
Evalúa en orden estricto los handlers determinísticos de entrada antes de llamar a la IA.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
import structlog
from sqlalchemy import select

from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import TipoTransaccion
from app.models.usuario import Moneda
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.db_lookups import (
    PLAZO_DESHACER_CORREGIR_MINUTOS,
    _buscar_propuesta_pendiente,
    _buscar_slot_filling_activo,
    _buscar_slot_filling_vencido,
    _obtener_billeteras_activas,
)
from app.routers.whatsapp.deshacer_corregir import (
    _detectar_correccion_ultimo_movimiento,
    _evaluar_correccion_billetera,
)
from app.routers.whatsapp.detectors import (
    _es_cancelacion,
    _es_pedido_deshacer,
    _es_pregunta_billetera,
)
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
from app.routers.whatsapp.handlers_conveniencia import (
    manejar_consulta_conveniencia,
    manejar_respuesta_pendiente_conveniencia,
)
from app.routers.whatsapp.handlers_permitirse import (
    manejar_consulta_permitirse,
    manejar_respuesta_pendiente_permitirse,
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
from app.routers.whatsapp.metas import (
    manejar_aporte_meta,
    manejar_cancelacion_aporte_meta,
    manejar_confirmacion_aporte_meta,
)
from app.routers.whatsapp.parsers import _resolver_y_validar_fecha
from app.routers.whatsapp.propuestas import (
    _construir_propuesta_credito,
    _construir_propuesta_transaccion,
)
from app.routers.whatsapp.resolvers_cascada import (
    _generar_menu_billeteras,
    resolver_billetera_cascada,
)
from app.services import calendario_tarjeta_service, whatsapp_service

logger = structlog.get_logger(__name__)


def procesar_estados_y_handlers_deterministicos(ctx: ContextoMensaje) -> None:
    """
    Evalúa en orden estricto los handlers determinísticos de entrada.
    Si algún handler maneja el mensaje, se marca ctx.terminado = True y se retorna.
    Si continúa, guarda conv_activa y estado_previo en ctx.
    """
    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario
    db = ctx.db
    from_number = ctx.from_number
    wamid = ctx.wamid

    # 0. Chequeo determinístico de pedido de pago de resumen de tarjeta (Tarea 7)
    if manejar_pago_resumen(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 0.5 Respuesta a pregunta pendiente de '¿Me lo puedo permitir?' (bloque T1)
    if manejar_respuesta_pendiente_permitirse(ctx):
        ctx.terminado = True
        return

    # 0.55 Respuesta a pregunta pendiente de 'Cuotas vs contado' (bloque T2)
    if manejar_respuesta_pendiente_conveniencia(ctx):
        ctx.terminado = True
        return

    # 1. Chequeo determinístico de saludo rioplatense (Tarea 4)
    if manejar_saludo(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 2. Si hay una propuesta pendiente, manejar anulación o corrección determinística
    propuesta_pendiente = _buscar_propuesta_pendiente(usuario.id, db)
    propuesta_30 = _buscar_propuesta_pendiente(
        usuario.id, db, minutos=PLAZO_DESHACER_CORREGIR_MINUTOS
    )
    ultima_conv = db.execute(
        select(ConversacionWpp)
        .where(ConversacionWpp.usuario_id == usuario.id)
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .limit(1)
    ).scalars().first()
    conv_activa = _buscar_slot_filling_activo(usuario.id, db)
    es_ultima_prop = bool(
        ultima_conv
        and propuesta_30
        and ultima_conv.id == propuesta_30.id
        and not conv_activa
    )
    prop_para_editar = (
        propuesta_pendiente if (propuesta_pendiente and not conv_activa) else None
    )
    if not prop_para_editar and es_ultima_prop:
        prop_para_editar = propuesta_30

    fac_pend_data = (
        (propuesta_30.entidades or {}).get("factura")
        if propuesta_30 and isinstance(propuesta_30.entidades, dict)
        else None
    )
    es_factura_con_venc = bool(
        isinstance(fac_pend_data, dict) and fac_pend_data.get("vencimientos")
    )
    if (
        es_ultima_prop
        and not es_factura_con_venc
        and (_es_cancelacion(mensaje_texto) or _es_pedido_deshacer(mensaje_texto))
    ):
        propuesta_30.accion_ejecutada = "cancelada"
        msg_cancel = "Listo, no lo anoto."
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
        ctx.terminado = True
        return

    if prop_para_editar and not conv_activa:
        es_corr, b_nueva, cands, err_moneda = _evaluar_correccion_billetera(
            mensaje_texto, usuario.id, prop_para_editar, db
        )
        if es_corr:
            if err_moneda:
                whatsapp_service.enviar_whatsapp(from_number, err_moneda)
                ctx.terminado = True
                return
            if len(cands) > 1:
                tipo_prop = prop_para_editar.entidades.get("tipo", "egreso")
                menu = _generar_menu_billeteras(cands, tipo=tipo_prop)
                prop_para_editar.slot_filling_activo = True
                clave_bill = "billetera_destino" if tipo_prop == "ingreso" else "billetera_origen"
                prop_para_editar.slot_filling_estado = {
                    **prop_para_editar.entidades,
                    "datos_faltantes": [clave_bill],
                }
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, menu)
                ctx.terminado = True
                return
            if b_nueva:
                tipo_prop = prop_para_editar.entidades.get("tipo", "egreso")
                clave_bill = "billetera_destino" if tipo_prop == "ingreso" else "billetera_origen"
                clave_otra = "billetera_origen" if tipo_prop == "ingreso" else "billetera_destino"

                entidades_nuevas = dict(prop_para_editar.entidades)
                entidades_nuevas[clave_bill] = b_nueva.nombre
                entidades_nuevas["billetera"] = b_nueva.nombre
                entidades_nuevas.pop(clave_otra, None)

                if entidades_nuevas.get("tipo_flujo") == "propuesta_usd_pesos":
                    from app.routers.whatsapp.dolares_desde_pesos import texto_propuesta_usd_pesos
                    nuevo_msg = texto_propuesta_usd_pesos(entidades_nuevas, b_nueva.nombre)
                else:
                    nuevo_msg = _construir_propuesta_transaccion(
                        entidades_nuevas,
                        b_nueva.nombre,
                        se_asumio_principal=False,
                        billetera_moneda=b_nueva.moneda,
                        db=db,
                        usuario_id=usuario.id,
                    )

                prop_para_editar.accion_ejecutada = "descartada_por_nueva_operacion"
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
                whatsapp_service.enviar_whatsapp(from_number, nuevo_msg)
                ctx.terminado = True
                return

        if es_ultima_prop and prop_para_editar.entidades:
            ents_act = dict(prop_para_editar.entidades)
            tipo_p = ents_act.get("tipo", "egreso")
            mon_p = Moneda.USD if ents_act.get("moneda") == "USD" else Moneda.ARS
            f_p, _ = _resolver_y_validar_fecha(ents_act.get("fecha"))
            bills_u = _obtener_billeteras_activas(usuario.id, db)
            clave_b = "billetera_destino" if tipo_p == "ingreso" else "billetera_origen"
            b_nom_p = ents_act.get(clave_b) or ents_act.get("billetera")
            b_obj_p, _ = (
                resolver_billetera_cascada(b_nom_p, bills_u)
                if b_nom_p
                else (None, [])
            )
            proxy_tx = SimpleNamespace(
                monto=Decimal(str(ents_act.get("monto", 0))),
                moneda=mon_p,
                tipo=TipoTransaccion.INGRESO if tipo_p == "ingreso" else TipoTransaccion.EGRESO,
                billetera_id=b_obj_p.id if b_obj_p else None,
                fecha=f_p,
            )
            es_cp, cambios_p, err_cp = _detectar_correccion_ultimo_movimiento(
                mensaje_texto, usuario.id, db, proxy_tx
            )
            if err_cp:
                whatsapp_service.enviar_whatsapp(from_number, err_cp)
                ctx.terminado = True
                return
            if es_cp and cambios_p:
                if "monto" in cambios_p:
                    ents_act["monto"] = cambios_p["monto"]
                    if ents_act.get("tarjeta_id"):
                        ents_act["monto_total"] = cambios_p["monto"]
                        cant_c = int(ents_act.get("cantidad_cuotas") or 1)
                        ents_act["monto_cuota"] = round(cambios_p["monto"] / cant_c, 2)
                if "billetera_nombre" in cambios_p:
                    ents_act[clave_b] = cambios_p["billetera_nombre"]
                    ents_act["billetera"] = cambios_p["billetera_nombre"]
                    b_nom_p = cambios_p["billetera_nombre"]
                if "fecha" in cambios_p:
                    ents_act["fecha"] = cambios_p["fecha"]
                if "categoria_nombre" in cambios_p:
                    ents_act["categoria"] = cambios_p["categoria_nombre"]
                    ents_act.pop("categoria_por_descarte", None)

                b_obj_n = (
                    db.get(Billetera, UUID(str(cambios_p["billetera_id"])))
                    if "billetera_id" in cambios_p
                    else b_obj_p
                )
                b_mon_n = b_obj_n.moneda if isinstance(b_obj_n, Billetera) else mon_p
                if ents_act.get("tipo_flujo") == "propuesta_usd_pesos":
                    from app.routers.whatsapp.dolares_desde_pesos import (
                        texto_propuesta_usd_pesos,
                    )
                    nuevo_msg = texto_propuesta_usd_pesos(ents_act, b_nom_p or "tu cuenta")
                elif (
                    ents_act.get("tipo_flujo") == "propuesta_credito"
                    and ents_act.get("tarjeta_id")
                ):
                    tarj_obj = db.get(TarjetaCredito, UUID(str(ents_act["tarjeta_id"])))
                    if tarj_obj:
                        f_c, _ = _resolver_y_validar_fecha(ents_act.get("fecha"))
                        _, pv = calendario_tarjeta_service.resumen_de_compra(
                            tarj_obj, f_c, proximo_resumen=False
                        )
                        nuevo_msg = _construir_propuesta_credito(
                            ents_act,
                            tarj_obj,
                            int(ents_act.get("cantidad_cuotas") or 1),
                            Decimal(str(ents_act.get("monto_cuota", ents_act["monto"]))),
                            Decimal(str(ents_act.get("monto_total", ents_act["monto"]))),
                            pv,
                            db=db,
                            usuario_id=usuario.id,
                        )
                    else:
                        nuevo_msg = _construir_propuesta_transaccion(
                            ents_act, b_nom_p, False, b_mon_n, db=db, usuario_id=usuario.id
                        )
                else:
                    nuevo_msg = _construir_propuesta_transaccion(
                        ents_act,
                        b_nom_p,
                        se_asumio_principal=False,
                        billetera_moneda=b_mon_n,
                        db=db,
                        usuario_id=usuario.id,
                    )

                prop_para_editar.accion_ejecutada = "descartada_por_nueva_operacion"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=nuevo_msg,
                    intent_detectado="registrar_transaccion",
                    entidades=ents_act,
                    accion_ejecutada=None,
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, nuevo_msg)
                ctx.terminado = True
                return

    # 2.05 Respuestas numeradas para «¿Qué es esta plata?» y elección de compra/gasto (fase_f2b)
    from app.routers.whatsapp.que_es_plata_wpp import manejar_respuesta_numerada
    if manejar_respuesta_numerada(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 2.1 Edición de monto en pesos para propuesta de gasto en dólares desde pesos (fase_f2a)
    from app.routers.whatsapp.dolares_desde_pesos import manejar_monto_pesos_usd
    if manejar_monto_pesos_usd(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # Chequeo determinístico de cancelación para aporte a meta
    if manejar_cancelacion_aporte_meta(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # Factura con vencimiento no pagada (Fase 4c2b2b)
    from app.routers.whatsapp.factura_wpp import manejar_factura_no_pagada
    if manejar_factura_no_pagada(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 3. Respuesta a verificación de duplicado, lote o suscripción pendiente
    if manejar_verificaciones_slot_filling(
        mensaje_texto, usuario, db, from_number, wamid=wamid
    ):
        ctx.terminado = True
        return

    # 3.1 Chequeo determinístico de cancelación (Tarea 5)
    if manejar_cancelacion(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # Chequeo determinístico de confirmación para aporte a meta
    if manejar_confirmacion_aporte_meta(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 4. Chequeo determinístico de confirmación con bloqueo de concurrencia (Tarea 2)
    if manejar_confirmacion(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
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
            msg_vencida = "Pasó mucho tiempo y lo descarté. Mandámelo de nuevo."
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
            whatsapp_service.enviar_whatsapp(from_number, msg_vencida)
            ctx.terminado = True
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
        ctx.terminado = True
        return

    # 5.1 Si hay pregunta de tarjeta pendiente activa
    if manejar_menu_tarjeta(
        mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa
    ):
        ctx.terminado = True
        return

    # 5.2 Si hay aclaración de cuotas pendiente activa ("¿Los $80.000 son el total o el valor de cada cuota?")
    if manejar_aclaracion_cuotas(
        mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa
    ):
        ctx.terminado = True
        return

    # 7. Si el mensaje es únicamente un número sin pregunta pendiente
    if manejar_numero_aislado(
        mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
    ):
        ctx.terminado = True
        return

    # 7.35 Detección determinística de 'Cuotas vs contado' (bloque T2, antes de permitirse)
    if manejar_consulta_conveniencia(ctx):
        ctx.terminado = True
        return

    # 7.4 Detección determinística de '¿me lo puedo permitir?' (Fase 3 F2)
    if manejar_consulta_permitirse(ctx):
        ctx.terminado = True
        return

    # 7.45 Detección determinística de reintegros y devoluciones de compras (fase_f2b)
    from app.routers.whatsapp.reintegro_wpp import manejar_reintegro_wpp
    if manejar_reintegro_wpp(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 7.5 Detección determinística de deshacer (Tarea 2)
    if manejar_deshacer(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 7.6 Detección determinística de corregir (Tarea 3)
    if manejar_corregir(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 7.7 Detección determinística de transferencias / cajero / dólares (Punto 9B)
    if manejar_transferencias(
        mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
    ):
        ctx.terminado = True
        return

    # 7.7b Detección determinística de aporte a meta
    if manejar_aporte_meta(
        mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
    ):
        ctx.terminado = True
        return

    # 7.8 Detección determinística de consultas de suscripciones (Tarea 7)
    if manejar_consulta_suscripciones(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 7.9 Detección determinística de baja de suscripciones (Tarea 5)
    if manejar_baja_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 7.10 Detección determinística de cambio de precio de suscripción (Tarea 6)
    if manejar_cambio_precio_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 7.11 Detección determinística de ambigüedad suscripción vs gasto suelto (Tarea 3.4)
    if manejar_ambiguedad_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 7.12 Detección determinística de alta de suscripción (Tarea 4)
    if manejar_alta_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    if manejar_consulta_gastos(mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa):
        ctx.terminado = True
        return

    # Si ningún handler determinístico manejó el mensaje, persistimos el estado previo y la conv activa en el contexto
    ctx.conv_activa = conv_activa
    ctx.estado_previo = estado_previo
