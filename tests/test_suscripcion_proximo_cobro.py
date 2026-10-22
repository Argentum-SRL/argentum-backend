"""
Tests de suscripciones: normalización de próximo cobro, cobro en fecha real y corrida de cobros pendientes.
Cubre los casos 1 a 10 de la Tarea D de fase_e6_suscripciones.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import types
import sqlite3
import uuid

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.notificacion import Notificacion
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import Transaccion, OrigenTransaccion, EstadoVerificacionTransaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.suscripcion import SuscripcionCreate, SuscripcionUpdate
from app.services import suscripcion_service
from app.services.cobro_suscripcion_service import procesar_cobros_suscripciones
from app.services.tarjeta_service import calcular_primer_vencimiento

FECHA_HOY = date(2026, 10, 13)


@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


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


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def parchear_hoy(monkeypatch):
    """Parchea hoy_argentina en suscripcion_service y cobro_suscripcion_service a 2026-10-13."""
    monkeypatch.setattr(
        "app.services.suscripcion_service.hoy_argentina",
        lambda: FECHA_HOY,
    )
    monkeypatch.setattr(
        "app.services.cobro_suscripcion_service.hoy_argentina",
        lambda: FECHA_HOY,
    )


@pytest.fixture
def datos_base(db_session):
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
        nombre="Billetera Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
    )
    cat = Categoria(id=uuid4(), nombre="Servicios", tipo=TipoCategoria.EGRESO)
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="•••• 1234",
        red="visa",
        dia_cierre=24,
        dia_vencimiento=7,
        limite_credito=Decimal("500000.00"),
        estado="activa",
    )
    db_session.add_all([u, b, cat, tarjeta])
    db_session.commit()
    return {"usuario": u, "billetera": b, "categoria": cat, "tarjeta": tarjeta}


def test_caso_1_crear_proximo_cobro_futuro(db_session, datos_base):
    """1. Crear con próximo cobro 2026-11-13 -> 0 transacciones, proximo_cobro 2026-11-13, saldo $100.000."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    data = SuscripcionCreate(
        nombre="Spotify",
        monto=Decimal("39000.00"),
        moneda="ARS",
        frecuencia="mensual",
        proximo_cobro=date(2026, 11, 13),
        billetera_id=b.id,
        categoria_id=cat.id,
    )
    sub = suscripcion_service.crear_suscripcion(db_session, u.id, data)

    txs = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub.id)
    ).scalars().all()

    assert len(txs) == 0
    assert sub.proximo_cobro == date(2026, 11, 13)
    b_db = db_session.get(Billetera, b.id)
    assert b_db.saldo_actual == Decimal("100000.00")


