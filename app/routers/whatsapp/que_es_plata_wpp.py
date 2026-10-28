# -*- coding: utf-8 -*-
"""
Módulo para el manejo de respuestas numeradas en WhatsApp.
Procesa selecciones para:
1) "¿Qué es esta plata?" (1: Me pagaron, 2: Devolución parte de gasto, 3: Pasó por mi cuenta).
2) Selección de gasto para vincular cobro ("que_es_esta_plata_gasto").
3) Selección de compra para vincular reintegro ("reintegro_elegir_compra").
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transaccion import Transaccion
from app.models.usuario import Usuario
from app.routers.whatsapp.db_lookups import PLAZO_EXPIRACION_ESTADO_MINUTOS
from app.routers.whatsapp.parsers import _formatear_fecha_natural
from app.routers.whatsapp.reintegro_wpp import nombre_compra, registrar_reintegro_wpp
from app.services import que_es_plata_service, whatsapp_service
from app.utils.formato import formatear_monto


def manejar_respuesta_numerada(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Handler determinístico para procesar respuestas numéricas a flujos activos pendientes.
    """
    m = re.fullmatch(r"\s*(\d{1,2})\s*[).]?\s*", mensaje_texto)
    if not m:
        return False
    n = int(m.group(1))

    # Estado: ConversacionWpp más reciente del usuario sin contar las de este wamid
    stmt = (
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            or_(ConversacionWpp.wamid.is_(None), ConversacionWpp.wamid != wamid) if wamid else True,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    )
    estado = db.execute(stmt).scalars().first()
    if not estado:
        return False

    if estado.intent_detectado not in ("que_es_esta_plata", "que_es_esta_plata_gasto", "reintegro_elegir_compra"):
        return False

    if estado.accion_ejecutada is not None:
        return False

    fecha_estado = estado.fecha.replace(tzinfo=timezone.utc) if estado.fecha.tzinfo is None else estado.fecha
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    if fecha_estado < limite:
        return False

    # 1. Flujo "que_es_esta_plata"
    if estado.intent_detectado == "que_es_esta_plata":
        tx_id_str = (estado.entidades or {}).get("transaccion_id")
        if not tx_id_str:
            return False
        tx_id = UUID(str(tx_id_str))
        tx = db.get(Transaccion, tx_id)
        if not tx:
            return False

        if n == 1:
            try:
                que_es_plata_service.responder(db, usuario.id, tx.id, "me_pagaron", commit=False)
                msg = "Listo, queda como cobro."
                estado.accion_ejecutada = "respondida"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg,
                    intent_detectado="que_es_esta_plata",
                    entidades={"transaccion_id": str(tx.id)},
                    accion_ejecutada=f"que_es_esta_plata:{tx.id}",
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg)
                return True
            except HTTPException as e:
                db.rollback()
                msg_err = str(e.detail)
                estado.accion_ejecutada = "respondida"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg_err,
                    intent_detectado="que_es_esta_plata",
                    entidades={"transaccion_id": str(tx.id)},
                    accion_ejecutada="error",
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_err)
                return True

        elif n == 3:
            try:
                que_es_plata_service.responder(db, usuario.id, tx.id, "paso_por_mi_cuenta", commit=False)
                msg = "Listo, queda como plata que solo pasó por tu cuenta: no cuenta como ingreso."
                estado.accion_ejecutada = "respondida"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg,
                    intent_detectado="que_es_esta_plata",
                    entidades={"transaccion_id": str(tx.id)},
                    accion_ejecutada=f"que_es_esta_plata:{tx.id}",
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg)
                return True
            except HTTPException as e:
                db.rollback()
                msg_err = str(e.detail)
                estado.accion_ejecutada = "respondida"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg_err,
                    intent_detectado="que_es_esta_plata",
                    entidades={"transaccion_id": str(tx.id)},
                    accion_ejecutada="error",
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_err)
                return True

        elif n == 2:
            gastos = que_es_plata_service.gastos_para_vincular(db, usuario.id, tx.moneda, tx.fecha, tx.monto, limite=5)
            if not gastos:
                msg = "No encontré un gasto para vincularla. Podés hacerlo desde la web de Argentum."
                estado.accion_ejecutada = "respondida"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg,
                    intent_detectado="que_es_esta_plata",
                    entidades={"transaccion_id": str(tx.id)},
                    accion_ejecutada="sin_efecto",
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg)
                return True
            else:
                lineas = ["¿De qué gasto?"]
                gasto_ids = []
                for idx, g in enumerate(gastos, start=1):
                    g_obj = db.get(Transaccion, g["id"])
                    nom = nombre_compra(g_obj) if g_obj else (g.get("descripcion") or "tu gasto")
                    f_nat = _formatear_fecha_natural(g["fecha"])
                    f_str = f" ({f_nat})" if f_nat else ""
                    m_str = formatear_monto(g["monto"], tx.moneda)
                    lineas.append(f"{idx}) {nom} {m_str}{f_str}")
                    gasto_ids.append(str(g["id"]))
                lineas.append("Respondé con el número.")
                msg = "\n".join(lineas)

                estado.accion_ejecutada = "respondida"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg,
                    intent_detectado="que_es_esta_plata_gasto",
                    entidades={"transaccion_id": str(tx.id), "gastos": gasto_ids},
                    accion_ejecutada=None,
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg)
                return True
        else:
            return False

    # 2. Flujo "que_es_esta_plata_gasto"
    elif estado.intent_detectado == "que_es_esta_plata_gasto":
        tx_id_str = (estado.entidades or {}).get("transaccion_id")
        gastos = (estado.entidades or {}).get("gastos", [])
        if not tx_id_str:
            return False
        tx_id = UUID(str(tx_id_str))
        tx = db.get(Transaccion, tx_id)
        if not tx:
            return False

        if 1 <= n <= len(gastos):
            gasto_id = UUID(str(gastos[n - 1]))
            gasto_obj = db.get(Transaccion, gasto_id)
            nom_gasto = nombre_compra(gasto_obj) if gasto_obj else "tu gasto"
            monto_cobro = abs(Decimal(str(tx.monto)))
            monto_fmt = formatear_monto(monto_cobro, tx.moneda)
            msg = f"Listo, los {monto_fmt} se restan de «{nom_gasto}»."

            try:
                que_es_plata_service.responder(db, usuario.id, tx.id, "parte_de_gasto", gasto_id=gasto_id, commit=False)
                estado.accion_ejecutada = "respondida"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg,
                    intent_detectado="que_es_esta_plata",
                    entidades={"transaccion_id": str(tx.id), "gasto_id": str(gasto_id)},
                    accion_ejecutada=f"que_es_esta_plata:{tx.id}",
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg)
                return True
            except HTTPException as e:
                db.rollback()
                msg_err = str(e.detail)
                estado.accion_ejecutada = "respondida"
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=msg_err,
                    intent_detectado="que_es_esta_plata_gasto",
                    entidades=dict(estado.entidades or {}),
                    accion_ejecutada="error",
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, msg_err)
                return True
        else:
            return False

    # 3. Flujo "reintegro_elegir_compra"
    elif estado.intent_detectado == "reintegro_elegir_compra":
        compras = (estado.entidades or {}).get("compras", [])
        monto_ent = (estado.entidades or {}).get("monto")
        monto_dec = Decimal(str(monto_ent)) if monto_ent is not None else None

        if 1 <= n <= len(compras):
            compra_id = UUID(str(compras[n - 1]))
            compra_obj = db.get(Transaccion, compra_id)
            if not compra_obj:
                return False

            estado.accion_ejecutada = "respondida"
            msg = registrar_reintegro_wpp(db, usuario, compra_obj, monto_dec, mensaje_texto, wamid=wamid)
            whatsapp_service.enviar_whatsapp(from_number, msg)
            return True
        else:
            return False

    return False

