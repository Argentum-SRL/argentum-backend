"""
Tests de integración y unidad para el subsistema de Scheduler Admin (fase_e8_scheduler).
Verifica:
1. registrar_ejecucion sobre función exitosa -> 1 fila 'ok', duracion_ms >= 0, detalle null.
2. registrar_ejecucion sobre función que lanza ValueError("x") -> 1 fila 'error', detalle 'ValueError: x', relanza.
3. registrar_ejecucion con lock no obtenido -> 1 fila 'omitido'.
4. registrar_ejecucion sobre función async -> 1 fila 'ok'.
5. Cálculo de estado: Atrasado, Al día, Falló, Sin datos.
6. GET /v1/admin/scheduler (403 no admin; 200 admin con 22 ítems, scheduler_activo false, proxima_corrida null).
7. POST /v1/admin/scheduler/{job_id}/correr (400 no permitido; 409 scheduler apagado; 202 con modify y next_run_time).
8. Vigilante: 2 jobs atrasados -> enviar_alerta_admin 2 llamadas; segunda corrida mismo día -> 0 nuevas.
9. Vigilante borra fila de hace 15 días y preserva la de hace 13 días.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta, date
import sqlite3
from unittest.mock import MagicMock, patch
import uuid
import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, types
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import get_current_admin_user
from app.core.database import Base, get_db
from app.core.job_lock import intentar_tomar_lock_job
from app.core.jobs_catalogo import JOBS_CATALOGO
from app.core.registro_jobs import (
    calcular_estado_job,
    registrar_ejecucion,
    vigilar_tareas,
    _reset_alertas_vigilante,
)
from app.main import app
from app.models.ejecucion_job import EjecucionJob
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda

# Compatibilidad de tipos Postgres en SQLite
if not hasattr(PGUUID, "_compiler_dispatcher") or "sqlite" not in getattr(PGUUID._compiler_dispatcher, "specs", {}):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if not hasattr(JSONB, "_compiler_dispatcher") or "sqlite" not in getattr(JSONB._compiler_dispatcher, "specs", {}):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

current_test_admin: Usuario | None = None


def override_get_current_admin_user():
    if current_test_admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
        )
    if not current_test_admin.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés permiso para hacer eso.",
        )
    return current_test_admin


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_sqlite_and_db(monkeypatch):
    global current_test_admin
    current_test_admin = None
    monkeypatch.setitem(app.dependency_overrides, get_db, override_get_db)
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, override_get_current_admin_user)

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
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSessionLocal)

    # Regla 38: Soporte SQLite para comparaciones aware/naive en synchronize_session del evaluador
    from sqlalchemy.orm.evaluator import _EvaluatorCompiler
    orig_straight_evaluate = _EvaluatorCompiler._straight_evaluate

    def _safe_straight_evaluate(self, operator, eval_left, eval_right, clause):
        orig_eval = orig_straight_evaluate(self, operator, eval_left, eval_right, clause)
        def evaluate(obj):
            try:
                return orig_eval(obj)
            except TypeError as e:
                if "offset-naive and offset-aware" in str(e):
                    left_val = eval_left(obj)
                    right_val = eval_right(obj)
                    if isinstance(left_val, datetime) and isinstance(right_val, datetime):
                        if left_val.tzinfo is None and right_val.tzinfo is not None:
                            left_val = left_val.replace(tzinfo=timezone.utc)
                        elif right_val.tzinfo is None and left_val.tzinfo is not None:
                            right_val = right_val.replace(tzinfo=timezone.utc)
                        return operator(left_val, right_val)
                raise
        return evaluate

    monkeypatch.setattr(_EvaluatorCompiler, "_straight_evaluate", _safe_straight_evaluate)
    monkeypatch.setattr(_EvaluatorCompiler, "visit_lt_binary_op", _safe_straight_evaluate)
    monkeypatch.setattr(_EvaluatorCompiler, "visit_le_binary_op", _safe_straight_evaluate)
    monkeypatch.setattr(_EvaluatorCompiler, "visit_gt_binary_op", _safe_straight_evaluate)
    monkeypatch.setattr(_EvaluatorCompiler, "visit_ge_binary_op", _safe_straight_evaluate)
    monkeypatch.setattr(_EvaluatorCompiler, "visit_eq_binary_op", _safe_straight_evaluate)
    monkeypatch.setattr(_EvaluatorCompiler, "visit_ne_binary_op", _safe_straight_evaluate)

    Base.metadata.create_all(bind=engine)
    _reset_alertas_vigilante()

    yield

    with engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.commit()
    _reset_alertas_vigilante()


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def admin_user(db_session: Session):
    user = Usuario(
        id=uuid.uuid4(),
        email="admin@argentum.com",
        password_hash="fakehash",
        nombre="Admin",
        rol=RolUsuario.ADMIN,
        is_admin=True,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        auth_provider=AuthProvider.EMAIL,
        onboarding_completado=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def regular_user(db_session: Session):
    user = Usuario(
        id=uuid.uuid4(),
        email="usuario@argentum.com",
        password_hash="fakehash",
        nombre="Usuario Común",
        rol=RolUsuario.USUARIO,
        is_admin=False,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        auth_provider=AuthProvider.EMAIL,
        onboarding_completado=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: registrar_ejecucion sobre función que termina bien
# ---------------------------------------------------------------------------
def test_1_registrar_ejecucion_ok(db_session):
    def mi_job_ok():
        return 42

    wrapped = registrar_ejecucion("test_job_ok", mi_job_ok)
    res = wrapped()
    assert res == 42

    filas = db_session.query(EjecucionJob).filter_by(job_id="test_job_ok").all()
    assert len(filas) == 1
    fila = filas[0]
    assert fila.resultado == "ok"
    assert fila.duracion_ms >= 0
    assert fila.detalle is None


# ---------------------------------------------------------------------------
# Test 2: registrar_ejecucion sobre función que lanza ValueError("x")
# ---------------------------------------------------------------------------
def test_2_registrar_ejecucion_error(db_session):
    def mi_job_falla():
        raise ValueError("x")

    wrapped = registrar_ejecucion("test_job_error", mi_job_falla)

    with pytest.raises(ValueError) as excinfo:
        wrapped()
    assert str(excinfo.value) == "x"

    filas = db_session.query(EjecucionJob).filter_by(job_id="test_job_error").all()
    assert len(filas) == 1
    fila = filas[0]
    assert fila.resultado == "error"
    assert fila.detalle == "ValueError: x"


# ---------------------------------------------------------------------------
# Test 3: registrar_ejecucion sobre función que no consigue el lock
# ---------------------------------------------------------------------------
def test_3_registrar_ejecucion_omitido(db_session):
    # Parchear el camino normal de advisory lock para simular postgres devolviendo False
    with patch("app.core.job_lock.engine.dialect.name", "postgresql"):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.scalar.return_value = False
        with patch("app.core.job_lock.engine.connect", return_value=mock_conn):
            def mi_job_con_lock():
                db = TestingSessionLocal()
                try:
                    if not intentar_tomar_lock_job(db, "test_job_omitido"):
                        return
                    # Trabajo si tuviera lock
                finally:
                    db.close()

            wrapped = registrar_ejecucion("test_job_omitido", mi_job_con_lock)
            wrapped()

    filas = db_session.query(EjecucionJob).filter_by(job_id="test_job_omitido").all()
    assert len(filas) == 1
    fila = filas[0]
    assert fila.resultado == "omitido"
    assert fila.duracion_ms >= 0


# ---------------------------------------------------------------------------
# Test 4: registrar_ejecucion sobre función async
# ---------------------------------------------------------------------------
def test_4_registrar_ejecucion_async(db_session):
    async def mi_job_async():
        await asyncio.sleep(0.001)
        return "exito_async"

    wrapped = registrar_ejecucion("test_job_async", mi_job_async)
    res = asyncio.run(wrapped())
    assert res == "exito_async"

    filas = db_session.query(EjecucionJob).filter_by(job_id="test_job_async").all()
    assert len(filas) == 1
    fila = filas[0]
    assert fila.resultado == "ok"
    assert fila.duracion_ms >= 0


# ---------------------------------------------------------------------------
# Test 5: cálculo de estado
# ---------------------------------------------------------------------------
def test_5_calculo_estado(db_session):
    ahora = datetime(2026, 10, 13, 15, 0, 0, tzinfo=timezone.utc)
    periodo = 60

    # A. Sin filas -> "Sin datos"
    assert calcular_estado_job("job_vacio", periodo, db_session, ahora) == "Sin datos"

    # B. Última corrida "error" -> "Falló"
    db_session.add(EjecucionJob(
        job_id="job_fallo",
        inicio=ahora - timedelta(minutes=10),
        fin=ahora - timedelta(minutes=9),
        resultado="error",
        duracion_ms=100,
        detalle="Exception: crash",
    ))
    db_session.commit()
    assert calcular_estado_job("job_fallo", periodo, db_session, ahora) == "Falló"

    # C. Último ok hace 3 horas (> 2 x 60 min = 120 min) -> "Atrasado"
    db_session.add(EjecucionJob(
        job_id="job_atrasado",
        inicio=ahora - timedelta(hours=3),
        fin=ahora - timedelta(hours=3) + timedelta(seconds=2),
        resultado="ok",
        duracion_ms=2000,
    ))
    db_session.commit()
    assert calcular_estado_job("job_atrasado", periodo, db_session, ahora) == "Atrasado"

    # D. Último ok hace 30 minutos (<= 120 min) -> "Al día"
    db_session.add(EjecucionJob(
        job_id="job_al_dia",
        inicio=ahora - timedelta(minutes=30),
        fin=ahora - timedelta(minutes=30) + timedelta(seconds=1),
        resultado="ok",
        duracion_ms=1000,
    ))
    db_session.commit()
    assert calcular_estado_job("job_al_dia", periodo, db_session, ahora) == "Al día"


# ---------------------------------------------------------------------------
# Test 6: GET /v1/admin/scheduler
# ---------------------------------------------------------------------------
def test_6_get_admin_scheduler(client, admin_user, regular_user):
    global current_test_admin

    # Usuario no admin -> 403
    current_test_admin = regular_user
    res_no_admin = client.get("/v1/admin/scheduler")
    assert res_no_admin.status_code == status.HTTP_403_FORBIDDEN

    # Usuario admin -> 200 con 22 ítems, scheduler_activo false y proxima_corrida null
    current_test_admin = admin_user
    app.state.scheduler = None

    res_admin = client.get("/v1/admin/scheduler")
    assert res_admin.status_code == status.HTTP_200_OK

    data = res_admin.json()
    assert data["scheduler_activo"] is False
    assert len(data["jobs"]) == 22
    assert all(j["proxima_corrida"] is None for j in data["jobs"])

    # Validar que los 22 jobs correspondan exactamente al catálogo
    job_ids_recibidos = [j["job_id"] for j in data["jobs"]]
    job_ids_esperados = [j.job_id for j in JOBS_CATALOGO]
    assert job_ids_recibidos == job_ids_esperados


# ---------------------------------------------------------------------------
# Test 7: POST /v1/admin/scheduler/{job_id}/correr
# ---------------------------------------------------------------------------
def test_7_post_correr_job(client, admin_user):
    global current_test_admin
    current_test_admin = admin_user

    # A. Job no permitido a mano -> 400
    res_no_permitido = client.post("/v1/admin/scheduler/cobros_suscripciones/correr")
    assert res_no_permitido.status_code == status.HTTP_400_BAD_REQUEST
    msg_400 = res_no_permitido.json().get("error", {}).get("message") or res_no_permitido.json().get("detail")
    assert msg_400 == "Esta tarea no se puede correr a mano."

    # B. Job permitido con scheduler apagado -> 409
    app.state.scheduler = None
    res_apagado = client.post("/v1/admin/scheduler/guardar_cotizaciones_diarias/correr")
    assert res_apagado.status_code == status.HTTP_409_CONFLICT
    msg_409 = res_apagado.json().get("error", {}).get("message") or res_apagado.json().get("detail")
    assert msg_409 == "Las tareas automáticas están apagadas en este servidor."

    # C. Con scheduler activo y mock get_job -> 202 y modify llamado con next_run_time
    class FakeJob:
        def __init__(self):
            self.modify_calls = []

        def modify(self, **kwargs):
            self.modify_calls.append(kwargs)

    class FakeScheduler:
        running = True

        def __init__(self):
            self.fake_job = FakeJob()

        def get_job(self, job_id):
            if job_id == "guardar_cotizaciones_diarias":
                return self.fake_job
            return None

    fake_sched = FakeScheduler()
    app.state.scheduler = fake_sched

    res_ok = client.post("/v1/admin/scheduler/guardar_cotizaciones_diarias/correr")
    assert res_ok.status_code == status.HTTP_202_ACCEPTED
    assert res_ok.json() == {"mensaje": "Se va a correr en unos segundos."}
    assert len(fake_sched.fake_job.modify_calls) == 1
    assert "next_run_time" in fake_sched.fake_job.modify_calls[0]
    assert isinstance(fake_sched.fake_job.modify_calls[0]["next_run_time"], datetime)


# ---------------------------------------------------------------------------
# Test 8: Vigilante con dos jobs atrasados
# ---------------------------------------------------------------------------
def test_8_vigilante_alertas(db_session):
    ahora = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

    # Insertar 2 jobs atrasados (> 2 x periodo)
    # job 1: limpiar_refresh_tokens (periodo 360 -> atrasado tras 720 min = 12h)
    db_session.add(EjecucionJob(
        job_id="limpiar_refresh_tokens",
        inicio=ahora - timedelta(hours=20),
        fin=ahora - timedelta(hours=20) + timedelta(seconds=5),
        resultado="ok",
        duracion_ms=5000,
    ))
    # job 2: guardar_cotizaciones_diarias (periodo 60 -> atrasado tras 120 min = 2h)
    db_session.add(EjecucionJob(
        job_id="guardar_cotizaciones_diarias",
        inicio=ahora - timedelta(hours=5),
        fin=ahora - timedelta(hours=5) + timedelta(seconds=2),
        resultado="ok",
        duracion_ms=2000,
    ))
    db_session.commit()

    with patch("app.core.registro_jobs.enviar_alerta_admin") as mock_alerta, \
         patch("app.core.registro_jobs.hoy_argentina", return_value=date(2026, 10, 13)):

        mock_alerta.return_value = True

        # Primera corrida del vigilante -> 2 alertas enviadas
        vigilar_tareas(db=db_session)
        assert mock_alerta.call_count == 2

        # Segunda corrida en el mismo día -> 0 alertas adicionales
        vigilar_tareas(db=db_session)
        assert mock_alerta.call_count == 2


# ---------------------------------------------------------------------------
# Test 9: Vigilante borra fila de 15 días y preserva la de 13 días
# ---------------------------------------------------------------------------
def test_9_vigilante_purgado_antiguedad(db_session):
    ahora = datetime.now(timezone.utc)

    # Fila vieja (15 días atrás)
    fila_vieja = EjecucionJob(
        id=uuid.uuid4(),
        job_id="job_antiguo",
        inicio=ahora - timedelta(days=15),
        fin=ahora - timedelta(days=15) + timedelta(seconds=1),
        resultado="ok",
        duracion_ms=1000,
    )
    # Fila reciente (13 días atrás)
    fila_reciente = EjecucionJob(
        id=uuid.uuid4(),
        job_id="job_reciente",
        inicio=ahora - timedelta(days=13),
        fin=ahora - timedelta(days=13) + timedelta(seconds=1),
        resultado="ok",
        duracion_ms=1000,
    )
    db_session.add(fila_vieja)
    db_session.add(fila_reciente)
    db_session.commit()

    id_viejo = fila_vieja.id
    id_reciente = fila_reciente.id

    with patch("app.core.registro_jobs.enviar_alerta_admin"):
        vigilar_tareas(db=db_session)

    # Comprobar que solo sobrevivió la de 13 días
    assert db_session.query(EjecucionJob).filter_by(id=id_viejo).first() is None
    assert db_session.query(EjecucionJob).filter_by(id=id_reciente).first() is not None
