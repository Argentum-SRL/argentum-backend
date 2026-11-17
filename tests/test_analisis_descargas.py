# -*- coding: utf-8 -*-
"""
tests/test_analisis_descargas.py
Pruebas exhaustivas para las descargas de Análisis (TD1 a TD9) según decisión fase_n4b_diseno_archivos.
"""
from __future__ import annotations

import ast
import re
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.analisis_comparativa_service import obtener_comparativa_ciclos
from app.services.analisis_excel_service import generar_excel_analisis
from app.services.analisis_proyeccion_service import obtener_proyeccion_compromisos
from app.services.analisis_resumen_service import obtener_resumen_analisis
from app.services.excel_estilos import (
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
    FMT_USD_DIF,
    FMT_USD_VAR,
    FONT_A1,
    FONT_A2,
    FONT_ENCABEZADO,
    FONT_NOTA_SEC,
    TEXTO_QUE_CUENTA_COMO_GASTO,
)


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and hasattr(JSONB._compiler_dispatcher, "specs")
        and "sqlite" in JSONB._compiler_dispatcher.specs
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    orig_uuid_processor = types.Uuid.bind_processor

    def _safe_uuid_processor(self, dialect):
        proc = orig_uuid_processor(self, dialect)
        if proc is None:
            return None

        def process(value):
            if isinstance(value, str):
                try:
                    import uuid
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    import uuid
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario(db: Session, ciclo_tipo: CicloTipo = CicloTipo.DIA_FIJO, ciclo_param: str = "1") -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:8]}@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=ciclo_tipo,
        ciclo_valor=ciclo_param,
        ciclo_ajuste_direccion=None,
    )
    db.add(u)
    db.commit()
    return u


def _crear_billetera(db: Session, usuario_id: UUID, nombre: str = "Billetera Principal", moneda: Moneda = Moneda.ARS) -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre=nombre,
        moneda=moneda,
        es_inversion=False,
    )
    db.add(b)
    db.commit()
    return b


def _crear_tarjeta(db: Session, usuario_id: UUID, billetera_id: UUID, nombre: str = "Visa Galicia") -> TarjetaCredito:
    tc = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        nombre=nombre,
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=5,
    )
    db.add(tc)
    db.commit()
    return tc


def _crear_categoria(db: Session, nombre: str, tipo: TipoCategoria = TipoCategoria.EGRESO) -> Categoria:
    c = Categoria(
        id=uuid4(),
        nombre=nombre,
        tipo=tipo,
        orden=1,
    )
    db.add(c)
    db.commit()
    return c


def _crear_transaccion(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID,
    monto: Decimal,
    fecha: date,
    tipo: TipoTransaccion = TipoTransaccion.EGRESO,
    moneda: Moneda = Moneda.ARS,
    categoria_id: UUID | None = None,
    tarjeta_id: UUID | None = None,
    descripcion: str = "Tx de prueba",
) -> Transaccion:
    t = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        tipo=tipo,
        monto=monto,
        moneda=moneda,
        fecha=fecha,
        descripcion=descripcion,
        categoria_id=categoria_id,
        tarjeta_id=tarjeta_id,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(t)
    db.commit()
    return t


def _cargar_datos_base(db: Session):
    """
    Datos base para descargas:
    - septiembre ingreso 1.500.000 el 05/09 y gasto 100.000 el 10/09
    - octubre gastos 250.000 el 05/10 y 950.000 el 20/10
    - noviembre gasto 125.000 el 05/11
    - todo en pesos, una billetera.
    """
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_ars = _crear_billetera(db, user.id, "Billetera Principal", Moneda.ARS)
    tc = _crear_tarjeta(db, user.id, b_ars.id, "Visa Galicia")
    cat = _crear_categoria(db, "General")

    # Septiembre 2026
    _crear_transaccion(db, user.id, b_ars.id, Decimal("1500000"), date(2026, 9, 5), tipo=TipoTransaccion.INGRESO, descripcion="Sueldo Septiembre")
    _crear_transaccion(db, user.id, b_ars.id, Decimal("100000"), date(2026, 9, 10), categoria_id=cat.id, descripcion="Gasto Sep")

    # Octubre 2026
    _crear_transaccion(db, user.id, b_ars.id, Decimal("250000"), date(2026, 10, 5), categoria_id=cat.id, descripcion="Gasto Oct 1")
    _crear_transaccion(db, user.id, b_ars.id, Decimal("950000"), date(2026, 10, 20), categoria_id=cat.id, descripcion="Gasto Oct 2")

    # Noviembre 2026
    _crear_transaccion(db, user.id, b_ars.id, Decimal("125000"), date(2026, 11, 5), categoria_id=cat.id, descripcion="Gasto Nov")

    return user, b_ars, tc, cat


