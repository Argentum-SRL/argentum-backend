# -*- coding: utf-8 -*-
"""
tests/test_metas_balance.py — Pruebas unitarias e integración de línea de metas y aportes_netos_metas.
Cubre:
1. Función pura linea_metas (5 casos requeridos por la especificación).
2. Integración de aportes_netos_metas con fecha <= hoy (ignora futuros).
3. Integración en GET /dashboard/resumen-completo (balance.ars.linea_metas y números sin alteración).
4. Integración en GET /movimientos (totales.ARS.linea_metas presente).
5. Detección de Mutación (13): aportes_netos_metas sin el filtro fecha <= hoy falla.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.services import dashboard_service
from app.services.metas_balance import aportes_netos_metas, linea_metas


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_jsonb = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_jsonb:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    had_pguuid = (
        hasattr(PGUUID, "_compiler_dispatcher")
        and "sqlite" in getattr(PGUUID._compiler_dispatcher, "specs", {})
    )
    if not had_pguuid:
        compiles(PGUUID, "sqlite")(lambda type_, compiler, **kw: "CHAR(32)")

    orig_uuid_processor = types.Uuid.bind_processor

    def _safe_uuid_processor(self, dialect):
        proc = orig_uuid_processor(self, dialect)
        if proc is None:
            return None

        def process(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_jsonb and hasattr(JSONB, "_compiler_dispatcher"):
        JSONB._compiler_dispatcher.specs.pop("sqlite", None)
    if not had_pguuid and hasattr(PGUUID, "_compiler_dispatcher"):
        PGUUID._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_linea_metas_casos_puros():
    """
    Verifica los 5 casos requeridos para la función pura linea_metas:
    - (700000, 70000, "ARS") -> "De eso, apartaste $70.000 para tus metas."
    - (-50000, 100000, "ARS") -> "Apartaste $100.000 para tus metas."
    - (50000, 100000, "ARS") -> "Apartaste $100.000 para tus metas."
    - (700000, -30000, "ARS") -> "Sacaste $30.000 de tus metas."
    - (700000, 0, "ARS") -> None
    """
    assert (
        linea_metas(Decimal("700000.00"), Decimal("70000.00"), "ARS")
        == "De eso, apartaste $70.000 para tus metas."
    )
    assert (
        linea_metas(Decimal("-50000.00"), Decimal("100000.00"), "ARS")
        == "Apartaste $100.000 para tus metas."
    )
    assert (
        linea_metas(Decimal("50000.00"), Decimal("100000.00"), "ARS")
        == "Apartaste $100.000 para tus metas."
    )
    assert (
        linea_metas(Decimal("700000.00"), Decimal("-30000.00"), "ARS")
        == "Sacaste $30.000 de tus metas."
    )
    assert (
        linea_metas(Decimal("700000.00"), Decimal("0.00"), "ARS")
        is None
    )


def test_integracion_aportes_netos_metas_y_mutacion_13(db_session: Session, monkeypatch):
    """
    Integración con hoy 20/11/2026:
    - Aporte de $100.000 el 05/11
    - Retiro de $30.000 el 10/11
    - Aporte de $50.000 el 25/11 (futuro respecto a hoy 20/11, no cuenta)
    -> aportes_netos_metas del 01/11 al 30/11 == 70.000
    Mutación (13): si aportes_netos_metas no tuviera fecha <= hoy, daría 120.000 y falla este test.
    """
    usuario = Usuario(
        id=uuid4(),
        email="test.metas@argentum.com",
        password_hash="fakehash",
        nombre="Test",
        apellido="Metas",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        moneda_principal=Moneda.ARS,
        auth_provider=AuthProvider.EMAIL,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db_session.add(usuario)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Banco Galicia",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1500000.00"),
        saldo_inicial=Decimal("0.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(billetera)

    meta = Meta(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Vacaciones",
        monto_objetivo=Decimal("500000.00"),
        moneda=Moneda.ARS,
        monto_actual=Decimal("70000.00"),
        estado=EstadoMeta.ACTIVA,
    )
    db_session.add(meta)

    # 1. Aporte 05/11: $100.000
    mov_meta_1 = MovimientoMeta(
        id=uuid4(),
        meta_id=meta.id,
        billetera_id=billetera.id,
        tipo=TipoMovimientoMeta.APORTE,
        monto=Decimal("100000.00"),
        moneda_movimiento=Moneda.ARS,
        fecha=date(2026, 11, 5),
    )
    db_session.add(mov_meta_1)
    tx_aporte_1 = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 5),
        descripcion="Aporte Vacaciones",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        movimiento_meta_id=mov_meta_1.id,
        fecha_creacion=datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_aporte_1)

    # 2. Retiro 10/11: $30.000
    mov_meta_2 = MovimientoMeta(
        id=uuid4(),
        meta_id=meta.id,
        billetera_id=billetera.id,
        tipo=TipoMovimientoMeta.RETIRO,
        monto=Decimal("30000.00"),
        moneda_movimiento=Moneda.ARS,
        fecha=date(2026, 11, 10),
    )
    db_session.add(mov_meta_2)
    tx_retiro = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 10),
        descripcion="Retiro Vacaciones",
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        movimiento_meta_id=mov_meta_2.id,
        fecha_creacion=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_retiro)

    # 3. Aporte futuro 25/11: $50.000 (futuro respecto a hoy 20/11)
    mov_meta_3 = MovimientoMeta(
        id=uuid4(),
        meta_id=meta.id,
        billetera_id=billetera.id,
        tipo=TipoMovimientoMeta.APORTE,
        monto=Decimal("50000.00"),
        moneda_movimiento=Moneda.ARS,
        fecha=date(2026, 11, 25),
    )
    db_session.add(mov_meta_3)
    tx_aporte_fut = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 25),
        descripcion="Aporte Vacaciones Futuro",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        movimiento_meta_id=mov_meta_3.id,
        fecha_creacion=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_aporte_fut)

    # Transacciones regulares para verificar que ingresos y egresos del ciclo no son alterados
    cat_trabajo = Categoria(id=uuid4(), nombre="Trabajo", tipo=TipoCategoria.INGRESO, estado=EstadoCategoria.ACTIVA)
    cat_alim = Categoria(id=uuid4(), nombre="Comida", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    db_session.add_all([cat_trabajo, cat_alim])

    tx_sueldo = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat_trabajo.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("1000000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 2),
        descripcion="Sueldo",
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc),
    )
    tx_super = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat_alim.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("200000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 4),
        descripcion="Supermercado",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([tx_sueldo, tx_super])
    db_session.commit()

    ref_hoy = date(2026, 11, 20)
    neto = aportes_netos_metas(
        db_session,
        usuario.id,
        desde=date(2026, 11, 1),
        hasta=date(2026, 11, 30),
        moneda=Moneda.ARS,
        hoy=ref_hoy,
    )
    # 100.000 - 30.000 = 70.000 (el aporte futuro de 50.000 no suma)
    assert neto == Decimal("70000.00")

    # FastAPI TestClient
    monkeypatch.setattr(dashboard_service, "hoy_argentina", lambda: ref_hoy)
    monkeypatch.setattr("app.services.historial_service.hoy_argentina", lambda: ref_hoy)

    app.dependency_overrides[get_current_user] = lambda: usuario
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        client = TestClient(app)

        # 1. GET /dashboard/resumen-completo
        resp_dash = client.get("/dashboard/resumen-completo")
        assert resp_dash.status_code == 200
        data_dash = resp_dash.json()

        resumen_dash = data_dash.get("resumen", data_dash)
        balance_ars = resumen_dash["balance"]["ars"]
        # linea_metas no nula
        assert balance_ars["linea_metas"] is not None
        assert balance_ars["linea_metas"] == "De eso, apartaste $70.000 para tus metas."

        # Ingresos, egresos y balance intactos respecto a las reglas financieras
        # Ingresos = $1.000.000, Egresos = $200.000, Balance = $800.000
        assert Decimal(str(balance_ars["ingresos"])) == Decimal("1000000.00")
        assert Decimal(str(balance_ars["egresos"])) == Decimal("200000.00")
        assert Decimal(str(balance_ars["balance"])) == Decimal("800000.00")

        # 2. GET /movimientos
        resp_movs = client.get("/movimientos?fecha_desde=2026-11-01&fecha_hasta=2026-11-30")
        assert resp_movs.status_code == 200
        data_movs = resp_movs.json()
        assert "totales" in data_movs
        assert "ARS" in data_movs["totales"]
        assert data_movs["totales"]["ARS"]["linea_metas"] is not None
        assert (
            data_movs["totales"]["ARS"]["linea_metas"]
            == "De eso, apartaste $70.000 para tus metas."
        )

    finally:
        app.dependency_overrides.clear()
