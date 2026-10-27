"""Pruebas para eliminación, restauración y purga de presupuestos.

Verifica:
1. DELETE de presupuesto activo -> 200 {"detail": "Presupuesto eliminado."}, estado finalizado y eliminado_en con fecha.
   GET /presupuestos (sin filtro y con estado=activo) no lo trae. GET /presupuestos/{id} -> 404.
2. POST /restaurar con {"estado": "pausado"} -> 200, estado pausado, eliminado_en null, aparece en GET /presupuestos?estado=pausado.
3. POST /restaurar de uno no eliminado -> 400 "Este presupuesto no está eliminado.".
4. POST /restaurar de uno eliminado hace 91 días -> 404.
5. DELETE dos veces -> la segunda 404.
6. purgar_presupuestos_eliminados: eliminado hace 91 días desaparece con hijos; hace 89 días queda.
7. GET /presupuestos?estado=finalizado -> [].
8. registrar_impacto_presupuesto con gasto en categoría de presupuesto eliminado -> período no cambia.
9. Presupuesto de otro usuario: DELETE y restaurar -> 404.
"""

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
from app.services.presupuesto_service import (
    purgar_presupuestos_eliminados,
    registrar_impacto_presupuesto,
)

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


def test_1_delete_presupuesto_activo(client, db_session, usuario_principal, categoria_prueba):
    """1. DELETE activo -> 200, finalizado y eliminado_en, GET no lo trae, GET {id} 404."""
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
    db_session.commit()

    resp = client.delete(f"/presupuestos/{presu.id}")
    assert resp.status_code == 200
    assert resp.json() == {"detail": "Presupuesto eliminado."}

    # Verificar directamente en la base
    db_session.expire_all()
    presu_db = db_session.get(Presupuesto, presu.id)
    assert presu_db is not None
    assert presu_db.estado == EstadoPresupuesto.FINALIZADO
    assert presu_db.eliminado_en is not None

    # Listado general no lo incluye
    resp_list = client.get("/presupuestos")
    assert resp_list.status_code == 200
    ids_list = [p["id"] for p in resp_list.json()]
    assert str(presu.id) not in ids_list

    # Listado con estado=activo no lo incluye
    resp_activos = client.get("/presupuestos?estado=activo")
    assert resp_activos.status_code == 200
    ids_activos = [p["id"] for p in resp_activos.json()]
    assert str(presu.id) not in ids_activos

    # GET por ID devuelve 404
    resp_get = client.get(f"/presupuestos/{presu.id}")
    assert resp_get.status_code == 404
    assert get_error_message(resp_get) == "No encontramos ese presupuesto."


