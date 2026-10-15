"""
Tests para calendario_tarjeta_service y fechas reales de tarjeta (fase5a_fechas).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.main import app
from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import MetodoPago, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.tarjeta_credito import FechasResumenBody, TarjetaCreditoCreate, TarjetaCreditoUpdate
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import calendario_tarjeta_service, tarjeta_service, transaccion_service


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


@pytest.fixture(name="usuario_base")
def usuario_fixture(db: Session):
    user = Usuario(
        id=uuid4(),
        email="test_calendario@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=user.id,
        nombre="Cuenta Banco",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        es_inversion=False,
    )
    db.add(billetera)

    cat = Categoria(
        id=uuid4(),
        nombre="General",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(cat)
    db.commit()
    return user, billetera, cat


def test_tarjeta_s_flujo_completo(db: Session, usuario_base):
    usuario, billetera, _ = usuario_base

    # Tarjeta S, alta con ultimo_cierre 2026-09-24, proximo_cierre 2026-10-22 y proximo_vencimiento 2026-11-04
    data_create = TarjetaCreditoCreate(
        nombre="Tarjeta S",
        red=RedTarjeta.VISA,
        billetera_id=billetera.id,
        moneda="ARS",
        ultimo_cierre=date(2026, 9, 24),
        proximo_cierre=date(2026, 10, 22),
        proximo_vencimiento=date(2026, 11, 4),
    )
    tarjeta = tarjeta_service.crear_tarjeta(db, usuario.id, data_create)

    # Verificaciones regla y texto
    assert tarjeta.regla_cierre == "dia_semana"
    assert tarjeta.semana_cierre == 4
    assert tarjeta.dia_semana_cierre == 3
    assert tarjeta.dias_hasta_vencimiento == 13
    assert calendario_tarjeta_service.texto_fechas(tarjeta) == "Cierra el cuarto jueves de cada mes y vence 13 días después"

    # Resúmenes de 2026-10-01 a 2027-01-31
    res = calendario_tarjeta_service.resumenes(tarjeta, date(2026, 10, 1), date(2027, 1, 31))
    esperados = [
        (date(2026, 10, 22), date(2026, 11, 4)),
        (date(2026, 11, 26), date(2026, 12, 9)),
        (date(2026, 12, 24), date(2027, 1, 6)),
        (date(2027, 1, 28), date(2027, 2, 10)),
    ]
    assert res == esperados

    # resumen_de_compra:
    # 2026-10-22 → (2026-10-22, 2026-11-04)
    assert calendario_tarjeta_service.resumen_de_compra(tarjeta, date(2026, 10, 22)) == (date(2026, 10, 22), date(2026, 11, 4))
    # 2026-10-23 → (2026-11-26, 2026-12-09)
    assert calendario_tarjeta_service.resumen_de_compra(tarjeta, date(2026, 10, 23)) == (date(2026, 11, 26), date(2026, 12, 9))
    # 2026-10-23 con proximo_resumen → (2026-12-24, 2027-01-06)
    assert calendario_tarjeta_service.resumen_de_compra(tarjeta, date(2026, 10, 23), proximo_resumen=True) == (date(2026, 12, 24), date(2027, 1, 6))

    # vencimiento_proximo con hoy 2026-11-05 → 2026-12-09
    assert calendario_tarjeta_service.vencimiento_proximo(tarjeta, date(2026, 11, 5)) == date(2026, 12, 9)

    # cierre_de_vencimiento(2026-12-09) → 2026-11-26
    assert calendario_tarjeta_service.cierre_de_vencimiento(tarjeta, date(2026, 12, 9)) == date(2026, 11, 26)


def test_tarjeta_m(db: Session, usuario_base):
    usuario, billetera, _ = usuario_base
    # Tarjeta M, alta con ultimo_cierre 2026-09-17, proximo_cierre 2026-10-15 y proximo_vencimiento 2026-10-26
    data_create = TarjetaCreditoCreate(
        nombre="Tarjeta M",
        red=RedTarjeta.MASTERCARD,
        billetera_id=billetera.id,
        moneda="ARS",
        ultimo_cierre=date(2026, 9, 17),
        proximo_cierre=date(2026, 10, 15),
        proximo_vencimiento=date(2026, 10, 26),
    )
    tarjeta = tarjeta_service.crear_tarjeta(db, usuario.id, data_create)

    assert tarjeta.regla_cierre == "dia_semana"
    assert tarjeta.semana_cierre == 3
    assert tarjeta.dia_semana_cierre == 3  # jueves
    assert tarjeta.dias_hasta_vencimiento == 11

    # resumen de noviembre: (2026-11-19, 2026-11-30)
    res_nov = calendario_tarjeta_service.resumenes(tarjeta, date(2026, 11, 1), date(2026, 11, 30))
    assert res_nov == [(date(2026, 11, 19), date(2026, 11, 30))]


def test_tarjeta_f(db: Session, usuario_base):
    usuario, billetera, _ = usuario_base
    # Tarjeta F, alta con ultimo_cierre 2026-09-10, proximo_cierre 2026-10-10 y proximo_vencimiento 2026-11-03
    data_create = TarjetaCreditoCreate(
        nombre="Tarjeta F",
        red=RedTarjeta.VISA,
        billetera_id=billetera.id,
        moneda="ARS",
        ultimo_cierre=date(2026, 9, 10),
        proximo_cierre=date(2026, 10, 10),
        proximo_vencimiento=date(2026, 11, 3),
    )
    tarjeta = tarjeta_service.crear_tarjeta(db, usuario.id, data_create)

    assert tarjeta.regla_cierre == "dia_fijo"
    assert tarjeta.dia_cierre == 10
    assert tarjeta.dia_vencimiento == 3
    assert calendario_tarjeta_service.texto_fechas(tarjeta) == "Cierra el 10 y vence el 3 de cada mes"

    # resumen_de_compra(2026-10-11) → (2026-11-10, 2026-12-03)
    assert calendario_tarjeta_service.resumen_de_compra(tarjeta, date(2026, 10, 11)) == (date(2026, 11, 10), date(2026, 12, 3))


def test_tarjeta_sin_ultimo_cierre(db: Session, usuario_base):
    usuario, billetera, _ = usuario_base
    # Tarjeta sin ultimo_cierre, con proximo_cierre 2026-10-22 y proximo_vencimiento 2026-11-04: "dia_fijo" 22 y 4.
    data_create = TarjetaCreditoCreate(
        nombre="Tarjeta Sin Ultimo",
        red=RedTarjeta.AMEX,
        billetera_id=billetera.id,
        moneda="ARS",
        proximo_cierre=date(2026, 10, 22),
        proximo_vencimiento=date(2026, 11, 4),
    )
    tarjeta = tarjeta_service.crear_tarjeta(db, usuario.id, data_create)

    assert tarjeta.regla_cierre == "dia_fijo"
    assert tarjeta.dia_cierre == 22
    assert tarjeta.dia_vencimiento == 4


def test_alta_vencimiento_anterior_a_cierre(db: Session, usuario_base):
    usuario, billetera, _ = usuario_base
    # Alta con proximo_vencimiento anterior a proximo_cierre: 400 con texto exacto
    data_create = TarjetaCreditoCreate(
        nombre="Tarjeta Invalida",
        red=RedTarjeta.VISA,
        billetera_id=billetera.id,
        moneda="ARS",
        proximo_cierre=date(2026, 10, 22),
        proximo_vencimiento=date(2026, 10, 15),
    )
    with pytest.raises(HTTPException) as excinfo:
        tarjeta_service.crear_tarjeta(db, usuario.id, data_create)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "El vencimiento tiene que ser posterior al cierre y dentro del mes siguiente."


def test_tarjeta_s_fechas_resumen_override_y_cuotas(db: Session, usuario_base):
    usuario, billetera, categoria = usuario_base

    # Tarjeta S
    data_create = TarjetaCreditoCreate(
        nombre="Tarjeta S Override",
        red=RedTarjeta.VISA,
        billetera_id=billetera.id,
        moneda="ARS",
        ultimo_cierre=date(2026, 9, 24),
        proximo_cierre=date(2026, 10, 22),
        proximo_vencimiento=date(2026, 11, 4),
    )
    tarjeta = tarjeta_service.crear_tarjeta(db, usuario.id, data_create)

    # Crear una compra con cuota no pagada que vence el 2026-12-09
    tx_data = TransaccionCreate(
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        categoria_id=categoria.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 23),
        metodo_pago=MetodoPago.CREDITO,
        info_cuotas=InfoCuotas(cantidad_cuotas=1, monto_total=Decimal("1000.00")),
        descripcion="Compra prueba override",
    )
    tx_padre = transaccion_service.crear_transaccion(db, usuario.id, tx_data, commit=True)
    cuota = db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre.id).first()
    assert cuota.fecha_vencimiento == date(2026, 12, 9)
    tx_hija = db.query(Transaccion).filter(Transaccion.id == cuota.transaccion_id).first()
    assert tx_hija.fecha == date(2026, 12, 9)

    # En S, fechas-resumen con fecha_cierre 2026-11-27 y fecha_vencimiento 2026-12-10
    body = FechasResumenBody(
        fecha_cierre=date(2026, 11, 27),
        fecha_vencimiento=date(2026, 12, 10),
    )
    res_override = tarjeta_service.actualizar_fechas_resumen(
        db, usuario.id, tarjeta.id, body.fecha_cierre, body.fecha_vencimiento
    )
    assert res_override["ok"] is True

    db.refresh(tarjeta)
    # resumen_de_compra(2026-11-27) → (2026-11-27, 2026-12-10)
    assert calendario_tarjeta_service.resumen_de_compra(tarjeta, date(2026, 11, 27)) == (date(2026, 11, 27), date(2026, 12, 10))
    # resumen_de_compra(2026-10-23) → (2026-11-27, 2026-12-10)
    assert calendario_tarjeta_service.resumen_de_compra(tarjeta, date(2026, 10, 23)) == (date(2026, 11, 27), date(2026, 12, 10))

    # una cuota no pagada que vencía el 2026-12-09 pasa al 2026-12-10, y su hija también
    db.refresh(cuota)
    db.refresh(tx_hija)
    assert cuota.fecha_vencimiento == date(2026, 12, 10)
    assert tx_hija.fecha == date(2026, 12, 10)


def test_edicion_tarjeta_recalculo_cuotas(db: Session, usuario_base, monkeypatch):
    usuario, billetera, categoria = usuario_base
    hoy_fijo = date(2026, 10, 15)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: hoy_fijo)

    # Tarjeta "dia_fijo" 22/4
    data_create = TarjetaCreditoCreate(
        nombre="Tarjeta Fija",
        red=RedTarjeta.VISA,
        billetera_id=billetera.id,
        moneda="ARS",
        proximo_cierre=date(2026, 10, 22),
        proximo_vencimiento=date(2026, 11, 4),
    )
    tarjeta = tarjeta_service.crear_tarjeta(db, usuario.id, data_create)
    assert tarjeta.regla_cierre == "dia_fijo"
    assert tarjeta.dia_cierre == 22
    assert tarjeta.dia_vencimiento == 4

    # Crear compra en 3 cuotas no pagadas que vencen 2026-11-04, 2026-12-04 y 2027-01-04
    tx_data = TransaccionCreate(
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        categoria_id=categoria.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("3000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        metodo_pago=MetodoPago.CREDITO,
        info_cuotas=InfoCuotas(cantidad_cuotas=3, monto_total=Decimal("3000.00")),
        descripcion="3 cuotas",
    )
    tx_padre = transaccion_service.crear_transaccion(db, usuario.id, tx_data, commit=True)
    grupo = db.query(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre.id).first()
    cuotas = db.query(Cuota).filter(Cuota.grupo_id == grupo.id).order_by(Cuota.numero_cuota.asc()).all()
    txs = db.query(Transaccion).filter(Transaccion.grupo_cuotas_id == grupo.id, Transaccion.es_cuota_hija == True).order_by(Transaccion.fecha.asc()).all()

    assert [c.fecha_vencimiento for c in cuotas] == [date(2026, 11, 4), date(2026, 12, 4), date(2027, 1, 4)]

    # Editada a S (ultimo 2026-09-24, proximo 2026-10-22, venc 2026-11-04), con hoy 2026-10-15:
    # quedan en 2026-11-04, 2026-12-09 y 2027-01-06, y cuotas_recalculadas = 2.
    data_update = TarjetaCreditoUpdate(
        ultimo_cierre=date(2026, 9, 24),
        proximo_cierre=date(2026, 10, 22),
        proximo_vencimiento=date(2026, 11, 4),
    )
    res_update = tarjeta_service.actualizar_tarjeta(db, usuario.id, tarjeta.id, data_update)

    assert res_update.cuotas_recalculadas == 2
    for c in cuotas:
        db.refresh(c)
    for tx in txs:
        db.refresh(tx)

    assert cuotas[0].fecha_vencimiento == date(2026, 11, 4)
    assert cuotas[1].fecha_vencimiento == date(2026, 12, 9)
    assert cuotas[2].fecha_vencimiento == date(2027, 1, 6)

    assert txs[0].fecha == date(2026, 11, 4)
    assert txs[1].fecha == date(2026, 12, 9)
    assert txs[2].fecha == date(2027, 1, 6)


def test_compra_en_cuotas_hijas_fechas(db: Session, usuario_base):
    usuario, billetera, categoria = usuario_base

    # Tarjeta S
    data_create = TarjetaCreditoCreate(
        nombre="Tarjeta S Compra",
        red=RedTarjeta.VISA,
        billetera_id=billetera.id,
        moneda="ARS",
        ultimo_cierre=date(2026, 9, 24),
        proximo_cierre=date(2026, 10, 22),
        proximo_vencimiento=date(2026, 11, 4),
    )
    tarjeta = tarjeta_service.crear_tarjeta(db, usuario.id, data_create)

    # Una compra con S por crear_transaccion con fecha 2026-10-23 en 3 cuotas:
    # hijas con fechas 2026-12-09, 2027-01-06 y 2027-02-10.
    tx_data = TransaccionCreate(
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        categoria_id=categoria.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("3000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 23),
        metodo_pago=MetodoPago.CREDITO,
        info_cuotas=InfoCuotas(cantidad_cuotas=3, monto_total=Decimal("3000.00")),
        descripcion="Compra 3 cuotas",
    )
    tx_padre = transaccion_service.crear_transaccion(db, usuario.id, tx_data, commit=True)
    assert tx_padre.grupo_cuotas_id is not None

    hijas = (
        db.query(Transaccion)
        .filter(Transaccion.grupo_cuotas_id == tx_padre.grupo_cuotas_id, Transaccion.es_cuota_hija == True)
        .order_by(Transaccion.fecha.asc())
        .all()
    )
    assert len(hijas) == 3
    fechas_hijas = [h.fecha for h in hijas]
    assert fechas_hijas == [date(2026, 12, 9), date(2027, 1, 6), date(2027, 2, 10)]
