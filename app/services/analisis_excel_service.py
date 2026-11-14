# -*- coding: utf-8 -*-
"""
Orquestador del servicio de exportación a Excel para el módulo de Análisis de Argentum.
Delega la construcción de las hojas individuales en analisis_excel_hojas y analisis_excel_movimientos.
Ubicación: app/services/analisis_excel_service.py
"""
from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any, Dict, List, Optional
from uuid import UUID

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.usuario import Usuario
from app.services.analisis_ciclos_service import generar_ventana_ciclos
from app.services.analisis_comparativa_service import obtener_comparativa_ciclos
from app.services.analisis_excel_hojas import (
    armar_hoja_categorias,
    armar_hoja_evolucion,
    armar_hoja_kpis,
)
from app.services.analisis_excel_hojas_b import (
    armar_hoja_comparativa,
    armar_hoja_proyeccion,
)
from app.services.analisis_excel_movimientos import (
    armar_hoja_como_leerlo,
    armar_hoja_movimientos,
)
from app.services.analisis_proyeccion_service import obtener_proyeccion_compromisos
from app.services.analisis_resumen_service import obtener_resumen_analisis
from app.utils.fecha import hoy_argentina

NOMBRES_HOJAS: Dict[str, str] = {
    "kpis": "Números principales",
    "evolucion_ciclos": "Evolución",
    "desglose_categorias": "Categorías",
    "comparativa_ciclos": "Comparación",
    "proyeccion_cuotas": "Proyección",
}


def _armar_linea_filtros(
    db: Session,
    usuario_id: UUID,
    moneda_modo: str,
    ventana_ciclos: List[Dict[str, Any]],
    billetera_id: Optional[UUID],
    tarjeta_id: Optional[UUID],
    naturaleza: Optional[str],
) -> str:
    """Construye la oración descriptiva para A2 según las reglas de diseño unificado."""
    f_ini = ventana_ciclos[0]["fecha_inicio"] if ventana_ciclos else date(2026, 1, 1)
    f_fin = ventana_ciclos[-1]["fecha_fin"] if ventana_ciclos else date(2026, 12, 31)
    if isinstance(f_ini, str):
        f_ini = date.fromisoformat(f_ini)
    if isinstance(f_fin, str):
        f_fin = date.fromisoformat(f_fin)
    rango_fechas = f"Del {f_ini.strftime('%d/%m/%Y')} al {f_fin.strftime('%d/%m/%Y')}."

    modo = (moneda_modo or "solo_ars").lower()
    if modo == "solo_usd":
        moneda_part = "Montos en dólares."
    elif modo == "consolidado_ars":
        moneda_part = "Todo pasado a pesos."
    elif modo == "consolidado_usd":
        moneda_part = "Todo pasado a dólares."
    else:
        moneda_part = "Montos en pesos."

    if billetera_id:
        b = db.query(Billetera).filter(Billetera.id == billetera_id, Billetera.usuario_id == usuario_id).first()
        b_nombre = b.nombre if b else "Billetera seleccionada"
        medio_part = f"Billetera {b_nombre}."
    elif tarjeta_id:
        tc = db.query(TarjetaCredito).filter(TarjetaCredito.id == tarjeta_id, TarjetaCredito.usuario_id == usuario_id).first()
        tc_nombre = tc.nombre if tc else "Tarjeta seleccionada"
        medio_part = f"Tarjeta {tc_nombre}."
    else:
        medio_part = "Todas las billeteras y tarjetas."

    nat = (naturaleza or "").lower().strip()
    if nat == "cuotas":
        nat_part = " Solo cuotas."
    elif nat == "fijos":
        nat_part = " Solo gastos fijos."
    elif nat == "variables":
        nat_part = " Solo gastos variables."
    else:
        nat_part = ""

    return f"{rango_fechas} {moneda_part} {medio_part}{nat_part}"