# ── TD1: Hojas ────────────────────────────────────────────────────────────────
def test_td1_hojas(db: Session):
    user, _, _, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 14)

    # 1. kpis,proyeccion_cuotas,desglose_categorias con titulos "Mis números||"
    b1 = generar_excel_analisis(
        db,
        user,
        bloques="kpis,proyeccion_cuotas,desglose_categorias",
        titulos="Mis números||",
        hoy=hoy,
    )
    wb1 = openpyxl.load_workbook(BytesIO(b1))
    assert wb1.sheetnames == ["Números principales", "Proyección", "Categorías", "Movimientos", "Cómo leerlo"]
    assert wb1["Números principales"]["A1"].value == "Mis números"
    assert wb1["Proyección"]["A1"].value == "Proyección"
    assert wb1["Categorías"]["A1"].value == "Categorías"
    assert wb1.active == wb1.worksheets[0]

    # Línea en Cómo leerlo nombra cada hoja y contiene TEXTO_QUE_CUENTA_COMO_GASTO
    ws_leer = wb1["Cómo leerlo"]
    valores_col_a = [ws_leer.cell(row=r, column=1).value for r in range(1, ws_leer.max_row + 1)]
    texto_total = "\n".join(str(v or "") for v in valores_col_a)
    for sh in ["Números principales", "Proyección", "Categorías", "Movimientos"]:
        assert sh in texto_total
    assert TEXTO_QUE_CUENTA_COMO_GASTO in texto_total

    # 2. evolucion_ciclos,evolucion_ciclos -> nombres repetidos
    b2 = generar_excel_analisis(db, user, bloques="evolucion_ciclos,evolucion_ciclos", hoy=hoy)
    wb2 = openpyxl.load_workbook(BytesIO(b2))
    assert wb2.sheetnames == ["Evolución", "Evolución (2)", "Movimientos", "Cómo leerlo"]
    assert wb2.active == wb2.worksheets[0]

    # 3. Sin bloques -> default
    b3 = generar_excel_analisis(db, user, hoy=hoy)
    wb3 = openpyxl.load_workbook(BytesIO(b3))
    assert wb3.sheetnames == ["Números principales", "Evolución", "Categorías", "Movimientos", "Cómo leerlo"]
    assert wb3.active == wb3.worksheets[0]

    # 4. tabla_movimientos solo -> Movimientos, Cómo leerlo
    b4 = generar_excel_analisis(db, user, bloques="tabla_movimientos", hoy=hoy)
    wb4 = openpyxl.load_workbook(BytesIO(b4))
    assert wb4.sheetnames == ["Movimientos", "Cómo leerlo"]
    assert wb4.active == wb4.worksheets[0]


