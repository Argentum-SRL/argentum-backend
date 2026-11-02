# -*- coding: utf-8 -*-
"""
tests/test_presupuestos_excel.py
Pruebas exhaustivas para presupuestos_excel_datos y presupuestos_excel_libro con valores esperados exactos.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

import openpyxl
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    EstadoPresupuesto,
    PeriodoPresupuestoTipo,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import MetodoPago, TipoTransaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services import reintegro_service, transaccion_service
from app.services import dashboard_service
from app.services.presupuesto_service import uso_periodo
from app.services.presupuestos_excel_datos import (
    armar_datos_excel,
    nombre_archivo,
    nombre_hoja_ciclo,
    nombres_unicos,
)
from app.services.presupuestos_excel_libro import (
    FMT_ARS,
    FMT_ARS_DIF,
    FMT_ARS_VAR,
    FMT_PCT,
    FMT_USD,
    armar_libro,
)
from app.utils.fecha import TZ_ARGENTINA


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
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


def test_nombres_hoja_y_desambiguacion():
    assert nombre_hoja_ciclo(date(2026, 8, 30), date(2026, 9, 29)) == "Septiembre 2026"
    assert nombre_hoja_ciclo(date(2026, 10, 30), date(2026, 11, 29)) == "Noviembre 2026"
    assert nombre_hoja_ciclo(date(2026, 10, 16), date(2026, 11, 15)) == "Octubre 2026"
    assert nombre_hoja_ciclo(date(2026, 11, 16), date(2026, 12, 15)) == "Diciembre 2026"
    assert nombre_hoja_ciclo(date(2026, 12, 20), date(2027, 1, 19)) == "Enero 2027"

    assert nombres_unicos(["Octubre 2026", "Octubre 2026", "Noviembre 2026"]) == [
        "Octubre 2026",
        "Octubre 2026 (2)",
        "Noviembre 2026",
    ]


@pytest.fixture(name="datos_escenario_completo")
def datos_escenario_completo_fixture(db: Session):
    usuario_a = Usuario(
        id=uuid4(),
        email="usuario_a@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="30",
        ciclo_ajuste_direccion=None,
        fecha_registro=datetime(2025, 10, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    usuario_b = Usuario(
        id=uuid4(),
        email="usuario_b@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="30",
        ciclo_ajuste_direccion=None,
        fecha_registro=datetime(2025, 10, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    usuario_vacio = Usuario(
        id=uuid4(),
        email="usuario_vacio@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="30",
        ciclo_ajuste_direccion=None,
        fecha_registro=datetime(2025, 10, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add_all([usuario_a, usuario_b, usuario_vacio])
    db.commit()

    b_cuenta = Billetera(
        id=uuid4(),
        usuario_id=usuario_a.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=None,
        es_inversion=False,
    )
    b_dolares = Billetera(
        id=uuid4(),
        usuario_id=usuario_a.id,
        nombre="Dólares",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=None,
        es_inversion=False,
    )
    b_plazo = Billetera(
        id=uuid4(),
        usuario_id=usuario_a.id,
        nombre="Plazo",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=None,
        es_inversion=True,
    )
    b_cuenta_b = Billetera(
        id=uuid4(),
        usuario_id=usuario_b.id,
        nombre="Cuenta B",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=None,
        es_inversion=False,
    )
    db.add_all([b_cuenta, b_dolares, b_plazo, b_cuenta_b])
    db.commit()

    c_alim = Categoria(id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO)
    c_trans = Categoria(id=uuid4(), nombre="Transporte", tipo=TipoCategoria.EGRESO)
    c_ocio = Categoria(id=uuid4(), nombre="Ocio", tipo=TipoCategoria.EGRESO)
    c_trabajo = Categoria(id=uuid4(), nombre="Trabajo", tipo=TipoCategoria.INGRESO)
    db.add_all([c_alim, c_trans, c_ocio, c_trabajo])
    db.commit()

    s_salidas = Subcategoria(id=uuid4(), categoria_id=c_ocio.id, nombre="Salidas")
    s_sueldo = Subcategoria(id=uuid4(), categoria_id=c_trabajo.id, nombre="Sueldo")
    db.add_all([s_salidas, s_sueldo])
    db.commit()

    def crear_tx(usr_id, monto, moneda, tipo, f, cat_id, subcat_id=None, bill_id=b_cuenta.id, desc="tx"):
        return transaccion_service.crear_transaccion(
            db,
            usr_id,
            TransaccionCreate(
                monto=Decimal(str(monto)),
                moneda=moneda,
                tipo=tipo,
                fecha=f,
                descripcion=desc,
                categoria_id=cat_id,
                subcategoria_id=subcat_id,
                billetera_id=bill_id,
                metodo_pago=MetodoPago.DEBITO,
            ),
        )

    # 10/11/2025 Alimentación 60.000
    crear_tx(usuario_a.id, "60000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2025, 11, 10), c_alim.id)
    # 05/12/2025 Transporte 5.000
    crear_tx(usuario_a.id, "5000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2025, 12, 5), c_trans.id)
    # 10/07/2026 ingreso Trabajo > Sueldo 400.000
    crear_tx(usuario_a.id, "400000.00", Moneda.ARS, TipoTransaccion.INGRESO, date(2026, 7, 10), c_trabajo.id, s_sueldo.id)
    # 12/07/2026 Alimentación 80.000
    crear_tx(usuario_a.id, "80000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 7, 12), c_alim.id)
    # 01/09/2026 ingreso 500.000
    crear_tx(usuario_a.id, "500000.00", Moneda.ARS, TipoTransaccion.INGRESO, date(2026, 9, 1), c_trabajo.id)
    # 05/09/2026 Alimentación 120.000 con reintegro recibido de 20.000 el 06/09/2026
    tx_alim_reint = crear_tx(usuario_a.id, "120000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 9, 5), c_alim.id)
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario_a.id,
        tx_alim_reint.id,
        monto=Decimal("20000.00"),
        recibido=True,
        fecha_recibido=date(2026, 9, 6),
    )
    # 10/09/2026 Transporte 30.000
    crear_tx(usuario_a.id, "30000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 9, 10), c_trans.id)
    # 12/09/2026 Ocio > Salidas 20.000
    crear_tx(usuario_a.id, "20000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 9, 12), c_ocio.id, s_salidas.id)
    # 20/09/2026 Ocio US$50 desde "Dólares" (sin subcategoría)
    crear_tx(usuario_a.id, "50.00", Moneda.USD, TipoTransaccion.EGRESO, date(2026, 9, 20), c_ocio.id, bill_id=b_dolares.id)
    # 21/09/2026 Alimentación 999.999 desde "Plazo" (inversión -> excluida)
    crear_tx(usuario_a.id, "999999.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 9, 21), c_alim.id, bill_id=b_plazo.id)
    # 01/10/2026 ingreso 500.000
    crear_tx(usuario_a.id, "500000.00", Moneda.ARS, TipoTransaccion.INGRESO, date(2026, 10, 1), c_trabajo.id)
    # 05/10/2026 Alimentación 150.000
    crear_tx(usuario_a.id, "150000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 10, 5), c_alim.id)
    # 10/10/2026 Transporte 45.000
    crear_tx(usuario_a.id, "45000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 10, 10), c_trans.id)
    # 01/11/2026 Transporte 10.000
    crear_tx(usuario_a.id, "10000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 11, 1), c_trans.id)
    # 05/11/2026 Alimentación 70.000 (futura respecto a hoy=02/11/2026)
    crear_tx(usuario_a.id, "70000.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 11, 5), c_alim.id)

    # Transacciones de Usuario B
    crear_tx(usuario_b.id, "300000.00", Moneda.ARS, TipoTransaccion.INGRESO, date(2026, 9, 15), c_trabajo.id, bill_id=b_cuenta_b.id)
    crear_tx(usuario_b.id, "777777.00", Moneda.ARS, TipoTransaccion.EGRESO, date(2026, 10, 10), c_alim.id, bill_id=b_cuenta_b.id)

    # Presupuestos de Usuario A
    p_comida = Presupuesto(
        id=uuid4(),
        usuario_id=usuario_a.id,
        nombre="Comida",
        monto=Decimal("150000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.MANUAL,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(p_comida)
    db.flush()
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=p_comida.id, categoria_id=c_alim.id, subcategoria_id=None))
    per_comida_1 = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p_comida.id,
        fecha_inicio=date(2026, 8, 30),
        fecha_fin=date(2026, 9, 29),
        monto_limite=Decimal("150000.00"),
    )
    per_comida_2 = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p_comida.id,
        fecha_inicio=date(2026, 9, 30),
        fecha_fin=date(2026, 10, 29),
        monto_limite=Decimal("140000.00"),
    )
    db.add_all([per_comida_1, per_comida_2])

    p_salir = Presupuesto(
        id=uuid4(),
        usuario_id=usuario_a.id,
        nombre="Salir",
        monto=Decimal("25000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.MANUAL,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(p_salir)
    db.flush()
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=p_salir.id, categoria_id=c_ocio.id, subcategoria_id=s_salidas.id))
    per_salir = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p_salir.id,
        fecha_inicio=date(2026, 8, 30),
        fecha_fin=date(2026, 9, 29),
        monto_limite=Decimal("25000.00"),
    )
    db.add(per_salir)

    p_semana = Presupuesto(
        id=uuid4(),
        usuario_id=usuario_a.id,
        nombre="Semana",
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.SEMANAL,
        renovacion=RenovacionPresupuesto.MANUAL,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(p_semana)
    db.flush()
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=p_semana.id, categoria_id=c_trans.id, subcategoria_id=None))
    per_semana = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p_semana.id,
        fecha_inicio=date(2026, 9, 7),
        fecha_fin=date(2026, 9, 13),
        monto_limite=Decimal("20000.00"),
    )
    db.add(per_semana)

    p_viejo = Presupuesto(
        id=uuid4(),
        usuario_id=usuario_a.id,
        nombre="Viejo",
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.MANUAL,
        estado=EstadoPresupuesto.ACTIVO,
        eliminado_en=datetime(2026, 10, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(p_viejo)
    db.flush()
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=p_viejo.id, categoria_id=c_trans.id, subcategoria_id=None))
    per_viejo = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p_viejo.id,
        fecha_inicio=date(2026, 8, 30),
        fecha_fin=date(2026, 9, 29),
        monto_limite=Decimal("10000.00"),
    )
    db.add(per_viejo)
    db.commit()

    return {
        "usuario_a": usuario_a,
        "usuario_b": usuario_b,
        "usuario_vacio": usuario_vacio,
        "p_comida": p_comida,
        "p_salir": p_salir,
        "p_semana": p_semana,
        "per_comida_1": per_comida_1,
        "per_comida_2": per_comida_2,
        "per_salir": per_salir,
        "per_semana": per_semana,
    }


def test_alcance_invalido_y_usuario_vacio(db: Session, datos_escenario_completo):
    hoy = date(2026, 11, 2)
    u_a = datos_escenario_completo["usuario_a"]
    u_vacio = datos_escenario_completo["usuario_vacio"]

    with pytest.raises(ValueError, match="El alcance tiene que ser 12 o todo."):
        armar_datos_excel(db, u_a, "13", hoy=hoy)

    assert armar_datos_excel(db, u_vacio, "12", hoy=hoy) is None
    assert armar_datos_excel(db, u_vacio, "todo", hoy=hoy) is None


def test_libro_usuario_a(db: Session, datos_escenario_completo):
    hoy = date(2026, 11, 2)
    u_a = datos_escenario_completo["usuario_a"]

    # 1. Alcance "12"
    datos_12 = armar_datos_excel(db, u_a, "12", hoy=hoy)
    assert datos_12 is not None
    assert nombre_archivo(datos_12) == "Argentum_presupuestos_2025-11-30_2026-11-29.xlsx"

    bytes_12 = armar_libro(datos_12)
    wb_12 = openpyxl.load_workbook(io.BytesIO(bytes_12))

    nombres_esperados_12 = [
        "Comparativo", "Anual", "Diciembre 2025", "Julio 2026",
        "Septiembre 2026", "Octubre 2026", "Noviembre 2026", "Cómo leerlo"
    ]
    assert [s.title for s in wb_12.worksheets] == nombres_esperados_12
    assert wb_12.active.title == "Comparativo"

    # Verificación general en todas las hojas: sin freeze_panes, sin auto_filter, sin celdas combinadas, showGridLines False
    for ws in wb_12.worksheets:
        assert ws.freeze_panes is None
        assert ws.auto_filter.ref is None
        assert len(ws.merged_cells.ranges) == 0
        assert ws.views.sheetView[0].showGridLines is False
        assert ws.views.sheetView[0].zoomScale == 100

    # 2. Alcance "todo"
    datos_todo = armar_datos_excel(db, u_a, "todo", hoy=hoy)
    assert datos_todo is not None
    bytes_todo = armar_libro(datos_todo)
    wb_todo = openpyxl.load_workbook(io.BytesIO(bytes_todo))

    nombres_esperados_todo = [
        "Comparativo", "Anual", "Noviembre 2025", "Diciembre 2025",
        "Julio 2026", "Septiembre 2026", "Octubre 2026", "Noviembre 2026", "Cómo leerlo"
    ]
    assert [s.title for s in wb_todo.worksheets] == nombres_esperados_todo

    # 3. Comprobación detallada de Hoja "Septiembre 2026"
    ws_sep = wb_12["Septiembre 2026"]
    assert ws_sep["A1"].value == "Septiembre 2026"
    assert ws_sep["A1"].font.size == 16 and ws_sep["A1"].font.bold is True
    assert ws_sep["A2"].value == "Ciclo del 30/08/2026 al 29/09/2026"

    # Resumen
    assert ws_sep["A4"].value == "Resumen"
    assert ws_sep["B4"].value == "Pesos"
    assert ws_sep["C4"].value == "Dólares"
    assert ws_sep["B5"].value == 500000
    assert ws_sep["C5"].value == 0
    assert ws_sep["B6"].value == 150000
    assert ws_sep["C6"].value == 50
    assert ws_sep["B7"].value == "=B5-B6"
    assert ws_sep["C7"].value == "=C5-C6"
    assert ws_sep["B8"].value == '=IF(B5>0,B7/B5,"")'
    assert ws_sep["C8"].value == '=IF(C5>0,C7/C5,"")'

    # Tus presupuestos
    assert ws_sep["A11"].value == "Tus presupuestos"
    assert ws_sep["A12"].value == "Cada fila es un presupuesto que armaste en Argentum, con lo mismo que ves en la app."
    assert [ws_sep.cell(row=13, column=i).value for i in range(1, 8)] == [
        "Presupuesto", "Incluye", "Período", "Límite", "Gastado", "Te queda", "% usado"
    ]
    # Comida
    assert ws_sep["A14"].value == "Comida"
    assert ws_sep["B14"].value == "Alimentación"
    assert ws_sep["C14"].value == "Todo el ciclo"
    assert ws_sep["D14"].value == 150000
    assert ws_sep["E14"].value == 100000
    assert ws_sep["F14"].value == "=D14-E14"
    assert ws_sep["G14"].value == '=IF(D14=0,"",E14/D14)'

    # Salir
    assert ws_sep["A15"].value == "Salir"
    assert ws_sep["B15"].value == "Ocio > Salidas"
    assert ws_sep["C15"].value == "Todo el ciclo"
    assert ws_sep["D15"].value == 25000
    assert ws_sep["E15"].value == 20000

    # Semana
    assert ws_sep["A16"].value == "Semana"
    assert ws_sep["B16"].value == "Transporte"
    assert ws_sep["C16"].value == "Del 07/09/2026 al 13/09/2026"
    assert ws_sep["D16"].value == 20000
    assert ws_sep["E16"].value == 30000

    # "Viejo" no aparece en ninguna hoja
    for ws_check in wb_12.worksheets:
        for r_idx in range(1, ws_check.max_row + 1):
            assert ws_check.cell(row=r_idx, column=1).value != "Viejo"

    # En qué gastaste
    assert ws_sep["A19"].value == "En qué gastaste"
    assert ws_sep["A20"].value == "Gastos en pesos por categoría, de mayor a menor, comparados con el mes anterior."
    assert [ws_sep.cell(row=21, column=i).value for i in range(1, 6)] == [
        "Categoría", "Gastado", "% del total", "Mes anterior", "Diferencia"
    ]
    assert ws_sep["A22"].value == "Alimentación"
    assert ws_sep["B22"].value == 100000
    assert ws_sep["C22"].value == '=IF($B$25=0,"",B22/$B$25)'
    assert ws_sep["D22"].value == 0
    assert ws_sep["E22"].value == "=B22-D22"

    assert ws_sep["A23"].value == "Transporte"
    assert ws_sep["B23"].value == 30000
    assert ws_sep["D23"].value == 0

    assert ws_sep["A24"].value == "Ocio"
    assert ws_sep["B24"].value == 20000
    assert ws_sep["D24"].value == 0

    assert ws_sep["A25"].value == "Total"
    assert ws_sep["B25"].value == "=SUM(B22:B24)"
    assert ws_sep["C25"].value == "=SUM(C22:C24)"
    assert ws_sep["D25"].value == "=SUM(D22:D24)"
    assert ws_sep["E25"].value == "=SUM(E22:E24)"

    # Detalle por subcategoría
    assert ws_sep["A28"].value == "Detalle por subcategoría"
    assert [ws_sep.cell(row=29, column=i).value for i in range(1, 4)] == [
        "Categoría", "Subcategoría", "Gastado"
    ]
    assert ws_sep["A30"].value == "Alimentación"
    assert ws_sep["B30"].value == "Sin subcategoría"
    assert ws_sep["C30"].value == 100000

    assert ws_sep["A31"].value == "Transporte"
    assert ws_sep["B31"].value == "Sin subcategoría"
    assert ws_sep["C31"].value == 30000

    assert ws_sep["A32"].value == "Ocio"
    assert ws_sep["B32"].value == "Salidas"
    assert ws_sep["C32"].value == 20000

    # En dólares
    assert ws_sep["A35"].value == "En dólares"
    assert [ws_sep.cell(row=36, column=i).value for i in range(1, 3)] == [
        "Categoría", "Gastado"
    ]
    assert ws_sep["A37"].value == "Ocio"
    assert ws_sep["B37"].value == 50
    assert ws_sep["A38"].value == "Total"
    assert ws_sep["B38"].value == "=SUM(B37:B37)"

    # Anchos de columna en Septiembre 2026
    assert ws_sep.column_dimensions["A"].width == 18
    assert ws_sep.column_dimensions["B"].width == 19
    assert ws_sep.column_dimensions["C"].width == 31

    # Formatos de número
    assert ws_sep["B5"].number_format == FMT_ARS
    assert ws_sep["B7"].number_format == FMT_ARS_DIF
    assert ws_sep["B8"].number_format == FMT_PCT
    assert ws_sep["C5"].number_format == FMT_USD
    assert ws_sep["D14"].number_format == FMT_ARS
    assert ws_sep["F14"].number_format == FMT_ARS_DIF
    assert ws_sep["G14"].number_format == FMT_PCT
    assert ws_sep["C22"].number_format == FMT_PCT
    assert ws_sep["E22"].number_format == FMT_ARS_VAR
    assert ws_sep["B37"].number_format == FMT_USD

    # Estilos
    fill_a13 = ws_sep["A13"].fill.start_color.rgb
    assert fill_a13.endswith("1F3864")
    font_a13 = ws_sep["A13"].font.color.rgb
    assert font_a13.endswith("FFFFFF")
    assert ws_sep["A13"].font.bold is True

    assert ws_sep["A25"].font.bold is True
    fill_a25 = ws_sep["A25"].fill.start_color.rgb
    assert fill_a25.endswith("EEF3FA")

    # B22: borde inferior fino, sin izquierdo ni derecho
    assert ws_sep["B22"].border.bottom.style == "thin"
    assert ws_sep["B22"].border.left is None or ws_sep["B22"].border.left.style is None
    assert ws_sep["B22"].border.right is None or ws_sep["B22"].border.right.style is None

    # G14: regla de formato condicional
    cf_rules = ws_sep.conditional_formatting
    assert len(cf_rules) > 0

    # 4. Octubre 2026
    ws_oct = wb_12["Octubre 2026"]
    assert ws_oct["B5"].value == 500000
    assert ws_oct["C5"].value == 0
    assert ws_oct["B6"].value == 195000
    assert ws_oct["C6"].value == 0
    assert ws_oct["A11"].value == "Tus presupuestos"
    assert ws_oct["A14"].value == "Comida"
    assert ws_oct["B14"].value == "Alimentación"
    assert ws_oct["C14"].value == "Todo el ciclo"
    assert ws_oct["D14"].value == 140000
    assert ws_oct["E14"].value == 150000

    assert ws_oct["A17"].value == "En qué gastaste"
    assert ws_oct["A20"].value == "Alimentación"
    assert ws_oct["B20"].value == 150000
    assert ws_oct["D20"].value == 100000

    assert ws_oct["A21"].value == "Transporte"
    assert ws_oct["B21"].value == 45000
    assert ws_oct["D21"].value == 30000

    assert ws_oct["A22"].value == "Ocio"
    assert ws_oct["B22"].value == 0
    assert ws_oct["D22"].value == 20000
    assert ws_oct["A23"].value == "Total"

    assert ws_oct["A26"].value == "Detalle por subcategoría"
    assert ws_oct["A28"].value == "Alimentación"
    assert ws_oct["B28"].value == "Sin subcategoría"
    assert ws_oct["C28"].value == 150000
    assert ws_oct["A29"].value == "Transporte"
    assert ws_oct["B29"].value == "Sin subcategoría"
    assert ws_oct["C29"].value == 45000

    for r_idx in range(1, ws_oct.max_row + 1):
        assert ws_oct.cell(row=r_idx, column=1).value != "En dólares"

    # 5. Noviembre 2026
    ws_nov = wb_12["Noviembre 2026"]
    assert ws_nov["A2"].value == "Ciclo del 30/10/2026 al 29/11/2026 (en curso: datos hasta el 02/11/2026)"
    assert ws_nov["B6"].value == 10000
    assert ws_nov["C6"].value == 0
    assert ws_nov["A11"].value == "En qué gastaste"
    assert ws_nov["A14"].value == "Transporte"
    assert ws_nov["B14"].value == 10000
    assert ws_nov["D14"].value == 45000
    assert ws_nov["A15"].value == "Alimentación"
    assert ws_nov["B15"].value == 0
    assert ws_nov["D15"].value == 150000

    # 6. Julio 2026
    ws_jul = wb_12["Julio 2026"]
    assert ws_jul["B5"].value == 400000
    assert ws_jul["B6"].value == 80000

    # 7. Comparativo
    ws_comp = wb_12["Comparativo"]
    assert ws_comp["A1"].value == "Comparativo por mes"
    assert ws_comp["A2"].value == "Del 30/11/2025 al 29/11/2026. Montos en pesos. Cada columna es un ciclo de cobro a cobro."
    assert [ws_comp.cell(row=4, column=i).value for i in range(1, 9)] == [
        "Resumen", "Dic 2025", "Jul 2026", "Sep 2026", "Oct 2026", "Nov 2026", "Total", "Promedio"
    ]
    # Fila 5: Ingresos
    assert [ws_comp.cell(row=5, column=i).value for i in range(1, 9)] == [
        "Ingresos", 0, 400000, 500000, 500000, 0, "=SUM(B5:F5)", "=AVERAGE(B5:F5)"
    ]
    # Fila 6: Gastos
    assert [ws_comp.cell(row=6, column=i).value for i in range(1, 9)] == [
        "Gastos", "=B16", "=C16", "=D16", "=E16", "=F16", "=SUM(B6:F6)", "=AVERAGE(B6:F6)"
    ]
    # Fila 7: Te sobró
    assert [ws_comp.cell(row=7, column=i).value for i in range(1, 9)] == [
        "Te sobró", "=B5-B6", "=C5-C6", "=D5-D6", "=E5-E6", "=F5-F6", "=SUM(B7:F7)", "=AVERAGE(B7:F7)"
    ]
    # Fila 8: % que ahorraste
    assert [ws_comp.cell(row=8, column=i).value for i in range(1, 9)] == [
        "% que ahorraste",
        '=IF(B5>0,B7/B5,"")',
        '=IF(C5>0,C7/C5,"")',
        '=IF(D5>0,D7/D5,"")',
        '=IF(E5>0,E7/E5,"")',
        '=IF(F5>0,F7/F5,"")',
        '=IF(G5>0,G7/G5,"")',
        None,
    ]

    # Gastos por categoría
    assert ws_comp["A11"].value == "Gastos por categoría"
    assert [ws_comp.cell(row=12, column=i).value for i in range(1, 10)] == [
        "Categoría", "Dic 2025", "Jul 2026", "Sep 2026", "Oct 2026", "Nov 2026", "Total", "Promedio", "Mes de mayor gasto"
    ]
    assert [ws_comp.cell(row=13, column=i).value for i in range(1, 10)] == [
        "Alimentación", 0, 80000, 100000, 150000, 0, "=SUM(B13:F13)", "=AVERAGE(B13:F13)",
        '=IF(MAX(B13:F13)<=0,"-",INDEX($B$12:$F$12,MATCH(MAX(B13:F13),B13:F13,0)))'
    ]
    assert [ws_comp.cell(row=14, column=i).value for i in range(1, 7)] == [
        "Transporte", 5000, 0, 30000, 45000, 10000
    ]
    assert [ws_comp.cell(row=15, column=i).value for i in range(1, 7)] == [
        "Ocio", 0, 0, 20000, 0, 0
    ]
    assert ws_comp["A16"].value == "Total de gastos"
    assert ws_comp["B16"].value == "=SUM(B13:B15)"
    assert ws_comp["F16"].value == "=SUM(F13:F15)"
    assert ws_comp["G16"].value == "=SUM(B16:F16)"
    assert ws_comp["H16"].value == "=AVERAGE(B16:F16)"
    assert ws_comp["I16"].value == '=IF(MAX(B16:F16)<=0,"-",INDEX($B$12:$F$12,MATCH(MAX(B16:F16),B16:F16,0)))'

    # Presupuestos de cobro a cobro
    assert ws_comp["A19"].value == "Presupuestos de cobro a cobro"
    assert ws_comp["A20"].value == "Qué parte de cada presupuesto usaste en cada mes. Más de 100% es que te pasaste."
    assert [ws_comp.cell(row=21, column=i).value for i in range(1, 8)] == [
        "Presupuesto", "Dic 2025", "Jul 2026", "Sep 2026", "Oct 2026", "Nov 2026", "Veces que te pasaste"
    ]
    assert [ws_comp.cell(row=22, column=i).value for i in range(1, 8)] == [
        "Comida", None, None, 0.6667, 1.0714, None, '=COUNTIF(B22:F22,">1")'
    ]
    assert [ws_comp.cell(row=23, column=i).value for i in range(1, 8)] == [
        "Salir", None, None, 0.8, None, None, '=COUNTIF(B23:F23,">1")'
    ]

    # En dólares
    assert ws_comp["A26"].value == "En dólares"
    assert [ws_comp.cell(row=27, column=i).value for i in range(1, 10)] == [
        "Categoría", "Dic 2025", "Jul 2026", "Sep 2026", "Oct 2026", "Nov 2026", "Total", "Promedio", "Mes de mayor gasto"
    ]
    assert [ws_comp.cell(row=28, column=i).value for i in range(1, 10)] == [
        "Ocio", 0, 0, 50, 0, 0, "=SUM(B28:F28)", "=AVERAGE(B28:F28)",
        '=IF(MAX(B28:F28)<=0,"-",INDEX($B$27:$F$27,MATCH(MAX(B28:F28),B28:F28,0)))'
    ]
    assert ws_comp["A29"].value == "Total de gastos"

    # Con "todo"
    ws_comp_todo = wb_todo["Comparativo"]
    assert [ws_comp_todo.cell(row=4, column=i).value for i in range(1, 4)] == [
        "Resumen", "Nov 2025", "Dic 2025"
    ]
    assert ws_comp_todo["B13"].value == 60000

    # 8. Anual
    ws_anual = wb_12["Anual"]
    assert ws_anual["A1"].value == "Comparativo anual"
    assert ws_anual["A2"].value == "Suma de los meses con datos de cada año. Montos en pesos."
    assert [ws_anual.cell(row=4, column=i).value for i in range(1, 6)] == [
        "Resumen", "2025", "2026", "Diferencia 2026 vs 2025", "Variación 2026 vs 2025"
    ]
    assert [ws_anual.cell(row=5, column=i).value for i in range(1, 6)] == [
        "Meses con datos", 1, 4, None, None
    ]
    assert [ws_anual.cell(row=6, column=i).value for i in range(1, 6)] == [
        "Ingresos", "=SUM(Comparativo!B5:B5)", "=SUM(Comparativo!C5:F5)", "=C6-B6", '=IF(B6>0,(C6-B6)/B6,"")'
    ]
    assert [ws_anual.cell(row=7, column=i).value for i in range(1, 6)] == [
        "Gastos", "=SUM(Comparativo!B6:B6)", "=SUM(Comparativo!C6:F6)", "=C7-B7", '=IF(B7>0,(C7-B7)/B7,"")'
    ]
    assert [ws_anual.cell(row=8, column=i).value for i in range(1, 6)] == [
        "Te sobró", "=B6-B7", "=C6-C7", "=C8-B8", '=IF(B8>0,(C8-B8)/B8,"")'
    ]
    assert [ws_anual.cell(row=9, column=i).value for i in range(1, 6)] == [
        "% que ahorraste", '=IF(B6>0,B8/B6,"")', '=IF(C6>0,C8/C6,"")', None, None
    ]

    # Gastos por categoría Anual
    assert ws_anual["A12"].value == "Gastos por categoría"
    assert [ws_anual.cell(row=13, column=i).value for i in range(1, 6)] == [
        "Categoría", "2025", "2026", "Diferencia 2026 vs 2025", "Variación 2026 vs 2025"
    ]
    assert [ws_anual.cell(row=14, column=i).value for i in range(1, 6)] == [
        "Alimentación", "=SUM(Comparativo!B13:B13)", "=SUM(Comparativo!C13:F13)", "=C14-B14", '=IF(B14>0,(C14-B14)/B14,"")'
    ]
    assert ws_anual["A15"].value == "Transporte"
    assert ws_anual["A16"].value == "Ocio"
    assert [ws_anual.cell(row=17, column=i).value for i in range(1, 6)] == [
        "Total de gastos", "=SUM(B14:B16)", "=SUM(C14:C16)", "=C17-B17", '=IF(B17>0,(C17-B17)/B17,"")'
    ]

    # En dólares Anual
    assert ws_anual["A20"].value == "En dólares"
    assert [ws_anual.cell(row=21, column=i).value for i in range(1, 6)] == [
        "Categoría", "2025", "2026", "Diferencia 2026 vs 2025", "Variación 2026 vs 2025"
    ]
    assert [ws_anual.cell(row=22, column=i).value for i in range(1, 6)] == [
        "Ocio", "=SUM(Comparativo!B28:B28)", "=SUM(Comparativo!C28:F28)", "=C22-B22", '=IF(B22>0,(C22-B22)/B22,"")'
    ]

    # Formatos Anual
    assert ws_anual["D14"].number_format == FMT_ARS_VAR
    assert ws_anual["E14"].number_format == '[Red]+0%;[Color10]-0%;0%'

    # Con "todo" en Anual
    ws_anual_todo = wb_todo["Anual"]
    assert [ws_anual_todo.cell(row=5, column=i).value for i in range(1, 4)] == [
        "Meses con datos", 2, 4
    ]
    assert [ws_anual_todo.cell(row=14, column=i).value for i in range(1, 4)] == [
        "Alimentación", "=SUM(Comparativo!B13:C13)", "=SUM(Comparativo!D13:G13)"
    ]

    # 9. Cómo leerlo
    ws_leer = wb_12["Cómo leerlo"]
    assert ws_leer["A1"].value == "Cómo leer este Excel"
    assert ws_leer["A1"].font.size == 16 and ws_leer["A1"].font.bold is True
    assert ws_leer.column_dimensions["A"].width == 110

    lineas_esperadas = [
        "Comparativo: cada columna es un ciclo tuyo, de cobro a cobro. Arriba, cuánto entró, cuánto salió y cuánto te sobró; abajo, cuánto gastaste en cada categoría (el celeste más fuerte marca el mes en que más gastaste) y qué parte de cada presupuesto usaste.",
        "Anual: lo mismo sumado por año, con la diferencia contra el año anterior. Fijate en «Meses con datos»: un año con pocos meses suma menos.",
        "Una hoja por mes: el resumen del ciclo, tus presupuestos tal como los armaste en Argentum, en qué gastaste por categoría comparado con el mes anterior y el detalle por subcategoría.",
        "Qué cuenta como gasto: lo que pagaste con tus cuentas y tarjetas. No cuentan el pago del resumen de la tarjeta, las transferencias entre tus cuentas, lo que pasa a tus ahorros, metas o inversiones, ni la plata que solo pasó por tu cuenta. Los reintegros ya están descontados.",
        "Compras con tarjeta: en un pago cuentan el día de la compra; en cuotas, cada cuota cuenta en el mes en que vence.",
        "Pesos y dólares van siempre separados: los dólares tienen su propia tabla.",
        "Los totales, las diferencias y los porcentajes son fórmulas: si cambiás un número, se recalculan solos.",
        "Solo aparecen los meses con movimientos. Los números son los mismos que ves en Argentum.",
    ]
    for idx_l, texto_l in enumerate(lineas_esperadas):
        r_l = 3 + idx_l
        assert ws_leer[f"A{r_l}"].value == texto_l
        assert ws_leer[f"A{r_l}"].alignment.wrap_text is True


def test_libro_usuario_b(db: Session, datos_escenario_completo):
    hoy = date(2026, 11, 2)
    u_b = datos_escenario_completo["usuario_b"]

    datos_b = armar_datos_excel(db, u_b, "12", hoy=hoy)
    assert datos_b is not None
    assert datos_b.tiene_dolares is False

    wb_b = openpyxl.load_workbook(io.BytesIO(armar_libro(datos_b)))
    assert [s.title for s in wb_b.worksheets] == [
        "Comparativo", "Anual", "Septiembre 2026", "Octubre 2026", "Cómo leerlo"
    ]

    ws_sep = wb_b["Septiembre 2026"]
    assert ws_sep["A4"].value == "Resumen"
    assert ws_sep["B4"].value == "Pesos"
    assert ws_sep["C4"].value is None
    assert ws_sep["B5"].value == 300000
    assert ws_sep["A11"].value == "En qué gastaste"
    assert ws_sep["A12"].value == "No hubo gastos en este ciclo."

    for r in range(1, ws_sep.max_row + 1):
        assert ws_sep.cell(row=r, column=1).value != "Detalle por subcategoría"
        assert ws_sep.cell(row=r, column=1).value != "En dólares"

    ws_oct = wb_b["Octubre 2026"]
    assert ws_oct["A14"].value == "Alimentación"
    assert ws_oct["B14"].value == 777777
    assert ws_oct["D14"].value == 0

    # En ningún libro de B hay "En dólares" ni "Dólares"; en ningún libro de A aparece 777777 ni 300000
    for ws in wb_b.worksheets:
        for r in range(1, ws.max_row + 1):
            assert ws.cell(row=r, column=1).value != "En dólares"
            for c in range(1, ws.max_column + 1):
                assert ws.cell(row=r, column=c).value != "Dólares"

    wb_a = openpyxl.load_workbook(io.BytesIO(armar_libro(armar_datos_excel(db, datos_escenario_completo["usuario_a"], "12", hoy=hoy))))
    for ws in wb_a.worksheets:
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                val = ws.cell(row=r, column=c).value
                assert val != 777777
                assert val != 300000


def test_igualdad_con_la_web(db: Session, datos_escenario_completo, monkeypatch):
    hoy = date(2026, 11, 2)
    u_a = datos_escenario_completo["usuario_a"]

    monkeypatch.setattr(dashboard_service, "hoy_argentina", lambda: hoy)

    datos_todo = armar_datos_excel(db, u_a, "todo", hoy=hoy)
    assert datos_todo is not None
    wb_todo = openpyxl.load_workbook(io.BytesIO(armar_libro(datos_todo)))

    for c in datos_todo.ciclos:
        ws = wb_todo[c.nombre_hoja]
        balance_web = dashboard_service.calcular_balance_ciclo(
            db,
            u_a,
            fecha_desde_override=c.inicio,
            fecha_hasta_override=c.fin,
        )
        assert ws["B5"].value == balance_web["ars"]["ingresos"]
        assert ws["B6"].value == balance_web["ars"]["egresos"]
        assert ws["C5"].value == balance_web["usd"]["ingresos"]
        assert ws["C6"].value == balance_web["usd"]["egresos"]

    # Cada Gastado de "Tus presupuestos" igual a uso_periodo(...).mostrado
    ws_sep = wb_todo["Septiembre 2026"]
    p_comida = datos_escenario_completo["p_comida"]
    per_comida_1 = datos_escenario_completo["per_comida_1"]
    uso_c1 = uso_periodo(db, p_comida, per_comida_1, hoy=hoy)
    assert ws_sep["E14"].value == float(uso_c1.mostrado)

    p_salir = datos_escenario_completo["p_salir"]
    per_salir = datos_escenario_completo["per_salir"]
    uso_s = uso_periodo(db, p_salir, per_salir, hoy=hoy)
    assert ws_sep["E15"].value == float(uso_s.mostrado)

    p_semana = datos_escenario_completo["p_semana"]
    per_semana = datos_escenario_completo["per_semana"]
    uso_sem = uso_periodo(db, p_semana, per_semana, hoy=hoy)
    assert ws_sep["E16"].value == float(uso_sem.mostrado)

    # En Comparativo, D22 igual a (uso_periodo de Comida en el período de septiembre).mostrado / 150000 redondeado a 4 decimales
    limite = Decimal("150000")
    pct_esperado = float((Decimal(str(uso_c1.mostrado)) / limite).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
    datos_12 = armar_datos_excel(db, u_a, "12", hoy=hoy)
    wb_12 = openpyxl.load_workbook(io.BytesIO(armar_libro(datos_12)))
    assert wb_12["Comparativo"]["D22"].value == pct_esperado


def test_solo_lectura(db: Session, datos_escenario_completo):
    hoy = date(2026, 11, 2)
    u_a = datos_escenario_completo["usuario_a"]

    datos_todo = armar_datos_excel(db, u_a, "todo", hoy=hoy)
    _ = armar_libro(datos_todo)

    assert len(db.new) == 0
    assert len(db.dirty) == 0
    assert len(db.deleted) == 0

    cnt_periodos = db.execute(select(func.count(PeriodoPresupuesto.id))).scalar()
    assert cnt_periodos == 5


def test_nombres_hoja_longitud_y_anchos(db: Session, datos_escenario_completo):
    hoy = date(2026, 11, 2)
    u_a = datos_escenario_completo["usuario_a"]

    datos_todo = armar_datos_excel(db, u_a, "todo", hoy=hoy)
    wb = openpyxl.load_workbook(io.BytesIO(armar_libro(datos_todo)))

    caracteres_prohibidos = [":", "\\", "/", "?", "*", "[", "]"]

    for ws in wb.worksheets:
        assert len(ws.title) <= 31
        for ch in caracteres_prohibidos:
            assert ch not in ws.title

        for col_letter, dim in ws.column_dimensions.items():
            if ws.title == "Cómo leerlo" and col_letter == "A":
                assert dim.width == 110
            else:
                assert 12 <= dim.width <= 50
