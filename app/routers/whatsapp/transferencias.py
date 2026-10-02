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
from app.routers.whatsapp.parsers import (
    _extraer_frecuencia_mencionada,
    _extraer_nombre_servicio,
    _formatear_fecha_natural,
    _parsear_monto_argentino,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.resolvers_cascada import (
    _generar_menu_billeteras,
    resolver_billetera_cascada,
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
                return None, "Esa operación ya fue confirmada.", True
        return None, "No tenés ninguna transferencia pendiente para confirmar.", False

    entidades = conv_previa.entidades or {}
    b_origen_id_str = entidades.get("billetera_origen_id")
    b_destino_id_str = entidades.get("billetera_destino_id")
    if not b_origen_id_str or not b_destino_id_str:
        return None, "No pude procesar la transferencia.", False

    b_orig_id = UUID(str(b_origen_id_str))
    b_dest_id = UUID(str(b_destino_id_str))
    monto = Decimal(str(entidades["monto"]))
    monto_origen = Decimal(str(entidades.get("monto_origen", monto)))
    monto_destino = Decimal(str(entidades.get("monto_destino", monto)))
    moneda_origen = Moneda.USD if entidades.get("moneda_origen") == "USD" else Moneda.ARS
    moneda_destino = Moneda.USD if entidades.get("moneda_destino") == "USD" else Moneda.ARS
    monto_comision = Decimal(str(entidades["monto_comision"])) if entidades.get("monto_comision") else None

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
        fecha=hoy_argentina(),
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
        return None, "Hubo un problema al procesar la transferencia.", False

    b_origen = db.get(Billetera, b_orig_id)
    b_destino = db.get(Billetera, b_dest_id)
    nom_orig = b_origen.nombre if b_origen else "origen"
    nom_dest = b_destino.nombre if b_destino else "destino"

    tipo_op = entidades.get("tipo_operacion", "transferencia")
    if tipo_op == "extraccion":
        msg_resp = f"Listo. Extracción de {formatear_monto(float(monto_origen), moneda_origen)} de {nom_orig} a {nom_dest} registrada."
    elif tipo_op == "compra_usd":
        d_str = formatear_monto(float(monto_destino), Moneda.USD).replace("USD", "").replace("US$", "").strip()
        msg_resp = f"Listo. Compra de USD {d_str} por {formatear_monto(float(monto_origen), Moneda.ARS)} registrada."
    elif tipo_op == "venta_usd":
        d_str = formatear_monto(float(monto_origen), Moneda.USD).replace("USD", "").replace("US$", "").strip()
        msg_resp = f"Listo. Venta de USD {d_str} por {formatear_monto(float(monto_destino), Moneda.ARS)} registrada."
    else:
        msg_resp = f"Listo. Transferí {formatear_monto(float(monto_origen), moneda_origen)} de {nom_orig} a {nom_dest}."

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
                        c1_d_str = f"{int(c1_dolares)}" if c1_dolares == int(c1_dolares) else f"{c1_dolares:g}"
                        c2_d_str = f"{int(c2_dolares)}" if c2_dolares == int(c2_dolares) else f"{c2_dolares:g}"
                        c2_c_str = formatear_monto(float(c2_cotiz), Moneda.ARS)
                        pregunta = (
                            f"¿Te referís a una cotización de {c1_c_str} por dólar (recibís USD {c1_d_str}) "
                            f"o a recibir USD {c2_d_str} ({c2_c_str} por dólar)?"
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
                            c1_d_str = f"{int(c1_dolares)}" if c1_dolares == int(c1_dolares) else f"{c1_dolares:g}"
                            c2_d_str = f"{int(c2_dolares)}" if c2_dolares == int(c2_dolares) else f"{c2_dolares:g}"
                            c2_c_str = formatear_monto(float(c2_cotiz), Moneda.ARS)
                            pregunta = (
                                f"¿Te referís a una cotización de {c1_c_str} por dólar (recibís USD {c1_d_str}) "
                                f"o a recibir USD {c2_d_str} ({c2_c_str} por dólar)?"
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
                    c_str = formatear_monto(float(candidato_elegido), Moneda.ARS)
                    ref_str = formatear_monto(float(cot_ref), Moneda.ARS)
                    return (
                        True,
                        "absurda",
                        {},
                        f"La cotización de {c_str} por dólar no parece razonable (la cotización de referencia es de {ref_str}). Por favor verificá el valor e intentá de nuevo.",
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
                    return True, "error", {}, "No se encontraron las billeteras necesarias para operar en dólares."

                cotiz_fmt = formatear_monto(float(cotiz), Moneda.ARS)
                pesos_fmt = formatear_monto(float(pesos), Moneda.ARS)
                dolares_str = f"{int(dolares)}" if dolares == int(dolares) else f"{dolares:g}"

                if tipo_op == "compra_usd":
                    prop = f"Voy a registrar una compra de USD {dolares_str} a {cotiz_fmt}: salen {pesos_fmt} de {b_ars.nombre} y entran USD {dolares_str} a {b_usd.nombre}. ¿Confirmás?"
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
                    prop = f"Voy a registrar una venta de USD {dolares_str} a {cotiz_fmt}: salen USD {dolares_str} de {b_usd.nombre} y entran {pesos_fmt} a {b_ars.nombre}. ¿Confirmás?"
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

        # 2. Billetera origen pendiente para transferencias
        if "billetera_origen" in estado_previo.get("datos_faltantes", []):
            b_dest_id_str = estado_previo.get("billetera_destino_id")
            b_dest = next((w for w in billeteras_usuario if str(w.id) == b_dest_id_str), None)
            cands = [w for w in billeteras_usuario if w.moneda == (b_dest.moneda if b_dest else Moneda.ARS) and str(w.id) != b_dest_id_str and w.estado == EstadoBilletera.ACTIVA]
            b_elegida = None
            if mensaje_texto.strip().isdigit():
                idx = int(mensaje_texto.strip()) - 1
                if 0 <= idx < len(cands):
                    b_elegida = cands[idx]
            else:
                b_match, _ = resolver_billetera_cascada(mensaje_texto.strip(), cands)
                if b_match:
                    b_elegida = b_match

            if b_elegida and b_dest:
                if b_elegida.id == b_dest.id:
                    return True, "misma_billetera", {}, "La billetera de origen y destino no pueden ser la misma."
                monto = Decimal(str(estado_previo["monto"]))
                monto_fmt = formatear_monto(float(monto), b_elegida.moneda)
                prop = f"Voy a transferir {monto_fmt} de {b_elegida.nombre} a {b_dest.nombre}. ¿Confirmás?"
                entidades = {
                    "tipo_operacion": "transferencia",
                    "billetera_origen_id": str(b_elegida.id),
                    "billetera_destino_id": str(b_dest.id),
                    "billetera_origen": b_elegida.nombre,
                    "billetera_destino": b_dest.nombre,
                    "monto": float(monto),
                    "monto_origen": float(monto),
                    "monto_destino": float(monto),
                    "moneda_origen": b_elegida.moneda.value,
                    "moneda_destino": b_dest.moneda.value,
                }
                return True, "propuesta", entidades, prop

    # 1. EXTRACCIÓN DE CAJERO
    if (re.search(r"\b(?:saque|retire|extraje|fui\s+al)\b.*\b(?:cajero|banco)\b", m_norm) or
        re.search(r"\bsaque\s+del\s+cajero\b", m_norm) or
        re.search(r"\bsaque\s+(?:plata|dinero)\b", m_norm) or
        re.search(r"\bextraccion(?:\s+de\s+cajero)?\b", m_norm)):

        m_num = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\b", m_norm)
        monto = _parsear_monto_argentino(m_num.group(1)) if m_num else None

        comision = None
        m_com = re.search(r"(?:con|mas)\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s+(?:de\s+)?comision", m_norm)
        if m_com:
            comision = _parsear_monto_argentino(m_com.group(1))

        cash_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and w.es_efectivo and w.estado == EstadoBilletera.ACTIVA]
        if not cash_wallets:
            return True, "no_cash", {}, "No tenés ninguna billetera de efectivo en pesos. Podés crearla desde la web de Argentum."

        b_dest = cash_wallets[0] if len(cash_wallets) == 1 else None

        non_cash = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and not w.es_efectivo and w.estado == EstadoBilletera.ACTIVA]
        b_orig = None
        for w in non_cash:
            if normalizar_texto(w.nombre) in m_norm:
                b_orig = w
                break
        if not b_orig:
            ppal = next((w for w in non_cash if w.es_principal), None)
            if ppal:
                b_orig = ppal
            elif len(non_cash) == 1:
                b_orig = non_cash[0]

        if not b_orig or not b_dest or not monto:
            cands_banco = [w for w in non_cash if w.estado == EstadoBilletera.ACTIVA]
            menu = _generar_menu_billeteras(cands_banco, tipo="egreso")
            return True, "slot_filling", {"intent_origen": "transferir_fondos", "tipo_operacion": "extraccion", "monto": float(monto) if monto else None, "datos_faltantes": ["billetera_origen"]}, f"¿De qué cuenta bancaria sale la plata?\n{menu}"

        monto_fmt = formatear_monto(float(monto), Moneda.ARS)
        if comision:
            com_fmt = formatear_monto(float(comision), Moneda.ARS)
            msg = f"Voy a registrar una extracción de {monto_fmt} de {b_orig.nombre} a {b_dest.nombre} (con {com_fmt} de comisión). ¿Confirmás?"
        else:
            msg = f"Voy a registrar una extracción de {monto_fmt} de {b_orig.nombre} a {b_dest.nombre}. ¿Confirmás?"

        entidades = {
            "tipo_operacion": "extraccion",
            "billetera_origen_id": str(b_orig.id),
            "billetera_destino_id": str(b_dest.id),
            "billetera_origen": b_orig.nombre,
            "billetera_destino": b_dest.nombre,
            "monto": float(monto),
            "monto_origen": float(monto),
            "monto_destino": float(monto),
            "moneda_origen": "ARS",
            "moneda_destino": "ARS",
            "monto_comision": float(comision) if comision else None,
        }
        return True, "propuesta", entidades, msg

    # 2. COMPRA Y VENTA DE DÓLARES
    if re.search(r"\b(?:d[oó]lares|verdes|usd|dolarice|cambie\s+pesos\s+a\s+d[oó]lares|cambie\s+d[oó]lares\s+a\s+pesos)\b", m_norm) and not any(w in m_norm for w in ["gaste", "pague", "compre una", "compre un"]):
        es_venta = bool(re.search(r"\b(?:vendi|cambie\s+d[oó]lares\s+a\s+pesos|vender)\b", m_norm))
        es_compra = not es_venta

        usd_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.USD and w.estado == EstadoBilletera.ACTIVA]
        if not usd_wallets:
            return True, "no_usd", {}, "No tenés ninguna billetera en dólares. Podés crearla desde la web de Argentum."
        b_usd = usd_wallets[0]

        ars_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and w.estado == EstadoBilletera.ACTIVA]
        if not ars_wallets:
            return True, "error", {}, "No tenés ninguna billetera en pesos."
        b_ars = next((w for w in ars_wallets if not w.es_efectivo and w.es_principal), ars_wallets[0])

        m_cot = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s*(?:d[oó]lares|verdes|usd)\s+a\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)", m_norm)
        if m_cot:
            dolares = _parsear_monto_argentino(m_cot.group(1))
            cotiz = _parsear_monto_argentino(m_cot.group(2))
            cot_ref = _obtener_cotizacion_referencia_usuario(usuario, db)
            if cot_ref is not None:
                rango_min = (cot_ref * FACTOR_MIN_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                rango_max = (cot_ref * FACTOR_MAX_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                if cotiz < rango_min or cotiz > rango_max:
                    c_str = formatear_monto(float(cotiz), Moneda.ARS)
                    ref_str = formatear_monto(float(cot_ref), Moneda.ARS)
                    return (
                        True,
                        "absurda",
                        {},
                        f"La cotización de {c_str} por dólar no parece razonable (la cotización de referencia es de {ref_str}). Por favor verificá el valor e intentá de nuevo.",
                    )
            pesos = (dolares * cotiz).quantize(Decimal("0.01"))

            cotiz_fmt = formatear_monto(float(cotiz), Moneda.ARS)
            pesos_fmt = formatear_monto(float(pesos), Moneda.ARS)
            dolares_str = f"{int(dolares)}" if dolares == int(dolares) else f"{dolares:g}"

            if es_compra:
                prop = f"Voy a registrar una compra de USD {dolares_str} a {cotiz_fmt}: salen {pesos_fmt} de {b_ars.nombre} y entran USD {dolares_str} a {b_usd.nombre}. ¿Confirmás?"
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
                prop = f"Voy a registrar una venta de USD {dolares_str} a {cotiz_fmt}: salen USD {dolares_str} de {b_usd.nombre} y entran {pesos_fmt} a {b_ars.nombre}. ¿Confirmás?"
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
                pregunta = "¿A qué cotización compraste o cuántos pesos pagaste?" if es_compra else "¿A qué cotización vendiste o cuántos pesos recibiste?"
                entidades = {
                    "intent_origen": "transferir_fondos",
                    "tipo_operacion": "compra_usd" if es_compra else "venta_usd",
                    "monto_usd": float(dolares),
                    "datos_faltantes": ["cotizacion"]
                }
                return True, "slot_filling", entidades, pregunta

    # 3. TRANSFERENCIAS ENTRE CUENTAS PROPIAS
    # Exclusión explícita de terceros: gastos a otra persona
    if (re.search(r"\b(?:le\s+transfer[ií]|le\s+mand[eé]|le\s+pas[eé]|le\s+pagu[eé])\b", m_norm) or
        re.search(r"\b(?:a\s+mi\s+hermano|a\s+mi\s+hermana|a\s+mi\s+mam[aá]|a\s+mi\s+pap[aá]|a\s+un\s+amigo|a\s+juan|a\s+pedro)\b", m_norm)):
        return False, "gasto_tercero", {}, None

    if re.search(r"\b(?:pas[eé]|transfer[ií]|me\s+transfer[ií]|mand[eé]|mov[ií]|cargu[eé]|mande\s+plata|pase\s+plata)\b", m_norm):
        if "sube" in m_norm:
            return False, "gasto_sube", {}, None

        m_num = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\b", m_norm)
        monto = _parsear_monto_argentino(m_num.group(1)) if m_num else None

        # Patrón "de X a Y"
        m_de_a = re.search(r"de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+?)\s+a\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+)", m_norm)
        b_orig = None
        b_dest = None
        if m_de_a:
            nom_orig = m_de_a.group(1).strip()
            nom_dest = m_de_a.group(2).strip()
            b_orig, _ = resolver_billetera_cascada(nom_orig, billeteras_usuario)
            b_dest, _ = resolver_billetera_cascada(nom_dest, billeteras_usuario)
        else:
            # Patrón "a Y"
            m_a = re.search(r"a\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+)", m_norm)
            if m_a:
                nom_dest = m_a.group(1).strip()
                b_dest, _ = resolver_billetera_cascada(nom_dest, billeteras_usuario)
                if b_dest:
                    ppal = next((w for w in billeteras_usuario if w.es_principal and w.moneda == b_dest.moneda and w.id != b_dest.id), None)
                    if ppal:
                        b_orig = ppal

        # Si no hubo coincidencia con ninguna billetera del usuario, es un gasto hacia un tercero
        if not b_dest and not m_de_a:
            return False, "no_es_transferencia_propia", {}, None

        if b_orig and b_dest and b_orig.id == b_dest.id:
            return True, "misma_billetera", {}, "La billetera de origen y destino no pueden ser la misma."

        if b_orig and b_dest and monto:
            if b_orig.moneda == b_dest.moneda:
                monto_fmt = formatear_monto(float(monto), b_orig.moneda)
                prop = f"Voy a transferir {monto_fmt} de {b_orig.nombre} a {b_dest.nombre}. ¿Confirmás?"
                entidades = {
                    "tipo_operacion": "transferencia",
                    "billetera_origen_id": str(b_orig.id),
                    "billetera_destino_id": str(b_dest.id),
                    "billetera_origen": b_orig.nombre,
                    "billetera_destino": b_dest.nombre,
                    "monto": float(monto),
                    "monto_origen": float(monto),
                    "monto_destino": float(monto),
                    "moneda_origen": b_orig.moneda.value,
                    "moneda_destino": b_dest.moneda.value,
                }
                return True, "propuesta", entidades, prop

            # Transferencia entre billeteras de distinta moneda (ARS <-> USD)
            es_compra = (b_orig.moneda == Moneda.ARS and b_dest.moneda == Moneda.USD)
            tipo_op = "compra_usd" if es_compra else "venta_usd"

            # 1. Detectar si el usuario ya dio ambos montos o cotización explícita en el mismo mensaje
            m_usd = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s*(?:d[oó]lares|verdes|usd)\b", m_norm)
            dolares_explicit = _parsear_monto_argentino(m_usd.group(1)) if m_usd else None

            m_ars = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\s*(?:pesos|ars)\b", m_norm)
            pesos_explicit = _parsear_monto_argentino(m_ars.group(1)) if m_ars else None

            m_cotiz = None
            for m_c in re.finditer(r"\ba\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\b", m_norm):
                val_c = _parsear_monto_argentino(m_c.group(1))
                if val_c and val_c > Decimal("100"):
                    m_cotiz = val_c
                    break

            m_son = re.search(r"(?:son|que\s+son|equivalen\s+a|por|recib[ií])\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\b", m_norm)
            segundo_monto = _parsear_monto_argentino(m_son.group(1)) if m_son else None

            pesos = None
            dolares = None
            cotiz = None

            if dolares_explicit and pesos_explicit:
                dolares = dolares_explicit
                pesos = pesos_explicit
            elif dolares_explicit and monto and monto != dolares_explicit:
                dolares = dolares_explicit
                pesos = monto
            elif pesos_explicit and monto and monto != pesos_explicit:
                pesos = pesos_explicit
                dolares = monto
            elif dolares_explicit and m_cotiz:
                dolares = dolares_explicit
                cotiz = m_cotiz
                pesos = (dolares * cotiz).quantize(Decimal("0.01"))
            elif pesos_explicit and m_cotiz:
                pesos = pesos_explicit
                cotiz = m_cotiz
                dolares = (pesos / cotiz).quantize(Decimal("0.01")) if cotiz > Decimal("0") else None
            elif m_cotiz and monto:
                cotiz = m_cotiz
                if es_compra:
                    pesos = monto
                    dolares = (pesos / cotiz).quantize(Decimal("0.01")) if cotiz > Decimal("0") else None
                else:
                    dolares = monto
                    pesos = (dolares * cotiz).quantize(Decimal("0.01"))
            elif segundo_monto and monto and segundo_monto != monto:
                if es_compra:
                    pesos = monto
                    dolares = segundo_monto
                else:
                    dolares = monto
                    pesos = segundo_monto

            if pesos is not None and dolares is not None and dolares > Decimal("0") and cotiz is None:
                cotiz = (pesos / dolares).quantize(Decimal("0.01"))

            # Si ya tenemos ambos montos y cotización, validar plausibilidad y armar propuesta
            if pesos is not None and dolares is not None and cotiz is not None and cotiz > Decimal("0"):
                cot_ref = _obtener_cotizacion_referencia_usuario(usuario, db)
                if cot_ref is not None:
                    rango_min = (cot_ref * FACTOR_MIN_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                    rango_max = (cot_ref * FACTOR_MAX_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                    if cotiz < rango_min or cotiz > rango_max:
                        c_str = formatear_monto(float(cotiz), Moneda.ARS)
                        ref_str = formatear_monto(float(cot_ref), Moneda.ARS)
                        return (
                            True,
                            "absurda",
                            {},
                            f"La cotización de {c_str} por dólar no parece razonable (la cotización de referencia es de {ref_str}). Por favor verificá el valor e intentá de nuevo.",
                        )

                cotiz_fmt = formatear_monto(float(cotiz), Moneda.ARS)
                pesos_fmt = formatear_monto(float(pesos), Moneda.ARS)
                dolares_str = f"{int(dolares)}" if dolares == int(dolares) else f"{dolares:g}"

                if es_compra:
                    prop = f"Voy a registrar una compra de USD {dolares_str} a {cotiz_fmt}: salen {pesos_fmt} de {b_orig.nombre} y entran USD {dolares_str} a {b_dest.nombre}. ¿Confirmás?"
                    entidades = {
                        "tipo_operacion": "compra_usd",
                        "billetera_origen_id": str(b_orig.id),
                        "billetera_destino_id": str(b_dest.id),
                        "monto": float(pesos),
                        "monto_origen": float(pesos),
                        "monto_destino": float(dolares),
                        "moneda_origen": "ARS",
                        "moneda_destino": "USD",
                        "cotizacion": float(cotiz),
                    }
                else:
                    prop = f"Voy a registrar una venta de USD {dolares_str} a {cotiz_fmt}: salen USD {dolares_str} de {b_orig.nombre} y entran {pesos_fmt} a {b_dest.nombre}. ¿Confirmás?"
                    entidades = {
                        "tipo_operacion": "venta_usd",
                        "billetera_origen_id": str(b_orig.id),
                        "billetera_destino_id": str(b_dest.id),
                        "monto": float(dolares),
                        "monto_origen": float(dolares),
                        "monto_destino": float(pesos),
                        "moneda_origen": "USD",
                        "moneda_destino": "ARS",
                        "cotizacion": float(cotiz),
                    }
                return True, "propuesta", entidades, prop

            # 2. Si no se especificó cotización ni segundo monto: activar slot-filling
            entidades = {
                "intent_origen": "transferir_fondos",
                "tipo_operacion": tipo_op,
                "billetera_origen_id": str(b_orig.id),
                "billetera_destino_id": str(b_dest.id),
                "datos_faltantes": ["cotizacion"]
            }
            if es_compra:
                if dolares_explicit:
                    entidades["monto_usd"] = float(dolares_explicit)
                    pregunta = "¿A qué cotización compraste o cuántos pesos pagaste?"
                else:
                    entidades["monto_pesos"] = float(monto)
                    pregunta = f"¿A qué cotización compraste o cuántos dólares recibís en {b_dest.nombre}?"
            else:
                if dolares_explicit:
                    entidades["monto_usd"] = float(dolares_explicit)
                elif b_orig.moneda == Moneda.USD:
                    entidades["monto_usd"] = float(monto)
                else:
                    entidades["monto_pesos"] = float(monto)
                pregunta = f"¿A qué cotización vendiste o cuántos pesos recibís en {b_dest.nombre}?"

            return True, "slot_filling", entidades, pregunta

        if b_dest and monto and not b_orig:
            entidades = {
                "intent_origen": "transferir_fondos",
                "tipo_operacion": "transferencia",
                "billetera_destino_id": str(b_dest.id),
                "billetera_destino": b_dest.nombre,
                "monto": float(monto),
                "datos_faltantes": ["billetera_origen"]
            }
            cands = [w for w in billeteras_usuario if w.moneda == b_dest.moneda and w.id != b_dest.id and w.estado == EstadoBilletera.ACTIVA]
            menu = _generar_menu_billeteras(cands, tipo="egreso")
            pregunta = f"¿De qué billetera sale la plata?\n{menu}"
            return True, "slot_filling", entidades, pregunta

    return False, "no_match", {}, None
