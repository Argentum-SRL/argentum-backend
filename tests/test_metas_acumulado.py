from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, select, types
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.transaccion import Transaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.meta import MetaCreate, MetaUpdate
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.services import meta_service, transaccion_service


@pytest.fixture(autouse=True)
def _sqlite_compat(monkeypatch):
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
    monkeypatch.setattr(
        "app.services.dolar_service.get_cotizaciones_dolar",
        lambda: {
            "cotizaciones": {
                "blue": {"promedio": 1450.0, "venta": 1450.0, "compra": 1450.0}
            }
        },
    )
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
    import app.models as _app_models  # noqa: F401
    assert _app_models is not None

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _crear_usuario_y_billetera(db) -> tuple[Usuario, Billetera]:
    u = Usuario(
        id=uuid4(),
        email=f"meta_{uuid4().hex[:8]}@test.com",
        nombre="Usuario Meta Test",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([u, b])
    db.commit()
    return u, b


def test_te5_meta_ars_aportes_retiro_y_borrados(db_session):
    """TE.5: Meta ARS $100.000: 3 aportes de $10.000 y 1 retiro de $5.000 -> 25.000,00; borrar aporte -> 15.000,00; borrar tx otro aporte -> 5.000,00."""
    u, b = _crear_usuario_y_billetera(db_session)
    hoy = date(2026, 10, 15)

    meta = meta_service.crear_meta(
        db_session,
        u.id,
        MetaCreate(
            nombre="Fondo ARS",
            monto_objetivo=Decimal("100000.00"),
            moneda=Moneda.ARS,
        ),
    )
    assert meta.monto_actual == Decimal("0.00")

    ap1 = meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("10000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    ap2 = meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("10000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("10000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.RETIRO,
            monto=Decimal("5000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )

    db_session.refresh(meta)
    assert meta.monto_actual == Decimal("25000.00")

    # eliminar_movimiento de un aporte -> 15.000,00
    meta_service.eliminar_movimiento(db_session, u.id, meta.id, ap1.id)
    db_session.refresh(meta)
    assert meta.monto_actual == Decimal("15000.00")

    # eliminar_transaccion de la transacción de otro aporte -> 5.000,00
    tx_ap2 = (
        db_session.execute(
            select(Transaccion).where(Transaccion.movimiento_meta_id == ap2.id)
        )
        .scalars()
        .one()
    )
    transaccion_service.eliminar_transaccion(db_session, u.id, tx_ap2.id)
    db_session.refresh(meta)
    assert meta.monto_actual == Decimal("5000.00")


def test_te5_meta_usd_redondeo_por_movimiento(db_session):
    """TE.5: Meta USD: aportes de $150.000 y $100.000 ARS a 1.450 -> 103,45 y 68,97, acumulado 172,42; borrar el primero -> 68,97."""
    u, b = _crear_usuario_y_billetera(db_session)
    hoy = date(2026, 10, 15)

    meta = meta_service.crear_meta(
        db_session,
        u.id,
        MetaCreate(
            nombre="Viaje USD",
            monto_objetivo=Decimal("1000.00"),
            moneda=Moneda.USD,
        ),
    )

    mov1 = meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("150000.00"),
            moneda_movimiento=Moneda.ARS,
            cotizacion_usada=Decimal("1450.00"),
            tipo_dolar_usado="blue",
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    db_session.refresh(meta)
    assert meta.monto_actual == Decimal("103.45")

    meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("100000.00"),
            moneda_movimiento=Moneda.ARS,
            cotizacion_usada=Decimal("1450.00"),
            tipo_dolar_usado="blue",
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    db_session.refresh(meta)
    assert meta.monto_actual == Decimal("172.42")

    meta_service.eliminar_movimiento(db_session, u.id, meta.id, mov1.id)
    db_session.refresh(meta)
    assert meta.monto_actual == Decimal("68.97")


def test_te5_estados_completada_activa_y_pausada(db_session):
    """TE.5: Meta $30.000 completada y vuelve a activa al borrar aporte; meta PAUSADA sigue PAUSADA al borrar un aporte."""
    u, b = _crear_usuario_y_billetera(db_session)
    hoy = date(2026, 10, 15)

    meta = meta_service.crear_meta(
        db_session,
        u.id,
        MetaCreate(
            nombre="Meta 30k",
            monto_objetivo=Decimal("30000.00"),
            moneda=Moneda.ARS,
        ),
    )
    m1 = meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("15000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("15000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    db_session.refresh(meta)
    assert meta.estado == EstadoMeta.COMPLETADA

    meta_service.eliminar_movimiento(db_session, u.id, meta.id, m1.id)
    db_session.refresh(meta)
    assert meta.estado == EstadoMeta.ACTIVA

    # Meta PAUSADA con dos aportes: borrar uno -> sigue PAUSADA
    meta_pausada = meta_service.crear_meta(
        db_session,
        u.id,
        MetaCreate(
            nombre="Meta Pausada",
            monto_objetivo=Decimal("50000.00"),
            moneda=Moneda.ARS,
        ),
    )
    mp1 = meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta_pausada.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("10000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta_pausada.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("10000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    meta_service.actualizar_meta(
        db_session,
        u.id,
        meta_pausada.id,
        MetaUpdate(estado=EstadoMeta.PAUSADA),
    )
    db_session.refresh(meta_pausada)
    assert meta_pausada.estado == EstadoMeta.PAUSADA

    meta_service.eliminar_movimiento(db_session, u.id, meta_pausada.id, mp1.id)
    db_session.refresh(meta_pausada)
    assert meta_pausada.estado == EstadoMeta.PAUSADA
    assert meta_pausada.monto_actual == Decimal("10000.00")


def test_te5_bloqueo_acumulado_negativo_y_crear_meta_en_cero(db_session):
    """TE.5: Aporte $10.000, retiro $10.000 y borrar aporte -> 400 con texto exacto y siguen los 2 movimientos; crear_meta con 5.000 -> 400."""
    u, b = _crear_usuario_y_billetera(db_session)
    hoy = date(2026, 10, 15)

    with pytest.raises(HTTPException) as exc_crear:
        meta_service.crear_meta(
            db_session,
            u.id,
            MetaCreate(
                nombre="Meta con saldo inicial",
                monto_objetivo=Decimal("50000.00"),
                moneda=Moneda.ARS,
                monto_actual=Decimal("5000.00"),
            ),
        )
    assert exc_crear.value.status_code == 400
    assert (
        exc_crear.value.detail
        == "Una meta nueva arranca en cero: cargá un aporte para sumarle plata."
    )

    meta = meta_service.crear_meta(
        db_session,
        u.id,
        MetaCreate(
            nombre="Meta Cero",
            monto_objetivo=Decimal("50000.00"),
            moneda=Moneda.ARS,
        ),
    )
    ap = meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("10000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    meta_service.registrar_movimiento(
        db_session,
        u.id,
        meta.id,
        MovimientoMetaCreate(
            tipo=TipoMovimientoMeta.RETIRO,
            monto=Decimal("10000.00"),
            moneda_movimiento=Moneda.ARS,
            billetera_id=b.id,
            fecha=hoy,
        ),
    )
    db_session.refresh(meta)
    assert meta.monto_actual == Decimal("0.00")

    with pytest.raises(HTTPException) as exc_del:
        meta_service.eliminar_movimiento(db_session, u.id, meta.id, ap.id)
    assert exc_del.value.status_code == 400
    assert (
        exc_del.value.detail
        == "No se puede borrar este aporte porque la meta quedaría en negativo. Borrá primero el retiro."
    )

    db_session.expire_all()
    meta_db = db_session.get(Meta, meta.id)
    assert meta_db is not None
    assert meta_db.monto_actual == Decimal("0.00")
    movs_db = (
        db_session.execute(
            select(MovimientoMeta).where(MovimientoMeta.meta_id == meta.id)
        )
        .scalars()
        .all()
    )
    assert len(movs_db) == 2
