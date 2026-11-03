# -*- coding: utf-8 -*-
"""
tests/test_movimientos_proximos.py
Pruebas para la separación de movimientos pasados y próximos en listar_movimientos y endpoints.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

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
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import TarjetaCredito, RedTarjeta, EstadoTarjeta
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
from app.services.historial_service import listar_movimientos


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
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
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


@pytest.fixture(name="datos_prueba")
def datos_prueba_fixture(db: Session):
    usuario = Usuario(
        id=uuid4(),
        email="test_proximos@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="25",
        onboarding_completo=True,
    )
    db.add(usuario)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        saldo_inicial=Decimal("0.00"),
        es_principal=True,
        es_efectivo=False,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(billetera)

    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa Galicia",
        limite_credito=Decimal("500000.00"),
        dia_cierre=20,
        dia_vencimiento=3,
        red=RedTarjeta.VISA,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta)

    categoria = Categoria(
        id=uuid4(),
        nombre="General",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
        orden=1,
    )
    db.add(categoria)
    db.flush()

    # 1. Dos gastos débito pasados: 10/11 $1.000 y 15/11 $2.000
    tx1 = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=categoria.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 10),
        descripcion="Gasto pasado 1",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
    )
    tx2 = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=categoria.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("2000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 15),
        descripcion="Gasto pasado 2",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
    )
    db.add_all([tx1, tx2])

    # 2. Un gasto débito con fecha futura: 25/11 $3.000
    tx_fut = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=categoria.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("3000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 25),
        descripcion="Gasto futuro 1",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
    )
    db.add(tx_fut)

    # 3. Compra de $30.000 en 3 cuotas con crédito (compra del 18/11/2026)
    # Cuotas vencen 05/12/2026, 05/01/2027, 05/02/2027
    padre_id = uuid4()
    grupo_id = uuid4()

    padre_cuotas = Transaccion(
        id=padre_id,
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        categoria_id=categoria.id,
        grupo_cuotas_id=grupo_id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 18),
        descripcion="Compra en 3 cuotas",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
    )
    db.add(padre_cuotas)
    db.flush()

    grupo = GrupoCuotas(
        id=grupo_id,
        usuario_id=usuario.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=padre_id,
        cantidad_cuotas=3,
        monto_total=Decimal("30000.00"),
        total_financiado=Decimal("30000.00"),
        moneda=Moneda.ARS,
        descripcion="Compra en 3 cuotas",
        primer_vencimiento=date(2026, 12, 5),
    )
    db.add(grupo)
    db.flush()

    vencimientos = [date(2026, 12, 5), date(2027, 1, 5), date(2027, 2, 5)]
    for n, f_venc in enumerate(vencimientos, start=1):
        tx_hija = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
            billetera_id=billetera.id,
            tarjeta_id=tarjeta.id,
            categoria_id=categoria.id,
            grupo_cuotas_id=grupo.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=f_venc,
            descripcion="Compra en 3 cuotas",
            metodo_pago=MetodoPago.CREDITO,
            origen=OrigenTransaccion.MANUAL,
            es_padre_cuotas=False,
            es_cuota_hija=True,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
        )
        db.add(tx_hija)
        db.flush()

        cuota_obj = Cuota(
            id=uuid4(),
            grupo_id=grupo.id,
            transaccion_id=tx_hija.id,
            numero_cuota=n,
            monto_proyectado=Decimal("10000.00"),
            fecha_vencimiento=f_venc,
            pagada=False,
        )
        db.add(cuota_obj)

    db.commit()
    return usuario, billetera, categoria


def test_listar_movimientos_pasados_y_proximos(db: Session, datos_prueba):
    usuario, billetera, categoria = datos_prueba
    ref_hoy = date(2026, 11, 20)

    # 1. Llamada a listar_movimientos con ref_hoy = 20/11/2026
    resp = listar_movimientos(db, usuario.id, modo="general", hoy=ref_hoy)

    # items solo con fecha <= 20/11 (los 2 gastos; la compra en cuotas padre no es visible como en hoy)
    assert len(resp.items) == 2
    for item in resp.items:
        assert item.fecha <= ref_hoy
        assert item.titulo != ""
        assert isinstance(item.iconos, list)

    assert resp.total_items == len(resp.items)
    assert resp.proximos_total == 4
    assert len(resp.proximos) == 4

    # proximos en orden de fecha ascendente
    fechas_proximos = [p.fecha for p in resp.proximos]
    assert fechas_proximos == [date(2026, 11, 25), date(2026, 12, 5), date(2027, 1, 5), date(2027, 2, 5)]

    for p in resp.proximos:
        assert p.titulo != ""
        assert isinstance(p.iconos, list)

    # Totales ARS: gastos 3.000 con este fixture (1.000 + 2.000; la compra en cuotas > 1 no suma al gasto)
    assert resp.totales["ARS"].gastos == Decimal("3000.00")

    # 2. Con skip=1: proximos == [] y proximos_total == 4
    resp_skip = listar_movimientos(db, usuario.id, modo="general", skip=1, hoy=ref_hoy)
    assert resp_skip.proximos == []
    assert resp_skip.proximos_total == 4


def test_tope_50_proximos(db: Session, datos_prueba):
    usuario, billetera, categoria = datos_prueba
    ref_hoy = date(2026, 11, 20)

    # Agregar 55 gastos futuros
    for i in range(1, 56):
        tx_extra = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
            billetera_id=billetera.id,
            categoria_id=categoria.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 11, 26),
            descripcion=f"Gasto futuro extra {i}",
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc),
        )
        db.add(tx_extra)
    db.commit()

    resp = listar_movimientos(db, usuario.id, modo="general", hoy=ref_hoy)
    # Total de futuros: 4 previos + 55 = 59 futuros
    assert resp.proximos_total == 59
    assert len(resp.proximos) == 50


def test_endpoints_proximos_y_dashboard(db: Session, datos_prueba):
    usuario, _, _ = datos_prueba

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario

    client = TestClient(app)
    try:
        # GET /movimientos
        resp_movs = client.get("/movimientos")
        assert resp_movs.status_code == 200
        data_movs = resp_movs.json()
        assert "proximos" in data_movs
        assert "proximos_total" in data_movs
        assert isinstance(data_movs["proximos"], list)
        assert isinstance(data_movs["proximos_total"], int)

        # GET /dashboard/resumen
        resp_dash_res = client.get("/dashboard/resumen")
        assert resp_dash_res.status_code == 200
        data_dash_res = resp_dash_res.json()
        assert "ultimos_movimientos" in data_dash_res
        for umov in data_dash_res["ultimos_movimientos"]:
            assert "titulo" in umov
            assert "iconos" in umov
            assert isinstance(umov["iconos"], list)

        # GET /dashboard/resumen-completo
        resp_dash = client.get("/dashboard/resumen-completo")
        assert resp_dash.status_code == 200
        data_dash = resp_dash.json()
        assert "resumen" in data_dash
        assert "ultimos_movimientos" in data_dash["resumen"]
        for umov in data_dash["resumen"]["ultimos_movimientos"]:
            assert "titulo" in umov
            assert "iconos" in umov
            assert isinstance(umov["iconos"], list)
    finally:
        app.dependency_overrides.clear()
