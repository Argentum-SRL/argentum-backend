"""
app/routers/whatsapp/confirmaciones.py — Confirmación de propuestas de suscripción, cambio de precio, baja y ejecución de intents por WhatsApp.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalogo_suscripciones import resolver_categoria_sugerida
from app.models.conversacion_wpp import ConversacionWpp
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.suscripcion import EstadoSuscripcion, Suscripcion
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.constantes import MESES_ES_GEN, logger
from app.routers.whatsapp.db_lookups import (
    PLAZO_EXPIRACION_ESTADO_MINUTOS,
    _buscar_propuesta_confirmable_mas_reciente,
)
from app.routers.whatsapp.deshacer_corregir import (
    _confirmar_propuesta_corregir,
    _confirmar_propuesta_deshacer,
)
from app.routers.whatsapp.metas import _confirmar_propuesta_aporte_meta
from app.routers.whatsapp.registro import _confirmar_propuesta_transaccion
from app.routers.whatsapp.transferencias import _confirmar_propuesta_transferencia
from app.schemas.suscripcion import ActualizarPrecioRequest, SuscripcionCreate
from app.services import suscripcion_service, whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto



def _confirmar_propuesta_suscripcion(
    usuario: Usuario,
    db: Session,
) -> tuple[Suscripcion | None, str, bool]:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    conv_sub = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "agregar_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .with_for_update()
    ).scalars().first()

    if not conv_sub:
        return None, "No tenés ninguna suscripción pendiente para confirmar.", False

    entidades = conv_sub.entidades or {}
    nombre = entidades.get("servicio")
    monto_val = entidades.get("monto")
    frecuencia_val = entidades.get("frecuencia", "mensual")
    moneda_val = entidades.get("moneda", "ARS")
    billetera_id_str = entidades.get("billetera_id")
    tarjeta_id_str = entidades.get("tarjeta_id")
    proximo_cobro_str = entidades.get("proximo_cobro")

    if not nombre or monto_val is None or not proximo_cobro_str:
        return None, "Faltan datos para crear la suscripción.", False

    proximo_cobro = date.fromisoformat(proximo_cobro_str)
    billetera_id = UUID(billetera_id_str) if billetera_id_str else None
    tarjeta_id = UUID(tarjeta_id_str) if tarjeta_id_str else None

    cat_id, subcat_id = resolver_categoria_sugerida(db, nombre)

    data_create = SuscripcionCreate(
        nombre=nombre,
        monto=Decimal(str(monto_val)),
        moneda=moneda_val,
        frecuencia=frecuencia_val,
        proximo_cobro=proximo_cobro,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=cat_id,
        subcategoria_id=subcat_id,
        vigente_desde=hoy_argentina(),
    )

    nueva_sub = suscripcion_service.crear_suscripcion(db, usuario.id, data_create)

    conv_sub.accion_ejecutada = str(nueva_sub.id)
    db.commit()

    mon_enum = Moneda.USD if moneda_val == "USD" else Moneda.ARS
    monto_fmt = formatear_monto(float(monto_val), mon_enum)
    medio_pago_txt = entidades.get("medio_pago_txt", "")
    fecha_fmt = f"{proximo_cobro.day} de {MESES_ES_GEN[proximo_cobro.month - 1]}"

    # Armado de la frase de categoría asignada
    from app.models.categoria import Categoria
    from app.models.subcategoria import Subcategoria
    cat_obj = db.get(Categoria, nueva_sub.categoria_id) if nueva_sub.categoria_id else None
    cat_nom = cat_obj.nombre if cat_obj else "Otros"
    subcat_obj = db.get(Subcategoria, nueva_sub.subcategoria_id) if nueva_sub.subcategoria_id else None
    subcat_nom = subcat_obj.nombre if subcat_obj else None

    if subcat_nom:
        frase_cat = f"La anoté en {cat_nom} / {subcat_nom}. Si va en otra, la cambiás desde Suscripciones en la web."
    else:
        frase_cat = f"La anoté en {cat_nom}. Si va en otra, la cambiás desde Suscripciones en la web."

    msg_resp = f"Listo. Suscripción a {nombre} por {monto_fmt} {frecuencia_val} programada {medio_pago_txt} (primer cobro el {fecha_fmt}). {frase_cat}"
    return nueva_sub, msg_resp, False


def _confirmar_propuesta_baja_suscripcion(
    usuario: Usuario,
    db: Session,
) -> tuple[Suscripcion | None, str, bool]:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    conv_baja = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "dar_baja_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .with_for_update()
    ).scalars().first()

    if not conv_baja:
        return None, "No tenés ninguna baja de suscripción pendiente para confirmar.", False

    entidades = conv_baja.entidades or {}
    sub_id_str = entidades.get("suscripcion_id")
    nombre = entidades.get("nombre", "el servicio")

    if not sub_id_str:
        return None, "No pude procesar la baja.", False

    sub_id = UUID(sub_id_str)
    sub = suscripcion_service.cambiar_estado(db, usuario.id, sub_id, EstadoSuscripcion.CANCELADA)

    conv_baja.accion_ejecutada = f"baja:{sub.id}"
    db.commit()

    return sub, f"Listo, dimos de baja tu suscripción a {nombre}.", False


def _confirmar_propuesta_cambio_precio(
    usuario: Usuario,
    db: Session,
) -> tuple[HistorialSuscripcion | None, str, bool]:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    conv_cp = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "cambiar_precio_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .with_for_update()
    ).scalars().first()

    if not conv_cp:
        return None, "No tenés ningún cambio de precio pendiente para confirmar.", False

    entidades = conv_cp.entidades or {}
    sub_id_str = entidades.get("suscripcion_id")
    nuevo_monto = entidades.get("nuevo_monto")
    moneda = entidades.get("moneda", "ARS")
    nombre = entidades.get("nombre", "el servicio")

    if not sub_id_str or nuevo_monto is None:
        return None, "No pude procesar el cambio de precio.", False

    sub_id = UUID(sub_id_str)
    req = ActualizarPrecioRequest(
        monto=Decimal(str(nuevo_monto)),
        moneda=moneda,
        vigente_desde=hoy_argentina(),
    )
    hist = suscripcion_service.actualizar_precio(db, usuario.id, sub_id, req)

    conv_cp.accion_ejecutada = f"precio:{hist.id}"
    db.commit()

    mon_enum = Moneda.USD if moneda == "USD" else Moneda.ARS
    nuevo_fmt = formatear_monto(float(nuevo_monto), mon_enum)
    return hist, f"Listo, actualicé el precio de {nombre} a {nuevo_fmt}.", False


def _ejecutar_intent(resultado_ia: dict, usuario: Usuario, db: Session) -> str | None:
    try:
        intent = resultado_ia.get("intent")
        confianza = resultado_ia.get("confianza", 0.0)
        slot_filling = resultado_ia.get("slot_filling", False)

        if intent == "registrar_transaccion" and confianza >= 0.85 and not slot_filling:
            # No crear todavía — el system prompt ya pidió confirmación al usuario
            # Solo retornar None; el estado queda en slot_filling_estado para el turno siguiente
            return None

        elif intent == "confirmar":
            propuesta_ganadora = _buscar_propuesta_confirmable_mas_reciente(usuario.id, db)
            intent_ganador = propuesta_ganadora.intent_detectado if propuesta_ganadora else None

            if intent_ganador == "deshacer":
                tx, msg_resp, ya_conf = _confirmar_propuesta_deshacer(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(tx.id) if tx else None

            elif intent_ganador == "corregir":
                tx, msg_resp, ya_conf = _confirmar_propuesta_corregir(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(tx.id) if tx else None

            elif intent_ganador == "aportar_meta":
                mov, msg_resp, ya_conf = _confirmar_propuesta_aporte_meta(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(mov.id) if mov else None

            elif intent_ganador == "transferir_fondos":
                tr, msg_resp, ya_conf = _confirmar_propuesta_transferencia(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(tr.id) if tr else None

            elif intent_ganador == "dar_baja_suscripcion":
                sub, msg_resp, ya_conf = _confirmar_propuesta_baja_suscripcion(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(sub.id) if sub else None

            elif intent_ganador == "cambiar_precio_suscripcion":
                hist, msg_resp, ya_conf = _confirmar_propuesta_cambio_precio(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(hist.id) if hist else None

            elif intent_ganador == "agregar_suscripcion":
                sub, msg_resp, ya_conf = _confirmar_propuesta_suscripcion(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(sub.id) if sub else None

            else:
                tx, msg_resp, ya_conf = _confirmar_propuesta_transaccion(
                    usuario, db, entidades_actuales=resultado_ia.get("entidades")
                )
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(tx.id) if tx else None

        elif intent == "cancelar":
            txs_pendientes = db.execute(
                select(Transaccion)
                .where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.origen == OrigenTransaccion.IA_WPP,
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE,
                )
            ).scalars().all()

            for tx in txs_pendientes:
                db.delete(tx)
            if txs_pendientes:
                emitir_evento_actualizacion(db, usuario.id, "transacciones")

            # Desactivar y marcar cancelado cualquier slot filling activo del usuario
            convs_activas = db.execute(
                select(ConversacionWpp)
                .where(
                    ConversacionWpp.usuario_id == usuario.id,
                    ConversacionWpp.slot_filling_activo == True,
                )
            ).scalars().all()
            for c in convs_activas:
                c.slot_filling_activo = False
                c.accion_ejecutada = "cancelada"

            # Marcar canceladas de forma definitiva todas las propuestas previas no ejecutadas
            propuestas_previas = db.execute(
                select(ConversacionWpp)
                .where(
                    ConversacionWpp.usuario_id == usuario.id,
                    ConversacionWpp.intent_detectado == "registrar_transaccion",
                    ConversacionWpp.accion_ejecutada.is_(None),
                )
            ).scalars().all()
            for p in propuestas_previas:
                p.accion_ejecutada = "cancelada"

            db.flush()
            return None

        return None

    except Exception as e:
        logger.error(f"Error al ejecutar intent {resultado_ia.get('intent')} para usuario {usuario.id}: {str(e)}")
        return None
