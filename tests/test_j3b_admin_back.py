# -*- coding: utf-8 -*-
"""
Pruebas del backend para fase_j3b_panel_admin (costos por usuario, último uso real,
registro de acciones, eliminación de resetear-onboarding, monitor WPP y controles estáticos).
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

if not hasattr(PGUUID, "_compiler_dispatcher") or "sqlite" not in getattr(PGUUID._compiler_dispatcher, "specs", {}):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if not hasattr(JSONB, "_compiler_dispatcher") or "sqlite" not in getattr(JSONB._compiler_dispatcher, "specs", {}):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"

import app.main as app_main
import app.models  # noqa: F401
from app.core.auth import get_current_admin_user, get_current_user
from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.main import app
from app.models.accion_admin import AccionAdmin
from app.models.conversacion_wpp import ConversacionWpp
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.evento_uso import EventoUso
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado
from app.models.uso_ia import UsoIA
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas import admin as admin_schemas
from app.services import (
    admin_registro_service,
    admin_service,
    admin_uso_service,
    cola_wpp_service,
    costos_service,
    monitor_wpp_service,
)
from app.utils.fecha import TZ_ARGENTINA

AHORA = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
HOY = date(2026, 11, 9)

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


def _crear_usuario(
    db: Session,
    *,
    nombre: str = "Usuario",
    apellido: str = "Prueba",
    email: str | None = None,
    is_admin: bool = False,
    estado: EstadoUsuario = EstadoUsuario.ACTIVO,
    ultimo_acceso: datetime | None = None,
    fecha_registro: datetime | None = None,
    telefono_verificado: bool = False,
    password: str | None = None,
    auth_provider: AuthProvider = AuthProvider.EMAIL,
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        nombre=nombre,
        apellido=apellido,
        email=email or f"u_{uuid4().hex[:8]}@prueba.com",
        telefono=f"+54911{uuid4().int % 100000000:08d}" if telefono_verificado else None,
        telefono_normalizado=f"+54911{uuid4().int % 100000000:08d}" if telefono_verificado else None,
        telefono_verificado=telefono_verificado,
        auth_provider=auth_provider,
        rol=RolUsuario.ADMIN if is_admin else RolUsuario.USUARIO,
        estado=estado,
        moneda_principal=Moneda.ARS,
        is_admin=is_admin,
        ultimo_acceso=ultimo_acceso,
        fecha_registro=fecha_registro or AHORA - timedelta(days=30),
        password_hash=get_password_hash(password) if password else None,
        password_configurada=bool(password),
        onboarding_completo=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _agregar_dolar_oficial(db: Session, fecha_cot: date, venta: Decimal) -> CotizacionDolar:
    cot = CotizacionDolar(
        id=uuid4(),
        fecha=fecha_cot,
        tipo="oficial",
        compra=venta - Decimal("50"),
        venta=venta,
        promedio=venta - Decimal("25"),
    )
    db.add(cot)
    db.commit()
    return cot


def _agregar_uso_ia(db: Session, usuario_id, costo_usd: Decimal, fecha: datetime) -> None:
    db.add(
        UsoIA(
            id=uuid4(),
            usuario_id=usuario_id,
            funcion="texto_whatsapp",
            modelo="gpt-6-luna",
            tokens_entrada=100,
            tokens_salida=20,
            tokens_entrada_cache=0,
            costo_usd=costo_usd,
            fecha=fecha,
        )
    )
    db.commit()


def _agregar_saliente(
    db: Session,
    usuario_id,
    fecha: datetime,
    *,
    enviado: bool = True,
    tipo_cobro: str = "regular",
) -> None:
    db.add(
        MensajeSalienteWpp(
            id=uuid4(),
            usuario_id=usuario_id,
            tipo="respuesta",
            es_plantilla=False,
            partes=1,
            enviado=enviado,
            tipo_cobro=tipo_cobro,
            fecha=fecha,
        )
    )
    db.commit()


def test_c1_costo_mes_por_usuario(db_session: Session):
    u_a = _crear_usuario(db_session, nombre="Ana")
    u_b = _crear_usuario(db_session, nombre="Beto")
    cot = _agregar_dolar_oficial(db_session, date(2026, 11, 6), Decimal("1450"))

    # IA de A este mes (0,25 + 0,15) y del mes pasado (9,99 que no cuenta)
    _agregar_uso_ia(db_session, u_a.id, Decimal("0.25"), datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc))
    _agregar_uso_ia(db_session, u_a.id, Decimal("0.15"), datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc))
    _agregar_uso_ia(db_session, u_a.id, Decimal("9.99"), datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc))

    # 20 cobrados este mes + 3 gratis + 2 cobrados del mes pasado + 1 no enviado
    f_mes = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
    f_ant = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
    for _ in range(20):
        _agregar_saliente(db_session, u_a.id, f_mes, enviado=True, tipo_cobro="regular")
    for _ in range(3):
        _agregar_saliente(db_session, u_a.id, f_mes, enviado=True, tipo_cobro="free_entry")
    for _ in range(2):
        _agregar_saliente(db_session, u_a.id, f_ant, enviado=True, tipo_cobro="regular")
    _agregar_saliente(db_session, u_a.id, f_mes, enviado=False, tipo_cobro="regular")

    res = costos_service.costo_mes_por_usuario(db_session, HOY, usuario_ids=[u_a.id, u_b.id])
    assert res[u_a.id] == costos_service.CostoUsuarioMes(
        ia_usd=Decimal("0.40"),
        ia_ars=580,
        whatsapp_cobrados=20,
        whatsapp_ars=754,
        total_ars=1334,
        dolar_oficial=Decimal("1450.0000"),
        dolar_fecha=date(2026, 11, 6),
        sin_dolar=False,
    )
    assert res[u_b.id] == costos_service.CostoUsuarioMes(
        ia_usd=Decimal("0.00"),
        ia_ars=0,
        whatsapp_cobrados=0,
        whatsapp_ars=0,
        total_ars=0,
        dolar_oficial=Decimal("1450.0000"),
        dolar_fecha=date(2026, 11, 6),
        sin_dolar=False,
    )

    # Sin cotización oficial
    db_session.delete(cot)
    db_session.commit()

    res_sin = costos_service.costo_mes_por_usuario(db_session, HOY, usuario_ids=[u_a.id, u_b.id])
    assert res_sin[u_a.id].ia_ars is None
    assert res_sin[u_a.id].total_ars == 754
    assert res_sin[u_a.id].sin_dolar is True
    assert res_sin[u_a.id].dolar_oficial is None
    assert res_sin[u_a.id].dolar_fecha is None


def test_c2_redondeo_costos(db_session: Session):
    _agregar_dolar_oficial(db_session, date(2026, 11, 9), Decimal("1450"))
    f = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)

    u1 = _crear_usuario(db_session)
    _agregar_uso_ia(db_session, u1.id, Decimal("0.001"), f)

    u2 = _crear_usuario(db_session)
    _agregar_uso_ia(db_session, u2.id, Decimal("0.0005"), f)

    u3 = _crear_usuario(db_session)
    _agregar_uso_ia(db_session, u3.id, Decimal("0.0003"), f)

    u4 = _crear_usuario(db_session)
    _agregar_saliente(db_session, u4.id, f)

    u5 = _crear_usuario(db_session)
    _agregar_uso_ia(db_session, u5.id, Decimal("0.0003"), f)
    _agregar_saliente(db_session, u5.id, f)

    u6 = _crear_usuario(db_session)
    _agregar_uso_ia(db_session, u6.id, Decimal("0.0005"), f)
    _agregar_saliente(db_session, u6.id, f)

    res = costos_service.costo_mes_por_usuario(
        db_session, HOY, usuario_ids=[u1.id, u2.id, u3.id, u4.id, u5.id, u6.id]
    )
    assert (res[u1.id].ia_ars, res[u1.id].total_ars) == (1, 1)
    assert (res[u2.id].ia_ars, res[u2.id].total_ars) == (1, 1)
    assert (res[u3.id].ia_ars, res[u3.id].total_ars) == (0, 0)
    assert (res[u4.id].whatsapp_ars, res[u4.id].total_ars) == (38, 38)
    assert (res[u5.id].ia_ars, res[u5.id].whatsapp_ars, res[u5.id].total_ars) == (0, 38, 38)
    assert (res[u6.id].ia_ars, res[u6.id].whatsapp_ars, res[u6.id].total_ars) == (1, 38, 38)


def test_c3_costo_mes_total_ars(db_session: Session):
    u_a = _crear_usuario(db_session)
    cot = _agregar_dolar_oficial(db_session, date(2026, 11, 6), Decimal("1450"))
    f_mes = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)
    f_ant = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)

    _agregar_uso_ia(db_session, u_a.id, Decimal("0.25"), f_mes)
    _agregar_uso_ia(db_session, u_a.id, Decimal("0.15"), f_mes)
    _agregar_uso_ia(db_session, u_a.id, Decimal("9.99"), f_ant)
    _agregar_uso_ia(db_session, None, Decimal("0.10"), f_mes)

    for _ in range(20):
        _agregar_saliente(db_session, u_a.id, f_mes, enviado=True, tipo_cobro="regular")
    for _ in range(3):
        _agregar_saliente(db_session, u_a.id, f_mes, enviado=True, tipo_cobro="free_entry")
    for _ in range(2):
        _agregar_saliente(db_session, u_a.id, f_ant, enviado=True, tipo_cobro="regular")
    _agregar_saliente(db_session, u_a.id, f_mes, enviado=False, tipo_cobro="regular")

    assert costos_service.costo_mes_total_ars(db_session, HOY) == (1479, False)

    db_session.delete(cot)
    db_session.commit()
    assert costos_service.costo_mes_total_ars(db_session, HOY) == (754, True)


def test_c4_orden_costo_mes(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(
        db_session,
        nombre="Admin",
        is_admin=True,
        fecha_registro=AHORA - timedelta(days=90),
        password="ClaveAdmin123",
    )
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    monkeypatch.setattr("app.routers.admin.hoy_argentina", lambda: HOY)

    _agregar_dolar_oficial(db_session, date(2026, 11, 6), Decimal("1450"))
    u_a = _crear_usuario(db_session, nombre="A", fecha_registro=AHORA - timedelta(days=10))
    u_b = _crear_usuario(db_session, nombre="B", fecha_registro=AHORA - timedelta(days=5))
    u_c = _crear_usuario(db_session, nombre="C", fecha_registro=AHORA - timedelta(days=2))

    f_mes = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)
    _agregar_uso_ia(db_session, u_a.id, Decimal("0.40"), f_mes)
    for _ in range(20):
        _agregar_saliente(db_session, u_a.id, f_mes)
    for _ in range(30):
        _agregar_saliente(db_session, u_b.id, f_mes)

    resp = client.get("/v1/admin/usuarios?orden=costo_mes")
    assert resp.status_code == status.HTTP_200_OK
    items = resp.json()["data"]["usuarios"]
    ids_orden = [it["id"] for it in items]
    assert ids_orden[:3] == [str(u_a.id), str(u_b.id), str(u_c.id)]

    esperados = costos_service.costo_mes_por_usuario(db_session, HOY, [u_a.id, u_b.id, u_c.id])
    for it in items[:3]:
        assert it["costo_mes_ars"] == esperados[u_a.id if it["id"] == str(u_a.id) else (u_b.id if it["id"] == str(u_b.id) else u_c.id)].total_ars


def test_u1_ultimos_usos(db_session: Session):
    assert admin_uso_service.EVENTOS_WHATSAPP == ("permitirse_wpp", "cuotas_contado_wpp")

    u_a = _crear_usuario(db_session, ultimo_acceso=AHORA - timedelta(days=5))
    db_session.add(EventoUso(usuario_id=u_a.id, evento="visita", dia=HOY, fecha=AHORA - timedelta(hours=2)))

    u_b = _crear_usuario(db_session, ultimo_acceso=None)
    db_session.add(ConversacionWpp(usuario_id=u_b.id, mensaje_usuario="hola", mensaje_bot="hola", fecha=AHORA - timedelta(hours=1)))

    u_c = _crear_usuario(db_session, ultimo_acceso=None)

    u_d = _crear_usuario(db_session, ultimo_acceso=AHORA - timedelta(hours=3))
    db_session.add(ConversacionWpp(usuario_id=u_d.id, mensaje_usuario="hola", mensaje_bot="hola", fecha=AHORA - timedelta(hours=3)))

    u_e = _crear_usuario(db_session, ultimo_acceso=AHORA - timedelta(days=4))
    db_session.add(EventoUso(usuario_id=u_e.id, evento="permitirse_wpp", dia=HOY, fecha=AHORA - timedelta(minutes=30)))
    db_session.add(ConversacionWpp(usuario_id=u_e.id, mensaje_usuario="hola", mensaje_bot="hola", fecha=AHORA - timedelta(minutes=30)))
    db_session.commit()

    res = admin_uso_service.ultimos_usos(db_session, [u_a.id, u_b.id, u_c.id, u_d.id, u_e.id])
    assert res[u_a.id] == {
        "ultimo_uso": AHORA - timedelta(hours=2),
        "ultimo_uso_canal": "web",
        "ultimo_uso_web": AHORA - timedelta(hours=2),
        "ultimo_uso_whatsapp": None,
    }
    assert res[u_b.id] == {
        "ultimo_uso": AHORA - timedelta(hours=1),
        "ultimo_uso_canal": "whatsapp",
        "ultimo_uso_web": None,
        "ultimo_uso_whatsapp": AHORA - timedelta(hours=1),
    }
    assert res[u_c.id] == {
        "ultimo_uso": None,
        "ultimo_uso_canal": None,
        "ultimo_uso_web": None,
        "ultimo_uso_whatsapp": None,
    }
    assert res[u_d.id]["ultimo_uso_canal"] == "whatsapp"
    assert res[u_d.id]["ultimo_uso"] == AHORA - timedelta(hours=3)
    assert res[u_e.id] == {
        "ultimo_uso": AHORA - timedelta(minutes=30),
        "ultimo_uso_canal": "whatsapp",
        "ultimo_uso_web": AHORA - timedelta(days=4),
        "ultimo_uso_whatsapp": AHORA - timedelta(minutes=30),
    }


def test_u2_listar_usuarios_orden_ultimo_uso_y_registro(db_session: Session, client: TestClient, monkeypatch):
    ahora = datetime.now(timezone.utc)
    admin = _crear_usuario(
        db_session,
        nombre="Admin",
        is_admin=True,
        fecha_registro=ahora - timedelta(days=50),
        password="ClaveAdmin123",
    )
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)

    u_b = _crear_usuario(db_session, nombre="B", fecha_registro=ahora - timedelta(days=20))
    db_session.add(ConversacionWpp(usuario_id=u_b.id, mensaje_usuario="x", mensaje_bot="y", fecha=ahora - timedelta(hours=1)))

    u_a = _crear_usuario(db_session, nombre="A", fecha_registro=ahora - timedelta(days=15))
    db_session.add(EventoUso(usuario_id=u_a.id, evento="visita", dia=ahora.date(), fecha=ahora - timedelta(hours=2)))

    u_d = _crear_usuario(db_session, nombre="D", ultimo_acceso=ahora - timedelta(hours=3), fecha_registro=ahora - timedelta(days=10))
    u_c = _crear_usuario(db_session, nombre="C", fecha_registro=ahora - timedelta(days=3))
    u_e = _crear_usuario(db_session, nombre="E", fecha_registro=ahora - timedelta(days=1))
    db_session.add(EventoUso(usuario_id=u_e.id, evento="cuotas_contado_wpp", dia=ahora.date(), fecha=ahora - timedelta(minutes=10)))
    db_session.commit()

    resp = client.get("/v1/admin/usuarios?orden=ultimo_uso")
    assert resp.status_code == status.HTTP_200_OK
    usuarios = resp.json()["data"]["usuarios"]
    assert [u["id"] for u in usuarios] == [
        str(u_b.id),
        str(u_a.id),
        str(u_d.id),
        str(u_e.id),
        str(u_c.id),
        str(admin.id),
    ]
    for item in usuarios:
        assert "estado_cuenta" in item
        assert "ultimo_uso" in item
        assert "ultimo_uso_canal" in item
        assert isinstance(item["costo_mes_ars"], int)

    resp_reg = client.get("/v1/admin/usuarios?orden=registro")
    assert resp_reg.status_code == status.HTTP_200_OK
    ids_reg = [u["id"] for u in resp_reg.json()["data"]["usuarios"]]
    assert ids_reg == [str(u_e.id), str(u_c.id), str(u_d.id), str(u_a.id), str(u_b.id), str(admin.id)]


def test_u3_filtro_estado_sin_verificar_y_estado_cuenta(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(db_session, is_admin=True, password="ClaveAdmin123")
    u_act = _crear_usuario(db_session, estado=EstadoUsuario.ACTIVO)
    u_susp = _crear_usuario(db_session, estado=EstadoUsuario.INACTIVO)
    u_pend = _crear_usuario(db_session, estado=EstadoUsuario.PENDIENTE_VERIFICACION)
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)

    assert admin_uso_service.estado_cuenta(u_act) == "activa"
    assert admin_uso_service.estado_cuenta(u_susp) == "suspendida"
    assert admin_uso_service.estado_cuenta(u_pend) == "sin_verificar"

    r_ok = client.get("/v1/admin/usuarios?estado=sin_verificar")
    assert r_ok.status_code == status.HTTP_200_OK
    lista = r_ok.json()["data"]["usuarios"]
    assert [u["id"] for u in lista] == [str(u_pend.id)]
    assert lista[0]["estado_cuenta"] == "sin_verificar"

    r_bad = client.get("/v1/admin/usuarios?estado=bloqueado")
    assert r_bad.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_u4_detalle_usuario(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(db_session, is_admin=True, password="ClaveAdmin123")
    u_pass = _crear_usuario(db_session, password="UserPass123", ultimo_acceso=datetime.now(timezone.utc) - timedelta(hours=2))
    u_google = _crear_usuario(db_session, auth_provider=AuthProvider.GOOGLE, password=None)
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)

    r1 = client.get(f"/v1/admin/usuarios/{u_pass.id}")
    assert r1.status_code == status.HTTP_200_OK
    d1 = r1.json()["data"]
    assert d1["tiene_contrasena"] is True
    assert d1["ultimo_uso_web"] is not None
    assert d1["ultimo_uso_whatsapp"] is None
    assert set(d1["costo_mes"].keys()) == {
        "ia_usd",
        "ia_ars",
        "whatsapp_cobrados",
        "whatsapp_ars",
        "total_ars",
        "dolar_oficial",
        "dolar_fecha",
        "sin_dolar",
    }

    r2 = client.get(f"/v1/admin/usuarios/{u_google.id}")
    assert r2.status_code == status.HTTP_200_OK
    assert r2.json()["data"]["tiene_contrasena"] is False


def test_u5_stats_usaron_7_dias_y_costo_mes(db_session: Session, client: TestClient, monkeypatch):
    ahora = datetime.now(timezone.utc)
    admin = _crear_usuario(db_session, is_admin=True, password="ClaveAdmin123")
    u_web = _crear_usuario(db_session, ultimo_acceso=ahora - timedelta(days=2))
    u_wpp = _crear_usuario(db_session)
    db_session.add(ConversacionWpp(usuario_id=u_wpp.id, mensaje_usuario="a", mensaje_bot="b", fecha=ahora - timedelta(days=6)))
    _crear_usuario(db_session, ultimo_acceso=ahora - timedelta(days=8))
    db_session.commit()
    assert u_web.id != u_wpp.id

    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    resp = client.get("/v1/admin/stats")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["data"]
    assert data["total"] == 4
    assert data["usaron_7_dias"] == 2
    assert isinstance(data["costo_mes_ars"], int)
    assert isinstance(data["costo_mes_sin_dolar"], bool)
    for k in ("activos", "onboarding_completo", "whatsapp_vinculados", "nuevos_hoy", "nuevos_7_dias", "activos_7_dias", "admins_total", "por_proveedor"):
        assert k in data


def test_r1_registro_cada_accion(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(
        db_session,
        nombre="Admin",
        apellido="Tester",
        is_admin=True,
        password="ClaveAdmin123",
    )
    user = _crear_usuario(
        db_session,
        nombre="Regular",
        apellido="User",
        telefono_verificado=True,
        password="UserPass123",
    )
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    monkeypatch.setattr("app.services.admin_service.enviar_reset_password_email", lambda *a, **kw: True)

    assert client.patch(f"/v1/admin/usuarios/{user.id}/estado", json={"is_active": False}).status_code == 200
    assert client.patch(f"/v1/admin/usuarios/{user.id}/estado", json={"is_active": True}).status_code == 200
    assert client.post(f"/v1/admin/usuarios/{user.id}/reset-password").status_code == 200
    assert client.post(f"/v1/admin/usuarios/{user.id}/revocar-sesiones").status_code == 200
    assert client.post(f"/v1/admin/usuarios/{user.id}/desconectar-wpp").status_code == 200
    assert client.patch(
        f"/v1/admin/usuarios/{user.id}/admin",
        json={"is_admin": True, "password_actual": "ClaveAdmin123"},
    ).status_code == 200
    assert client.patch(
        f"/v1/admin/usuarios/{user.id}/admin",
        json={"is_admin": False, "password_actual": "ClaveAdmin123"},
    ).status_code == 200
    assert client.request(
        "DELETE",
        f"/v1/admin/usuarios/{user.id}",
        json={"email_confirmacion": user.email, "password_actual": "ClaveAdmin123"},
    ).status_code == 200

    class FakeJob:
        def __init__(self):
            self.calls = []

        def modify(self, **kwargs):
            self.calls.append(kwargs)

    class FakeScheduler:
        running = True

        def __init__(self):
            self.job = FakeJob()

        def get_job(self, job_id):
            return self.job if job_id == "guardar_cotizaciones_diarias" else None

    monkeypatch.setattr(app.state, "scheduler", FakeScheduler(), raising=False)
    r_sched = client.post("/v1/admin/scheduler/guardar_cotizaciones_diarias/correr")
    assert r_sched.status_code == status.HTTP_202_ACCEPTED
    assert r_sched.json() == {"mensaje": "Se va a correr en unos segundos."}

    monkeypatch.setattr(app.state, "scheduler", None, raising=False)
    r_sched_off = client.post("/v1/admin/scheduler/guardar_cotizaciones_diarias/correr")
    assert r_sched_off.status_code == status.HTTP_409_CONFLICT

    filas = db_session.scalars(select(AccionAdmin).order_by(AccionAdmin.fecha.asc(), AccionAdmin.id.asc())).all()
    acciones = [f.accion for f in filas]
    assert acciones == [
        "suspender",
        "reactivar",
        "mail_contrasena",
        "cerrar_sesiones",
        "desconectar_whatsapp",
        "hacer_admin",
        "quitar_admin",
        "eliminar_cuenta",
        "correr_tarea",
    ]
    assert filas[0].admin_nombre == "Admin Tester"
    assert filas[0].usuario_texto == "Regular User"
    assert filas[0].detalle is None
    assert filas[7].usuario_id is None
    assert filas[7].usuario_texto == "Cuenta eliminada"
    assert filas[8].detalle == "Registro de cotizaciones del dólar"


def test_r2_acciones_fallidas_no_se_registran(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(db_session, is_admin=True, password="ClaveAdmin123")
    user = _crear_usuario(db_session, telefono_verificado=False)
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)

    assert client.patch(f"/v1/admin/usuarios/{admin.id}/admin", json={"is_admin": False}).status_code == 400
    assert client.request("DELETE", f"/v1/admin/usuarios/{user.id}", json={"email_confirmacion": "otro@x.com"}).status_code == 400
    assert client.post(f"/v1/admin/usuarios/{user.id}/desconectar-wpp").status_code == 400

    assert db_session.scalars(select(AccionAdmin)).all() == []


def test_r3_get_registro_paginado_y_filtro(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(db_session, nombre="Admin", apellido="Uno", is_admin=True, password="ClaveAdmin123")
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)

    for i in range(25):
        acc = "hacer_admin" if i < 3 else "suspender"
        db_session.add(
            AccionAdmin(
                id=uuid4(),
                fecha=AHORA + timedelta(minutes=i),
                admin_id=admin.id,
                admin_nombre="Admin Uno",
                accion=acc,
                usuario_texto=f"User {i}",
                detalle=f"det {i}",
            )
        )
    db_session.commit()

    r1 = client.get("/v1/admin/registro?page=1&limit=20")
    assert r1.status_code == 200
    d1 = r1.json()["data"]
    assert d1["total"] == 25
    assert d1["pages"] == 2
    assert len(d1["items"]) == 20
    assert d1["items"][0]["usuario"] == "User 24"
    assert set(d1["items"][0].keys()) == {"fecha", "admin", "accion", "accion_texto", "usuario", "detalle"}

    r2 = client.get("/v1/admin/registro?page=2&limit=20")
    assert r2.status_code == 200
    assert len(r2.json()["data"]["items"]) == 5

    r_filt = client.get("/v1/admin/registro?accion=hacer_admin")
    assert r_filt.status_code == 200
    assert r_filt.json()["data"]["total"] == 3

    r_bad = client.get("/v1/admin/registro?accion=otra")
    assert r_bad.status_code == 422

    with pytest.raises(ValueError):
        admin_registro_service.registrar_accion(db_session, admin, "accion_inexistente")


def test_r4_purgar_acciones_admin_y_job_limpiar_tokens(db_session: Session):
    admin = _crear_usuario(db_session, is_admin=True)
    db_session.add(AccionAdmin(id=uuid4(), fecha=AHORA - timedelta(days=366), admin_id=admin.id, admin_nombre="A", accion="suspender"))
    db_session.add(AccionAdmin(id=uuid4(), fecha=AHORA - timedelta(days=364), admin_id=admin.id, admin_nombre="A", accion="reactivar"))
    db_session.commit()

    borradas = admin_registro_service.purgar_acciones_admin(db_session, AHORA)
    assert borradas == 1
    restantes = db_session.scalars(select(AccionAdmin)).all()
    assert len(restantes) == 1
    assert restantes[0].accion == "reactivar"

    orden_llamadas: list[str] = []
    with patch("app.main.SessionLocal", return_value=db_session), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job"), \
         patch("app.main.limpiar_tokens_expirados", side_effect=lambda db: orden_llamadas.append("tokens") or 0), \
         patch("app.main.limpiar_codigos_y_rate_limits_expirados", side_effect=lambda db: orden_llamadas.append("codigos") or {"codigos_verificacion_eliminados": 0, "rate_limits_eliminados": 0}), \
         patch("app.main.purgar_conversaciones_wpp_antiguas", side_effect=lambda db: orden_llamadas.append("conv") or 0), \
         patch("app.main.purgar_mensajes_whatsapp_procesados_antiguos", side_effect=lambda db: orden_llamadas.append("wamid") or 0), \
         patch("app.main.purgar_uso_ia_antiguo", side_effect=lambda db: orden_llamadas.append("uso_ia") or 0), \
         patch("app.main.purgar_salientes_antiguos", side_effect=lambda db: orden_llamadas.append("salientes") or 0), \
         patch("app.main.purgar_claves_idempotencia", side_effect=lambda db: orden_llamadas.append("claves") or 0), \
         patch("app.main.purgar_eventos_actualizacion", side_effect=lambda db: orden_llamadas.append("eventos") or 0), \
         patch("app.main.purgar_acciones_admin", side_effect=lambda db, ahora: orden_llamadas.append("acciones_admin") or 1):
        app_main._job_limpiar_tokens()

    assert orden_llamadas.count("acciones_admin") == 1
    assert orden_llamadas.index("acciones_admin") > orden_llamadas.index("eventos")


def test_o1_resetear_onboarding_eliminado(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(db_session, is_admin=True)
    user = _crear_usuario(db_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)

    res = client.post(f"/v1/admin/usuarios/{user.id}/resetear-onboarding", json={"confirmar": True})
    assert res.status_code in (404, 405)
    assert not hasattr(admin_service, "resetear_onboarding")
    assert not hasattr(admin_schemas, "ResetearOnboardingRequest")


def test_m1_m2_monitor_wpp_resumen(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(db_session, is_admin=True)
    _agregar_dolar_oficial(db_session, date(2026, 11, 6), Decimal("1450"))
    f_mes = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)

    for i in range(12):
        u = _crear_usuario(db_session, nombre=f"User{i}", apellido="Costo")
        _agregar_uso_ia(db_session, u.id, Decimal("0.01") * (i + 1), f_mes)
        _agregar_saliente(db_session, u.id, f_mes)
    u_cero = _crear_usuario(db_session, nombre="Cero", apellido="Costo")
    _agregar_uso_ia(db_session, u_cero.id, Decimal("0"), f_mes)

    ahora_real = datetime.now(timezone.utc)
    delta_dentro = (
        timedelta(hours=1)
        if datetime.now(TZ_ARGENTINA).hour >= 1
        else timedelta(minutes=1)
    )
    dentro = ahora_real - delta_dentro
    fuera = ahora_real - timedelta(days=2)

    for idx, st in enumerate([
        cola_wpp_service.FALLO,
        cola_wpp_service.FALLO,
        cola_wpp_service.INTERRUMPIDO,
        cola_wpp_service.VENCIDO,
    ]):
        db_session.add(
            MensajeWhatsappProcesado(wamid=f"in_{idx}", estado=st, fecha_recepcion=dentro)
        )
    db_session.add(
        MensajeWhatsappProcesado(
            wamid="out_1", estado=cola_wpp_service.FALLO, fecha_recepcion=fuera
        )
    )
    db_session.commit()

    res = monitor_wpp_service.obtener_resumen_monitor(db_session, dias=1, hoy=HOY)
    por_u_mes = res["costo"]["por_usuario_mes"]
    assert len(por_u_mes) == 10
    assert all(it["total_ars"] > 0 for it in por_u_mes)
    totales_res = [it["total_ars"] for it in por_u_mes]
    assert totales_res == sorted(totales_res, reverse=True)
    assert set(por_u_mes[0].keys()) == {
        "usuario_id", "nombre", "total_ars", "ia_usd", "whatsapp_cobrados"
    }

    assert res["salud"]["wpp_fallados"] == 2
    assert res["salud"]["wpp_interrumpidos"] == 1
    assert res["salud"]["wpp_vencidos"] == 1
    assert "tiempo_respuesta_mediana_ms" in res["salud"]

    # Mes siguiente con < 10 usuarios para confirmar que total 0 queda fuera del ranking
    f_dic = datetime(2026, 12, 5, 15, 0, tzinfo=timezone.utc)
    _agregar_dolar_oficial(db_session, date(2026, 12, 6), Decimal("1450"))
    u_dic_pago = _crear_usuario(db_session, nombre="Dic", apellido="Pago")
    _agregar_uso_ia(db_session, u_dic_pago.id, Decimal("0.10"), f_dic)
    _agregar_uso_ia(db_session, u_cero.id, Decimal("0.0001"), f_dic)
    db_session.commit()

    res_dic = monitor_wpp_service.obtener_resumen_monitor(
        db_session, dias=1, hoy=date(2026, 12, 15)
    )
    por_dic = res_dic["costo"]["por_usuario_mes"]
    assert len(por_dic) == 1
    assert por_dic[0]["usuario_id"] == u_dic_pago.id
    assert all(it["total_ars"] > 0 for it in por_dic)

    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: admin)
    resp = client.get("/v1/admin/monitor-wpp/resumen?dias=1")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["costo"]["por_usuario_mes"], list)
    assert "wpp_fallados" in body["salud"]
    assert "wpp_interrumpidos" in body["salud"]
    assert "wpp_vencidos" in body["salud"]


def test_s1_estaticos_j3b():
    raiz = Path(__file__).resolve().parents[1]
    app_dir = raiz / "app"

    archivos_tarifa = [
        p.relative_to(raiz).as_posix()
        for p in sorted(app_dir.rglob("*.py"))
        if "WHATSAPP_TARIFA_SERVICIO_ARS" in p.read_text(encoding="utf-8")
    ]
    assert archivos_tarifa == ["app/core/config.py", "app/services/costos_service.py"]

    uso_txt = (app_dir / "services/admin_uso_service.py").read_text(encoding="utf-8")
    costos_txt = (app_dir / "services/costos_service.py").read_text(encoding="utf-8")
    assert "greatest(" not in uso_txt.lower()
    assert "greatest(" not in costos_txt.lower()
    assert "permitirse_wpp" not in uso_txt
    assert "cuotas_contado_wpp" not in uso_txt

    router_txt = (app_dir / "routers/admin.py").read_text(encoding="utf-8")
    assert "resetear" not in router_txt.lower()

    mod = ast.parse(router_txt)
    llamadas_por_fn: dict[str, set[str]] = {}
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("cambiar_rol_admin", "eliminar_usuario_admin"):
            nombres: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if isinstance(fn, ast.Attribute):
                        nombres.add(fn.attr)
                    elif isinstance(fn, ast.Name):
                        nombres.add(fn.id)
            llamadas_por_fn[node.name] = nombres

    assert "verificar_identidad_admin" in llamadas_por_fn["cambiar_rol_admin"]
    assert "verificar_identidad_admin" in llamadas_por_fn["eliminar_usuario_admin"]
