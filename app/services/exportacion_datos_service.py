# -*- coding: utf-8 -*-
"""
Servicio para exportar los datos del usuario en un archivo ZIP con planillas CSV.
Cumple con el derecho de portabilidad de datos personales (TG.2).
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta
from app.models.presupuesto import Presupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.suscripcion import Suscripcion
from app.models.tarjeta_credito import TarjetaCredito
from app.services import historial_service, suscripcion_service
from app.utils.cuotas import monto_efectivo_cuota
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina

logger = logging.getLogger(__name__)


def dia_ar(dt: Optional[datetime | date]) -> Optional[date]:
    """Convierte a fecha local argentina (asume UTC si no tiene zona)."""
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_ARGENTINA).date()


def formatear_monto_csv(val: Any) -> str:
    """
    Formatea un monto numérico con 2 decimales, coma decimal, signo negativo y sin separador de miles.
    Ejemplo: -25000 -> "-25000,00". Si es None o vacío, devuelve "".
    """
    if val is None or val == "":
        return ""
    d = Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d:.2f}".replace(".", ",")


def _bool_a_csv(val: Optional[bool]) -> str:
    """Convierte un booleano a 'Sí' o 'No'."""
    if val is None:
        return ""
    return "Sí" if val else "No"


def _enum_o_str(val: Any) -> str:
    """Convierte un enum o valor a string (usando .value si existe)."""
    if val is None:
        return ""
    if hasattr(val, "value"):
        return str(val.value)
    return str(val)


def _crear_csv(encabezados: list[str], filas: list[list[str]]) -> bytes:
    """Genera los bytes de un CSV en UTF-8 con BOM (utf-8-sig), delimitador ';' y fin de línea CRLF."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(encabezados)
    for fila in filas:
        writer.writerow(fila)
    return out.getvalue().encode("utf-8-sig")


