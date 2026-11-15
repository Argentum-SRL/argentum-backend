# -*- coding: utf-8 -*-
"""
tests/test_transferencias_m1_servicio.py — Pruebas de servicio para transferencias (M1).
Valida que crear_transferencia permita saldo negativo en origen y comisiones (TS1, TS2, TS3).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import transferencia_service


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


@pytest.fixture(name="db")
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
        session.rollback()
        session.close()


@pytest.fixture(name="usuario")
def usuario_fixture(db: Session):
    user = Usuario(
        id=uuid4(),
        email="test_ts_servicio@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="categoria_banco")
def categoria_banco_fixture(db: Session):
    cat = Categoria(
        id=uuid4(),
        nombre="Banco",
        tipo=TipoCategoria.EGRESO,
        icono="bank",
        color="#000000",
    )
    db.add(cat)
    db.flush()
    subcat = Subcategoria(
        id=uuid4(),
        categoria_id=cat.id,
        nombre="Comisiones y gastos bancarios",
    )
    db.add(subcat)
    db.commit()
    return cat


def test_ts1_transferencia_sobregiro_origen(db: Session, usuario: Usuario):
    """
    TS1 crear_transferencia de 300.000 desde billetera con 270.000 a otra con 0
    -> se crea; origen -30.000, destino 300.000.
    """
    b_orig = Billetera(
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("270000"),
        saldo_actual=Decimal("270000"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        estado=EstadoBilletera.ACTIVA,
    )
    b_dest = Billetera(
        usuario_id=usuario.id,
        nombre="Santander",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_orig, b_dest])
    db.commit()

    data = TransferenciaInternaCreate(
        billetera_origen_id=b_orig.id,
        billetera_destino_id=b_dest.id,
        monto=Decimal("300000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 14),
    )
    tr = transferencia_service.crear_transferencia(db, usuario.id, data)
    assert tr is not None
    db.refresh(b_orig)
    db.refresh(b_dest)
    assert b_orig.saldo_actual == Decimal("-30000")
    assert b_dest.saldo_actual == Decimal("300000")


def test_ts2_comision_origen_queda_negativo(
    db: Session, usuario: Usuario, categoria_banco: Categoria
):
    """
    TS2 con comisión de 500 en el origen: origen con 1.000, transferencia de 1.000
    -> se crea; origen -500 (fixture con 'Banco' > 'Comisiones y gastos bancarios').
    """
    b_orig = Billetera(
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000"),
        saldo_actual=Decimal("1000"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        estado=EstadoBilletera.ACTIVA,
    )
    b_dest = Billetera(
        usuario_id=usuario.id,
        nombre="Efectivo Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_orig, b_dest])
    db.commit()

    data = TransferenciaInternaCreate(
        billetera_origen_id=b_orig.id,
        billetera_destino_id=b_dest.id,
        monto=Decimal("1000"),
        moneda=Moneda.ARS,
        monto_comision=Decimal("500"),
        moneda_comision=Moneda.ARS,
        fecha=date(2026, 11, 14),
    )
    tr = transferencia_service.crear_transferencia(db, usuario.id, data)
    assert tr is not None
    db.refresh(b_orig)
    db.refresh(b_dest)
    assert b_orig.saldo_actual == Decimal("-500")
    assert b_dest.saldo_actual == Decimal("1000")


def test_ts3_comision_usd_destino_negativo(
    db: Session, usuario: Usuario, categoria_banco: Categoria
):
    """
    TS3 comisión en dólares en la billetera destino en dólares que queda negativa
    -> se crea (sin 'Saldo insuficiente').
    """
    b_orig = Billetera(
        usuario_id=usuario.id,
        nombre="Galicia Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("150000"),
        saldo_actual=Decimal("150000"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        estado=EstadoBilletera.ACTIVA,
    )
    b_dest = Billetera(
        usuario_id=usuario.id,
        nombre="Lemon USD",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_orig, b_dest])
    db.commit()

    # Compra/transferencia de ARS a USD con comision de 150 USD en destino
    data = TransferenciaInternaCreate(
        billetera_origen_id=b_orig.id,
        billetera_destino_id=b_dest.id,
        monto=Decimal("150000"),
        moneda=Moneda.ARS,
        monto_origen=Decimal("150000"),
        moneda_origen=Moneda.ARS,
        monto_destino=Decimal("100"),
        moneda_destino=Moneda.USD,
        monto_comision=Decimal("150"),
        moneda_comision=Moneda.USD,
        fecha=date(2026, 11, 14),
    )
    tr = transferencia_service.crear_transferencia(db, usuario.id, data)
    assert tr is not None
    db.refresh(b_orig)
    db.refresh(b_dest)
    # origen pierde 150.000 ARS -> queda en 0
    assert b_orig.saldo_actual == Decimal("0")
    # destino recibe 100 USD pero paga 150 USD de comisión -> queda en -50 USD
    assert b_dest.saldo_actual == Decimal("-50")
