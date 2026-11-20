# -*- coding: utf-8 -*-
"""
Pruebas del backend para fase_j3b2_panel_admin: estado_visible, conteos_estado,
movimientos_mes, stats ampliados y controles estáticos.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

if (
    not hasattr(PGUUID, "_compiler_dispatcher")
    or "sqlite" not in PGUUID._compiler_dispatcher.specs
):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if (
    not hasattr(JSONB, "_compiler_dispatcher")
    or "sqlite" not in JSONB._compiler_dispatcher.specs
):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"

import app.models  # noqa: F401
from app.core.auth import get_current_admin_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp
from app.models.evento_uso import EventoUso
from app.models.transaccion import OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import admin_uso_service
from app.utils.fecha import TZ_ARGENTINA

AHORA = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
HOY = date(2026, 11, 20)

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_sqlite(monkeypatch):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSessionLocal)

    def _override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setitem(app.dependency_overrides, get_db, _override_db)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _admin_memoria() -> Usuario:
    return Usuario(
        id=uuid4(),
        nombre="Admin",
        apellido="Test",
        email="admin.mem@prueba.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.ADMIN,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        is_admin=True,
        onboarding_completo=True,
    )


def _crear_usuario(
    db: Session,
    *,
    nombre: str = "Usuario",
    apellido: str = "Prueba",
    email: str | None = None,
    estado: EstadoUsuario = EstadoUsuario.ACTIVO,
    onboarding_completo: bool = True,
    ultimo_acceso: datetime | None = None,
    fecha_registro: datetime | None = None,
    telefono_verificado: bool = False,
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        nombre=nombre,
        apellido=apellido,
        email=email or f"u_{uuid4().hex[:8]}@prueba.com",
        telefono=f"+54911{uuid4().int % 100000000:08d}" if telefono_verificado else None,
        telefono_normalizado=f"+54911{uuid4().int % 100000000:08d}" if telefono_verificado else None,
        telefono_verificado=telefono_verificado,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=estado,
        moneda_principal=Moneda.ARS,
        is_admin=False,
        ultimo_acceso=ultimo_acceso,
        fecha_registro=fecha_registro or (AHORA - timedelta(days=60)),
        onboarding_completo=onboarding_completo,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _crear_billetera(db: Session, usuario_id) -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre="Principal",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("0"),
        saldo_inicial=Decimal("0"),
        es_principal=True,
    )
    db.add(b)
    db.commit()
    return b


def _crear_tx(
    db: Session,
    usuario_id,
    billetera_id,
    *,
    origen: OrigenTransaccion = OrigenTransaccion.MANUAL,
    fecha_creacion: datetime,
    es_cuota_hija: bool = False,
    tipo_espejo: str | None = None,
    reintegro_de_id=None,
) -> Transaccion:
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=fecha_creacion.date(),
        descripcion="Prueba",
        billetera_id=billetera_id,
        origen=origen,
        es_cuota_hija=es_cuota_hija,
        tipo_espejo=tipo_espejo,
        reintegro_de_id=reintegro_de_id,
        fecha_creacion=fecha_creacion,
    )
    db.add(tx)
    db.commit()
    return tx


def test_e1_estado_visible_reglas(db_session: Session):
    hace_60d = AHORA - timedelta(days=60)

    u_susp = _crear_usuario(db_session, estado=EstadoUsuario.INACTIVO, fecha_registro=hace_60d)
    assert admin_uso_service.estado_visible(u_susp, AHORA - timedelta(hours=1), AHORA) == "suspendida"

    u_sin_ver = _crear_usuario(
        db_session,
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        onboarding_completo=False,
        fecha_registro=hace_60d,
    )
    assert admin_uso_service.estado_visible(u_sin_ver, None, AHORA) == "sin_verificar"

    u_sin_emp = _crear_usuario(
        db_session,
        estado=EstadoUsuario.ACTIVO,
        onboarding_completo=False,
        fecha_registro=AHORA - timedelta(days=2),
    )
    assert admin_uso_service.estado_visible(u_sin_emp, None, AHORA) == "sin_empezar"

    u_nuevo_3d = _crear_usuario(db_session, fecha_registro=AHORA - timedelta(days=3))
    assert admin_uso_service.estado_visible(u_nuevo_3d, None, AHORA) == "nuevo"

    u_nuevo_7d = _crear_usuario(db_session, fecha_registro=AHORA - timedelta(days=7))
    assert admin_uso_service.estado_visible(u_nuevo_7d, None, AHORA) == "nuevo"

    u_base = _crear_usuario(db_session, fecha_registro=hace_60d)
    assert admin_uso_service.estado_visible(u_base, AHORA - timedelta(days=7), AHORA) == "activo"
    assert (
        admin_uso_service.estado_visible(u_base, AHORA - timedelta(days=7, seconds=1), AHORA)
        == "poco_uso"
    )
    assert admin_uso_service.estado_visible(u_base, AHORA - timedelta(days=30), AHORA) == "poco_uso"
    assert (
        admin_uso_service.estado_visible(u_base, AHORA - timedelta(days=30, seconds=1), AHORA)
        == "en_desuso"
    )
    assert admin_uso_service.estado_visible(u_base, None, AHORA) == "en_desuso"


def test_e2_e3_e4_listar_usuarios_estados_conteos_y_filtros(
    db_session: Session, client: TestClient, monkeypatch
):
    admin = _admin_memoria()
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    monkeypatch.setattr("app.routers.admin.ahora_utc", lambda: AHORA)
    monkeypatch.setattr("app.routers.admin.hoy_argentina", lambda: HOY)

    # 10 usuarios: todos los casos de estado_visible + activo WPP + activo visita web + permitirse_wpp solo
    u1_susp = _crear_usuario(
        db_session, nombre="BuscarAlpha", estado=EstadoUsuario.INACTIVO, ultimo_acceso=AHORA - timedelta(hours=1)
    )
    u2_sin_ver = _crear_usuario(
        db_session,
        nombre="BuscarBeta",
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        onboarding_completo=False,
    )
    u3_sin_emp = _crear_usuario(db_session, nombre="Tres", onboarding_completo=False)
    u4_nuevo = _crear_usuario(db_session, nombre="Cuatro", fecha_registro=AHORA - timedelta(days=3))
    u5_act_7d = _crear_usuario(db_session, nombre="Cinco", ultimo_acceso=AHORA - timedelta(days=7))
    u6_act_wpp = _crear_usuario(db_session, nombre="Seis", telefono_verificado=True)
    db_session.add(
        ConversacionWpp(
            usuario_id=u6_act_wpp.id,
            mensaje_usuario="hola",
            mensaje_bot="hola",
            fecha=AHORA - timedelta(hours=2),
        )
    )
    u7_act_visita = _crear_usuario(db_session, nombre="Siete")
    db_session.add(
        EventoUso(
            usuario_id=u7_act_visita.id,
            evento="visita",
            dia=HOY - timedelta(days=1),
            fecha=AHORA - timedelta(days=1),
        )
    )
    u8_poco_7d1s = _crear_usuario(
        db_session, nombre="Ocho", ultimo_acceso=AHORA - timedelta(days=7, seconds=1)
    )
    u9_poco_30d = _crear_usuario(db_session, nombre="Nueve", ultimo_acceso=AHORA - timedelta(days=30))
    u10_desuso = _crear_usuario(db_session, nombre="Diez")
    # Un evento permitirse_wpp solo no cuenta como uso web
    db_session.add(
        EventoUso(
            usuario_id=u10_desuso.id,
            evento="permitirse_wpp",
            dia=HOY,
            fecha=AHORA - timedelta(hours=1),
        )
    )
    db_session.commit()

    usuarios_todos = [
        u1_susp, u2_sin_ver, u3_sin_emp, u4_nuevo, u5_act_7d,
        u6_act_wpp, u7_act_visita, u8_poco_7d1s, u9_poco_30d, u10_desuso,
    ]
    usos_py = admin_uso_service.ultimos_usos(db_session, [u.id for u in usuarios_todos])
    esperado_por_usuario = {
        str(u.id): admin_uso_service.estado_visible(u, usos_py[u.id]["ultimo_uso"], AHORA)
        for u in usuarios_todos
    }
    assert esperado_por_usuario[str(u10_desuso.id)] == "en_desuso"
    assert usos_py[u10_desuso.id]["ultimo_uso_web"] is None

    # E2: filtro por cada uno de los 7 estados
    for est in admin_uso_service.ESTADOS_VISIBLES:
        resp = client.get(f"/v1/admin/usuarios?estado={est}")
        assert resp.status_code == status.HTTP_200_OK
        items = resp.json()["data"]["usuarios"]
        ids_resp = {it["id"] for it in items}
        ids_esp = {uid for uid, ev in esperado_por_usuario.items() if ev == est}
        assert ids_resp == ids_esp
        for it in items:
            assert it["estado_visible"] == est
            assert it["estado_visible"] == esperado_por_usuario[it["id"]]

    # E3: conteos_estado sin filtro y con search
    resp_all = client.get("/v1/admin/usuarios")
    assert resp_all.status_code == 200
    conteos = resp_all.json()["data"]["conteos_estado"]
    assert conteos == {
        "todos": 10,
        "activo": 3,
        "nuevo": 1,
        "poco_uso": 2,
        "en_desuso": 1,
        "sin_empezar": 1,
        "suspendida": 1,
        "sin_verificar": 1,
    }

    resp_search = client.get("/v1/admin/usuarios?search=Buscar")
    assert resp_search.status_code == 200
    conteos_s = resp_search.json()["data"]["conteos_estado"]
    assert conteos_s["todos"] == 2
    assert conteos_s["suspendida"] == 1
    assert conteos_s["sin_verificar"] == 1

    # conteos_estado no mira el filtro de estado activo
    resp_est_act = client.get("/v1/admin/usuarios?estado=activo")
    assert resp_est_act.status_code == 200
    assert resp_est_act.json()["data"]["conteos_estado"] == conteos

    # E4: estado=bloqueado -> 422; wpp y onboarding ya no filtran
    assert client.get("/v1/admin/usuarios?estado=bloqueado").status_code == 422
    resp_extra = client.get("/v1/admin/usuarios?wpp=vinculado&onboarding=completo")
    assert resp_extra.status_code == 200
    assert resp_extra.json()["data"] == resp_all.json()["data"]


def test_mv1_mv2_movimientos_mes_y_orden(db_session: Session, client: TestClient, monkeypatch):
    admin = _admin_memoria()
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    monkeypatch.setattr("app.routers.admin.ahora_utc", lambda: AHORA)
    monkeypatch.setattr("app.routers.admin.hoy_argentina", lambda: HOY)

    u_a = _crear_usuario(db_session, nombre="A", fecha_registro=AHORA - timedelta(days=30))
    u_b = _crear_usuario(db_session, nombre="B", fecha_registro=AHORA - timedelta(days=10))
    u_c = _crear_usuario(db_session, nombre="C", fecha_registro=AHORA - timedelta(days=25))
    u_d = _crear_usuario(db_session, nombre="D", fecha_registro=AHORA - timedelta(days=20))

    b_a = _crear_billetera(db_session, u_a.id)
    b_b = _crear_billetera(db_session, u_b.id)
    b_c = _crear_billetera(db_session, u_c.id)

    f_mes = datetime(2026, 11, 10, 12, 0, tzinfo=TZ_ARGENTINA)
    # A: 2 MANUAL + 1 IA_WPP + 1 IA_PDF = 4
    _crear_tx(db_session, u_a.id, b_a.id, origen=OrigenTransaccion.MANUAL, fecha_creacion=f_mes)
    _crear_tx(db_session, u_a.id, b_a.id, origen=OrigenTransaccion.MANUAL, fecha_creacion=f_mes)
    _crear_tx(db_session, u_a.id, b_a.id, origen=OrigenTransaccion.IA_WPP, fecha_creacion=f_mes)
    _crear_tx(db_session, u_a.id, b_a.id, origen=OrigenTransaccion.IA_PDF, fecha_creacion=f_mes)

    # B: 1 RECURRENTE (también sirve de padre para espejo), 1 cuota hija, 1 tipo_espejo "reintegro" -> 0
    tx_padre_b = _crear_tx(
        db_session, u_b.id, b_b.id, origen=OrigenTransaccion.RECURRENTE, fecha_creacion=f_mes
    )
    _crear_tx(
        db_session, u_b.id, b_b.id, origen=OrigenTransaccion.MANUAL, fecha_creacion=f_mes, es_cuota_hija=True
    )
    _crear_tx(
        db_session,
        u_b.id,
        b_b.id,
        origen=OrigenTransaccion.MANUAL,
        fecha_creacion=f_mes,
        tipo_espejo="reintegro",
        reintegro_de_id=tx_padre_b.id,
    )

    # C: 1 MANUAL 31/10 23:00 AR (no cuenta) + 1 MANUAL 01/11 00:30 AR (cuenta) -> 1
    _crear_tx(
        db_session,
        u_c.id,
        b_c.id,
        origen=OrigenTransaccion.MANUAL,
        fecha_creacion=datetime(2026, 10, 31, 23, 0, tzinfo=TZ_ARGENTINA),
    )
    _crear_tx(
        db_session,
        u_c.id,
        b_c.id,
        origen=OrigenTransaccion.MANUAL,
        fecha_creacion=datetime(2026, 11, 1, 0, 30, tzinfo=TZ_ARGENTINA),
    )

    conteos = admin_uso_service.movimientos_mes(db_session, [u_a.id, u_b.id, u_c.id, u_d.id], HOY)
    assert conteos == {
        u_a.id: 4,
        u_b.id: 0,
        u_c.id: 1,
        u_d.id: 0,
    }

    resp = client.get("/v1/admin/usuarios?orden=movimientos")
    assert resp.status_code == 200
    items = resp.json()["data"]["usuarios"]
    assert [it["id"] for it in items] == [str(u_a.id), str(u_c.id), str(u_b.id), str(u_d.id)]
    assert [it["movimientos_mes"] for it in items] == [4, 1, 0, 0]

    det = client.get(f"/v1/admin/usuarios/{u_a.id}")
    assert det.status_code == 200
    assert det.json()["data"]["movimientos_mes"] == 4


def test_s1_stats_ampliados(db_session: Session, client: TestClient, monkeypatch):
    admin = _admin_memoria()
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    monkeypatch.setattr("app.routers.admin.ahora_utc", lambda: AHORA)
    monkeypatch.setattr("app.routers.admin.hoy_argentina", lambda: HOY)
    monkeypatch.setattr("app.core.config.settings.OPENAI_ADMIN_KEY", "")

    # 2 activos, 1 en_desuso, 1 nuevo (que también usó la app)
    _crear_usuario(db_session, ultimo_acceso=AHORA - timedelta(days=1))
    _crear_usuario(db_session, ultimo_acceso=AHORA - timedelta(days=3))
    _crear_usuario(db_session, ultimo_acceso=AHORA - timedelta(days=40))
    _crear_usuario(
        db_session,
        fecha_registro=AHORA - timedelta(days=2),
        ultimo_acceso=AHORA - timedelta(hours=5),
    )

    resp = client.get("/v1/admin/stats")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 4
    assert data["usaron_7_dias"] == 3
    assert data["activos_pct"] == 75
    assert data["en_desuso"] == 1
    assert data["costo_mes_fuente"] == "estimado"
    for k in (
        "activos",
        "onboarding_completo",
        "whatsapp_vinculados",
        "nuevos_hoy",
        "nuevos_7_dias",
        "activos_7_dias",
        "costo_mes_ars",
        "costo_mes_sin_dolar",
        "admins_total",
        "por_proveedor",
    ):
        assert k in data


def test_s2_estaticos_j3b2():
    raiz = Path(__file__).resolve().parents[1]
    app_dir = raiz / "app"

    uso_txt = (app_dir / "services/admin_uso_service.py").read_text(encoding="utf-8")
    assert "greatest(" not in uso_txt.lower()
    assert "permitirse_wpp" not in uso_txt
    assert "cuotas_contado_wpp" not in uso_txt

    archivos_key = [
        p.relative_to(raiz).as_posix()
        for p in sorted(app_dir.rglob("*.py"))
        if "OPENAI_ADMIN_KEY" in p.read_text(encoding="utf-8")
    ]
    assert archivos_key == [
        "app/core/config.py",
        "app/services/openai_costos_service.py",
    ]
