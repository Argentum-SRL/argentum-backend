"""Pruebas para eliminación definitiva y purga de presupuestos (E1 a E6)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Compiladores para SQLite en fixtures de prueba
if not hasattr(PGUUID, "_compiler_dispatcher") or "sqlite" not in getattr(PGUUID._compiler_dispatcher, "specs", {}):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if not hasattr(JSONB, "_compiler_dispatcher") or "sqlite" not in getattr(JSONB._compiler_dispatcher, "specs", {}):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"

from app.main import app
from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.core.limiter import limiter
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.presupuesto import (
    Presupuesto,
    EstadoPresupuesto,
    PeriodoPresupuestoTipo,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
)
from app.services.presupuesto_service import purgar_presupuestos_eliminados

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

usuario_autenticado_actual: Usuario | None = None


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def override_get_current_user():
    return usuario_autenticado_actual


def get_error_message(resp) -> str:
    """Extrae el mensaje de error de la respuesta, compatible con envelope de Argentum y FastAPI."""
    data = resp.json()
    if isinstance(data, dict):
        if "detail" in data:
            return data["detail"]
        if "error" in data and isinstance(data["error"], dict) and "message" in data["error"]:
            return data["error"]["message"]
    return str(data)


@pytest.fixture(autouse=True)
def deshabilitar_rate_limiter(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    Base.metadata.create_all(bind=engine)
    yield
    # No requerimos drop_all en memoria


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(name="client")
def client_fixture(db_session):
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(name="usuario_principal")
def usuario_principal_fixture(db_session):
    global usuario_autenticado_actual
    usr = Usuario(
        id=uuid.uuid4(),
        email=f"test_presupuesto_{uuid.uuid4().hex[:6]}@argentum.com",
        nombre="Test",
        apellido="User",
        password_hash="hashed_dummy_pw",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        moneda_principal=Moneda.ARS,
        fecha_registro=datetime.now(timezone.utc),
    )
    db_session.add(usr)
    db_session.commit()
    db_session.refresh(usr)
    usuario_autenticado_actual = usr
    return usr


@pytest.fixture(name="usuario_otro")
def usuario_otro_fixture(db_session):
    usr = Usuario(
        id=uuid.uuid4(),
        email=f"otro_user_{uuid.uuid4().hex[:6]}@argentum.com",
        nombre="Otro",
        apellido="User",
        password_hash="hashed_dummy_pw",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        moneda_principal=Moneda.ARS,
        fecha_registro=datetime.now(timezone.utc),
    )
    db_session.add(usr)
    db_session.commit()
    db_session.refresh(usr)
    return usr


@pytest.fixture(name="categoria_prueba")
def categoria_prueba_fixture(db_session):
    cat = Categoria(
        id=uuid.uuid4(),
        nombre=f"Supermercado {uuid.uuid4().hex[:6]}",
        tipo=TipoCategoria.EGRESO,
    )
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def test_e1_delete_borra_definitivo_con_periodos_y_categorias(
    client, db_session, usuario_principal, categoria_prueba
):
    """E1: DELETE borra el presupuesto, sus 2 períodos y 2 categorías; GET -> 404."""
    cat_extra = Categoria(
        id=uuid.uuid4(),
        nombre=f"Transporte {uuid.uuid4().hex[:6]}",
        tipo=TipoCategoria.EGRESO,
    )
    db_session.add(cat_extra)
    db_session.flush()

    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Alimentos",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db_session.add(presu)
    db_session.flush()

    per1 = PeriodoPresupuesto(
        id=uuid.uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2026, 10, 31),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
    )
    per2 = PeriodoPresupuesto(
        id=uuid.uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 11, 1),
        fecha_fin=date(2026, 11, 30),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
    )
    pc1 = PresupuestoCategoria(
        id=uuid.uuid4(),
        presupuesto_id=presu.id,
        categoria_id=categoria_prueba.id,
    )
    pc2 = PresupuestoCategoria(
        id=uuid.uuid4(),
        presupuesto_id=presu.id,
        categoria_id=cat_extra.id,
    )
    db_session.add_all([per1, per2, pc1, pc2])
    db_session.commit()
    presu_id = presu.id
    per1_id, per2_id = per1.id, per2.id
    pc1_id, pc2_id = pc1.id, pc2.id

    resp = client.delete(f"/presupuestos/{presu_id}")
    assert resp.status_code == 200
    assert resp.json() == {"detail": "Presupuesto eliminado."}

    db_session.expunge_all()
    assert db_session.get(Presupuesto, presu_id) is None
    assert db_session.get(PeriodoPresupuesto, per1_id) is None
    assert db_session.get(PeriodoPresupuesto, per2_id) is None
    assert db_session.get(PresupuestoCategoria, pc1_id) is None
    assert db_session.get(PresupuestoCategoria, pc2_id) is None

    resp_get = client.get(f"/presupuestos/{presu_id}")
    assert resp_get.status_code == 404
    assert get_error_message(resp_get) == "No se encontró ese presupuesto."


def test_e2_delete_dos_veces(client, db_session, usuario_principal):
    """E2: DELETE dos veces -> la segunda 404."""
    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Temporal",
        monto=Decimal("15000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db_session.add(presu)
    db_session.commit()

    resp1 = client.delete(f"/presupuestos/{presu.id}")
    assert resp1.status_code == 200

    resp2 = client.delete(f"/presupuestos/{presu.id}")
    assert resp2.status_code == 404
    assert get_error_message(resp2) == "No se encontró ese presupuesto."


def test_e3_delete_presupuesto_otro_usuario(
    client, db_session, usuario_principal, usuario_otro
):
    """E3: DELETE de un presupuesto de otro usuario -> 404 y sigue en la base."""
    _ = usuario_principal
    presu_otro = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_otro.id,
        nombre="Presupuesto Ajeno",
        monto=Decimal("80000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db_session.add(presu_otro)
    db_session.commit()

    resp_del = client.delete(f"/presupuestos/{presu_otro.id}")
    assert resp_del.status_code == 404
    assert get_error_message(resp_del) == "No se encontró ese presupuesto."

    db_session.expire_all()
    assert db_session.get(Presupuesto, presu_otro.id) is not None


def test_e4_restaurar_no_existe(client, db_session, usuario_principal):
    """E4: POST /presupuestos/{id}/restaurar -> 404 o 405 (la ruta no existe)."""
    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Sin Restaurar",
        monto=Decimal("25000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db_session.add(presu)
    db_session.commit()

    resp = client.post(
        f"/presupuestos/{presu.id}/restaurar",
        json={"estado": "activo"},
    )
    assert resp.status_code in (404, 405)


def test_e5_purgar_presupuestos_eliminados(
    client, db_session, usuario_principal, categoria_prueba
):
    """E5: 91 días desaparece con hijos; 89 días queda y no sale en GET."""
    hace_91 = datetime.now(timezone.utc) - timedelta(days=91)
    hace_89 = datetime.now(timezone.utc) - timedelta(days=89)

    p_viejo = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto a purgar",
        monto=Decimal("40000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.FINALIZADO,
        eliminado_en=hace_91,
        fecha_creacion=hace_91 - timedelta(days=30),
    )
    per_viejo = PeriodoPresupuesto(
        id=uuid.uuid4(),
        presupuesto_id=p_viejo.id,
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 31),
        monto_limite=Decimal("40000.00"),
        monto_usado=Decimal("0.00"),
    )
    cat_viejo = PresupuestoCategoria(
        id=uuid.uuid4(),
        presupuesto_id=p_viejo.id,
        categoria_id=categoria_prueba.id,
    )

    p_reciente = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto a conservar",
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.FINALIZADO,
        eliminado_en=hace_89,
        fecha_creacion=hace_89 - timedelta(days=30),
    )
    per_reciente = PeriodoPresupuesto(
        id=uuid.uuid4(),
        presupuesto_id=p_reciente.id,
        fecha_inicio=date(2026, 2, 1),
        fecha_fin=date(2026, 2, 28),
        monto_limite=Decimal("50000.00"),
        monto_usado=Decimal("0.00"),
    )
    cat_reciente = PresupuestoCategoria(
        id=uuid.uuid4(),
        presupuesto_id=p_reciente.id,
        categoria_id=categoria_prueba.id,
    )

    db_session.add_all(
        [p_viejo, per_viejo, cat_viejo, p_reciente, per_reciente, cat_reciente]
    )
    db_session.commit()

    total_purgados = purgar_presupuestos_eliminados(db_session)
    assert total_purgados >= 1

    db_session.expire_all()
    assert db_session.get(Presupuesto, p_viejo.id) is None
    assert db_session.get(PeriodoPresupuesto, per_viejo.id) is None
    assert db_session.get(PresupuestoCategoria, cat_viejo.id) is None

    assert db_session.get(Presupuesto, p_reciente.id) is not None
    assert db_session.get(PeriodoPresupuesto, per_reciente.id) is not None
    assert db_session.get(PresupuestoCategoria, cat_reciente.id) is not None

    resp_list = client.get("/presupuestos")
    assert resp_list.status_code == 200
    ids = [p["id"] for p in resp_list.json()]
    assert str(p_reciente.id) not in ids


def test_e6_transacciones_quedan_intactas_al_eliminar(
    client, db_session, usuario_principal, categoria_prueba
):
    """E6: las transacciones de la categoría del presupuesto borrado siguen todas."""
    hoy = date(2026, 11, 18)
    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Supermercado",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    periodo = PeriodoPresupuesto(
        id=uuid.uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 11, 1),
        fecha_fin=date(2026, 11, 30),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
    )
    pc = PresupuestoCategoria(
        id=uuid.uuid4(),
        presupuesto_id=presu.id,
        categoria_id=categoria_prueba.id,
    )
    billetera = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Efectivo Test",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        es_principal=True,
    )
    db_session.add_all([presu, periodo, pc, billetera])
    db_session.flush()

    tx1 = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        billetera_id=billetera.id,
        categoria_id=categoria_prueba.id,
        monto=Decimal("12500.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx2 = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        billetera_id=billetera.id,
        categoria_id=categoria_prueba.id,
        monto=Decimal("8750.50"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy,
        metodo_pago=MetodoPago.EFECTIVO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add_all([tx1, tx2])
    db_session.commit()

    resp = client.delete(f"/presupuestos/{presu.id}")
    assert resp.status_code == 200

    db_session.expire_all()
    txs = (
        db_session.query(Transaccion)
        .filter(
            Transaccion.usuario_id == usuario_principal.id,
            Transaccion.categoria_id == categoria_prueba.id,
        )
        .order_by(Transaccion.monto.asc())
        .all()
    )
    assert len(txs) == 2
    assert [t.monto for t in txs] == [Decimal("8750.50"), Decimal("12500.00")]
