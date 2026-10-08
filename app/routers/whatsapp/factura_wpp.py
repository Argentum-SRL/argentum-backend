"""
app/routers/whatsapp/factura_wpp.py
Lógica de propuestas y confirmaciones de facturas con vencimiento para WhatsApp (Fase 4c2b2b).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.factura import Factura
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.db_lookups import (
    _buscar_propuesta_pendiente,
    _resolver_categoria_y_subcategoria,
)
from app.routers.whatsapp.detectors import _es_cancelacion
from app.routers.whatsapp.parsers import _nombre_corto_categoria
from app.services import whatsapp_service
from app.services.factura_service import crear_factura_pendiente
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto


def _formatear_lista_fechas(fechas_str: list[str]) -> str:
    if len(fechas_str) == 1:
        return f"el {fechas_str[0]}"
    if len(fechas_str) == 2:
        return f"el {fechas_str[0]} y el {fechas_str[1]}"
    return ", ".join(f"el {f}" for f in fechas_str[:-1]) + f" y el {fechas_str[-1]}"


def texto_propuesta_factura(
    entidades: dict[str, Any],
    billetera_nombre: str | None = None,
    se_asumio_principal: bool = False,
    billetera_moneda: Moneda | None = None,
    billeteras_usuario: list[Any] | None = None,
) -> str:
    """
    Construye el texto de propuesta para facturas con vencimiento según Decisión 2.
    """
    factura_data = entidades.get("factura") or {}
    vencimientos = factura_data.get("vencimientos") or []
    empresa = factura_data.get("empresa") or entidades.get("descripcion") or "Servicio"

    moneda_prop = (
        Moneda.USD
        if entidades.get("moneda") == "USD" or (billetera_moneda and billetera_moneda == Moneda.USD)
        else Moneda.ARS
    )

    cat_display = _nombre_corto_categoria(entidades.get("categoria"))
    en_cat = f" en {cat_display}" if cat_display else ""
    bill = (
        billetera_nombre
        or entidades.get("billetera")
        or entidades.get("billetera_origen")
        or "tu billetera"
    )
    linea_asumida = "\nSi fue con otra, decime cuál." if se_asumio_principal else ""

    hoy = hoy_argentina()

    if len(vencimientos) == 1:
        v = vencimientos[0]
        monto_val = Decimal(str(v["monto"]))
        m_str = formatear_monto(float(monto_val), moneda_prop)
        f_date = date.fromisoformat(v["fecha"])
        f_str = f_date.strftime("%d/%m")

        if f_date == hoy:
            cuando = "vence hoy"
        elif f_date > hoy:
            cuando = f"vence el {f_str}"
        else:
            cuando = f"venció el {f_str}"

        return (
            f"Factura de {empresa} por {m_str}, {cuando}.\n"
            f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy{en_cat} desde {bill}."
            f"{linea_asumida}\n"
            f"Si me decís que no, te la anoto en la web para que no se te pase."
        )

    # N cuotas (N >= 2)
    n = len(vencimientos)
    montos = [Decimal(str(v["monto"])) for v in vencimientos]
    montos_iguales = len(set(montos)) == 1

    if montos_iguales:
        m_str = formatear_monto(float(montos[0]), moneda_prop)
        fechas_str = [date.fromisoformat(v["fecha"]).strftime("%d/%m") for v in vencimientos]
        fechas_texto = _formatear_lista_fechas(fechas_str)
        linea_cuotas = f"Factura de {empresa} en {n} cuotas de {m_str}: vencen {fechas_texto}."
    else:
        items = [
            f"{formatear_monto(float(v['monto']), moneda_prop)} que vence el {date.fromisoformat(v['fecha']).strftime('%d/%m')}"
            for v in vencimientos
        ]
        if n == 2:
            cuotas_texto = f"{items[0]} y {items[1]}"
        else:
            cuotas_texto = ", ".join(items[:-1]) + f" y {items[-1]}"
        linea_cuotas = f"Factura de {empresa} en {n} cuotas: {cuotas_texto}."

    resto = "la segunda" if n == 2 else f"las otras {n - 1}"
    todas = "las dos" if n == 2 else f"las {n}"

    return (
        f"{linea_cuotas}\n"
        f"¿Ya pagaste la primera? Si me decís que sí, la anoto como gasto de hoy{en_cat} desde {bill} y te anoto {resto} en la web."
        f"{linea_asumida}\n"
        f"Si me decís que no, te anoto {todas} en la web para que no se te pasen."
    )


def registrar_cuotas_restantes(
    db: Session,
    usuario_id: UUID,
    factura_data: dict[str, Any],
    categoria_id: UUID | None,
    subcategoria_id: UUID | None,
    moneda: Moneda,
) -> str:
    """
    Crea las facturas pendientes para cuotas 2..N en el mismo commit que la primera cuota (Decisión 3).
    Retorna el texto adicional a sumar al 'Listo.'
    """
    vencimientos = factura_data.get("vencimientos") or []
    if len(vencimientos) <= 1:
        return ""

    empresa = factura_data.get("empresa") or "Factura"
    origen = factura_data.get("origen", "whatsapp_foto")

    for v in vencimientos[1:]:
        crear_factura_pendiente(
            db=db,
            usuario_id=usuario_id,
            descripcion=empresa,
            monto=Decimal(str(v["monto"])),
            moneda=moneda,
            fecha_vencimiento=date.fromisoformat(v["fecha"]),
            origen=origen,
            categoria_id=categoria_id,
            subcategoria_id=subcategoria_id,
            commit=False,
        )

    if len(vencimientos) == 2:
        m2 = formatear_monto(float(vencimientos[1]["monto"]), moneda)
        f2 = date.fromisoformat(vencimientos[1]["fecha"]).strftime("%d/%m")
        return f"\nTe anoté la segunda cuota ({m2}, vence el {f2}) en la web."
    else:
        return f"\nTe anoté las otras {len(vencimientos) - 1} cuotas en la web."


def manejar_factura_no_pagada(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Maneja el rechazo ('no') de una propuesta de factura con vencimientos (Decisión 4).
    Anota todas las cuotas como facturas pendientes en la web y cierra la propuesta.
    """
    if not _es_cancelacion(mensaje_texto):
        return False

    propuesta = _buscar_propuesta_pendiente(usuario.id, db)
    if not propuesta:
        return False

    entidades = propuesta.entidades or {}
    factura_data = entidades.get("factura")
    if not factura_data or not isinstance(factura_data, dict):
        return False

    vencimientos = factura_data.get("vencimientos") or []
    if not vencimientos:
        return False

    cat_id, subcat_id = _resolver_categoria_y_subcategoria(
        entidades.get("categoria"), usuario.id, db, tipo="egreso"
    )

    moneda_prop = Moneda.USD if entidades.get("moneda") == "USD" else Moneda.ARS
    empresa = factura_data.get("empresa") or entidades.get("descripcion") or "Factura"
    origen = factura_data.get("origen", "whatsapp_foto")

    facturas_creadas: list[Factura] = []
    for v in vencimientos:
        fac = crear_factura_pendiente(
            db=db,
            usuario_id=usuario.id,
            descripcion=empresa,
            monto=Decimal(str(v["monto"])),
            moneda=moneda_prop,
            fecha_vencimiento=date.fromisoformat(v["fecha"]),
            origen=origen,
            categoria_id=cat_id,
            subcategoria_id=subcat_id,
            commit=False,
        )
        facturas_creadas.append(fac)

    todas_ya_existian = bool(
        facturas_creadas and all(getattr(f, "ya_existia", False) for f in facturas_creadas)
    )

    if todas_ya_existian:
        msg_resp = "Esa factura ya la tenía anotada."
    elif len(vencimientos) >= 2:
        msg_resp = f"Listo, te anoto las {len(vencimientos)} cuotas en la web. Te aviso 3 días antes y el día de cada vencimiento."
    else:
        v_fecha = date.fromisoformat(vencimientos[0]["fecha"])
        if v_fecha < hoy_argentina():
            msg_resp = "Listo, te la anoto en la web como vencida."
        else:
            msg_resp = "Listo, te la anoto en la web. Te aviso 3 días antes y el día del vencimiento."

    propuesta.accion_ejecutada = "factura_pendiente"

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_resp,
        intent_detectado="cancelar",
        entidades={},
        accion_ejecutada="factura_pendiente",
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_resp)
    return True
