from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.categoria import Categoria
from app.models.transaccion import Transaccion
from app.models.usuario import Usuario
from app.services.analisis_ciclos_service import (
    aplicar_filtros_extra,
    convertir_movimiento,
    generar_ventana_ciclos,
    obtener_mapa_cotizaciones,
)
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.utils.fecha import hoy_argentina


def _agrupar_ciclo(
    db: Session,
    usuario: Usuario,
    ciclo_meta: Dict[str, Any],
    ref_hoy: date,
    modo: str,
    billetera_id: Optional[UUID],
    tarjeta_id: Optional[UUID],
    naturaleza: Optional[str],
    solo_gastos: bool,
) -> tuple[Decimal, Decimal, Dict[str, Dict[str, Any]]]:
    c_ini: date = ciclo_meta["fecha_inicio"]
    c_fin: date = min(ciclo_meta["fecha_fin"], ref_hoy)
    if c_ini > ref_hoy:
        return Decimal("0"), Decimal("0"), {}

    cond_g = aplicar_filtros_extra(
        condicion_gasto(usuario.id, desde=c_ini, hasta=c_fin, hoy=ref_hoy),
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        naturaleza=naturaleza,
    )
    rows_cat = db.execute(
        select(
            Transaccion.fecha,
            Transaccion.moneda,
            Transaccion.categoria_id,
            Categoria.nombre.label("categoria_nombre"),
            func.coalesce(func.sum(Transaccion.monto), 0).label("total_monto"),
        )
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .where(cond_g)
        .group_by(Transaccion.fecha, Transaccion.moneda, Transaccion.categoria_id, Categoria.nombre)
    ).all()

    fechas_g = {r[0] for r in rows_cat}
    cots_g = obtener_mapa_cotizaciones(db, modo, fechas_g)

    por_cat: Dict[str, Dict[str, Any]] = {}
    gasto_total = Decimal("0")
    for f_tx, mon_tx, cat_id, cnombre, total_m in rows_cat:
        m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
        m_conv, _ = convertir_movimiento(Decimal(str(total_m or 0)), m_code, f_tx, modo, cots_g)
        if m_conv is None:
            continue

        gasto_total += m_conv
        cid = str(cat_id) if cat_id else "sin-categoria"
        c_name = cnombre or "Otros"
        if cid not in por_cat:
            por_cat[cid] = {
                "categoria_id": str(cat_id) if cat_id else None,
                "categoria_nombre": c_name,
                "monto": Decimal("0"),
            }
        por_cat[cid]["monto"] += m_conv

    ingreso_total = Decimal("0")
    if not solo_gastos:
        cond_i_base = condicion_ingreso(usuario.id, desde=c_ini, hasta=c_fin, hoy=ref_hoy)
        cond_i = (
            and_(cond_i_base, Transaccion.billetera_id == billetera_id)
            if billetera_id
            else cond_i_base
        )
        rows_i = db.execute(
            select(
                Transaccion.fecha,
                Transaccion.moneda,
                func.coalesce(func.sum(Transaccion.monto), 0),
            )
            .where(cond_i)
            .group_by(Transaccion.fecha, Transaccion.moneda)
        ).all()

        fechas_i = {r[0] for r in rows_i}
        cots_i = obtener_mapa_cotizaciones(db, modo, fechas_i)
        for f_tx, mon_tx, total_m in rows_i:
            m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
            m_conv, _ = convertir_movimiento(Decimal(str(total_m or 0)), m_code, f_tx, modo, cots_i)
            if m_conv is not None:
                ingreso_total += m_conv

    return gasto_total, ingreso_total, por_cat


