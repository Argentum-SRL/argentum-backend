# -*- coding: utf-8 -*-
"""
Generación del libro de Excel de presupuestos en memoria con openpyxl.
Ubicación: app/services/presupuestos_excel_libro.py
"""
from __future__ import annotations

import io

from openpyxl import Workbook

from app.services.presupuestos_excel_datos import LibroDatos


def armar_libro(datos: LibroDatos) -> bytes:
    """
    Construye el libro completo de Excel en memoria a partir de LibroDatos.
    Retorna el contenido en formato bytes .xlsx.
    """
    from app.services.presupuestos_excel_hojas import (
        _armar_hoja_comparativo,
        _armar_hoja_anual,
        _armar_hoja_como_leerlo,
    )
    from app.services.presupuestos_excel_hoja_ciclo import _armar_hoja_ciclo

    wb = Workbook()
    wb.properties.creator = "Argentum"
    wb.properties.title = "Presupuestos"

    # 1. Hoja Comparativo (activa por defecto)
    ws_comp = wb.active
    ws_comp.title = "Comparativo"
    _armar_hoja_comparativo(ws_comp, datos)

    # 2. Hoja Anual
    ws_anual = wb.create_sheet(title="Anual")
    _armar_hoja_anual(ws_anual, datos)

    # 3. Una hoja por cada ciclo con datos (de la más vieja a la más nueva)
    for c in datos.ciclos:
        ws_ciclo = wb.create_sheet(title=c.nombre_hoja)
        _armar_hoja_ciclo(ws_ciclo, c, datos)

    # 4. Hoja Cómo leerlo al final
    ws_leer = wb.create_sheet(title="Cómo leerlo")
    _armar_hoja_como_leerlo(ws_leer)

    wb.active = ws_comp

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
