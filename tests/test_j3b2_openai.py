# -*- coding: utf-8 -*-
"""
Pruebas del backend para fase_j3b2_panel_admin: costos reales de OpenAI y monitor WPP con rango.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
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
from app.core.reglas_texto_web import problemas_texto_web
from app.main import app
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.models.uso_ia import UsoIA
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import costos_service, monitor_wpp_service, openai_costos_service
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
    openai_costos_service.limpiar_cache()
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSessionLocal)

    def _override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setitem(app.dependency_overrides, get_db, _override_db)
    yield
    openai_costos_service.limpiar_cache()
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
        email="admin.openai@prueba.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.ADMIN,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        is_admin=True,
        onboarding_completo=True,
    )


def _crear_usuario(db: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        nombre="Tester",
        apellido="Uno",
        email=f"u_{uuid4().hex[:8]}@prueba.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        is_admin=False,
        onboarding_completo=True,
    )
    db.add(u)
    db.commit()
    return u


def _ts_utc(dia: date) -> int:
    return int(datetime(dia.year, dia.month, dia.day, 0, 0, 0, tzinfo=timezone.utc).timestamp())


def _fake_httpx_client(monkeypatch, responses_or_fn):
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None, headers=None):
            calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
            if callable(responses_or_fn):
                return responses_or_fn(len(calls) - 1, url, params, headers)
            return responses_or_fn[len(calls) - 1]

    monkeypatch.setattr(openai_costos_service.httpx, "Client", FakeClient)
    return calls


def _resp_json(status_code: int, body: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body
    return r


def test_o1_sin_clave_openai(monkeypatch):
    openai_costos_service.limpiar_cache()
    monkeypatch.setattr("app.core.config.settings.OPENAI_ADMIN_KEY", "")
    calls = _fake_httpx_client(monkeypatch, [])

    res = openai_costos_service.costos_por_dia(date(2026, 11, 18), date(2026, 11, 20), AHORA)
    assert res is None
    assert calls == []
    assert openai_costos_service.estado_openai(res) == "sin_conexion"


def test_o2_o3_paginacion_proyecto_y_moneda(monkeypatch):
    openai_costos_service.limpiar_cache()
    monkeypatch.setattr("app.core.config.settings.OPENAI_ADMIN_KEY", "sk-admin-secreto-test")
    monkeypatch.setattr("app.core.config.settings.OPENAI_PROJECT_ID", "proj_prod")

    pag1 = _resp_json(
        200,
        {
            "has_more": True,
            "next_page": "p2",
            "data": [
                {
                    "start_time": _ts_utc(date(2026, 11, 18)),
                    "results": [
                        {"project_id": "proj_prod", "amount": {"value": 0.10, "currency": "usd"}},
                        {"project_id": "proj_pruebas", "amount": {"value": 0.90, "currency": "usd"}},
                    ],
                },
                {
                    "start_time": _ts_utc(date(2026, 11, 19)),
                    "results": [
                        {"project_id": "proj_prod", "amount": {"value": 0.05, "currency": "usd"}},
                    ],
                },
            ],
        },
    )
    pag2 = _resp_json(
        200,
        {
            "has_more": False,
            "data": [
                {
                    "start_time": _ts_utc(date(2026, 11, 20)),
                    "results": [
                        {"project_id": "proj_prod", "amount": {"value": 0.02, "currency": "usd"}},
                        {"project_id": "proj_prod", "amount": {"value": 9.99, "currency": "eur"}},
                    ],
                },
            ],
        },
    )
    calls = _fake_httpx_client(monkeypatch, [pag1, pag2])

    res = openai_costos_service.costos_por_dia(date(2026, 11, 18), date(2026, 11, 20), AHORA)
    assert res == {
        date(2026, 11, 18): Decimal("0.10"),
        date(2026, 11, 19): Decimal("0.05"),
        date(2026, 11, 20): Decimal("0.02"),
    }
    assert all(isinstance(v, Decimal) for v in res.values())
    assert len(calls) == 2
    assert "page" not in calls[0]["params"]
    assert calls[1]["params"]["page"] == "p2"
    assert calls[0]["params"]["start_time"] == _ts_utc(date(2026, 11, 18))
    assert calls[0]["params"]["end_time"] == _ts_utc(date(2026, 11, 21))
    assert calls[0]["params"]["bucket_width"] == "1d"
    assert calls[0]["params"]["group_by"] == "project_id"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-admin-secreto-test"
    assert openai_costos_service.estado_openai(res) == "ok"

    # O3: sin OPENAI_PROJECT_ID suma todos los proyectos en usd
    openai_costos_service.limpiar_cache()
    monkeypatch.setattr("app.core.config.settings.OPENAI_PROJECT_ID", "")
    _fake_httpx_client(monkeypatch, [pag1, pag2])
    res_todos = openai_costos_service.costos_por_dia(date(2026, 11, 18), date(2026, 11, 20), AHORA)
    assert res_todos[date(2026, 11, 18)] == Decimal("1.00")


def test_o4_errores_openai_sin_filtrar_clave(monkeypatch, caplog):
    clave = "sk-admin-ultra-secreto-999"
    monkeypatch.setattr("app.core.config.settings.OPENAI_ADMIN_KEY", clave)

    def _raises(*_args):
        raise httpx.ConnectError("fallo red")

    with caplog.at_level(logging.WARNING):
        openai_costos_service.limpiar_cache()
        _fake_httpx_client(monkeypatch, _raises)
        r_exc = openai_costos_service.costos_por_dia(date(2026, 11, 18), date(2026, 11, 20), AHORA)
        assert r_exc is None
        assert openai_costos_service.estado_openai(r_exc) == "error"

        openai_costos_service.limpiar_cache()
        _fake_httpx_client(monkeypatch, [_resp_json(500, {"error": "boom"})])
        r_500 = openai_costos_service.costos_por_dia(date(2026, 11, 18), date(2026, 11, 20), AHORA)
        assert r_500 is None
        assert openai_costos_service.estado_openai(r_500) == "error"

        openai_costos_service.limpiar_cache()
        _fake_httpx_client(monkeypatch, [_resp_json(200, {"other": []})])
        r_bad = openai_costos_service.costos_por_dia(date(2026, 11, 18), date(2026, 11, 20), AHORA)
        assert r_bad is None
        assert openai_costos_service.estado_openai(r_bad) == "error"

    assert "No se pudo consultar el costo de OpenAI." in caplog.text
    assert clave not in caplog.text


def test_o5_cache_15_min_y_none_no_se_guarda(monkeypatch):
    openai_costos_service.limpiar_cache()
    monkeypatch.setattr("app.core.config.settings.OPENAI_ADMIN_KEY", "sk-admin-cache")
    monkeypatch.setattr("app.core.config.settings.OPENAI_PROJECT_ID", "")

    pag_ok = _resp_json(
        200,
        {
            "has_more": False,
            "data": [
                {
                    "start_time": _ts_utc(date(2026, 11, 19)),
                    "results": [{"project_id": "p", "amount": {"value": 0.12, "currency": "usd"}}],
                }
            ],
        },
    )
    calls = _fake_httpx_client(monkeypatch, [pag_ok, pag_ok])

    openai_costos_service.costos_por_dia(date(2026, 11, 19), date(2026, 11, 19), AHORA)
    openai_costos_service.costos_por_dia(
        date(2026, 11, 19), date(2026, 11, 19), AHORA + timedelta(minutes=10)
    )
    assert len(calls) == 1

    openai_costos_service.costos_por_dia(
        date(2026, 11, 19), date(2026, 11, 19), AHORA + timedelta(minutes=16)
    )
    assert len(calls) == 2

    # None no se guarda en cache
    openai_costos_service.limpiar_cache()
    calls_none = _fake_httpx_client(monkeypatch, [_resp_json(500, {}), pag_ok])
    assert openai_costos_service.costos_por_dia(date(2026, 11, 19), date(2026, 11, 19), AHORA) is None
    res2 = openai_costos_service.costos_por_dia(
        date(2026, 11, 19), date(2026, 11, 19), AHORA + timedelta(minutes=1)
    )
    assert res2 == {date(2026, 11, 19): Decimal("0.12")}
    assert len(calls_none) == 2


def test_o6_o7_costo_ia_mes_usd_y_total_con_fuente(db_session: Session, monkeypatch):
    openai_costos_service.limpiar_cache()
    u = _crear_usuario(db_session)
    db_session.add(
        UsoIA(
            id=uuid4(),
            usuario_id=u.id,
            funcion="texto_whatsapp",
            modelo="gpt-6-luna",
            tokens_entrada=10,
            tokens_salida=5,
            tokens_entrada_cache=0,
            costo_usd=Decimal("0.04"),
            fecha=datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc),
        )
    )
    db_session.add(
        UsoIA(
            id=uuid4(),
            usuario_id=u.id,
            funcion="texto_whatsapp",
            modelo="gpt-6-luna",
            tokens_entrada=10,
            tokens_salida=5,
            tokens_entrada_cache=0,
            costo_usd=Decimal("9.99"),
            fecha=datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc),
        )
    )
    db_session.add(
        CotizacionDolar(
            id=uuid4(),
            fecha=date(2026, 11, 20),
            tipo="oficial",
            compra=Decimal("1400"),
            venta=Decimal("1450"),
            promedio=Decimal("1425"),
        )
    )
    for _ in range(2):
        db_session.add(
            MensajeSalienteWpp(
                id=uuid4(),
                usuario_id=u.id,
                tipo="respuesta",
                es_plantilla=False,
                partes=1,
                enviado=True,
                tipo_cobro="regular",
                fecha=datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc),
            )
        )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.openai_costos_service.costos_por_dia",
        lambda d, h, a: {
            date(2026, 11, 1): Decimal("0.30"),
            date(2026, 11, 19): Decimal("0.20"),
            date(2026, 11, 20): Decimal("0.50"),
        },
    )
    usd_real, fuente = costos_service.costo_ia_mes_usd(db_session, HOY, AHORA)
    assert (usd_real, fuente) == (Decimal("0.54"), "openai")

    assert costos_service.costo_mes_total_con_fuente(db_session, HOY, AHORA) == (858, False, "openai")

    monkeypatch.setattr("app.services.openai_costos_service.costos_por_dia", lambda d, h, a: None)
    usd_est, fuente_est = costos_service.costo_ia_mes_usd(db_session, HOY, AHORA)
    assert (usd_est, fuente_est) == (costos_service.gasto_ia_mes_usd(db_session, HOY), "estimado")


def test_r1_validaciones_monitor_wpp_resumen(db_session: Session, client: TestClient, monkeypatch):
    admin = _admin_memoria()
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    monkeypatch.setattr("app.routers.admin.hoy_argentina", lambda: HOY)
    monkeypatch.setattr("app.routers.admin.ahora_utc", lambda: AHORA)
    monkeypatch.setattr("app.core.config.settings.OPENAI_ADMIN_KEY", "")

    r_sin = client.get("/v1/admin/monitor-wpp/resumen")
    r_1 = client.get("/v1/admin/monitor-wpp/resumen?dias=1")
    assert r_sin.status_code == 200
    assert r_sin.json() == r_1.json()

    r_mix = client.get("/v1/admin/monitor-wpp/resumen?dias=7&desde=2026-11-18&hasta=2026-11-20")
    assert r_mix.status_code == 422
    assert r_mix.json()["error"]["message"] == "Elegí un período o un rango de fechas."

    r_una = client.get("/v1/admin/monitor-wpp/resumen?desde=2026-11-18")
    assert r_una.status_code == 422
    assert r_una.json()["error"]["message"] == "Elegí las dos fechas."

    r_ord = client.get("/v1/admin/monitor-wpp/resumen?desde=2026-11-10&hasta=2026-11-05")
    assert r_ord.status_code == 422
    assert r_ord.json()["error"]["message"] == "La fecha de inicio tiene que ser anterior a la de fin."

    r_fut = client.get("/v1/admin/monitor-wpp/resumen?desde=2026-11-18&hasta=2026-11-21")
    assert r_fut.status_code == 422
    assert r_fut.json()["error"]["message"] == "La fecha de fin no puede ser posterior a hoy."

    r_90_a = client.get("/v1/admin/monitor-wpp/resumen?desde=2026-08-01&hasta=2026-11-20")
    assert r_90_a.status_code == 422
    assert r_90_a.json()["error"]["message"] == "Elegí un rango de hasta 90 días."

    r_91 = client.get("/v1/admin/monitor-wpp/resumen?desde=2026-08-22&hasta=2026-11-20")
    assert r_91.status_code == 422
    assert r_91.json()["error"]["message"] == "Elegí un rango de hasta 90 días."

    r_90_ok = client.get("/v1/admin/monitor-wpp/resumen?desde=2026-08-23&hasta=2026-11-20")
    assert r_90_ok.status_code == 200


def test_r2_r3_monitor_wpp_rango_resumen_y_conversaciones(
    db_session: Session, client: TestClient, monkeypatch
):
    admin = _admin_memoria()
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)
    monkeypatch.setattr("app.routers.admin.hoy_argentina", lambda: HOY)
    monkeypatch.setattr("app.routers.admin.ahora_utc", lambda: AHORA)

    u = _crear_usuario(db_session)
    fechas_conv = [
        datetime(2026, 11, 17, 23, 30, tzinfo=TZ_ARGENTINA),
        datetime(2026, 11, 18, 10, 0, tzinfo=TZ_ARGENTINA),
        datetime(2026, 11, 19, 23, 30, tzinfo=TZ_ARGENTINA),
        datetime(2026, 11, 20, 0, 30, tzinfo=TZ_ARGENTINA),
    ]
    for idx, f in enumerate(fechas_conv):
        db_session.add(
            ConversacionWpp(
                id=uuid4(),
                usuario_id=u.id,
                tipo_mensaje=TipoMensajeWpp.TEXTO,
                mensaje_usuario=f"msg_{idx}",
                mensaje_bot=f"bot_{idx}",
                intent_detectado="registrar_transaccion",
                fecha=f,
            )
        )
    db_session.add(
        UsoIA(
            id=uuid4(),
            usuario_id=u.id,
            funcion="texto_whatsapp",
            modelo="gpt-6-luna",
            tokens_entrada=10,
            tokens_salida=5,
            tokens_entrada_cache=0,
            costo_usd=Decimal("0.10"),
            fecha=datetime(2026, 11, 18, 10, 0, tzinfo=TZ_ARGENTINA),
        )
    )
    db_session.add(
        UsoIA(
            id=uuid4(),
            usuario_id=u.id,
            funcion="texto_whatsapp",
            modelo="gpt-6-luna",
            tokens_entrada=10,
            tokens_salida=5,
            tokens_entrada_cache=0,
            costo_usd=Decimal("5.00"),
            fecha=datetime(2026, 11, 20, 0, 30, tzinfo=TZ_ARGENTINA),
        )
    )
    db_session.commit()

    monkeypatch.setattr("app.core.config.settings.OPENAI_ADMIN_KEY", "sk-admin-r2")
    monkeypatch.setattr(
        "app.services.openai_costos_service.costos_por_dia",
        lambda d, h, a: {
            date(2026, 11, 18): Decimal("0.07"),
            date(2026, 11, 19): Decimal("0.01"),
        },
    )
    res_ok = monitor_wpp_service.obtener_resumen_monitor(
        db_session, desde=date(2026, 11, 18), hasta=date(2026, 11, 19), hoy=HOY, ahora=AHORA
    )
    assert res_ok["calidad"]["total"] == 2
    assert res_ok["costo"]["total_usd"] == Decimal("0.10")
    assert res_ok["costo"]["openai_usd"] == Decimal("0.08")
    assert res_ok["costo"]["openai_estado"] == "ok"
    assert res_ok["costo"]["openai_hasta"] == date(2026, 11, 19)

    monkeypatch.setattr("app.core.config.settings.OPENAI_ADMIN_KEY", "")
    monkeypatch.setattr("app.services.openai_costos_service.costos_por_dia", lambda d, h, a: None)
    res_sin = monitor_wpp_service.obtener_resumen_monitor(
        db_session, desde=date(2026, 11, 18), hasta=date(2026, 11, 19), hoy=HOY, ahora=AHORA
    )
    assert res_sin["costo"]["openai_usd"] is None
    assert res_sin["costo"]["openai_estado"] == "sin_conexion"

    # R3: GET /v1/admin/monitor-wpp/conversaciones con desde/hasta
    r_conv = client.get("/v1/admin/monitor-wpp/conversaciones?desde=2026-11-18&hasta=2026-11-19")
    assert r_conv.status_code == 200
    body_c = r_conv.json()
    assert body_c["total"] == 2
    assert len(body_c["items"]) == 2


def test_r4_textos_error_b7_pasan_reglas_texto_web():
    textos = [
        "Elegí un período o un rango de fechas.",
        "Elegí las dos fechas.",
        "La fecha de inicio tiene que ser anterior a la de fin.",
        "La fecha de fin no puede ser posterior a hoy.",
        "Elegí un rango de hasta 90 días.",
        "Elegí 1, 7 o 30 días.",
    ]
    for t in textos:
        assert problemas_texto_web(t) == [], f"Falló reglas_texto_web para: {t}"
