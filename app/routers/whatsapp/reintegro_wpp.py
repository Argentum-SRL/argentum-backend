# -*- coding: utf-8 -*-
"""
Módulo para la detección, búsqueda y registro de reintegros y devoluciones por WhatsApp.
Permite vincular devoluciones directas a compras recientes en pesos, manejar desambiguación
con listas numeradas y registrar el espejo correspondiente preservando la trazabilidad.
"""
from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.parsers import (
    _formatear_fecha_natural,
    _parsear_monto_argentino,
)
from app.services import reintegro_service, whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion
from app.services.que_es_plata_service import gastos_para_vincular
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto

PREFIJOS_REINTEGRO = [
    "me llego el reintegro",
    "me llego la devolucion",
    "me entro el reintegro",
    "me entro la devolucion",
    "llego el reintegro",
    "llego la devolucion",
    "me reintegraron",
    "me devolvieron",
]

CONECTORES_REINTEGRO = [
    "de la ",
    "de las ",
    "de los ",
    "del ",
    "de ",
    "por la ",
    "por el ",
    "por ",
    "en la ",
    "en el ",
    "en ",
]


def detectar_pedido_reintegro(texto: str) -> tuple[Decimal | None, str] | None:
    """
    Detecta si el mensaje es un pedido de registro de reintegro o devolución.
    Retorna (monto, busqueda) o None si no coincide.
    """
    norm = normalizar_texto(texto)
    norm = norm.rstrip(".!?¡¿ ").lstrip("¡¿ ")
    if norm.startswith("ya "):
        norm = norm[3:].strip()

    resto = None
    for prefijo in PREFIJOS_REINTEGRO:
        if norm.startswith(prefijo + " "):
            resto = norm[len(prefijo) + 1:].strip()
            break

    if resto is None:
        return None

    # Extracción opcional del monto
    monto = None
    m = re.match(r"^(?:de )?(\$?\s?\d[\d.,]*\s?(?:k|mil|lucas?)?)\s+(.*)$", resto)
    if m:
        monto_parsed = _parsear_monto_argentino(m.group(1))
        if monto_parsed is None or monto_parsed <= 0 or monto_parsed > MAX_MONTO_INTEGRIDAD:
            return None
        monto = monto_parsed
        resto = m.group(2).strip()

    # Búsqueda de conector
    busqueda = None
    for conector in CONECTORES_REINTEGRO:
        if resto.startswith(conector):
            busqueda = resto[len(conector):].strip()
            break

    if busqueda is None:
        return None

    # Validaciones sobre el término de búsqueda
    if len(busqueda) < 2 or len(busqueda) > 40:
        return None
    if any(ch.isdigit() for ch in busqueda):
        return None
    if not any(ch.isalpha() for ch in busqueda):
        return None

    return monto, busqueda


def buscar_compras_para_reintegro(db: Session, usuario_id: UUID, busqueda: str) -> list[Transaccion]:
    """
    Busca compras candidatas para asociar un reintegro según el término de búsqueda.
    """
    candidatas = gastos_para_vincular(db, usuario_id, Moneda.ARS, hoy_argentina(), Decimal("0.01"), limite=50)
    norm_busq = normalizar_texto(busqueda)
    coinciden_ids = []
    candidatas_map = {}
    for c in candidatas:
        candidatas_map[c["id"]] = c
        desc_norm = normalizar_texto(c.get("descripcion") or "")
        subcat_norm = normalizar_texto(c.get("subcategoria_nombre") or "")
        if (norm_busq in desc_norm) or (norm_busq in subcat_norm) or (len(desc_norm) >= 3 and desc_norm in norm_busq):
            coinciden_ids.append(c["id"])

    if not coinciden_ids:
        return []

    stmt = (
        select(Transaccion)
        .options(joinedload(Transaccion.subcategoria), joinedload(Transaccion.espejos_reintegro))
        .where(Transaccion.id.in_(coinciden_ids))
    )
    txs = db.execute(stmt).scalars().unique().all()
    txs_map = {tx.id: tx for tx in txs}

    resultado = []
    for cid in coinciden_ids:
        if cid in txs_map:
            tx_obj = txs_map[cid]
            setattr(tx_obj, "_disponible", candidatas_map[cid].get("disponible", tx_obj.monto))
            resultado.append(tx_obj)
    return resultado


def nombre_compra(compra: Transaccion) -> str:
    """
    Obtiene el nombre representativo de una compra (descripción, subcategoría o 'tu gasto').
    """
    if compra.descripcion and compra.descripcion.strip():
        return compra.descripcion.strip()
    if compra.subcategoria and compra.subcategoria.nombre and compra.subcategoria.nombre.strip():
        return compra.subcategoria.nombre.strip()
    return "tu gasto"


def tiene_reintegro_recibido(db: Session, compra_id: UUID) -> bool:
    """
    Verifica si una compra ya tiene algún espejo de reintegro recibido activo (no cuota hija).
    """
    espejo = db.execute(
        select(Transaccion).where(
            Transaccion.reintegro_de_id == compra_id,
            or_(Transaccion.tipo_espejo == "reintegro", Transaccion.tipo_espejo.is_(None)),
            Transaccion.es_cuota_hija.is_(False),
        )
    ).first()
    return espejo is not None


