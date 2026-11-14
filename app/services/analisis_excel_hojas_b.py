# -*- coding: utf-8 -*-
"""
Generación de hojas de cálculo estructuradas para el Excel de Análisis de Argentum (Parte B).
Contiene las hojas: Comparación y Proyección.
Ubicación: app/services/analisis_excel_hojas_b.py
"""
from __future__ import annotations

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
    FMT_PCT_VAR,
    FMT_USD,
    FMT_USD_DIF,
    FMT_USD_VAR,
    FONT_A1,
    FONT_A2,
    FONT_BASE,
    FONT_ENCABEZADO,
    FONT_NOTA_SEC,
    FONT_TITULO_SEC,
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


def armar_hoja_comparativa(
    ws: Any,
    comparativa: Dict[str, Any],
    titulo: str,
    linea_filtros: str,
    moneda_modo: str = "solo_ars",
) -> None:
    """Arma la hoja 'Comparación' rubro por rubro entre ciclo A y ciclo B."""
    _configurar_cabecera_hoja(ws, titulo, linea_filtros)
    fmt_monto, _, fmt_var = _fmt_moneda(moneda_modo)

    nom_a = mes_corto(comparativa.get("ciclo_a", {}).get("etiqueta", "Ciclo A"))
    nom_b = mes_corto(comparativa.get("ciclo_b", {}).get("etiqueta", "Ciclo B"))

    headers = [
        "Categoría",
        nom_a,
        nom_b,
        "Diferencia",
        "Diferencia %",
    ]
    ws.row_dimensions[4].height = 22
    for col_i, h_text in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_i, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_i == 1 else ALIGN_CENTER

    categorias = comparativa.get("categorias", [])
    for idx, it in enumerate(categorias):
        r = 5 + idx
        ws.row_dimensions[r].height = 18

        c1 = ws.cell(row=r, column=1, value=it.get("categoria_nombre", ""))
        c1.font = FONT_BASE
        c1.border = BORDE_DATOS
        c1.alignment = ALIGN_LEFT_INDENT

        c2 = ws.cell(row=r, column=2, value=it.get("monto_a", 0.0))
        c2.font = FONT_BASE
        c2.border = BORDE_DATOS
        c2.number_format = fmt_monto
        c2.alignment = ALIGN_RIGHT_INDENT

        c3 = ws.cell(row=r, column=3, value=it.get("monto_b", 0.0))
        c3.font = FONT_BASE
        c3.border = BORDE_DATOS
        c3.number_format = fmt_monto
        c3.alignment = ALIGN_RIGHT_INDENT

        c4 = ws.cell(row=r, column=4, value=f"=B{r}-C{r}")
        c4.font = FONT_BASE
        c4.border = BORDE_DATOS
        c4.number_format = fmt_var
        c4.alignment = ALIGN_RIGHT_INDENT

        c5 = ws.cell(row=r, column=5, value=f'=IF(C{r}=0,"",(B{r}-C{r})/C{r})')
        c5.font = FONT_BASE
        c5.border = BORDE_DATOS
        c5.number_format = FMT_PCT_VAR
        c5.alignment = ALIGN_RIGHT_INDENT

    tot_r = 5 + len(categorias)
    ws.row_dimensions[tot_r].height = 20

    t1 = ws.cell(row=tot_r, column=1, value="Total")
    t1.font = FONT_TOTAL
    t1.fill = FILL_TOTAL
    t1.border = BORDE_TOTAL
    t1.alignment = ALIGN_LEFT_INDENT

    last_r = tot_r - 1
    form_a = f"=SUM(B5:B{last_r})" if len(categorias) > 0 else 0.0
    form_b = f"=SUM(C5:C{last_r})" if len(categorias) > 0 else 0.0

    t2 = ws.cell(row=tot_r, column=2, value=form_a)
    t2.font = FONT_TOTAL
    t2.fill = FILL_TOTAL
    t2.border = BORDE_TOTAL
    t2.number_format = fmt_monto
    t2.alignment = ALIGN_RIGHT_INDENT

    t3 = ws.cell(row=tot_r, column=3, value=form_b)
    t3.font = FONT_TOTAL
    t3.fill = FILL_TOTAL
    t3.border = BORDE_TOTAL
    t3.number_format = fmt_monto
    t3.alignment = ALIGN_RIGHT_INDENT

    t4 = ws.cell(row=tot_r, column=4, value=f"=B{tot_r}-C{tot_r}")
    t4.font = FONT_TOTAL
    t4.fill = FILL_TOTAL
    t4.border = BORDE_TOTAL
    t4.number_format = fmt_var
    t4.alignment = ALIGN_RIGHT_INDENT

    t5 = ws.cell(row=tot_r, column=5, value=f'=IF(C{tot_r}=0,"",(B{tot_r}-C{tot_r})/C{tot_r})')
    t5.font = FONT_TOTAL
    t5.fill = FILL_TOTAL
    t5.border = BORDE_TOTAL
    t5.number_format = FMT_PCT_VAR
    t5.alignment = ALIGN_RIGHT_INDENT

    modo = (moneda_modo or "solo_ars").lower()
    if modo.startswith("consolidado"):
        c_nota = ws.cell(row=tot_r + 2, column=1, value="Cada movimiento se pasó con el dólar oficial del día en que se hizo.")
        c_nota.font = FONT_NOTA_SEC

    ws.freeze_panes = "A5"
    ajustar_ancho_columnas(ws)