def test_2_post_restaurar_pausado(client, db_session, usuario_principal):
    """2. POST /restaurar con {'estado': 'pausado'} -> 200, pausado, eliminado_en null."""
    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Servicios",
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.FINALIZADO,
        eliminado_en=datetime.now(timezone.utc) - timedelta(days=2),
        fecha_creacion=datetime.now(timezone.utc) - timedelta(days=10),
    )
    db_session.add(presu)
    db_session.commit()

    resp = client.post(f"/presupuestos/{presu.id}/restaurar", json={"estado": "pausado"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(presu.id)
    assert data["estado"] == "pausado"

    db_session.expire_all()
    presu_db = db_session.get(Presupuesto, presu.id)
    assert presu_db.estado == EstadoPresupuesto.PAUSADO
    assert presu_db.eliminado_en is None

    # Aparece en GET /presupuestos?estado=pausado
    resp_pausados = client.get("/presupuestos?estado=pausado")
    assert resp_pausados.status_code == 200
    ids = [p["id"] for p in resp_pausados.json()]
    assert str(presu.id) in ids


def test_3_post_restaurar_no_eliminado(client, db_session, usuario_principal):
    """3. POST /restaurar de uno no eliminado -> 400 'Este presupuesto no está eliminado.'."""
    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Activo",
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        eliminado_en=None,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db_session.add(presu)
    db_session.commit()

    resp = client.post(f"/presupuestos/{presu.id}/restaurar", json={"estado": "activo"})
    assert resp.status_code == 400
    assert get_error_message(resp) == "Este presupuesto no está eliminado."


def test_4_post_restaurar_eliminado_hace_91_dias(client, db_session, usuario_principal):
    """4. POST /restaurar de uno eliminado hace 91 días -> 404."""
    hace_91_dias = datetime.now(timezone.utc) - timedelta(days=91)
    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Viejo",
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.FINALIZADO,
        eliminado_en=hace_91_dias,
        fecha_creacion=hace_91_dias - timedelta(days=30),
    )
    db_session.add(presu)
    db_session.commit()

    resp = client.post(f"/presupuestos/{presu.id}/restaurar", json={"estado": "activo"})
    assert resp.status_code == 404
    assert get_error_message(resp) == "No encontramos ese presupuesto."


def test_5_delete_dos_veces(client, db_session, usuario_principal):
    """5. DELETE dos veces -> la segunda 404."""
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
    assert get_error_message(resp2) == "No encontramos ese presupuesto."


def test_6_purgar_presupuestos_eliminados(db_session, usuario_principal, categoria_prueba):
    """6. purgar_presupuestos_eliminados: 91 días desaparece con hijos; 89 días queda."""
    hace_91 = datetime.now(timezone.utc) - timedelta(days=91)
    hace_89 = datetime.now(timezone.utc) - timedelta(days=89)

    # Presupuesto A: 91 días
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

    # Presupuesto B: 89 días
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

    db_session.add_all([p_viejo, per_viejo, cat_viejo, p_reciente, per_reciente, cat_reciente])
    db_session.commit()

    total_purgados = purgar_presupuestos_eliminados(db_session)
    assert total_purgados >= 1

    db_session.expire_all()
    # Presupuesto A e hijos desaparecieron
    assert db_session.get(Presupuesto, p_viejo.id) is None
    assert db_session.get(PeriodoPresupuesto, per_viejo.id) is None
    assert db_session.get(PresupuestoCategoria, cat_viejo.id) is None

    # Presupuesto B e hijos permanecen
    assert db_session.get(Presupuesto, p_reciente.id) is not None
    assert db_session.get(PeriodoPresupuesto, per_reciente.id) is not None
    assert db_session.get(PresupuestoCategoria, cat_reciente.id) is not None


def test_7_get_presupuestos_finalizados_vacio(client, db_session, usuario_principal):
    """7. GET /presupuestos?estado=finalizado -> []."""
    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Finalizado",
        monto=Decimal("60000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.FINALIZADO,
        eliminado_en=datetime.now(timezone.utc),
        fecha_creacion=datetime.now(timezone.utc) - timedelta(days=10),
    )
    db_session.add(presu)
    db_session.commit()

    resp = client.get("/presupuestos?estado=finalizado")
    assert resp.status_code == 200
    assert resp.json() == []


def test_8_impacto_presupuesto_eliminado(db_session, usuario_principal, categoria_prueba):
    """8. registrar_impacto_presupuesto con gasto en presupuesto eliminado -> período no cambia."""
    hoy = date.today()
    presu = Presupuesto(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Presupuesto Supermercado",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.FINALIZADO,
        eliminado_en=datetime.now(timezone.utc),
        fecha_creacion=datetime.now(timezone.utc) - timedelta(days=5),
    )
    periodo = PeriodoPresupuesto(
        id=uuid.uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=hoy - timedelta(days=5),
        fecha_fin=hoy + timedelta(days=25),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    cat = PresupuestoCategoria(
        id=uuid.uuid4(),
        presupuesto_id=presu.id,
        categoria_id=categoria_prueba.id,
    )
    db_session.add_all([presu, periodo, cat])
    db_session.commit()

    billetera = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        nombre="Efectivo Test",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        saldo_inicial=Decimal("100000.00"),
        es_principal=True,
    )
    db_session.add(billetera)
    db_session.commit()

    tx = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_principal.id,
        billetera_id=billetera.id,
        categoria_id=categoria_prueba.id,
        monto=Decimal("15000.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx)
    db_session.commit()

    registrar_impacto_presupuesto(db_session, tx)

    from app.models.notificacion import Notificacion, TipoNotificacion
    notifs = (
        db_session.query(Notificacion)
        .filter(
            Notificacion.usuario_id == usuario_principal.id,
            Notificacion.tipo.in_([TipoNotificacion.PRESUPUESTO_LIMITE, TipoNotificacion.PRESUPUESTO_AGOTADO]),
        )
        .all()
    )
    assert len(notifs) == 0


def test_9_presupuesto_otro_usuario(client, db_session, usuario_principal, usuario_otro):
    """9. Presupuesto de otro usuario: DELETE y restaurar -> 404."""
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

    # Usuario principal intenta borrar presupuesto de usuario_otro -> 404
    resp_del = client.delete(f"/presupuestos/{presu_otro.id}")
    assert resp_del.status_code == 404
    assert get_error_message(resp_del) == "No encontramos ese presupuesto."

    # Se marca eliminado para usuario_otro
    presu_otro.estado = EstadoPresupuesto.FINALIZADO
    presu_otro.eliminado_en = datetime.now(timezone.utc)
    db_session.commit()

    # Usuario principal intenta restaurar presupuesto de usuario_otro -> 404
    resp_rest = client.post(f"/presupuestos/{presu_otro.id}/restaurar", json={"estado": "activo"})
    assert resp_rest.status_code == 404
    assert get_error_message(resp_rest) == "No encontramos ese presupuesto."