def manejar_reintegro_wpp(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Handler determinístico para procesar mensajes de reintegro o devolución de gastos por WhatsApp.
    """
    pedido = detectar_pedido_reintegro(mensaje_texto)
    if pedido is None:
        return False

    monto, busqueda = pedido
    coinciden = buscar_compras_para_reintegro(db, usuario.id, busqueda)
    if not coinciden:
        return False

    con_recibido = [c for c in coinciden if tiene_reintegro_recibido(db, c.id)]
    libres = [c for c in coinciden if c not in con_recibido]

    if not libres:
        msg = "Esa compra ya tiene su reintegro cargado. Para cambiarlo, entrá a la web de Argentum."
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg,
            intent_detectado="registrar_reintegro",
            entidades={},
            accion_ejecutada="sin_efecto",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg)
        return True

    if monto is not None:
        aptas = [c for c in libres if getattr(c, "_disponible", c.monto) >= monto]
        if not aptas:
            msg = "El reintegro no puede ser mayor que la compra."
            nueva_conv = ConversacionWpp(
                usuario_id=usuario.id,
                wamid=wamid,
                mensaje_usuario=mensaje_texto,
                tipo_mensaje=TipoMensajeWpp.TEXTO,
                transcripcion=None,
                mensaje_bot=msg,
                intent_detectado="registrar_reintegro",
                entidades={},
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
        aptas = [c for c in libres if c.reintegro_esperado is not None and c.reintegro_esperado > 0]
        if not aptas:
            msg = "¿Cuánto te devolvieron? Decímelo con el monto, por ejemplo «me devolvieron 25000 del súper»."
            nueva_conv = ConversacionWpp(
                usuario_id=usuario.id,
                wamid=wamid,
                mensaje_usuario=mensaje_texto,
                tipo_mensaje=TipoMensajeWpp.TEXTO,
                transcripcion=None,
                mensaje_bot=msg,
                intent_detectado="registrar_reintegro",
                entidades={},
                accion_ejecutada="sin_efecto",
                confianza=Decimal("1.000"),
                slot_filling_activo=False,
                slot_filling_estado=None,
            )
            db.add(nueva_conv)
            db.commit()
            whatsapp_service.enviar_whatsapp(from_number, msg)
            return True

    if len(aptas) == 1:
        compra = aptas[0]
        msg = registrar_reintegro_wpp(db, usuario, compra, monto, mensaje_texto, wamid=wamid)
        whatsapp_service.enviar_whatsapp(from_number, msg)
        return True

    # Varias compras aptas: preguntar con menú numerado (hasta 5)
    lineas = ["¿De cuál compra?"]
    compras_5 = aptas[:5]
    compras_ids = []
    for idx, c in enumerate(compras_5, start=1):
        nom = nombre_compra(c)
        m_fmt = formatear_monto(c.monto, Moneda.ARS)
        f_nat = _formatear_fecha_natural(c.fecha)
        f_str = f" ({f_nat})" if f_nat else ""
        lineas.append(f"{idx}) {nom} {m_fmt}{f_str}")
        compras_ids.append(str(c.id))
    lineas.append("Respondé con el número.")
    msg = "\n".join(lineas)

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg,
        intent_detectado="reintegro_elegir_compra",
        entidades={"compras": compras_ids, "monto": float(monto) if monto is not None else None},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg)
    return True


def registrar_reintegro_wpp(
    db: Session,
    usuario: Usuario,
    compra: Transaccion,
    monto: Decimal | None,
    mensaje_texto: str,
    wamid: str | None = None,
) -> str:
    """
    Registra directamente el reintegro de una compra específica en WhatsApp y retorna el texto de confirmación.
    """
    previo = float(compra.reintegro_esperado) if compra.reintegro_esperado is not None else None
    monto_final = monto if monto is not None else compra.reintegro_esperado
    f_nat = _formatear_fecha_natural(compra.fecha)
    f_str = f" ({f_nat})" if f_nat else ""
    nombre = nombre_compra(compra)
    monto_fmt = formatear_monto(monto_final, Moneda.ARS)
    texto = f"Listo, anoté la devolución de {monto_fmt} de «{nombre}»{f_str}. Si no era esa, decime «borrá eso»."

    try:
        reintegro_service.registrar_o_actualizar_reintegro(
            db,
            usuario.id,
            compra.id,
            monto_final,
            recibido=True,
            fecha_recibido=hoy_argentina(),
            commit=False,
        )
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=texto,
            intent_detectado="registrar_reintegro",
            entidades={
                "compra_id": str(compra.id),
                "monto": float(monto_final),
                "reintegro_esperado_previo": previo,
            },
            accion_ejecutada=f"reintegro:{compra.id}",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.commit()
        return texto
    except HTTPException as e:
        db.rollback()
        msg_err = str(e.detail)
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_err,
            intent_detectado="registrar_reintegro",
            entidades={
                "compra_id": str(compra.id),
                "monto": float(monto_final) if monto_final is not None else None,
                "reintegro_esperado_previo": previo,
            },
            accion_ejecutada="error",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        return msg_err

