"""
tests/test_gastos_consulta_espejos.py - Pruebas para calcular_gastos_periodo con reintegros y partes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.transaccion import MetodoPago, TipoTransaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services import (
    que_es_plata_service,
    reintegro_service,
    transaccion_service,
)
from app.services.gastos_consulta_service import calcular_gastos_periodo


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
        email="test_gastos_espejos@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="billetera_galicia")
def billetera_galicia_fixture(db: Session, usuario: Usuario):
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        fecha_saldo=date(2026, 9, 1),
        es_principal=True,
        es_inversion=False,
    )
    db.add(billetera)
    db.commit()
    db.refresh(billetera)
    return billetera


@pytest.fixture(name="setup_datos")
def setup_datos_fixture(db: Session, usuario: Usuario, billetera_galicia: Billetera):
    cat_alim = Categoria(
        id=uuid4(),
        nombre="Alimentación",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
        orden=1,
    )
    sub_super = Subcategoria(
        id=uuid4(),
        categoria_id=cat_alim.id,
        nombre="Supermercado",
        estado=EstadoSubcategoria.ACTIVA,
        orden=1,
    )
    cat_ocio = Categoria(
        id=uuid4(),
        nombre="Ocio",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
        orden=2,
    )
    sub_salidas = Subcategoria(
        id=uuid4(),
        categoria_id=cat_ocio.id,
        nombre="Salidas",
        estado=EstadoSubcategoria.ACTIVA,
        orden=1,
    )
    cat_otros = Categoria(
        id=uuid4(),
        nombre="Otros",
        tipo=TipoCategoria.INGRESO,
        estado=EstadoCategoria.ACTIVA,
        orden=3,
    )
    sub_cobros = Subcategoria(
        id=uuid4(),
        categoria_id=cat_otros.id,
        nombre="Cobros de personas",
        estado=EstadoSubcategoria.ACTIVA,
        orden=1,
    )
    db.add_all([cat_alim, sub_super, cat_ocio, sub_salidas, cat_otros, sub_cobros])
    db.commit()

    # 1. Compra "Súper" de 100.000 el 10/10/2026 en Supermercado con reintegro de 25.000 el 12/10
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 10),
            categoria_id=cat_alim.id,
            subcategoria_id=sub_super.id,
            descripcion="Súper",
            billetera_id=billetera_galicia.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 12),
    )

    # 2. Gasto "Asado" de 80.000 el 20/10 en Salidas
    asado = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("80000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 20),
            categoria_id=cat_ocio.id,
            subcategoria_id=sub_salidas.id,
            descripcion="Asado",
            billetera_id=billetera_galicia.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    # 3. Ingreso "Juan" de 30.000 el 22/10 en Cobros de personas pasado a parte del Asado
    ingreso = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 22),
            categoria_id=cat_otros.id,
            subcategoria_id=sub_cobros.id,
            descripcion="Juan",
            billetera_id=billetera_galicia.id,
            metodo_pago=MetodoPago.TRANSFERENCIA,
        ),
    )
    que_es_plata_service.responder(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=ingreso.id,
        opcion="parte_de_gasto",
        gasto_id=asado.id,
    )

    return {
        "sub_super": sub_super,
        "sub_salidas": sub_salidas,
        "sub_cobros": sub_cobros,
        "compra": compra,
        "asado": asado,
    }


def test_gastos_consulta_espejos(db: Session, usuario: Usuario, setup_datos: dict):
    """
    calcular_gastos_periodo del 01/10/2026 al 31/10/2026:
    ars total 125000.00 y cantidad 2;
    top_categorias_ars [("Alimentación", 75000.0), ("Ocio", 50000.0)];
    con la subcategoría Supermercado: 75000.00 y 1;
    con texto_descripcion "Súper": 75000.00 y 1;
    con texto_descripcion "Asado": 50000.00 y 1.
    """
    # Global en el período
    res = calcular_gastos_periodo(db, usuario.id, date(2026, 10, 1), date(2026, 10, 31))
    assert res["ars"]["total"] == Decimal("125000.00")
    assert res["ars"]["cantidad"] == 2
    assert res["top_categorias_ars"] == [("Alimentación", 75000.0), ("Ocio", 50000.0)]

    # Filtro por subcategoría Supermercado
    sub_super = setup_datos["sub_super"]
    res_sub = calcular_gastos_periodo(
        db, usuario.id, date(2026, 10, 1), date(2026, 10, 31), subcategoria_id=sub_super.id
    )
    assert res_sub["ars"]["total"] == Decimal("75000.00")
    assert res_sub["ars"]["cantidad"] == 1

    # Filtro por texto_descripcion "Súper"
    res_desc_super = calcular_gastos_periodo(
        db, usuario.id, date(2026, 10, 1), date(2026, 10, 31), texto_descripcion="Súper"
    )
    assert res_desc_super["ars"]["total"] == Decimal("75000.00")
    assert res_desc_super["ars"]["cantidad"] == 1

    # Filtro por texto_descripcion "Asado"
    res_desc_asado = calcular_gastos_periodo(
        db, usuario.id, date(2026, 10, 1), date(2026, 10, 31), texto_descripcion="Asado"
    )
    assert res_desc_asado["ars"]["total"] == Decimal("50000.00")
    assert res_desc_asado["ars"]["cantidad"] == 1
