"""
Tests de redondeo de cuotas con ROUND_HALF_UP y ajuste en la última cuota (TE.9).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import cuotas_service, transaccion_service


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


def _setup_usuario_y_tarjeta(db: Session):
    u = Usuario(
        id=uuid4(),
        email=f"te9_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Banco Principal",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        es_principal=True,
        es_inversion=False,
        estado="activa",
    )
    db.add(b)

    cat = Categoria(
        id=uuid4(),
        nombre="Tecnología",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(cat)

    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Visa 1234",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=10,
        dia_vencimiento=20,
        limite_credito=Decimal("5000000.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta)
    db.commit()
    return u, b, cat, tarjeta


def test_te9_compra_12_cuotas_con_interes_5_porciento(db: Session):
    u, b, cat, tarjeta = _setup_usuario_y_tarjeta(db)

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("120000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Notebook",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.CREDITO,
        billetera_id=b.id,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=12,
            cuota_inicial=1,
            tiene_interes=True,
            tasa_interes=Decimal("60"),
            monto_total=Decimal("120000.00"),
            proximo_resumen=False,
        ),
    )

    tx_padre = transaccion_service.crear_transaccion(db, u.id, tx_data, commit=False)
    grupo = db.execute(
        select(GrupoCuotas).where(GrupoCuotas.id == tx_padre.grupo_cuotas_id)
    ).scalar_one()
    cuotas = (
        db.execute(
            select(Cuota)
            .where(Cuota.grupo_id == grupo.id)
            .order_by(Cuota.numero_cuota)
        )
        .scalars()
        .all()
    )

    assert grupo.total_financiado == Decimal("162468.59")
    assert grupo.total_financiado.as_tuple().exponent == -2
    assert len(cuotas) == 12

    for c in cuotas[:11]:
        assert c.monto_proyectado == Decimal("13539.05")
        assert c.monto_proyectado.as_tuple().exponent == -2

    assert cuotas[11].monto_proyectado == Decimal("13539.04")
    assert cuotas[11].monto_proyectado.as_tuple().exponent == -2

    suma_cuotas = sum((c.monto_proyectado for c in cuotas), Decimal("0.00"))
    assert suma_cuotas == Decimal("162468.59")
    assert suma_cuotas == grupo.total_financiado


def test_te9_compra_3_cuotas_sin_interes_100000(db: Session):
    u, b, cat, tarjeta = _setup_usuario_y_tarjeta(db)

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Celular",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.CREDITO,
        billetera_id=b.id,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=3,
            cuota_inicial=1,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("100000.00"),
            proximo_resumen=False,
        ),
    )

    tx_padre = transaccion_service.crear_transaccion(db, u.id, tx_data, commit=False)
    grupo = db.execute(
        select(GrupoCuotas).where(GrupoCuotas.id == tx_padre.grupo_cuotas_id)
    ).scalar_one()
    cuotas = (
        db.execute(
            select(Cuota)
            .where(Cuota.grupo_id == grupo.id)
            .order_by(Cuota.numero_cuota)
        )
        .scalars()
        .all()
    )

    assert grupo.total_financiado == Decimal("100000.00")
    assert len(cuotas) == 3
    assert [c.monto_proyectado for c in cuotas] == [
        Decimal("33333.33"),
        Decimal("33333.33"),
        Decimal("33333.34"),
    ]
    assert sum((c.monto_proyectado for c in cuotas), Decimal("0.00")) == Decimal("100000.00")


def test_te9_redondeo_half_up_en_crear_cuotas(db: Session):
    u, b, cat, tarjeta = _setup_usuario_y_tarjeta(db)

    from app.models.transaccion import EstadoVerificacionTransaccion, Transaccion

    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tarjeta_id=tarjeta.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("200.01"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Mitad exacta",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_padre)
    db.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        transaccion_padre_id=tx_padre.id,
        tarjeta_id=tarjeta.id,
        descripcion="Mitad exacta",
        monto_total=Decimal("200.01"),
        total_financiado=Decimal("200.01"),
        cantidad_cuotas=2,
        tiene_interes=False,
        moneda=Moneda.ARS,
    )
    db.add(grupo)
    db.flush()

    # 100.005 con ROUND_HALF_UP da 100.01 (con round() bancario daría 100.00)
    cuotas = cuotas_service.crear_cuotas(
        db=db,
        transaccion_padre=tx_padre,
        grupo=grupo,
        cantidad_cuotas=2,
        primer_vencimiento=date(2026, 10, 20),
        monto_cuota=Decimal("100.005"),
        usuario_id=str(u.id),
    )
    assert cuotas[0].monto_proyectado == Decimal("100.01")
    assert cuotas[1].monto_proyectado == Decimal("100.00")
    assert cuotas[0].monto_proyectado + cuotas[1].monto_proyectado == Decimal("200.01")
