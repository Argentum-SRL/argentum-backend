# -*- coding: utf-8 -*-
"""
app/routers/whatsapp/transferencias_flujo.py — Flujo determinístico y conversacional
de transferencias internas, extracciones de cajero y depósitos (Bloque M1).
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.entidades import ENTIDADES
from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp import aviso_personas_wpp, aviso_saldo_wpp, registro
from app.routers.whatsapp.montos_palabras import buscar_monto
from app.core.reglas_texto_wpp import ejemplo_wpp, fecha_corta_wpp
from app.routers.whatsapp.parsers import _resolver_y_validar_fecha
from app.routers.whatsapp.resolvers_cascada import _generar_menu_billeteras
from app.routers.whatsapp.transferencias_cuentas import (
    billeteras_para_transferir,
    candidatas_menu,
    resolver_cuenta,
)
from app.routers.whatsapp.transferencias_flujo_b import (
    _armar_slot_filling_estado,
    _guardar_conversacion_directa,
    _guardar_conversacion_propuesta,
    _guardar_conversacion_sin_efecto,
    _guardar_conversacion_slot_filling,
    _manejar_multiples_transferencias,
    resolver_transferencia_distinta_moneda,
)
from app.routers.whatsapp.transferencias_texto import (
    extraer_transferencias,
    normalizar_basico,
)
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import transferencia_service, whatsapp_service
from app.services.billetera_alta_service import crear_billetera_sin_saldo
from app.services.evento_service import emitir_evento_actualizacion
from app.services.saldo_billetera_service import movimiento_anterior_al_saldo
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto


def _registrar_transferencia_directa(
    b_orig: Billetera,
    b_dest: Billetera,
    item: dict,
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None,
    cuenta_creada_nombre: str | None = None,
) -> bool:
    fecha_obj = item.get("fecha") or hoy_argentina()
    monto = item["monto"]
    comision = item.get("comision")

    data_tr = TransferenciaInternaCreate(
        billetera_origen_id=b_orig.id,
        billetera_destino_id=b_dest.id,
        monto=monto,
        moneda=b_orig.moneda,
        monto_origen=monto,
        monto_destino=monto,
        moneda_origen=b_orig.moneda,
        moneda_destino=b_dest.moneda,
        monto_comision=comision,
        moneda_comision=b_orig.moneda if comision else None,
        fecha=fecha_obj,
        notas=item.get("notas", "Transferencia por WhatsApp"),
    )

    try:
        tr = transferencia_service.crear_transferencia(
            db, usuario.id, data_tr, commit=False
        )
        emitir_evento_actualizacion(db, usuario.id, "transferencias")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.commit()
    except HTTPException as exc:
        db.rollback()
        _guardar_conversacion_sin_efecto(
            usuario, db, mensaje_texto, str(exc.detail), wamid, from_number
        )
        return True
    except Exception:
        db.rollback()
        _guardar_conversacion_sin_efecto(
            usuario,
            db,
            mensaje_texto,
            "Hubo un problema y no anoté nada. Probá de nuevo en un rato.",
            wamid,
            from_number,
        )
        return True

    monto_fmt = formatear_monto(float(monto), b_orig.moneda, con_decimales=False)
    f_corta = fecha_corta_wpp(fecha_obj, hoy_argentina())
    fecha_sufijo = f" ({f_corta})" if f_corta else ""
    com_fmt = (
        formatear_monto(float(comision), b_orig.moneda, con_decimales=False)
        if comision
        else ""
    )
    comision_sufijo = f", con {com_fmt} de comisión" if comision else ""

    if cuenta_creada_nombre:
        msg_principal = (
            f"Listo. Creé {cuenta_creada_nombre} y pasaste "
            f"{monto_fmt} de {b_orig.nombre} a {b_dest.nombre}"
            f"{fecha_sufijo}{comision_sufijo}."
        )
    else:
        msg_principal = (
            f"Listo. Pasaste {monto_fmt} de {b_orig.nombre} a "
            f"{b_dest.nombre}{fecha_sufijo}{comision_sufijo}."
        )

    lineas_extra = []
    if item.get("aviso_fecha"):
        lineas_extra.append(item["aviso_fecha"])

    db.refresh(b_orig)
    if b_orig.saldo_actual < Decimal("0"):
        lineas_extra.append(f"{b_orig.nombre} quedó en negativo.")

    for b_chk in (b_orig, b_dest):
        if movimiento_anterior_al_saldo(b_chk, fecha_obj):
            txt_aviso = aviso_saldo_wpp.texto_aviso_saldo_individual(b_chk)
            if txt_aviso not in lineas_extra:
                lineas_extra.append(txt_aviso)

    msg_final = "\n".join([msg_principal] + lineas_extra)

    _guardar_conversacion_directa(
        usuario, db, mensaje_texto, msg_final, f"transferencia:{tr.id}", wamid, from_number
    )
    return True


def _proponer_transferencia_ia(
    b_orig: Billetera,
    b_dest: Billetera,
    item: dict,
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None,
    cuenta_creada_nombre: str | None = None,
) -> bool:
    fecha_obj = item.get("fecha") or hoy_argentina()
    monto = item["monto"]
    comision = item.get("comision")

    monto_fmt = formatear_monto(float(monto), b_orig.moneda, con_decimales=False)
    f_corta = fecha_corta_wpp(fecha_obj, hoy_argentina())
    fecha_sufijo = f" ({f_corta})" if f_corta else ""
    com_fmt = (
        formatear_monto(float(comision), b_orig.moneda, con_decimales=False)
        if comision
        else ""
    )
    comision_sufijo = f", con {com_fmt} de comisión" if comision else ""

    if cuenta_creada_nombre:
        msg_prop = (
            f"Creé {cuenta_creada_nombre}. ¿Paso {monto_fmt} de {b_orig.nombre} "
            f"a {b_dest.nombre}{fecha_sufijo}{comision_sufijo}?"
        )
    else:
        msg_prop = (
            f"¿Paso {monto_fmt} de {b_orig.nombre} a "
            f"{b_dest.nombre}{fecha_sufijo}{comision_sufijo}?"
        )

    entidades_prop = {
        "tipo_operacion": item.get("tipo", "transferencia"),
        "billetera_origen_id": str(b_orig.id),
        "billetera_destino_id": str(b_dest.id),
        "billetera_origen": b_orig.nombre,
        "billetera_destino": b_dest.nombre,
        "monto": float(monto),
        "monto_origen": float(monto),
        "monto_destino": float(monto),
        "moneda_origen": b_orig.moneda.value,
        "moneda_destino": b_dest.moneda.value,
        "monto_comision": float(comision) if comision else None,
        "fecha": fecha_obj.isoformat(),
        "aviso_fecha": item.get("aviso_fecha"),
    }
    _guardar_conversacion_propuesta(
        usuario, db, mensaje_texto, msg_prop, entidades_prop, wamid, from_number
    )
    return True


def _ejecutar_tabla_flujo(
    item: dict,
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None,
    billeteras: list[Billetera],
    origen_ia: bool = False,
    cuenta_creada: str | None = None,
) -> bool:
    # 1. Extracción: destino efectivo de la moneda
    if item.get("tipo") == "extraccion":
        moneda_ext = item.get("moneda") or "ARS"
        cash_wallets = [
            b for b in billeteras if b.es_efectivo and b.moneda.value == moneda_ext
        ]
        if not cash_wallets:
            mon_e = "dólares" if moneda_ext == "USD" else "pesos"
            msg = f"No tenés una cuenta de efectivo en {mon_e}. Creala en miargentum.com"
            _guardar_conversacion_sin_efecto(
                usuario, db, mensaje_texto, msg, wamid, from_number
            )
            return True
        if len(cash_wallets) == 1:
            item["billetera_destino_obj"] = cash_wallets[0]
            item["destino"] = cash_wallets[0].nombre

    # 2. Depósito sin origen: origen efectivo de la moneda si hay uno
    if item.get("tipo") == "deposito" and not item.get("origen"):
        moneda_dep = item.get("moneda") or "ARS"
        cash_wallets = [
            b for b in billeteras if b.es_efectivo and b.moneda.value == moneda_dep
        ]
        if not cash_wallets:
            mon_d = "dólares" if moneda_dep == "USD" else "pesos"
            msg = f"No tenés una cuenta de efectivo en {mon_d}. Creala en miargentum.com"
            _guardar_conversacion_sin_efecto(
                usuario, db, mensaje_texto, msg, wamid, from_number
            )
            return True
        if len(cash_wallets) == 1:
            item["billetera_origen_obj"] = cash_wallets[0]
            item["origen"] = cash_wallets[0].nombre

    # 3. Resolver origen y destino
    b_orig = item.get("billetera_origen_obj")
    if b_orig:
        st_orig, cands_orig, ent_orig = "ok", [], None
    elif item.get("origen"):
        st_orig, b_orig, cands_orig, ent_orig = resolver_cuenta(
            item["origen"], billeteras, moneda_hint=item.get("moneda")
        )
        item["billetera_origen_obj"] = b_orig
    else:
        st_orig, cands_orig, ent_orig = "falta", [], None

    b_dest = item.get("billetera_destino_obj")
    if b_dest:
        st_dest, cands_dest, ent_dest = "ok", [], None
    elif item.get("destino"):
        mon_hint_dest = item.get("moneda") or (b_orig.moneda.value if b_orig else None)
        st_dest, b_dest, cands_dest, ent_dest = resolver_cuenta(
            item["destino"], billeteras, moneda_hint=mon_hint_dest
        )
        item["billetera_destino_obj"] = b_dest
    else:
        st_dest, cands_dest, ent_dest = "falta", [], None

    if st_orig == "desconocida" or st_dest == "desconocida":
        if not origen_ia:
            return False
        cuenta_raw = (
            item.get("raw_origen") or item.get("origen")
            if st_orig == "desconocida"
            else item.get("raw_destino") or item.get("destino")
        )
        ex_p = ejemplo_wpp("pasé 20 mil de Galicia a Mercado Pago")
        msg_ia = f"No encontré la cuenta {cuenta_raw}. Mandámelo así: {ex_p}."
        _guardar_conversacion_sin_efecto(
            usuario, db, mensaje_texto, msg_ia, wamid, from_number
        )
        return True

    # 4. Origen "no_cargada" / Destino "no_cargada"
    if st_orig == "no_cargada":
        ent_nom = ENTIDADES.get(ent_orig, {}).get("nombre", ent_orig.capitalize())
        msg = f"No tenés {ent_nom} cargada.\n1. Crearla\n2. Era otra de tus cuentas"
        estado_sf = _armar_slot_filling_estado(
            item, paso="origen_no_cargada", entidad_id=ent_orig, origen_ia=origen_ia
        )
        _guardar_conversacion_slot_filling(
            usuario, db, mensaje_texto, msg, estado_sf, wamid, from_number
        )
        return True

    if st_dest == "no_cargada":
        ent_nom = ENTIDADES.get(ent_dest, {}).get("nombre", ent_dest.capitalize())
        msg = (
            f"No tenés {ent_nom} cargada.\n"
            f"1. Crearla\n2. Era otra de tus cuentas\n3. Fue un pago a otra persona"
        )
        estado_sf = _armar_slot_filling_estado(
            item, paso="destino_no_cargada", entidad_id=ent_dest, origen_ia=origen_ia
        )
        _guardar_conversacion_slot_filling(
            usuario, db, mensaje_texto, msg, estado_sf, wamid, from_number
        )
        return True

    # 5. Origen "ambigua" o falta
    if st_orig in ("ambigua", "falta"):
        moneda_cands = (
            Moneda.USD
            if (item.get("moneda") == "USD" or (b_dest and b_dest.moneda == Moneda.USD))
            else Moneda.ARS
        )
        excluir = b_dest.id if b_dest else None
        solo_bancos = (item.get("tipo") == "extraccion")

        if st_orig == "ambigua" and cands_orig:
            cands = [
                c for c in cands_orig
                if not c.es_inversion and (excluir is None or c.id != excluir)
            ]
        else:
            cands = candidatas_menu(
                billeteras, moneda_cands, excluir_id=excluir, solo_bancos=solo_bancos
            )

        if len(cands) == 0:
            mon_txt = "en dólares" if moneda_cands == Moneda.USD else "en pesos"
            msg = f"No tenés otra cuenta {mon_txt} para esa transferencia."
            _guardar_conversacion_sin_efecto(
                usuario, db, mensaje_texto, msg, wamid, from_number
            )
            return True
        elif len(cands) == 1:
            b_orig = cands[0]
            item["billetera_origen_obj"] = b_orig
            st_orig = "ok"
        else:
            monto_fmt = (
                formatear_monto(float(item["monto"]), moneda_cands, con_decimales=False)
                if item.get("monto")
                else "la plata"
            )
            if item.get("tipo") == "extraccion":
                encabezado = f"¿De qué cuenta sacaste los {monto_fmt}?"
            else:
                dest_nom = b_dest.nombre if b_dest else "la otra cuenta"
                encabezado = f"¿Desde qué cuenta pasaste los {monto_fmt} a {dest_nom}?"
            menu = _generar_menu_billeteras(cands, encabezado=encabezado)
            estado_sf = _armar_slot_filling_estado(
                item,
                paso="elegir_origen",
                candidatas_ids=[str(c.id) for c in cands],
                origen_ia=origen_ia,
            )
            _guardar_conversacion_slot_filling(
                usuario, db, mensaje_texto, menu, estado_sf, wamid, from_number
            )
            return True

    # 6. Destino "ambigua" o falta
    if st_dest in ("ambigua", "falta"):
        moneda_cands = (
            b_orig.moneda
            if b_orig
            else (Moneda.USD if item.get("moneda") == "USD" else Moneda.ARS)
        )
        excluir = b_orig.id if b_orig else None

        if st_dest == "ambigua" and cands_dest:
            cands = [
                c for c in cands_dest
                if not c.es_inversion and (excluir is None or c.id != excluir)
            ]
        else:
            cands = candidatas_menu(
                billeteras, moneda_cands, excluir_id=excluir, solo_bancos=False
            )

        if len(cands) == 0:
            mon_txt = "en dólares" if moneda_cands == Moneda.USD else "en pesos"
            msg = f"No tenés otra cuenta {mon_txt} para esa transferencia."
            _guardar_conversacion_sin_efecto(
                usuario, db, mensaje_texto, msg, wamid, from_number
            )
            return True
        elif len(cands) == 1:
            b_dest = cands[0]
            item["billetera_destino_obj"] = b_dest
            st_dest = "ok"
        else:
            monto_fmt = (
                formatear_monto(float(item["monto"]), moneda_cands, con_decimales=False)
                if item.get("monto")
                else "la plata"
            )
            orig_nom = b_orig.nombre if b_orig else "tu cuenta"
            encabezado = f"¿A qué cuenta pasaste los {monto_fmt} de {orig_nom}?"
            menu = _generar_menu_billeteras(cands, encabezado=encabezado)
            estado_sf = _armar_slot_filling_estado(
                item,
                paso="elegir_destino",
                candidatas_ids=[str(c.id) for c in cands],
                origen_ia=origen_ia,
            )
            _guardar_conversacion_slot_filling(
                usuario, db, mensaje_texto, menu, estado_sf, wamid, from_number
            )
            return True

    # 7. Mismo origen y destino
    if b_orig and b_dest and b_orig.id == b_dest.id:
        msg = "Pusiste la misma cuenta de los dos lados. ¿A qué otra cuenta lo pasaste?"
        _guardar_conversacion_sin_efecto(
            usuario, db, mensaje_texto, msg, wamid, from_number
        )
        return True

    # 8. Moneda escrita distinta de la de las dos cuentas
    if b_orig.moneda == b_dest.moneda:
        moneda_cuentas = b_orig.moneda.value
        moneda_escrita = item.get("moneda")
        if moneda_escrita and moneda_escrita != moneda_cuentas:
            if moneda_cuentas == "ARS":
                msg = "Esas cuentas son en pesos. Mandame el monto en pesos."
            else:
                msg = "Esas cuentas son en dólares. Mandame el monto en dólares."
            _guardar_conversacion_sin_efecto(
                usuario, db, mensaje_texto, msg, wamid, from_number
            )
            return True

    # 9. Cuentas de distinta moneda
    if b_orig.moneda != b_dest.moneda:
        return resolver_transferencia_distinta_moneda(
            b_orig, b_dest, item, mensaje_texto, usuario, db, from_number, wamid
        )

    # 10. Falta el monto
    if item.get("monto") is None:
        msg = f"¿Cuánto pasaste de {b_orig.nombre} a {b_dest.nombre}?"
        estado_sf = _armar_slot_filling_estado(
            item, paso="monto", origen_ia=origen_ia
        )
        _guardar_conversacion_slot_filling(
            usuario, db, mensaje_texto, msg, estado_sf, wamid, from_number
        )
        return True

    # 11. Completa y misma moneda
    if origen_ia:
        return _proponer_transferencia_ia(
            b_orig,
            b_dest,
            item,
            mensaje_texto,
            usuario,
            db,
            from_number,
            wamid,
            cuenta_creada_nombre=cuenta_creada,
        )

    return _registrar_transferencia_directa(
        b_orig,
        b_dest,
        item,
        mensaje_texto,
        usuario,
        db,
        from_number,
        wamid,
        cuenta_creada_nombre=cuenta_creada,
    )


def manejar_texto_transferencia(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    items = extraer_transferencias(mensaje_texto, hoy_argentina())
    if items is None or len(items) == 0:
        return False

    if len(items) > 5:
        msg = "Mandame hasta 5 transferencias por mensaje."
        _guardar_conversacion_sin_efecto(
            usuario, db, mensaje_texto, msg, wamid, from_number
        )
        return True

    billeteras = billeteras_para_transferir(usuario.id, db)

    if len(items) >= 2:
        return _manejar_multiples_transferencias(
            items, mensaje_texto, usuario, db, from_number, wamid, billeteras
        )

    it = items[0]
    if it.get("fecha"):
        f_val, av_f = _resolver_y_validar_fecha(it["fecha"])
        it["fecha"] = f_val
        it["aviso_fecha"] = av_f

    return _ejecutar_tabla_flujo(
        it,
        mensaje_texto,
        usuario,
        db,
        from_number,
        wamid,
        billeteras,
        origen_ia=False,
    )


def manejar_transferencia_ia(
    entidades: dict,
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> None:
    b_orig_str = entidades.get("billetera_origen")
    b_dest_str = entidades.get("billetera_destino")

    if not b_orig_str and not b_dest_str:
        ex_p = ejemplo_wpp("pasé 20 mil de Galicia a Mercado Pago")
        msg = f"Decime de qué cuenta a qué cuenta. Por ejemplo: {ex_p}."
        _guardar_conversacion_sin_efecto(
            usuario, db, mensaje_texto, msg, wamid, from_number
        )
        return

    monto_raw = entidades.get("monto")
    monto = Decimal(str(monto_raw)) if monto_raw is not None else None
    moneda = "USD" if entidades.get("moneda") == "USD" else None

    fecha_raw = entidades.get("fecha")
    fecha_val = None
    aviso_fecha = None
    if fecha_raw:
        try:
            f_parsed = (
                date.fromisoformat(str(fecha_raw))
                if isinstance(fecha_raw, str)
                else fecha_raw
            )
            fecha_val, aviso_fecha = _resolver_y_validar_fecha(f_parsed)
        except Exception:
            fecha_val = hoy_argentina()
    else:
        fecha_val = hoy_argentina()

    comision = (
        Decimal(str(entidades["monto_comision"]))
        if entidades.get("monto_comision")
        else None
    )

    item = {
        "origen": normalizar_basico(b_orig_str) if b_orig_str else None,
        "destino": normalizar_basico(b_dest_str) if b_dest_str else None,
        "raw_origen": b_orig_str,
        "raw_destino": b_dest_str,
        "monto": monto,
        "moneda": moneda,
        "fecha": fecha_val,
        "aviso_fecha": aviso_fecha,
        "comision": comision,
        "tipo": "transferencia",
        "origen_ia": True,
    }

    billeteras = billeteras_para_transferir(usuario.id, db)
    _ejecutar_tabla_flujo(
        item,
        mensaje_texto,
        usuario,
        db,
        from_number,
        wamid,
        billeteras,
        origen_ia=True,
    )


def _es_opcion_1_4a(t: str) -> bool:
    return t in ("1", "1.") or any(w in t for w in ["crearla", "creala", "crear"])


def _es_opcion_2_4a(t: str) -> bool:
    return t in ("2", "2.") or any(
        w in t for w in ["otra cuenta", "era otra", "otra"]
    )


def _es_opcion_3_4a(t: str) -> bool:
    return t in ("3", "3.") or any(
        w in t for w in ["fue un pago", "a otra persona", "otra persona", "pago"]
    )


def continuar_transferencia(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None,
    conv_activa: ConversacionWpp | None,
    estado: dict,
) -> bool:
    paso = estado.get("paso")
    if not paso:
        return False

    billeteras = billeteras_para_transferir(usuario.id, db)
    b_orig = next(
        (b for b in billeteras if str(b.id) == estado.get("billetera_origen_id")),
        None,
    )
    b_dest = next(
        (b for b in billeteras if str(b.id) == estado.get("billetera_destino_id")),
        None,
    )

    fecha_iso = estado.get("fecha")
    fecha_obj = date.fromisoformat(fecha_iso) if fecha_iso else None
    monto_val = (
        Decimal(str(estado["monto"]))
        if estado.get("monto") is not None
        else None
    )
    comision_val = (
        Decimal(str(estado["comision"]))
        if estado.get("comision") is not None
        else None
    )
    origen_ia = bool(estado.get("origen_ia"))

    item = {
        "origen": estado.get("origen_texto"),
        "destino": estado.get("destino_texto"),
        "billetera_origen_obj": b_orig,
        "billetera_destino_obj": b_dest,
        "monto": monto_val,
        "moneda": estado.get("moneda"),
        "fecha": fecha_obj,
        "aviso_fecha": estado.get("aviso_fecha"),
        "comision": comision_val,
        "tipo": estado.get("tipo_operacion", "transferencia"),
        "origen_ia": origen_ia,
    }

    t_norm = normalizar_basico(mensaje_texto)

    # 4A Origen no cargada
    if paso == "origen_no_cargada":
        if _es_opcion_1_4a(t_norm):
            ent_id = estado.get("entidad_id")
            ent_info = ENTIDADES.get(ent_id, {})
            nombre = ent_info.get("nombre", ent_id.capitalize())
            moneda_nueva = (
                b_dest.moneda
                if b_dest
                else (Moneda.USD if item.get("moneda") == "USD" else Moneda.ARS)
            )
            nueva_b = crear_billetera_sin_saldo(
                db, usuario.id, nombre, moneda_nueva, ent_id, commit=False
            )
            item["billetera_origen_obj"] = nueva_b
            item["origen"] = nueva_b.nombre
            if conv_activa:
                conv_activa.slot_filling_activo = False
                db.flush()
            billeteras_actualizadas = billeteras_para_transferir(usuario.id, db)
            return _ejecutar_tabla_flujo(
                item,
                mensaje_texto,
                usuario,
                db,
                from_number,
                wamid,
                billeteras_actualizadas,
                origen_ia=origen_ia,
                cuenta_creada=nombre,
            )
        elif _es_opcion_2_4a(t_norm):
            if conv_activa:
                conv_activa.slot_filling_activo = False
                db.flush()
            item["billetera_origen_obj"] = None
            item["origen"] = None
            return _ejecutar_tabla_flujo(
                item,
                mensaje_texto,
                usuario,
                db,
                from_number,
                wamid,
                billeteras,
                origen_ia=origen_ia,
            )
        return False

    # 4A Destino no cargada
    if paso == "destino_no_cargada":
        if _es_opcion_1_4a(t_norm):
            ent_id = estado.get("entidad_id")
            ent_info = ENTIDADES.get(ent_id, {})
            nombre = ent_info.get("nombre", ent_id.capitalize())
            moneda_nueva = (
                b_orig.moneda
                if b_orig
                else (Moneda.USD if item.get("moneda") == "USD" else Moneda.ARS)
            )
            nueva_b = crear_billetera_sin_saldo(
                db, usuario.id, nombre, moneda_nueva, ent_id, commit=False
            )
            item["billetera_destino_obj"] = nueva_b
            item["destino"] = nueva_b.nombre
            if conv_activa:
                conv_activa.slot_filling_activo = False
                db.flush()
            billeteras_actualizadas = billeteras_para_transferir(usuario.id, db)
            return _ejecutar_tabla_flujo(
                item,
                mensaje_texto,
                usuario,
                db,
                from_number,
                wamid,
                billeteras_actualizadas,
                origen_ia=origen_ia,
                cuenta_creada=nombre,
            )
        elif _es_opcion_2_4a(t_norm):
            if conv_activa:
                conv_activa.slot_filling_activo = False
                db.flush()
            item["billetera_destino_obj"] = None
            item["destino"] = None
            return _ejecutar_tabla_flujo(
                item,
                mensaje_texto,
                usuario,
                db,
                from_number,
                wamid,
                billeteras,
                origen_ia=origen_ia,
            )
        elif _es_opcion_3_4a(t_norm):
            if conv_activa:
                conv_activa.slot_filling_activo = False
                db.flush()
            entidades_gasto = {
                "monto": float(item["monto"]),
                "tipo": "egreso",
                "moneda": item.get("moneda") or (b_orig.moneda.value if b_orig else "ARS"),
                "billetera_origen": b_orig.nombre if b_orig else "Galicia",
                "categoria": "Pagos a personas",
                "descripcion": "Pago a otra persona",
                "fecha": (item["fecha"] or hoy_argentina()).isoformat(),
            }
            tr_obj, msg_gasto = registro._registrar_movimiento_directo(
                usuario, entidades_gasto, db, registrar_adicionales=False
            )
            msg_final = f"{msg_gasto}\n{aviso_personas_wpp.TEXTO_AVISO_PAGOS}"
            nueva_conv = ConversacionWpp(
                usuario_id=usuario.id,
                wamid=wamid,
                mensaje_usuario=mensaje_texto,
                tipo_mensaje=TipoMensajeWpp.TEXTO,
                transcripcion=None,
                mensaje_bot=msg_final,
                intent_detectado="transferir_fondos",
                entidades=entidades_gasto,
                accion_ejecutada=str(tr_obj.id) if tr_obj else None,
                confianza=Decimal("1.000"),
                slot_filling_activo=False,
                slot_filling_estado=None,
            )
            db.add(nueva_conv)
            db.commit()
            whatsapp_service.enviar_whatsapp(from_number, msg_final)
            return True
        return False

    # Elegir origen o destino desde menú
    if paso in ("elegir_origen", "elegir_destino"):
        cands_ids = estado.get("candidatas_ids", [])
        cands_list = [b for b in billeteras if str(b.id) in cands_ids]
        b_elegida = None

        clean_num = t_norm.strip().rstrip(".")
        if clean_num.isdigit():
            idx = int(clean_num) - 1
            if 0 <= idx < len(cands_ids):
                target_id = cands_ids[idx]
                b_elegida = next(
                    (b for b in billeteras if str(b.id) == target_id), None
                )
        else:
            st, b_res, _, _ = resolver_cuenta(
                mensaje_texto, cands_list, moneda_hint=None
            )
            if st == "ok" and b_res:
                b_elegida = b_res

        if not b_elegida:
            return False

        if conv_activa:
            conv_activa.slot_filling_activo = False
            db.flush()

        if paso == "elegir_origen":
            item["billetera_origen_obj"] = b_elegida
            item["origen"] = b_elegida.nombre
        else:
            item["billetera_destino_obj"] = b_elegida
            item["destino"] = b_elegida.nombre

        # Cuentas elegidas por la persona -> registra directo
        return _ejecutar_tabla_flujo(
            item,
            mensaje_texto,
            usuario,
            db,
            from_number,
            wamid,
            billeteras,
            origen_ia=False,
        )

    # Monto
    if paso == "monto":
        monto, _ = buscar_monto(mensaje_texto)
        if not monto or monto <= Decimal("0"):
            return False

        if conv_activa:
            conv_activa.slot_filling_activo = False
            db.flush()

        item["monto"] = monto
        m_curr = re.search(
            r"\b(?:d[oó]lares|verdes|usd)\b", normalizar_texto(mensaje_texto)
        )
        if m_curr:
            item["moneda"] = "USD"
        elif re.search(r"\b(?:pesos|mangos|ars)\b", normalizar_texto(mensaje_texto)):
            item["moneda"] = "ARS"

        return _ejecutar_tabla_flujo(
            item,
            mensaje_texto,
            usuario,
            db,
            from_number,
            wamid,
            billeteras,
            origen_ia=origen_ia,
        )

    return False