# ── TD2: Fidelidad ────────────────────────────────────────────────────────────
def test_td2_fidelidad(db: Session):
    user, _, _, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 14)

    resumen = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    b = generar_excel_analisis(
        db,
        user,
        bloques="kpis,evolucion_ciclos",
        cantidad_ciclos=6,
        hoy=hoy,
    )
    wb = openpyxl.load_workbook(BytesIO(b), data_only=False)

    # Hoja Evolución
    ws_evo = wb["Evolución"]
    assert str(ws_evo["A2"].value).startswith("Del 01/06/2026 al 30/11/2026. Montos en pesos. Todas las billeteras y tarjetas.")

    serie = resumen["serie_ciclos"]
    assert len(serie) == 6

    estados = []
    terminadas_filas = []
    for idx, c in enumerate(serie):
        r = 5 + idx
        c_est = ws_evo.cell(row=r, column=8).value
        estados.append(c_est)
        if c_est == "Terminado":
            terminadas_filas.append(r)

        # Te quedó es fórmula =D{r}-E{r}
        assert ws_evo.cell(row=r, column=6).value == f"=D{r}-E{r}"
        # Ingresos y gastos coinciden
        assert float(ws_evo.cell(row=r, column=4).value or 0.0) == float(c["ingresos"] or 0.0)
        assert float(ws_evo.cell(row=r, column=5).value or 0.0) == float(c["gastos"] or 0.0)

    assert estados[0] == "Sin datos"
    assert estados[1] == "Sin datos"
    assert estados[2] == "Sin datos"
    assert estados[3] == "Terminado"
    assert estados[4] == "Terminado"
    assert estados[5] == "En curso"

    tot_r = 5 + len(serie)
    assert ws_evo.cell(row=tot_r, column=1).value == "Total ciclos terminados"
    f_ing = str(ws_evo.cell(row=tot_r, column=4).value)
    f_gas = str(ws_evo.cell(row=tot_r, column=5).value)

    # La fórmula no incluye noviembre ni las filas Sin datos
    assert "D10" not in f_ing and "E10" not in f_gas  # nov es fila 10
    for r_sd in (5, 6, 7):
        assert f"D{r_sd}" not in f_ing
        assert f"E{r_sd}" not in f_gas

    # Evaluar suma de las celdas referenciadas en la fórmula
    celdas_ing = [int(m) for m in re.findall(r"D(\d+)", f_ing)]
    celdas_gas = [int(m) for m in re.findall(r"E(\d+)", f_gas)]
    suma_ing = sum(float(ws_evo.cell(row=r, column=4).value or 0.0) for r in celdas_ing)
    suma_gas = sum(float(ws_evo.cell(row=r, column=5).value or 0.0) for r in celdas_gas)
    assert suma_ing == 1500000.0
    assert suma_gas == 1300000.0

    # Hoja Números principales
    ws_kpis = wb["Números principales"]
    assert ws_kpis.cell(row=5, column=1).value == "Ingresos"
    assert ws_kpis.cell(row=5, column=2).value == 1500000.0
    assert ws_kpis.cell(row=6, column=1).value == "Egresos"
    assert ws_kpis.cell(row=6, column=2).value == 1300000.0
    assert ws_kpis.cell(row=7, column=1).value == "Te quedó"
    assert ws_kpis.cell(row=7, column=2).value == "=B5-B6"

    # Hoja Movimientos: suma de Importe de 'Cuenta como' = 'Gasto' con fecha <= 31/10 da -1.300.000
    ws_mov = wb["Movimientos"]
    suma_gastos_oct = 0.0
    for r in range(6, ws_mov.max_row + 1):
        f_val = ws_mov.cell(row=r, column=1).value
        imp_val = ws_mov.cell(row=r, column=4).value
        cuenta_val = ws_mov.cell(row=r, column=6).value
        if cuenta_val == "Gasto" and imp_val is not None and not str(imp_val).startswith("="):
            f_d = f_val.date() if isinstance(f_val, datetime) else f_val
            if f_d and f_d <= date(2026, 10, 31):
                suma_gastos_oct += float(imp_val)

    assert round(suma_gastos_oct, 2) == -1300000.0


