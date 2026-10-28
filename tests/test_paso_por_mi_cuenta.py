"""
tests/test_paso_por_mi_cuenta.py — Pruebas para la subcategoría «Pasó por mi cuenta» (egreso e ingreso).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import (
    MetodoPago,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services import transaccion_service
from app.services.definiciones_service import (
    cargar_contexto,
    condicion_gasto,
    condicion_ingreso,
    es_gasto,
    es_ingreso,
    etiqueta_movimiento,
)


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
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


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        def _sqlite_translate(val, from_chars, to_chars):
            if val is None:
                return None
            return str(val).translate(str.maketrans(from_chars, to_chars))
        dbapi_connection.create_function("translate", 3, _sqlite_translate)

    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(name="usuario")
def usuario_fixture(db: Session):
    user = Usuario(
        id=uuid4(),
        email="test_paso@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="billetera_ars")
def billetera_ars_fixture(db: Session, usuario: Usuario):
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Principal",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        fecha_saldo=date(2026, 9, 1),
        es_inversion=False,
    )
    db.add(billetera)
    db.commit()
    db.refresh(billetera)
    return billetera


@pytest.fixture(name="categorias")
def categorias_fixture(db: Session):
    cat_otros_egr = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO)
    cat_otros_ing = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.INGRESO)
    db.add_all([cat_otros_egr, cat_otros_ing])
    db.flush()

    sub_paso_egr = Subcategoria(id=uuid4(), categoria_id=cat_otros_egr.id, nombre="Pasó por mi cuenta", orden=4)
    sub_cobros = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Cobros de personas", orden=1)
    sub_paso_ing = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Pasó por mi cuenta", orden=3)
    db.add_all([sub_paso_egr, sub_cobros, sub_paso_ing])
    db.commit()

    return {
        "otros_egreso": cat_otros_egr,
        "otros_ingreso": cat_otros_ing,
        "sub_paso_egr": sub_paso_egr,
        "sub_cobros": sub_cobros,
        "sub_paso_ing": sub_paso_ing,
    }


def test_paso_por_mi_cuenta_egreso_e_ingreso(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    egreso de 50.000 en débito el 10/10 en Otros > Pasó por mi cuenta:
    gasto de octubre 0.00 (condicion_gasto), es_gasto False, saldo 950000.00,
    etiqueta ("Pasó por mi cuenta", "nada", "No cuenta como gasto ni ingreso");
    ingreso de 70.000 el 11/10 en Otros (ingreso) > Pasó por mi cuenta:
    ingresos de octubre 0.00 (condicion_ingreso), es_ingreso False, saldo 1020000.00.
    """
    # 1. Egreso de 50.000 en débito el 10/10 en Otros > Pasó por mi cuenta
    egreso_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Pago por un amigo",
        categoria_id=categorias["otros_egreso"].id,
        subcategoria_id=categorias["sub_paso_egr"].id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    egreso = transaccion_service.crear_transaccion(db, usuario.id, egreso_data)

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("950000.00")

    ctx = cargar_contexto(db, usuario.id, hoy=date(2026, 10, 21))

    # Gasto de octubre con condicion_gasto: 0.00
    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21))
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("0.00")

    assert es_gasto(egreso, ctx) is False
    etiq_egr = etiqueta_movimiento(egreso, ctx)
    assert etiq_egr == ("Pasó por mi cuenta", "nada", "No cuenta como gasto ni ingreso")

    # 2. Ingreso de 70.000 el 11/10 en Otros (ingreso) > Pasó por mi cuenta
    ingreso_data = TransaccionCreate(
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("70000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 11),
        descripcion="Me transfirieron para pagar",
        categoria_id=categorias["otros_ingreso"].id,
        subcategoria_id=categorias["sub_paso_ing"].id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        billetera_id=billetera_ars.id,
    )
    ingreso = transaccion_service.crear_transaccion(db, usuario.id, ingreso_data)

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("1020000.00")

    # Ingresos de octubre con condicion_ingreso: 0.00
    stmt_ingreso = select(Transaccion).where(
        condicion_ingreso(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21))
    )
    total_ingreso = sum(t.monto for t in db.execute(stmt_ingreso).scalars().all())
    assert total_ingreso == Decimal("0.00")

    assert es_ingreso(ingreso, ctx) is False
    etiq_ing = etiqueta_movimiento(ingreso, ctx)
    assert etiq_ing == ("Pasó por mi cuenta", "nada", "No cuenta como gasto ni ingreso")

