"""
tests/test_fecha_saldo.py — Pruebas para la fecha de saldo de billeteras y avisos de movimientos anteriores.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
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
from app.models.categoria import Categoria, TipoCategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.aviso_saldo_wpp import (
    obtener_aviso_saldo_individual,
    obtener_aviso_saldo_lote,
    texto_aviso_saldo_individual,
    texto_aviso_saldo_lote,
)
from app.schemas.transaccion import TransaccionCreate
from app.services import transaccion_service
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.saldo_billetera_service import (
    fecha_creacion_billetera,
    movimiento_anterior_al_saldo,
)
from app.utils.fecha import hoy_argentina


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
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(
            JSONB._compiler_dispatcher, "specs"
        ):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario_y_categoria(db: Session) -> tuple[Usuario, Categoria]:
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)
    c = Categoria(
        id=uuid4(),
        nombre="General",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(c)
    db.commit()
    db.refresh(u)
    db.refresh(c)
    return u, c


def test_caso_a_billetera_con_fecha_saldo(db_session: Session):
    """
    Caso A: billetera ARS saldo 1.000.000 con fecha_saldo = hoy menos 10 días.
    Egreso 100.000 con fecha hoy menos 5: saldo_actual 900.000, saldo_inicial 1.000.000.
    Egreso 50.000 con fecha hoy menos 11: saldo_actual 900.000, saldo_inicial 1.050.000.
    Egreso 20.000 con fecha hoy menos 10: saldo_actual 880.000.
    En los tres estados calcular_saldo_teorico es igual a saldo_actual.
    """
    u, c = _crear_usuario_y_categoria(db_session)
    hoy = hoy_argentina()

    # Billetera ARS saldo 1.000.000 con fecha_saldo = hoy menos 10 días
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Banco Nación",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        fecha_saldo=hoy - timedelta(days=10),
        fecha_creacion=datetime.now(timezone.utc),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)

    # 1. Egreso 100.000 con fecha hoy menos 5
    tx1_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=hoy - timedelta(days=5),
        categoria_id=c.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
        descripcion="Supermercado 1",
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=u.id,
        data=tx1_data,
        commit=True,
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("900000.00")
    assert b.saldo_inicial == Decimal("1000000.00")
    assert calcular_saldo_teorico(db_session, b.id) == Decimal("900000.00")
    assert calcular_saldo_teorico(db_session, b.id) == b.saldo_actual

    # 2. Egreso 50.000 con fecha hoy menos 11
    tx2_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=hoy - timedelta(days=11),
        categoria_id=c.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
        descripcion="Gasto viejo anterior a fecha_saldo",
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=u.id,
        data=tx2_data,
        commit=True,
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("900000.00")
    assert b.saldo_inicial == Decimal("1050000.00")
    assert calcular_saldo_teorico(db_session, b.id) == Decimal("900000.00")
    assert calcular_saldo_teorico(db_session, b.id) == b.saldo_actual

    # 3. Egreso 20.000 con fecha hoy menos 10
    tx3_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        fecha=hoy - timedelta(days=10),
        categoria_id=c.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
        descripcion="Gasto en la fecha exacta de fecha_saldo",
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=u.id,
        data=tx3_data,
        commit=True,
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("880000.00")
    assert b.saldo_inicial == Decimal("1050000.00")
    assert calcular_saldo_teorico(db_session, b.id) == Decimal("880000.00")
    assert calcular_saldo_teorico(db_session, b.id) == b.saldo_actual


def test_caso_b_billetera_sin_fecha_saldo(db_session: Session):
    """
    Caso B: billetera creada hoy sin fecha_saldo, egreso 7.000 con fecha hoy menos 3:
    saldo_actual sin cambio, saldo_inicial sube 7.000 (igual que antes del paso).
    """
    u, c = _crear_usuario_y_categoria(db_session)
    hoy = hoy_argentina()

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Efectivo Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        fecha_saldo=None,
        fecha_creacion=datetime.now(timezone.utc),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("7000.00"),
        moneda=Moneda.ARS,
        fecha=hoy - timedelta(days=3),
        categoria_id=c.id,
        metodo_pago=MetodoPago.EFECTIVO,
        billetera_id=b.id,
        descripcion="Almuerzo hace 3 días",
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=u.id,
        data=tx_data,
        commit=True,
    )
    db_session.refresh(b)

    # saldo_actual sin cambio, saldo_inicial sube 7.000
    assert b.saldo_actual == Decimal("50000.00")
    assert b.saldo_inicial == Decimal("57000.00")
    assert calcular_saldo_teorico(db_session, b.id) == Decimal("50000.00")
    assert calcular_saldo_teorico(db_session, b.id) == b.saldo_actual


def test_caso_c_validaciones_api_fecha_saldo(db_session: Session):
    """
    Caso C: fecha_saldo futura da 422 con el texto exacto;
    fecha de hace 731 días da 422 con el texto exacto.
    """
    u, _ = _crear_usuario_y_categoria(db_session)
    hoy = hoy_argentina()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u
    client = TestClient(app)

    # 1. Fecha futura -> 422 "La fecha del saldo no puede ser futura."
    res_futura = client.post(
        "/billeteras",
        json={
            "nombre": "Cuenta Futura",
            "moneda": "ARS",
            "saldo_inicial": "1000",
            "fecha_saldo": (hoy + timedelta(days=1)).isoformat(),
        },
    )
    assert res_futura.status_code == 422
    assert "La fecha del saldo no puede ser futura." in res_futura.text
    data_futura = res_futura.json()
    msg_futura = data_futura.get("detail") or data_futura.get("error", {}).get("message")
    assert msg_futura == "La fecha del saldo no puede ser futura."

    # 2. Fecha de hace 731 días -> 422 "La fecha del saldo no puede ser de hace más de 2 años."
    res_vieja = client.post(
        "/billeteras",
        json={
            "nombre": "Cuenta Muy Vieja",
            "moneda": "ARS",
            "saldo_inicial": "1000",
            "fecha_saldo": (hoy - timedelta(days=731)).isoformat(),
        },
    )
    assert res_vieja.status_code == 422
    assert "La fecha del saldo no puede ser de hace más de 2 años." in res_vieja.text
    data_vieja = res_vieja.json()
    msg_vieja = data_vieja.get("detail") or data_vieja.get("error", {}).get("message")
    assert msg_vieja == "La fecha del saldo no puede ser de hace más de 2 años."

    # 3. Fecha válida de hace 730 días -> 201 Created y expone fecha_saldo_efectiva
    res_ok = client.post(
        "/billeteras",
        json={
            "nombre": "Cuenta Valida",
            "moneda": "ARS",
            "saldo_inicial": "1000",
            "fecha_saldo": (hoy - timedelta(days=730)).isoformat(),
        },
    )
    assert res_ok.status_code == 201
    data = res_ok.json()
    assert data["fecha_saldo"] == (hoy - timedelta(days=730)).isoformat()
    assert data["fecha_saldo_efectiva"] == (hoy - timedelta(days=730)).isoformat()

    app.dependency_overrides.clear()


def test_caso_d_whatsapp_textos_aviso_saldo(db_session: Session):
    """
    Caso D: los textos de WhatsApp de la decisión 7 (individual y lote con n=3) comparados exactos;
    y no se agrega nada si el método es crédito ni si la fecha es igual o posterior a la fecha efectiva.
    """
    u, c = _crear_usuario_y_categoria(db_session)

    # Billetera con fecha_saldo fija
    fecha_saldo_fija = date(2026, 10, 17)
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Banco Nación",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1500000.00"),
        saldo_actual=Decimal("1500000.00"),
        fecha_saldo=fecha_saldo_fija,
        fecha_creacion=datetime(2026, 10, 17, 23, 46, tzinfo=timezone.utc),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)

    assert fecha_creacion_billetera(b) == fecha_saldo_fija

    # 1. Comparación exacta de textos generados
    texto_ind_esperado = (
        "Ojo: es anterior a tu saldo de «Banco Nación» (17/10) y no lo modifica."
    )
    assert texto_aviso_saldo_individual(b) == texto_ind_esperado

    texto_lote_3_esperado = (
        "Ojo: 3 son anteriores a tu saldo de «Banco Nación» (17/10) y no lo modifican."
    )
    assert texto_aviso_saldo_lote(b, 3) == texto_lote_3_esperado

    # 2. Validación de movimiento_anterior_al_saldo y helpers de WhatsApp
    fecha_anterior = date(2026, 10, 16)
    fecha_igual = date(2026, 10, 17)
    fecha_posterior = date(2026, 10, 18)

    # Fecha anterior no crédito -> True
    assert movimiento_anterior_al_saldo(b, fecha_anterior, MetodoPago.DEBITO) is True
    assert movimiento_anterior_al_saldo(b, fecha_anterior, "debito") is True
    assert movimiento_anterior_al_saldo(b, fecha_anterior, MetodoPago.EFECTIVO) is True
    assert movimiento_anterior_al_saldo(b, fecha_anterior, MetodoPago.TRANSFERENCIA) is True

    # Método crédito -> False (no se agrega nada)
    assert movimiento_anterior_al_saldo(b, fecha_anterior, MetodoPago.CREDITO) is False
    assert movimiento_anterior_al_saldo(b, fecha_anterior, "credito") is False

    # Fecha igual -> False (no se agrega nada)
    assert movimiento_anterior_al_saldo(b, fecha_igual, MetodoPago.DEBITO) is False

    # Fecha posterior -> False (no se agrega nada)
    assert movimiento_anterior_al_saldo(b, fecha_posterior, MetodoPago.DEBITO) is False

    # 3. Helpers WhatsApp con objetos Transaccion
    tx_ant = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        categoria_id=c.id,
        billetera_id=b.id,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=fecha_anterior,
        metodo_pago=MetodoPago.DEBITO,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx_credito = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        categoria_id=c.id,
        billetera_id=b.id,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=fecha_anterior,
        metodo_pago=MetodoPago.CREDITO,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx_igual = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        categoria_id=c.id,
        billetera_id=b.id,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=fecha_igual,
        metodo_pago=MetodoPago.DEBITO,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )

    # Individual
    assert obtener_aviso_saldo_individual(tx_ant, billetera=b) == texto_ind_esperado
    # Sin billetera en mano busca en db
    db_session.add(tx_ant)
    db_session.commit()
    assert obtener_aviso_saldo_individual(tx_ant, billetera=None, db=db_session) == texto_ind_esperado
    # Con crédito -> None
    assert obtener_aviso_saldo_individual(tx_credito, billetera=b) is None
    # Con fecha igual o posterior -> None
    assert obtener_aviso_saldo_individual(tx_igual, billetera=b) is None

    # Lote de 3 movimientos anteriores
    txs_lote = [
        Transaccion(id=uuid4(), usuario_id=u.id, categoria_id=c.id, billetera_id=b.id, monto=Decimal("100.00"), moneda=Moneda.ARS, fecha=fecha_anterior, metodo_pago=MetodoPago.DEBITO, tipo=TipoTransaccion.EGRESO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA),
        Transaccion(id=uuid4(), usuario_id=u.id, categoria_id=c.id, billetera_id=b.id, monto=Decimal("200.00"), moneda=Moneda.ARS, fecha=fecha_anterior, metodo_pago=MetodoPago.DEBITO, tipo=TipoTransaccion.EGRESO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA),
        Transaccion(id=uuid4(), usuario_id=u.id, categoria_id=c.id, billetera_id=b.id, monto=Decimal("300.00"), moneda=Moneda.ARS, fecha=fecha_anterior, metodo_pago=MetodoPago.DEBITO, tipo=TipoTransaccion.EGRESO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA),
        tx_credito,  # Se ignora
        tx_igual,    # Se ignora
    ]
    aviso_lote_res = obtener_aviso_saldo_lote(txs_lote, billeteras_map={b.id: b})
    assert aviso_lote_res == texto_lote_3_esperado