# ── TD3: Movimientos completos ────────────────────────────────────────────────
def test_td3_movimientos_completos(db: Session):
    user, b_ars, tc, cat = _cargar_datos_base(db)
    hoy = date(2026, 11, 14)

    # 150 gastos de 1.000 en octubre
    for i in range(150):
        _crear_transaccion(db, user.id, b_ars.id, Decimal("1000"), date(2026, 10, 15), categoria_id=cat.id, descripcion=f"Gasto lote {i}")

    # Gasto fuera de ventana (05/04/2026)
    _crear_transaccion(db, user.id, b_ars.id, Decimal("5000"), date(2026, 4, 5), categoria_id=cat.id, descripcion="Gasto viejo abril")

    # Gasto en dólares
    b_usd = _crear_billetera(db, user.id, "Billetera USD", Moneda.USD)
    _crear_transaccion(db, user.id, b_usd.id, Decimal("50"), date(2026, 10, 18), moneda=Moneda.USD, categoria_id=cat.id, descripcion="Gasto dolares")

    # Otra billetera
    b_otra = _crear_billetera(db, user.id, "Otra Billetera", Moneda.ARS)
    _crear_transaccion(db, user.id, b_otra.id, Decimal("2000"), date(2026, 10, 22), categoria_id=cat.id, descripcion="Gasto otra billetera")

    b = generar_excel_analisis(db, user, bloques="tabla_movimientos", cantidad_ciclos=6, hoy=hoy)
    wb = openpyxl.load_workbook(BytesIO(b), data_only=False)
    ws = wb["Movimientos"]

    # Encabezados exactos sin Moneda
    headers_obtenidos = [ws.cell(row=5, column=col_i).value for col_i in range(1, 8)]
    assert headers_obtenidos == ["Fecha", "Tipo", "Descripción", "Importe", "Billetera", "Cuenta como", "Cuota"]

    # Sección en pesos no contiene el gasto en dólares y tiene fila Total
    valores_pesos_desc = []
    encontro_total_pesos = False
    fila_sec_usd = None
    for r in range(6, ws.max_row + 1):
        v_col1 = ws.cell(row=r, column=1).value
        if v_col1 == "En dólares":
            fila_sec_usd = r
            break
        if v_col1 == "Total":
            encontro_total_pesos = True
            assert str(ws.cell(row=r, column=4).value).startswith("=SUM(D6:")
        else:
            desc = ws.cell(row=r, column=3).value
            if desc:
                valores_pesos_desc.append(desc)

    assert encontro_total_pesos
    assert "Gasto dolares" not in valores_pesos_desc
    assert "Gasto viejo abril" not in valores_pesos_desc
    # 150 de lote + 1 ingreso sep + 1 gasto sep + 2 gastos oct base + 1 gasto nov base + 1 gasto otra billetera = 156
    assert len(valores_pesos_desc) == 156

    # Sección En dólares
    assert fila_sec_usd is not None
    headers_usd = [ws.cell(row=fila_sec_usd + 1, column=col_i).value for col_i in range(1, 8)]
    assert headers_usd == ["Fecha", "Tipo", "Descripción", "Importe", "Billetera", "Cuenta como", "Cuota"]
    celda_gasto_usd = ws.cell(row=fila_sec_usd + 2, column=4)
    assert celda_gasto_usd.value == -50.0
    assert celda_gasto_usd.number_format == FMT_USD_DIF

    # Filtro por billetera_id
    b_filt = generar_excel_analisis(db, user, bloques="tabla_movimientos", billetera_id=b_otra.id, cantidad_ciclos=6, hoy=hoy)
    wb_filt = openpyxl.load_workbook(BytesIO(b_filt), data_only=True)
    ws_f = wb_filt["Movimientos"]
    descs_f = [ws_f.cell(row=r, column=3).value for r in range(6, ws_f.max_row + 1) if ws_f.cell(row=r, column=1).value != "Total"]
    assert descs_f == ["Gasto otra billetera"]

    # Filtro por tarjeta_id
    _crear_transaccion(db, user.id, b_ars.id, Decimal("3000"), date(2026, 10, 25), tarjeta_id=tc.id, categoria_id=cat.id, descripcion="Gasto con tarjeta")
    b_tc = generar_excel_analisis(db, user, bloques="tabla_movimientos", tarjeta_id=tc.id, cantidad_ciclos=6, hoy=hoy)
    wb_tc = openpyxl.load_workbook(BytesIO(b_tc), data_only=True)
    ws_tc = wb_tc["Movimientos"]
    descs_tc = [ws_tc.cell(row=r, column=3).value for r in range(6, ws_tc.max_row + 1) if ws_tc.cell(row=r, column=1).value != "Total"]
    assert descs_tc == ["Gasto con tarjeta"]