def test_caso_2_crear_hoy_con_billetera(db_session, datos_base):
    """2. Crear con 2026-10-13 y billetera -> 1 transacción con fecha 2026-10-13, $39.000 y suscripcion_id; saldo $61.000; proximo_cobro 2026-11-13."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    data = SuscripcionCreate(
        nombre="Netflix",
        monto=Decimal("39000.00"),
        moneda="ARS",
        frecuencia="mensual",
        proximo_cobro=date(2026, 10, 13),
        billetera_id=b.id,
        categoria_id=cat.id,
    )
    sub = suscripcion_service.crear_suscripcion(db_session, u.id, data)

    txs = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub.id)
    ).scalars().all()

    assert len(txs) == 1
    assert txs[0].fecha == date(2026, 10, 13)
    assert txs[0].monto == Decimal("39000.00")
    assert txs[0].suscripcion_id == sub.id
    b_db = db_session.get(Billetera, b.id)
    assert b_db.saldo_actual == Decimal("61000.00")
    assert sub.proximo_cobro == date(2026, 11, 13)


def test_caso_3_crear_hoy_con_tarjeta(db_session, datos_base):
    """3. Crear con 2026-10-13 y tarjeta en pesos (cierre 24, vencimiento 7) -> 1 padre con fecha 2026-10-13, grupo de 1 cuota cuyo vencimiento es calcular_primer_vencimiento(2026-10-13, 24, 7, False); proximo_cobro 2026-11-13."""
    u = datos_base["usuario"]
    tarjeta = datos_base["tarjeta"]
    cat = datos_base["categoria"]

    data = SuscripcionCreate(
        nombre="Gym",
        monto=Decimal("39000.00"),
        moneda="ARS",
        frecuencia="mensual",
        proximo_cobro=date(2026, 10, 13),
        tarjeta_id=tarjeta.id,
        categoria_id=cat.id,
    )
    sub = suscripcion_service.crear_suscripcion(db_session, u.id, data)

    txs = db_session.execute(
        select(Transaccion).where(
            Transaccion.suscripcion_id == sub.id,
            Transaccion.es_padre_cuotas == True,
        )
    ).scalars().all()

    assert len(txs) == 1
    padre = txs[0]
    from app.models.grupo_cuotas import GrupoCuotas
    grupo = db_session.execute(
        select(GrupoCuotas).where(GrupoCuotas.transaccion_padre_id == padre.id)
    ).scalar_one_or_none()
    assert grupo is not None
    assert grupo.cantidad_cuotas == 1
    venc_esperado = calcular_primer_vencimiento(date(2026, 10, 13), 24, 7, False)
    assert grupo.primer_vencimiento == venc_esperado
    assert sub.proximo_cobro == date(2026, 11, 13)


def test_caso_4_crear_pasada_cae_hoy_al_normalizar(db_session, datos_base):
    """4. Crear con 2026-08-13 (pasada; cae hoy al normalizar) -> 1 transacción con fecha 2026-10-13; proximo_cobro 2026-11-13; ninguna transacción de agosto ni de septiembre."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    data = SuscripcionCreate(
        nombre="Amazon Prime",
        monto=Decimal("39000.00"),
        moneda="ARS",
        frecuencia="mensual",
        proximo_cobro=date(2026, 8, 13),
        billetera_id=b.id,
        categoria_id=cat.id,
    )
    sub = suscripcion_service.crear_suscripcion(db_session, u.id, data)

    txs = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub.id)
    ).scalars().all()

    assert len(txs) == 1
    assert txs[0].fecha == date(2026, 10, 13)
    assert sub.proximo_cobro == date(2026, 11, 13)

    # Verificar que no hay transacciones de agosto ni septiembre
    for tx in txs:
        assert not (tx.fecha.year == 2026 and tx.fecha.month in (8, 9))


def test_caso_5_crear_pasada_cae_en_fecha_futura(db_session, datos_base):
    """5. Crear con 2026-08-20 -> 0 transacciones; proximo_cobro 2026-10-20."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    data = SuscripcionCreate(
        nombre="YouTube Premium",
        monto=Decimal("39000.00"),
        moneda="ARS",
        frecuencia="mensual",
        proximo_cobro=date(2026, 8, 20),
        billetera_id=b.id,
        categoria_id=cat.id,
    )
    sub = suscripcion_service.crear_suscripcion(db_session, u.id, data)

    txs = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub.id)
    ).scalars().all()

    assert len(txs) == 0
    assert sub.proximo_cobro == date(2026, 10, 20)


def test_caso_6_actualizar_suscripcion_fecha_pasada(db_session, datos_base):
    """6. actualizar_suscripcion de la del caso 1 a 2026-09-01 -> proximo_cobro 2026-11-01, 0 transacciones."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    data = SuscripcionCreate(
        nombre="Disney+",
        monto=Decimal("39000.00"),
        moneda="ARS",
        frecuencia="mensual",
        proximo_cobro=date(2026, 11, 13),
        billetera_id=b.id,
        categoria_id=cat.id,
    )
    sub = suscripcion_service.crear_suscripcion(db_session, u.id, data)

    # Actualizar proximo_cobro a 2026-09-01
    upd = SuscripcionUpdate(proximo_cobro=date(2026, 9, 1))
    sub_act = suscripcion_service.actualizar_suscripcion(db_session, u.id, sub.id, upd)

    assert sub_act.proximo_cobro == date(2026, 11, 1)

    txs = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub.id)
    ).scalars().all()
    assert len(txs) == 0


