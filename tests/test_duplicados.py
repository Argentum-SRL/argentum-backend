from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base


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


from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.transaccion import (
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.duplicados_service import buscar_coincidencias, pares_en_billetera


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
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


def _crear_usuario(db: Session, email_prefix: str = "dup_user") -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"{email_prefix}_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _crear_billetera(db: Session, usuario_id: uuid.UUID, nombre: str = "Billetera A") -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre=nombre,
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _crear_categoria(db: Session) -> Categoria:
    cat = Categoria(
        id=uuid4(),
        nombre="General",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def test_d2_buscar_coincidencias_y_pares(db_session: Session):
    u = _crear_usuario(db_session, "user_dup")
    billetera_a = _crear_billetera(db_session, u.id, "Billetera A")
    billetera_b = _crear_billetera(db_session, u.id, "Billetera B")
    cat = _crear_categoria(db_session)

    # Ya existe un egreso de 5.000 en la billetera A, el 10/09, con descripción "uber"
    tx_base = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=billetera_a.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("5000.00"),
        fecha=date(2026, 9, 10),
        descripcion="uber",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        es_cuota_hija=False,
        es_padre_cuotas=False,
        origen=OrigenTransaccion.MANUAL,
    )
    db_session.add(tx_base)
    db_session.commit()

    # 1. billetera A, 13/09, "uber" -> coincide (3 días)
    res_1 = buscar_coincidencias(
        db=db_session,
        usuario_id=u.id,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 13),
        tipo="egreso",
        billetera_id=billetera_a.id,
        descripcion="uber",
    )
    assert len(res_1) == 1
    assert res_1[0].transaccion.id == tx_base.id
    assert res_1[0].dias == 3
    assert res_1[0].misma_descripcion is True

    # 2. billetera A, 18/09, "uber" -> coincide (8 días, misma descripción)
    res_2 = buscar_coincidencias(
        db=db_session,
        usuario_id=u.id,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 18),
        tipo="egreso",
        billetera_id=billetera_a.id,
        descripcion="uber",
    )
    assert len(res_2) == 1
    assert res_2[0].transaccion.id == tx_base.id
    assert res_2[0].dias == 8
    assert res_2[0].misma_descripcion is True

    # 3. billetera A, 18/09, "coto" -> no coincide (8 días > 3 y distinta descripción)
    res_3 = buscar_coincidencias(
        db=db_session,
        usuario_id=u.id,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 18),
        tipo="egreso",
        billetera_id=billetera_a.id,
        descripcion="coto",
    )
    assert len(res_3) == 0

    # 4. billetera A, 21/09, "uber" -> no coincide (11 días > 10)
    res_4 = buscar_coincidencias(
        db=db_session,
        usuario_id=u.id,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 21),
        tipo="egreso",
        billetera_id=billetera_a.id,
        descripcion="uber",
    )
    assert len(res_4) == 0

    # 5. billetera B, 10/09 -> no coincide (distinta billetera)
    res_5 = buscar_coincidencias(
        db=db_session,
        usuario_id=u.id,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 10),
        tipo="egreso",
        billetera_id=billetera_b.id,
        descripcion="uber",
    )
    assert len(res_5) == 0

    # 6. 5.001 -> no coincide (distinto monto)
    res_6 = buscar_coincidencias(
        db=db_session,
        usuario_id=u.id,
        monto=Decimal("5001.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 10),
        tipo="egreso",
        billetera_id=billetera_a.id,
        descripcion="uber",
    )
    assert len(res_6) == 0

    # 7. Si el existente es cuota hija -> no coincide
    tx_base.es_cuota_hija = True
    db_session.commit()
    res_7 = buscar_coincidencias(
        db=db_session,
        usuario_id=u.id,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 10),
        tipo="egreso",
        billetera_id=billetera_a.id,
        descripcion="uber",
    )
    assert len(res_7) == 0
    tx_base.es_cuota_hija = False
    db_session.commit()

    # 8. Con dos coincidencias, primero la de misma descripción
    # tx_base: 10/09, "uber"
    # tx_otra: 12/09, "farmacia"
    tx_otra = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=billetera_a.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("5000.00"),
        fecha=date(2026, 9, 12),
        descripcion="farmacia",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    db_session.add(tx_otra)
    db_session.commit()

    # Candidato el 11/09 con descripción "uber" (distancia a tx_otra es 1 día, a tx_base es 1 día)
    # tx_base tiene misma descripción ("uber"), tx_otra no. tx_base debe ir primera.
    res_8 = buscar_coincidencias(
        db=db_session,
        usuario_id=u.id,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 11),
        tipo="egreso",
        billetera_id=billetera_a.id,
        descripcion="uber",
    )
    assert len(res_8) == 2
    assert res_8[0].transaccion.id == tx_base.id
    assert res_8[0].misma_descripcion is True
    assert res_8[1].transaccion.id == tx_otra.id
    assert res_8[1].misma_descripcion is False

    # 9. pares_en_billetera: dos egresos del mismo día y monto -> 1 grupo con cantidad 2
    tx_dup1 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=billetera_a.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("2500.00"),
        fecha=date(2026, 9, 15),
        descripcion="cafetería 1",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    tx_dup2 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=billetera_a.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("2500.00"),
        fecha=date(2026, 9, 15),
        descripcion="cafetería 2",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    db_session.add_all([tx_dup1, tx_dup2])
    db_session.commit()

    pares = pares_en_billetera(
        db=db_session,
        usuario_id=u.id,
        billetera_id=billetera_a.id,
        desde=date(2026, 9, 1),
        hasta=date(2026, 9, 30),
    )
    assert len(pares) == 1
    assert pares[0]["fecha"] == date(2026, 9, 15)
    assert pares[0]["monto"] == Decimal("2500.00")
    assert pares[0]["cantidad"] == 2
    assert "cafetería 1" in pares[0]["descripciones"]
    assert "cafetería 2" in pares[0]["descripciones"]