# ── TD4: Comparación ──────────────────────────────────────────────────────────
def test_td4_comparacion(db: Session):
    user, _, _, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 14)

    comp_disp = obtener_comparativa_ciclos(db, user, hoy=hoy)
    clave_oct = next(c["clave"] for c in comp_disp["ciclos_disponibles"] if "oct" in c["etiqueta"].lower())
    clave_sep = next(c["clave"] for c in comp_disp["ciclos_disponibles"] if "sep" in c["etiqueta"].lower())

    comp = obtener_comparativa_ciclos(
        db, user, ciclo_a_clave=clave_oct, ciclo_b_clave=clave_sep, hoy=hoy
    )
    b = generar_excel_analisis(
        db,
        user,
        bloques="comparativa_ciclos",
        ciclo_a=clave_oct,
        ciclo_b=clave_sep,
        hoy=hoy,
    )
    wb = openpyxl.load_workbook(BytesIO(b), data_only=False)
    ws = wb["Comparación"]

    # Encabezados con nombres cortos
    assert ws.cell(row=4, column=2).value == "Oct 2026"
    assert ws.cell(row=4, column=3).value == "Sep 2026"

    # Fila de datos
    assert ws.cell(row=5, column=1).value == comp["categorias"][0]["categoria_nombre"]
    assert ws.cell(row=5, column=2).value == comp["categorias"][0]["monto_a"]
    assert ws.cell(row=5, column=3).value == comp["categorias"][0]["monto_b"]

    c_diff = ws.cell(row=5, column=4)
    assert c_diff.value == "=B5-C5"
    assert c_diff.number_format == FMT_ARS_VAR

    c_diff_pct = ws.cell(row=5, column=5)
    assert c_diff_pct.value == '=IF(C5=0,"",(B5-C5)/C5)'
    assert c_diff_pct.number_format == FMT_PCT_VAR


# ── TD5: Proyección ───────────────────────────────────────────────────────────
def test_td5_proyeccion(db: Session):
    user, _, _, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 14)

    # Meses 12 -> 12 filas y Total es fórmula
    b12 = generar_excel_analisis(db, user, bloques="proyeccion_cuotas", meses=12, hoy=hoy)
    wb12 = openpyxl.load_workbook(BytesIO(b12), data_only=False)
    ws12 = wb12["Proyección"]

    for r in range(5, 17):
        assert ws12.cell(row=r, column=4).value == f"=B{r}+C{r}"

    assert ws12.cell(row=17, column=1).value == "Total"
    assert ws12.cell(row=17, column=4).value == "=SUM(D5:D16)"

    # Meses 7 -> 422
    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    try:
        resp = client.get("/analisis/exportar-excel?meses=7")
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


# ── TD6: Diseño ───────────────────────────────────────────────────────────────
def test_td6_diseno(db: Session, monkeypatch):
    user, _, _, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 14)

    b = generar_excel_analisis(db, user, titulo_informe="Resumen del ciclo", hoy=hoy)
    wb = openpyxl.load_workbook(BytesIO(b), data_only=False)

    assert wb.properties.creator == "Argentum"
    assert wb.properties.title == "Resumen del ciclo"

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        assert ws.views.sheetView[0].showGridLines is False
        assert ws.page_setup.orientation == ws.ORIENTATION_LANDSCAPE
        assert str(ws.page_setup.paperSize) == str(ws.PAPERSIZE_A4)
        assert ws.page_setup.fitToWidth == 1
        assert ws.auto_filter.ref is None

        if sheet_name == "Cómo leerlo":
            assert "7F7F7F" in str(ws.sheet_properties.tabColor.rgb)
        else:
            assert "1F3864" in str(ws.sheet_properties.tabColor.rgb)
            assert ws.row_dimensions[1].height == 28
            assert ws.cell(row=1, column=1).font.name == FONT_A1.name
            assert ws.cell(row=1, column=1).font.size == FONT_A1.size
            assert ws.cell(row=1, column=1).font.bold == FONT_A1.bold
            assert ws.cell(row=2, column=1).font.name == FONT_A2.name
            assert ws.cell(row=2, column=1).font.size == FONT_A2.size
            assert ws.cell(row=2, column=1).font.italic == FONT_A2.italic

            # Encabezado fila 4 (o primera tabla)
            h_row = 4 if sheet_name != "Movimientos" else 5
            assert ws.row_dimensions[h_row].height == 22
            c_hdr = ws.cell(row=h_row, column=1)
            assert c_hdr.fill.fill_type == FILL_ENCABEZADO.fill_type
            assert c_hdr.font.color.rgb == FONT_ENCABEZADO.color.rgb

            # Fila de datos
            d_row = h_row + 1
            assert ws.row_dimensions[d_row].height == 18
            c_dat = ws.cell(row=d_row, column=1)
            assert c_dat.border.bottom.color.rgb == BORDE_DATOS.bottom.color.rgb

        # Sin siglas ARS ni USD en ninguna celda
        for row in ws.iter_rows(values_only=True):
            for val in row:
                if isinstance(val, str) and not val.startswith("="):
                    assert "ARS" not in val
                    assert "USD" not in val

    # Formatos específicos
    ws_kpis = wb["Números principales"]
    assert ws_kpis.cell(row=5, column=2).number_format == FMT_ARS
    assert ws_kpis.cell(row=7, column=2).number_format == FMT_ARS_DIF
    assert ws_kpis.cell(row=8, column=2).number_format == FMT_PCT

    ws_evo = wb["Evolución"]
    tot_evo = ws_evo.cell(row=11, column=1)
    assert tot_evo.fill.fill_type == FILL_TOTAL.fill_type
    assert tot_evo.border.top.color.rgb == BORDE_TOTAL.top.color.rgb
    assert ws_evo.row_dimensions[11].height == 20

    # Modo solo_usd con ingreso de 1.000 dólares
    b_usd = _crear_billetera(db, user.id, "Billetera USD 2", Moneda.USD)
    _crear_transaccion(db, user.id, b_usd.id, Decimal("1000"), date(2026, 10, 1), tipo=TipoTransaccion.INGRESO, moneda=Moneda.USD, descripcion="Ingreso USD")
    b_usd_xl = generar_excel_analisis(db, user, moneda_modo="solo_usd", bloques="kpis", hoy=hoy)
    wb_usd = openpyxl.load_workbook(BytesIO(b_usd_xl), data_only=False)
    ws_u_kpis = wb_usd["Números principales"]
    assert ws_u_kpis.cell(row=5, column=2).number_format == FMT_USD
    assert "Montos en dólares." in str(ws_u_kpis.cell(row=2, column=1).value)

    # Content-Disposition header en endpoint
    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    try:
        monkeypatch.setattr("app.routers.analisis.hoy_argentina", lambda: hoy)
        resp = client.get("/analisis/exportar-excel?titulo=Resumen%20del%20ciclo")
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "argentum_resumen_del_ciclo_20261114.xlsx" in cd
    finally:
        app.dependency_overrides.clear()


