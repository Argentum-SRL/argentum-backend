# -*- coding: utf-8 -*-
"""
tests/test_analisis_n3.py
Pruebas para fase_n3_analisis_visual (TN23 a TN27) según decisión 13/11/2026 (2A, 3B, 4A).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
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
from app.services.analisis_excel_service import generar_excel_analisis
from app.services.analisis_resumen_service import obtener_resumen_analisis


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


def _crear_billetera(db: Session, usuario_id: UUID, moneda: Moneda = Moneda.ARS) -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre="Billetera ARS",
        moneda=moneda,
        es_inversion=False,
    )
    db.add(b)
    db.commit()
    return b


def _crear_tarjeta(db: Session, usuario_id: UUID, billetera_id: UUID) -> TarjetaCredito:
    tc = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        nombre="Visa Galicia",
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
) -> Transaccion:
    t = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        tipo=tipo,
        monto=monto,
        moneda=moneda,
        fecha=fecha,
        descripcion="Tx de prueba",
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
    Datos base para test_analisis_n3:
    - septiembre: ingreso 1.500.000 el 05/09 y gasto 100.000 el 10/09
    - octubre: gastos 250.000 el 05/10 y 950.000 el 20/10
    - noviembre: gasto 125.000 el 05/11
    - junio, julio y agosto: sin ninguna transacción.
    """
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_ars = _crear_billetera(db, user.id, Moneda.ARS)
    tc = _crear_tarjeta(db, user.id, b_ars.id)
    cat = _crear_categoria(db, "General")

    # Septiembre 2026
    _crear_transaccion(db, user.id, b_ars.id, Decimal("1500000"), date(2026, 9, 5), tipo=TipoTransaccion.INGRESO)
    _crear_transaccion(db, user.id, b_ars.id, Decimal("100000"), date(2026, 9, 10), categoria_id=cat.id)

    # Octubre 2026
    _crear_transaccion(db, user.id, b_ars.id, Decimal("250000"), date(2026, 10, 5), categoria_id=cat.id, tarjeta_id=tc.id)
    _crear_transaccion(db, user.id, b_ars.id, Decimal("950000"), date(2026, 10, 20), categoria_id=cat.id)

    # Noviembre 2026
    _crear_transaccion(db, user.id, b_ars.id, Decimal("125000"), date(2026, 11, 5), categoria_id=cat.id)

    return user, b_ars, tc, cat


# ── TN23 (2A): Números de arriba cuentan solo ciclos terminados ───────────────
def test_tn23_kpis_ciclos_terminados(db: Session):
    """
    TN23 (2A):
    - 6 ciclos solo_ars → ingresos_totales 1.500.000, gastos_totales 1.300.000,
      balance_neto 200.000, tasa_ahorro_pct 13.3, alcance "terminados".
    - 1 ciclo → gastos_totales 125.000, ingresos_totales 0, balance_neto -125.000,
      tasa null, alcance "en_curso".
    """
    user, _, _, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 13)

    # 1. 6 ciclos
    res_6 = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    kpis_6 = res_6["kpis"]

    assert kpis_6["ingresos_totales"] == 1500000.0
    assert kpis_6["gastos_totales"] == 1300000.0
    assert kpis_6["balance_neto"] == 200000.0
    assert kpis_6["tasa_ahorro_pct"] == 13.3
    assert kpis_6["alcance"] == "terminados"

    # 2. 1 ciclo (ciclo actual en curso)
    res_1 = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=1, hoy=hoy)
    kpis_1 = res_1["kpis"]

    assert kpis_1["gastos_totales"] == 125000.0
    assert kpis_1["ingresos_totales"] == 0.0
    assert kpis_1["balance_neto"] == -125000.0
    assert kpis_1["tasa_ahorro_pct"] is None
    assert kpis_1["alcance"] == "en_curso"


