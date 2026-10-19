"""
tests/test_punto_partida.py — Pruebas para la fase saldo punto de partida.

Valores fijos especificados en el prompt:
- Sin saldo (POST saldo_inicial 0): tiene_punto_partida False, fecha_saldo_efectiva null.
  Egreso 10.000 (hoy-5) -> actual -10.000.
  Ingreso 50.000 (hoy-20) -> actual 40.000.
  Egreso 5.000 (hoy) -> actual 35.000.
  saldo_inicial 0; saldo_actual == calcular_saldo_teorico; movimiento_anterior_al_saldo False.
- Regresión (270.000 sin fecha): egreso 22.000 (hoy-10) -> actual 270.000, inicial 292.000.
- Con saldo y fecha (270.000, hoy-20): egreso 22.000 (hoy-10) -> actual 248.000.
  Egreso 7.000 (hoy-25) -> actual 248.000, inicial 277.000.
- Previsualizar y aplicar sobre ese estado (hoy-30): {248.000, 241.000, 1}.
  Aplicar deja actual 241.000, inicial 270.000, fecha_saldo hoy-30, teórico 241.000.
  Crédito y pendiente en el tramo no alteran ni cantidad ni saldo nuevo.
- Transferencia: A (100.000, hoy-10), B sin saldo. Transferencia 5.000 (hoy-15) -> A 100.000 (ini 105.000), B 5.000.
  Mover A a hoy-20 -> prev {100.000, 95.000, 1}, aplicar A 95.000 (ini 100.000).
- Validaciones 422 y 404 con textos exactos.
- Historial: sin punto de partida no hay fila saldo_inicial; con punto de partida sí.
- Textos de WhatsApp de decisión 8 (< 90 caracteres).
- Regla de historia (decisión 11).
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
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.aviso_saldo_wpp import (
    formatear_fecha_ddmm,
    obtener_aviso_saldo,
    recortar_nombre_billetera,
    texto_aviso_saldo_individual,
    texto_aviso_saldo_lote,
)
from app.schemas.transaccion import TransaccionCreate
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import transaccion_service, transferencia_service
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.dashboard_service import calcular_balance_ciclo
from app.services.fecha_saldo_service import cambiar_fecha_saldo, previsualizar_cambio_fecha
from app.services.historial_service import listar_movimientos
from app.services.ingreso_habitual_service import obtener_ingreso_habitual
from app.services.saldo_billetera_service import (
    fecha_creacion_billetera,
    fecha_punto_partida,
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


def test_billetera_sin_saldo(db_session: Session):
    """
    Sin saldo (POST con saldo_inicial 0):
    - respuesta con tiene_punto_partida False y fecha_saldo_efectiva null.
    - Egreso 10.000 con fecha hoy-5 da saldo_actual -10.000.
    - Ingreso 50.000 hoy-20 da 40.000.
    - Egreso 5.000 hoy da 35.000.
    - saldo_inicial 0.
    - saldo_actual igual a calcular_saldo_teorico.
    - movimiento_anterior_al_saldo False.
    """
    hoy = hoy_argentina()
    u, c = _crear_usuario_y_categoria(db_session)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u
    client = TestClient(app)

    res = client.post(
        "/billeteras",
        json={
            "nombre": "Caja Ahorro Sin Saldo",
            "moneda": "ARS",
            "saldo_inicial": 0,
        },
    )
    assert res.status_code in (200, 201)
    data = res.json()
    assert data["tiene_punto_partida"] is False
    assert data["fecha_saldo_efectiva"] is None

    b_id = uuid.UUID(data["id"])
    b = db_session.get(Billetera, b_id)
    assert b is not None
    assert b.tiene_punto_partida is False
    assert b.fecha_saldo_efectiva is None

    # Egreso 10.000 hoy-5
    transaccion_service.crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            billetera_id=b.id,
            categoria_id=c.id,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            metodo_pago=MetodoPago.DEBITO,
            fecha=hoy - timedelta(days=5),
            descripcion="Egreso hoy-5",
        ),
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("-10000.00")

    # Ingreso 50.000 hoy-20
    transaccion_service.crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            billetera_id=b.id,
            categoria_id=c.id,
            monto=Decimal("50000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.INGRESO,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            fecha=hoy - timedelta(days=20),
            descripcion="Ingreso hoy-20",
        ),
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("40000.00")

    # Egreso 5.000 hoy
    transaccion_service.crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            billetera_id=b.id,
            categoria_id=c.id,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            metodo_pago=MetodoPago.DEBITO,
            fecha=hoy,
            descripcion="Egreso hoy",
        ),
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("35000.00")
    assert b.saldo_inicial == Decimal("0.00")

    # saldo_actual igual a calcular_saldo_teorico
    teorico = calcular_saldo_teorico(db_session, b.id, hasta=hoy)
    assert b.saldo_actual == teorico

    # movimiento_anterior_al_saldo False
    assert movimiento_anterior_al_saldo(b, hoy - timedelta(days=5), MetodoPago.DEBITO) is False
    assert movimiento_anterior_al_saldo(b, hoy - timedelta(days=50), MetodoPago.DEBITO) is False


def test_regresion_saldo_sin_fecha(db_session: Session):
    """
    Regresión (saldo 270.000 sin fecha):
    - egreso 22.000 hoy-10 deja actual 270.000 e inicial 292.000.
    """
    hoy = hoy_argentina()
    u, c = _crear_usuario_y_categoria(db_session)

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Regresion",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("270000.00"),
        saldo_actual=Decimal("270000.00"),
        fecha_saldo=None,
        tiene_punto_partida=True,
    )
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)

    transaccion_service.crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            billetera_id=b.id,
            categoria_id=c.id,
            monto=Decimal("22000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            metodo_pago=MetodoPago.DEBITO,
            fecha=hoy - timedelta(days=10),
            descripcion="Egreso anterior a hoy",
        ),
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("270000.00")
    assert b.saldo_inicial == Decimal("292000.00")


def test_con_saldo_y_fecha_y_previsualizar_aplicar(db_session: Session):
    """
    - Con saldo y fecha (270.000, fecha_saldo hoy-20):
      egreso 22.000 hoy-10 da actual 248.000;
      egreso 7.000 hoy-25 deja actual 248.000 e inicial 277.000.
    - Sobre ese estado, previsualizar con fecha hoy-30 devuelve
      saldo_actual 248.000, saldo_nuevo 241.000, cantidad_movimientos 1;
      aplicar deja actual 241.000, inicial 270.000, fecha_saldo hoy-30, teórico 241.000.
      Una compra con crédito y un movimiento pendiente con fecha dentro del tramo
      no cambian ni la cantidad ni el saldo nuevo.
    """
    hoy = hoy_argentina()
    u, c = _crear_usuario_y_categoria(db_session)

    fecha_saldo_ini = hoy - timedelta(days=20)
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Test Previsualizar",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("270000.00"),
        saldo_actual=Decimal("270000.00"),
        fecha_saldo=fecha_saldo_ini,
        tiene_punto_partida=True,
    )
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)

    # Egreso 22.000 hoy-10 (posterior al saldo -> mueve actual)
    transaccion_service.crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            billetera_id=b.id,
            categoria_id=c.id,
            monto=Decimal("22000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            metodo_pago=MetodoPago.DEBITO,
            fecha=hoy - timedelta(days=10),
            descripcion="Egreso hoy-10",
        ),
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("248000.00")
    assert b.saldo_inicial == Decimal("270000.00")

    # Egreso 7.000 hoy-25 (anterior al saldo -> no mueve actual, sube inicial)
    transaccion_service.crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            billetera_id=b.id,
            categoria_id=c.id,
            monto=Decimal("7000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            metodo_pago=MetodoPago.DEBITO,
            fecha=hoy - timedelta(days=25),
            descripcion="Egreso hoy-25",
        ),
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("248000.00")
    assert b.saldo_inicial == Decimal("277000.00")

    # Movimientos en el tramo [hoy-30, hoy-20) que NO afectan saldo
    # 1. Compra con crédito
    tx_credito = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        categoria_id=c.id,
        billetera_id=b.id,
        monto=Decimal("3000.00"),
        moneda=Moneda.ARS,
        fecha=hoy - timedelta(days=26),
        metodo_pago=MetodoPago.CREDITO,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx_credito)

    # 2. Movimiento pendiente
    tx_pendiente = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        categoria_id=c.id,
        billetera_id=b.id,
        monto=Decimal("4000.00"),
        moneda=Moneda.ARS,
        fecha=hoy - timedelta(days=27),
        metodo_pago=MetodoPago.DEBITO,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
    )
    db_session.add(tx_pendiente)
    db_session.commit()

    # Previsualizar con fecha hoy-30
    prev = previsualizar_cambio_fecha(db_session, u.id, b.id, hoy - timedelta(days=30))
    assert prev["saldo_actual"] == Decimal("248000.00")
    assert prev["saldo_nuevo"] == Decimal("241000.00")
    assert prev["cantidad_movimientos"] == 1

    # Aplicar cambio de fecha
    b_actualizada = cambiar_fecha_saldo(db_session, u.id, b.id, hoy - timedelta(days=30))
    assert b_actualizada.saldo_actual == Decimal("241000.00")
    assert b_actualizada.saldo_inicial == Decimal("270000.00")
    assert b_actualizada.fecha_saldo == (hoy - timedelta(days=30))

    teorico = calcular_saldo_teorico(db_session, b.id, hasta=hoy)
    assert teorico == Decimal("241000.00")


def test_transferencia_y_cambio_fecha(db_session: Session):
    """
    Transferencia:
    - A con 100.000 y fecha_saldo hoy-10; B sin saldo;
    - transferencia de 5.000 de A a B con fecha hoy-15:
      A queda 100.000 (inicial 105.000) y B queda en 5.000.
    - Mover la fecha de A a hoy-20:
      previsualizar da {100.000, 95.000, 1};
      aplicar deja A en 95.000 (inicial 100.000).
    """
    hoy = hoy_argentina()
    u, _ = _crear_usuario_y_categoria(db_session)

    b_a = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Billetera A",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        fecha_saldo=hoy - timedelta(days=10),
        tiene_punto_partida=True,
    )
    b_b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Billetera B",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        fecha_saldo=None,
        tiene_punto_partida=False,
    )
    db_session.add_all([b_a, b_b])
    db_session.commit()
    db_session.refresh(b_a)
    db_session.refresh(b_b)

    transferencia_service.crear_transferencia(
        db_session,
        u.id,
        TransferenciaInternaCreate(
            billetera_origen_id=b_a.id,
            billetera_destino_id=b_b.id,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            fecha=hoy - timedelta(days=15),
            notas="Transferencia A a B",
        ),
    )
    db_session.refresh(b_a)
    db_session.refresh(b_b)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("105000.00")
    assert b_b.saldo_actual == Decimal("5000.00")

    # Previsualizar mover la fecha de A a hoy-20
    prev_a = previsualizar_cambio_fecha(db_session, u.id, b_a.id, hoy - timedelta(days=20))
    assert prev_a["saldo_actual"] == Decimal("100000.00")
    assert prev_a["saldo_nuevo"] == Decimal("95000.00")
    assert prev_a["cantidad_movimientos"] == 1

    # Aplicar
    b_a_act = cambiar_fecha_saldo(db_session, u.id, b_a.id, hoy - timedelta(days=20))
    assert b_a_act.saldo_actual == Decimal("95000.00")
    assert b_a_act.saldo_inicial == Decimal("100000.00")


def test_validaciones_422_y_404(db_session: Session):
    """
    Validaciones (422, textos exactos y en este orden):
    - billetera sin punto de partida "Esta billetera no tiene saldo inicial."
    - fecha futura "La fecha del saldo no puede ser futura."
    - de hace más de 730 días "La fecha del saldo no puede ser de hace más de 2 años."
    - fecha igual o posterior a la efectiva actual "Solo se puede mover la fecha hacia atrás."
    - 404 con billetera de otro usuario o inexistente.
    """
    hoy = hoy_argentina()
    u, _ = _crear_usuario_y_categoria(db_session)
    u_otro, _ = _crear_usuario_y_categoria(db_session)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u
    client = TestClient(app)

    # Billetera con punto de partida
    b_con_pp = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Con PP",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        fecha_saldo=hoy - timedelta(days=10),
        tiene_punto_partida=True,
    )
    # Billetera sin punto de partida
    b_sin_pp = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Sin PP",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        fecha_saldo=None,
        tiene_punto_partida=False,
    )
    db_session.add_all([b_con_pp, b_sin_pp])
    db_session.commit()

    # 1. 404 billetera de otro usuario o inexistente
    res_404 = client.post(
        f"/billeteras/{uuid4()}/fecha-saldo/previsualizar",
        json={"fecha_saldo": (hoy - timedelta(days=15)).isoformat()},
    )
    assert res_404.status_code == 404

    # 2. 422 sin punto de partida: "Esta billetera no tiene saldo inicial."
    res_sin_pp = client.post(
        f"/billeteras/{b_sin_pp.id}/fecha-saldo/previsualizar",
        json={"fecha_saldo": (hoy - timedelta(days=15)).isoformat()},
    )
    assert res_sin_pp.status_code == 422
    assert "Esta billetera no tiene saldo inicial." in res_sin_pp.text

    # 3. 422 fecha futura: "La fecha del saldo no puede ser futura."
    res_futura = client.post(
        f"/billeteras/{b_con_pp.id}/fecha-saldo/previsualizar",
        json={"fecha_saldo": (hoy + timedelta(days=1)).isoformat()},
    )
    assert res_futura.status_code == 422
    assert "La fecha del saldo no puede ser futura." in res_futura.text

    # 4. 422 hace más de 730 días (731 días): "La fecha del saldo no puede ser de hace más de 2 años."
    res_vieja = client.post(
        f"/billeteras/{b_con_pp.id}/fecha-saldo/previsualizar",
        json={"fecha_saldo": (hoy - timedelta(days=731)).isoformat()},
    )
    assert res_vieja.status_code == 422
    assert "La fecha del saldo no puede ser de hace más de 2 años." in res_vieja.text

    # 5. 422 fecha igual o posterior: "Solo se puede mover la fecha hacia atrás."
    # Fecha igual (hoy-10)
    res_igual = client.post(
        f"/billeteras/{b_con_pp.id}/fecha-saldo/previsualizar",
        json={"fecha_saldo": (hoy - timedelta(days=10)).isoformat()},
    )
    assert res_igual.status_code == 422
    assert "Solo se puede mover la fecha hacia atrás." in res_igual.text

    # Fecha posterior (hoy-5)
    res_posterior = client.patch(
        f"/billeteras/{b_con_pp.id}/fecha-saldo",
        json={"fecha_saldo": (hoy - timedelta(days=5)).isoformat()},
    )
    assert res_posterior.status_code == 422
    assert "Solo se puede mover la fecha hacia atrás." in res_posterior.text


def test_historial_fila_saldo_inicial(db_session: Session):
    """
    historial: sin punto de partida no hay fila saldo_inicial; con punto de partida sigue como antes.
    """
    hoy = hoy_argentina()
    u, _ = _crear_usuario_y_categoria(db_session)

    b_con = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Con Saldo Inicial",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        fecha_saldo=hoy - timedelta(days=10),
        tiene_punto_partida=True,
    )
    b_sin = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Sin Saldo Inicial",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        fecha_saldo=None,
        tiene_punto_partida=False,
    )
    db_session.add_all([b_con, b_sin])
    db_session.commit()

    # Historial de b_sin: no debe tener fila con clase "saldo_inicial"
    res_sin = listar_movimientos(
        db_session, u.id, modo="billetera", billetera_id=b_sin.id
    )
    clases_sin = [item.clase for item in res_sin.items]
    assert "saldo_inicial" not in clases_sin

    # Historial de b_con: sí debe tener fila con clase "saldo_inicial"
    res_con = listar_movimientos(
        db_session, u.id, modo="billetera", billetera_id=b_con.id
    )
    clases_con = [item.clase for item in res_con.items]
    assert "saldo_inicial" in clases_con


def test_textos_whatsapp_decision_8(db_session: Session):
    """
    Textos de WhatsApp exactos de la decisión 8:
    - 1 movimiento: "Ojo: es anterior a tu saldo de «Banco Nación» (18/10) y no lo modifica."
    - varios de una misma billetera: "Ojo: 3 son anteriores a tu saldo de «Banco Nación» (18/10) y no lo modifican."
    - varios de distintas billeteras: "Ojo: 3 son anteriores a tu saldo y no lo modifican."
    - nombre largo recortado a 25 caracteres (primeros 24 más '…').
    - sin aviso con crédito, con billetera sin punto de partida o con fecha igual o posterior.
    - cada línea de menos de 90 caracteres.
    """
    fecha_ref = date(2026, 10, 18)
    fecha_ant = date(2026, 10, 15)
    fecha_igual = date(2026, 10, 18)
    fecha_post = date(2026, 10, 20)

    u, _ = _crear_usuario_y_categoria(db_session)

    b1 = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Banco Nación",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000.00"),
        saldo_actual=Decimal("1000.00"),
        fecha_saldo=fecha_ref,
        tiene_punto_partida=True,
    )
    b2 = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Santander Río",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000.00"),
        saldo_actual=Decimal("1000.00"),
        fecha_saldo=fecha_ref,
        tiene_punto_partida=True,
    )
    b_sin_pp = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Efectivo",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        fecha_saldo=None,
        tiene_punto_partida=False,
    )
    b_larga = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Banco de la Provincia de Buenos Aires",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000.00"),
        saldo_actual=Decimal("1000.00"),
        fecha_saldo=fecha_ref,
        tiene_punto_partida=True,
    )
    db_session.add_all([b1, b2, b_sin_pp, b_larga])
    db_session.commit()

    # 1 movimiento
    m1 = {"billetera": b1, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO}
    txt1 = obtener_aviso_saldo([m1])
    esperado1 = "Ojo: es anterior a tu saldo de «Banco Nación» (18/10) y no lo modifica."
    assert txt1 == esperado1
    assert len(txt1) <= 90

    # Varios de una misma billetera
    m_misma = [
        {"billetera": b1, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO},
        {"billetera": b1, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO},
        {"billetera": b1, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO},
    ]
    txt_misma = obtener_aviso_saldo(m_misma)
    esperado_misma = "Ojo: 3 son anteriores a tu saldo de «Banco Nación» (18/10) y no lo modifican."
    assert txt_misma == esperado_misma
    assert len(txt_misma) <= 90

    # Varios de distintas billeteras
    m_distintas = [
        {"billetera": b1, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO},
        {"billetera": b2, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO},
        {"billetera": b2, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO},
    ]
    txt_distintas = obtener_aviso_saldo(m_distintas)
    esperado_distintas = "Ojo: 3 son anteriores a tu saldo y no lo modifican."
    assert txt_distintas == esperado_distintas
    assert len(txt_distintas) <= 90

    # Nombre largo recortado a 25 caracteres
    assert len(recortar_nombre_billetera(b_larga.nombre)) == 25
    txt_larga = obtener_aviso_saldo([{"billetera": b_larga, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO}])
    assert len(txt_larga) <= 90
    assert "…" in txt_larga

    # Sin aviso con crédito
    assert obtener_aviso_saldo([{"billetera": b1, "fecha": fecha_ant, "metodo_pago": MetodoPago.CREDITO}]) is None
    assert obtener_aviso_saldo([{"billetera": b1, "fecha": fecha_ant, "metodo_pago": "credito"}]) is None

    # Sin aviso con billetera sin punto de partida
    assert obtener_aviso_saldo([{"billetera": b_sin_pp, "fecha": fecha_ant, "metodo_pago": MetodoPago.DEBITO}]) is None

    # Sin aviso con fecha igual o posterior
    assert obtener_aviso_saldo([{"billetera": b1, "fecha": fecha_igual, "metodo_pago": MetodoPago.DEBITO}]) is None
    assert obtener_aviso_saldo([{"billetera": b1, "fecha": fecha_post, "metodo_pago": MetodoPago.DEBITO}]) is None


def test_regla_de_historia_decision_11(db_session: Session):
    """
    Regla escrita de historia: el saldo inicial nunca es ingreso, sueldo ni ahorro
    ni entra en ingreso habitual, gasto típico, tasa de ahorro, patrones, presupuestos ni balance del ciclo.
    Un cálculo de ingreso habitual o balance del ciclo con una billetera con saldo inicial grande
    y sin movimientos da lo mismo que sin ella.
    """
    hoy = hoy_argentina()
    u, _ = _crear_usuario_y_categoria(db_session)

    # Balance e ingreso inicial sin billeteras
    bal_sin = calcular_balance_ciclo(db_session, u)
    ing_sin = obtener_ingreso_habitual(db_session, u, hoy=hoy, moneda=Moneda.ARS)

    # Agregar billetera con saldo inicial enorme y sin movimientos
    b_grande = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cofre Fuerte",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000000.00"),
        saldo_actual=Decimal("50000000.00"),
        fecha_saldo=hoy - timedelta(days=10),
        tiene_punto_partida=True,
    )
    db_session.add(b_grande)
    db_session.commit()

    bal_con = calcular_balance_ciclo(db_session, u)
    ing_con = obtener_ingreso_habitual(db_session, u, hoy=hoy, moneda=Moneda.ARS)

    # Ambos cálculos deben ser idénticos
    assert bal_sin["ars"]["ingresos"] == bal_con["ars"]["ingresos"]
    assert bal_sin["ars"]["egresos"] == bal_con["ars"]["egresos"]
    assert bal_sin["ars"]["balance"] == bal_con["ars"]["balance"]
    assert ing_sin.monto == ing_con.monto