def _cargar_datos_bloques(
    db: Session,
    usuario: Usuario,
    bloques_lista: List[str],
    moneda_modo: str,
    cantidad_ciclos: int,
    billetera_id: Optional[UUID],
    tarjeta_id: Optional[UUID],
    categoria_id: Optional[UUID],
    naturaleza: Optional[str],
    ciclo_a: Optional[str],
    ciclo_b: Optional[str],
    meses: int,
    ref_hoy: date,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Carga bajo demanda los datos requeridos por los bloques del informe."""
    resumen_data: Optional[Dict[str, Any]] = None
    if any(b in ("kpis", "evolucion_ciclos", "desglose_categorias") for b in bloques_lista):
        resumen_data = obtener_resumen_analisis(
            db=db,
            usuario=usuario,
            moneda_modo=moneda_modo,
            cantidad_ciclos=cantidad_ciclos,
            billetera_id=billetera_id,
            tarjeta_id=tarjeta_id,
            categoria_id=categoria_id,
            naturaleza=naturaleza,
            hoy=ref_hoy,
        )

    comparativa_data: Optional[Dict[str, Any]] = None
    if "comparativa_ciclos" in bloques_lista:
        comparativa_data = obtener_comparativa_ciclos(
            db=db,
            usuario=usuario,
            moneda_modo=moneda_modo,
            ciclo_a_clave=ciclo_a,
            ciclo_b_clave=ciclo_b,
            billetera_id=billetera_id,
            tarjeta_id=tarjeta_id,
            naturaleza=naturaleza,
            hoy=ref_hoy,
        )

    proyeccion_data: Optional[Dict[str, Any]] = None
    if "proyeccion_cuotas" in bloques_lista:
        proyeccion_data = obtener_proyeccion_compromisos(
            db=db,
            usuario=usuario,
            moneda_modo=moneda_modo,
            meses=meses,
            billetera_id=billetera_id,
            tarjeta_id=tarjeta_id,
            hoy=ref_hoy,
        )

    return resumen_data, comparativa_data, proyeccion_data


def generar_excel_analisis(
    db: Session,
    usuario: Usuario,
    moneda_modo: str = "solo_ars",
    cantidad_ciclos: int = 6,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    categoria_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
    titulo_informe: Optional[str] = None,
    bloques: Optional[List[str] | str] = None,
    titulos: Optional[List[str] | str] = None,
    ciclo_a: Optional[str] = None,
    ciclo_b: Optional[str] = None,
    meses: int = 6,
    hoy: Optional[date] = None,
) -> bytes:
    """
    Genera un archivo Excel (.xlsx) estructurado por partes según los bloques solicitados,
    seguido de 'Movimientos' y finalizando con la hoja 'Cómo leerlo'.
    """
    ref_hoy = hoy or hoy_argentina()

    # Normalización de bloques
    if not bloques:
        bloques_lista = ["kpis", "evolucion_ciclos", "desglose_categorias"]
    elif isinstance(bloques, str):
        bloques_lista = [b.strip() for b in bloques.split(",") if b.strip()]
    else:
        bloques_lista = list(bloques)

    # Normalización de títulos personalizados
    if isinstance(titulos, str):
        titulos_lista = [t.strip() for t in titulos.split("|")]
    elif isinstance(titulos, list):
        titulos_lista = list(titulos)
    else:
        titulos_lista = []

    ventana_ciclos = generar_ventana_ciclos(usuario, ref_hoy, cantidad_ciclos)

    linea_filtros = _armar_linea_filtros(
        db=db,
        usuario_id=usuario.id,
        moneda_modo=moneda_modo,
        ventana_ciclos=ventana_ciclos,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        naturaleza=naturaleza,
    )

    resumen_data, comparativa_data, proyeccion_data = _cargar_datos_bloques(
        db=db,
        usuario=usuario,
        bloques_lista=bloques_lista,
        moneda_modo=moneda_modo,
        cantidad_ciclos=cantidad_ciclos,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=categoria_id,
        naturaleza=naturaleza,
        ciclo_a=ciclo_a,
        ciclo_b=ciclo_b,
        meses=meses,
        ref_hoy=ref_hoy,
    )

    wb = Workbook()
    wb.properties.creator = "Argentum"
    wb.properties.title = titulo_informe or "Análisis"

    primer_hoja_usada = False
    conteo_hojas: Dict[str, int] = {}

    for idx, b_tipo in enumerate(bloques_lista):
        if b_tipo == "tabla_movimientos" or b_tipo not in NOMBRES_HOJAS:
            continue

        nombre_base = NOMBRES_HOJAS[b_tipo]
        conteo = conteo_hojas.get(nombre_base, 0) + 1
        conteo_hojas[nombre_base] = conteo
        titulo_tab = nombre_base if conteo == 1 else f"{nombre_base} ({conteo})"

        tit_custom = titulos_lista[idx] if idx < len(titulos_lista) and titulos_lista[idx] else None
        titulo_contenido = tit_custom or (titulo_informe if idx == 0 and titulo_informe else nombre_base)

        if not primer_hoja_usada:
            ws = wb.active
            ws.title = titulo_tab
            primer_hoja_usada = True
        else:
            ws = wb.create_sheet(title=titulo_tab)

        if b_tipo == "kpis" and resumen_data is not None:
            armar_hoja_kpis(ws, resumen_data, titulo_contenido, linea_filtros, moneda_modo)
        elif b_tipo == "evolucion_ciclos" and resumen_data is not None:
            armar_hoja_evolucion(ws, resumen_data, titulo_contenido, linea_filtros, moneda_modo)
        elif b_tipo == "desglose_categorias" and resumen_data is not None:
            armar_hoja_categorias(ws, resumen_data, titulo_contenido, linea_filtros, moneda_modo)
        elif b_tipo == "comparativa_ciclos" and comparativa_data is not None:
            armar_hoja_comparativa(ws, comparativa_data, titulo_contenido, linea_filtros, moneda_modo)
        elif b_tipo == "proyeccion_cuotas" and proyeccion_data is not None:
            armar_hoja_proyeccion(ws, proyeccion_data, titulo_contenido, linea_filtros, moneda_modo)

    # Hoja 'Movimientos'
    if not primer_hoja_usada:
        ws_mov = wb.active
        ws_mov.title = "Movimientos"
        primer_hoja_usada = True
    else:
        ws_mov = wb.create_sheet(title="Movimientos")

    armar_hoja_movimientos(
        ws=ws_mov,
        db=db,
        usuario=usuario,
        ventana_ciclos=ventana_ciclos,
        ref_hoy=ref_hoy,
        linea_filtros=linea_filtros,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=categoria_id,
        naturaleza=naturaleza,
    )

    # Hoja final obligatoria: 'Cómo leerlo'
    hojas_creadas = [s.title for s in wb.worksheets]
    ws_leer = wb.create_sheet(title="Cómo leerlo")
    armar_hoja_como_leerlo(ws_leer, hojas_creadas)

    wb.active = wb.worksheets[0]

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
