"""
app/routers/whatsapp/transferencias.py — Interpretación y confirmación de transferencias y operaciones cambiarias por WhatsApp.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.conversacion_wpp import ConversacionWpp
from app.models.transaccion import MetodoPago, TipoTransaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.constantes import (
    FACTOR_MAX_COTIZACION_DOLAR,
    FACTOR_MIN_COTIZACION_DOLAR,
    logger,
)
from app.routers.whatsapp.db_lookups import (
    PLAZO_EXPIRACION_ESTADO_MINUTOS,
    _obtener_billeteras_activas,
    _obtener_cotizacion_referencia_usuario,
)
from app.routers.whatsapp.detectors import _es_senial_suscripcion
from app.core.reglas_texto_wpp import fecha_corta_wpp
from app.routers.whatsapp.parsers import (
    _extraer_frecuencia_mencionada,
    _extraer_nombre_servicio,
    _parsear_monto_argentino,
    _resolver_y_validar_fecha,
)
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import transferencia_service, whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto



def _confirmar_propuesta_transferencia(
    usuario: Usuario,
    db: Session,
    propuesta_id: UUID | None = None,
) -> tuple[TransferenciaInterna | None, str, bool]:
    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)

    stmt_conv = (
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "transferir_fondos",
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
                ConversacionWpp.intent_detectado == "transferir_fondos",
                ConversacionWpp.accion_ejecutada.is_not(None),
                ConversacionWpp.fecha >= limite_reciente,
            )
            .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().all()

        for c in candidatas:
            if c.accion_ejecutada and str(c.accion_ejecutada).startswith("transferencia:"):
                return None, "Ya estaba anotado.", True
        return None, "No tengo nada pendiente para confirmar.", False

    entidades = conv_previa.entidades or {}
    b_origen_id_str = entidades.get("billetera_origen_id")
    b_destino_id_str = entidades.get("billetera_destino_id")
    if not b_origen_id_str or not b_destino_id_str:
        return None, "Hubo un problema y no anoté nada. Probá de nuevo en un rato.", False

    b_orig_id = UUID(str(b_origen_id_str))
    b_dest_id = UUID(str(b_destino_id_str))
    monto = Decimal(str(entidades["monto"]))
    monto_origen = Decimal(str(entidades.get("monto_origen", monto)))
    monto_destino = Decimal(str(entidades.get("monto_destino", monto)))
    moneda_origen = Moneda.USD if entidades.get("moneda_origen") == "USD" else Moneda.ARS
    moneda_destino = Moneda.USD if entidades.get("moneda_destino") == "USD" else Moneda.ARS
    monto_comision = Decimal(str(entidades["monto_comision"])) if entidades.get("monto_comision") else None

    fecha_iso = entidades.get("fecha")
    fecha_obj = date.fromisoformat(str(fecha_iso)) if fecha_iso else hoy_argentina()

    data_tr = TransferenciaInternaCreate(
        billetera_origen_id=b_orig_id,
        billetera_destino_id=b_dest_id,
        monto=monto_origen,
        moneda=moneda_origen,
        monto_origen=monto_origen,
        monto_destino=monto_destino,
        moneda_origen=moneda_origen,
        moneda_destino=moneda_destino,
        monto_comision=monto_comision,
        fecha=fecha_obj,
        notas=entidades.get("notas", "Transferencia por WhatsApp"),
    )

    try:
        tr = transferencia_service.crear_transferencia(db, usuario.id, data_tr, commit=False)
        conv_previa.accion_ejecutada = f"transferencia:{tr.id}"
        db.commit()
    except HTTPException as exc:
        db.rollback()
        return None, str(exc.detail), False
    except Exception as exc:
        db.rollback()
        logger.error(f"Error al confirmar transferencia: {exc}")
        return None, "Hubo un problema y no anoté nada. Probá de nuevo en un rato.", False

    b_origen = db.get(Billetera, b_orig_id)
    b_destino = db.get(Billetera, b_dest_id)
    nom_orig = b_origen.nombre if b_origen else "origen"
    nom_dest = b_destino.nombre if b_destino else "destino"

    tipo_op = entidades.get("tipo_operacion", "transferencia")
    if tipo_op == "compra_usd":
        m_usd = formatear_monto(float(monto_destino), Moneda.USD, con_decimales=False)
        m_ars = formatear_monto(float(monto_origen), Moneda.ARS, con_decimales=False)
        msg_resp = f"Listo. Compraste {m_usd} por {m_ars}."
    elif tipo_op == "venta_usd":
        m_usd = formatear_monto(float(monto_origen), Moneda.USD, con_decimales=False)
        m_ars = formatear_monto(float(monto_destino), Moneda.ARS, con_decimales=False)
        msg_resp = f"Listo. Vendiste {m_usd} por {m_ars}."
    else:
        monto_fmt = formatear_monto(float(monto_origen), moneda_origen, con_decimales=False)
        f_corta = fecha_corta_wpp(fecha_obj, hoy_argentina())
        fecha_sufijo = f" ({f_corta})" if f_corta else ""
        com_fmt = (
            formatear_monto(float(monto_comision), moneda_origen, con_decimales=False)
            if monto_comision
            else ""
        )
        comision_sufijo = f", con {com_fmt} de comisión" if monto_comision else ""
        msg_principal = (
            f"Listo. Pasaste {monto_fmt} de {nom_orig} a {nom_dest}"
            f"{fecha_sufijo}{comision_sufijo}."
        )

        lineas_extra = []
        if entidades.get("aviso_fecha"):
            lineas_extra.append(entidades["aviso_fecha"])

        if b_origen:
            db.refresh(b_origen)
            if b_origen.saldo_actual < Decimal("0"):
                lineas_extra.append(f"{b_origen.nombre} quedó en negativo.")

        from app.routers.whatsapp import aviso_saldo_wpp
        from app.services.saldo_billetera_service import movimiento_anterior_al_saldo
        for b_chk in (b_origen, b_destino):
            if b_chk and movimiento_anterior_al_saldo(b_chk, fecha_obj):
                txt_av = aviso_saldo_wpp.texto_aviso_saldo_individual(b_chk)
                if txt_av not in lineas_extra:
                    lineas_extra.append(txt_av)

        msg_resp = "\n".join([msg_principal] + lineas_extra)

    return tr, msg_resp, False


def _interpretar_transferencia(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    estado_previo: dict | None = None,
) -> tuple[bool, str | None, dict | None, str | None]:
    """
    Interpreta determinísticamente transferencias entre cuentas propias, extracciones de cajero
    y compra/venta de dólares (Punto 9B).
    Retorna: (es_transferencia, tipo_o_estado, entidades, respuesta_o_pregunta)
    """
    billeteras_usuario = _obtener_billeteras_activas(usuario.id, db)
    m_norm = normalizar_texto(mensaje_texto)

    # Si es señal de suscripción, no interpretar como transferencia/dólares
    if not estado_previo:
        if _es_senial_suscripcion(mensaje_texto) or (_extraer_frecuencia_mencionada(mensaje_texto) and _extraer_nombre_servicio(mensaje_texto)):
            return False, None, None, None

    # Manejo de slot-filling previo para transferir_fondos
    if estado_previo and (estado_previo.get("intent_origen") == "transferir_fondos" or estado_previo.get("tipo_operacion") in ("compra_usd", "venta_usd", "transferencia", "extraccion")):
        tipo_op = estado_previo.get("tipo_operacion")

        # 1. Cotización pendiente para compra/venta dólares
        if tipo_op in ("compra_usd", "venta_usd") and "cotizacion" in estado_previo.get("datos_faltantes", []):
            m_cot = _parsear_monto_argentino(mensaje_texto)
            if not m_cot:
                m_num = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\b", m_norm)
                if m_num:
                    m_cot = _parsear_monto_argentino(m_num.group(1))
            if m_cot:
                if "monto_usd" in estado_previo:
                    dolares = Decimal(str(estado_previo["monto_usd"]))
                    # Candidato 1: m_cot interpretado como cotización unitaria
                    c1_cotiz = m_cot
                    c1_pesos = (dolares * c1_cotiz).quantize(Decimal("0.01"))

                    # Candidato 2: m_cot interpretado como monto total en pesos
                    c2_pesos = m_cot
                    c2_cotiz = (c2_pesos / dolares).quantize(Decimal("0.01"))
                else:
                    pesos_base = Decimal(str(estado_previo["monto_pesos"]))
                    # Candidato 1: m_cot interpretado como cotización unitaria
                    c1_cotiz = m_cot
                    c1_pesos = pesos_base
                    c1_dolares = (pesos_base / c1_cotiz).quantize(Decimal("0.01")) if c1_cotiz > Decimal("0") else Decimal("0")

                    # Candidato 2: m_cot interpretado como monto total en dólares recibidos
                    c2_pesos = pesos_base
                    c2_dolares = m_cot
                    c2_cotiz = (pesos_base / m_cot).quantize(Decimal("0.01")) if m_cot > Decimal("0") else Decimal("0")

                cot_ref = _obtener_cotizacion_referencia_usuario(usuario, db)

                if cot_ref is None:
                    # Sin cotización de referencia: preguntar al usuario mostrando ambas opciones
                    c1_c_str = formatear_monto(float(c1_cotiz), Moneda.ARS)
                    if "monto_usd" in estado_previo:
                        c1_p_str = formatear_monto(float(c1_pesos), Moneda.ARS)
                        c2_p_str = formatear_monto(float(c2_pesos), Moneda.ARS)
                        c2_c_str = formatear_monto(float(c2_cotiz), Moneda.ARS)
                        pregunta = (
                            f"¿Te referís a una cotización de {c1_c_str} por dólar (total {c1_p_str}) "
                            f"o a un total de {c2_p_str} ({c2_c_str} por dólar)?"
                        )
                    else:
                        c2_c_str = formatear_monto(float(c2_cotiz), Moneda.ARS)
                        pregunta = (
                            f"¿Te referís a una cotización de {c1_c_str} por dólar (recibís {formatear_monto(float(c1_dolares), Moneda.USD)}) "
                            f"o a recibir {formatear_monto(float(c2_dolares), Moneda.USD)} ({c2_c_str} por dólar)?"
                        )
                    return True, "slot_filling", estado_previo, pregunta

                # Con cotización de referencia: evaluar plausibilidad y cercanía
                rango_min = (cot_ref * FACTOR_MIN_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                rango_max = (cot_ref * FACTOR_MAX_COTIZACION_DOLAR).quantize(Decimal("0.01"))

                c1_valida = (rango_min <= c1_cotiz <= rango_max)
                c2_valida = (rango_min <= c2_cotiz <= rango_max)

                d1 = abs(c1_cotiz - cot_ref)
                d2 = abs(c2_cotiz - cot_ref)

                if c1_valida and c2_valida:
                    # Ambas plausibles: si están demasiado cerca en distancia relativa, preguntar
                    umbral_ambiguedad = cot_ref * Decimal("0.25")
                    if abs(d1 - d2) < umbral_ambiguedad:
                        c1_c_str = formatear_monto(float(c1_cotiz), Moneda.ARS)
                        if "monto_usd" in estado_previo:
                            c1_p_str = formatear_monto(float(c1_pesos), Moneda.ARS)
                            c2_p_str = formatear_monto(float(c2_pesos), Moneda.ARS)
                            c2_c_str = formatear_monto(float(c2_cotiz), Moneda.ARS)
                            pregunta = (
                                f"¿Te referís a una cotización de {c1_c_str} por dólar (total {c1_p_str}) "
                                f"o a un total de {c2_p_str} ({c2_c_str} por dólar)?"
                            )
                        else:
                            c2_c_str = formatear_monto(float(c2_cotiz), Moneda.ARS)
                            pregunta = (
                                f"¿Te referís a una cotización de {c1_c_str} por dólar (recibís {formatear_monto(float(c1_dolares), Moneda.USD)}) "
                                f"o a recibir {formatear_monto(float(c2_dolares), Moneda.USD)} ({c2_c_str} por dólar)?"
                            )
                        return True, "slot_filling", estado_previo, pregunta
                    if d1 <= d2:
                        cotiz = c1_cotiz
                        pesos = c1_pesos
                        dolares = c1_dolares if "monto_pesos" in estado_previo else dolares
                    else:
                        cotiz = c2_cotiz
                        pesos = c2_pesos
                        dolares = c2_dolares if "monto_pesos" in estado_previo else dolares
                elif c1_valida and not c2_valida:
                    cotiz = c1_cotiz
                    pesos = c1_pesos
                    dolares = c1_dolares if "monto_pesos" in estado_previo else dolares
                elif c2_valida and not c1_valida:
                    cotiz = c2_cotiz
                    pesos = c2_pesos
                    dolares = c2_dolares if "monto_pesos" in estado_previo else dolares
                else:
                    # Fuera de rango plausible
                    candidato_elegido = c1_cotiz if d1 <= d2 else c2_cotiz
                    c_str = formatear_monto(
                        float(candidato_elegido), Moneda.ARS, con_decimales=False
                    )
                    ref_str = formatear_monto(float(cot_ref), Moneda.ARS, con_decimales=False)
                    return (
                        True,
                        "absurda",
                        {},
                        f"¿{c_str} por dólar? Hoy el dólar está a {ref_str}. "
                        "Revisá el número y mandámelo de nuevo.",
                    )

                usd_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.USD and w.estado == EstadoBilletera.ACTIVA]
                ars_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and w.estado == EstadoBilletera.ACTIVA]
                b_usd = usd_wallets[0] if usd_wallets else None
                b_ars = next((w for w in ars_wallets if not w.es_efectivo and w.es_principal), (ars_wallets[0] if ars_wallets else None))

                # Respetar billeteras si ya venían especificadas en estado_previo
                if estado_previo.get("billetera_origen_id") and estado_previo.get("billetera_destino_id"):
                    b_orig_prev = next((w for w in billeteras_usuario if str(w.id) == str(estado_previo["billetera_origen_id"])), None)
                    b_dest_prev = next((w for w in billeteras_usuario if str(w.id) == str(estado_previo["billetera_destino_id"])), None)
                    if b_orig_prev and b_dest_prev:
                        if tipo_op == "compra_usd":
                            b_ars = b_orig_prev
                            b_usd = b_dest_prev
                        else:
                            b_usd = b_orig_prev
                            b_ars = b_dest_prev

                if not b_usd or not b_ars:
                    msg_err_usd = "No tenés las cuentas necesarias para operar en dólares."
                    return True, "error", {}, msg_err_usd

                cotiz_fmt = formatear_monto(float(cotiz), Moneda.ARS, con_decimales=False)
                pesos_fmt = formatear_monto(float(pesos), Moneda.ARS, con_decimales=False)
                dolares_fmt = formatear_monto(float(dolares), Moneda.USD, con_decimales=False)

                if tipo_op == "compra_usd":
                    prop = (
                        f"Salen {pesos_fmt} de {b_ars.nombre} y entran {dolares_fmt} "
                        f"a {b_usd.nombre} (a {cotiz_fmt}).\n¿Anoto la compra?"
                    )
                    entidades = {
                        "tipo_operacion": "compra_usd",
                        "billetera_origen_id": str(b_ars.id),
                        "billetera_destino_id": str(b_usd.id),
                        "monto": float(pesos),
                        "monto_origen": float(pesos),
                        "monto_destino": float(dolares),
                        "moneda_origen": "ARS",
                        "moneda_destino": "USD",
                        "cotizacion": float(cotiz),
                    }
                else:
                    prop = (
                        f"Salen {dolares_fmt} de {b_usd.nombre} y entran {pesos_fmt} "
                        f"a {b_ars.nombre} (a {cotiz_fmt}).\n¿Anoto la venta?"
                    )
                    entidades = {
                        "tipo_operacion": "venta_usd",
                        "billetera_origen_id": str(b_usd.id),
                        "billetera_destino_id": str(b_ars.id),
                        "monto": float(dolares),
                        "monto_origen": float(dolares),
                        "monto_destino": float(pesos),
                        "moneda_origen": "USD",
                        "moneda_destino": "ARS",
                        "cotizacion": float(cotiz),
                    }
                return True, "propuesta", entidades, prop

    # 2. COMPRA Y VENTA DE DÓLARES
    if re.search(r"\b(?:d[oó]lares|verdes|usd|dolarice|cambie\s+pesos\s+a\s+d[oó]lares|cambie\s+d[oó]lares\s+a\s+pesos)\b", m_norm) and not any(w in m_norm for w in ["gaste", "pague", "compre una", "compre un"]):
        es_venta = bool(re.search(r"\b(?:vendi|cambie\s+d[oó]lares\s+a\s+pesos|vender)\b", m_norm))
        es_compra = not es_venta

        usd_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.USD and w.estado == EstadoBilletera.ACTIVA]
        if not usd_wallets:
            return True, "no_usd", {}, "No tenés una cuenta en dólares. Creala en miargentum.com"
        b_usd = usd_wallets[0]

        ars_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and w.estado == EstadoBilletera.ACTIVA]
        if not ars_wallets:
            return True, "error", {}, "No tenés una cuenta en pesos."
        b_ars = None
        for w in ars_wallets:
            if normalizar_texto(w.nombre) in m_norm:
                b_ars = w
                break
        if not b_ars:
            b_ars = next(
                (w for w in ars_wallets if not w.es_efectivo and w.es_principal),
                ars_wallets[0],
            )

        m_usd = re.search(
            r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s*"
            r"(?:d[oó]lares|verdes|usd)\b",
            m_norm,
        )
        m_tasa = re.search(
            r"\ba\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)(?:\b|$)",
            m_norm,
        )
        if m_usd and m_tasa:
            dolares = _parsear_monto_argentino(m_usd.group(1))
            cotiz = _parsear_monto_argentino(m_tasa.group(1))
            cot_ref = _obtener_cotizacion_referencia_usuario(usuario, db)
            if cot_ref is not None:
                rango_min = (cot_ref * FACTOR_MIN_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                rango_max = (cot_ref * FACTOR_MAX_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                if cotiz < rango_min or cotiz > rango_max:
                    c_str = formatear_monto(float(cotiz), Moneda.ARS, con_decimales=False)
                    ref_str = formatear_monto(float(cot_ref), Moneda.ARS, con_decimales=False)
                    return (
                        True,
                        "absurda",
                        {},
                        f"¿{c_str} por dólar? Hoy el dólar está a {ref_str}. "
                        "Revisá el número y mandámelo de nuevo.",
                    )
            pesos = (dolares * cotiz).quantize(Decimal("0.01"))

            cotiz_fmt = formatear_monto(float(cotiz), Moneda.ARS, con_decimales=False)
            pesos_fmt = formatear_monto(float(pesos), Moneda.ARS, con_decimales=False)
            dolares_fmt = formatear_monto(float(dolares), Moneda.USD, con_decimales=False)

            if es_compra:
                prop = (
                    f"Salen {pesos_fmt} de {b_ars.nombre} y entran {dolares_fmt} "
                    f"a {b_usd.nombre} (a {cotiz_fmt}).\n¿Anoto la compra?"
                )
                entidades = {
                    "tipo_operacion": "compra_usd",
                    "billetera_origen_id": str(b_ars.id),
                    "billetera_destino_id": str(b_usd.id),
                    "monto": float(pesos),
                    "monto_origen": float(pesos),
                    "monto_destino": float(dolares),
                    "moneda_origen": "ARS",
                    "moneda_destino": "USD",
                    "cotizacion": float(cotiz),
                }
            else:
                prop = (
                    f"Salen {dolares_fmt} de {b_usd.nombre} y entran {pesos_fmt} "
                    f"a {b_ars.nombre} (a {cotiz_fmt}).\n¿Anoto la venta?"
                )
                entidades = {
                    "tipo_operacion": "venta_usd",
                    "billetera_origen_id": str(b_usd.id),
                    "billetera_destino_id": str(b_ars.id),
                    "monto": float(dolares),
                    "monto_origen": float(dolares),
                    "monto_destino": float(pesos),
                    "moneda_origen": "USD",
                    "moneda_destino": "ARS",
                    "cotizacion": float(cotiz),
                }
            return True, "propuesta", entidades, prop
        else:
            m_d = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s*(?:d[oó]lares|verdes|usd)", m_norm)
            if m_d:
                dolares = _parsear_monto_argentino(m_d.group(1))
                pregunta = (
                    "¿A cuánto compraste o cuántos pesos pagaste?"
                    if es_compra
                    else "¿A cuánto vendiste o cuántos pesos recibiste?"
                )
                entidades = {
                    "intent_origen": "transferir_fondos",
                    "tipo_operacion": "compra_usd" if es_compra else "venta_usd",
                    "monto_usd": float(dolares),
                    "datos_faltantes": ["cotizacion"]
                }
                return True, "slot_filling", entidades, pregunta

    return False, "no_match", {}, None
