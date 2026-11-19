"""
app/routers/whatsapp/metas.py — Gestión y confirmación de aportes a metas por WhatsApp.
"""
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import structlog
from app.routers.whatsapp.constantes import logger
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.db_lookups import (
    PLAZO_EXPIRACION_ESTADO_MINUTOS,
    _buscar_meta_activa_por_nombre,
    _buscar_propuesta_confirmable_mas_reciente,
    _buscar_propuesta_pendiente,
    _obtener_billeteras_activas,
)
from app.routers.whatsapp.detectors import _es_cancelacion, _es_confirmacion
from app.routers.whatsapp.parsers import _parsear_monto_argentino
from app.routers.whatsapp.resolvers_cascada import (
    _generar_menu_billeteras,
    resolver_billetera_cascada,
)
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.services import meta_service, whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion
from app.core.jerga import sufijo_multiplicador_rx
from app.core.reglas_texto_wpp import ejemplo_wpp
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto



def _confirmar_propuesta_aporte_meta(
    usuario: Usuario,
    db: Session,
    propuesta_id: UUID | None = None,
) -> tuple[MovimientoMeta | None, str, bool]:
    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)

    stmt_conv = (
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "aportar_meta",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite_tiempo,
        )
    )
    if propuesta_id:
        stmt_conv = stmt_conv.where(ConversacionWpp.id == propuesta_id)

    conv_previa = db.execute(
        stmt_conv.order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc()).with_for_update()
    ).scalars().first()

    if not conv_previa:
        limite_reciente = datetime.now(timezone.utc) - timedelta(minutes=10)
        candidatas = db.execute(
            select(ConversacionWpp)
            .where(
                ConversacionWpp.usuario_id == usuario.id,
                ConversacionWpp.intent_detectado == "aportar_meta",
                ConversacionWpp.accion_ejecutada.is_not(None),
                ConversacionWpp.fecha >= limite_reciente,
            )
            .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().all()

        for c in candidatas:
            if c.accion_ejecutada and str(c.accion_ejecutada).startswith("aporte_meta:"):
                return None, "Ya estaba anotado.", True
        return None, "No tengo nada pendiente para confirmar.", False

    entidades = conv_previa.entidades or {}
    meta_id_str = entidades.get("meta_id")
    billetera_id_str = entidades.get("billetera_id")
    monto_val = entidades.get("monto")
    moneda_str = entidades.get("moneda", "ARS")

    if not meta_id_str or not billetera_id_str or not monto_val:
        return None, "No pude anotar el aporte a la meta.", False

    meta_id = UUID(str(meta_id_str))
    billetera_id = UUID(str(billetera_id_str))
    monto = Decimal(str(monto_val))
    moneda_mov = Moneda.USD if moneda_str == "USD" else Moneda.ARS

    meta = db.get(Meta, meta_id)
    if not meta or meta.usuario_id != usuario.id:
        return None, "No encontré la meta seleccionada.", False

    billetera = db.get(Billetera, billetera_id)
    if not billetera or billetera.usuario_id != usuario.id:
        return None, "No encontré esa cuenta.", False

    data_mov = MovimientoMetaCreate(
        tipo=TipoMovimientoMeta.APORTE,
        monto=monto,
        moneda_movimiento=moneda_mov,
        billetera_id=billetera_id,
        fecha=hoy_argentina(),
    )

    try:
        nuevo_mov = meta_service.registrar_movimiento(db, usuario.id, meta_id, data_mov)
        conv_previa.accion_ejecutada = f"aporte_meta:{nuevo_mov.id}"
        emitir_evento_actualizacion(db, usuario.id, "metas")
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.commit()
    except HTTPException as exc:
        db.rollback()
        return None, str(exc.detail), False
    except Exception as exc:
        db.rollback()
        logger.error(f"Error al confirmar aporte a meta: {exc}")
        return None, "Hubo un problema y no anoté nada. Probá de nuevo en un rato.", False

    monto_fmt = formatear_monto(float(monto), moneda_mov, con_decimales=False)
    if meta.monto_actual >= meta.monto_objetivo:
        msg_resp = f"Listo, aporté {monto_fmt} a {meta.nombre}. ¡Completaste tu meta!"
    else:
        msg_resp = f"Listo, aporté {monto_fmt} a tu meta {meta.nombre}."

    return nuevo_mov, msg_resp, False


def manejar_confirmacion_aporte_meta(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    if not _es_confirmacion(mensaje_texto):
        return False

    propuesta = _buscar_propuesta_confirmable_mas_reciente(usuario.id, db)
    if not propuesta or propuesta.intent_detectado != "aportar_meta":
        return False

    mov_creado, msg_confirm, ya_conf = _confirmar_propuesta_aporte_meta(usuario, db)
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_confirm,
        intent_detectado="confirmar_aporte_meta",
        entidades={},
        accion_ejecutada=f"aporte_meta:{mov_creado.id}" if mov_creado else ("ya_confirmado" if ya_conf else None),
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_confirm)
    return True


def manejar_cancelacion_aporte_meta(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    if not _es_cancelacion(mensaje_texto):
        return False

    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    propuesta = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "aportar_meta",
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite_tiempo,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

    if not propuesta:
        return False

    propuesta.accion_ejecutada = "cancelada"
    propuesta.slot_filling_activo = False

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


def manejar_aporte_meta(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
    conv_activa: ConversacionWpp | None = None,
    estado_previo: dict | None = None,
) -> bool:
    """
    Detección determinística o procesamiento de aporte a meta de ahorro.
    Siempre pide confirmación antes de ejecutar.
    """
    billeteras_usuario = _obtener_billeteras_activas(usuario.id, db)

    # 1. Si hay un slot-filling activo de meta esperando elegir cuál (ambigüedad)
    if estado_previo and estado_previo.get("intent_origen") == "aportar_meta":
        if "meta_ambigua_cands" in estado_previo:
            cands_ids = estado_previo.get("meta_ambigua_cands", [])
            metas_cands = db.query(Meta).filter(Meta.id.in_([UUID(cid) for cid in cands_ids])).all()
            meta_elegida = None
            if mensaje_texto.strip().isdigit():
                idx = int(mensaje_texto.strip()) - 1
                if 0 <= idx < len(metas_cands):
                    meta_elegida = metas_cands[idx]
            else:
                m_txt_norm = normalizar_texto(mensaje_texto)
                for mc in metas_cands:
                    mc_norm = normalizar_texto(mc.nombre)
                    if m_txt_norm in mc_norm or mc_norm in m_txt_norm:
                        meta_elegida = mc
                        break

            if meta_elegida:
                monto = Decimal(str(estado_previo["monto"]))
                bill_id_str = estado_previo.get("billetera_id")
                billetera = db.get(Billetera, UUID(bill_id_str)) if bill_id_str else None
                if not billetera:
                    bills_mon = [b for b in billeteras_usuario if b.moneda == meta_elegida.moneda]
                    billetera = next((b for b in bills_mon if b.es_principal), None) or (bills_mon[0] if len(bills_mon) == 1 else None)

                if conv_activa:
                    conv_activa.slot_filling_activo = False
                    db.flush()

                if not billetera:
                    bills_mon = [b for b in billeteras_usuario if b.moneda == meta_elegida.moneda]
                    enc = f"¿De qué cuenta sale el aporte a {meta_elegida.nombre}?"
                    pregunta = _generar_menu_billeteras(bills_mon, tipo="egreso", encabezado=enc)
                    entidades_sf = {
                        "intent_origen": "aportar_meta",
                        "meta_id": str(meta_elegida.id),
                        "meta_nombre": meta_elegida.nombre,
                        "monto": float(monto),
                        "moneda": meta_elegida.moneda.value,
                    }
                    nueva_conv = ConversacionWpp(
                        usuario_id=usuario.id,
                        wamid=wamid,
                        mensaje_usuario=mensaje_texto,
                        tipo_mensaje=TipoMensajeWpp.TEXTO,
                        transcripcion=None,
                        mensaje_bot=pregunta,
                        intent_detectado="aportar_meta",
                        entidades=entidades_sf,
                        accion_ejecutada=None,
                        confianza=Decimal("1.000"),
                        slot_filling_activo=True,
                        slot_filling_estado=entidades_sf,
                    )
                    db.add(nueva_conv)
                    db.commit()
                    whatsapp_service.enviar_whatsapp(from_number, pregunta)
                    return True

                monto_fmt = formatear_monto(float(monto), meta_elegida.moneda, con_decimales=False)
                prop = (
                    f"¿Aporto {monto_fmt} a tu meta {meta_elegida.nombre} "
                    f"desde {billetera.nombre}?"
                )
                entidades_prop = {
                    "meta_id": str(meta_elegida.id),
                    "meta_nombre": meta_elegida.nombre,
                    "billetera_id": str(billetera.id),
                    "billetera_nombre": billetera.nombre,
                    "monto": float(monto),
                    "moneda": meta_elegida.moneda.value,
                }
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=prop,
                    intent_detectado="aportar_meta",
                    entidades=entidades_prop,
                    accion_ejecutada=None,
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                whatsapp_service.enviar_whatsapp(from_number, prop)
                return True

    # Parsear mensaje para aporte a meta
    m_norm = normalizar_texto(mensaje_texto)
    signals_meta = ("meta", "metas", "aporte", "aportar", "aporta")
    tiene_senal_meta = any(s in m_norm for s in signals_meta)
    verbo_ahorro_rx = (
        r"\b(?:aporte|aportar|aporta|puse|poner|guarde|guardar|"
        r"separe|separar|mande|mandar|meti|meter|deposite|depositar|"
        r"transferi|transferir)\b"
    )
    tiene_verbo_ahorro = bool(re.search(verbo_ahorro_rx, m_norm))
    if not (tiene_senal_meta or tiene_verbo_ahorro):
        return False

    # Extraer monto usando sufijo_multiplicador_rx() de jerga.py
    suf_rx = sufijo_multiplicador_rx()
    m_monto = re.search(
        rf"(\$?\s*\d+(?:[.,]\d+)?(?:\s*{suf_rx})?)\b",
        m_norm,
    )
    if not m_monto:
        return False
    monto_val = _parsear_monto_argentino(m_monto.group(1))
    if not monto_val or monto_val <= Decimal("0"):
        return False

    # Extraer billetera mencionada
    billetera_encontrada = None
    for b in billeteras_usuario:
        b_low = normalizar_texto(b.nombre)
        if re.search(rf"\b(?:desde|con|en|de)\s+{re.escape(b_low)}\b", m_norm):
            billetera_encontrada = b
            break
        elif (
            re.search(rf"\b{re.escape(b_low)}\b", m_norm)
            and b_low not in ("pesos", "dolares", "dólares", "efectivo")
        ):
            if any(p in m_norm for p in [f"con {b_low}", f"desde {b_low}", f"por {b_low}"]):
                billetera_encontrada = b
                break

    # Extraer nombre de meta
    clean_text = mensaje_texto
    if billetera_encontrada:
        clean_text = re.sub(
            rf"\b(?:desde|con|en|de)\s+{re.escape(billetera_encontrada.nombre)}\b",
            "",
            clean_text,
            flags=re.IGNORECASE,
        )
        clean_text = re.sub(
            rf"\b{re.escape(billetera_encontrada.nombre)}\b",
            "",
            clean_text,
            flags=re.IGNORECASE,
        )

    m_meta = re.search(
        r"\b(?:a|para|en)\s+(?:la\s+|mi\s+)?meta\s+"
        r"(?:de\s+|del\s+|para\s+)?([a-zA-Z0-9_áéíóúÁÉÍÓÚñÑ\s]+)",
        clean_text,
        re.IGNORECASE,
    )
    meta_name = None
    if m_meta:
        meta_name = m_meta.group(1).strip()
    else:
        clean_norm = normalizar_texto(clean_text)
        m_meta2 = re.search(
            rf"{verbo_ahorro_rx}\s+.*?\b(?:en|a|para|al)\s+"
            r"([a-z0-9_\s]+)",
            clean_norm,
            re.IGNORECASE,
        )
        if m_meta2:
            cand = m_meta2.group(1).strip()
            cand = re.sub(
                r"^(?:el|la|los|las|mi|tu|un|una)\s+",
                "",
                cand,
                flags=re.IGNORECASE,
            ).strip()
            meta_name = cand

    if meta_name:
        meta_name = re.sub(r"[.,;:!?]+$", "", meta_name).strip()
        meta_name = re.sub(
            rf"\$?\s*\d+(?:[.,]\d+)?(?:\s*{suf_rx})?",
            "",
            meta_name,
            flags=re.IGNORECASE,
        ).strip()

    if not tiene_senal_meta and not meta_name:
        return False

    # Resolver meta
    meta, estado_meta, cands = _buscar_meta_activa_por_nombre(usuario.id, meta_name, db)
    if not tiene_senal_meta and estado_meta not in ("ok", "ambigua"):
        return False

    if estado_meta == "no_metas":
        msg_resp = "No tenés metas activas."
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="aportar_meta",
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

    if estado_meta == "no_encontrada":
        nombre_disp = meta_name or "esa"
        ex_m = ejemplo_wpp("mis metas")
        msg_resp = f"No encontré la meta {nombre_disp}. Preguntame {ex_m} para verlas."
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="aportar_meta",
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

    if estado_meta == "ambigua":
        lineas_amb = ["¿A cuál meta?"] + [f"{i}. {m.nombre}" for i, m in enumerate(cands, 1)]
        msg_resp = "\n".join(lineas_amb)
        entidades_sf = {
            "intent_origen": "aportar_meta",
            "meta_ambigua_cands": [str(m.id) for m in cands],
            "monto": float(monto_val),
            "billetera_id": str(billetera_encontrada.id) if billetera_encontrada else None,
        }
        if conv_activa:
            conv_activa.slot_filling_activo = False
            db.flush()
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="aportar_meta",
            entidades=entidades_sf,
            accion_ejecutada=None,
            confianza=Decimal("1.000"),
            slot_filling_activo=True,
            slot_filling_estado=entidades_sf,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, msg_resp)
        return True

    # Resolver billetera de origen en la moneda de la meta
    bills_mon = [b for b in billeteras_usuario if b.moneda == meta.moneda]
    billetera = billetera_encontrada
    if not billetera:
        billetera = next((b for b in bills_mon if b.es_principal), None) or (bills_mon[0] if len(bills_mon) == 1 else None)

    if not billetera:
        enc = f"¿De qué cuenta sale el aporte a {meta.nombre}?"
        pregunta = _generar_menu_billeteras(bills_mon, tipo="egreso", encabezado=enc)
        entidades_sf = {
            "intent_origen": "aportar_meta",
            "meta_id": str(meta.id),
            "meta_nombre": meta.nombre,
            "monto": float(monto_val),
            "moneda": meta.moneda.value,
        }
        if conv_activa:
            conv_activa.slot_filling_activo = False
            db.flush()
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=pregunta,
            intent_detectado="aportar_meta",
            entidades=entidades_sf,
            accion_ejecutada=None,
            confianza=Decimal("1.000"),
            slot_filling_activo=True,
            slot_filling_estado=entidades_sf,
        )
        db.add(nueva_conv)
        db.commit()
        whatsapp_service.enviar_whatsapp(from_number, pregunta)
        return True

    # Validar saldo suficiente
    if billetera.saldo_actual < monto_val:
        saldo_val = float(billetera.saldo_actual)
        monto_disp = formatear_monto(saldo_val, billetera.moneda, con_decimales=False)
        msg_resp = (
            f"No te alcanza: {billetera.nombre} tiene {monto_disp}. "
            "Elegí otra cuenta o un monto menor."
        )
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="aportar_meta",
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

    # Armar propuesta de confirmación
    monto_fmt = formatear_monto(float(monto_val), meta.moneda, con_decimales=False)
    prop = f"¿Aporto {monto_fmt} a tu meta {meta.nombre} desde {billetera.nombre}?"
    entidades_prop = {
        "meta_id": str(meta.id),
        "meta_nombre": meta.nombre,
        "billetera_id": str(billetera.id),
        "billetera_nombre": billetera.nombre,
        "monto": float(monto_val),
        "moneda": meta.moneda.value,
    }

    if conv_activa:
        conv_activa.slot_filling_activo = False
        db.flush()

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=prop,
        intent_detectado="aportar_meta",
        entidades=entidades_prop,
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, prop)
    return True
