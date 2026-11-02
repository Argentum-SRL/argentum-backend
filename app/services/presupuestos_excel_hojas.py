# -*- coding: utf-8 -*-
"""
Armado de las hojas Comparativo, Anual y Cómo leerlo para el Excel de presupuestos.
Ubicación: app/services/presupuestos_excel_hojas.py
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Dict, List, Set, Tuple

from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from app.services.presupuestos_excel_datos import LibroDatos
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
    FMT_PCT_VAR,
    FMT_USD,
    FMT_USD_VAR,
    FONT_A1,
    FONT_A2,
    FONT_BASE,
    FONT_ENCABEZADO,
    FONT_NOTA_SEC,
    FONT_TITULO_SEC,
    FONT_TOTAL,
    _ajustar_ancho_columnas,
    _configurar_pagina,
    anio_de_hoja,
    aplicar_reglas_porcentaje,
    mes_corto,
)

LINEAS_COMO_LEERLO = (
    "Comparativo: cada columna es un ciclo tuyo, de cobro a cobro. Arriba, cuánto entró, cuánto salió y cuánto te sobró; abajo, cuánto gastaste en cada categoría (el celeste más fuerte marca el mes en que más gastaste) y qué parte de cada presupuesto usaste.",
    "Anual: lo mismo sumado por año, con la diferencia contra el año anterior. Fijate en «Meses con datos»: un año con pocos meses suma menos.",
    "Una hoja por mes: el resumen del ciclo, tus presupuestos tal como los armaste en Argentum, en qué gastaste por categoría comparado con el mes anterior y el detalle por subcategoría.",
    "Qué cuenta como gasto: lo que pagaste con tus cuentas y tarjetas. No cuentan el pago del resumen de la tarjeta, las transferencias entre tus cuentas, lo que pasa a tus ahorros, metas o inversiones, ni la plata que solo pasó por tu cuenta. Los reintegros ya están descontados.",
    "Compras con tarjeta: en un pago cuentan el día de la compra; en cuotas, cada cuota cuenta en el mes en que vence.",
    "Pesos y dólares van siempre separados: los dólares tienen su propia tabla.",
    "Los totales, las diferencias y los porcentajes son fórmulas: si cambiás un número, se recalculan solos.",
    "Solo aparecen los meses con movimientos. Los números son los mismos que ves en Argentum.",
)


def _armar_hoja_comparativo(ws, datos: LibroDatos) -> None:
    """Arma la hoja 'Comparativo'."""
    ws.sheet_properties.tabColor = "1F3864"
    _configurar_pagina(ws)
    celdas_excluidas: Set[Tuple[int, int]] = set()

    # A1 y A2
    ws.row_dimensions[1].height = 28
    c_a1 = ws["A1"]
    c_a1.value = "Comparativo por mes"
    c_a1.font = FONT_A1

    ini_fmt = datos.ciclos[0].inicio.strftime("%d/%m/%Y")
    fin_fmt = datos.ciclos[-1].fin.strftime("%d/%m/%Y")
    c_a2 = ws["A2"]
    c_a2.value = f"Del {ini_fmt} al {fin_fmt}. Montos en pesos. Cada columna es un ciclo de cobro a cobro."
    c_a2.font = FONT_A2

    # Columnas de ciclos
    first_col_idx = 2
    last_col_idx = 1 + len(datos.ciclos)
    tot_col_idx = last_col_idx + 1
    prom_col_idx = last_col_idx + 2
    max_col_idx = last_col_idx + 3

    first_col = get_column_letter(first_col_idx)
    last_col = get_column_letter(last_col_idx)
    tot_col = get_column_letter(tot_col_idx)

    # Categorías ARS ordenadas por gasto total descendente, luego nombre
    totales_cat_ars: Dict[str, Decimal] = {}
    for c in datos.ciclos:
        for cat_nom, gastado in c.gasto_ars_por_categoria.items():
            totales_cat_ars[cat_nom] = totales_cat_ars.get(cat_nom, Decimal("0")) + gastado

    categorias_ars_ordenadas = sorted(
        totales_cat_ars.keys(),
        key=lambda nom: (-totales_cat_ars[nom], nom),
    )

    p_cat = 13
    u_cat = p_cat + len(categorias_ars_ordenadas) - 1
    t_gastos = u_cat + 1

    # Encabezado Resumen (fila 4)
    ws.row_dimensions[4].height = 22
    headers_res = ["Resumen"] + [mes_corto(c.nombre_hoja) for c in datos.ciclos] + ["Total", "Promedio"]
    for col_idx, h_text in enumerate(headers_res, 1):
        cell = ws.cell(row=4, column=col_idx, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

    # Fila 5: Ingresos
    ws.row_dimensions[5].height = 18
    c_ing_lbl = ws.cell(row=5, column=1, value="Ingresos")
    c_ing_lbl.font = FONT_BASE
    c_ing_lbl.alignment = ALIGN_LEFT_INDENT
    c_ing_lbl.border = BORDE_DATOS

    for idx_c, c in enumerate(datos.ciclos):
        col_idx = first_col_idx + idx_c
        cell = ws.cell(row=5, column=col_idx, value=float(c.ingresos_ars))
        cell.font = FONT_BASE
        cell.number_format = FMT_ARS
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    c_ing_tot = ws.cell(row=5, column=tot_col_idx, value=f"=SUM({first_col}5:{last_col}5)")
    c_ing_tot.font = FONT_BASE
    c_ing_tot.number_format = FMT_ARS
    c_ing_tot.alignment = ALIGN_RIGHT_INDENT
    c_ing_tot.border = BORDE_DATOS

    c_ing_prom = ws.cell(row=5, column=prom_col_idx, value=f"=AVERAGE({first_col}5:{last_col}5)")
    c_ing_prom.font = FONT_BASE
    c_ing_prom.number_format = FMT_ARS
    c_ing_prom.alignment = ALIGN_RIGHT_INDENT
    c_ing_prom.border = BORDE_DATOS

    # Fila 6: Gastos '=X{t}'
    ws.row_dimensions[6].height = 18
    c_gst_lbl = ws.cell(row=6, column=1, value="Gastos")
    c_gst_lbl.font = FONT_BASE
    c_gst_lbl.alignment = ALIGN_LEFT_INDENT
    c_gst_lbl.border = BORDE_DATOS

    for idx_c in range(len(datos.ciclos)):
        col_l = get_column_letter(first_col_idx + idx_c)
        cell = ws.cell(row=6, column=first_col_idx + idx_c, value=f"={col_l}{t_gastos}")
        cell.font = FONT_BASE
        cell.number_format = FMT_ARS
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    c_gst_tot = ws.cell(row=6, column=tot_col_idx, value=f"=SUM({first_col}6:{last_col}6)")
    c_gst_tot.font = FONT_BASE
    c_gst_tot.number_format = FMT_ARS
    c_gst_tot.alignment = ALIGN_RIGHT_INDENT
    c_gst_tot.border = BORDE_DATOS

    c_gst_prom = ws.cell(row=6, column=prom_col_idx, value=f"=AVERAGE({first_col}6:{last_col}6)")
    c_gst_prom.font = FONT_BASE
    c_gst_prom.number_format = FMT_ARS
    c_gst_prom.alignment = ALIGN_RIGHT_INDENT
    c_gst_prom.border = BORDE_DATOS

    # Fila 7: Te sobró '=X5-X6'
    ws.row_dimensions[7].height = 18
    c_sob_lbl = ws.cell(row=7, column=1, value="Te sobró")
    c_sob_lbl.font = FONT_BASE
    c_sob_lbl.alignment = ALIGN_LEFT_INDENT
    c_sob_lbl.border = BORDE_DATOS

    for idx_c in range(len(datos.ciclos)):
        col_l = get_column_letter(first_col_idx + idx_c)
        cell = ws.cell(row=7, column=first_col_idx + idx_c, value=f"={col_l}5-{col_l}6")
        cell.font = FONT_BASE
        cell.number_format = FMT_ARS_DIF
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    c_sob_tot = ws.cell(row=7, column=tot_col_idx, value=f"=SUM({first_col}7:{last_col}7)")
    c_sob_tot.font = FONT_BASE
    c_sob_tot.number_format = FMT_ARS_DIF
    c_sob_tot.alignment = ALIGN_RIGHT_INDENT
    c_sob_tot.border = BORDE_DATOS

    c_sob_prom = ws.cell(row=7, column=prom_col_idx, value=f"=AVERAGE({first_col}7:{last_col}7)")
    c_sob_prom.font = FONT_BASE
    c_sob_prom.number_format = FMT_ARS_DIF
    c_sob_prom.alignment = ALIGN_RIGHT_INDENT
    c_sob_prom.border = BORDE_DATOS

    # Fila 8: % que ahorraste
    ws.row_dimensions[8].height = 18
    c_aho_lbl = ws.cell(row=8, column=1, value="% que ahorraste")
    c_aho_lbl.font = FONT_BASE
    c_aho_lbl.alignment = ALIGN_LEFT_INDENT
    c_aho_lbl.border = BORDE_DATOS

    for idx_c in range(len(datos.ciclos)):
        col_l = get_column_letter(first_col_idx + idx_c)
        cell = ws.cell(row=8, column=first_col_idx + idx_c, value=f'=IF({col_l}5>0,{col_l}7/{col_l}5,"")')
        cell.font = FONT_BASE
        cell.number_format = FMT_PCT
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    c_aho_tot = ws.cell(row=8, column=tot_col_idx, value=f'=IF({tot_col}5>0,{tot_col}7/{tot_col}5,"")')
    c_aho_tot.font = FONT_BASE
    c_aho_tot.number_format = FMT_PCT
    c_aho_tot.alignment = ALIGN_RIGHT_INDENT
    c_aho_tot.border = BORDE_DATOS

    # Promedio en fila 8 vacío
    c_aho_prom = ws.cell(row=8, column=prom_col_idx, value=None)
    c_aho_prom.font = FONT_BASE
    c_aho_prom.border = BORDE_DATOS

    # Sección "Gastos por categoría" (empieza en la fila 11)
    celdas_excluidas.add((11, 1))
    c_cat_tit = ws.cell(row=11, column=1, value="Gastos por categoría")
    c_cat_tit.font = FONT_TITULO_SEC

    ws.row_dimensions[12].height = 22
    headers_cat = ["Categoría"] + [mes_corto(c.nombre_hoja) for c in datos.ciclos] + ["Total", "Promedio", "Mes de mayor gasto"]
    for col_idx, h_text in enumerate(headers_cat, 1):
        cell = ws.cell(row=12, column=col_idx, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

    # Referencia al encabezado armada sin f-string con '$'
    ref_hdr = "".join(["$", first_col, "$12:$", last_col, "$12"])
    rule_cs = ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="9BC2E6")

    for idx_cat, cat_nom in enumerate(categorias_ars_ordenadas):
        r = p_cat + idx_cat
        ws.row_dimensions[r].height = 18

        c_nom = ws.cell(row=r, column=1, value=cat_nom)
        c_nom.font = FONT_BASE
        c_nom.alignment = ALIGN_LEFT_INDENT
        c_nom.border = BORDE_DATOS

        for idx_c, c in enumerate(datos.ciclos):
            col_idx = first_col_idx + idx_c
            val = float(c.gasto_ars_por_categoria.get(cat_nom, Decimal("0")))
            cell = ws.cell(row=r, column=col_idx, value=val)
            cell.font = FONT_BASE
            cell.number_format = FMT_ARS
            cell.alignment = ALIGN_RIGHT_INDENT
            cell.border = BORDE_DATOS

        c_tot = ws.cell(row=r, column=tot_col_idx, value=f"=SUM({first_col}{r}:{last_col}{r})")
        c_tot.font = FONT_BASE
        c_tot.number_format = FMT_ARS
        c_tot.alignment = ALIGN_RIGHT_INDENT
        c_tot.border = BORDE_DATOS

        c_prom = ws.cell(row=r, column=prom_col_idx, value=f"=AVERAGE({first_col}{r}:{last_col}{r})")
        c_prom.font = FONT_BASE
        c_prom.number_format = FMT_ARS
        c_prom.alignment = ALIGN_RIGHT_INDENT
        c_prom.border = BORDE_DATOS

        rango_fila = f"{first_col}{r}:{last_col}{r}"
        f_max = f'=IF(MAX({rango_fila})<=0,"-",INDEX(' + ref_hdr + f',MATCH(MAX({rango_fila}),{rango_fila},0)))'
        c_max = ws.cell(row=r, column=max_col_idx, value=f_max)
        c_max.font = FONT_BASE
        c_max.alignment = ALIGN_CENTER
        c_max.border = BORDE_DATOS

        # Escala de color por fila de categoría
        ws.conditional_formatting.add(rango_fila, rule_cs)

    # Fila "Total de gastos"
    ws.row_dimensions[t_gastos].height = 20
    c_tot_lbl = ws.cell(row=t_gastos, column=1, value="Total de gastos")
    c_tot_lbl.font = FONT_TOTAL
    c_tot_lbl.fill = FILL_TOTAL
    c_tot_lbl.alignment = ALIGN_LEFT_INDENT
    c_tot_lbl.border = BORDE_TOTAL

    for idx_c in range(len(datos.ciclos)):
        col_l = get_column_letter(first_col_idx + idx_c)
        cell = ws.cell(row=t_gastos, column=first_col_idx + idx_c, value=f"=SUM({col_l}{p_cat}:{col_l}{u_cat})")
        cell.font = FONT_TOTAL
        cell.fill = FILL_TOTAL
        cell.number_format = FMT_ARS
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_TOTAL

    c_tot_t = ws.cell(row=t_gastos, column=tot_col_idx, value=f"=SUM({first_col}{t_gastos}:{last_col}{t_gastos})")
    c_tot_t.font = FONT_TOTAL
    c_tot_t.fill = FILL_TOTAL
    c_tot_t.number_format = FMT_ARS
    c_tot_t.alignment = ALIGN_RIGHT_INDENT
    c_tot_t.border = BORDE_TOTAL

    c_prom_t = ws.cell(row=t_gastos, column=prom_col_idx, value=f"=AVERAGE({first_col}{t_gastos}:{last_col}{t_gastos})")
    c_prom_t.font = FONT_TOTAL
    c_prom_t.fill = FILL_TOTAL
    c_prom_t.number_format = FMT_ARS
    c_prom_t.alignment = ALIGN_RIGHT_INDENT
    c_prom_t.border = BORDE_TOTAL

    rango_tot = f"{first_col}{t_gastos}:{last_col}{t_gastos}"
    f_max_t = f'=IF(MAX({rango_tot})<=0,"-",INDEX(' + ref_hdr + f',MATCH(MAX({rango_tot}),{rango_tot},0)))'
    c_max_t = ws.cell(row=t_gastos, column=max_col_idx, value=f_max_t)
    c_max_t.font = FONT_TOTAL
    c_max_t.fill = FILL_TOTAL
    c_max_t.alignment = ALIGN_CENTER
    c_max_t.border = BORDE_TOTAL

    ultima_fila = t_gastos

    # Sección "Presupuestos de cobro a cobro" (solo si hay presupuestos de cobro a cobro)
    if datos.uso_mensual:
        r_pre_tit = ultima_fila + 3
        celdas_excluidas.add((r_pre_tit, 1))
        c_pre_tit = ws.cell(row=r_pre_tit, column=1, value="Presupuestos de cobro a cobro")
        c_pre_tit.font = FONT_TITULO_SEC

        r_pre_not = r_pre_tit + 1
        celdas_excluidas.add((r_pre_not, 1))
        c_pre_not = ws.cell(row=r_pre_not, column=1, value="Qué parte de cada presupuesto usaste en cada mes. Más de 100% es que te pasaste.")
        c_pre_not.font = FONT_NOTA_SEC

        r_pre_hdr = r_pre_not + 1
        ws.row_dimensions[r_pre_hdr].height = 22
        headers_pre = ["Presupuesto"] + [mes_corto(c.nombre_hoja) for c in datos.ciclos] + ["Veces que te pasaste"]
        for col_idx, h_text in enumerate(headers_pre, 1):
            cell = ws.cell(row=r_pre_hdr, column=col_idx, value=h_text)
            cell.font = FONT_ENCABEZADO
            cell.fill = FILL_ENCABEZADO
            cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

        curr_r = r_pre_hdr + 1
        for p_nom, vals in datos.uso_mensual:
            r = curr_r
            ws.row_dimensions[r].height = 18

            c_nom = ws.cell(row=r, column=1, value=p_nom)
            c_nom.font = FONT_BASE
            c_nom.alignment = ALIGN_LEFT_INDENT
            c_nom.border = BORDE_DATOS

            for idx_c in range(len(datos.ciclos)):
                val = vals[idx_c]
                col_idx = first_col_idx + idx_c
                if val is not None:
                    cell = ws.cell(row=r, column=col_idx, value=float(val))
                    cell.font = FONT_BASE
                    cell.number_format = FMT_PCT
                    cell.alignment = ALIGN_RIGHT_INDENT
                    cell.border = BORDE_DATOS
                else:
                    cell = ws.cell(row=r, column=col_idx, value=None)
                    cell.font = FONT_BASE
                    cell.border = BORDE_DATOS

            # Última columna: '=COUNTIF(B{r}:Y{r},">1")'
            c_cnt = ws.cell(row=r, column=tot_col_idx, value=f'=COUNTIF({first_col}{r}:{last_col}{r},">1")')
            c_cnt.font = FONT_BASE
            c_cnt.alignment = ALIGN_RIGHT_INDENT
            c_cnt.border = BORDE_DATOS

            aplicar_reglas_porcentaje(ws, f"{first_col}{r}:{last_col}{r}")
            curr_r += 1

        ultima_fila = curr_r - 1

    # Sección "En dólares" (solo si el libro tiene dólares)
    if datos.tiene_dolares:
        r_usd_tit = ultima_fila + 3
        celdas_excluidas.add((r_usd_tit, 1))
        c_usd_tit = ws.cell(row=r_usd_tit, column=1, value="En dólares")
        c_usd_tit.font = FONT_TITULO_SEC

        r_usd_hdr = r_usd_tit + 1
        ws.row_dimensions[r_usd_hdr].height = 22
        headers_usd = ["Categoría"] + [mes_corto(c.nombre_hoja) for c in datos.ciclos] + ["Total", "Promedio", "Mes de mayor gasto"]
        for col_idx, h_text in enumerate(headers_usd, 1):
            cell = ws.cell(row=r_usd_hdr, column=col_idx, value=h_text)
            cell.font = FONT_ENCABEZADO
            cell.fill = FILL_ENCABEZADO
            cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

        totales_cat_usd: Dict[str, Decimal] = {}
        for c in datos.ciclos:
            for cat_nom, gastado in c.gasto_usd_por_categoria.items():
                totales_cat_usd[cat_nom] = totales_cat_usd.get(cat_nom, Decimal("0")) + gastado

        categorias_usd_ordenadas = sorted(
            totales_cat_usd.keys(),
            key=lambda nom: (-totales_cat_usd[nom], nom),
        )

        p_u = r_usd_hdr + 1
        u_u = p_u + len(categorias_usd_ordenadas) - 1
        t_u = u_u + 1
        ref_hdr_usd = "".join(["$", first_col, "$", str(r_usd_hdr), ":$", last_col, "$", str(r_usd_hdr)])

        for idx_u, cat_nom in enumerate(categorias_usd_ordenadas):
            r = p_u + idx_u
            ws.row_dimensions[r].height = 18

            c_nom = ws.cell(row=r, column=1, value=cat_nom)
            c_nom.font = FONT_BASE
            c_nom.alignment = ALIGN_LEFT_INDENT
            c_nom.border = BORDE_DATOS

            for idx_c, c in enumerate(datos.ciclos):
                col_idx = first_col_idx + idx_c
                val = float(c.gasto_usd_por_categoria.get(cat_nom, Decimal("0")))
                cell = ws.cell(row=r, column=col_idx, value=val)
                cell.font = FONT_BASE
                cell.number_format = FMT_USD
                cell.alignment = ALIGN_RIGHT_INDENT
                cell.border = BORDE_DATOS

            c_tot = ws.cell(row=r, column=tot_col_idx, value=f"=SUM({first_col}{r}:{last_col}{r})")
            c_tot.font = FONT_BASE
            c_tot.number_format = FMT_USD
            c_tot.alignment = ALIGN_RIGHT_INDENT
            c_tot.border = BORDE_DATOS

            c_prom = ws.cell(row=r, column=prom_col_idx, value=f"=AVERAGE({first_col}{r}:{last_col}{r})")
            c_prom.font = FONT_BASE
            c_prom.number_format = FMT_USD
            c_prom.alignment = ALIGN_RIGHT_INDENT
            c_prom.border = BORDE_DATOS

            rango_fila = f"{first_col}{r}:{last_col}{r}"
            f_max_usd = f'=IF(MAX({rango_fila})<=0,"-",INDEX(' + ref_hdr_usd + f',MATCH(MAX({rango_fila}),{rango_fila},0)))'
            c_max = ws.cell(row=r, column=max_col_idx, value=f_max_usd)
            c_max.font = FONT_BASE
            c_max.alignment = ALIGN_CENTER
            c_max.border = BORDE_DATOS

            ws.conditional_formatting.add(rango_fila, rule_cs)

        # Fila Total de gastos USD
        ws.row_dimensions[t_u].height = 20
        c_tot_u_lbl = ws.cell(row=t_u, column=1, value="Total de gastos")
        c_tot_u_lbl.font = FONT_TOTAL
        c_tot_u_lbl.fill = FILL_TOTAL
        c_tot_u_lbl.alignment = ALIGN_LEFT_INDENT
        c_tot_u_lbl.border = BORDE_TOTAL

        for idx_c in range(len(datos.ciclos)):
            col_l = get_column_letter(first_col_idx + idx_c)
            cell = ws.cell(row=t_u, column=first_col_idx + idx_c, value=f"=SUM({col_l}{p_u}:{col_l}{u_u})")
            cell.font = FONT_TOTAL
            cell.fill = FILL_TOTAL
            cell.number_format = FMT_USD
            cell.alignment = ALIGN_RIGHT_INDENT
            cell.border = BORDE_TOTAL

        c_tot_u_t = ws.cell(row=t_u, column=tot_col_idx, value=f"=SUM({first_col}{t_u}:{last_col}{t_u})")
        c_tot_u_t.font = FONT_TOTAL
        c_tot_u_t.fill = FILL_TOTAL
        c_tot_u_t.number_format = FMT_USD
        c_tot_u_t.alignment = ALIGN_RIGHT_INDENT
        c_tot_u_t.border = BORDE_TOTAL

        c_prom_u_t = ws.cell(row=t_u, column=prom_col_idx, value=f"=AVERAGE({first_col}{t_u}:{last_col}{t_u})")
        c_prom_u_t.font = FONT_TOTAL
        c_prom_u_t.fill = FILL_TOTAL
        c_prom_u_t.number_format = FMT_USD
        c_prom_u_t.alignment = ALIGN_RIGHT_INDENT
        c_prom_u_t.border = BORDE_TOTAL

        rango_tot_u = f"{first_col}{t_u}:{last_col}{t_u}"
        f_max_tot_u = f'=IF(MAX({rango_tot_u})<=0,"-",INDEX(' + ref_hdr_usd + f',MATCH(MAX({rango_tot_u}),{rango_tot_u},0)))'
        c_max_u_t = ws.cell(row=t_u, column=max_col_idx, value=f_max_tot_u)
        c_max_u_t.font = FONT_TOTAL
        c_max_u_t.fill = FILL_TOTAL
        c_max_u_t.alignment = ALIGN_CENTER
        c_max_u_t.border = BORDE_TOTAL

        ultima_fila = t_u

    _ajustar_ancho_columnas(ws, celdas_excluidas)


def _armar_hoja_anual(ws, datos: LibroDatos) -> None:
    """Arma la hoja 'Anual'."""
    ws.sheet_properties.tabColor = "2E75B6"
    _configurar_pagina(ws)
    celdas_excluidas: Set[Tuple[int, int]] = set()

    # A1 y A2
    ws.row_dimensions[1].height = 28
    c_a1 = ws["A1"]
    c_a1.value = "Comparativo anual"
    c_a1.font = FONT_A1

    c_a2 = ws["A2"]
    c_a2.value = "Suma de los meses con datos de cada año. Montos en pesos."
    c_a2.font = FONT_A2

    anios_ciclos = [anio_de_hoja(c.nombre_hoja) for c in datos.ciclos]
    anios_unicos = sorted(list(dict.fromkeys(anios_ciclos)))

    # Mapeo de columnas de cada año en Comparativo
    mapa_cols_comp: Dict[str, Tuple[str, str]] = {}
    conteo_meses_anio: Dict[str, int] = {}
    for anio in anios_unicos:
        indices = [idx + 2 for idx, a in enumerate(anios_ciclos) if a == anio]
        mapa_cols_comp[anio] = (get_column_letter(indices[0]), get_column_letter(indices[-1]))
        conteo_meses_anio[anio] = len(indices)

    # Columnas en Anual: un año por columna, luego Diferencia y Variación por cada año desde el segundo
    col_anio: Dict[str, str] = {}
    for idx_a, anio in enumerate(anios_unicos):
        col_anio[anio] = get_column_letter(2 + idx_a)

    diff_pairs: List[Tuple[str, str, str, str]] = []
    headers_dif_var = []
    next_col_idx = 2 + len(anios_unicos)
    for i in range(1, len(anios_unicos)):
        a_curr = anios_unicos[i]
        a_prev = anios_unicos[i - 1]
        c_d = get_column_letter(next_col_idx)
        c_v = get_column_letter(next_col_idx + 1)
        headers_dif_var.append(f"Diferencia {a_curr} vs {a_prev}")
        headers_dif_var.append(f"Variación {a_curr} vs {a_prev}")
        diff_pairs.append((col_anio[a_curr], col_anio[a_prev], c_d, c_v))
        next_col_idx += 2

    headers_anual = ["Resumen"] + anios_unicos + headers_dif_var
    ws.row_dimensions[4].height = 22
    for col_idx, h_text in enumerate(headers_anual, 1):
        cell = ws.cell(row=4, column=col_idx, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

    # Fila 5: Meses con datos
    ws.row_dimensions[5].height = 18
    c_m_lbl = ws.cell(row=5, column=1, value="Meses con datos")
    c_m_lbl.font = FONT_BASE
    c_m_lbl.alignment = ALIGN_LEFT_INDENT
    c_m_lbl.border = BORDE_DATOS

    for idx_a, anio in enumerate(anios_unicos, 2):
        cell = ws.cell(row=5, column=idx_a, value=conteo_meses_anio[anio])
        cell.font = FONT_BASE
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    for _, _, c_d, c_v in diff_pairs:
        c_cell_d = ws[f"{c_d}5"]
        c_cell_d.value = None
        c_cell_d.font = FONT_BASE
        c_cell_d.border = BORDE_DATOS

        c_cell_v = ws[f"{c_v}5"]
        c_cell_v.value = None
        c_cell_v.font = FONT_BASE
        c_cell_v.border = BORDE_DATOS

    # Fila 6: Ingresos
    ws.row_dimensions[6].height = 18
    c_ing_lbl = ws.cell(row=6, column=1, value="Ingresos")
    c_ing_lbl.font = FONT_BASE
    c_ing_lbl.alignment = ALIGN_LEFT_INDENT
    c_ing_lbl.border = BORDE_DATOS

    for idx_a, anio in enumerate(anios_unicos, 2):
        c_ini, c_fin = mapa_cols_comp[anio]
        cell = ws.cell(row=6, column=idx_a, value=f"=SUM(Comparativo!{c_ini}5:{c_fin}5)")
        cell.font = FONT_BASE
        cell.number_format = FMT_ARS
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    for col_c, col_p, c_d, c_v in diff_pairs:
        cell_d = ws[f"{c_d}6"]
        cell_d.value = f"={col_c}6-{col_p}6"
        cell_d.font = FONT_BASE
        cell_d.number_format = FMT_ARS_DIF
        cell_d.alignment = ALIGN_RIGHT_INDENT
        cell_d.border = BORDE_DATOS

        cell_v = ws[f"{c_v}6"]
        cell_v.value = f'=IF({col_p}6>0,({col_c}6-{col_p}6)/{col_p}6,"")'
        cell_v.font = FONT_BASE
        cell_v.number_format = FMT_PCT
        cell_v.alignment = ALIGN_RIGHT_INDENT
        cell_v.border = BORDE_DATOS

    # Fila 7: Gastos
    ws.row_dimensions[7].height = 18
    c_gst_lbl = ws.cell(row=7, column=1, value="Gastos")
    c_gst_lbl.font = FONT_BASE
    c_gst_lbl.alignment = ALIGN_LEFT_INDENT
    c_gst_lbl.border = BORDE_DATOS

    for idx_a, anio in enumerate(anios_unicos, 2):
        c_ini, c_fin = mapa_cols_comp[anio]
        cell = ws.cell(row=7, column=idx_a, value=f"=SUM(Comparativo!{c_ini}6:{c_fin}6)")
        cell.font = FONT_BASE
        cell.number_format = FMT_ARS
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    for col_c, col_p, c_d, c_v in diff_pairs:
        cell_d = ws[f"{c_d}7"]
        cell_d.value = f"={col_c}7-{col_p}7"
        cell_d.font = FONT_BASE
        cell_d.number_format = FMT_ARS_VAR
        cell_d.alignment = ALIGN_RIGHT_INDENT
        cell_d.border = BORDE_DATOS

        cell_v = ws[f"{c_v}7"]
        cell_v.value = f'=IF({col_p}7>0,({col_c}7-{col_p}7)/{col_p}7,"")'
        cell_v.font = FONT_BASE
        cell_v.number_format = FMT_PCT_VAR
        cell_v.alignment = ALIGN_RIGHT_INDENT
        cell_v.border = BORDE_DATOS

    # Fila 8: Te sobró
    ws.row_dimensions[8].height = 18
    c_sob_lbl = ws.cell(row=8, column=1, value="Te sobró")
    c_sob_lbl.font = FONT_BASE
    c_sob_lbl.alignment = ALIGN_LEFT_INDENT
    c_sob_lbl.border = BORDE_DATOS

    for idx_a, anio in enumerate(anios_unicos, 2):
        col_l = col_anio[anio]
        cell = ws.cell(row=8, column=idx_a, value=f"={col_l}6-{col_l}7")
        cell.font = FONT_BASE
        cell.number_format = FMT_ARS_DIF
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    for col_c, col_p, c_d, c_v in diff_pairs:
        cell_d = ws[f"{c_d}8"]
        cell_d.value = f"={col_c}8-{col_p}8"
        cell_d.font = FONT_BASE
        cell_d.number_format = FMT_ARS_DIF
        cell_d.alignment = ALIGN_RIGHT_INDENT
        cell_d.border = BORDE_DATOS

        cell_v = ws[f"{c_v}8"]
        cell_v.value = f'=IF({col_p}8>0,({col_c}8-{col_p}8)/{col_p}8,"")'
        cell_v.font = FONT_BASE
        cell_v.number_format = FMT_PCT
        cell_v.alignment = ALIGN_RIGHT_INDENT
        cell_v.border = BORDE_DATOS

    # Fila 9: % que ahorraste
    ws.row_dimensions[9].height = 18
    c_aho_lbl = ws.cell(row=9, column=1, value="% que ahorraste")
    c_aho_lbl.font = FONT_BASE
    c_aho_lbl.alignment = ALIGN_LEFT_INDENT
    c_aho_lbl.border = BORDE_DATOS

    for idx_a, anio in enumerate(anios_unicos, 2):
        col_l = col_anio[anio]
        cell = ws.cell(row=9, column=idx_a, value=f'=IF({col_l}6>0,{col_l}8/{col_l}6,"")')
        cell.font = FONT_BASE
        cell.number_format = FMT_PCT
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_DATOS

    for _, _, c_d, c_v in diff_pairs:
        cell_d = ws[f"{c_d}9"]
        cell_d.value = None
        cell_d.font = FONT_BASE
        cell_d.border = BORDE_DATOS

        cell_v = ws[f"{c_v}9"]
        cell_v.value = None
        cell_v.font = FONT_BASE
        cell_v.border = BORDE_DATOS

    # Sección "Gastos por categoría" (empieza en la fila 12)
    celdas_excluidas.add((12, 1))
    c_cat_tit = ws.cell(row=12, column=1, value="Gastos por categoría")
    c_cat_tit.font = FONT_TITULO_SEC

    ws.row_dimensions[13].height = 22
    headers_cat_anual = ["Categoría"] + anios_unicos + headers_dif_var
    for col_idx, h_text in enumerate(headers_cat_anual, 1):
        cell = ws.cell(row=13, column=col_idx, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

    totales_cat_ars: Dict[str, Decimal] = {}
    for c in datos.ciclos:
        for cat_nom, gastado in c.gasto_ars_por_categoria.items():
            totales_cat_ars[cat_nom] = totales_cat_ars.get(cat_nom, Decimal("0")) + gastado

    categorias_ars_ordenadas = sorted(
        totales_cat_ars.keys(),
        key=lambda nom: (-totales_cat_ars[nom], nom),
    )

    p_anual = 14
    u_anual = p_anual + len(categorias_ars_ordenadas) - 1
    t_anual = u_anual + 1

    for idx_cat, cat_nom in enumerate(categorias_ars_ordenadas):
        r = p_anual + idx_cat
        rc = 13 + idx_cat  # Fila correspondiente en Comparativo
        ws.row_dimensions[r].height = 18

        c_nom = ws.cell(row=r, column=1, value=cat_nom)
        c_nom.font = FONT_BASE
        c_nom.alignment = ALIGN_LEFT_INDENT
        c_nom.border = BORDE_DATOS

        for idx_a, anio in enumerate(anios_unicos, 2):
            c_ini, c_fin = mapa_cols_comp[anio]
            cell = ws.cell(row=r, column=idx_a, value=f"=SUM(Comparativo!{c_ini}{rc}:{c_fin}{rc})")
            cell.font = FONT_BASE
            cell.number_format = FMT_ARS
            cell.alignment = ALIGN_RIGHT_INDENT
            cell.border = BORDE_DATOS

        for col_c, col_p, c_d, c_v in diff_pairs:
            cell_d = ws[f"{c_d}{r}"]
            cell_d.value = f"={col_c}{r}-{col_p}{r}"
            cell_d.font = FONT_BASE
            cell_d.number_format = FMT_ARS_VAR
            cell_d.alignment = ALIGN_RIGHT_INDENT
            cell_d.border = BORDE_DATOS

            cell_v = ws[f"{c_v}{r}"]
            cell_v.value = f'=IF({col_p}{r}>0,({col_c}{r}-{col_p}{r})/{col_p}{r},"")'
            cell_v.font = FONT_BASE
            cell_v.number_format = FMT_PCT_VAR
            cell_v.alignment = ALIGN_RIGHT_INDENT
            cell_v.border = BORDE_DATOS

    # Fila "Total de gastos" en Anual
    ws.row_dimensions[t_anual].height = 20
    c_tot_lbl = ws.cell(row=t_anual, column=1, value="Total de gastos")
    c_tot_lbl.font = FONT_TOTAL
    c_tot_lbl.fill = FILL_TOTAL
    c_tot_lbl.alignment = ALIGN_LEFT_INDENT
    c_tot_lbl.border = BORDE_TOTAL

    for idx_a, anio in enumerate(anios_unicos, 2):
        col_l = col_anio[anio]
        cell = ws.cell(row=t_anual, column=idx_a, value=f"=SUM({col_l}{p_anual}:{col_l}{u_anual})")
        cell.font = FONT_TOTAL
        cell.fill = FILL_TOTAL
        cell.number_format = FMT_ARS
        cell.alignment = ALIGN_RIGHT_INDENT
        cell.border = BORDE_TOTAL

    for col_c, col_p, c_d, c_v in diff_pairs:
        cell_d = ws[f"{c_d}{t_anual}"]
        cell_d.value = f"={col_c}{t_anual}-{col_p}{t_anual}"
        cell_d.font = FONT_TOTAL
        cell_d.fill = FILL_TOTAL
        cell_d.number_format = FMT_ARS_VAR
        cell_d.alignment = ALIGN_RIGHT_INDENT
        cell_d.border = BORDE_TOTAL

        cell_v = ws[f"{c_v}{t_anual}"]
        cell_v.value = f'=IF({col_p}{t_anual}>0,({col_c}{t_anual}-{col_p}{t_anual})/{col_p}{t_anual},"")'
        cell_v.font = FONT_TOTAL
        cell_v.fill = FILL_TOTAL
        cell_v.number_format = FMT_PCT_VAR
        cell_v.alignment = ALIGN_RIGHT_INDENT
        cell_v.border = BORDE_TOTAL

    ultima_fila = t_anual

    # Sección "En dólares" (solo si el libro tiene dólares)
    if datos.tiene_dolares:
        r_usd_tit = ultima_fila + 3
        celdas_excluidas.add((r_usd_tit, 1))
        c_usd_tit = ws.cell(row=r_usd_tit, column=1, value="En dólares")
        c_usd_tit.font = FONT_TITULO_SEC

        r_usd_hdr = r_usd_tit + 1
        ws.row_dimensions[r_usd_hdr].height = 22
        headers_usd_anual = ["Categoría"] + anios_unicos + headers_dif_var
        for col_idx, h_text in enumerate(headers_usd_anual, 1):
            cell = ws.cell(row=r_usd_hdr, column=col_idx, value=h_text)
            cell.font = FONT_ENCABEZADO
            cell.fill = FILL_ENCABEZADO
            cell.alignment = ALIGN_LEFT_INDENT if col_idx == 1 else ALIGN_CENTER

        totales_cat_usd: Dict[str, Decimal] = {}
        for c in datos.ciclos:
            for cat_nom, gastado in c.gasto_usd_por_categoria.items():
                totales_cat_usd[cat_nom] = totales_cat_usd.get(cat_nom, Decimal("0")) + gastado

        categorias_usd_ordenadas = sorted(
            totales_cat_usd.keys(),
            key=lambda nom: (-totales_cat_usd[nom], nom),
        )

        # Fila base de USD en Comparativo
        t_gastos_comp = 13 + len(categorias_ars_ordenadas)
        if datos.uso_mensual:
            r_usd_comp_hdr = t_gastos_comp + 3 + 2 + len(datos.uso_mensual) + 3 + 1
        else:
            r_usd_comp_hdr = t_gastos_comp + 3 + 1
        p_u_comp = r_usd_comp_hdr + 1

        p_u_anual = r_usd_hdr + 1
        u_u_anual = p_u_anual + len(categorias_usd_ordenadas) - 1
        t_u_anual = u_u_anual + 1

        for idx_u, cat_nom in enumerate(categorias_usd_ordenadas):
            r = p_u_anual + idx_u
            rc_usd = p_u_comp + idx_u
            ws.row_dimensions[r].height = 18

            c_nom = ws.cell(row=r, column=1, value=cat_nom)
            c_nom.font = FONT_BASE
            c_nom.alignment = ALIGN_LEFT_INDENT
            c_nom.border = BORDE_DATOS

            for idx_a, anio in enumerate(anios_unicos, 2):
                c_ini, c_fin = mapa_cols_comp[anio]
                cell = ws.cell(row=r, column=idx_a, value=f"=SUM(Comparativo!{c_ini}{rc_usd}:{c_fin}{rc_usd})")
                cell.font = FONT_BASE
                cell.number_format = FMT_USD
                cell.alignment = ALIGN_RIGHT_INDENT
                cell.border = BORDE_DATOS

            for col_c, col_p, c_d, c_v in diff_pairs:
                cell_d = ws[f"{c_d}{r}"]
                cell_d.value = f"={col_c}{r}-{col_p}{r}"
                cell_d.font = FONT_BASE
                cell_d.number_format = FMT_USD_VAR
                cell_d.alignment = ALIGN_RIGHT_INDENT
                cell_d.border = BORDE_DATOS

                cell_v = ws[f"{c_v}{r}"]
                cell_v.value = f'=IF({col_p}{r}>0,({col_c}{r}-{col_p}{r})/{col_p}{r},"")'
                cell_v.font = FONT_BASE
                cell_v.number_format = FMT_PCT_VAR
                cell_v.alignment = ALIGN_RIGHT_INDENT
                cell_v.border = BORDE_DATOS

        # Total USD en Anual
        ws.row_dimensions[t_u_anual].height = 20
        c_tot_u_lbl = ws.cell(row=t_u_anual, column=1, value="Total de gastos")
        c_tot_u_lbl.font = FONT_TOTAL
        c_tot_u_lbl.fill = FILL_TOTAL
        c_tot_u_lbl.alignment = ALIGN_LEFT_INDENT
        c_tot_u_lbl.border = BORDE_TOTAL

        for idx_a, anio in enumerate(anios_unicos, 2):
            col_l = col_anio[anio]
            cell = ws.cell(row=t_u_anual, column=idx_a, value=f"=SUM({col_l}{p_u_anual}:{col_l}{u_u_anual})")
            cell.font = FONT_TOTAL
            cell.fill = FILL_TOTAL
            cell.number_format = FMT_USD
            cell.alignment = ALIGN_RIGHT_INDENT
            cell.border = BORDE_TOTAL

        for col_c, col_p, c_d, c_v in diff_pairs:
            cell_d = ws[f"{c_d}{t_u_anual}"]
            cell_d.value = f"={col_c}{t_u_anual}-{col_p}{t_u_anual}"
            cell_d.font = FONT_TOTAL
            cell_d.fill = FILL_TOTAL
            cell_d.number_format = FMT_USD_VAR
            cell_d.alignment = ALIGN_RIGHT_INDENT
            cell_d.border = BORDE_TOTAL

            cell_v = ws[f"{c_v}{t_u_anual}"]
            cell_v.value = f'=IF({col_p}{t_u_anual}>0,({col_c}{t_u_anual}-{col_p}{t_u_anual})/{col_p}{t_u_anual},"")'
            cell_v.font = FONT_TOTAL
            cell_v.fill = FILL_TOTAL
            cell_v.number_format = FMT_PCT_VAR
            cell_v.alignment = ALIGN_RIGHT_INDENT
            cell_v.border = BORDE_TOTAL

        ultima_fila = t_u_anual

    _ajustar_ancho_columnas(ws, celdas_excluidas)


def _armar_hoja_como_leerlo(ws) -> None:
    """Arma la hoja explicativa 'Cómo leerlo'."""
    ws.sheet_properties.tabColor = "7F7F7F"
    _configurar_pagina(ws)

    # A1: título
    ws.row_dimensions[1].height = 28
    c_a1 = ws["A1"]
    c_a1.value = "Cómo leer este Excel"
    c_a1.font = FONT_A1

    for idx, linea in enumerate(LINEAS_COMO_LEERLO):
        r = 3 + idx
        cell = ws.cell(row=r, column=1, value=linea)
        cell.font = FONT_BASE
        cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        ws.row_dimensions[r].height = 16 * math.ceil(len(linea) / 100) + 4

    ws.column_dimensions["A"].width = 110
