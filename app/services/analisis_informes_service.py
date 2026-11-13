from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.informe_personalizado import InformePersonalizado
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.usuario import Usuario
from app.services import dolar_service
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.cuotas import monto_efectivo_cuota
from app.utils.fecha import hoy_argentina

MESES_CORTOS = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}


def _sumar_meses_calendario(anio: int, mes: int, delta_meses: int) -> tuple[int, int]:
    total = (anio * 12 + (mes - 1)) + delta_meses
    return total // 12, (total % 12) + 1


def _salto_meses_frecuencia(freq: FrecuenciaSuscripcion | str) -> int:
    val = freq.value if isinstance(freq, FrecuenciaSuscripcion) else str(freq)
    mapa = {
        "mensual": 1,
        "bimestral": 2,
        "trimestral": 3,
        "semestral": 6,
        "anual": 12,
    }
    return mapa.get(val, 1)


def obtener_proyeccion_compromisos(
    db: Session,
    usuario: Usuario,
    moneda_modo: str = "solo_ars",
    meses: int = 6,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    hoy: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Proyecta la carga futura de tarjetas, cuotas pendientes y suscripciones activas
    para los próximos ciclos financieros del usuario, identificando cuándo finalizan planes
    y cuánto flujo se libera.
    """
    ref_hoy = hoy or hoy_argentina()
    horizonte = max(3, min(12, meses))
    modo = (moneda_modo or "solo_ars").lower()
    if modo not in ("solo_ars", "solo_usd", "consolidado_ars", "consolidado_usd"):
        modo = "solo_ars"

    moneda_visual = "USD" if modo in ("solo_usd", "consolidado_usd") else "ARS"

    # Cotización oficial de hoy para proyecciones futuras
    cot_hoy_map = dolar_service.obtener_cotizaciones_por_fechas(db, "oficial", [ref_hoy])
    cot_hoy = cot_hoy_map.get(ref_hoy)
    tc_oficial_hoy = Decimal(str(cot_hoy.venta)) if (cot_hoy and cot_hoy.venta and cot_hoy.venta > 0) else Decimal("0")

    # Armado de buckets por ciclo financiero futuro
    ciclos: List[Dict[str, Any]] = []
    cursor = ref_hoy
    for _ in range(horizonte):
        ini, fin = get_ciclo_fechas(usuario, cursor)
        punto_medio = ini + (fin - ini) // 2
        mes_txt = MESES_CORTOS.get(punto_medio.month, str(punto_medio.month))
        anio_corto = str(punto_medio.year)[-2:]
        etiqueta = f"{mes_txt} {anio_corto}"
        rango_texto = f"{ini.strftime('%d/%m')} al {fin.strftime('%d/%m')}"
        clave = f"{punto_medio.year}-{punto_medio.month:02d}-{ini.isoformat()}"

        ciclos.append({
            "clave": clave,
            "etiqueta": etiqueta,
            "rango_texto": rango_texto,
            "fecha_inicio": ini,
            "fecha_fin": fin,
            "cuotas_dec": Decimal("0"),
            "suscripciones_dec": Decimal("0"),
            "cantidad_cuotas": 0,
            "planes_que_finalizan": 0,
            "liberacion_dec": Decimal("0"),
        })
        cursor = fin + timedelta(days=1)

    primer_ciclo_inicio = ciclos[0]["fecha_inicio"]
    ultimo_ciclo_fin = ciclos[-1]["fecha_fin"]

    stmt_grupos = (
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas),
            selectinload(GrupoCuotas.tarjeta),
            selectinload(GrupoCuotas.transaccion_padre),
        )
        .where(
            GrupoCuotas.usuario_id == usuario.id,
            GrupoCuotas.estado == EstadoGrupoCuotas.ACTIVO,
        )
    )
    if tarjeta_id is not None:
        stmt_grupos = stmt_grupos.where(GrupoCuotas.tarjeta_id == tarjeta_id)

    grupos = db.execute(stmt_grupos).scalars().all()

    planes_activos: List[Dict[str, Any]] = []
    total_cuotas_pendientes_dec = Decimal("0")

    for g in grupos:
        if billetera_id is not None:
            b_padre = g.transaccion_padre.billetera_id if g.transaccion_padre else None
            b_tarj = g.tarjeta.billetera_id if g.tarjeta else None
            if b_padre != billetera_id and b_tarj != billetera_id:
                continue

        mon_g = (g.moneda.value if hasattr(g.moneda, "value") else str(g.moneda)).upper()
        cuotas_pend = [c for c in g.cuotas if not c.pagada]
        if not cuotas_pend:
            continue

        cuotas_pend.sort(key=lambda c: (c.fecha_vencimiento, c.numero_cuota))
        ultima_cuota = cuotas_pend[-1]

        # Percepción de moneda extranjera de la tarjeta
        porcentaje_percep = Decimal("30.00")
        if g.tarjeta and getattr(g.tarjeta, "percepcion_moneda_extranjera", None) is not None:
            porcentaje_percep = Decimal(str(g.tarjeta.percepcion_moneda_extranjera))
        tc_tarjeta = tc_oficial_hoy * (Decimal("1") + (porcentaje_percep / Decimal("100")))

        saldo_plan_dec = Decimal("0")
        monto_cuota_ref_dec = Decimal("0")

        for c in cuotas_pend:
            m_base = monto_efectivo_cuota(c)
            if modo == "solo_ars":
                if mon_g != "ARS":
                    continue
                m_conv = m_base
            elif modo == "solo_usd":
                if mon_g != "USD":
                    continue
                m_conv = m_base
            elif modo == "consolidado_ars":
                if mon_g == "ARS":
                    m_conv = m_base
                else:
                    m_conv = m_base * tc_tarjeta
            elif modo == "consolidado_usd":
                if mon_g == "USD":
                    m_conv = m_base
                else:
                    m_conv = (m_base / tc_tarjeta) if tc_tarjeta > 0 else Decimal("0")
            else:
                m_conv = m_base

            saldo_plan_dec += m_conv
            if monto_cuota_ref_dec == Decimal("0"):
                monto_cuota_ref_dec = m_conv

            venc = c.fecha_vencimiento
            if venc < primer_ciclo_inicio:
                # Cuotas vencidas sin pagar de meses anteriores suman en el ciclo actual
                ciclos[0]["cuotas_dec"] += m_conv
                ciclos[0]["cantidad_cuotas"] += 1
                total_cuotas_pendientes_dec += m_conv
            elif primer_ciclo_inicio <= venc <= ultimo_ciclo_fin:
                for b in ciclos:
                    if b["fecha_inicio"] <= venc <= b["fecha_fin"]:
                        b["cuotas_dec"] += m_conv
                        b["cantidad_cuotas"] += 1
                        total_cuotas_pendientes_dec += m_conv
                        break

        if saldo_plan_dec <= Decimal("0"):
            continue

        venc_ult = ultima_cuota.fecha_vencimiento
        if venc_ult < primer_ciclo_inicio:
            ciclos[0]["planes_que_finalizan"] += 1
            ciclos[0]["liberacion_dec"] += monto_cuota_ref_dec
        else:
            for b in ciclos:
                if b["fecha_inicio"] <= venc_ult <= b["fecha_fin"]:
                    b["planes_que_finalizan"] += 1
                    b["liberacion_dec"] += monto_cuota_ref_dec
                    break

        cuotas_pagadas_cnt = max(0, g.cantidad_cuotas - len(cuotas_pend))
        progreso_pct = (
            round((cuotas_pagadas_cnt / g.cantidad_cuotas) * 100.0, 1)
            if g.cantidad_cuotas > 0
            else 0.0
        )

        planes_activos.append({
            "id": str(g.id),
            "descripcion": g.descripcion,
            "tarjeta_nombre": g.tarjeta.nombre if g.tarjeta else "Tarjeta",
            "cuotas_restantes": len(cuotas_pend),
            "cantidad_cuotas": g.cantidad_cuotas,
            "monto_cuota": round(float(monto_cuota_ref_dec), 2),
            "saldo_pendiente": round(float(saldo_plan_dec), 2),
            "ultimo_vencimiento": ultima_cuota.fecha_vencimiento.isoformat(),
            "ultimo_mes_etiqueta": f"{MESES_CORTOS[ultima_cuota.fecha_vencimiento.month]} {str(ultima_cuota.fecha_vencimiento.year)[-2:]}",
            "progreso_pct": progreso_pct,
        })

    planes_activos.sort(key=lambda p: -p["saldo_pendiente"])

    # ── Suscripciones activas ─────────────────────────────────────────────────
    stmt_subs = (
        select(Suscripcion)
        .options(selectinload(Suscripcion.historial))
        .where(
            Suscripcion.usuario_id == usuario.id,
            Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        )
    )
    if tarjeta_id is not None:
        stmt_subs = stmt_subs.where(Suscripcion.tarjeta_id == tarjeta_id)
    if billetera_id is not None:
        stmt_subs = stmt_subs.where(Suscripcion.billetera_id == billetera_id)

    suscripciones = db.execute(stmt_subs).scalars().all()
    carga_mensual_suscripciones_dec = Decimal("0")

    for sub in suscripciones:
        if not sub.historial:
            continue
        hist_ordenado = sorted(
            sub.historial,
            key=lambda h: (h.vigente_desde, h.fecha_creacion),
            reverse=True,
        )
        vigente = hist_ordenado[0]
        mon_s = (vigente.moneda.value if hasattr(vigente.moneda, "value") else str(vigente.moneda)).upper()
        m_base = Decimal(str(vigente.monto))

        if modo == "solo_ars":
            if mon_s != "ARS":
                continue
            m_sub_conv = m_base
        elif modo == "solo_usd":
            if mon_s != "USD":
                continue
            m_sub_conv = m_base
        elif modo == "consolidado_ars":
            if mon_s == "ARS":
                m_sub_conv = m_base
            else:
                m_sub_conv = m_base * tc_oficial_hoy
        elif modo == "consolidado_usd":
            if mon_s == "USD":
                m_sub_conv = m_base
            else:
                m_sub_conv = (m_base / tc_oficial_hoy) if tc_oficial_hoy > 0 else Decimal("0")
        else:
            m_sub_conv = m_base

        salto = _salto_meses_frecuencia(sub.frecuencia)
        carga_mensual_suscripciones_dec += m_sub_conv / Decimal(str(salto))

        d_cobro = sub.proximo_cobro
        guard = 0
        while d_cobro < primer_ciclo_inicio and guard < 120:
            y, m = _sumar_meses_calendario(d_cobro.year, d_cobro.month, salto)
            try:
                d_cobro = d_cobro.replace(year=y, month=m)
            except ValueError:
                d_cobro = date(y, m, 28)
            guard += 1

        while d_cobro <= ultimo_ciclo_fin and guard < 240:
            for b in ciclos:
                if b["fecha_inicio"] <= d_cobro <= b["fecha_fin"]:
                    b["suscripciones_dec"] += m_sub_conv
                    break
            y, m = _sumar_meses_calendario(d_cobro.year, d_cobro.month, salto)
            try:
                d_cobro = d_cobro.replace(year=y, month=m)
            except ValueError:
                d_cobro = date(y, m, 28)
            guard += 1

    serie_mensual: List[Dict[str, Any]] = []
    mes_mayor_alivio: Optional[Dict[str, Any]] = None
    max_liberacion = Decimal("0")

    for b in ciclos:
        c_dec = b["cuotas_dec"]
        s_dec = b["suscripciones_dec"]
        tot_dec = c_dec + s_dec
        lib_dec = b["liberacion_dec"]

        if lib_dec > max_liberacion:
            max_liberacion = lib_dec
            mes_mayor_alivio = {
                "etiqueta": b["etiqueta"],
                "rango_texto": b["rango_texto"],
                "monto_liberado": round(float(lib_dec), 2),
                "planes_finalizados": b["planes_que_finalizan"],
            }

        serie_mensual.append({
            "clave": b["clave"],
            "etiqueta": b["etiqueta"],
            "rango_texto": b["rango_texto"],
            "monto_cuotas": round(float(c_dec), 2),
            "monto_suscripciones": round(float(s_dec), 2),
            "monto_total": round(float(tot_dec), 2),
            "cantidad_cuotas": b["cantidad_cuotas"],
            "planes_que_finalizan": b["planes_que_finalizan"],
            "monto_que_se_libera": round(float(lib_dec), 2),
        })

    compromiso_mes_actual = serie_mensual[0]["monto_total"] if serie_mensual else 0.0

    return {
        "moneda_visual": moneda_visual,
        "horizonte_meses": horizonte,
        "cotizacion_referencia": {
            "texto": "Las cuotas en dólares están pasadas con el dólar oficial de hoy más el impuesto de la tarjeta.",
        },
        "kpis": {
            "compromiso_mes_actual": compromiso_mes_actual,
            "total_cuotas_ventana": round(float(total_cuotas_pendientes_dec), 2),
            "carga_mensual_suscripciones": round(float(carga_mensual_suscripciones_dec), 2),
            "cantidad_planes_activos": len(planes_activos),
            "mes_mayor_alivio": mes_mayor_alivio,
        },
        "serie_mensual": serie_mensual,
        "planes_activos": planes_activos[:15],
    }


def _serializar_informe(inf: InformePersonalizado) -> Dict[str, Any]:
    return {
        "id": str(inf.id),
        "nombre": inf.nombre,
        "descripcion": inf.descripcion,
        "icono": inf.icono,
        "es_favorito": bool(inf.es_favorito),
        "configuracion": inf.configuracion or {},
        "fecha_creacion": inf.fecha_creacion.isoformat() if inf.fecha_creacion else None,
        "fecha_actualizacion": inf.fecha_actualizacion.isoformat() if inf.fecha_actualizacion else None,
    }


def listar_informes_usuario(db: Session, usuario: Usuario) -> List[Dict[str, Any]]:
    rows = (
        db.execute(
            select(InformePersonalizado)
            .where(InformePersonalizado.usuario_id == usuario.id)
            .order_by(
                InformePersonalizado.es_favorito.desc(),
                InformePersonalizado.fecha_actualizacion.desc(),
            )
        )
        .scalars()
        .all()
    )
    return [_serializar_informe(r) for r in rows]


def crear_informe_usuario(
    db: Session,
    usuario: Usuario,
    nombre: str,
    descripcion: Optional[str],
    icono: str,
    es_favorito: bool,
    configuracion: Dict[str, Any],
) -> Dict[str, Any]:
    nuevo = InformePersonalizado(
        usuario_id=usuario.id,
        nombre=(nombre or "Informe sin título").strip()[:120],
        descripcion=(descripcion.strip()[:300] if descripcion else None),
        icono=(icono or "pie-chart")[:40],
        es_favorito=bool(es_favorito),
        configuracion=configuracion or {},
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return _serializar_informe(nuevo)


def obtener_informe_usuario(
    db: Session,
    usuario: Usuario,
    informe_id: UUID,
) -> Optional[Dict[str, Any]]:
    inf = (
        db.execute(
            select(InformePersonalizado).where(
                InformePersonalizado.id == informe_id,
                InformePersonalizado.usuario_id == usuario.id,
            )
        )
        .scalars()
        .first()
    )
    if not inf:
        return None
    return _serializar_informe(inf)


def actualizar_informe_usuario(
    db: Session,
    usuario: Usuario,
    informe_id: UUID,
    datos: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    inf = (
        db.execute(
            select(InformePersonalizado).where(
                InformePersonalizado.id == informe_id,
                InformePersonalizado.usuario_id == usuario.id,
            )
        )
        .scalars()
        .first()
    )
    if not inf:
        return None

    if "nombre" in datos and datos["nombre"] is not None:
        inf.nombre = str(datos["nombre"]).strip()[:120] or inf.nombre
    if "descripcion" in datos:
        inf.descripcion = str(datos["descripcion"]).strip()[:300] if datos["descripcion"] else None
    if "icono" in datos and datos["icono"]:
        inf.icono = str(datos["icono"])[:40]
    if "es_favorito" in datos and datos["es_favorito"] is not None:
        inf.es_favorito = bool(datos["es_favorito"])
    if "configuracion" in datos and isinstance(datos["configuracion"], dict):
        inf.configuracion = datos["configuracion"]

    inf.fecha_actualizacion = datetime.now(timezone.utc)
    db.commit()
    db.refresh(inf)
    return _serializar_informe(inf)


def eliminar_informe_usuario(db: Session, usuario: Usuario, informe_id: UUID) -> bool:
    inf = (
        db.execute(
            select(InformePersonalizado).where(
                InformePersonalizado.id == informe_id,
                InformePersonalizado.usuario_id == usuario.id,
            )
        )
        .scalars()
        .first()
    )
    if not inf:
        return False
    db.delete(inf)
    db.commit()
    return True


def generar_excel_analisis(
    db: Session,
    usuario: Usuario,
    moneda_modo: str = "solo_ars",
    cantidad_ciclos: int = 6,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
    titulo_informe: Optional[str] = None,
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from app.services.analisis_service import (
        obtener_movimientos_drilldown,
        obtener_resumen_analisis,
    )

    resumen = obtener_resumen_analisis(
        db=db,
        usuario=usuario,
        moneda_modo=moneda_modo,
        cantidad_ciclos=cantidad_ciclos,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        naturaleza=naturaleza,
    )
    movimientos = obtener_movimientos_drilldown(
        db=db,
        usuario=usuario,
        moneda_modo=moneda_modo,
        cantidad_ciclos=cantidad_ciclos,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        naturaleza=naturaleza,
        limit=100,
    )

    wb = Workbook()
    navy_fill = PatternFill(start_color="0D2045", end_color="0D2045", fill_type="solid")
    sub_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=14, bold=True, color="0D2045")
    bold_font = Font(name="Calibri", size=11, bold=True, color="0F172A")
    regular_font = Font(name="Calibri", size=10, color="1E293B")
    thin_border = Border(
        bottom=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
    )

    moneda_vis = resumen["moneda_visual"]
    kpis = resumen["kpis"]
    periodo_txt = resumen["periodo_seleccionado"]["etiqueta"]

    # Hoja 1: Resumen y Ciclos
    ws1 = wb.active or wb.create_sheet("Resumen y Ciclos")
    ws1.title = "Resumen y Ciclos"
    ws1["A1"] = titulo_informe or "Argentum — Tu análisis"
    ws1["A1"].font = title_font
    ws1["A2"] = f"Período: {periodo_txt} | Moneda: {moneda_vis}"
    ws1["A2"].font = regular_font

    ws1["A4"] = "Números principales"
    ws1["B4"] = "Valor"
    ws1["A4"].fill = navy_fill
    ws1["B4"].fill = navy_fill
    ws1["A4"].font = header_font
    ws1["B4"].font = header_font

    kpi_rows = [
        (f"Ingresos ({moneda_vis})", kpis["ingresos_totales"]),
        (f"Gastos ({moneda_vis})", kpis["gastos_totales"]),
        (f"Te quedó ({moneda_vis})", kpis["balance_neto"]),
        ("Te quedó (%)", kpis["tasa_ahorro_pct"]),
        (f"Promedio de gasto por ciclo ({moneda_vis})", kpis["promedio_gasto_ciclo"]),
        ("Cantidad de movimientos", kpis["cantidad_operaciones"]),
    ]
    for idx_r, (k_label, k_val) in enumerate(kpi_rows, start=5):
        ws1.cell(row=idx_r, column=1, value=k_label).font = bold_font
        c_val = ws1.cell(row=idx_r, column=2, value=k_val)
        c_val.font = regular_font
        if isinstance(k_val, float):
            c_val.number_format = "#,##0.00"

    start_ciclos = 13
    ws1.cell(row=start_ciclos - 1, column=1, value="Por ciclo").font = bold_font
    headers_ciclos = [
        "Ciclo",
        "Rango",
        f"Ingresos ({moneda_vis})",
        f"Gastos ({moneda_vis})",
        f"Te quedó ({moneda_vis})",
        "Te quedó (%)",
    ]
    for col_i, h_text in enumerate(headers_ciclos, start=1):
        cell = ws1.cell(row=start_ciclos, column=col_i, value=h_text)
        cell.fill = navy_fill
        cell.font = header_font

    for r_offset, c_item in enumerate(resumen["serie_ciclos"], start=start_ciclos + 1):
        ws1.cell(row=r_offset, column=1, value=c_item["etiqueta"]).font = regular_font
        ws1.cell(row=r_offset, column=2, value=c_item["rango_texto"]).font = regular_font
        for col_m, key_m in [(3, "ingresos"), (4, "gastos"), (5, "balance")]:
            cm = ws1.cell(row=r_offset, column=col_m, value=c_item[key_m])
            if isinstance(c_item[key_m], (int, float)):
                cm.number_format = "#,##0.00"
            cm.font = regular_font
        ws1.cell(row=r_offset, column=6, value=c_item["tasa_ahorro_pct"]).font = regular_font

    # Hoja 2: Categorías y Subcategorías
    ws2 = wb.create_sheet("Categorías y Subcategorías")
    headers_cat = [
        "Categoría",
        "Subcategoría",
        f"Monto ({moneda_vis})",
        "% de Categoría",
        "% del Total",
        "Movimientos",
    ]
    for col_i, h_text in enumerate(headers_cat, start=1):
        cell = ws2.cell(row=1, column=col_i, value=h_text)
        cell.fill = navy_fill
        cell.font = header_font

    row_cursor = 2
    for cat in resumen["desglose_categorias"]:
        c1 = ws2.cell(row=row_cursor, column=1, value=cat["categoria_nombre"])
        c2 = ws2.cell(row=row_cursor, column=2, value="(Total Categoría)")
        c3 = ws2.cell(row=row_cursor, column=3, value=cat["monto"])
        c4 = ws2.cell(row=row_cursor, column=4, value=100.0)
        c5 = ws2.cell(row=row_cursor, column=5, value=cat["porcentaje_del_total"])
        c6 = ws2.cell(row=row_cursor, column=6, value=cat["cantidad_movimientos"])
        c3.number_format = "#,##0.00"
        for cell in (c1, c2, c3, c4, c5, c6):
            cell.font = bold_font
            cell.fill = sub_fill
            cell.border = thin_border
        row_cursor += 1

        for sub in cat["subcategorias"]:
            ws2.cell(row=row_cursor, column=1, value=cat["categoria_nombre"]).font = regular_font
            ws2.cell(row=row_cursor, column=2, value=sub["subcategoria_nombre"]).font = regular_font
            cs_m = ws2.cell(row=row_cursor, column=3, value=sub["monto"])
            cs_m.number_format = "#,##0.00"
            cs_m.font = regular_font
            ws2.cell(row=row_cursor, column=4, value=sub["porcentaje_categoria"]).font = regular_font
            ws2.cell(row=row_cursor, column=5, value=sub["porcentaje_total"]).font = regular_font
            ws2.cell(row=row_cursor, column=6, value=sub["cantidad_movimientos"]).font = regular_font
            row_cursor += 1

    # Hoja 3: Detalle de Movimientos
    ws3 = wb.create_sheet("Movimientos")
    headers_mov = [
        "Fecha",
        "Descripción",
        "Categoría",
        "Subcategoría",
        "Billetera / Medio",
        "Moneda",
        "Monto",
    ]
    for col_i, h_text in enumerate(headers_mov, start=1):
        cell = ws3.cell(row=1, column=col_i, value=h_text)
        cell.fill = navy_fill
        cell.font = header_font

    for idx_m, mov in enumerate(movimientos, start=2):
        medio = mov["billetera_nombre"]
        if mov.get("tarjeta_nombre"):
            medio = f"{medio} ({mov['tarjeta_nombre']})"
        ws3.cell(row=idx_m, column=1, value=mov["fecha"]).font = regular_font
        ws3.cell(row=idx_m, column=2, value=mov["descripcion"]).font = regular_font
        ws3.cell(row=idx_m, column=3, value=mov["categoria_nombre"]).font = regular_font
        ws3.cell(row=idx_m, column=4, value=mov.get("subcategoria_nombre") or "-").font = regular_font
        ws3.cell(row=idx_m, column=5, value=medio).font = regular_font
        ws3.cell(row=idx_m, column=6, value=mov["moneda"]).font = regular_font
        cm = ws3.cell(row=idx_m, column=7, value=mov["monto"])
        cm.number_format = "#,##0.00"
        cm.font = regular_font

    for ws in (ws1, ws2, ws3):
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=12)
            col_idx = int(col[0].column or 1)
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 14), 44)
            for cell in col:
                if cell.row == 1 and ws != ws1:
                    cell.alignment = Alignment(vertical="center")

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