# ── TN24 (3B): Ciclo sin datos ────────────────────────────────────────────────
def test_tn24_ciclos_sin_datos(db: Session):
    """
    TN24 (3B):
    - 6 ciclos → sin_datos true en jun, jul y ago y false en sep, oct y nov;
      promedio_gasto_ciclo 650.000.
    - Con filtro de la tarjeta de un gasto de octubre: sin_datos igual que sin filtro.
    - Un usuario con solo un gasto de US$10 el 05/08: agosto sin_datos false también en solo_ars.
    - Un usuario sin ninguna transacción: promedios null.
    """
    user, _, tc, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 13)

    # 1. 6 ciclos sin filtro
    res_6 = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    serie = res_6["serie_ciclos"]
    assert len(serie) == 6

    # jun, jul, ago, sep, oct, nov
    assert serie[0]["sin_datos"] is True   # Junio
    assert serie[1]["sin_datos"] is True   # Julio
    assert serie[2]["sin_datos"] is True   # Agosto
    assert serie[3]["sin_datos"] is False  # Septiembre
    assert serie[4]["sin_datos"] is False  # Octubre
    assert serie[5]["sin_datos"] is False  # Noviembre
    assert res_6["kpis"]["promedio_gasto_ciclo"] == 650000.0

    # 2. Con filtro de tarjeta (solo tiene gasto en octubre)
    res_tc = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6, tarjeta_id=tc.id, hoy=hoy)
    serie_tc = res_tc["serie_ciclos"]
    assert serie_tc[0]["sin_datos"] is True
    assert serie_tc[1]["sin_datos"] is True
    assert serie_tc[2]["sin_datos"] is True
    assert serie_tc[3]["sin_datos"] is False
    assert serie_tc[4]["sin_datos"] is False
    assert serie_tc[5]["sin_datos"] is False

    # 3. Usuario con solo un gasto de US$10 el 05/08: agosto sin_datos false también en solo_ars
    user_usd = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_usd = _crear_billetera(db, user_usd.id, Moneda.USD)
    _crear_transaccion(db, user_usd.id, b_usd.id, Decimal("10"), date(2026, 8, 5), moneda=Moneda.USD)

    res_usd = obtener_resumen_analisis(db, user_usd, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    serie_u = res_usd["serie_ciclos"]
    # En 6 ciclos: jun(0), jul(1), ago(2), sep(3), oct(4), nov(5)
    assert serie_u[2]["sin_datos"] is False

    # 4. Usuario sin ninguna transacción: promedios null
    user_vacio = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    res_vacio = obtener_resumen_analisis(db, user_vacio, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    assert res_vacio["kpis"]["promedio_gasto_ciclo"] is None
    assert res_vacio["kpis"]["promedio_ingreso_ciclo"] is None


# ── TN25 (4A): Frase resumen determinística ──────────────────────────────────
def test_tn25_frase_resumen(db: Session):
    """
    TN25 (4A):
    - 6 ciclos → frase_resumen "Números de los ciclos terminados. En estos 13 días gastaste 50% menos que en los mismos días del ciclo anterior."
    - 1 ciclo → "Números de lo que va del ciclo (13 de 30 días). En estos 13 días gastaste 50% menos que en los mismos días del ciclo anterior."
    - con filtro de categoría → empieza con "Con este filtro solo se ven gastos."
    - con el gasto de noviembre en 375.000 → "50% más que"
    - con 250.000 → "lo mismo que"
    - sin gastos en octubre → solo la primera parte.
    """
    user, _, _, cat = _cargar_datos_base(db)
    hoy = date(2026, 11, 13)

    # 1. 6 ciclos
    res_6 = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    assert res_6["kpis"]["frase_resumen"] == (
        "Números de los ciclos terminados. "
        "En estos 13 días gastaste 50% menos que en los mismos días del ciclo anterior."
    )

    # 2. 1 ciclo
    res_1 = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=1, hoy=hoy)
    assert res_1["kpis"]["frase_resumen"] == (
        "Números de lo que va del ciclo (13 de 30 días). "
        "En estos 13 días gastaste 50% menos que en los mismos días del ciclo anterior."
    )

    # 3. Con filtro de categoría
    res_cat = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6, categoria_id=cat.id, hoy=hoy)
    assert res_cat["kpis"]["frase_resumen"].startswith("Con este filtro solo se ven gastos.")

    # 4. Con el gasto de noviembre en 375.000 → 50% más que
    user_mas = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_mas = _crear_billetera(db, user_mas.id, Moneda.ARS)
    _crear_transaccion(db, user_mas.id, b_mas.id, Decimal("250000"), date(2026, 10, 5))
    _crear_transaccion(db, user_mas.id, b_mas.id, Decimal("375000"), date(2026, 11, 5))
    res_mas = obtener_resumen_analisis(db, user_mas, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    assert "50% más que" in res_mas["kpis"]["frase_resumen"]

    # 5. Con 250.000 en noviembre → lo mismo que
    user_eq = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_eq = _crear_billetera(db, user_eq.id, Moneda.ARS)
    _crear_transaccion(db, user_eq.id, b_eq.id, Decimal("250000"), date(2026, 10, 5))
    _crear_transaccion(db, user_eq.id, b_eq.id, Decimal("250000"), date(2026, 11, 5))
    res_eq = obtener_resumen_analisis(db, user_eq, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    assert "lo mismo que" in res_eq["kpis"]["frase_resumen"]

    # 6. Sin gastos en octubre → solo la primera parte
    user_sin_ant = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_sin_ant = _crear_billetera(db, user_sin_ant.id, Moneda.ARS)
    _crear_transaccion(db, user_sin_ant.id, b_sin_ant.id, Decimal("125000"), date(2026, 11, 5))
    res_sin_ant = obtener_resumen_analisis(db, user_sin_ant, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    assert res_sin_ant["kpis"]["frase_resumen"] == "Números de los ciclos terminados."


# ── TN26: Compatibilidad con N2 y Excel ──────────────────────────────────────
def test_tn26_compatibilidad_y_excel(db: Session):
    """
    TN26:
    - tasa_ahorro_texto y variacion_texto conservan los valores del N2 con los mismos datos (TN20 sigue verde sin cambios).
    - Excel: la hoja de resumen muestra los kpis nuevos (gastos 1.300.000 con los datos base).
    """
    user, _, _, _ = _cargar_datos_base(db)
    hoy = date(2026, 11, 13)

    res_6 = obtener_resumen_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6, hoy=hoy)
    assert res_6["kpis"]["tasa_ahorro_texto"] == "Sin contar el ciclo en curso."
    assert res_6["kpis"]["variacion_texto"] == "Comparado con los mismos 13 días del ciclo anterior"

    # Excel
    excel_bytes = generar_excel_analisis(db, user, moneda_modo="solo_ars", cantidad_ciclos=6)
    wb = openpyxl.load_workbook(BytesIO(excel_bytes), data_only=True)
    ws = wb["Resumen y Ciclos"]

    # Fila 5: Ingresos (ARS) -> 1.500.000
    # Fila 6: Gastos (ARS) -> 1.300.000
    # Fila 7: Te quedó (ARS) -> 200.000
    val_gastos = ws.cell(row=6, column=2).value
    assert val_gastos == 1300000.0


# ── TN27: Tamaño y orden de archivos backend ──────────────────────────────────
def test_tn27_tamanio_resumen_service():
    """
    TN27:
    - analisis_resumen_service.py <= 600 líneas
    - TN21 sigue verde
    """
    backend_root = Path(__file__).resolve().parent.parent
    resumen_file = backend_root / "app" / "services" / "analisis_resumen_service.py"
    with open(resumen_file, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    assert len(lines) <= 600, f"analisis_resumen_service.py tiene {len(lines)} líneas (máximo 600)"
