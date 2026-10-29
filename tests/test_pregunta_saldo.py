# -*- coding: utf-8 -*-
"""
tests/test_pregunta_saldo.py - Pruebas de selección, salteo y decisión de guardado directo
para la pregunta de saldo (fase_g2_saldo_confianza).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.ajuste_saldo_service import decidir_guardar_directo, registrar_control
from app.services.dashboard_service import calcular_balance_ciclo
from app.services.pregunta_saldo_service import elegir_billetera_pregunta, saltear_pregunta
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


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


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db: Session) -> Usuario:
    usuario = Usuario(
        id=uuid4(),
        email="test_pregunta_saldo@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_test: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_test

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_elegir_y_saltear_pregunta(db: Session, usuario_test: Usuario):
    hoy = date(2026, 11, 2)

    # 1. Galicia: ARS, activa, fecha_ultimo_control 20/09, 5 egresos no crédito (05/10 a 09/10), 10 egresos crédito (10/10 a 19/10)
    b_galicia = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("150000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_ultimo_control=date(2026, 9, 20),
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )

    # 2. Mercado Pago: ARS, activa, sin control, fecha_saldo 01/09, 8 egresos no crédito (01/10 a 08/10)
    b_mp = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("80000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=date(2026, 9, 1),
        fecha_creacion=datetime(2026, 2, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )

    # 3. Ahorro: es_inversion, sin control, fecha_saldo 01/08, 12 egresos (01/10 a 12/10)
    b_ahorro = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Ahorro",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000.00"),
        estado=EstadoBilletera.ACTIVA,
        es_inversion=True,
        fecha_saldo=date(2026, 8, 1),
        fecha_creacion=datetime(2026, 3, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )

    # 4. Vieja: archivada, sin control, 12 egresos (01/10 a 12/10)
    b_vieja = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Vieja",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000.00"),
        estado=EstadoBilletera.ARCHIVADA,
        fecha_saldo=date(2026, 1, 1),
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )

    # 5. Nueva: activa, creada 01/08 10:00, sin movimientos
    b_nueva = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Nueva",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("5000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=datetime(2026, 8, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )

    # 6. Otro usuario con "Ajena" (activa, sin control, 20 egresos en octubre)
    otro_user = Usuario(
        id=uuid4(),
        email="otro_pregunta@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    b_ajena = Billetera(
        id=uuid4(),
        usuario_id=otro_user.id,
        nombre="Ajena",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("20000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=date(2026, 8, 1),
        fecha_creacion=datetime(2026, 8, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )

    db.add_all([b_galicia, b_mp, b_ahorro, b_vieja, b_nueva, otro_user, b_ajena])
    db.flush()

    # Movimientos Galicia: 5 no crédito + 10 crédito
    txs_galicia = [
        Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=b_galicia.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("1000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 5 + i),
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.MANUAL,
            fecha_creacion=datetime(2026, 10, 5 + i, 12, 0, tzinfo=TZ_ARGENTINA),
        )
        for i in range(5)
    ] + [
        Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=b_galicia.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("2000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 10 + i),
            metodo_pago=MetodoPago.CREDITO,
            origen=OrigenTransaccion.MANUAL,
            fecha_creacion=datetime(2026, 10, 10 + i, 12, 0, tzinfo=TZ_ARGENTINA),
        )
        for i in range(10)
    ]

    # Movimientos Mercado Pago: 8 no crédito
    txs_mp = [
        Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=b_mp.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("500.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 1 + i),
            metodo_pago=MetodoPago.TRANSFERENCIA,
            origen=OrigenTransaccion.MANUAL,
            fecha_creacion=datetime(2026, 10, 1 + i, 12, 0, tzinfo=TZ_ARGENTINA),
        )
        for i in range(8)
    ]

    # Movimientos Ahorro: 12
    txs_ahorro = [
        Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=b_ahorro.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 1 + i),
            metodo_pago=MetodoPago.TRANSFERENCIA,
            origen=OrigenTransaccion.MANUAL,
            fecha_creacion=datetime(2026, 10, 1 + i, 12, 0, tzinfo=TZ_ARGENTINA),
        )
        for i in range(12)
    ]

    # Movimientos Vieja: 12
    txs_vieja = [
        Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=b_vieja.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 1 + i),
            metodo_pago=MetodoPago.EFECTIVO,
            origen=OrigenTransaccion.MANUAL,
            fecha_creacion=datetime(2026, 10, 1 + i, 12, 0, tzinfo=TZ_ARGENTINA),
        )
        for i in range(12)
    ]

    # Movimientos Ajena: 20
    txs_ajena = [
        Transaccion(
            id=uuid4(),
            usuario_id=otro_user.id,
            billetera_id=b_ajena.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 1 + i),
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.MANUAL,
            fecha_creacion=datetime(2026, 10, 1 + i, 12, 0, tzinfo=TZ_ARGENTINA),
        )
        for i in range(20)
    ]

    db.add_all(txs_galicia + txs_mp + txs_ahorro + txs_vieja + txs_ajena)
    db.commit()

    # 1. elegir_billetera_pregunta(hoy) -> "Mercado Pago" (8 movimientos válidos vs 5 de Galicia)
    ganadora = elegir_billetera_pregunta(db, usuario_test.id, hoy)
    assert ganadora is not None
    assert ganadora.nombre == "Mercado Pago"

    # 2. saltear_pregunta(Mercado Pago, hoy) -> pospuesta hasta 02/12/2026
    saltear_pregunta(db, usuario_test.id, b_mp.id, hoy)
    db.refresh(b_mp)
    assert b_mp.pregunta_saldo_pospuesta_hasta == date(2026, 12, 2)

    # 3. elegir(hoy) -> "Galicia"
    assert elegir_billetera_pregunta(db, usuario_test.id, hoy).nombre == "Galicia"

    # 4. elegir(date(2026, 12, 1)) -> "Galicia" (MP sigue pospuesta hasta el 02/12)
    assert elegir_billetera_pregunta(db, usuario_test.id, date(2026, 12, 1)).nombre == "Galicia"

    # 5. elegir(date(2026, 12, 2)) -> "Mercado Pago" (ya venció la postergación, <= hoy)
    assert elegir_billetera_pregunta(db, usuario_test.id, date(2026, 12, 2)).nombre == "Mercado Pago"

    # 6. Con Mercado Pago y Galicia salteadas el 02/11: elegir(hoy) -> None
    saltear_pregunta(db, usuario_test.id, b_galicia.id, hoy)
    assert elegir_billetera_pregunta(db, usuario_test.id, hoy) is None


def test_saltear_y_registrar_control(db: Session, usuario_test: Usuario):
    hoy = date(2026, 11, 2)

    b_galicia = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("150000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_ultimo_control=date(2026, 9, 20),
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    b_mp = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("80000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=date(2026, 9, 1),
        fecha_creacion=datetime(2026, 2, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add_all([b_galicia, b_mp])
    db.flush()

    # Movimientos para que tengan uso >= 1
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=b_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        fecha_creacion=datetime(2026, 10, 15, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(tx)
    db.commit()

    # Mercado Pago salteada el 02/11
    saltear_pregunta(db, usuario_test.id, b_mp.id, hoy)

    # Balance antes de registrar control
    bal_antes = calcular_balance_ciclo(db, usuario_test)
    egresos_antes_ars = bal_antes["ars"]["egresos"]

    # Registrar control sobre Galicia con saldo_declarado = saldo_actual - 500
    saldo_dec = b_galicia.saldo_actual - Decimal("500.00")
    ajuste = registrar_control(db, usuario_test.id, b_galicia.id, saldo_declarado=saldo_dec, commit=True)

    # 1 fila en ajustes_saldo con monto -500.00
    assert ajuste.monto == Decimal("-500.00")
    ajustes_filas = db.execute(select(AjusteSaldo).where(AjusteSaldo.billetera_id == b_galicia.id)).scalars().all()
    assert len(ajustes_filas) == 1

    # Egresos de calcular_balance_ciclo en ARS iguales antes y después (el ajuste no es egreso ni gasto)
    bal_despues = calcular_balance_ciclo(db, usuario_test)
    egresos_despues_ars = bal_despues["ars"]["egresos"]
    assert egresos_antes_ars == egresos_despues_ars

    # elegir(hoy) -> None (Galicia recién controlada hoy, MP pospuesta)
    assert elegir_billetera_pregunta(db, usuario_test.id, hoy) is None


def test_borde_30_dias(db: Session, usuario_test: Usuario):
    hoy = date(2026, 11, 2)

    b_caja = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Caja",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("10000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_ultimo_control=date(2026, 10, 4),
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(b_caja)
    db.flush()

    # 3 egresos del 20/10 al 22/10
    for i in range(3):
        db.add(Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=b_caja.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 20 + i),
            metodo_pago=MetodoPago.EFECTIVO,
            origen=OrigenTransaccion.MANUAL,
            fecha_creacion=datetime(2026, 10, 20 + i, 12, 0, tzinfo=TZ_ARGENTINA),
        ))
    db.commit()

    # Con fecha_ultimo_control 04/10 -> (02/11 - 04/10) = 29 días < 30 -> None
    assert elegir_billetera_pregunta(db, usuario_test.id, hoy) is None

    # Con fecha_ultimo_control 03/10 -> (02/11 - 03/10) = 30 días >= 30 -> "Caja"
    b_caja.fecha_ultimo_control = date(2026, 10, 3)
    db.commit()
    res = elegir_billetera_pregunta(db, usuario_test.id, hoy)
    assert res is not None
    assert res.nombre == "Caja"


def test_decidir_guardar_directo():
    # (0, True, None, []) -> True (diferencia 0 siempre guarda directo)
    assert decidir_guardar_directo(0, True, None, []) is True
    assert decidir_guardar_directo(Decimal("0.00"), True, None, []) is True

    # (-500, False, None, []) -> True (diferencia no grande, sin rendimiento ni duplicados)
    assert decidir_guardar_directo(-500, False, None, []) is True
    assert decidir_guardar_directo(Decimal("-500.00"), False, None, []) is True

    # (-500, True, None, []) -> False (diferencia grande)
    assert decidir_guardar_directo(-500, True, None, []) is False

    # (300, False, Decimal("200"), []) -> False (hay rendimiento propuesto)
    assert decidir_guardar_directo(300, False, Decimal("200"), []) is False

    # (300, False, None, [un duplicado]) -> False (hay posibles duplicados)
    assert decidir_guardar_directo(300, False, None, [{"id": "dup1"}]) is False


def test_rutas_pregunta_saldo(client: TestClient, db: Session, usuario_test: Usuario):
    hoy = hoy_argentina()

    b_caja = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Caja",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("15000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_ultimo_control=hoy - timedelta(days=40),
        fecha_creacion=datetime(hoy.year, hoy.month, hoy.day, 12, 0, tzinfo=TZ_ARGENTINA) - timedelta(days=60),
    )

    otro_user = Usuario(
        id=uuid4(),
        email="otro_rutas@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    b_ajena = Billetera(
        id=uuid4(),
        usuario_id=otro_user.id,
        nombre="Ajena",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("5000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_ultimo_control=hoy - timedelta(days=40),
        fecha_creacion=datetime(hoy.year, hoy.month, hoy.day, 12, 0, tzinfo=TZ_ARGENTINA) - timedelta(days=60),
    )
    db.add_all([b_caja, otro_user, b_ajena])
    db.flush()

    # 3 egresos en hoy - 5, hoy - 4 y hoy - 3
    for d_offset in (5, 4, 3):
        db.add(Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=b_caja.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100.00"),
            moneda=Moneda.ARS,
            fecha=hoy - timedelta(days=d_offset),
            metodo_pago=MetodoPago.EFECTIVO,
            origen=OrigenTransaccion.MANUAL,
            fecha_creacion=datetime(hoy.year, hoy.month, hoy.day, 12, 0, tzinfo=TZ_ARGENTINA) - timedelta(days=d_offset),
        ))
    db.commit()

    # 1. GET /billeteras/pregunta-saldo -> 200 con billetera.nombre "Caja"
    r1 = client.get("/billeteras/pregunta-saldo")
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["billetera"] is not None
    assert data1["billetera"]["nombre"] == "Caja"

    # 2. POST /billeteras/{id}/pregunta-saldo/saltear -> 204
    r2 = client.post(f"/billeteras/{b_caja.id}/pregunta-saldo/saltear")
    assert r2.status_code == 204

    # 3. GET otra vez -> 200 con {"billetera": null}
    r3 = client.get("/billeteras/pregunta-saldo")
    assert r3.status_code == 200
    assert r3.json() == {"billetera": None}

    # 4. POST saltear con la billetera del otro usuario -> 404 con detail "No encontramos esa billetera."
    r4 = client.post(f"/billeteras/{b_ajena.id}/pregunta-saldo/saltear")
    assert r4.status_code == 404
    d4 = r4.json()
    assert (d4.get("detail") or d4.get("error", {}).get("message")) == "No encontramos esa billetera."

    # 5. GET /billeteras/{id}/control-saldo?saldo=<saldo_actual> -> guardar_directo true
    r5 = client.get(f"/billeteras/{b_caja.id}/control-saldo?saldo=15000.00")
    assert r5.status_code == 200
    assert r5.json()["guardar_directo"] is True

    # 6. con saldo_actual - 100 -> es_grande true y guardar_directo false
    r6 = client.get(f"/billeteras/{b_caja.id}/control-saldo?saldo=14900.00")
    assert r6.status_code == 200
    assert r6.json()["es_grande"] is True
    assert r6.json()["guardar_directo"] is False

