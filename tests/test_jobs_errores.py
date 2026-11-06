# -*- coding: utf-8 -*-
"""
tests/test_jobs_errores.py — Pruebas para propagación de excepciones en tareas automáticas (R1, R2 y R3).
Valores exactos del prompt de fase_j1_costos_jobs.
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
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

from app.core.database import Base
from app.core.registro_jobs import registrar_ejecucion
from app.models.tools import IPCCache
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.models.presupuesto import Presupuesto, EstadoPresupuesto, PeriodoPresupuestoTipo, RenovacionPresupuesto
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.services.inflacion_esperada_service import InflacionEsperada

import app.main as app_main
import app.services.notificacion_scheduler_service as nss
import app.services.factura_avisos_service as fas
import app.services.notificacion_despacho_service as nds


@pytest.fixture(name="db_session")
def db_session_fixture(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.services.rate_limit_service.SessionLocal", TestingSession)
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSession)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario(db: Session, email_prefix="user") -> Usuario:
    tel = f"+54911{uuid4().int % 100000000:08d}"
    u = Usuario(
        id=uuid4(),
        email=f"{email_prefix}_{uuid4().hex[:6]}@argentum.test",
        telefono_normalizado=tel,
        telefono=tel,
        telefono_verificado=True,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        onboarding_completo=True,
    )
    db.add(u)
    db.commit()
    return u


# ==============================================================================
# 1. R1: CADA UNA DE LAS 21 FUNCIONES DEJA SALIR SU EXCEPCIÓN
# ==============================================================================

# Funciones de app/main.py
def test_r1_main_limpiar_tokens():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.main.limpiar_tokens_expirados", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_limpiar_tokens()
    assert "_job_limpiar_tokens" in locks_liberados


def test_r1_main_vencimientos_tarjetas():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.main.procesar_vencimientos_tarjetas", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_vencimientos_tarjetas()
    assert "_job_vencimientos_tarjetas" in locks_liberados


def test_r1_main_renovar_presupuestos():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.main.renovar_presupuestos", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_renovar_presupuestos()
    assert "_job_renovar_presupuestos" in locks_liberados


def test_r1_main_cobros_suscripciones():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.main.procesar_cobros_suscripciones", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_cobros_suscripciones()
    assert "_job_cobros_suscripciones" in locks_liberados


def test_r1_main_job_entrega_whatsapp_batched():
    alertas = []
    with patch("app.main._job_entrega_whatsapp_batched", side_effect=RuntimeError("falla simulada")), \
         patch("app.services.alerta_service.enviar_alerta_admin", side_effect=lambda *a, **kw: alertas.append(kw)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main.job_entrega_whatsapp_batched()
    assert len(alertas) == 1


def test_r1_main_actualizar_perfiles():
    mock_db = MagicMock()
    mock_db.execute.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_actualizar_perfiles()
    assert "_job_actualizar_perfiles" in locks_liberados


def test_r1_main_consejos_diarios():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.services.inflacion_esperada_service.inflacion_esperada", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_consejos_diarios()
    assert "_job_consejos_diarios" in locks_liberados


def test_r1_main_refresh_feriados():
    import anyio
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.services.dias_habiles_service.asegurar_feriados_cargados", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            anyio.run(app_main._job_refresh_feriados)
    assert "_job_refresh_feriados" in locks_liberados


def test_r1_main_guardar_cotizaciones_diarias():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.services.dolar_service.guardar_cotizaciones_del_dia", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_guardar_cotizaciones_diarias()
    assert "_job_guardar_cotizaciones_diarias" in locks_liberados


def test_r1_main_actualizar_rem():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.services.inflacion_esperada_service.bajar_rem", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_actualizar_rem()
    assert "_job_actualizar_rem" in locks_liberados


def test_r1_main_actualizar_ipc():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.services.tools_service.get_current_ipc", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_actualizar_ipc()
    assert "_job_actualizar_ipc" in locks_liberados


def test_r1_main_actualizar_tasas():
    mock_db = MagicMock()
    locks_liberados = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.services.tasas_service.actualizar_tasas", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_actualizar_tasas()
    assert "_job_actualizar_tasas" in locks_liberados


# Funciones de notificacion_scheduler_service
def test_r1_nss_notificaciones_cuotas():
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_scheduler_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            nss._job_notificaciones_cuotas(lambda: mock_db)
    assert "_job_notificaciones_cuotas" in locks_liberados


def test_r1_nss_notificaciones_presupuestos():
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_scheduler_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            nss._job_notificaciones_presupuestos(lambda: mock_db)
    assert "_job_notificaciones_presupuestos" in locks_liberados


def test_r1_nss_notificaciones_suscripciones():
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_scheduler_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            nss._job_notificaciones_suscripciones(lambda: mock_db)
    assert "_job_notificaciones_suscripciones" in locks_liberados


def test_r1_nss_notificaciones_inactividad():
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_scheduler_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            nss._job_notificaciones_inactividad(lambda: mock_db)
    assert "_job_notificaciones_inactividad" in locks_liberados


def test_r1_nss_entrega_whatsapp_batched():
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_scheduler_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            nss._job_entrega_whatsapp_batched(lambda: mock_db)
    assert "_job_entrega_whatsapp_batched" in locks_liberados


def test_r1_nss_resumen_cierre_ciclo():
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_scheduler_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            nss._job_resumen_cierre_ciclo(lambda: mock_db)
    assert "_job_resumen_cierre_ciclo" in locks_liberados


def test_r1_nss_resumen_semanal():
    mock_db = MagicMock()
    mock_db.execute.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_scheduler_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            nss._job_resumen_semanal(lambda: mock_db)
    assert "_job_resumen_semanal" in locks_liberados


# factura_avisos_service
def test_r1_fas_notificaciones_facturas():
    mock_db = MagicMock()
    mock_db.execute.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.factura_avisos_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.factura_avisos_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            fas._job_notificaciones_facturas(lambda: mock_db)
    assert "_job_notificaciones_facturas" in locks_liberados


# notificacion_despacho_service
def test_r1_nds_reintentar_inmediatas():
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("falla simulada")
    locks_liberados = []
    with patch("app.services.notificacion_despacho_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_despacho_service.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)):
        with pytest.raises(RuntimeError, match="falla simulada"):
            nds._job_reintentar_inmediatas(lambda: mock_db)
    assert "_job_reintentar_inmediatas" in locks_liberados


# ==============================================================================
# REGISTRAR EJECUCION CON Y SIN FALLA
# ==============================================================================

def test_registrar_ejecucion_con_falla_guarda_error():
    mock_db = MagicMock()
    registros = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job"), \
         patch("app.main.renovar_presupuestos", side_effect=RuntimeError("falla simulada")), \
         patch("app.core.registro_jobs.guardar_registro_ejecucion", side_effect=lambda **kw: registros.append(kw)):

        wrapped = registrar_ejecucion("renovar_presupuestos", app_main._job_renovar_presupuestos)
        with pytest.raises(RuntimeError, match="falla simulada"):
            wrapped()

    assert len(registros) == 1
    assert registros[0]["resultado"] == "error"
    assert registros[0]["detalle"] == "RuntimeError: falla simulada"


def test_registrar_ejecucion_sin_falla_guarda_ok():
    mock_db = MagicMock()
    registros = []
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job"), \
         patch("app.main.renovar_presupuestos"), \
         patch("app.core.registro_jobs.guardar_registro_ejecucion", side_effect=lambda **kw: registros.append(kw)):

        wrapped = registrar_ejecucion("renovar_presupuestos", app_main._job_renovar_presupuestos)
        wrapped()  # No lanza

    assert len(registros) == 1
    assert registros[0]["resultado"] == "ok"
    assert registros[0]["detalle"] is None


# ==============================================================================
# 2. R2: JOBS QUE RECORREN PERSONAS O PRESUPUESTOS
# ==============================================================================

def test_r2_actualizar_perfiles(db_session: Session, monkeypatch):
    u1 = _crear_usuario(db_session, "u1")
    u2 = _crear_usuario(db_session, "u2")
    u3 = _crear_usuario(db_session, "u3")

    engine = db_session.get_bind()
    TestSessionMaker = sessionmaker(bind=engine)
    monkeypatch.setattr("app.main.SessionLocal", TestSessionMaker)
    monkeypatch.setattr("app.main.intentar_tomar_lock_job", lambda *a, **kw: True)
    monkeypatch.setattr("app.main.liberar_lock_job", lambda *a, **kw: None)

    calculados = []

    def fake_calcular(db, uid):
        if uid == u2.id:
            raise RuntimeError("Fallo perfil u2")
        calculados.append(uid)

    monkeypatch.setattr("app.services.perfil_financiero_service.calcular_y_persistir_perfil", fake_calcular)

    with pytest.raises(RuntimeError, match=r"^Perfiles: 1 de 3 con error$"):
        app_main._job_actualizar_perfiles()

    assert u1.id in calculados
    assert u3.id in calculados
    assert u2.id not in calculados

    # Sin fallas -> no lanza
    calculados.clear()
    monkeypatch.setattr("app.services.perfil_financiero_service.calcular_y_persistir_perfil", lambda db, uid: calculados.append(uid))
    app_main._job_actualizar_perfiles()
    assert len(calculados) == 3


def test_r2_notificaciones_presupuestos(db_session: Session, monkeypatch):
    u = _crear_usuario(db_session)
    p1 = Presupuesto(
        id=uuid4(),
        usuario_id=u.id,
        nombre="P1",
        monto=Decimal("1000"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    p2 = Presupuesto(
        id=uuid4(),
        usuario_id=u.id,
        nombre="P2",
        monto=Decimal("2000"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db_session.add_all([p1, p2])
    db_session.commit()

    engine = db_session.get_bind()
    TestSessionMaker = sessionmaker(bind=engine)
    monkeypatch.setattr("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.notificacion_scheduler_service.liberar_lock_job", lambda *a, **kw: None)

    monkeypatch.setattr("app.services.presupuesto_service.obtener_periodo_activo", lambda db, pres, **kw: MagicMock())
    monkeypatch.setattr("app.services.presupuesto_service.uso_periodo", lambda db, pres, per, **kw: MagicMock(mostrado=Decimal("500")))

    evaluados = []

    def fake_verificar(db, pres, per, uso, **kw):
        if pres.id == p1.id:
            raise RuntimeError("Fallo p1")
        evaluados.append(pres.id)

    monkeypatch.setattr("app.services.presupuesto_service.verificar_alertas_presupuesto", fake_verificar)

    with pytest.raises(RuntimeError, match=r"^Presupuestos: 1 de 2 con error$"):
        nss._job_notificaciones_presupuestos(TestSessionMaker)

    assert p2.id in evaluados
    assert p1.id not in evaluados

    # Sin fallas -> no lanza
    evaluados.clear()
    monkeypatch.setattr("app.services.presupuesto_service.verificar_alertas_presupuesto", lambda db, pres, per, uso, **kw: evaluados.append(pres.id))
    nss._job_notificaciones_presupuestos(TestSessionMaker)
    assert len(evaluados) == 2


def test_r2_resumen_cierre_ciclo(db_session: Session, monkeypatch):
    u1 = _crear_usuario(db_session, "u1")
    u2 = _crear_usuario(db_session, "u2")
    cfg1 = ConfiguracionNotificacion(id=uuid4(), usuario_id=u1.id)
    cfg2 = ConfiguracionNotificacion(id=uuid4(), usuario_id=u2.id)
    db_session.add_all([cfg1, cfg2])
    db_session.commit()

    engine = db_session.get_bind()
    TestSessionMaker = sessionmaker(bind=engine)
    monkeypatch.setattr("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.notificacion_scheduler_service.liberar_lock_job", lambda *a, **kw: None)

    ayer = date(2026, 11, 5)
    monkeypatch.setattr("app.services.notificacion_scheduler_service.hoy_argentina", lambda: date(2026, 11, 6))

    def fake_ciclo(user, ref):
        if user.id == u1.id:
            raise RuntimeError("Fallo u1 resumen")
        return (ayer - timedelta(days=30), ayer)

    monkeypatch.setattr("app.services.dashboard_service.get_ciclo_fechas", fake_ciclo)

    with pytest.raises(RuntimeError, match=r"^Resumen de ciclo: 1 de 2 con error$"):
        nss._job_resumen_cierre_ciclo(TestSessionMaker)

    # Sin fallas -> no lanza
    monkeypatch.setattr("app.services.dashboard_service.get_ciclo_fechas", lambda user, ref: (ayer - timedelta(days=30), ayer))
    nss._job_resumen_cierre_ciclo(TestSessionMaker)


def test_r2_resumen_semanal(db_session: Session, monkeypatch):
    u1 = _crear_usuario(db_session, "u1")
    _ = _crear_usuario(db_session, "u2")

    engine = db_session.get_bind()
    TestSessionMaker = sessionmaker(bind=engine)
    monkeypatch.setattr("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.notificacion_scheduler_service.liberar_lock_job", lambda *a, **kw: None)

    from app.services import notificacion_scheduler_service as nss_mod
    original_condicion_gasto = nss_mod.condicion_gasto

    def fake_condicion_gasto(usuario_id, **kw):
        if usuario_id == u1.id:
            raise RuntimeError("Fallo u1 semanal")
        return original_condicion_gasto(usuario_id, **kw)

    monkeypatch.setattr("app.services.notificacion_scheduler_service.condicion_gasto", fake_condicion_gasto)

    with pytest.raises(RuntimeError, match=r"^Resumen semanal: 1 de 2 con error$"):
        nss._job_resumen_semanal(TestSessionMaker)

    # Sin fallas -> no lanza
    monkeypatch.setattr("app.services.notificacion_scheduler_service.condicion_gasto", original_condicion_gasto)
    nss._job_resumen_semanal(TestSessionMaker)


def test_r2_consejos_diarios(db_session: Session, monkeypatch):
    _ = _crear_usuario(db_session, "u1")
    u2 = _crear_usuario(db_session, "u2")
    _ = _crear_usuario(db_session, "u3")

    engine = db_session.get_bind()
    TestSessionMaker = sessionmaker(bind=engine)
    monkeypatch.setattr("app.main.SessionLocal", TestSessionMaker)
    monkeypatch.setattr("app.main.intentar_tomar_lock_job", lambda *a, **kw: True)
    monkeypatch.setattr("app.main.liberar_lock_job", lambda *a, **kw: None)

    monkeypatch.setattr(
        "app.services.inflacion_esperada_service.inflacion_esperada",
        lambda *a, **kw: InflacionEsperada(tasas=[Decimal("2.0")], fuente_texto="REM", es_respaldo=False),
    )

    def fake_emitir(db, user, hoy, inflacion):
        if user.id == u2.id:
            raise RuntimeError("Fallo emitir u2")
        return None

    def fake_avisar(db, user, hoy):
        if user.id == u2.id:
            raise RuntimeError("Fallo avisar u2")
        return 0

    monkeypatch.setattr("app.services.consejos_service.emitir_si_corresponde", fake_emitir)
    monkeypatch.setattr("app.services.consejos_service.medir_pendientes", lambda *a, **kw: 0)
    monkeypatch.setattr("app.services.aviso_fin_de_mes_service.avisar_fin_de_mes", fake_avisar)

    with pytest.raises(RuntimeError, match=r"^Consejos: 1 de 3 con error$"):
        app_main._job_consejos_diarios()

    # Sin fallas -> no lanza
    monkeypatch.setattr("app.services.consejos_service.emitir_si_corresponde", lambda *a, **kw: None)
    monkeypatch.setattr("app.services.aviso_fin_de_mes_service.avisar_fin_de_mes", lambda *a, **kw: 0)
    app_main._job_consejos_diarios()


# ==============================================================================
# 3. R3: ACTUALIZAR IPC
# ==============================================================================

def test_r3_actualizar_ipc(monkeypatch):
    mock_db = MagicMock()
    monkeypatch.setattr("app.main.SessionLocal", lambda: mock_db)
    monkeypatch.setattr("app.main.intentar_tomar_lock_job", lambda *a, **kw: True)
    monkeypatch.setattr("app.main.liberar_lock_job", lambda *a, **kw: None)

    # 1. get_current_ipc devuelve None -> RuntimeError "IPC: no hay datos en la base ni respuesta de datos.gob.ar"
    monkeypatch.setattr("app.services.tools_service.get_current_ipc", lambda db: None)
    with pytest.raises(RuntimeError, match=r"^IPC: no hay datos en la base ni respuesta de datos\.gob\.ar$"):
        app_main._job_actualizar_ipc()

    # 2. get_current_ipc devuelve IPCCache con es_estimado True y fecha_dato "2026-08" -> RuntimeError "IPC: datos.gob.ar no respondió; queda el dato de 2026-08"
    cache_estimado = IPCCache(
        id=uuid4(),
        indice_acumulado=100.0,
        fecha_dato="2026-08",
        fecha_actualizacion=datetime.now(timezone.utc),
        fuente="datos.gob.ar",
        es_estimado=True,
    )
    cache_estimado.valor_mensual = 2.5
    monkeypatch.setattr("app.services.tools_service.get_current_ipc", lambda db: cache_estimado)
    with pytest.raises(RuntimeError, match=r"^IPC: datos\.gob\.ar no respondió; queda el dato de 2026-08$"):
        app_main._job_actualizar_ipc()

    # 3. con es_estimado False y fecha_dato "2026-08" -> no lanza
    cache_real = IPCCache(
        id=uuid4(),
        indice_acumulado=100.0,
        fecha_dato="2026-08",
        fecha_actualizacion=datetime.now(timezone.utc),
        fuente="datos.gob.ar",
        es_estimado=False,
    )
    cache_real.valor_mensual = 2.5
    monkeypatch.setattr("app.services.tools_service.get_current_ipc", lambda db: cache_real)
    app_main._job_actualizar_ipc()  # No lanza


# ==============================================================================
# 4. CONTROL ESTATICO AST
# ==============================================================================

def test_ast_control_raise_en_21_funciones():
    backend_dir = Path(__file__).resolve().parent.parent

    funciones_objetivo = [
        # app/main.py
        ("app/main.py", "_job_limpiar_tokens"),
        ("app/main.py", "_job_vencimientos_tarjetas"),
        ("app/main.py", "_job_renovar_presupuestos"),
        ("app/main.py", "_job_cobros_suscripciones"),
        ("app/main.py", "job_entrega_whatsapp_batched"),
        ("app/main.py", "_job_actualizar_perfiles"),
        ("app/main.py", "_job_consejos_diarios"),
        ("app/main.py", "_job_refresh_feriados"),
        ("app/main.py", "_job_guardar_cotizaciones_diarias"),
        ("app/main.py", "_job_actualizar_rem"),
        ("app/main.py", "_job_actualizar_ipc"),
        ("app/main.py", "_job_actualizar_tasas"),
        # app/services/notificacion_scheduler_service.py
        ("app/services/notificacion_scheduler_service.py", "_job_notificaciones_cuotas"),
        ("app/services/notificacion_scheduler_service.py", "_job_notificaciones_presupuestos"),
        ("app/services/notificacion_scheduler_service.py", "_job_notificaciones_suscripciones"),
        ("app/services/notificacion_scheduler_service.py", "_job_notificaciones_inactividad"),
        ("app/services/notificacion_scheduler_service.py", "_job_entrega_whatsapp_batched"),
        ("app/services/notificacion_scheduler_service.py", "_job_resumen_cierre_ciclo"),
        ("app/services/notificacion_scheduler_service.py", "_job_resumen_semanal"),
        # app/services/factura_avisos_service.py
        ("app/services/factura_avisos_service.py", "_job_notificaciones_facturas"),
        # app/services/notificacion_despacho_service.py
        ("app/services/notificacion_despacho_service.py", "_job_reintentar_inmediatas"),
    ]

    assert len(funciones_objetivo) == 21

    for rel_path, fn_name in funciones_objetivo:
        file_path = backend_dir / rel_path
        code = file_path.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(file_path))

        fn_node = None
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == fn_name:
                fn_node = item
                break

        assert fn_node is not None, f"Función {fn_name} no encontrada en {rel_path}"

        # Buscar el try del cuerpo principal de la función
        tries_principales = [n for n in fn_node.body if isinstance(n, ast.Try)]
        assert len(tries_principales) >= 1, f"No se encontró try principal en {fn_name}"

        for try_node in tries_principales:
            for handler in try_node.handlers:
                # Verificar si atrapa Exception
                es_exception = False
                if handler.type is None:
                    es_exception = True
                elif isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                    es_exception = True

                if es_exception:
                    # Debe tener un Raise en su cuerpo
                    raises = [n for n in ast.walk(handler) if isinstance(n, ast.Raise)]
                    assert len(raises) >= 1, f"Handler en {fn_name} ({rel_path}) no tiene Raise"

    # En _job_actualizar_ipc el texto "es_estimado" aparece
    ipc_code = (backend_dir / "app" / "main.py").read_text(encoding="utf-8")
    assert "es_estimado" in ipc_code