def exportar_zip(db: Session, usuario_id: UUID, hoy: Optional[date] = None) -> bytes:
    """
    Genera en memoria un archivo ZIP con nueve planillas CSV conteniendo todos los datos del usuario.
    Los archivos se guardan en el orden estricto especificado por la decisión 3.a.
    """
    ref_hoy = hoy if hoy is not None else hoy_argentina()

    archivos: list[tuple[str, bytes]] = []

    # 1. movimientos.csv
    # Fecha;Tipo;Descripción;Importe;Moneda;Billetera;Cuenta como;Cuota
    mov_headers = ["Fecha", "Tipo", "Descripción", "Importe", "Moneda", "Billetera", "Cuenta como", "Cuota"]
    mov_filas: list[list[str]] = []
    skip = 0
    limit = 500
    while True:
        resp = historial_service.listar_movimientos(
            db=db,
            usuario_id=usuario_id,
            modo="general",
            skip=skip,
            limit=limit,
            hoy=ref_hoy,
        )
        for m in resp.items:
            f_str = m.fecha.strftime("%d/%m/%Y") if m.fecha else ""
            tipo_str = m.etiqueta or ""
            desc_str = m.descripcion or ""

            monto_dec = Decimal(str(m.monto))
            if m.sentido == "sale":
                monto_dec = -monto_dec
            imp_str = formatear_monto_csv(monto_dec)

            moneda_str = m.moneda or ""

            billetera_str = m.billetera_nombre or ""
            if m.billetera_destino_nombre:
                billetera_str = f"{billetera_str} → {m.billetera_destino_nombre}"

            cuenta_map = {"gasto": "Gasto", "ingreso": "Ingreso", "nada": "Nada"}
            cuenta_como_str = cuenta_map.get(m.cuenta_como, m.cuenta_como.capitalize())

            cuota_str = m.cuota_texto or ""

            mov_filas.append([
                f_str,
                tipo_str,
                desc_str,
                imp_str,
                moneda_str,
                billetera_str,
                cuenta_como_str,
                cuota_str,
            ])

        if not resp.hay_mas:
            break
        skip += limit

    archivos.append(("movimientos.csv", _crear_csv(mov_headers, mov_filas)))

    # 2. billeteras.csv
    # Nombre;Moneda;Saldo actual;Es de inversión;Estado;Fecha del saldo;Creada
    bill_headers = ["Nombre", "Moneda", "Saldo actual", "Es de inversión", "Estado", "Fecha del saldo", "Creada"]
    bill_filas: list[list[str]] = []
    billeteras = (
        db.query(Billetera)
        .filter(Billetera.usuario_id == usuario_id)
        .order_by(Billetera.fecha_creacion.asc())
        .all()
    )
    for b in billeteras:
        f_saldo = b.fecha_saldo.strftime("%d/%m/%Y") if b.fecha_saldo else ""
        d_creada = dia_ar(b.fecha_creacion)
        creada_str = d_creada.strftime("%d/%m/%Y") if d_creada else ""
        bill_filas.append([
            b.nombre or "",
            _enum_o_str(b.moneda),
            formatear_monto_csv(b.saldo_actual),
            _bool_a_csv(b.es_inversion),
            _enum_o_str(b.estado),
            f_saldo,
            creada_str,
        ])
    archivos.append(("billeteras.csv", _crear_csv(bill_headers, bill_filas)))

    # 3. tarjetas.csv
    # Nombre;Red;Billetera;Día de cierre;Día de vencimiento;Límite;Moneda;Percepción %;Estado
    tarj_headers = ["Nombre", "Red", "Billetera", "Día de cierre", "Día de vencimiento", "Límite", "Moneda", "Percepción %", "Estado"]
    tarj_filas: list[list[str]] = []
    tarjetas = (
        db.query(TarjetaCredito)
        .options(joinedload(TarjetaCredito.billetera))
        .filter(TarjetaCredito.usuario_id == usuario_id)
        .order_by(TarjetaCredito.fecha_creacion.asc())
        .all()
    )
    for t in tarjetas:
        percepcion_str = ""
        if t.percepcion_moneda_extranjera is not None:
            if t.percepcion_moneda_extranjera == int(t.percepcion_moneda_extranjera):
                percepcion_str = str(int(t.percepcion_moneda_extranjera))
            else:
                percepcion_str = str(t.percepcion_moneda_extranjera)

        tarj_filas.append([
            t.nombre or "",
            _enum_o_str(t.red),
            t.billetera.nombre if t.billetera else "",
            str(t.dia_cierre) if t.dia_cierre is not None else "",
            str(t.dia_vencimiento) if t.dia_vencimiento is not None else "",
            formatear_monto_csv(t.limite_credito) if t.limite_credito is not None else "",
            _enum_o_str(t.moneda),
            percepcion_str,
            _enum_o_str(t.estado),
        ])
    archivos.append(("tarjetas.csv", _crear_csv(tarj_headers, tarj_filas)))

    # 4. cuotas.csv
    # Compra;Tarjeta;Cuota;Monto;Vencimiento;Pagada
    cuot_headers = ["Compra", "Tarjeta", "Cuota", "Monto", "Vencimiento", "Pagada"]
    cuot_filas: list[list[str]] = []
    grupos = (
        db.query(GrupoCuotas)
        .options(joinedload(GrupoCuotas.tarjeta), joinedload(GrupoCuotas.cuotas))
        .filter(GrupoCuotas.usuario_id == usuario_id)
        .order_by(GrupoCuotas.fecha_creacion.asc())
        .all()
    )
    for g in grupos:
        tarj_nom = g.tarjeta.nombre if g.tarjeta else ""
        compra_desc = g.descripcion or ""
        # Ordenar cuotas por numero_cuota
        cuotas_ordenadas = sorted(g.cuotas, key=lambda c: c.numero_cuota)
        for c in cuotas_ordenadas:
            cuota_txt = f"{c.numero_cuota}/{g.cantidad_cuotas}"
            monto_ef = monto_efectivo_cuota(c)
            f_venc = c.fecha_vencimiento.strftime("%d/%m/%Y") if c.fecha_vencimiento else ""
            cuot_filas.append([
                compra_desc,
                tarj_nom,
                cuota_txt,
                formatear_monto_csv(monto_ef),
                f_venc,
                _bool_a_csv(c.pagada),
            ])
    archivos.append(("cuotas.csv", _crear_csv(cuot_headers, cuot_filas)))

    # 5. metas.csv
    # Nombre;Objetivo;Ahorrado;Moneda;Fecha límite;Estado
    met_headers = ["Nombre", "Objetivo", "Ahorrado", "Moneda", "Fecha límite", "Estado"]
    met_filas: list[list[str]] = []
    metas = (
        db.query(Meta)
        .filter(Meta.usuario_id == usuario_id)
        .order_by(Meta.fecha_creacion.asc())
        .all()
    )
    for meta in metas:
        f_lim = meta.fecha_limite.strftime("%d/%m/%Y") if meta.fecha_limite else ""
        ahorrado = getattr(meta, "monto_actual", getattr(meta, "monto_ahorrado", Decimal("0")))
        met_filas.append([
            meta.nombre or "",
            formatear_monto_csv(meta.monto_objetivo),
            formatear_monto_csv(ahorrado),
            _enum_o_str(meta.moneda),
            f_lim,
            _enum_o_str(meta.estado),
        ])
    archivos.append(("metas.csv", _crear_csv(met_headers, met_filas)))

    # 6. movimientos_metas.csv
    # Meta;Tipo;Monto;Moneda;Billetera;Fecha
    mm_headers = ["Meta", "Tipo", "Monto", "Moneda", "Billetera", "Fecha"]
    mm_filas: list[list[str]] = []
    movs_metas = (
        db.query(MovimientoMeta)
        .join(Meta, MovimientoMeta.meta_id == Meta.id)
        .options(joinedload(MovimientoMeta.meta), joinedload(MovimientoMeta.billetera))
        .filter(Meta.usuario_id == usuario_id)
        .order_by(MovimientoMeta.fecha.asc())
        .all()
    )
    for mm in movs_metas:
        f_mm = mm.fecha.strftime("%d/%m/%Y") if mm.fecha else ""
        meta_nom = mm.meta.nombre if mm.meta else ""
        bill_nom = mm.billetera.nombre if mm.billetera else ""
        mm_filas.append([
            meta_nom,
            _enum_o_str(mm.tipo),
            formatear_monto_csv(mm.monto),
            _enum_o_str(getattr(mm, "moneda_movimiento", getattr(mm, "moneda", None))),
            bill_nom,
            f_mm,
        ])
    archivos.append(("movimientos_metas.csv", _crear_csv(mm_headers, mm_filas)))

    # 7. presupuestos.csv
    # Nombre;Monto;Moneda;Período;Estado;Categorías
    pres_headers = ["Nombre", "Monto", "Moneda", "Período", "Estado", "Categorías"]
    pres_filas: list[list[str]] = []
    presupuestos = (
        db.query(Presupuesto)
        .options(
            joinedload(Presupuesto.categorias).joinedload(PresupuestoCategoria.categoria),
            joinedload(Presupuesto.categorias).joinedload(PresupuestoCategoria.subcategoria),
        )
        .filter(Presupuesto.usuario_id == usuario_id, Presupuesto.eliminado_en.is_(None))
        .order_by(Presupuesto.fecha_creacion.asc())
        .all()
    )
    for p in presupuestos:
        cat_strs: list[str] = []
        for pc in p.categorias:
            cat_nom = pc.categoria.nombre if pc.categoria else ""
            subcat_nom = pc.subcategoria.nombre if pc.subcategoria else None
            if cat_nom and subcat_nom:
                cat_strs.append(f"{cat_nom} > {subcat_nom}")
            elif cat_nom:
                cat_strs.append(cat_nom)
            elif subcat_nom:
                cat_strs.append(subcat_nom)
        cats_unidas = ", ".join(cat_strs)

        pres_filas.append([
            p.nombre or "",
            formatear_monto_csv(p.monto),
            _enum_o_str(p.moneda),
            _enum_o_str(p.periodo),
            _enum_o_str(p.estado),
            cats_unidas,
        ])
    archivos.append(("presupuestos.csv", _crear_csv(pres_headers, pres_filas)))

    # 8. suscripciones.csv
    # Nombre;Precio;Moneda;Frecuencia;Próximo cobro;Estado;Se cobra en
    susc_headers = ["Nombre", "Precio", "Moneda", "Frecuencia", "Próximo cobro", "Estado", "Se cobra en"]
    susc_filas: list[list[str]] = []
    suscripciones = (
        db.query(Suscripcion)
        .options(joinedload(Suscripcion.tarjeta), joinedload(Suscripcion.billetera))
        .filter(Suscripcion.usuario_id == usuario_id)
        .order_by(Suscripcion.fecha_creacion.asc())
        .all()
    )
    for s in suscripciones:
        precio_hist = suscripcion_service.obtener_precio_vigente(db, s.id, ref_hoy)
        precio_str = formatear_monto_csv(precio_hist.monto) if precio_hist else ""
        moneda_str = _enum_o_str(precio_hist.moneda) if precio_hist else ""

        se_cobra_en = s.tarjeta.nombre if s.tarjeta else (s.billetera.nombre if s.billetera else "")
        f_prox = s.proximo_cobro.strftime("%d/%m/%Y") if s.proximo_cobro else ""

        susc_filas.append([
            s.nombre or "",
            precio_str,
            moneda_str,
            _enum_o_str(s.frecuencia),
            f_prox,
            _enum_o_str(s.estado),
            se_cobra_en,
        ])
    archivos.append(("suscripciones.csv", _crear_csv(susc_headers, susc_filas)))

    # 9. mensajes_whatsapp.csv
    # Fecha y hora;Tu mensaje;Transcripción;Respuesta
    wpp_headers = ["Fecha y hora", "Tu mensaje", "Transcripción", "Respuesta"]
    wpp_filas: list[list[str]] = []
    f_lim_90 = ref_hoy - timedelta(days=90)
    convs = (
        db.query(ConversacionWpp)
        .filter(ConversacionWpp.usuario_id == usuario_id)
        .order_by(ConversacionWpp.fecha.asc())
        .all()
    )
    for c in convs:
        d_c = dia_ar(c.fecha)
        if d_c and d_c >= f_lim_90:
            dt_aware = c.fecha if c.fecha.tzinfo else c.fecha.replace(tzinfo=timezone.utc)
            dt_ar = dt_aware.astimezone(TZ_ARGENTINA)
            fh_str = dt_ar.strftime("%d/%m/%Y %H:%M")
            wpp_filas.append([
                fh_str,
                c.mensaje_usuario or "",
                c.transcripcion or "",
                c.mensaje_bot or "",
            ])
    archivos.append(("mensajes_whatsapp.csv", _crear_csv(wpp_headers, wpp_filas)))

    # Compresión ZIP en memoria
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, contenido in archivos:
            zf.writestr(nombre, contenido)

    return bio.getvalue()

