"""
Etapa de estados pendientes y handlers determinísticos para WhatsApp.
Evalúa en orden estricto los handlers determinísticos de entrada antes de llamar a la IA.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import structlog

from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.db_lookups import (
    _buscar_propuesta_pendiente,
    _buscar_slot_filling_activo,
    _buscar_slot_filling_vencido,
)
from app.routers.whatsapp.deshacer_corregir import _evaluar_correccion_billetera
from app.routers.whatsapp.detectors import _es_pregunta_billetera
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
from app.routers.whatsapp.metas import (
    manejar_aporte_meta,
    manejar_cancelacion_aporte_meta,
    manejar_confirmacion_aporte_meta,
)
from app.routers.whatsapp.propuestas import _construir_propuesta_transaccion
from app.routers.whatsapp.resolvers_cascada import _generar_menu_billeteras
from app.services import whatsapp_service

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

    # 1. Chequeo determinístico de saludo rioplatense (Tarea 4)
    if manejar_saludo(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
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
                whatsapp_service.enviar_whatsapp(from_number, err_moneda)
                ctx.terminado = True
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
                whatsapp_service.enviar_whatsapp(from_number, f"¿A cuál te referís?\n{menu}")
                ctx.terminado = True
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
                whatsapp_service.enviar_whatsapp(from_number, nuevo_msg)
                ctx.terminado = True
                return

    # Chequeo determinístico de cancelación para aporte a meta
    if manejar_cancelacion_aporte_meta(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 3. Chequeo determinístico de cancelación (Tarea 5)
    if manejar_cancelacion(mensaje_texto, usuario, db, from_number, wamid=wamid):
        ctx.terminado = True
        return

    # 3. Chequeo de respuesta a verificación de duplicado o lote pendiente
    if manejar_verificaciones_slot_filling(
        mensaje_texto, usuario, db, from_number, wamid=wamid
    ):
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
