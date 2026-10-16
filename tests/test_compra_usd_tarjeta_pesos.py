"""
tests/test_compra_usd_tarjeta_pesos.py — Pruebas de compra en USD con tarjeta en pesos.

Verifica:
1. Compra con crédito de US$15 en 1 pago con una tarjeta en pesos:
   - Se guarda con moneda USD y tarjeta_id.
   - El grupo de cuotas y la cuota hija quedan en USD.
   - Ningún saldo de billetera cambia.
2. Un egreso con débito en USD en una billetera en pesos sin monto_original
   sigue dando el error de validación de moneda (HTTPException 400).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    MetodoPago,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services.transaccion_service import crear_transaccion


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


def _setup_usuario_y_cuentas(db: Session):
    usuario = Usuario(
        id=uuid4(),
        email="test_compra_usd@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)

    # Billetera en ARS
    billetera_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Corriente Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000.00"),
        saldo_inicial=Decimal("500000.00"),
        estado=EstadoBilletera.ACTIVA,
        es_principal=True,
        es_efectivo=False,
        es_inversion=False,
    )
    db.add(billetera_ars)

    # Tarjeta de crédito en ARS asociada a la billetera en ARS
    tarjeta_ars = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera_ars.id,
        nombre="Visa Signature",
        moneda=Moneda.ARS,
        limite_credito=Decimal("2000000.00"),
        dia_cierre=25,
        dia_vencimiento=5,
        estado=EstadoTarjeta.ACTIVA,
        red=RedTarjeta.VISA,
        percepcion_moneda_extranjera=Decimal("30.00"),
    )
    db.add(tarjeta_ars)

    # Categoría general
    categoria = Categoria(
        id=uuid4(),
        nombre="Tecnología",
        tipo=TipoCategoria.EGRESO,
        icono="laptop",
        color="#3B82F6",
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(categoria)

    db.commit()
    return usuario, billetera_ars, tarjeta_ars, categoria


def test_compra_usd_tarjeta_pesos_credito(db: Session):
    """
    Compra con crédito de US$15 en 1 pago con una tarjeta en pesos.
    Se guarda con moneda USD y tarjeta_id; el grupo y la hija quedan en USD;
    ningún saldo de billetera cambia.
    """
    usuario, billetera_ars, tarjeta_ars, categoria = _setup_usuario_y_cuentas(db)

    saldo_inicial_billetera = billetera_ars.saldo_actual

    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("15.00"),
        moneda=Moneda.USD,
        descripcion="Suscripción Software USD",
        fecha=date(2026, 10, 15),
        categoria_id=categoria.id,
        billetera_id=tarjeta_ars.billetera_id,
        tarjeta_id=tarjeta_ars.id,
        metodo_pago=MetodoPago.CREDITO,
    )

    tx_padre = crear_transaccion(db, usuario.id, data, commit=True)

    # 1. Transacción padre se guarda con moneda USD y tarjeta_id
    assert tx_padre.id is not None
    assert tx_padre.moneda == Moneda.USD
    assert tx_padre.monto == Decimal("15.00")
    assert tx_padre.tarjeta_id == tarjeta_ars.id
    assert tx_padre.metodo_pago == MetodoPago.CREDITO
    assert tx_padre.es_padre_cuotas is True

    # 2. Grupo de cuotas queda en USD
    assert tx_padre.grupo_cuotas_id is not None
    grupo = db.query(GrupoCuotas).filter(GrupoCuotas.id == tx_padre.grupo_cuotas_id).first()
    assert grupo is not None
    assert grupo.moneda == Moneda.USD
    assert grupo.monto_total == Decimal("15.00")
    assert grupo.cantidad_cuotas == 1
    assert grupo.tarjeta_id == tarjeta_ars.id

    # 3. Cuota hija queda en USD
    cuotas = db.query(Cuota).filter(Cuota.grupo_id == grupo.id).all()
    assert len(cuotas) == 1
    cuota = cuotas[0]
    assert cuota.monto_proyectado == Decimal("15.00")
    assert cuota.pagada is False

    tx_hija = db.query(Transaccion).filter(Transaccion.id == cuota.transaccion_id).first()
    assert tx_hija is not None
    assert tx_hija.moneda == Moneda.USD
    assert tx_hija.monto == Decimal("15.00")
    assert tx_hija.es_cuota_hija is True
    assert tx_hija.tarjeta_id == tarjeta_ars.id

    # 4. Ningún saldo cambia
    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == saldo_inicial_billetera


def test_egreso_debito_usd_en_billetera_pesos_falla(db: Session):
    """
    Un egreso con débito en USD en una billetera en pesos sin monto_original
    sigue dando el error de moneda que da hoy.
    """
    usuario, billetera_ars, _, categoria = _setup_usuario_y_cuentas(db)

    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("15.00"),
        moneda=Moneda.USD,
        descripcion="Compra débito en USD en cuenta pesos",
        fecha=date(2026, 10, 15),
        categoria_id=categoria.id,
        billetera_id=billetera_ars.id,
        tarjeta_id=None,
        metodo_pago=MetodoPago.DEBITO,
    )

    with pytest.raises(HTTPException) as exc_info:
        crear_transaccion(db, usuario.id, data, commit=True)

    assert exc_info.value.status_code == 400
    assert "no coincide con la moneda de la billetera" in exc_info.value.detail
