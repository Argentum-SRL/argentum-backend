"""
Tests para compras con tarjeta cargadas con 'voy por la cuota N' (fase5a_cuota_n).
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
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.tarjeta_credito import RedTarjeta
from app.models.transaccion import MetodoPago, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.tarjeta_credito import TarjetaCreditoCreate
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import tarjeta_service, transaccion_service


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


@pytest.fixture(name="escenario_base")
def escenario_base_fixture(db: Session, monkeypatch):
    # Fijar hoy en 2026-10-15
    fecha_hoy = date(2026, 10, 15)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.calendario_tarjeta_service.hoy_argentina", lambda: fecha_hoy)

    usuario = Usuario(
        id=uuid4(),
        email="test_cuota_n@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(usuario)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Banco Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        es_inversion=False,
    )
    db.add(billetera)

    categoria = Categoria(
        id=uuid4(),
        nombre="Compras",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(categoria)
    db.commit()

    # Tarjeta S de test_calendario_tarjeta.py: cuarto jueves, vence 13 días después
    data_tarjeta = TarjetaCreditoCreate(
        nombre="Tarjeta S",
        red=RedTarjeta.VISA,
        billetera_id=billetera.id,
        moneda="ARS",
        ultimo_cierre=date(2026, 9, 24),
        proximo_cierre=date(2026, 10, 22),
        proximo_vencimiento=date(2026, 11, 4),
    )
    tarjeta = tarjeta_service.crear_tarjeta(db, usuario.id, data_tarjeta)
    assert tarjeta.regla_cierre == "dia_semana"
    assert tarjeta.semana_cierre == 4
    assert tarjeta.dia_semana_cierre == 3
    assert tarjeta.dias_hasta_vencimiento == 13

    return usuario, billetera, categoria, tarjeta


def test_compra_cuota_inicial_4_de_6(db: Session, escenario_base):
    """
    Compra con fecha 2026-06-10 en 6 cuotas con cuota_inicial 4:
    cuotas 1, 2 y 3 pagadas con vencimientos 2026-08-05, 2026-09-09 y 2026-10-07;
    cuotas 4, 5 y 6 sin pagar con vencimientos 2026-11-04, 2026-12-09 y 2027-01-06.
    Ninguna hija con fecha posterior a 2027-01-06.
    """
    usuario, billetera, categoria, tarjeta = escenario_base

    data = TransaccionCreate(
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("60000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 6, 10),
        descripcion="Zapatillas 6 cuotas voy por la 4",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=6,
            cuota_inicial=4,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("60000.00"),
            proximo_resumen=False,
        ),
    )
    padre = transaccion_service.crear_transaccion(db, usuario.id, data)
    assert padre.grupo_cuotas_id is not None

    cuotas = (
        db.query(Cuota)
        .filter(Cuota.grupo_id == padre.grupo_cuotas_id)
        .order_by(Cuota.numero_cuota)
        .all()
    )
    assert len(cuotas) == 6

    # Cuotas 1, 2 y 3 pagadas con vencimientos esperados
    assert cuotas[0].numero_cuota == 1
    assert cuotas[0].pagada is True
    assert cuotas[0].fecha_vencimiento == date(2026, 8, 5)

    assert cuotas[1].numero_cuota == 2
    assert cuotas[1].pagada is True
    assert cuotas[1].fecha_vencimiento == date(2026, 9, 9)

    assert cuotas[2].numero_cuota == 3
    assert cuotas[2].pagada is True
    assert cuotas[2].fecha_vencimiento == date(2026, 10, 7)

    # Cuotas 4, 5 y 6 sin pagar con vencimientos esperados
    assert cuotas[3].numero_cuota == 4
    assert cuotas[3].pagada is False
    assert cuotas[3].fecha_vencimiento == date(2026, 11, 4)

    assert cuotas[4].numero_cuota == 5
    assert cuotas[4].pagada is False
    assert cuotas[4].fecha_vencimiento == date(2026, 12, 9)

    assert cuotas[5].numero_cuota == 6
    assert cuotas[5].pagada is False
    assert cuotas[5].fecha_vencimiento == date(2027, 1, 6)

    # Comprobar fechas de transacciones hijas
    for c in cuotas:
        hija = db.get(Transaccion, c.transaccion_id)
        assert hija is not None
        assert hija.fecha == c.fecha_vencimiento
        assert hija.fecha <= date(2027, 1, 6)


def test_compra_cuota_inicial_1_de_3_no_cambia(db: Session, escenario_base):
    """
    Compra con fecha 2026-10-23 en 3 cuotas con cuota_inicial 1:
    2026-12-09, 2027-01-06 y 2027-02-10 (no cambia).
    """
    usuario, billetera, categoria, tarjeta = escenario_base

    data = TransaccionCreate(
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 23),
        descripcion="Compra 3 cuotas normal",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=3,
            cuota_inicial=1,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("30000.00"),
            proximo_resumen=False,
        ),
    )
    padre = transaccion_service.crear_transaccion(db, usuario.id, data)
    assert padre.grupo_cuotas_id is not None

    cuotas = (
        db.query(Cuota)
        .filter(Cuota.grupo_id == padre.grupo_cuotas_id)
        .order_by(Cuota.numero_cuota)
        .all()
    )
    assert len(cuotas) == 3

    assert cuotas[0].numero_cuota == 1
    assert cuotas[0].pagada is False
    assert cuotas[0].fecha_vencimiento == date(2026, 12, 9)

    assert cuotas[1].numero_cuota == 2
    assert cuotas[1].pagada is False
    assert cuotas[1].fecha_vencimiento == date(2027, 1, 6)

    assert cuotas[2].numero_cuota == 3
    assert cuotas[2].pagada is False
    assert cuotas[2].fecha_vencimiento == date(2027, 2, 10)


def test_compra_cuota_inicial_3_de_3_cargada_hoy(db: Session, escenario_base):
    """
    Compra en 3 cuotas con cuota_inicial 3 cargada hoy:
    cuotas 1 y 2 pagadas (2026-09-09 y 2026-10-07), cuota 3 sin pagar 2026-11-04.
    """
    usuario, billetera, categoria, tarjeta = escenario_base

    data = TransaccionCreate(
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Compra 3 cuotas voy por la 3",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=3,
            cuota_inicial=3,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("30000.00"),
            proximo_resumen=False,
        ),
    )
    padre = transaccion_service.crear_transaccion(db, usuario.id, data)
    assert padre.grupo_cuotas_id is not None

    cuotas = (
        db.query(Cuota)
        .filter(Cuota.grupo_id == padre.grupo_cuotas_id)
        .order_by(Cuota.numero_cuota)
        .all()
    )
    assert len(cuotas) == 3

    assert cuotas[0].numero_cuota == 1
    assert cuotas[0].pagada is True
    assert cuotas[0].fecha_vencimiento == date(2026, 9, 9)

    assert cuotas[1].numero_cuota == 2
    assert cuotas[1].pagada is True
    assert cuotas[1].fecha_vencimiento == date(2026, 10, 7)

    assert cuotas[2].numero_cuota == 3
    assert cuotas[2].pagada is False
    assert cuotas[2].fecha_vencimiento == date(2026, 11, 4)