def _procesar_items_comparativa(
    cats_a: Dict[str, Dict[str, Any]],
    cats_b: Dict[str, Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    todas_claves = set(cats_a.keys()) | set(cats_b.keys())
    items_raw: List[Dict[str, Any]] = []
    max_monto_cat = Decimal("0")

    for cid in todas_claves:
        info_a = cats_a.get(cid)
        info_b = cats_b.get(cid)
        info_base = info_a or info_b or {}
        nombre = info_base.get("categoria_nombre", "Otros")
        cat_id_real = info_base.get("categoria_id", cid)
        ma = info_a["monto"] if info_a else Decimal("0")
        mb = info_b["monto"] if info_b else Decimal("0")

        if ma <= Decimal("0") and mb <= Decimal("0"):
            continue

        mayor = max(ma, mb)
        if mayor > max_monto_cat:
            max_monto_cat = mayor

        diff = ma - mb
        diff_pct: Optional[float] = None
        if mb > 0:
            diff_pct = round(float((diff / mb) * Decimal("100")), 1)

        if diff > Decimal("0.01"):
            tendencia = "subio"
        elif diff < Decimal("-0.01"):
            tendencia = "bajo"
        else:
            tendencia = "igual"

        items_raw.append({
            "categoria_id": cat_id_real,
            "categoria_nombre": nombre,
            "monto_a_dec": ma,
            "monto_b_dec": mb,
            "diff_dec": diff,
            "monto_a": round(float(ma), 2),
            "monto_b": round(float(mb), 2),
            "diferencia_monto": round(float(diff), 2),
            "diferencia_pct": diff_pct,
            "tendencia": tendencia,
        })

    items_raw.sort(key=lambda x: -max(x["monto_a_dec"], x["monto_b_dec"]))

    mayor_aumento: Optional[Dict[str, Any]] = None
    mayor_ahorro: Optional[Dict[str, Any]] = None

    aumentos = [x for x in items_raw if x["diff_dec"] > 0]
    if aumentos:
        top_up = max(aumentos, key=lambda x: x["diff_dec"])
        mayor_aumento = {
            "categoria_nombre": top_up["categoria_nombre"],
            "diferencia_monto": top_up["diferencia_monto"],
            "diferencia_pct": top_up["diferencia_pct"],
        }

    ahorros = [x for x in items_raw if x["diff_dec"] < 0]
    if ahorros:
        top_down = min(ahorros, key=lambda x: x["diff_dec"])
        mayor_ahorro = {
            "categoria_nombre": top_down["categoria_nombre"],
            "diferencia_monto": abs(top_down["diferencia_monto"]),
            "diferencia_pct": abs(top_down["diferencia_pct"]) if top_down["diferencia_pct"] is not None else None,
        }

    categorias_out: List[Dict[str, Any]] = []
    for it in items_raw:
        ancho_a = (
            round(float((it["monto_a_dec"] / max_monto_cat) * Decimal("100")), 1)
            if max_monto_cat > 0
            else 0.0
        )
        ancho_b = (
            round(float((it["monto_b_dec"] / max_monto_cat) * Decimal("100")), 1)
            if max_monto_cat > 0
            else 0.0
        )
        categorias_out.append({
            "categoria_id": it["categoria_id"],
            "categoria_nombre": it["categoria_nombre"],
            "monto_a": it["monto_a"],
            "monto_b": it["monto_b"],
            "diferencia_monto": it["diferencia_monto"],
            "diferencia_pct": it["diferencia_pct"],
            "tendencia": it["tendencia"],
            "ancho_a_pct": ancho_a,
            "ancho_b_pct": ancho_b,
        })

    return categorias_out, mayor_aumento, mayor_ahorro


def obtener_comparativa_ciclos(
    db: Session,
    usuario: Usuario,
    moneda_modo: str = "solo_ars",
    ciclo_a_clave: Optional[str] = None,
    ciclo_b_clave: Optional[str] = None,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
    hoy: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Compara dos ciclos financieros devolviendo totales, diferencias y
    qué cambió entre ambos rubro por rubro.
    """
    ref_hoy = hoy or hoy_argentina()
    modo = (moneda_modo or "solo_ars").lower()
    if modo not in ("solo_ars", "solo_usd", "consolidado_ars", "consolidado_usd"):
        modo = "solo_ars"

    moneda_visual = "USD" if modo in ("solo_usd", "consolidado_usd") else "ARS"
    solo_gastos = bool(tarjeta_id)

    catalogo_ciclos = generar_ventana_ciclos(usuario, ref_hoy, 12)
    mapa_ciclos = {c["clave"]: c for c in catalogo_ciclos}

    ciclo_a = mapa_ciclos.get(ciclo_a_clave or "") or catalogo_ciclos[-1]
    ciclo_b_default = catalogo_ciclos[-2] if len(catalogo_ciclos) >= 2 else catalogo_ciclos[-1]
    ciclo_b = mapa_ciclos.get(ciclo_b_clave or "") or ciclo_b_default

    gasto_a, ingreso_a, cats_a = _agrupar_ciclo(
        db, usuario, ciclo_a, ref_hoy, modo, billetera_id, tarjeta_id, naturaleza, solo_gastos
    )
    gasto_b, ingreso_b, cats_b = _agrupar_ciclo(
        db, usuario, ciclo_b, ref_hoy, modo, billetera_id, tarjeta_id, naturaleza, solo_gastos
    )

    categorias_out, mayor_aumento, mayor_ahorro = _procesar_items_comparativa(cats_a, cats_b)

    diff_gasto_total = gasto_a - gasto_b
    diff_gasto_pct = (
        round(float((diff_gasto_total / gasto_b) * Decimal("100")), 1)
        if gasto_b > 0
        else None
    )
    diff_ingreso_total = ingreso_a - ingreso_b
    diff_ingreso_pct = (
        round(float((diff_ingreso_total / ingreso_b) * Decimal("100")), 1)
        if ingreso_b > 0 and not solo_gastos
        else None
    )

    return {
        "moneda_visual": moneda_visual,
        "ciclos_disponibles": [
            {
                "clave": c["clave"],
                "etiqueta": c["etiqueta"],
                "rango_texto": c["rango_texto"],
                "es_actual": c["es_actual"],
                "dias_transcurridos": c["dias_transcurridos"],
                "dias_totales": c["dias_totales"],
            }
            for c in reversed(catalogo_ciclos)
        ],
        "ciclo_a": {
            "clave": ciclo_a["clave"],
            "etiqueta": ciclo_a["etiqueta"],
            "rango_texto": ciclo_a["rango_texto"],
            "dias_transcurridos": ciclo_a["dias_transcurridos"],
            "dias_totales": ciclo_a["dias_totales"],
        },
        "ciclo_b": {
            "clave": ciclo_b["clave"],
            "etiqueta": ciclo_b["etiqueta"],
            "rango_texto": ciclo_b["rango_texto"],
            "dias_transcurridos": ciclo_b["dias_transcurridos"],
            "dias_totales": ciclo_b["dias_totales"],
        },
        "resumen": {
            "gasto_a": round(float(gasto_a), 2),
            "gasto_b": round(float(gasto_b), 2),
            "diferencia_gasto_monto": round(float(diff_gasto_total), 2),
            "diferencia_gasto_pct": diff_gasto_pct,
            "ingreso_a": round(float(ingreso_a), 2) if not solo_gastos else None,
            "ingreso_b": round(float(ingreso_b), 2) if not solo_gastos else None,
            "diferencia_ingreso_monto": round(float(diff_ingreso_total), 2) if not solo_gastos else None,
            "diferencia_ingreso_pct": diff_ingreso_pct,
            "balance_a": round(float(ingreso_a - gasto_a), 2) if not solo_gastos else None,
            "balance_b": round(float(ingreso_b - gasto_b), 2) if not solo_gastos else None,
            "mayor_aumento": mayor_aumento,
            "mayor_ahorro": mayor_ahorro,
        },
        "categorias": categorias_out,
    }
