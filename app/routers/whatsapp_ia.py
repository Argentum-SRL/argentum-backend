"""
app/routers/whatsapp_ia.py — Webhook de WhatsApp para IA conversacional de Argentum.
Recibe mensajes de Twilio, los procesa con ai_service y responde en TwiML.
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from app.core.auth import get_current_user, get_db
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Usuario, Moneda
from app.services import ai_service
from app.services.whatsapp_service import enviar_whatsapp, formatear_numero_whatsapp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp-ia"])


def _buscar_usuario_por_telefono(telefono_raw: str, db: Session) -> Usuario | None:
    telefono_limpio = telefono_raw
    if telefono_raw.startswith("whatsapp:"):
        telefono_limpio = telefono_raw[9:]
    
    usuario = db.execute(
        select(Usuario).where(Usuario.telefono == telefono_limpio)
    ).scalar_one_or_none()
    return usuario


def _buscar_slot_filling_activo(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    conv = db.execute(
        select(ConversacionWpp)
        .where(ConversacionWpp.usuario_id == usuario_id, ConversacionWpp.slot_filling_activo == True)
        .order_by(ConversacionWpp.fecha.desc())
    ).scalars().first()
    return conv


def _resolver_billetera(nombre: str | None, usuario_id: UUID, db: Session) -> UUID | None:
    if not nombre:
        return None
    billetera = db.execute(
        select(Billetera)
        .where(
            Billetera.usuario_id == usuario_id,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.nombre.ilike(f"%{nombre}%")
        )
    ).scalars().first()
    return billetera.id if billetera else None


def _resolver_categoria(nombre: str | None, usuario_id: UUID, db: Session) -> UUID | None:
    if not nombre:
        return None
    categoria = db.execute(
        select(Categoria)
        .where(
            Categoria.estado == EstadoCategoria.ACTIVA,
            (Categoria.es_global == True) | (Categoria.creador_id == usuario_id),
            Categoria.nombre.ilike(f"%{nombre}%")
        )
    ).scalars().first()
    return categoria.id if categoria else None


def _ejecutar_intent(resultado_ia: dict, usuario: Usuario, db: Session) -> str | None:
    try:
        intent = resultado_ia.get("intent")
        confianza = resultado_ia.get("confianza", 0.0)
        slot_filling = resultado_ia.get("slot_filling", False)

        if intent == "registrar_transaccion" and confianza >= 0.85 and not slot_filling:
            entidades = resultado_ia.get("entidades", {})
            monto = entidades.get("monto")
            tipo = entidades.get("tipo")
            categoria = entidades.get("categoria")
            billetera_origen = entidades.get("billetera_origen")
            moneda = entidades.get("moneda")
            fecha_val = entidades.get("fecha")
            descripcion = entidades.get("descripcion")

            if monto is None:
                return None

            tipo_val = tipo if tipo is not None else "egreso"

            billetera_id = _resolver_billetera(billetera_origen, usuario.id, db)
            if not billetera_id:
                billetera_id = db.execute(
                    select(Billetera.id)
                    .where(Billetera.usuario_id == usuario.id, Billetera.estado == EstadoBilletera.ACTIVA, Billetera.es_principal == True)
                ).scalar_one_or_none()
            if not billetera_id:
                billetera_id = db.execute(
                    select(Billetera.id)
                    .where(Billetera.usuario_id == usuario.id, Billetera.estado == EstadoBilletera.ACTIVA)
                ).scalars().first()

            if not billetera_id:
                logger.error(f"No se encontró ninguna billetera activa para el usuario {usuario.id}")
                return None

            categoria_id = _resolver_categoria(categoria, usuario.id, db)

            moneda_val = Moneda.ARS
            if moneda == "USD":
                moneda_val = Moneda.USD

            fecha_obj = date.today()
            if fecha_val:
                try:
                    if isinstance(fecha_val, str):
                        fecha_obj = date.fromisoformat(fecha_val)
                    elif isinstance(fecha_val, (date, datetime.date)):
                        fecha_obj = fecha_val
                except Exception:
                    fecha_obj = date.today()

            transaccion = Transaccion(
                usuario_id=usuario.id,
                tipo=TipoTransaccion.INGRESO if tipo_val == "ingreso" else TipoTransaccion.EGRESO,
                monto=Decimal(str(monto)),
                moneda=moneda_val,
                fecha=fecha_obj,
                descripcion=descripcion or categoria or "Transacción por WhatsApp",
                billetera_id=billetera_id,
                categoria_id=categoria_id,
                origen=OrigenTransaccion.IA_WPP,
                estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
                es_recurrente=False,
                es_cuota_hija=False,
                es_padre_cuotas=False
            )
            db.add(transaccion)
            db.flush()
            return str(transaccion.id)

        elif intent == "confirmar":
            tx = db.execute(
                select(Transaccion)
                .where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.origen == OrigenTransaccion.IA_WPP,
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
                )
                .order_by(Transaccion.fecha_creacion.desc())
            ).scalars().first()

            if tx:
                tx.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
                db.flush()
            return None

        elif intent == "cancelar":
            tx = db.execute(
                select(Transaccion)
                .where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.origen == OrigenTransaccion.IA_WPP,
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
                )
                .order_by(Transaccion.fecha_creacion.desc())
            ).scalars().first()

            if tx:
                db.delete(tx)
                db.flush()
            return None

        return None
    except Exception as e:
        logger.error(f"Error al ejecutar el intent {resultado_ia.get('intent')} para el usuario {usuario.id}: {str(e)}")
        return None


@router.post("/webhook", response_class=PlainTextResponse)
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(default=""),
    From: str = Form(default=""),
    NumMedia: str = Form(default="0"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    try:
        usuario = _buscar_usuario_por_telefono(From, db)
        if not usuario:
            resp = MessagingResponse()
            resp.message("No encontramos tu cuenta. Registrate en argentum.app")
            return PlainTextResponse(content=str(resp), media_type="application/xml")

        if not Body.strip() and NumMedia == "0":
            resp = MessagingResponse()
            resp.message("No entendí tu mensaje. Mandame texto.")
            return PlainTextResponse(content=str(resp), media_type="application/xml")

        conv_activa = _buscar_slot_filling_activo(usuario.id, db)

        resultado_ia = ai_service.procesar_mensaje(
            mensaje=Body.strip(),
            usuario=usuario,
            db=db,
            slot_filling_estado=conv_activa.slot_filling_estado if conv_activa else None
        )

        transaccion_id = _ejecutar_intent(resultado_ia, usuario, db)

        if conv_activa and not resultado_ia.get("slot_filling", False):
            conv_activa.slot_filling_activo = False
            conv_activa.slot_filling_estado = None

        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            mensaje_usuario=Body.strip(),
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot=resultado_ia["respuesta_usuario"],
            intent_detectado=resultado_ia.get("intent"),
            entidades=resultado_ia.get("entidades"),
            accion_ejecutada=str(transaccion_id) if transaccion_id else None,
            confianza=Decimal(str(resultado_ia.get("confianza", 0))),
            slot_filling_activo=resultado_ia.get("slot_filling", False),
            slot_filling_estado=resultado_ia.get("entidades") if resultado_ia.get("slot_filling") else None,
        )
        db.add(nueva_conv)
        db.commit()

        resp = MessagingResponse()
        resp.message(resultado_ia["respuesta_usuario"])
        return PlainTextResponse(content=str(resp), media_type="application/xml")

    except Exception as e:
        db.rollback()
        logger.exception("Error procesando mensaje en webhook")
        resp = MessagingResponse()
        resp.message("Hubo un problema procesando tu mensaje. Intentá de nuevo.")
        return PlainTextResponse(content=str(resp), media_type="application/xml")


@router.post("/test")
def test_ia(
    mensaje: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> dict:
    return ai_service.procesar_mensaje(mensaje=mensaje, usuario=current_user, db=db)