def test_caso_7_cobros_pendientes_reales(db_session, datos_base):
    """7. Suscripción creada el 2026-08-01 (fecha_creacion), con una transacción previa del 2026-08-11 y proximo_cobro 2026-09-11 -> procesar_cobros_suscripciones crea 2 transacciones, con fechas 2026-09-11 y 2026-10-11; proximo_cobro 2026-11-11; 1 notificación con el texto exacto 'Anotamos 2 cobros de \'<nombre>\' que estaban pendientes.'."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    sub = Suscripcion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Club La Nacion",
        categoria_id=cat.id,
        frecuencia="mensual",
        proximo_cobro=date(2026, 9, 11),
        estado=EstadoSuscripcion.ACTIVA,
        fecha_creacion=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(sub)
    db_session.flush()

    from app.models.historial_suscripcion import HistorialSuscripcion
    hp = HistorialSuscripcion(
        suscripcion_id=sub.id,
        monto=Decimal("39000.00"),
        moneda="ARS",
        vigente_desde=date(2026, 8, 1),
    )
    tx_previa = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo="egreso",
        monto=Decimal("39000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 11),
        descripcion=sub.nombre,
        categoria_id=cat.id,
        billetera_id=b.id,
        suscripcion_id=sub.id,
        origen=OrigenTransaccion.RECURRENTE,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add_all([hp, tx_previa])
    db_session.commit()

    resultado = procesar_cobros_suscripciones(db_session, commit=True)
    assert resultado["cobradas"] == 1

    txs = db_session.execute(
        select(Transaccion)
        .where(Transaccion.suscripcion_id == sub.id)
        .order_by(Transaccion.fecha.asc())
    ).scalars().all()

    # Previa (2026-08-11) + 2 nuevas (2026-09-11 y 2026-10-11) = 3 total
    assert len(txs) == 3
    fechas = [t.fecha for t in txs]
    assert fechas == [date(2026, 8, 11), date(2026, 9, 11), date(2026, 10, 11)]

    sub_db = db_session.get(Suscripcion, sub.id)
    assert sub_db.proximo_cobro == date(2026, 11, 11)

    notifs = db_session.execute(
        select(Notificacion).where(Notificacion.usuario_id == u.id)
    ).scalars().all()
    assert len(notifs) == 1
    assert notifs[0].mensaje == "Anotamos 2 cobros de 'Club La Nacion' que estaban pendientes."


def test_caso_8_idempotencia_dos_corridas_caso_7(db_session, datos_base):
    """8. Correr procesar_cobros_suscripciones dos veces sobre el caso 7 -> siguen siendo 2 transacciones nuevas en total."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    sub = Suscripcion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Club La Nacion",
        categoria_id=cat.id,
        frecuencia="mensual",
        proximo_cobro=date(2026, 9, 11),
        estado=EstadoSuscripcion.ACTIVA,
        fecha_creacion=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(sub)
    db_session.flush()

    from app.models.historial_suscripcion import HistorialSuscripcion
    hp = HistorialSuscripcion(
        suscripcion_id=sub.id,
        monto=Decimal("39000.00"),
        moneda="ARS",
        vigente_desde=date(2026, 8, 1),
    )
    tx_previa = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo="egreso",
        monto=Decimal("39000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 11),
        descripcion=sub.nombre,
        categoria_id=cat.id,
        billetera_id=b.id,
        suscripcion_id=sub.id,
        origen=OrigenTransaccion.RECURRENTE,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add_all([hp, tx_previa])
    db_session.commit()

    # Corrida 1
    procesar_cobros_suscripciones(db_session, commit=True)

    # Corrida 2
    procesar_cobros_suscripciones(db_session, commit=True)

    txs = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub.id)
    ).scalars().all()
    # Siguen siendo 3 en total (1 previa + 2 nuevas)
    assert len(txs) == 3


