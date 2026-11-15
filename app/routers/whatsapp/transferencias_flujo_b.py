# -*- coding: utf-8 -*-
"""
app/routers/whatsapp/transferencias_flujo_b.py — Operaciones auxiliares para el flujo
de transferencias: persistencia de conversaciones, compra/venta de dólares y
transferencias múltiples.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp import aviso_saldo_wpp
from app.routers.whatsapp.constantes import (
    FACTOR_MAX_COTIZACION_DOLAR,
    FACTOR_MIN_COTIZACION_DOLAR,
)
from app.routers.whatsapp.db_lookups import _obtener_cotizacion_referencia_usuario
from app.routers.whatsapp.parsers import (
    _formatear_fecha_natural,
    _parsear_monto_argentino,
)
from app.routers.whatsapp.transferencias_cuentas import resolver_cuenta
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import transferencia_service, whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion
from app.services.saldo_billetera_service import movimiento_anterior_al_saldo
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto


def _guardar_conversacion_sin_efecto(
    usuario: Usuario,
    db: Session,
    mensaje_usuario: str,
    mensaje_bot: str,
    wamid: str | None,
    from_number: str,
) -> None:
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_usuario,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=mensaje_bot,
        intent_detectado="transferir_fondos",
        entidades={},
        accion_ejecutada="sin_efecto",
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, mensaje_bot)


def _guardar_conversacion_slot_filling(
    usuario: Usuario,
    db: Session,
    mensaje_usuario: str,
    mensaje_bot: str,
    estado_sf: dict,
    wamid: str | None,
    from_number: str,
) -> None:
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_usuario,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=mensaje_bot,
        intent_detectado="transferir_fondos",
        entidades=estado_sf,
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=True,
        slot_filling_estado=estado_sf,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, mensaje_bot)


def _guardar_conversacion_propuesta(
    usuario: Usuario,
    db: Session,
    mensaje_usuario: str,
    mensaje_bot: str,
    entidades_prop: dict,
    wamid: str | None,
    from_number: str,
) -> None:
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_usuario,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=mensaje_bot,
        intent_detectado="transferir_fondos",
        entidades=entidades_prop,
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, mensaje_bot)


def _guardar_conversacion_directa(
    usuario: Usuario,
    db: Session,
    mensaje_usuario: str,
    mensaje_bot: str,
    accion_ejecutada: str,
    wamid: str | None,
    from_number: str,
) -> None:
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_usuario,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=mensaje_bot,
        intent_detectado="transferir_fondos",
        entidades={},
        accion_ejecutada=accion_ejecutada,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, mensaje_bot)


def _armar_slot_filling_estado(
    item: dict,
    paso: str,
    entidad_id: str | None = None,
    candidatas_ids: list[str] | None = None,
    origen_ia: bool = False,
) -> dict:
    b_orig = item.get("billetera_origen_obj")
    b_dest = item.get("billetera_destino_obj")
    fecha_obj = item.get("fecha")
    fecha_iso = fecha_obj.isoformat() if isinstance(fecha_obj, date) else None
    monto_val = float(item["monto"]) if item.get("monto") is not None else None
    comision_val = float(item["comision"]) if item.get("comision") is not None else None

    estado = {
        "intent_origen": "transferir_fondos",
        "tipo_operacion": item.get("tipo", "transferencia"),
        "paso": paso,
        "datos_faltantes": [paso],
        "billetera_origen_id": str(b_orig.id) if b_orig else None,
        "billetera_destino_id": str(b_dest.id) if b_dest else None,
        "origen_texto": item.get("origen"),
        "destino_texto": item.get("destino"),
        "monto": monto_val,
        "moneda": item.get("moneda"),
        "fecha": fecha_iso,
        "comision": comision_val,
        "origen_ia": origen_ia,
        "aviso_fecha": item.get("aviso_fecha"),
    }
    if entidad_id:
        estado["entidad_id"] = entidad_id
    if candidatas_ids:
        estado["candidatas_ids"] = candidatas_ids
    return estado


def resolver_transferencia_distinta_moneda(
    b_orig: Billetera,
    b_dest: Billetera,
    item: dict,
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None,
) -> bool:
    es_compra = (b_orig.moneda == Moneda.ARS and b_dest.moneda == Moneda.USD)
    tipo_op = "compra_usd" if es_compra else "venta_usd"
    m_norm = normalizar_texto(mensaje_texto)
    monto = item.get("monto")

    m_usd = re.search(
        r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s*(?:d[oó]lares|verdes|usd)\b",
        m_norm,
    )
    dolares_explicit = _parsear_monto_argentino(m_usd.group(1)) if m_usd else None

    m_ars = re.search(
        r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\s*(?:pesos|ars)\b",
        m_norm,
    )
    pesos_explicit = _parsear_monto_argentino(m_ars.group(1)) if m_ars else None

    m_cotiz = None
    for m_c in re.finditer(
        r"\ba\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\b", m_norm
    ):
        val_c = _parsear_monto_argentino(m_c.group(1))
        if val_c and val_c > Decimal("100"):
            m_cotiz = val_c
            break

    m_son = re.search(
        r"(?:son|que\s+son|equivalen\s+a|por|recib[ií])\s+"
        r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\b",
        m_norm,
    )
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
        dolares = (
            (pesos / cotiz).quantize(Decimal("0.01")) if cotiz > Decimal("0") else None
        )
    elif m_cotiz and monto:
        cotiz = m_cotiz
        if es_compra:
            pesos = monto
            dolares = (
                (pesos / cotiz).quantize(Decimal("0.01")) if cotiz > Decimal("0") else None
            )
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

    if pesos is not None and dolares is not None and cotiz is not None and cotiz > Decimal("0"):
        cot_ref = _obtener_cotizacion_referencia_usuario(usuario, db)
        if cot_ref is not None:
            rango_min = (cot_ref * FACTOR_MIN_COTIZACION_DOLAR).quantize(Decimal("0.01"))
            rango_max = (cot_ref * FACTOR_MAX_COTIZACION_DOLAR).quantize(Decimal("0.01"))
            if cotiz < rango_min or cotiz > rango_max:
                c_str = formatear_monto(float(cotiz), Moneda.ARS)
                ref_str = formatear_monto(float(cot_ref), Moneda.ARS)
                msg_err = (
                    f"La cotización de {c_str} por dólar no parece razonable "
                    f"(la cotización de referencia es de {ref_str}). "
                    f"Por favor verificá el valor e intentá de nuevo."
                )
                _guardar_conversacion_sin_efecto(
                    usuario, db, mensaje_texto, msg_err, wamid, from_number
                )
                return True

        cotiz_fmt = formatear_monto(float(cotiz), Moneda.ARS)
        pesos_fmt = formatear_monto(float(pesos), Moneda.ARS)
        dolares_fmt = formatear_monto(float(dolares), Moneda.USD)

        if es_compra:
            prop = (
                f"Voy a registrar una compra de {dolares_fmt} a {cotiz_fmt}: "
                f"salen {pesos_fmt} de {b_orig.nombre} y entran {dolares_fmt} a "
                f"{b_dest.nombre}. ¿Confirmás?"
            )
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
            prop = (
                f"Voy a registrar una venta de {dolares_fmt} a {cotiz_fmt}: "
                f"salen {dolares_fmt} de {b_orig.nombre} y entran {pesos_fmt} a "
                f"{b_dest.nombre}. ¿Confirmás?"
            )
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
        _guardar_conversacion_propuesta(
            usuario, db, mensaje_texto, prop, entidades, wamid, from_number
        )
        return True

    entidades_sf = {
        "intent_origen": "transferir_fondos",
        "tipo_operacion": tipo_op,
        "billetera_origen_id": str(b_orig.id),
        "billetera_destino_id": str(b_dest.id),
        "datos_faltantes": ["cotizacion"],
    }
    if es_compra:
        if dolares_explicit:
            entidades_sf["monto_usd"] = float(dolares_explicit)
            pregunta = "¿A qué cotización compraste o cuántos pesos pagaste?"
        else:
            entidades_sf["monto_pesos"] = float(monto) if monto else 0.0
            pregunta = (
                f"¿A qué cotización compraste o cuántos dólares "
                f"recibís en {b_dest.nombre}?"
            )
    else:
        if dolares_explicit:
            entidades_sf["monto_usd"] = float(dolares_explicit)
        elif b_orig.moneda == Moneda.USD:
            entidades_sf["monto_usd"] = float(monto) if monto else 0.0
        else:
            entidades_sf["monto_pesos"] = float(monto) if monto else 0.0
        pregunta = f"¿A qué cotización vendiste o cuántos pesos recibís en {b_dest.nombre}?"

    _guardar_conversacion_slot_filling(
        usuario, db, mensaje_texto, pregunta, entidades_sf, wamid, from_number
    )
    return True


def _manejar_multiples_transferencias(
    items: list[dict],
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None,
    billeteras: list[Billetera],
) -> bool:
    items_resueltos = []

    for it in items:
        # Extracción
        if it.get("tipo") == "extraccion":
            mon = it.get("moneda") or "ARS"
            cash_w = [b for b in billeteras if b.es_efectivo and b.moneda.value == mon]
            if len(cash_w) != 1:
                _guardar_conversacion_sin_efecto(
                    usuario,
                    db,
                    mensaje_texto,
                    "No pude entender todas las transferencias. "
                    "Mandame cada una en un mensaje aparte.",
                    wamid,
                    from_number,
                )
                return True
            it["billetera_destino_obj"] = cash_w[0]
            it["destino"] = cash_w[0].nombre

        # Depósito sin origen
        if it.get("tipo") == "deposito" and not it.get("origen"):
            mon = it.get("moneda") or "ARS"
            cash_w = [b for b in billeteras if b.es_efectivo and b.moneda.value == mon]
            if len(cash_w) != 1:
                _guardar_conversacion_sin_efecto(
                    usuario,
                    db,
                    mensaje_texto,
                    "No pude entender todas las transferencias. "
                    "Mandame cada una en un mensaje aparte.",
                    wamid,
                    from_number,
                )
                return True
            it["billetera_origen_obj"] = cash_w[0]
            it["origen"] = cash_w[0].nombre

        # Origen
        if not it.get("origen"):
            _guardar_conversacion_sin_efecto(
                usuario,
                db,
                mensaje_texto,
                "No pude entender todas las transferencias. "
                "Mandame cada una en un mensaje aparte.",
                wamid,
                from_number,
            )
            return True

        b_orig = it.get("billetera_origen_obj")
        if not b_orig:
            st_orig, b_orig, _, _ = resolver_cuenta(
                it["origen"], billeteras, moneda_hint=it.get("moneda")
            )
            if st_orig == "desconocida":
                return False
            if st_orig != "ok" or not b_orig:
                _guardar_conversacion_sin_efecto(
                    usuario,
                    db,
                    mensaje_texto,
                    "No pude entender todas las transferencias. "
                    "Mandame cada una en un mensaje aparte.",
                    wamid,
                    from_number,
                )
                return True
            it["billetera_origen_obj"] = b_orig

        # Destino
        if not it.get("destino"):
            _guardar_conversacion_sin_efecto(
                usuario,
                db,
                mensaje_texto,
                "No pude entender todas las transferencias. "
                "Mandame cada una en un mensaje aparte.",
                wamid,
                from_number,
            )
            return True

        b_dest = it.get("billetera_destino_obj")
        if not b_dest:
            st_dest, b_dest, _, _ = resolver_cuenta(
                it["destino"], billeteras, moneda_hint=b_orig.moneda.value
            )
            if st_dest == "desconocida":
                return False
            if st_dest != "ok" or not b_dest:
                _guardar_conversacion_sin_efecto(
                    usuario,
                    db,
                    mensaje_texto,
                    "No pude entender todas las transferencias. "
                    "Mandame cada una en un mensaje aparte.",
                    wamid,
                    from_number,
                )
                return True
            it["billetera_destino_obj"] = b_dest

        # Mismo origen y destino
        if b_orig.id == b_dest.id:
            _guardar_conversacion_sin_efecto(
                usuario,
                db,
                mensaje_texto,
                "No pude entender todas las transferencias. "
                "Mandame cada una en un mensaje aparte.",
                wamid,
                from_number,
            )
            return True

        # Monto y moneda
        monto = it.get("monto")
        if monto is None or monto <= Decimal("0"):
            _guardar_conversacion_sin_efecto(
                usuario,
                db,
                mensaje_texto,
                "No pude entender todas las transferencias. "
                "Mandame cada una en un mensaje aparte.",
                wamid,
                from_number,
            )
            return True

        if b_orig.moneda != b_dest.moneda:
            _guardar_conversacion_sin_efecto(
                usuario,
                db,
                mensaje_texto,
                "No pude entender todas las transferencias. "
                "Mandame cada una en un mensaje aparte.",
                wamid,
                from_number,
            )
            return True

        if it.get("moneda") and it["moneda"] != b_orig.moneda.value:
            _guardar_conversacion_sin_efecto(
                usuario,
                db,
                mensaje_texto,
                "No pude entender todas las transferencias. "
                "Mandame cada una en un mensaje aparte.",
                wamid,
                from_number,
            )
            return True

        items_resueltos.append((it, b_orig, b_dest))

    # All items are valid: register in a single commit!
    trs_creadas = []
    try:
        for it, b_o, b_d in items_resueltos:
            fecha_obj = it.get("fecha") or hoy_argentina()
            data_tr = TransferenciaInternaCreate(
                billetera_origen_id=b_o.id,
                billetera_destino_id=b_d.id,
                monto=it["monto"],
                moneda=b_o.moneda,
                monto_origen=it["monto"],
                monto_destino=it["monto"],
                moneda_origen=b_o.moneda,
                moneda_destino=b_d.moneda,
                monto_comision=it.get("comision"),
                moneda_comision=b_o.moneda if it.get("comision") else None,
                fecha=fecha_obj,
                notas=it.get("notas", "Transferencia por WhatsApp"),
            )
            tr = transferencia_service.crear_transferencia(
                db, usuario.id, data_tr, commit=False
            )
            trs_creadas.append(tr)

        emitir_evento_actualizacion(db, usuario.id, "transferencias")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.commit()
    except Exception:
        db.rollback()
        _guardar_conversacion_sin_efecto(
            usuario,
            db,
            mensaje_texto,
            "Hubo un problema al procesar las transferencias.",
            wamid,
            from_number,
        )
        return True

    # Construct unified message
    frases = []
    for it, b_o, b_d in items_resueltos:
        monto_fmt = formatear_monto(float(it["monto"]), b_o.moneda)
        fecha_obj = it.get("fecha") or hoy_argentina()
        fecha_nat = _formatear_fecha_natural(fecha_obj)
        fecha_sufijo = f" ({fecha_nat})" if fecha_nat else ""
        com = it.get("comision")
        com_fmt = formatear_monto(float(com), b_o.moneda) if com else ""
        com_sufijo = f", con {com_fmt} de comisión" if com else ""
        frases.append(
            f"{monto_fmt} de {b_o.nombre} a {b_d.nombre}{fecha_sufijo}{com_sufijo}"
        )

    if len(frases) == 2:
        texto_unido = f"{frases[0]} y {frases[1]}"
    else:
        texto_unido = ", ".join(frases[:-1]) + f" y {frases[-1]}"

    msg_principal = f"Listo. Pasaste {texto_unido}."

    lineas_extra = []
    for it, _, _ in items_resueltos:
        if it.get("aviso_fecha") and it["aviso_fecha"] not in lineas_extra:
            lineas_extra.append(it["aviso_fecha"])

    origenes_vistos = set()
    for _, b_o, _ in items_resueltos:
        if b_o.id not in origenes_vistos:
            origenes_vistos.add(b_o.id)
            db.refresh(b_o)
            if b_o.saldo_actual < Decimal("0"):
                lineas_extra.append(f"{b_o.nombre} quedó en negativo.")

    billeteras_vistas = set()
    for it, b_o, b_d in items_resueltos:
        fecha_obj = it.get("fecha") or hoy_argentina()
        for b_chk in (b_o, b_d):
            if b_chk.id not in billeteras_vistas:
                if movimiento_anterior_al_saldo(b_chk, fecha_obj):
                    billeteras_vistas.add(b_chk.id)
                    txt_av = aviso_saldo_wpp.texto_aviso_saldo_individual(b_chk)
                    if txt_av not in lineas_extra:
                        lineas_extra.append(txt_av)

    msg_final = "\n".join([msg_principal] + lineas_extra)
    ids_str = ",".join(str(t.id) for t in trs_creadas)
    _guardar_conversacion_directa(
        usuario,
        db,
        mensaje_texto,
        msg_final,
        f"transferencias:{ids_str}",
        wamid,
        from_number,
    )
    return True
