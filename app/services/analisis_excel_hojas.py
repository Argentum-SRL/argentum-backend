# -*- coding: utf-8 -*-
"""
Generación de hojas de cálculo estructuradas para el Excel de Análisis de Argentum (Parte A).
Contiene las hojas: Números principales, Evolución y Categorías.
Ubicación: app/services/analisis_excel_hojas.py
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict

from app.services.excel_estilos import (
    ALIGN_CENTER,
    ALIGN_LEFT_INDENT,
    ALIGN_RIGHT_INDENT,
    BORDE_DATOS,
    BORDE_TOTAL,
    FILL_ENCABEZADO,
    FILL_TOTAL,
    FMT_ARS,
    FMT_ARS_DIF,
    FMT_ARS_VAR,
    FMT_FECHA,
    FMT_PCT,
    FMT_USD,
    FMT_USD_DIF,
    FMT_USD_VAR,
    FONT_A1,
    FONT_A2,
    FONT_BASE,
    FONT_ENCABEZADO,
    FONT_NOTA_SEC,
    FONT_TOTAL,
    ajustar_ancho_columnas,
    configurar_pagina,
    mes_corto,
)


def _configurar_cabecera_hoja(ws: Any, titulo: str, linea_filtros: str) -> None:
    configurar_pagina(ws)
    ws.sheet_properties.tabColor = "1F3864"
    ws.row_dimensions[1].height = 28
    c_a1 = ws["A1"]
    c_a1.value = titulo
    c_a1.font = FONT_A1

    c_a2 = ws["A2"]
    c_a2.value = linea_filtros
    c_a2.font = FONT_A2


def _fmt_moneda(modo: str) -> tuple[str, str, str]:
    """Retorna (fmt_monto, fmt_dif, fmt_var) para el modo de moneda seleccionado."""
    m = (modo or "solo_ars").lower()
    if m in ("solo_usd", "consolidado_usd"):
        return FMT_USD, FMT_USD_DIF, FMT_USD_VAR
    return FMT_ARS, FMT_ARS_DIF, FMT_ARS_VAR


def armar_hoja_kpis(
    ws: Any,
    resumen: Dict[str, Any],
    titulo: str,
    linea_filtros: str,
    moneda_modo: str = "solo_ars",
) -> None:
    """Arma la hoja 'Números principales' con KPIs, Te quedó fórmula y frase resumen."""
    _configurar_cabecera_hoja(ws, titulo, linea_filtros)
    fmt_monto, fmt_dif, _ = _fmt_moneda(moneda_modo)

    ws.row_dimensions[4].height = 22
    c_h1 = ws.cell(row=4, column=1, value="Concepto")
    c_h1.font = FONT_ENCABEZADO
    c_h1.fill = FILL_ENCABEZADO
    c_h1.alignment = ALIGN_LEFT_INDENT

    c_h2 = ws.cell(row=4, column=2, value="Monto")
    c_h2.font = FONT_ENCABEZADO
    c_h2.fill = FILL_ENCABEZADO
    c_h2.alignment = ALIGN_CENTER

    kpis = resumen.get("kpis", {})
    ingresos_val = kpis.get("ingresos_totales") or 0.0
    gastos_val = kpis.get("gastos_totales") or 0.0
    prom_val = kpis.get("promedio_gasto_ciclo") or 0.0

    filas_datos = [
        (5, "Ingresos", ingresos_val, fmt_monto),
        (6, "Egresos", gastos_val, fmt_monto),
        (7, "Te quedó", "=B5-B6", fmt_dif),
        (8, "% de lo que entró", '=IF(B5>0,B7/B5,"")', FMT_PCT),
        (9, "Promedio de gastos por ciclo", prom_val, fmt_monto),
    ]

    for r_idx, lbl, val, fmt in filas_datos:
        ws.row_dimensions[r_idx].height = 18
        c1 = ws.cell(row=r_idx, column=1, value=lbl)
        c1.font = FONT_BASE
        c1.border = BORDE_DATOS
        c1.alignment = ALIGN_LEFT_INDENT

        c2 = ws.cell(row=r_idx, column=2, value=val)
        c2.font = FONT_BASE
        c2.border = BORDE_DATOS
        c2.number_format = fmt
        c2.alignment = ALIGN_RIGHT_INDENT

    frase = kpis.get("frase_resumen") or ""
    if frase:
        c_frase = ws.cell(row=11, column=1, value=frase)
        c_frase.font = FONT_NOTA_SEC

    modo = (moneda_modo or "solo_ars").lower()
    if modo.startswith("consolidado"):
        c_nota = ws.cell(row=12, column=1, value="Cada movimiento se pasó con el dólar oficial del día en que se hizo.")
        c_nota.font = FONT_NOTA_SEC

    ws.freeze_panes = "A5"
    ajustar_ancho_columnas(ws)


def _escribir_fila_total_evolucion(
    ws: Any,
    tot_r: int,
    terminadas_filas: list[int],
    fmt_monto: str,
    fmt_dif: str,
) -> None:
    """Escribe la fila de totales de ciclos terminados en la hoja de Evolución."""
    ws.row_dimensions[tot_r].height = 20

    c_t1 = ws.cell(row=tot_r, column=1, value="Total ciclos terminados")
    c_t1.font = FONT_TOTAL
    c_t1.fill = FILL_TOTAL
    c_t1.border = BORDE_TOTAL
    c_t1.alignment = ALIGN_LEFT_INDENT

    for col_v in (2, 3):
        cv = ws.cell(row=tot_r, column=col_v, value="")
        cv.font = FONT_TOTAL
        cv.fill = FILL_TOTAL
        cv.border = BORDE_TOTAL

    if terminadas_filas:
        formula_ing = f"=SUM({','.join(f'D{rf}' for rf in terminadas_filas)})"
        formula_gas = f"=SUM({','.join(f'E{rf}' for rf in terminadas_filas)})"
    else:
        formula_ing = 0.0
        formula_gas = 0.0

    c_ti = ws.cell(row=tot_r, column=4, value=formula_ing)
    c_ti.font = FONT_TOTAL
    c_ti.fill = FILL_TOTAL
    c_ti.border = BORDE_TOTAL
    c_ti.number_format = fmt_monto
    c_ti.alignment = ALIGN_RIGHT_INDENT

    c_tg = ws.cell(row=tot_r, column=5, value=formula_gas)
    c_tg.font = FONT_TOTAL
    c_tg.fill = FILL_TOTAL
    c_tg.border = BORDE_TOTAL
    c_tg.number_format = fmt_monto
    c_tg.alignment = ALIGN_RIGHT_INDENT

    c_tte = ws.cell(row=tot_r, column=6, value=f"=D{tot_r}-E{tot_r}")
    c_tte.font = FONT_TOTAL
    c_tte.fill = FILL_TOTAL
    c_tte.border = BORDE_TOTAL
    c_tte.number_format = fmt_dif
    c_tte.alignment = ALIGN_RIGHT_INDENT

    c_tpct = ws.cell(row=tot_r, column=7, value=f'=IF(D{tot_r}>0,F{tot_r}/D{tot_r},"")')
    c_tpct.font = FONT_TOTAL
    c_tpct.fill = FILL_TOTAL
    c_tpct.border = BORDE_TOTAL
    c_tpct.number_format = FMT_PCT
    c_tpct.alignment = ALIGN_RIGHT_INDENT

    c_te8 = ws.cell(row=tot_r, column=8, value="")
    c_te8.font = FONT_TOTAL
    c_te8.fill = FILL_TOTAL
    c_te8.border = BORDE_TOTAL


def armar_hoja_evolucion(
    ws: Any,
    resumen: Dict[str, Any],
    titulo: str,
    linea_filtros: str,
    moneda_modo: str = "solo_ars",
) -> None:
    """Arma la hoja 'Evolución' con serie ciclo a ciclo y fila de total terminados."""
    _configurar_cabecera_hoja(ws, titulo, linea_filtros)
    fmt_monto, fmt_dif, _ = _fmt_moneda(moneda_modo)

    headers = [
        "Ciclo",
        "Desde",
        "Hasta",
        "Ingresos",
        "Gastos",
        "Te quedó",
        "% de lo que entró",
        "Estado",
    ]
    ws.row_dimensions[4].height = 22
    for col_i, h_text in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_i, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_i == 1 else ALIGN_CENTER

    serie = resumen.get("serie_ciclos", [])
    terminadas_filas: list[int] = []

    for idx, c in enumerate(serie):
        r = 5 + idx
        ws.row_dimensions[r].height = 18

        if c.get("sin_datos"):
            estado = "Sin datos"
        elif c.get("es_actual"):
            estado = "En curso"
        else:
            estado = "Terminado"
            terminadas_filas.append(r)

        f_ini = date.fromisoformat(c["fecha_inicio"]) if isinstance(c["fecha_inicio"], str) else c["fecha_inicio"]
        f_fin = date.fromisoformat(c["fecha_fin"]) if isinstance(c["fecha_fin"], str) else c["fecha_fin"]

        c_ciclo = ws.cell(row=r, column=1, value=mes_corto(c.get("etiqueta", "")))
        c_ciclo.font = FONT_BASE
        c_ciclo.border = BORDE_DATOS
        c_ciclo.alignment = ALIGN_LEFT_INDENT

        c_desde = ws.cell(row=r, column=2, value=f_ini)
        c_desde.font = FONT_BASE
        c_desde.border = BORDE_DATOS
        c_desde.number_format = FMT_FECHA
        c_desde.alignment = ALIGN_CENTER

        c_hasta = ws.cell(row=r, column=3, value=f_fin)
        c_hasta.font = FONT_BASE
        c_hasta.border = BORDE_DATOS
        c_hasta.number_format = FMT_FECHA
        c_hasta.alignment = ALIGN_CENTER

        ing_val = c.get("ingresos") or 0.0
        c_ing = ws.cell(row=r, column=4, value=ing_val)
        c_ing.font = FONT_BASE
        c_ing.border = BORDE_DATOS
        c_ing.number_format = fmt_monto
        c_ing.alignment = ALIGN_RIGHT_INDENT

        gas_val = c.get("gastos") or 0.0
        c_gas = ws.cell(row=r, column=5, value=gas_val)
        c_gas.font = FONT_BASE
        c_gas.border = BORDE_DATOS
        c_gas.number_format = fmt_monto
        c_gas.alignment = ALIGN_RIGHT_INDENT

        c_te = ws.cell(row=r, column=6, value=f"=D{r}-E{r}")
        c_te.font = FONT_BASE
        c_te.border = BORDE_DATOS
        c_te.number_format = fmt_dif
        c_te.alignment = ALIGN_RIGHT_INDENT

        c_pct = ws.cell(row=r, column=7, value=f'=IF(D{r}>0,F{r}/D{r},"")')
        c_pct.font = FONT_BASE
        c_pct.border = BORDE_DATOS
        c_pct.number_format = FMT_PCT
        c_pct.alignment = ALIGN_RIGHT_INDENT

        c_est = ws.cell(row=r, column=8, value=estado)
        c_est.font = FONT_BASE
        c_est.border = BORDE_DATOS
        c_est.alignment = ALIGN_CENTER

    tot_r = 5 + len(serie)
    _escribir_fila_total_evolucion(ws, tot_r, terminadas_filas, fmt_monto, fmt_dif)

    modo = (moneda_modo or "solo_ars").lower()
    if modo.startswith("consolidado"):
        c_nota = ws.cell(row=tot_r + 2, column=1, value="Cada movimiento se pasó con el dólar oficial del día en que se hizo.")
        c_nota.font = FONT_NOTA_SEC

    ws.freeze_panes = "A5"
    ajustar_ancho_columnas(ws)


def armar_hoja_categorias(
    ws: Any,
    resumen: Dict[str, Any],
    titulo: str,
    linea_filtros: str,
    moneda_modo: str = "solo_ars",
) -> None:
    """Arma la hoja 'Categorías' con desglose jerárquico y fórmulas contra total."""
    _configurar_cabecera_hoja(ws, titulo, linea_filtros)
    fmt_monto, _, _ = _fmt_moneda(moneda_modo)

    headers = [
        "Categoría",
        "Subcategoría",
        "Monto",
        "% del total",
        "Movimientos",
    ]
    ws.row_dimensions[4].height = 22
    for col_i, h_text in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_i, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_i == 1 else ALIGN_CENTER

    cats = resumen.get("desglose_categorias", [])
    total_filas = sum(1 + len(c.get("subcategorias", [])) for c in cats)
    tot_row = 5 + total_filas

    r = 5
    cat_rows: list[int] = []

    for cat in cats:
        cat_rows.append(r)
        ws.row_dimensions[r].height = 18

        c1 = ws.cell(row=r, column=1, value=cat.get("categoria_nombre", ""))
        c1.font = FONT_TOTAL
        c1.border = BORDE_DATOS
        c1.alignment = ALIGN_LEFT_INDENT

        c2 = ws.cell(row=r, column=2, value="")
        c2.font = FONT_TOTAL
        c2.border = BORDE_DATOS

        c3 = ws.cell(row=r, column=3, value=cat.get("monto", 0.0))
        c3.font = FONT_TOTAL
        c3.border = BORDE_DATOS
        c3.number_format = fmt_monto
        c3.alignment = ALIGN_RIGHT_INDENT

        c4 = ws.cell(row=r, column=4, value=f'=IF(C{tot_row}=0,"",C{r}/C{tot_row})')
        c4.font = FONT_TOTAL
        c4.border = BORDE_DATOS
        c4.number_format = FMT_PCT
        c4.alignment = ALIGN_RIGHT_INDENT

        c5 = ws.cell(row=r, column=5, value=cat.get("cantidad_movimientos", 0))
        c5.font = FONT_TOTAL
        c5.border = BORDE_DATOS
        c5.alignment = ALIGN_RIGHT_INDENT

        r += 1

        for sub in cat.get("subcategorias", []):
            ws.row_dimensions[r].height = 18

            s1 = ws.cell(row=r, column=1, value="")
            s1.font = FONT_BASE
            s1.border = BORDE_DATOS

            s2 = ws.cell(row=r, column=2, value=sub.get("subcategoria_nombre", ""))
            s2.font = FONT_BASE
            s2.border = BORDE_DATOS
            s2.alignment = ALIGN_LEFT_INDENT

            s3 = ws.cell(row=r, column=3, value=sub.get("monto", 0.0))
            s3.font = FONT_BASE
            s3.border = BORDE_DATOS
            s3.number_format = fmt_monto
            s3.alignment = ALIGN_RIGHT_INDENT

            s4 = ws.cell(row=r, column=4, value=f'=IF(C{tot_row}=0,"",C{r}/C{tot_row})')
            s4.font = FONT_BASE
            s4.border = BORDE_DATOS
            s4.number_format = FMT_PCT
            s4.alignment = ALIGN_RIGHT_INDENT

            s5 = ws.cell(row=r, column=5, value=sub.get("cantidad_movimientos", 0))
            s5.font = FONT_BASE
            s5.border = BORDE_DATOS
            s5.alignment = ALIGN_RIGHT_INDENT

            r += 1

    ws.row_dimensions[tot_row].height = 20

    t1 = ws.cell(row=tot_row, column=1, value="Total")
    t1.font = FONT_TOTAL
    t1.fill = FILL_TOTAL
    t1.border = BORDE_TOTAL
    t1.alignment = ALIGN_LEFT_INDENT

    t2 = ws.cell(row=tot_row, column=2, value="")
    t2.font = FONT_TOTAL
    t2.fill = FILL_TOTAL
    t2.border = BORDE_TOTAL

    if cat_rows:
        formula_m = f"=SUM({','.join(f'C{cr}' for cr in cat_rows)})"
        formula_v = f"=SUM({','.join(f'E{cr}' for cr in cat_rows)})"
    else:
        formula_m = 0.0
        formula_v = 0

    t3 = ws.cell(row=tot_row, column=3, value=formula_m)
    t3.font = FONT_TOTAL
    t3.fill = FILL_TOTAL
    t3.border = BORDE_TOTAL
    t3.number_format = fmt_monto
    t3.alignment = ALIGN_RIGHT_INDENT

    t4 = ws.cell(row=tot_row, column=4, value=f'=IF(C{tot_row}=0,"",1)')
    t4.font = FONT_TOTAL
    t4.fill = FILL_TOTAL
    t4.border = BORDE_TOTAL
    t4.number_format = FMT_PCT
    t4.alignment = ALIGN_RIGHT_INDENT

    t5 = ws.cell(row=tot_row, column=5, value=formula_v)
    t5.font = FONT_TOTAL
    t5.fill = FILL_TOTAL
    t5.border = BORDE_TOTAL
    t5.alignment = ALIGN_RIGHT_INDENT

    modo = (moneda_modo or "solo_ars").lower()
    if modo.startswith("consolidado"):
        c_nota = ws.cell(row=tot_row + 2, column=1, value="Cada movimiento se pasó con el dólar oficial del día en que se hizo.")
        c_nota.font = FONT_NOTA_SEC

    ws.freeze_panes = "A5"
    ajustar_ancho_columnas(ws)