def test_caso_9_suscripcion_creada_hoy_fecha_vieja(db_session, datos_base):
    """9. Suscripción creada hoy (fecha_creacion 2026-10-13), sin transacciones, con proximo_cobro forzado en la base a 2026-08-13 -> procesar_cobros_suscripciones cobra 1 sola transacción con fecha 2026-10-13 y proximo_cobro queda 2026-11-13. Con 2026-08-20 en vez de 2026-08-13 -> 0 transacciones y proximo_cobro 2026-10-20."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    from app.models.historial_suscripcion import HistorialSuscripcion

    # Parte A: proximo_cobro forzado a 2026-08-13 (cae hoy al normalizar)
    sub_a = Suscripcion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Vieja Que Cae Hoy",
        categoria_id=cat.id,
        frecuencia="mensual",
        proximo_cobro=date(2026, 8, 13),
        estado=EstadoSuscripcion.ACTIVA,
        fecha_creacion=datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc),
    )
    db_session.add(sub_a)
    db_session.flush()
    db_session.add(HistorialSuscripcion(
        suscripcion_id=sub_a.id,
        monto=Decimal("39000.00"),
        moneda="ARS",
        vigente_desde=date(2026, 8, 13),
    ))

    # Parte B: proximo_cobro forzado a 2026-08-20 (cae en el futuro al normalizar)
    sub_b = Suscripcion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Vieja Que Cae Futuro",
        categoria_id=cat.id,
        frecuencia="mensual",
        proximo_cobro=date(2026, 8, 20),
        estado=EstadoSuscripcion.ACTIVA,
        fecha_creacion=datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc),
    )
    db_session.add(sub_b)
    db_session.flush()
    db_session.add(HistorialSuscripcion(
        suscripcion_id=sub_b.id,
        monto=Decimal("39000.00"),
        moneda="ARS",
        vigente_desde=date(2026, 8, 20),
    ))
    db_session.commit()

    procesar_cobros_suscripciones(db_session, commit=True)

    # Validaciones Parte A
    sub_a_db = db_session.get(Suscripcion, sub_a.id)
    assert sub_a_db.proximo_cobro == date(2026, 11, 13)
    txs_a = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub_a.id)
    ).scalars().all()
    assert len(txs_a) == 1
    assert txs_a[0].fecha == date(2026, 10, 13)

    # Validaciones Parte B
    sub_b_db = db_session.get(Suscripcion, sub_b.id)
    assert sub_b_db.proximo_cobro == date(2026, 10, 20)
    txs_b = db_session.execute(
        select(Transaccion).where(Transaccion.suscripcion_id == sub_b.id)
    ).scalars().all()
    assert len(txs_b) == 0


def test_caso_10_cobro_salteado_fechas_reales(db_session, datos_base):
    """10. Suscripción con fecha_creacion 2026-08-01, transacción previa del 2026-08-05 y proximo_cobro 2026-09-05 (cobro diario salteado) -> cobra 2026-09-05 y 2026-10-05 con sus fechas reales."""
    u = datos_base["usuario"]
    b = datos_base["billetera"]
    cat = datos_base["categoria"]

    sub = Suscripcion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Servicio Salteado",
        categoria_id=cat.id,
        frecuencia="mensual",
        proximo_cobro=date(2026, 9, 5),
        estado=EstadoSuscripcion.ACTIVA,
        fecha_creacion=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
    )
    db_session.add(sub)
    db_session.flush()

    from app.models.historial_suscripcion import HistorialSuscripcion
    hp = HistorialSuscripcion(
        suscripcion_id=sub.id,
        monto=Decimal("39000.00"),
        moneda="ARS",
        vigente_desde=date(2026, 8, 1),
    )
    tx_previa = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo="egreso",
        monto=Decimal("39000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 5),
        descripcion=sub.nombre,
        categoria_id=cat.id,
        billetera_id=b.id,
        suscripcion_id=sub.id,
        origen=OrigenTransaccion.RECURRENTE,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add_all([hp, tx_previa])
    db_session.commit()

    procesar_cobros_suscripciones(db_session, commit=True)

    txs = db_session.execute(
        select(Transaccion)
        .where(Transaccion.suscripcion_id == sub.id)
        .order_by(Transaccion.fecha.asc())
    ).scalars().all()

    assert len(txs) == 3
    fechas = [t.fecha for t in txs]
    assert fechas == [date(2026, 8, 5), date(2026, 9, 5), date(2026, 10, 5)]

    sub_db = db_session.get(Suscripcion, sub.id)
    assert sub_db.proximo_cobro == date(2026, 11, 5)
