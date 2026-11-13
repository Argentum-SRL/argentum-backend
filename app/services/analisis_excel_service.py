from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List, Optional
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.models.usuario import Usuario
from app.services.analisis_resumen_service import (
    obtener_movimientos_drilldown,
    obtener_resumen_analisis,
)


def _armar_hoja_resumen_ciclos(
    ws1: Any,
    resumen: Dict[str, Any],
    titulo_informe: Optional[str],
    navy_fill: PatternFill,
    header_font: Font,
    title_font: Font,
    bold_font: Font,
    regular_font: Font,
) -> None:
    moneda_vis = resumen["moneda_visual"]
    kpis = resumen["kpis"]
    periodo_txt = resumen["periodo_seleccionado"]["etiqueta"]

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


def _armar_hoja_categorias(
    ws2: Any,
    resumen: Dict[str, Any],
    navy_fill: PatternFill,
    sub_fill: PatternFill,
    header_font: Font,
    bold_font: Font,
    regular_font: Font,
    thin_border: Border,
) -> None:
    moneda_vis = resumen["moneda_visual"]
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


def _armar_hoja_movimientos(
    ws3: Any,
    movimientos: List[Dict[str, Any]],
    navy_fill: PatternFill,
    header_font: Font,
    regular_font: Font,
) -> None:
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


def _ajustar_anchos_columnas(wb: Workbook, ws1: Any) -> None:
    for ws in wb.worksheets:
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=12)
            col_idx = int(col[0].column or 1)
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 14), 44)
            for cell in col:
                if cell.row == 1 and ws != ws1:
                    cell.alignment = Alignment(vertical="center")


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
    """
    Genera un archivo Excel (.xlsx) estructurado con el resumen, la serie por ciclo,
    el desglose jerárquico por categorías y el detalle de transacciones.
    """
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

    ws1 = wb.active or wb.create_sheet("Resumen y Ciclos")
    _armar_hoja_resumen_ciclos(ws1, resumen, titulo_informe, navy_fill, header_font, title_font, bold_font, regular_font)

    ws2 = wb.create_sheet("Categorías y Subcategorías")
    _armar_hoja_categorias(ws2, resumen, navy_fill, sub_fill, header_font, bold_font, regular_font, thin_border)

    ws3 = wb.create_sheet("Movimientos")
    _armar_hoja_movimientos(ws3, movimientos, navy_fill, header_font, regular_font)

    _ajustar_anchos_columnas(wb, ws1)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
