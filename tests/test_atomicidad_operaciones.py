from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

import uuid
from sqlalchemy import types

_orig_uuid_processor = types.Uuid.bind_processor
def _safe_uuid_processor(self, dialect):
    proc = _orig_uuid_processor(self, dialect)
    if proc is None:
        return None
    def process(value):
        if isinstance(value, str):
            value = uuid.UUID(value)
        return proc(value)
    return process
types.Uuid.bind_processor = _safe_uuid_processor

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.notificacion import Notificacion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.schemas.transaccion import InfoCuotas, TransaccionCreate, TransaccionUpdate
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import (
    cuotas_service,
    meta_service,
    rendimiento_billetera_service,
    transaccion_service,
    transferencia_service,
)


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


def _crear_base(db):
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
        saldo_actual=Decimal("10000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    cat = Categoria(id=uuid4(), nombre="General", tipo=TipoCategoria.EGRESO)
    cat_banco = Categoria(id=uuid4(), nombre="Banco", tipo=TipoCategoria.EGRESO)
    db.add_all([u, b, cat, cat_banco])
    db.commit()
    return u, b, cat


# 1. Débito normal
def test_debito_normal_commit_true(db_session):
    u, b, cat = _crear_base(db_session)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Supermercado",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
    )
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=True)
        assert mock_commit.call_count == 1
        assert tx.id is not None
        b_ref = db_session.get(Billetera, b.id)
        assert b_ref.saldo_actual == Decimal("9000.00")


def test_debito_normal_commit_false_rollback(db_session):
    u, b, cat = _crear_base(db_session)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Supermercado",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
    )
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=False)
        assert mock_commit.call_count == 0
        tx_id = tx.id
        db_session.rollback()

    assert db_session.get(Transaccion, tx_id) is None
    assert db_session.get(Billetera, b.id).saldo_actual == Decimal("10000.00")


# 2. Débito que deja la billetera en 0 o menos (sin configuración previa)
def test_debito_saldo_cero_commit_true(db_session):
    u, b, cat = _crear_base(db_session)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Gasto total",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
    )
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=True)
        assert mock_commit.call_count == 1
        assert tx.id is not None
        b_ref = db_session.get(Billetera, b.id)
        assert b_ref.saldo_actual == Decimal("0.00")
        notifs = db_session.execute(select(Notificacion).where(Notificacion.usuario_id == u.id)).scalars().all()
        assert len(notifs) >= 1


def test_debito_saldo_cero_commit_false_rollback(db_session):
    u, b, cat = _crear_base(db_session)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Gasto total",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
    )
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=False)
        assert mock_commit.call_count == 0
        tx_id = tx.id
        db_session.rollback()

    assert db_session.get(Transaccion, tx_id) is None
    assert db_session.get(Billetera, b.id).saldo_actual == Decimal("10000.00")
    notifs = db_session.execute(select(Notificacion).where(Notificacion.usuario_id == u.id)).scalars().all()
    assert len(notifs) == 0


# 3. Crédito en cuotas
def test_credito_cuotas_commit_true(db_session):
    u, b, cat = _crear_base(db_session)
    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Visa Gold",
        billetera_id=b.id,
        dia_cierre=20,
        dia_vencimiento=10,
    )
    db_session.add(t)
    db_session.commit()

    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("3000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Electrodoméstico",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.CREDITO,
        billetera_id=b.id,
        tarjeta_id=t.id,
        info_cuotas=InfoCuotas(cantidad_cuotas=3, monto_total=Decimal("3000.00")),
    )
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=True)
        assert mock_commit.call_count == 1
        assert tx.id is not None
        cuotas = db_session.execute(select(Cuota).where(Cuota.grupo_id == tx.grupo_cuotas_id)).scalars().all()
        assert len(cuotas) == 3


def test_credito_cuotas_commit_false_rollback(db_session):
    u, b, cat = _crear_base(db_session)
    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Visa Gold",
        billetera_id=b.id,
        dia_cierre=20,
        dia_vencimiento=10,
    )
    db_session.add(t)
    db_session.commit()

    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("3000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Electrodoméstico",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.CREDITO,
        billetera_id=b.id,
        tarjeta_id=t.id,
        info_cuotas=InfoCuotas(cantidad_cuotas=3, monto_total=Decimal("3000.00")),
    )
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=False)
        assert mock_commit.call_count == 0
        tx_id = tx.id
        grupo_id = tx.grupo_cuotas_id
        db_session.rollback()

    assert db_session.get(Transaccion, tx_id) is None
    if grupo_id:
        assert db_session.get(GrupoCuotas, grupo_id) is None


