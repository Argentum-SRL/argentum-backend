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

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import RedTarjeta
from app.models.transaccion import MetodoPago, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.tarjeta_credito import TarjetaCreditoCreate
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import cuotas_service, tarjeta_service, transaccion_service
from app.routers.grupos_cuotas import mapear_grupo_resumen


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


@pytest.fixture(name="escenario")
def escenario_fixture(db: Session, monkeypatch):
    fecha_hoy = date(2026, 10, 15)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.calendario_tarjeta_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.cuotas_service.hoy_argentina", lambda: fecha_hoy)

    usuario = Usuario(
        id=uuid4(),
        email="test_adelantar@argentum.com",
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

    # Tarjeta S de test_calendario_tarjeta.py (cuarto jueves, vence 13 días después)
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

    return usuario, billetera, categoria, tarjeta


def test_adelantar_cuotas_hoy_15_octubre(db: Session, escenario, monkeypatch):
    """
    hoy 2026-10-15, grupo de 6 cuotas con 2 pagadas y 4 sin pagar que vencen
    2026-11-04, 2026-12-09, 2027-01-06 y 2027-02-10:
    vencimiento_adelanto 2026-11-04;
    después de adelantar, las 4 vencen 2026-11-04 y sus hijas tienen esa fecha;
    las pagadas no cambian;
    mismos saldos de billeteras y misma cantidad de transacciones que antes.
    """
    usuario, billetera, categoria, tarjeta = escenario
    fecha_hoy = date(2026, 10, 15)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.calendario_tarjeta_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.cuotas_service.hoy_argentina", lambda: fecha_hoy)

    # Crear grupo de 6 cuotas con cuota_inicial = 3 (2 pagadas, 4 sin pagar)
    data = TransaccionCreate(
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("60000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Compra 6 cuotas voy por la 3",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=6,
            cuota_inicial=3,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("60000.00"),
            proximo_resumen=False,
        ),
    )
    padre = transaccion_service.crear_transaccion(db, usuario.id, data)
    grupo_id = padre.grupo_cuotas_id

    grupo = db.get(GrupoCuotas, grupo_id)
    cuotas_antes = sorted(grupo.cuotas, key=lambda c: c.numero_cuota)

    # Verificar estado inicial de las cuotas
    assert len(cuotas_antes) == 6
    assert cuotas_antes[0].pagada is True
    assert cuotas_antes[1].pagada is True
    assert [c.pagada for c in cuotas_antes[2:]] == [False, False, False, False]
    assert [c.fecha_vencimiento for c in cuotas_antes[2:]] == [
        date(2026, 11, 4),
        date(2026, 12, 9),
        date(2027, 1, 6),
        date(2027, 2, 10),
    ]

    vencimientos_pagadas_antes = [c.fecha_vencimiento for c in cuotas_antes[:2]]
    fechas_hijas_pagadas_antes = [c.transaccion.fecha for c in cuotas_antes[:2]]

    # vencimiento_adelanto en GrupoCuotasResumen debe ser 2026-11-04
    resumen_antes = mapear_grupo_resumen(db, grupo)
    assert resumen_antes["vencimiento_adelanto"] == date(2026, 11, 4)

    # Capturar cantidad de transacciones y saldos antes
    tx_count_antes = db.query(Transaccion).count()
    saldo_billetera_antes = db.get(Billetera, billetera.id).saldo_actual

    # Adelantar grupo
    grupo_act = cuotas_service.adelantar_grupo(db, grupo.id, usuario.id, commit=True)

    cuotas_desp = sorted(grupo_act.cuotas, key=lambda c: c.numero_cuota)

    # Las 4 cuotas sin pagar ahora vencen 2026-11-04 y sus hijas tienen esa fecha
    for c in cuotas_desp[2:]:
        assert c.fecha_vencimiento == date(2026, 11, 4)
        assert c.transaccion.fecha == date(2026, 11, 4)

    # Las 2 pagadas no cambian
    for i in range(2):
        assert cuotas_desp[i].fecha_vencimiento == vencimientos_pagadas_antes[i]
        assert cuotas_desp[i].transaccion.fecha == fechas_hijas_pagadas_antes[i]

    # Mismos saldos de billeteras y misma cantidad de transacciones que antes
    tx_count_desp = db.query(Transaccion).count()
    assert tx_count_desp == tx_count_antes

    saldo_billetera_desp = db.get(Billetera, billetera.id).saldo_actual
    assert saldo_billetera_desp == saldo_billetera_antes


def test_adelantar_cuotas_hoy_25_octubre(db: Session, escenario, monkeypatch):
    """
    el mismo grupo con hoy 2026-10-25 (ya cerró el 22/10):
    vencimiento_adelanto 2026-12-09;
    la de 2026-11-04 queda y las otras 3 pasan a 2026-12-09.
    """
    usuario, billetera, categoria, tarjeta = escenario

    # Fijar hoy inicial en 2026-10-15 para crear el mismo grupo
    fecha_hoy = date(2026, 10, 15)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.calendario_tarjeta_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.cuotas_service.hoy_argentina", lambda: fecha_hoy)

    data = TransaccionCreate(
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("60000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Compra 6 cuotas voy por la 3",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=6,
            cuota_inicial=3,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("60000.00"),
            proximo_resumen=False,
        ),
    )
    padre = transaccion_service.crear_transaccion(db, usuario.id, data)
    grupo = db.get(GrupoCuotas, padre.grupo_cuotas_id)

    # Ahora cambiar hoy a 2026-10-25 (ya cerró el 22/10)
    fecha_hoy_25 = date(2026, 10, 25)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha_hoy_25)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: fecha_hoy_25)
    monkeypatch.setattr("app.services.calendario_tarjeta_service.hoy_argentina", lambda: fecha_hoy_25)
    monkeypatch.setattr("app.services.cuotas_service.hoy_argentina", lambda: fecha_hoy_25)

    # vencimiento_adelanto debe ser 2026-12-09
    resumen = mapear_grupo_resumen(db, grupo)
    assert resumen["vencimiento_adelanto"] == date(2026, 12, 9)

    # Adelantar grupo
    grupo_act = cuotas_service.adelantar_grupo(db, grupo.id, usuario.id, commit=True)
    cuotas_desp = sorted(grupo_act.cuotas, key=lambda c: c.numero_cuota)

    # La de 2026-11-04 (cuota 3) queda intacta
    assert cuotas_desp[2].fecha_vencimiento == date(2026, 11, 4)
    assert cuotas_desp[2].transaccion.fecha == date(2026, 11, 4)

    # Las otras 3 (cuotas 4, 5, 6) pasan a 2026-12-09
    for c in cuotas_desp[3:]:
        assert c.fecha_vencimiento == date(2026, 12, 9)
        assert c.transaccion.fecha == date(2026, 12, 9)


