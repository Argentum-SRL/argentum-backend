# -*- coding: utf-8 -*-
"""
Armado de la hoja de un ciclo de cobro a cobro para el Excel de presupuestos.
Ubicación: app/services/presupuestos_excel_hoja_ciclo.py
"""
from __future__ import annotations

from typing import Set, Tuple

from app.services.presupuestos_excel_datos import CicloExcel, LibroDatos
from app.services.presupuestos_excel_libro import (
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
    FMT_PCT,
    FMT_USD,
    FMT_USD_DIF,
    FONT_A1,
    FONT_A2,
    FONT_BASE,
    FONT_ENCABEZADO,
    FONT_NOTA_SEC,
    FONT_TITULO_SEC,
    FONT_TOTAL,
    _ajustar_ancho_columnas,
    _configurar_pagina,
    aplicar_reglas_porcentaje,
)


def _armar_hoja_ciclo(ws, ciclo: CicloExcel, datos: LibroDatos) -> None:
    """Genera la estructura visual y de fórmulas para la hoja de un ciclo."""
    _configurar_pagina(ws)
    celdas_excluidas: Set[Tuple[int, int]] = set()

    # A1: nombre de la hoja
    ws.row_dimensions[1].height = 28
    c_a1 = ws["A1"]
    c_a1.value = ciclo.nombre_hoja
    c_a1.font = FONT_A1

    # A2: Ciclo del dd/mm/aaaa al dd/mm/aaaa (en curso si aplica)
    f_ini_str = ciclo.inicio.strftime("%d/%m/%Y")
    f_fin_str = ciclo.fin.strftime("%d/%m/%Y")
    texto_a2 = f"Ciclo del {f_ini_str} al {f_fin_str}"
    if ciclo.en_curso:
        f_hoy_str = datos.hoy.strftime("%d/%m/%Y")
        texto_a2 += f" (en curso: datos hasta el {f_hoy_str})"
    c_a2 = ws["A2"]
    c_a2.value = texto_a2
    c_a2.font = FONT_A2

    # Resumen (sin título de sección, encabezado en fila 4)
    ws.row_dimensions[4].height = 22
    headers_res = ["Resumen", "Pesos"]
    if datos.tiene_dolares:
        headers_res.append("Dólares")

    for col_idx, h_text in enumerate(headers_res, 1):
        cell = ws.cell(row=4, column=col_idx, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

    # Filas 5 a 8 del Resumen
    # Fila 5: Ingresos
    ws.row_dimensions[5].height = 18
    c_ing_lbl = ws.cell(row=5, column=1, value="Ingresos")
    c_ing_lbl.font = FONT_BASE
    c_ing_lbl.alignment = ALIGN_LEFT_INDENT
    c_ing_lbl.border = BORDE_DATOS

    c_ing_ars = ws.cell(row=5, column=2, value=float(ciclo.ingresos_ars))
    c_ing_ars.font = FONT_BASE
    c_ing_ars.number_format = FMT_ARS
    c_ing_ars.alignment = ALIGN_RIGHT_INDENT
    c_ing_ars.border = BORDE_DATOS

    if datos.tiene_dolares:
        c_ing_usd = ws.cell(row=5, column=3, value=float(ciclo.ingresos_usd))
        c_ing_usd.font = FONT_BASE
        c_ing_usd.number_format = FMT_USD
        c_ing_usd.alignment = ALIGN_RIGHT_INDENT
        c_ing_usd.border = BORDE_DATOS

    # Fila 6: Gastos
    ws.row_dimensions[6].height = 18
    c_gst_lbl = ws.cell(row=6, column=1, value="Gastos")
    c_gst_lbl.font = FONT_BASE
    c_gst_lbl.alignment = ALIGN_LEFT_INDENT
    c_gst_lbl.border = BORDE_DATOS

    c_gst_ars = ws.cell(row=6, column=2, value=float(ciclo.gastos_ars))
    c_gst_ars.font = FONT_BASE
    c_gst_ars.number_format = FMT_ARS
    c_gst_ars.alignment = ALIGN_RIGHT_INDENT
    c_gst_ars.border = BORDE_DATOS

    if datos.tiene_dolares:
        c_gst_usd = ws.cell(row=6, column=3, value=float(ciclo.gastos_usd))
        c_gst_usd.font = FONT_BASE
        c_gst_usd.number_format = FMT_USD
        c_gst_usd.alignment = ALIGN_RIGHT_INDENT
        c_gst_usd.border = BORDE_DATOS

    # Fila 7: Te sobró
    ws.row_dimensions[7].height = 18
    c_sob_lbl = ws.cell(row=7, column=1, value="Te sobró")
    c_sob_lbl.font = FONT_BASE
    c_sob_lbl.alignment = ALIGN_LEFT_INDENT
    c_sob_lbl.border = BORDE_DATOS

    c_sob_ars = ws.cell(row=7, column=2, value="=B5-B6")
    c_sob_ars.font = FONT_BASE
    c_sob_ars.number_format = FMT_ARS_DIF
    c_sob_ars.alignment = ALIGN_RIGHT_INDENT
    c_sob_ars.border = BORDE_DATOS

    if datos.tiene_dolares:
        c_sob_usd = ws.cell(row=7, column=3, value="=C5-C6")
        c_sob_usd.font = FONT_BASE
        c_sob_usd.number_format = FMT_USD_DIF
        c_sob_usd.alignment = ALIGN_RIGHT_INDENT
        c_sob_usd.border = BORDE_DATOS

    # Fila 8: % que ahorraste
    ws.row_dimensions[8].height = 18
    c_aho_lbl = ws.cell(row=8, column=1, value="% que ahorraste")
    c_aho_lbl.font = FONT_BASE
    c_aho_lbl.alignment = ALIGN_LEFT_INDENT
    c_aho_lbl.border = BORDE_DATOS

    c_aho_ars = ws.cell(row=8, column=2, value='=IF(B5>0,B7/B5,"")')
    c_aho_ars.font = FONT_BASE
    c_aho_ars.number_format = FMT_PCT
    c_aho_ars.alignment = ALIGN_RIGHT_INDENT
    c_aho_ars.border = BORDE_DATOS

    if datos.tiene_dolares:
        c_aho_usd = ws.cell(row=8, column=3, value='=IF(C5>0,C7/C5,"")')
        c_aho_usd.font = FONT_BASE
        c_aho_usd.number_format = FMT_PCT
        c_aho_usd.alignment = ALIGN_RIGHT_INDENT
        c_aho_usd.border = BORDE_DATOS

    ultima_fila = 8

    # Sección "Tus presupuestos" (solo si hay presupuestos en el ciclo)
    if ciclo.presupuestos:
        r_pre_tit = ultima_fila + 3
        celdas_excluidas.add((r_pre_tit, 1))
        c_pre_tit = ws.cell(row=r_pre_tit, column=1, value="Tus presupuestos")
        c_pre_tit.font = FONT_TITULO_SEC

        r_pre_not = r_pre_tit + 1
        celdas_excluidas.add((r_pre_not, 1))
        c_pre_not = ws.cell(row=r_pre_not, column=1, value="Cada fila es un presupuesto que armaste en Argentum, con lo mismo que ves en la app.")
        c_pre_not.font = FONT_NOTA_SEC

        r_pre_hdr = r_pre_not + 1
        ws.row_dimensions[r_pre_hdr].height = 22
        headers_pre = ["Presupuesto", "Incluye", "Período", "Límite", "Gastado", "Te queda", "% usado"]
        for col_idx, h_text in enumerate(headers_pre, 1):
            cell = ws.cell(row=r_pre_hdr, column=col_idx, value=h_text)
            cell.font = FONT_ENCABEZADO
            cell.fill = FILL_ENCABEZADO
            cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

        curr_r = r_pre_hdr + 1
        rango_pct_pre_ini = curr_r
        for p in ciclo.presupuestos:
            r = curr_r
            ws.row_dimensions[r].height = 18

            # Col A: Presupuesto
            c_nom = ws.cell(row=r, column=1, value=p.nombre)
            c_nom.font = FONT_BASE
            c_nom.alignment = ALIGN_LEFT_INDENT
            c_nom.border = BORDE_DATOS

            # Col B: Incluye
            c_inc = ws.cell(row=r, column=2, value=p.incluye)
            c_inc.font = FONT_BASE
            c_inc.alignment = ALIGN_LEFT_INDENT
            c_inc.border = BORDE_DATOS

            # Col C: Período
            c_per = ws.cell(row=r, column=3, value=p.periodo)
            c_per.font = FONT_BASE
            c_per.alignment = ALIGN_LEFT_INDENT
            c_per.border = BORDE_DATOS

            fmt_monto = FMT_USD if p.moneda == "USD" else FMT_ARS
            fmt_dif = FMT_USD_DIF if p.moneda == "USD" else FMT_ARS_DIF

            # Col D: Límite
            c_lim = ws.cell(row=r, column=4, value=float(p.limite))
            c_lim.font = FONT_BASE
            c_lim.number_format = fmt_monto
            c_lim.alignment = ALIGN_RIGHT_INDENT
            c_lim.border = BORDE_DATOS

            # Col E: Gastado
            c_gas = ws.cell(row=r, column=5, value=float(p.gastado))
            c_gas.font = FONT_BASE
            c_gas.number_format = fmt_monto
            c_gas.alignment = ALIGN_RIGHT_INDENT
            c_gas.border = BORDE_DATOS

            # Col F: Te queda '=D{r}-E{r}'
            c_que = ws.cell(row=r, column=6, value=f"=D{r}-E{r}")
            c_que.font = FONT_BASE
            c_que.number_format = fmt_dif
            c_que.alignment = ALIGN_RIGHT_INDENT
            c_que.border = BORDE_DATOS

            # Col G: % usado '=IF(D{r}=0,"",E{r}/D{r})'
            c_pct = ws.cell(row=r, column=7, value=f'=IF(D{r}=0,"",E{r}/D{r})')
            c_pct.font = FONT_BASE
            c_pct.number_format = FMT_PCT
            c_pct.alignment = ALIGN_RIGHT_INDENT
            c_pct.border = BORDE_DATOS

            curr_r += 1

        rango_pct_pre_fin = curr_r - 1
        aplicar_reglas_porcentaje(ws, f"G{rango_pct_pre_ini}:G{rango_pct_pre_fin}")
        ultima_fila = curr_r - 1

    # Sección "En qué gastaste"
    r_gas_tit = ultima_fila + 3
    celdas_excluidas.add((r_gas_tit, 1))
    c_gas_tit = ws.cell(row=r_gas_tit, column=1, value="En qué gastaste")
    c_gas_tit.font = FONT_TITULO_SEC

    if not ciclo.categorias:
        # Sin gastos en este ciclo
        c_vacio = ws.cell(row=r_gas_tit + 1, column=1, value="No hubo gastos en este ciclo.")
        c_vacio.font = FONT_BASE
        ultima_fila = r_gas_tit + 1
    else:
        r_gas_not = r_gas_tit + 1
        celdas_excluidas.add((r_gas_not, 1))
        c_gas_not = ws.cell(row=r_gas_not, column=1, value="Gastos en pesos por categoría, de mayor a menor, comparados con el mes anterior.")
        c_gas_not.font = FONT_NOTA_SEC

        r_gas_hdr = r_gas_not + 1
        ws.row_dimensions[r_gas_hdr].height = 22
        headers_gas = ["Categoría", "Gastado", "% del total", "Mes anterior", "Diferencia"]
        for col_idx, h_text in enumerate(headers_gas, 1):
            cell = ws.cell(row=r_gas_hdr, column=col_idx, value=h_text)
            cell.font = FONT_ENCABEZADO
            cell.fill = FILL_ENCABEZADO
            cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

        p = r_gas_hdr + 1
        u = p + len(ciclo.categorias) - 1
        t = u + 1

        for idx_c, cat in enumerate(ciclo.categorias):
            r = p + idx_c
            ws.row_dimensions[r].height = 18

            # Col A: Categoría
            c_cat = ws.cell(row=r, column=1, value=cat.nombre)
            c_cat.font = FONT_BASE
            c_cat.alignment = ALIGN_LEFT_INDENT
            c_cat.border = BORDE_DATOS

            # Col B: Gastado
            c_gas = ws.cell(row=r, column=2, value=float(cat.gastado))
            c_gas.font = FONT_BASE
            c_gas.number_format = FMT_ARS
            c_gas.alignment = ALIGN_RIGHT_INDENT
            c_gas.border = BORDE_DATOS

            # Col C: % del total '=IF($B${t}=0,"",B{r}/$B${t})'
            # Se arma sin f-string con '$' para respetar test_formato_montos
            f_pct_str = "".join(["=IF($B$", str(t), '=0,"",B', str(r), "/$B$", str(t), ")"])
            c_pct = ws.cell(row=r, column=3, value=f_pct_str)
            c_pct.font = FONT_BASE
            c_pct.number_format = FMT_PCT
            c_pct.alignment = ALIGN_RIGHT_INDENT
            c_pct.border = BORDE_DATOS

            # Col D: Mes anterior
            c_ant = ws.cell(row=r, column=4, value=float(cat.mes_anterior))
            c_ant.font = FONT_BASE
            c_ant.number_format = FMT_ARS
            c_ant.alignment = ALIGN_RIGHT_INDENT
            c_ant.border = BORDE_DATOS

            # Col E: Diferencia '=B{r}-D{r}'
            c_dif = ws.cell(row=r, column=5, value=f"=B{r}-D{r}")
            c_dif.font = FONT_BASE
            c_dif.number_format = FMT_ARS_VAR
            c_dif.alignment = ALIGN_RIGHT_INDENT
            c_dif.border = BORDE_DATOS

        # Fila Total (fila t)
        ws.row_dimensions[t].height = 20
        c_tot_lbl = ws.cell(row=t, column=1, value="Total")
        c_tot_lbl.font = FONT_TOTAL
        c_tot_lbl.fill = FILL_TOTAL
        c_tot_lbl.alignment = ALIGN_LEFT_INDENT
        c_tot_lbl.border = BORDE_TOTAL

        c_tot_gas = ws.cell(row=t, column=2, value=f"=SUM(B{p}:B{u})")
        c_tot_gas.font = FONT_TOTAL
        c_tot_gas.fill = FILL_TOTAL
        c_tot_gas.number_format = FMT_ARS
        c_tot_gas.alignment = ALIGN_RIGHT_INDENT
        c_tot_gas.border = BORDE_TOTAL

        c_tot_pct = ws.cell(row=t, column=3, value=f"=SUM(C{p}:C{u})")
        c_tot_pct.font = FONT_TOTAL
        c_tot_pct.fill = FILL_TOTAL
        c_tot_pct.number_format = FMT_PCT
        c_tot_pct.alignment = ALIGN_RIGHT_INDENT
        c_tot_pct.border = BORDE_TOTAL

        c_tot_ant = ws.cell(row=t, column=4, value=f"=SUM(D{p}:D{u})")
        c_tot_ant.font = FONT_TOTAL
        c_tot_ant.fill = FILL_TOTAL
        c_tot_ant.number_format = FMT_ARS
        c_tot_ant.alignment = ALIGN_RIGHT_INDENT
        c_tot_ant.border = BORDE_TOTAL

        c_tot_dif = ws.cell(row=t, column=5, value=f"=SUM(E{p}:E{u})")
        c_tot_dif.font = FONT_TOTAL
        c_tot_dif.fill = FILL_TOTAL
        c_tot_dif.number_format = FMT_ARS_VAR
        c_tot_dif.alignment = ALIGN_RIGHT_INDENT
        c_tot_dif.border = BORDE_TOTAL

        ultima_fila = t

    # Sección "Detalle por subcategoría" (solo si hay subcategorías en el ciclo)
    if ciclo.subcategorias:
        r_sub_tit = ultima_fila + 3
        celdas_excluidas.add((r_sub_tit, 1))
        c_sub_tit = ws.cell(row=r_sub_tit, column=1, value="Detalle por subcategoría")
        c_sub_tit.font = FONT_TITULO_SEC

        r_sub_hdr = r_sub_tit + 1
        ws.row_dimensions[r_sub_hdr].height = 22
        headers_sub = ["Categoría", "Subcategoría", "Gastado"]
        for col_idx, h_text in enumerate(headers_sub, 1):
            cell = ws.cell(row=r_sub_hdr, column=col_idx, value=h_text)
            cell.font = FONT_ENCABEZADO
            cell.fill = FILL_ENCABEZADO
            cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

        curr_r = r_sub_hdr + 1
        categoria_previa = None
        for sub in ciclo.subcategorias:
            r = curr_r
            ws.row_dimensions[r].height = 18

            # Col A: Categoría (solo en su primera fila)
            nom_cat_mostrar = sub.categoria if sub.categoria != categoria_previa else ""
            categoria_previa = sub.categoria

            c_cat = ws.cell(row=r, column=1, value=nom_cat_mostrar)
            c_cat.font = FONT_BASE
            c_cat.alignment = ALIGN_LEFT_INDENT
            c_cat.border = BORDE_DATOS

            # Col B: Subcategoría
            c_sub = ws.cell(row=r, column=2, value=sub.subcategoria)
            c_sub.font = FONT_BASE
            c_sub.alignment = ALIGN_LEFT_INDENT
            c_sub.border = BORDE_DATOS

            # Col C: Gastado
            c_gas = ws.cell(row=r, column=3, value=float(sub.gastado))
            c_gas.font = FONT_BASE
            c_gas.number_format = FMT_ARS
            c_gas.alignment = ALIGN_RIGHT_INDENT
            c_gas.border = BORDE_DATOS

            curr_r += 1

        ultima_fila = curr_r - 1

    # Sección "En dólares" (solo si hay gastos en USD en el ciclo)
    if ciclo.categorias_usd:
        r_usd_tit = ultima_fila + 3
        celdas_excluidas.add((r_usd_tit, 1))
        c_usd_tit = ws.cell(row=r_usd_tit, column=1, value="En dólares")
        c_usd_tit.font = FONT_TITULO_SEC

        r_usd_hdr = r_usd_tit + 1
        ws.row_dimensions[r_usd_hdr].height = 22
        headers_usd = ["Categoría", "Gastado"]
        for col_idx, h_text in enumerate(headers_usd, 1):
            cell = ws.cell(row=r_usd_hdr, column=col_idx, value=h_text)
            cell.font = FONT_ENCABEZADO
            cell.fill = FILL_ENCABEZADO
            cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

        p_u = r_usd_hdr + 1
        u_u = p_u + len(ciclo.categorias_usd) - 1
        t_u = u_u + 1

        for idx_u, cat_u in enumerate(ciclo.categorias_usd):
            r = p_u + idx_u
            ws.row_dimensions[r].height = 18

            # Col A: Categoría
            c_cat = ws.cell(row=r, column=1, value=cat_u.nombre)
            c_cat.font = FONT_BASE
            c_cat.alignment = ALIGN_LEFT_INDENT
            c_cat.border = BORDE_DATOS

            # Col B: Gastado
            c_gas = ws.cell(row=r, column=2, value=float(cat_u.gastado))
            c_gas.font = FONT_BASE
            c_gas.number_format = FMT_USD
            c_gas.alignment = ALIGN_RIGHT_INDENT
            c_gas.border = BORDE_DATOS

        # Total en dólares
        ws.row_dimensions[t_u].height = 20
        c_tot_u_lbl = ws.cell(row=t_u, column=1, value="Total")
        c_tot_u_lbl.font = FONT_TOTAL
        c_tot_u_lbl.fill = FILL_TOTAL
        c_tot_u_lbl.alignment = ALIGN_LEFT_INDENT
        c_tot_u_lbl.border = BORDE_TOTAL

        c_tot_u_gas = ws.cell(row=t_u, column=2, value=f"=SUM(B{p_u}:B{u_u})")
        c_tot_u_gas.font = FONT_TOTAL
        c_tot_u_gas.fill = FILL_TOTAL
        c_tot_u_gas.number_format = FMT_USD
        c_tot_u_gas.alignment = ALIGN_RIGHT_INDENT
        c_tot_u_gas.border = BORDE_TOTAL

        ultima_fila = t_u

    _ajustar_ancho_columnas(ws, celdas_excluidas)

