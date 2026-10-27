from datetime import date
from decimal import Decimal
import sqlite3
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.notificacion_scheduler_service import _gastos_hormiga


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
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)
    yield
    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models

    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _crear_escenario_hormiga(db_session, cantidad_compras: int):
    usuario = Usuario(
        id=uuid4(),
        email=f"hormiga_{uuid4().hex[:6]}@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db_session.add(usuario)
    db_session.flush()

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Principal",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        es_principal=True,
        es_efectivo=False,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(billetera)

    cat = Categoria(
        id=uuid4(),
        nombre="Gastronomía",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db_session.add(cat)
    db_session.flush()

    sub = Subcategoria(
        id=uuid4(),
        categoria_id=cat.id,
        nombre="Cafetería",
        estado=EstadoSubcategoria.ACTIVA,
        orden=1,
    )
    db_session.add(sub)
    db_session.flush()

    compras = []
    for i in range(cantidad_compras):
        tx = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
            billetera_id=billetera.id,
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("2000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 5 + i),
            descripcion=f"Café {i + 1}",
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            saldo_aplicado=True,
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db_session.add(tx)
        compras.append(tx)
    db_session.flush()

    # Reintegro recibido de $500 de la primera compra (espejo en transacciones con monto negativo)
    espejo = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat.id,
        subcategoria_id=sub.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("-500.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 6),
        descripcion="Reintegro: Café 1",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        saldo_aplicado=True,
        es_cuota_hija=False,
        es_padre_cuotas=False,
        reintegro_de_id=compras[0].id,
    )
    db_session.add(espejo)
    db_session.commit()
    return usuario


def test_gastos_hormiga_5_compras_y_reintegro(db_session):
    """
    TE.14: 5 compras con débito de $2.000 en Gastronomía > Cafetería y un reintegro recibido
    de $500 de una de ellas, en el ciclo: _gastos_hormiga devuelve
    [{"categoria": "Gastronomía", "cantidad": 5, "total": 9500.0}].
    """
    usuario = _crear_escenario_hormiga(db_session, cantidad_compras=5)
    res = _gastos_hormiga(
        db=db_session,
        usuario_id=usuario.id,
        desde=date(2026, 10, 1),
        hasta=date(2026, 10, 31),
        moneda=Moneda.ARS,
        hoy=date(2026, 10, 31),
    )
    assert res == [{"categoria": "Gastronomía", "cantidad": 5, "total": 9500.0}]


def test_gastos_hormiga_4_compras_y_reintegro_no_llega_al_umbral(db_session):
    """
    TE.14: con 4 compras y el mismo reintegro de $500: _gastos_hormiga devuelve [].
    """
    usuario = _crear_escenario_hormiga(db_session, cantidad_compras=4)
    res = _gastos_hormiga(
        db=db_session,
        usuario_id=usuario.id,
        desde=date(2026, 10, 1),
        hasta=date(2026, 10, 31),
        moneda=Moneda.ARS,
        hoy=date(2026, 10, 31),
    )
    assert res == []