def test_adelantar_grupo_sin_tarjeta(db: Session, escenario):
    """
    grupo sin tarjeta: 400 con el texto exacto
    "Esta opción es solo para compras con tarjeta."
    """
    usuario, billetera, categoria, _ = escenario

    data = TransaccionCreate(
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Compra sin tarjeta en cuotas",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        tarjeta_id=None,
        es_padre_cuotas=True,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=3,
            cuota_inicial=1,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("30000.00"),
        ),
    )
    padre = transaccion_service.crear_transaccion(db, usuario.id, data)
    grupo = db.get(GrupoCuotas, padre.grupo_cuotas_id)

    # vencimiento_adelanto en resumen debe ser None
    resumen = mapear_grupo_resumen(db, grupo)
    assert resumen["vencimiento_adelanto"] is None

    with pytest.raises(HTTPException) as exc_info:
        cuotas_service.adelantar_grupo(db, grupo.id, usuario.id, commit=True)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Esta opción es solo para compras con tarjeta."


def test_adelantar_grupo_todas_cuotas_ya_en_destino(db: Session, escenario, monkeypatch):
    """
    grupo con todas sus cuotas ya en el resumen destino:
    400 "No hay cuotas para adelantar."
    """
    usuario, billetera, categoria, tarjeta = escenario
    fecha_hoy = date(2026, 10, 15)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.calendario_tarjeta_service.hoy_argentina", lambda: fecha_hoy)
    monkeypatch.setattr("app.services.cuotas_service.hoy_argentina", lambda: fecha_hoy)

    # Crear compra de 2 cuotas: cuota 1 pagada, cuota 2 sin pagar (vence 2026-11-04)
    data = TransaccionCreate(
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Compra 2 cuotas",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=2,
            cuota_inicial=2,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("20000.00"),
        ),
    )
    padre = transaccion_service.crear_transaccion(db, usuario.id, data)
    grupo = db.get(GrupoCuotas, padre.grupo_cuotas_id)

    # La única cuota pendiente vence el 2026-11-04, que ya es el resumen destino.
    with pytest.raises(HTTPException) as exc_info:
        cuotas_service.adelantar_grupo(db, grupo.id, usuario.id, commit=True)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No hay cuotas para adelantar."
