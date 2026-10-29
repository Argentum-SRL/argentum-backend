# -*- coding: utf-8 -*-
"""
tests/test_frescura.py - Pruebas del servicio de frescura de datos (fase_g2_saldo_confianza).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.frescura_service import Frescura, calcular_frescura, frescura_desde, umbral_dias
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
        email="test_frescura@argentum.com",
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


def test_umbral_dias():
    # Menos de 5 intervalos devuelve siempre 7
    assert umbral_dias([]) == 7
    assert umbral_dias([19]) == 7
    assert umbral_dias([30, 30, 30, 30]) == 7

    # Con 5 o más intervalos: mediana + 3 * mad con piso 7
    assert umbral_dias([30, 30, 30, 30, 30]) == 30
    assert umbral_dias([1] * 24) == 7

    # Mediana 11, mad 2 -> 11 + 3 * 2 = 17
    assert umbral_dias([8, 10, 10, 12, 14, 20]) == 17

    # Mediana 9.5, mad 2 -> 9.5 + 3 * 2 = 15.5 -> hacia arriba = 16
    assert umbral_dias([6, 8, 9, 10, 12, 15]) == 16


def test_frescura_desde():
    hoy = date(2026, 11, 2)

    # Sin cargas
    f_sin = frescura_desde([], None, hoy)
    assert f_sin == Frescura(None, None, 7, 0, False)

    # Cargas todos los días del 01/10 al 25/10 -> última carga 25/10, hoy 02/11
    # días sin cargar = 8, umbral = 7, intervalos = 24, datos_viejos = True
    cargas_25 = [date(2026, 10, i) for i in range(1, 26)]
    f_25 = frescura_desde(cargas_25, date(2026, 10, 25), hoy)
    assert f_25.dias_sin_cargar == 8
    assert f_25.umbral_dias == 7
    assert f_25.intervalos == 24
    assert f_25.datos_viejos is True

    # Del 01/10 al 26/10 -> días sin cargar = 7, umbral = 7, datos_viejos = False
    cargas_26 = [date(2026, 10, i) for i in range(1, 27)]
    f_26 = frescura_desde(cargas_26, date(2026, 10, 26), hoy)
    assert f_26.dias_sin_cargar == 7
    assert f_26.datos_viejos is False

    # Cargas el 01/08, 09/08, 19/08, 29/08, 10/09, 24/09 y 14/10 (intervalos 8, 10, 10, 12, 14, 20)
    cargas_pautas = [
        date(2026, 8, 1),
        date(2026, 8, 9),
        date(2026, 8, 19),
        date(2026, 8, 29),
        date(2026, 9, 10),
        date(2026, 9, 24),
        date(2026, 10, 14),
    ]
    # Con hoy 31/10 -> dias_sin_cargar 17, umbral 17, datos_viejos False
    f_31_10 = frescura_desde(cargas_pautas, date(2026, 10, 14), date(2026, 10, 31))
    assert f_31_10.dias_sin_cargar == 17
    assert f_31_10.umbral_dias == 17
    assert f_31_10.datos_viejos is False

    # Con hoy 01/11 -> 18, True
    f_01_11 = frescura_desde(cargas_pautas, date(2026, 10, 14), date(2026, 11, 1))
    assert f_01_11.dias_sin_cargar == 18
    assert f_01_11.umbral_dias == 17
    assert f_01_11.datos_viejos is True

    # Cargas el 01/10 y el 20/10 con hoy 28/10 -> umbral 7, dias_sin_cargar 8, True
    f_28_10 = frescura_desde([date(2026, 10, 1), date(2026, 10, 20)], date(2026, 10, 20), date(2026, 10, 28))
    assert f_28_10.umbral_dias == 7
    assert f_28_10.dias_sin_cargar == 8
    assert f_28_10.datos_viejos is True


def test_calcular_frescura_bd(db: Session, usuario_test: Usuario):
    hoy = date(2026, 11, 2)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera Test",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    billetera_otra = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera Destino",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500.00"),
        estado=EstadoBilletera.ACTIVA,
    )

    otro_usuario = Usuario(
        id=uuid4(),
        email="otro_usuario@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    billetera_ajena = Billetera(
        id=uuid4(),
        usuario_id=otro_usuario.id,
        nombre="Billetera Ajena",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([billetera, billetera_otra, otro_usuario, billetera_ajena])
    db.flush()

    # 1. Transacción MANUAL creada el 10/10 10:00 (hora argentina)
    dt_manual = datetime(2026, 10, 10, 10, 0, tzinfo=TZ_ARGENTINA)
    tx_manual = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        origen=OrigenTransaccion.MANUAL,
        metodo_pago=MetodoPago.EFECTIVO,
        fecha_creacion=dt_manual,
    )

    # 2. Transacción IA_WPP creada el 12/10 11:00 (hora argentina)
    dt_wpp = datetime(2026, 10, 12, 11, 0, tzinfo=TZ_ARGENTINA)
    tx_wpp = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("150.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 12),
        origen=OrigenTransaccion.IA_WPP,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        fecha_creacion=dt_wpp,
    )

    # 3. Transferencia interna creada el 15/10 12:00 (hora argentina)
    dt_tr = datetime(2026, 10, 15, 12, 0, tzinfo=TZ_ARGENTINA)
    tr = TransferenciaInterna(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_origen_id=billetera.id,
        billetera_destino_id=billetera_otra.id,
        monto=Decimal("200.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        fecha_creacion=dt_tr,
    )

    # 4. Ajuste de saldo creado el 20/10 13:00 (hora argentina)
    dt_aj = datetime(2026, 10, 20, 13, 0, tzinfo=TZ_ARGENTINA)
    aj = AjusteSaldo(
        id=uuid4(),
        billetera_id=billetera.id,
        monto=Decimal("50.00"),
        saldo_anterior=Decimal("1000.00"),
        saldo_declarado=Decimal("1050.00"),
        fecha=date(2026, 10, 20),
        fecha_creacion=dt_aj,
    )

    # 5. Transacción RECURRENTE creada el 30/10 10:00 (NO debe contar como carga del usuario)
    dt_rec = datetime(2026, 10, 30, 10, 0, tzinfo=TZ_ARGENTINA)
    tx_rec = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("300.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 30),
        origen=OrigenTransaccion.RECURRENTE,
        metodo_pago=MetodoPago.DEBITO,
        fecha_creacion=dt_rec,
    )

    # 6. Transacción MANUAL de otro usuario el 01/11 10:00 (NO debe contar)
    dt_ajena = datetime(2026, 11, 1, 10, 0, tzinfo=TZ_ARGENTINA)
    tx_ajena = Transaccion(
        id=uuid4(),
        usuario_id=otro_usuario.id,
        billetera_id=billetera_ajena.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("500.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 1),
        origen=OrigenTransaccion.MANUAL,
        metodo_pago=MetodoPago.EFECTIVO,
        fecha_creacion=dt_ajena,
    )

    db.add_all([tx_manual, tx_wpp, tr, aj, tx_rec, tx_ajena])
    db.commit()

    frescura = calcular_frescura(db, usuario_test.id, hoy=hoy)

    assert frescura.ultima_carga == date(2026, 10, 20)
    assert frescura.dias_sin_cargar == 13
    assert frescura.intervalos == 3
    assert frescura.umbral_dias == 7
    assert frescura.datos_viejos is True

    esperado_dict = {
        "ultima_carga": "2026-10-20",
        "dias_sin_cargar": 13,
        "umbral_dias": 7,
        "intervalos": 3,
        "datos_viejos": True,
    }
    assert frescura.a_dict() == esperado_dict