# ── TD7: Validaciones 422 ─────────────────────────────────────────────────────
def test_td7_validaciones_422(db: Session):
    user, _, _, _ = _cargar_datos_base(db)
    otro_user = _crear_usuario(db)
    b_ajena = _crear_billetera(db, otro_user.id, "Billetera Ajena")
    tc_ajena = _crear_tarjeta(db, otro_user.id, b_ajena.id, "Tarjeta Ajena")

    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    try:
        # Bloques inválidos
        r1 = client.get("/analisis/exportar-excel?bloques=kpis,otra")
        assert r1.status_code == 422

        # Ciclo no existente
        r2 = client.get("/analisis/exportar-excel?ciclo_a=ciclo_fantasma")
        assert r2.status_code == 422

        # Billetera ajena
        r3 = client.get(f"/analisis/exportar-excel?billetera_id={b_ajena.id}")
        assert r3.status_code == 422
        assert "No se encontró esa billetera." in r3.text

        # Tarjeta ajena
        r4 = client.get(f"/analisis/exportar-excel?tarjeta_id={tc_ajena.id}")
        assert r4.status_code == 422
        assert "No se encontró esa tarjeta." in r4.text
    finally:
        app.dependency_overrides.clear()


# ── TD8: Verificaciones estáticas ─────────────────────────────────────────────
def test_td8_estatico():
    backend_services = Path(__file__).resolve().parent.parent / "app" / "services"

    prohibidos = ["Font(", "PatternFill(", "Border(", "Side(", "Alignment("]
    defs_prohibidas = ["NAVY_FILL", "HEADER_FONT", "REGULAR_FONT", "FORMAT_MONTO"]

    for py_file in backend_services.glob("analisis_*.py"):
        content = py_file.read_text(encoding="utf-8")
        for p in prohibidos:
            assert p not in content, f"{py_file.name} contiene llamada directa prohibida a {p}"
        for d in defs_prohibidas:
            assert f"{d} =" not in content, f"{py_file.name} define constante de estilo prohibida {d}"
        assert "obtener_movimientos_drilldown" not in content or "limit=100" not in content, f"{py_file.name} usa obtener_movimientos_drilldown con limit=100"
        # TD8 suma: ningún app/services/analisis_*.py arma texto de fórmula concatenando "$" con str(
        assert '"$" + str' not in content and "'$' + str" not in content, f"{py_file.name} concatena '$' + str"
        assert '"$C$"' not in content and "'$C$'" not in content, f"{py_file.name} contiene '$C$'"

    for py_file in backend_services.glob("analisis_excel_*.py"):
        lineas = len(py_file.read_text(encoding="utf-8").splitlines())
        assert lineas <= 600, f"{py_file.name} tiene {lineas} líneas (> 600)"

    # presupuestos_excel_libro.py no define constantes ni funciones movidas
    libro_py = (backend_services / "presupuestos_excel_libro.py").read_text(encoding="utf-8")
    for prefijo in ["FMT_", "FONT_", "FILL_", "SIDE_", "BORDE_", "ALIGN_"]:
        assert f"{prefijo}" not in libro_py, f"presupuestos_excel_libro.py aún contiene {prefijo}"
    for fn in ["configurar_pagina", "ajustar_ancho_columnas", "aplicar_reglas_porcentaje"]:
        assert f"def {fn}" not in libro_py, f"presupuestos_excel_libro.py define función movida {fn}"

    # excel_estilos.py no importa nada de app.services
    estilos_py = (backend_services / "excel_estilos.py").read_text(encoding="utf-8")
    assert "from app.services" not in estilos_py
    assert "import app.services" not in estilos_py


