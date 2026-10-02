from datetime import date
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import uuid
from sqlalchemy import types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.suscripcion import Suscripcion
from app.models.transaccion import Transaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.suscripcion import SuscripcionCreate
from app.services import suscripcion_service
from app.services.cobro_suscripcion_service import procesar_cobros_suscripciones
from app.utils.fecha import hoy_argentina


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
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


def test_cobros_suscripciones_aislados_error_db_no_afecta_demas(db_session):
    """
    Verifica que procesar_cobros_suscripciones aísla cada cobro con savepoints (db.begin_nested()).
    Si la primera suscripción provoca un error real de base de datos durante flush (NOT NULL violado),
    solo esa se deshace y no deja nada grabado, mientras que la segunda se cobra y queda grabada.
    """
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Principal",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    cat_egr = Categoria(id=uuid4(), nombre="Servicios", tipo=TipoCategoria.EGRESO)
    db_session.add_all([u, b, cat_egr])
    db_session.commit()

    hoy = hoy_argentina()

    sub1_data = SuscripcionCreate(
        nombre="Sub1_ConFallo",
        monto=Decimal("1000.00"),
        moneda="ARS",
        frecuencia="mensual",
        proximo_cobro=hoy,
        billetera_id=b.id,
        categoria_id=cat_egr.id,
    )
    sub1 = suscripcion_service.crear_suscripcion(db_session, u.id, sub1_data)

    sub2_data = SuscripcionCreate(
        nombre="Sub2_Exitosa",
        monto=Decimal("2000.00"),
        moneda="ARS",
        frecuencia="mensual",
        proximo_cobro=hoy,
        billetera_id=b.id,
        categoria_id=cat_egr.id,
    )
    sub2 = suscripcion_service.crear_suscripcion(db_session, u.id, sub2_data)

    db_session.commit()

    # Interceptar antes de flush para forzar un error real de base (NOT NULL violado en monto)
    # exclusivamente en la transacción que genera sub1
    @event.listens_for(db_session, "before_flush")
    def trigger_not_null_error(session, flush_context, instances):
        for obj in session.new:
            if isinstance(obj, Transaccion) and obj.suscripcion_id == sub1.id:
                obj.monto = None  # Viola NOT NULL en columna monto de transacciones

    # Ejecutar el procesamiento de cobros
    resultado = procesar_cobros_suscripciones(db_session, commit=True)

    # Validar estadísticas del resultado
    assert resultado["total_encontradas"] == 2
    assert resultado["cobradas"] == 1
    assert resultado["errores"] == 1

    # Verificar que la primera (sub1) NO dejó transacciones grabadas
    txs_sub1 = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub1.id)
    ).scalars().all()
    assert len(txs_sub1) == 0

    # Verificar que sub1 mantuvo su proximo_cobro sin avanzar
    sub1_db = db_session.get(Suscripcion, sub1.id)
    assert sub1_db.proximo_cobro == hoy

    # Verificar que la segunda (sub2) SÍ quedó cobrada y grabada
    txs_sub2 = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub2.id)
    ).scalars().all()
    assert len(txs_sub2) == 1
    assert txs_sub2[0].monto == Decimal("2000.00")

    # Verificar que sub2 avanzó su proximo_cobro
    sub2_db = db_session.get(Suscripcion, sub2.id)
    assert sub2_db.proximo_cobro > hoy

    # Verificar que el saldo de la billetera solo impactó el cobro exitoso
    b_db = db_session.get(Billetera, b.id)
    assert b_db.saldo_actual == Decimal("498000.00")