def _escribir_tabla_planes_proyeccion(
    ws: Any,
    r_sec: int,
    planes: list[dict[str, Any]],
    fmt_monto: str,
) -> None:
    """Escribe la tabla de compras en cuotas en la hoja de Proyección."""
    c_sec = ws.cell(row=r_sec, column=1, value="Compras en cuotas")
    c_sec.font = FONT_TITULO_SEC

    r_h2 = r_sec + 1
    ws.row_dimensions[r_h2].height = 22
    headers_t2 = [
        "Compra",
        "Tarjeta",
        "Cuotas que faltan",
        "De",
        "Cuota",
        "Falta pagar",
        "Última cuota",
    ]
    for col_i, h_text in enumerate(headers_t2, start=1):
        cell = ws.cell(row=r_h2, column=col_i, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_i == 1 else ALIGN_CENTER

    for p_idx, p in enumerate(planes):
        pr = r_h2 + 1 + p_idx
        ws.row_dimensions[pr].height = 18

        p1 = ws.cell(row=pr, column=1, value=p.get("descripcion", ""))
        p1.font = FONT_BASE
        p1.border = BORDE_DATOS
        p1.alignment = ALIGN_LEFT_INDENT

        p2 = ws.cell(row=pr, column=2, value=p.get("tarjeta_nombre", ""))
        p2.font = FONT_BASE
        p2.border = BORDE_DATOS
        p2.alignment = ALIGN_LEFT_INDENT

        p3 = ws.cell(row=pr, column=3, value=p.get("cuotas_restantes", 0))
        p3.font = FONT_BASE
        p3.border = BORDE_DATOS
        p3.alignment = ALIGN_RIGHT_INDENT

        p4 = ws.cell(row=pr, column=4, value=p.get("cantidad_cuotas", 0))
        p4.font = FONT_BASE
        p4.border = BORDE_DATOS
        p4.alignment = ALIGN_RIGHT_INDENT

        p5 = ws.cell(row=pr, column=5, value=p.get("monto_cuota", 0.0))
        p5.font = FONT_BASE
        p5.border = BORDE_DATOS
        p5.number_format = fmt_monto
        p5.alignment = ALIGN_RIGHT_INDENT

        p6 = ws.cell(row=pr, column=6, value=p.get("saldo_pendiente", 0.0))
        p6.font = FONT_BASE
        p6.border = BORDE_DATOS
        p6.number_format = fmt_monto
        p6.alignment = ALIGN_RIGHT_INDENT

        p7 = ws.cell(row=pr, column=7, value=mes_corto(p.get("ultimo_mes_etiqueta", "")))
        p7.font = FONT_BASE
        p7.border = BORDE_DATOS
        p7.alignment = ALIGN_CENTER


def armar_hoja_proyeccion(
    ws: Any,
    proyeccion: Dict[str, Any],
    titulo: str,
    linea_filtros: str,
    moneda_modo: str = "solo_ars",
) -> None:
    """Arma la hoja 'Proyección' con proyección mensual y detalle de cuotas."""
    _configurar_cabecera_hoja(ws, titulo, linea_filtros)
    fmt_monto, _, _ = _fmt_moneda(moneda_modo)

    headers_t1 = [
        "Ciclo",
        "Cuotas",
        "Suscripciones",
        "Total",
        "Compras que terminan",
        "Se libera",
    ]
    ws.row_dimensions[4].height = 22
    for col_i, h_text in enumerate(headers_t1, start=1):
        cell = ws.cell(row=4, column=col_i, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_i == 1 else ALIGN_CENTER

    serie = proyeccion.get("serie_mensual", [])
    for idx, m in enumerate(serie):
        r = 5 + idx
        ws.row_dimensions[r].height = 18

        c1 = ws.cell(row=r, column=1, value=mes_corto(m.get("etiqueta", "")))
        c1.font = FONT_BASE
        c1.border = BORDE_DATOS
        c1.alignment = ALIGN_LEFT_INDENT

        c2 = ws.cell(row=r, column=2, value=m.get("monto_cuotas", 0.0))
        c2.font = FONT_BASE
        c2.border = BORDE_DATOS
        c2.number_format = fmt_monto
        c2.alignment = ALIGN_RIGHT_INDENT

        c3 = ws.cell(row=r, column=3, value=m.get("monto_suscripciones", 0.0))
        c3.font = FONT_BASE
        c3.border = BORDE_DATOS
        c3.number_format = fmt_monto
        c3.alignment = ALIGN_RIGHT_INDENT

        c4 = ws.cell(row=r, column=4, value=f"=B{r}+C{r}")
        c4.font = FONT_BASE
        c4.border = BORDE_DATOS
        c4.number_format = fmt_monto
        c4.alignment = ALIGN_RIGHT_INDENT

        c5 = ws.cell(row=r, column=5, value=m.get("planes_que_finalizan", 0))
        c5.font = FONT_BASE
        c5.border = BORDE_DATOS
        c5.alignment = ALIGN_RIGHT_INDENT

        c6 = ws.cell(row=r, column=6, value=m.get("monto_que_se_libera", 0.0))
        c6.font = FONT_BASE
        c6.border = BORDE_DATOS
        c6.number_format = fmt_monto
        c6.alignment = ALIGN_RIGHT_INDENT

    tot_r = 5 + len(serie)
    ws.row_dimensions[tot_r].height = 20

    t1 = ws.cell(row=tot_r, column=1, value="Total")
    t1.font = FONT_TOTAL
    t1.fill = FILL_TOTAL
    t1.border = BORDE_TOTAL
    t1.alignment = ALIGN_LEFT_INDENT

    last_t1 = tot_r - 1
    for col_idx, col_let in [(2, "B"), (3, "C"), (4, "D"), (6, "F")]:
        tc = ws.cell(row=tot_r, column=col_idx, value=f"=SUM({col_let}5:{col_let}{last_t1})" if serie else 0.0)
        tc.font = FONT_TOTAL
        tc.fill = FILL_TOTAL
        tc.border = BORDE_TOTAL
        tc.number_format = fmt_monto
        tc.alignment = ALIGN_RIGHT_INDENT

    t5 = ws.cell(row=tot_r, column=5, value=f"=SUM(E5:E{last_t1})" if serie else 0)
    t5.font = FONT_TOTAL
    t5.fill = FILL_TOTAL
    t5.border = BORDE_TOTAL
    t5.alignment = ALIGN_RIGHT_INDENT

    planes = proyeccion.get("planes_activos", [])
    if planes:
        _escribir_tabla_planes_proyeccion(ws, tot_r + 2, planes, fmt_monto)

    modo = (moneda_modo or "solo_ars").lower()
    if modo.startswith("consolidado"):
        c_nota = ws.cell(row=ws.max_row + 2, column=1, value="Cada movimiento se pasó con el dólar oficial del día en que se hizo.")
        c_nota.font = FONT_NOTA_SEC

    ws.freeze_panes = "A5"
    ajustar_ancho_columnas(ws)