# ── TD9: Presupuestos igualdad ────────────────────────────────────────────────
def test_td9_presupuestos_foto():
    # TD9 reescrito sin rutas de la PC, fija los valores exactos copiados de HEAD
    assert FMT_ARS == '"$" #,##0;-"$" #,##0;"-"'
    assert FMT_ARS_DIF == '"$" #,##0;[Red]-"$" #,##0;"-"'
    assert FMT_USD == '"US$" #,##0.00;-"US$" #,##0.00;"-"'
    assert FMT_USD_DIF == '"US$" #,##0.00;[Red]-"US$" #,##0.00;"-"'
    assert FMT_PCT == '0%'
    assert FMT_ARS_VAR == '[Red]+"$" #,##0;[Color10]-"$" #,##0;"-"'
    assert FMT_USD_VAR == '[Red]+"US$" #,##0.00;[Color10]-"US$" #,##0.00;"-"'
    assert FMT_PCT_VAR == '[Red]+0%;[Color10]-0%;0%'

    def _color_hex(color_val) -> str:
        if hasattr(color_val, "rgb") and color_val.rgb:
            return str(color_val.rgb)[-6:].upper()
        if hasattr(color_val, "value") and color_val.value:
            return str(color_val.value)[-6:].upper()
        return str(color_val)[-6:].upper()

    assert _color_hex(FONT_A1.color) == "1F3864"
    assert _color_hex(FONT_A2.color) == "595959"
    assert _color_hex(FONT_NOTA_SEC.color) == "7F7F7F"
    assert _color_hex(FILL_ENCABEZADO.start_color) == "1F3864"
    assert _color_hex(FILL_TOTAL.start_color) == "EEF3FA"