# 4. Edición de monto
def test_edicion_transaccion_commit_true(db_session):
    u, b, cat = _crear_base(db_session)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Comida",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
    )
    tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=True)

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tx_up = transaccion_service.actualizar_transaccion(
            db_session, u.id, tx.id, TransaccionUpdate(monto=Decimal("2500.00")), commit=True
        )
        assert mock_commit.call_count == 1
        assert tx_up.monto == Decimal("2500.00")
        assert db_session.get(Billetera, b.id).saldo_actual == Decimal("7500.00")


def test_edicion_transaccion_commit_false_rollback(db_session):
    u, b, cat = _crear_base(db_session)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Comida",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
    )
    tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=True)

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        transaccion_service.actualizar_transaccion(
            db_session, u.id, tx.id, TransaccionUpdate(monto=Decimal("2500.00")), commit=False
        )
        assert mock_commit.call_count == 0
        db_session.rollback()

    tx_orig = db_session.get(Transaccion, tx.id)
    assert tx_orig.monto == Decimal("1000.00")
    assert db_session.get(Billetera, b.id).saldo_actual == Decimal("9000.00")


# 5. Borrado de transacción
def test_borrado_transaccion_commit_true(db_session):
    u, b, cat = _crear_base(db_session)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1500.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Gasto a borrar",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
    )
    tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=True)

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        transaccion_service.eliminar_transaccion(db_session, u.id, tx.id, commit=True)
        assert mock_commit.call_count == 1
        assert db_session.get(Transaccion, tx.id) is None
        assert db_session.get(Billetera, b.id).saldo_actual == Decimal("10000.00")


def test_borrado_transaccion_commit_false_rollback(db_session):
    u, b, cat = _crear_base(db_session)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1500.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Gasto a borrar",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
    )
    tx = transaccion_service.crear_transaccion(db_session, u.id, data, commit=True)

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        transaccion_service.eliminar_transaccion(db_session, u.id, tx.id, commit=False)
        assert mock_commit.call_count == 0
        db_session.rollback()

    assert db_session.get(Transaccion, tx.id) is not None
    assert db_session.get(Billetera, b.id).saldo_actual == Decimal("8500.00")


# 6. Confirmar una pendiente
def test_confirmar_pendiente_commit_true(db_session):
    u, b, cat = _crear_base(db_session)
    tx_pend = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("2000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Ticket pendiente",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
    )
    db_session.add(tx_pend)
    db_session.commit()

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tx_conf = transaccion_service.confirmar_transaccion_ia(db_session, u.id, tx_pend.id, commit=True)
        assert mock_commit.call_count == 1
        assert tx_conf.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
        assert db_session.get(Billetera, b.id).saldo_actual == Decimal("8000.00")


def test_confirmar_pendiente_commit_false_rollback(db_session):
    u, b, cat = _crear_base(db_session)
    tx_pend = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("2000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        descripcion="Ticket pendiente",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b.id,
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
    )
    db_session.add(tx_pend)
    db_session.commit()

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        transaccion_service.confirmar_transaccion_ia(db_session, u.id, tx_pend.id, commit=False)
        assert mock_commit.call_count == 0
        db_session.rollback()

    tx_ref = db_session.get(Transaccion, tx_pend.id)
    assert tx_ref.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
    assert db_session.get(Billetera, b.id).saldo_actual == Decimal("10000.00")


# 7. Transferencia con comisión y su borrado
def test_transferencia_con_comision_commit_true_y_borrado(db_session):
    u, b1, cat = _crear_base(db_session)
    b2 = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Secundaria",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(b2)
    db_session.commit()

    data = TransferenciaInternaCreate(
        billetera_origen_id=b1.id,
        billetera_destino_id=b2.id,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        notas="Envio de fondos",
        monto_comision=Decimal("50.00"),
    )

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tr = transferencia_service.crear_transferencia(db_session, u.id, data, commit=True)
        assert mock_commit.call_count == 1
        assert tr.id is not None
        assert db_session.get(Billetera, b1.id).saldo_actual == Decimal("8950.00")
        assert db_session.get(Billetera, b2.id).saldo_actual == Decimal("1500.00")

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        transferencia_service.eliminar_transferencia(db_session, u.id, tr.id, commit=True)
        assert mock_commit.call_count == 1
        assert db_session.get(TransferenciaInterna, tr.id) is None
        assert db_session.get(Billetera, b1.id).saldo_actual == Decimal("10000.00")
        assert db_session.get(Billetera, b2.id).saldo_actual == Decimal("500.00")


