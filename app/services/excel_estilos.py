# -*- coding: utf-8 -*-
"""
Estilos compartidos para generación de reportes en Excel (Presupuestos y Análisis).
Ubicación: app/services/excel_estilos.py
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Set, Tuple

from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.utils.formato import formatear_monto

# Constantes de formatos de número exactas según diseño
FMT_ARS = '"$" #,##0;-"$" #,##0;"-"'
FMT_ARS_DIF = '"$" #,##0;[Red]-"$" #,##0;"-"'
FMT_USD = '"US$" #,##0.00;-"US$" #,##0.00;"-"'
FMT_USD_DIF = '"US$" #,##0.00;[Red]-"US$" #,##0.00;"-"'
FMT_PCT = '0%'

# Formatos para diferencias y variaciones
FMT_ARS_VAR = '[Red]+"$" #,##0;[Color10]-"$" #,##0;"-"'
FMT_USD_VAR = '[Red]+"US$" #,##0.00;[Color10]-"US$" #,##0.00;"-"'
FMT_PCT_VAR = '[Red]+0%;[Color10]-0%;0%'

# Formato de fecha
FMT_FECHA = "DD/MM/YYYY"

# Estilos reutilizables (Calibri en todo)
FONT_BASE = Font(name="Calibri", size=11)
FONT_A1 = Font(name="Calibri", size=16, bold=True, color="1F3864")
FONT_A2 = Font(name="Calibri", size=10, italic=True, color="595959")
FONT_TITULO_SEC = Font(name="Calibri", size=13, bold=True, color="1F3864")
FONT_NOTA_SEC = Font(name="Calibri", size=10, italic=True, color="7F7F7F")
FONT_ENCABEZADO = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
FONT_TOTAL = Font(name="Calibri", size=11, bold=True)

FILL_ENCABEZADO = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
FILL_TOTAL = PatternFill(start_color="EEF3FA", end_color="EEF3FA", fill_type="solid")

SIDE_THIN_1F3864 = Side(style="thin", color="1F3864")
SIDE_THIN_D9E1F2 = Side(style="thin", color="D9E1F2")

BORDE_DATOS = Border(bottom=SIDE_THIN_D9E1F2)
BORDE_TOTAL = Border(top=SIDE_THIN_1F3864, bottom=SIDE_THIN_D9E1F2)

ALIGN_LEFT_INDENT = Alignment(horizontal="left", vertical="center", indent=1)
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_RIGHT_INDENT = Alignment(horizontal="right", vertical="center", indent=1)

MAPA_MES_CORTO = {
    "Enero": "Ene", "Febrero": "Feb", "Marzo": "Mar", "Abril": "Abr",
    "Mayo": "May", "Junio": "Jun", "Julio": "Jul", "Agosto": "Ago",
    "Septiembre": "Sep", "Octubre": "Oct", "Noviembre": "Nov", "Diciembre": "Dic"
}

TEXTO_QUE_CUENTA_COMO_GASTO = (
    "Qué cuenta como gasto: lo que pagaste con tus cuentas y tarjetas. "
    "No cuentan el pago del resumen de la tarjeta, las transferencias entre tus cuentas, "
    "lo que pasa a tus ahorros, metas o inversiones, ni la plata que solo pasó por tu cuenta. "
    "Los reintegros ya están descontados."
)


def mes_corto(nombre_hoja: str) -> str:
    """Convierte el nombre de hoja al formato de mes corto ('Dic 2025', 'Oct 2026 (2)')."""
    partes = nombre_hoja.split(" ", 1)
    mes_largo = partes[0]
    resto = partes[1] if len(partes) > 1 else ""
    c = MAPA_MES_CORTO.get(mes_largo, mes_largo[:3])
    if len(resto) == 2 and resto.isdigit():
        resto = f"20{resto}"
    return f"{c} {resto}".strip()


def anio_de_hoja(nombre_hoja: str) -> str:
    """Extrae el año del nombre de la hoja de un ciclo."""
    for p in nombre_hoja.split():
        p_clean = p.strip("()")
        if p_clean.isdigit() and len(p_clean) == 4:
            return p_clean
    return nombre_hoja.split()[-1]


def configurar_pagina(ws) -> None:
    """Configura márgenes, orientación horizontal, A4 y ajuste a 1 página de ancho."""
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.4
    ws.page_margins.right = 0.4
    ws.page_margins.top = 0.6
    ws.page_margins.bottom = 0.6
    ws.views.sheetView[0].showGridLines = False
    ws.views.sheetView[0].zoomScale = 100


def aplicar_reglas_porcentaje(ws, rango: str) -> None:
    """Aplica formato condicional para % usado y presupuestos de cobro a cobro."""
    rule_gt1 = CellIsRule(
        operator="greaterThan",
        formula=["1"],
        font=Font(name="Calibri", size=11, bold=True, color="C00000"),
    )
    rule_bet = CellIsRule(
        operator="between",
        formula=["0.8", "1"],
        font=Font(name="Calibri", size=11, color="C55A11"),
    )
    ws.conditional_formatting.add(rango, rule_gt1)
    ws.conditional_formatting.add(rango, rule_bet)


def ajustar_ancho_columnas(ws, celdas_excluidas: Optional[Set[Tuple[int, int]]] = None) -> None:
    """
    Calcula el ancho de cada columna según: min(max(L + 3, 12), 50),
    donde L es el largo más largo desde la fila 4 hacia abajo, excluyendo títulos y notas.
    Montos con el largo de su texto mostrado, fórmulas = 12.
    """
    excluidas = celdas_excluidas or set()
    for col_idx in range(1, ws.max_column + 1):
        max_l = 0
        for row_idx in range(4, ws.max_row + 1):
            if (row_idx, col_idx) in excluidas:
                continue
            cell = ws.cell(row=row_idx, column=col_idx)
            val = cell.value
            if val is None or val == "":
                continue

            if isinstance(val, str) and val.startswith("="):
                l = 12
            elif isinstance(val, (int, float, Decimal)):
                fmt = cell.number_format or ""
                if "US$" in fmt:
                    s = formatear_monto(Decimal(str(val)), moneda="USD")
                    l = len(s)
                elif "%" in fmt:
                    l = len(f"{int(round(float(val) * 100))}%")
                elif "$" in fmt:
                    s = formatear_monto(Decimal(str(val)), moneda="ARS")
                    l = len(s)
                else:
                    l = len(str(val))
            else:
                l = len(str(val))

            if l > max_l:
                max_l = l

        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = min(max(max_l + 3, 12), 50)