# ── TD10: Totales y nombres ───────────────────────────────────────────────────
def test_td10_totales_y_nombres(db: Session):
    user, _, _, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 14)

    # 1. Resumen con 6 ciclos
    resumen = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)

    assert resumen["totales_terminados"] == {
        "ingresos": 1500000.0,
        "gastos": 1300000.0,
        "balance": 200000.0,
        "tasa_ahorro_pct": 13.3,
    }

    cats = resumen["desglose_categorias"]
    suma_cats = sum(c["monto"] for c in cats)
    assert resumen["total_desglose"]["monto"] == suma_cats

    # Excel de Categorías y suma de celdas de la fórmula del total
    xl_bytes = generar_excel_analisis(db, user, moneda_modo="solo_ars", bloques="desglose_categorias", hoy=hoy)
    wb = openpyxl.load_workbook(BytesIO(xl_bytes), data_only=False)
    ws_cat = wb["Categorías"]
    tot_row = None
    for r in range(5, ws_cat.max_row + 1):
        if ws_cat.cell(row=r, column=1).value == "Total":
            tot_row = r
            break
    assert tot_row is not None
    formula_val = str(ws_cat.cell(row=tot_row, column=3).value)
    celdas_ref = re.findall(r"C(\d+)", formula_val)
    suma_celdas = sum(ws_cat.cell(row=int(cr), column=3).value for cr in celdas_ref)
    assert resumen["total_desglose"]["monto"] == suma_celdas

    # Serie ciclos de octubre con nombre_corto "Oct 2026"
    c_oct = next(c for c in resumen["serie_ciclos"] if "10-01" in c["fecha_inicio"] or "Oct" in c["etiqueta"])
    assert c_oct["nombre_corto"] == "Oct 2026"

    # 2. Comparativa oct contra sep
    c_sep = next(c for c in resumen["serie_ciclos"] if "09-01" in c["fecha_inicio"] or "Sep" in c["etiqueta"])
    comp = obtener_comparativa_ciclos(
        db, user, moneda_modo="solo_ars", ciclo_a_clave=c_oct["clave"], ciclo_b_clave=c_sep["clave"], hoy=hoy
    )
    assert comp["ciclo_a"]["nombre_corto"] == "Oct 2026"

    totales = comp["totales"]
    suma_a = sum(c["monto_a"] for c in comp["categorias"])
    suma_b = sum(c["monto_b"] for c in comp["categorias"])
    assert totales["monto_a"] == suma_a
    assert totales["monto_b"] == suma_b
    assert totales["diferencia_monto"] == round(suma_a - suma_b, 2)

    # 3. Proyección meses 7
    proy = obtener_proyeccion_compromisos(db, user, moneda_modo="solo_ars", meses=7, hoy=hoy)
    totales_serie = proy["totales_serie"]
    suma_total = round(sum(m["monto_total"] for m in proy["serie_mensual"]), 2)
    assert totales_serie["monto_total"] == suma_total

    for m in proy["serie_mensual"]:
        partes = m["nombre_corto"].split()
        assert len(partes[-1]) == 4
        assert partes[-1].isdigit()


# ── TD11: Límites de líneas y servicio de totales ────────────────────────────
def test_td11_limites_y_totales_service():
    backend_services = Path(__file__).resolve().parent.parent / "app" / "services"
    totales_file = backend_services / "analisis_totales_service.py"

    assert totales_file.exists(), "analisis_totales_service.py no existe"

    # No tiene líneas de más de 100 caracteres
    lineas_totales = totales_file.read_text(encoding="utf-8").splitlines()
    for idx, linea in enumerate(lineas_totales, start=1):
        assert len(linea) <= 100, (
            f"analisis_totales_service.py línea {idx} tiene {len(linea)} chars (> 100): {linea!r}"
        )

    # Ninguna línea suya tiene más de un ': ' de clave de diccionario (regex r'"\w+":\s')
    patron_clave = re.compile(r'"\w+":\s')
    for idx, linea in enumerate(lineas_totales, start=1):
        matches = patron_clave.findall(linea)
        assert len(matches) <= 1, (
            f"analisis_totales_service.py línea {idx} tiene {len(matches)} claves (> 1): {linea!r}"
        )

    # analisis_resumen_service.py no define _calcular_totales_reporte
    # ni ninguna función auxiliar que arme "totales_terminados"
    resumen_text = (backend_services / "analisis_resumen_service.py").read_text(
        encoding="utf-8"
    )
    resumen_ast = ast.parse(resumen_text)
    for node in resumen_ast.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name != "_calcular_totales_reporte", (
                "analisis_resumen_service.py no debe definir _calcular_totales_reporte"
            )
            if node.name != "obtener_resumen_analisis":
                assert "totales_terminados" not in ast.unparse(node), (
                    f"Función {node.name} menciona 'totales_terminados'"
                )

    # ninguna línea de analisis_proyeccion_service.py con "nombre_corto" > 110 chars
    proy_path = backend_services / "analisis_proyeccion_service.py"
    proy_lines = proy_path.read_text(encoding="utf-8").splitlines()
    for idx, linea in enumerate(proy_lines, start=1):
        if "nombre_corto" in linea:
            assert len(linea) <= 110, (
                f"Línea {idx} con 'nombre_corto' tiene {len(linea)} chars (> 110): {linea!r}"
            )

