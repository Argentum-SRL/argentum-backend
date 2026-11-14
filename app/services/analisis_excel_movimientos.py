# -*- coding: utf-8 -*-
"""
Generación de las hojas 'Movimientos' y 'Cómo leerlo' para el Excel de Análisis de Argentum.
Ubicación: app/services/analisis_excel_movimientos.py
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.usuario import Usuario
from app.services import historial_service
from app.services.analisis_ciclos_service import ids_transacciones_ventana
from app.services.excel_estilos import (
    ALIGN_CENTER,
    ALIGN_LEFT_INDENT,
    ALIGN_RIGHT_INDENT,
    BORDE_DATOS,
    BORDE_TOTAL,
    FILL_ENCABEZADO,
    FILL_TOTAL,
    FMT_ARS_DIF,
    FMT_FECHA,
    FMT_USD_DIF,
    FONT_A1,
    FONT_A2,
    FONT_BASE,
    FONT_ENCABEZADO,
    FONT_NOTA_SEC,
    FONT_TITULO_SEC,
    FONT_TOTAL,
    TEXTO_QUE_CUENTA_COMO_GASTO,
    ajustar_ancho_columnas,
    configurar_pagina,
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


def _cargar_movimientos_ventana(
    db: Session,
    usuario_id: UUID,
    billetera_id: Optional[UUID],
    f_desde: date,
    f_hasta: date,
    ref_hoy: date,
    filtrar_ids: bool,
    ids_validos: set[str],
) -> tuple[list[Any], list[Any]]:
    """Consulta el historial de movimientos y los separa por moneda."""
    movs_ars: list[Any] = []
    movs_usd: list[Any] = []
    skip = 0
    limit = 500

    while True:
        resp = historial_service.listar_movimientos(
            db=db,
            usuario_id=usuario_id,
            modo="general",
            billetera_id=billetera_id,
            fecha_desde=f_desde,
            fecha_hasta=f_hasta,
            skip=skip,
            limit=limit,
            hoy=ref_hoy,
        )

        for m in resp.items:
            if filtrar_ids:
                tx_id_str = str(m.transaccion.id) if m.transaccion else m.id
                if tx_id_str not in ids_validos and m.id not in ids_validos:
                    continue

            moneda_str = (m.moneda or "").upper()
            if moneda_str in ("USD", "DOLARES", "DÓLARES"):
                movs_usd.append(m)
            else:
                movs_ars.append(m)

        if not resp.hay_mas:
            break
        skip += limit

    return movs_ars, movs_usd


def _sanitizar_texto(texto: Optional[str]) -> str:
    if not texto:
        return ""
    # Reemplazar siglas ARS y USD respetando regla 7 de diseño y consigna de exportación
    res = re.sub(r"\bARS\b", "Pesos", texto, flags=re.IGNORECASE)
    res = re.sub(r"\bUSD\b", "Dólares", res, flags=re.IGNORECASE)
    return res


def _escribir_tabla_moneda_movimientos(
    ws: Any,
    cur_r: int,
    titulo_sec: str,
    movs: list[Any],
    fmt_monto: str,
    headers: list[str],
    cuenta_map: dict[str, str],
) -> int:
    """Escribe una tabla completa de movimientos (pesos o dólares) con su cabecera y total."""
    c_sec = ws.cell(row=cur_r, column=1, value=titulo_sec)
    c_sec.font = FONT_TITULO_SEC
    cur_r += 1

    ws.row_dimensions[cur_r].height = 22
    for col_i, h_text in enumerate(headers, start=1):
        cell = ws.cell(row=cur_r, column=col_i, value=h_text)
        cell.font = FONT_ENCABEZADO
        cell.fill = FILL_ENCABEZADO
        cell.alignment = ALIGN_LEFT_INDENT if col_i == 1 else ALIGN_CENTER
    cur_r += 1

    start_r = cur_r
    for m in movs:
        ws.row_dimensions[cur_r].height = 18
        monto_val = float(m.monto)
        if m.sentido == "sale":
            monto_val = -monto_val

        billetera_str = m.billetera_nombre or ""
        if m.billetera_destino_nombre:
            billetera_str = f"{billetera_str} → {m.billetera_destino_nombre}"

        cuenta_como_str = cuenta_map.get(m.cuenta_como, m.cuenta_como.capitalize())

        c1 = ws.cell(row=cur_r, column=1, value=m.fecha)
        c1.font = FONT_BASE
        c1.border = BORDE_DATOS
        c1.number_format = FMT_FECHA
        c1.alignment = ALIGN_CENTER

        c2 = ws.cell(row=cur_r, column=2, value=_sanitizar_texto(m.etiqueta))
        c2.font = FONT_BASE
        c2.border = BORDE_DATOS
        c2.alignment = ALIGN_LEFT_INDENT

        c3 = ws.cell(row=cur_r, column=3, value=_sanitizar_texto(m.descripcion))
        c3.font = FONT_BASE
        c3.border = BORDE_DATOS
        c3.alignment = ALIGN_LEFT_INDENT

        c4 = ws.cell(row=cur_r, column=4, value=monto_val)
        c4.font = FONT_BASE
        c4.border = BORDE_DATOS
        c4.number_format = fmt_monto
        c4.alignment = ALIGN_RIGHT_INDENT

        c5 = ws.cell(row=cur_r, column=5, value=_sanitizar_texto(billetera_str))
        c5.font = FONT_BASE
        c5.border = BORDE_DATOS
        c5.alignment = ALIGN_LEFT_INDENT

        c6 = ws.cell(row=cur_r, column=6, value=cuenta_como_str)
        c6.font = FONT_BASE
        c6.border = BORDE_DATOS
        c6.alignment = ALIGN_LEFT_INDENT

        c7 = ws.cell(row=cur_r, column=7, value=_sanitizar_texto(m.cuota_texto))
        c7.font = FONT_BASE
        c7.border = BORDE_DATOS
        c7.alignment = ALIGN_LEFT_INDENT

        cur_r += 1

    tot_r = cur_r
    ws.row_dimensions[tot_r].height = 20
    t1 = ws.cell(row=tot_r, column=1, value="Total")
    t1.font = FONT_TOTAL
    t1.fill = FILL_TOTAL
    t1.border = BORDE_TOTAL
    t1.alignment = ALIGN_LEFT_INDENT

    for col_v in (2, 3, 5, 6, 7):
        cv = ws.cell(row=tot_r, column=col_v, value="")
        cv.font = FONT_TOTAL
        cv.fill = FILL_TOTAL
        cv.border = BORDE_TOTAL

    t4 = ws.cell(row=tot_r, column=4, value=f"=SUM(D{start_r}:D{tot_r - 1})")
    t4.font = FONT_TOTAL
    t4.fill = FILL_TOTAL
    t4.border = BORDE_TOTAL
    t4.number_format = fmt_monto
    t4.alignment = ALIGN_RIGHT_INDENT

    return tot_r + 1


def armar_hoja_movimientos(
    ws: Any,
    db: Session,
    usuario: Usuario,
    ventana_ciclos: List[Dict[str, Any]],
    ref_hoy: date,
    linea_filtros: str,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    categoria_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
) -> None:
    """
    Arma la hoja 'Movimientos' con todos los movimientos de la ventana,
    separados en secciones 'En pesos' y 'En dólares' con fórmulas de total.
    """
    _configurar_cabecera_hoja(ws, "Movimientos", linea_filtros)

    headers = [
        "Fecha",
        "Tipo",
        "Descripción",
        "Importe",
        "Billetera",
        "Cuenta como",
        "Cuota",
    ]

    if not ventana_ciclos:
        c_vacia = ws.cell(row=4, column=1, value="No hay movimientos en este período.")
        c_vacia.font = FONT_NOTA_SEC
        ajustar_ancho_columnas(ws)
        return

    f_desde = ventana_ciclos[0]["fecha_inicio"]
    f_hasta = ventana_ciclos[-1]["fecha_fin"]
    if isinstance(f_desde, str):
        f_desde = date.fromisoformat(f_desde)
    if isinstance(f_hasta, str):
        f_hasta = date.fromisoformat(f_hasta)

    filtrar_ids = bool(
        tarjeta_id is not None
        or categoria_id is not None
        or (naturaleza and naturaleza.lower().strip() != "todos")
    )

    ids_validos: set[str] = set()
    if filtrar_ids:
        ids_validos = ids_transacciones_ventana(
            db=db,
            usuario_id=usuario.id,
            desde=f_desde,
            hasta=f_hasta,
            ref_hoy=ref_hoy,
            billetera_id=billetera_id,
            tarjeta_id=tarjeta_id,
            categoria_id=categoria_id,
            naturaleza=naturaleza,
        )

    movs_ars, movs_usd = _cargar_movimientos_ventana(
        db=db,
        usuario_id=usuario.id,
        billetera_id=billetera_id,
        f_desde=f_desde,
        f_hasta=f_hasta,
        ref_hoy=ref_hoy,
        filtrar_ids=filtrar_ids,
        ids_validos=ids_validos,
    )

    if not movs_ars and not movs_usd:
        c_vacia = ws.cell(row=4, column=1, value="No hay movimientos en este período.")
        c_vacia.font = FONT_NOTA_SEC
        ajustar_ancho_columnas(ws)
        return

    cuenta_map = {"gasto": "Gasto", "ingreso": "Ingreso", "nada": "Nada"}
    headers = ["Fecha", "Tipo", "Descripción", "Importe", "Billetera", "Cuenta como", "Cuota"]
    cur_r = 4

    # Sección 'En pesos'
    if movs_ars:
        cur_r = _escribir_tabla_moneda_movimientos(
            ws, cur_r, "En pesos", movs_ars, FMT_ARS_DIF, headers, cuenta_map
        )

    # Sección 'En dólares'
    if movs_usd:
        if movs_ars:
            cur_r += 1  # Fila en blanco antes de la nueva sección
        cur_r = _escribir_tabla_moneda_movimientos(
            ws, cur_r, "En dólares", movs_usd, FMT_USD_DIF, headers, cuenta_map
        )

    ws.freeze_panes = "A6" if movs_ars else "A5"
    ajustar_ancho_columnas(ws)


def armar_hoja_como_leerlo(ws: Any, hojas: List[str]) -> None:
    """Arma la hoja explicativa 'Cómo leerlo' adaptada a las hojas de este libro."""
    ws.sheet_properties.tabColor = "7F7F7F"
    configurar_pagina(ws)

    ws.row_dimensions[1].height = 28
    c_a1 = ws["A1"]
    c_a1.value = "Cómo leer este Excel"
    c_a1.font = FONT_A1

    lineas: List[str] = []

    # Una línea por cada hoja de datos que tiene este libro
    for h in hojas:
        if h == "Cómo leerlo":
            continue
        if "Números principales" in h:
            lineas.append(f"{h}: el resumen general del período, tus ingresos, egresos, lo que te quedó y el promedio de gastos por ciclo.")
        elif "Evolución" in h:
            lineas.append(f"{h}: la serie histórica ciclo a ciclo con ingresos, gastos, lo que te quedó y el porcentaje ahorrado.")
        elif "Categorías" in h:
            lineas.append(f"{h}: el detalle de gastos agrupado por categorías y subcategorías con sus montos y porcentajes del total.")
        elif "Comparación" in h:
            lineas.append(f"{h}: la comparativa rubro por rubro entre los dos ciclos seleccionados, con sus diferencias de monto y porcentaje.")
        elif "Proyección" in h:
            lineas.append(f"{h}: el compromiso mensual futuro en cuotas y suscripciones, y el detalle de compras en cuotas activas.")
        elif "Movimientos" in h:
            lineas.append(f"{h}: el listado completo de todos los movimientos del período con fecha, importe y medio de pago.")
        else:
            lineas.append(f"{h}: resumen y detalle de datos correspondientes a {h}.")

    # Reglas de DISEÑO
    lineas.append("Los números de arriba son de ciclos terminados.")
    lineas.append("«Sin datos»: ciclo sin ningún movimiento, no cuenta en los promedios.")
    lineas.append("«En curso»: ciclo que todavía no terminó.")
    lineas.append("En «Todo pasado a pesos/dólares» cada movimiento se pasa con el dólar oficial del día en que se hizo.")
    lineas.append(TEXTO_QUE_CUENTA_COMO_GASTO)
    lineas.append("Los totales, porcentajes y diferencias son fórmulas: si cambiás un número, se recalculan solos.")

    for idx, linea in enumerate(lineas):
        r = 3 + idx
        cell = ws.cell(row=r, column=1, value=linea)
        cell.font = FONT_BASE
        cell.alignment = ALIGN_LEFT_INDENT
        ws.row_dimensions[r].height = 16 * math.ceil(len(linea) / 100) + 4

    ws.column_dimensions["A"].width = 110