def test_transferencia_commit_false_rollback(db_session):
    u, b1, cat = _crear_base(db_session)
    b2 = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Secundaria",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(b2)
    db_session.commit()

    data = TransferenciaInternaCreate(
        billetera_origen_id=b1.id,
        billetera_destino_id=b2.id,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        notas="Envio de fondos",
        monto_comision=Decimal("50.00"),
    )

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        tr = transferencia_service.crear_transferencia(db_session, u.id, data, commit=False)
        assert mock_commit.call_count == 0
        tr_id = tr.id
        db_session.rollback()

    assert db_session.get(TransferenciaInterna, tr_id) is None
    assert db_session.get(Billetera, b1.id).saldo_actual == Decimal("10000.00")
    assert db_session.get(Billetera, b2.id).saldo_actual == Decimal("500.00")


# 8. Aporte a meta y su borrado
def test_meta_aporte_commit_true_y_borrado(db_session):
    u, b, cat = _crear_base(db_session)
    meta = Meta(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Vacaciones",
        monto_objetivo=Decimal("50000.00"),
        monto_actual=Decimal("0.00"),
        moneda=Moneda.ARS,
        estado=EstadoMeta.ACTIVA,
    )
    db_session.add(meta)
    db_session.commit()

    data = MovimientoMetaCreate(
        monto=Decimal("3000.00"),
        tipo=TipoMovimientoMeta.APORTE,
        moneda_movimiento=Moneda.ARS,
        billetera_id=b.id,
        fecha=date.today(),
    )

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        mov = meta_service.registrar_movimiento(db_session, u.id, meta.id, data, commit=True)
        assert mock_commit.call_count == 1
        assert mov.id is not None
        assert db_session.get(Meta, meta.id).monto_actual == Decimal("3000.00")
        assert db_session.get(Billetera, b.id).saldo_actual == Decimal("7000.00")

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        meta_service.eliminar_movimiento(db_session, u.id, meta.id, mov.id, commit=True)
        assert mock_commit.call_count == 1
        assert db_session.get(MovimientoMeta, mov.id) is None
        assert db_session.get(Meta, meta.id).monto_actual == Decimal("0.00")
        assert db_session.get(Billetera, b.id).saldo_actual == Decimal("10000.00")


def test_meta_aporte_commit_false_rollback(db_session):
    u, b, cat = _crear_base(db_session)
    meta = Meta(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Vacaciones",
        monto_objetivo=Decimal("50000.00"),
        monto_actual=Decimal("0.00"),
        moneda=Moneda.ARS,
        estado=EstadoMeta.ACTIVA,
    )
    db_session.add(meta)
    db_session.commit()

    data = MovimientoMetaCreate(
        monto=Decimal("3000.00"),
        tipo=TipoMovimientoMeta.APORTE,
        moneda_movimiento=Moneda.ARS,
        billetera_id=b.id,
        fecha=date.today(),
    )

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        mov = meta_service.registrar_movimiento(db_session, u.id, meta.id, data, commit=False)
        assert mock_commit.call_count == 0
        mov_id = mov.id
        db_session.rollback()

    assert db_session.get(MovimientoMeta, mov_id) is None
    assert db_session.get(Meta, meta.id).monto_actual == Decimal("0.00")
    assert db_session.get(Billetera, b.id).saldo_actual == Decimal("10000.00")


# 9. Rendimiento de billetera
def test_rendimiento_billetera_commit_true(db_session):
    u, b, cat = _crear_base(db_session)
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        rendimiento_billetera_service.confirmar_rendimiento(
            db_session, u.id, b.id, Decimal("150.00"), commit=True
        )
        assert mock_commit.call_count == 1
        assert db_session.get(Billetera, b.id).saldo_actual == Decimal("10150.00")


def test_rendimiento_billetera_commit_false_rollback(db_session):
    u, b, cat = _crear_base(db_session)
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        rendimiento_billetera_service.confirmar_rendimiento(
            db_session, u.id, b.id, Decimal("150.00"), commit=False
        )
        assert mock_commit.call_count == 0
        db_session.rollback()

    assert db_session.get(Billetera, b.id).saldo_actual == Decimal("10000.00")
