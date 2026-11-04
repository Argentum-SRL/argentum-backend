# -*- coding: utf-8 -*-
"""
tests/test_inflacion_esperada.py — Pruebas unitarias y de integración de inflación esperada (REM e INDEC).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.expectativa_inflacion import ExpectativaInflacion
from app.models.tools import IPCCache
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.inflacion_esperada_service import (
    bajar_rem,
    inflacion_esperada,
    leer_rem_xlsx,
    tasas_desde_rem,
)
from app.services.permitirse_service import calcular_puede_permitirse


# Setup SQLite compatibility para pruebas unitarias
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

    import sqlite3
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# 1. Puras: tasas_desde_rem
# ---------------------------------------------------------------------------

def test_tasas_desde_rem_con_prox12():
    # tasas_desde_rem([("2026-10", 2,2), ("2026-11", 2,0), ("2026-12", 1,8)], 24, "2026-11", 4) → [2,0000; 1,8000; 1,8088; 1,8088]
    mensuales = [
        ("2026-10", Decimal("2.2")),
        ("2026-11", Decimal("2.0")),
        ("2026-12", Decimal("1.8")),
    ]
    tasas = tasas_desde_rem(mensuales, Decimal("24"), "2026-11", 4)
    assert tasas == [
        Decimal("2.0000"),
        Decimal("1.8000"),
        Decimal("1.8088"),
        Decimal("1.8088"),
    ]


def test_tasas_desde_rem_sin_prox12():
    # sin prox12 → [2,0; 1,8; 1,8; 1,8]
    mensuales = [
        ("2026-10", Decimal("2.2")),
        ("2026-11", Decimal("2.0")),
        ("2026-12", Decimal("1.8")),
    ]
    tasas = tasas_desde_rem(mensuales, None, "2026-11", 4)
    assert tasas == [
        Decimal("2.0000"),
        Decimal("1.8000"),
        Decimal("1.8000"),
        Decimal("1.8000"),
    ]


def test_tasas_desde_rem_sin_mensuales_con_prox12():
    # sin mensuales con prox12 24 y 3 meses → [1,8088]×3
    tasas = tasas_desde_rem([], Decimal("24"), "2026-11", 3)
    assert tasas == [Decimal("1.8088"), Decimal("1.8088"), Decimal("1.8088")]


def test_tasas_desde_rem_sin_nada():
    # sin nada → None
    tasas = tasas_desde_rem([], None, "2026-11", 4)
    assert tasas is None


# ---------------------------------------------------------------------------
# 2. Pura: leer_rem_xlsx con fixture
# ---------------------------------------------------------------------------

def test_leer_rem_xlsx_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "rem_2026_09.xlsx"
    contenido = fixture_path.read_bytes()

    relevamiento, mensuales, prox12 = leer_rem_xlsx(contenido)

    assert relevamiento == "2026-09"
    assert prox12 == Decimal("21.05")
    assert len(mensuales) == 7
    assert mensuales[0] == ("2026-09", Decimal("1.9"))
    assert mensuales[1] == ("2026-10", Decimal("1.7"))
    assert mensuales[2] == ("2026-11", Decimal("1.615"))
    assert mensuales[3] == ("2026-12", Decimal("1.8"))
    assert mensuales[4] == ("2027-01", Decimal("1.6"))
    assert mensuales[5] == ("2027-02", Decimal("1.6"))
    assert mensuales[6] == ("2027-03", Decimal("1.7"))


# ---------------------------------------------------------------------------
# 3. Integración con SQLite: inflacion_esperada
# ---------------------------------------------------------------------------

def test_inflacion_esperada_rem_vigente(db: Session):
    # relevamiento 2026-09 guardado y hoy 03/11/2026 → fuente_texto "REM de septiembre", es_respaldo False, enviar_alerta_admin no se llama
    ahora = datetime.now(timezone.utc)
    db.add(ExpectativaInflacion(
        id=uuid4(),
        relevamiento="2026-09",
        periodo="2026-12",
        valor=Decimal("1.80"),
        fuente="REM BCRA",
        fecha_actualizacion=ahora,
    ))
    db.add(ExpectativaInflacion(
        id=uuid4(),
        relevamiento="2026-09",
        periodo="prox12",
        valor=Decimal("21.05"),
        fuente="REM BCRA",
        fecha_actualizacion=ahora,
    ))
    db.commit()

    with patch("app.services.inflacion_esperada_service.enviar_alerta_admin") as mock_alerta:
        res = inflacion_esperada(db, hoy=date(2026, 11, 3), meses=4)
        assert res is not None
        assert res.fuente_texto == "REM de septiembre"
        assert res.es_respaldo is False
        assert len(res.tasas) == 4
        mock_alerta.assert_not_called()


def test_inflacion_esperada_respaldo_indec(db: Session):
    # relevamiento 2026-07 y un IPC 2026-09 → fuente_texto "inflación INDEC de septiembre", es_respaldo True, enviar_alerta_admin llamado 1 vez con clave "rem_respaldo"
    ahora = datetime.now(timezone.utc)
    db.add(ExpectativaInflacion(
        id=uuid4(),
        relevamiento="2026-07",
        periodo="2026-08",
        valor=Decimal("2.50"),
        fuente="REM BCRA",
        fecha_actualizacion=ahora,
    ))
    db.add(IPCCache(
        fecha_dato="2026-08",
        indice_acumulado=100.0,
        es_estimado=False,
    ))
    db.add(IPCCache(
        fecha_dato="2026-09",
        indice_acumulado=103.5,
        es_estimado=False,
    ))
    db.commit()

    with patch("app.services.inflacion_esperada_service.enviar_alerta_admin") as mock_alerta:
        res = inflacion_esperada(db, hoy=date(2026, 11, 3), meses=4)
        assert res is not None
        assert res.fuente_texto == "inflación INDEC de septiembre"
        assert res.es_respaldo is True
        assert res.tasas == [Decimal("3.5000")] * 4
        mock_alerta.assert_called_once()
        assert mock_alerta.call_args[1].get("clave") == "rem_respaldo"


def test_inflacion_esperada_sin_datos(db: Session):
    # sin REM ni IPC → None
    res = inflacion_esperada(db, hoy=date(2026, 11, 3), meses=4)
    assert res is None


# ---------------------------------------------------------------------------
# 3b. Seguridad y contención: sin red ni commit (Corrección 1.c)
# ---------------------------------------------------------------------------

def test_inflacion_esperada_sin_red_ni_commit(db: Session, monkeypatch):
    """
    Control de seguridad (Corrección 1.c):
    inflacion_esperada con IPC 2026-09 guardado (y sin REM vigente):
    -> fuente_texto 'inflación INDEC de septiembre', 0 llamadas de red, 0 commits, 0 flush.
    """
    fecha_vieja = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    db.add(IPCCache(
        fecha_dato="2026-08",
        indice_acumulado=100.0,
        es_estimado=False,
        fecha_actualizacion=fecha_vieja,
    ))
    db.add(IPCCache(
        fecha_dato="2026-09",
        indice_acumulado=103.5,
        es_estimado=False,
        fecha_actualizacion=fecha_vieja,
    ))
    db.commit()

    commit_count = 0
    flush_count = 0
    orig_commit = db.commit
    orig_flush = db.flush

    def tracked_commit():
        nonlocal commit_count
        commit_count += 1
        return orig_commit()

    def tracked_flush():
        nonlocal flush_count
        flush_count += 1
        return orig_flush()

    monkeypatch.setattr(db, "commit", tracked_commit)
    monkeypatch.setattr(db, "flush", tracked_flush)

    def fail_network(*args, **kwargs):
        raise RuntimeError("LLAMADA DE RED NO PERMITIDA")

    monkeypatch.setattr("requests.get", fail_network)
    monkeypatch.setattr("httpx.Client.get", fail_network)
    monkeypatch.setattr("httpx.get", fail_network)

    with patch("app.services.inflacion_esperada_service.enviar_alerta_admin"):
        res = inflacion_esperada(db, hoy=date(2026, 11, 3), meses=4)

    assert res is not None
    assert res.fuente_texto == "inflación INDEC de septiembre"
    assert res.es_respaldo is True
    assert commit_count == 0
    assert flush_count == 0


def test_calcular_puede_permitirse_cuotas_sin_red_ni_commit(db: Session, monkeypatch):
    """
    Control de seguridad (Corrección 1.c):
    calcular_puede_permitirse en modo cuotas (usuario del test de ruta):
    -> 0 llamadas de red, 0 commits, 0 flush.
    """
    u = Usuario(
        id=uuid4(),
        email="sin_ingreso_cuotas_seguridad@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        telefono="+5491100000001",
    )
    db.add(u)
    db.flush()

    caja = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Caja",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("270000"),
        saldo_inicial=Decimal("270000"),
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
    )
    db.add(caja)

    fecha_vieja = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    db.add(IPCCache(
        fecha_dato="2026-08",
        indice_acumulado=100.0,
        es_estimado=False,
        fecha_actualizacion=fecha_vieja,
    ))
    db.add(IPCCache(
        fecha_dato="2026-09",
        indice_acumulado=103.5,
        es_estimado=False,
        fecha_actualizacion=fecha_vieja,
    ))
    db.commit()

    commit_count = 0
    flush_count = 0
    orig_commit = db.commit
    orig_flush = db.flush

    def tracked_commit():
        nonlocal commit_count
        commit_count += 1
        return orig_commit()

    def tracked_flush():
        nonlocal flush_count
        flush_count += 1
        return orig_flush()

    monkeypatch.setattr(db, "commit", tracked_commit)
    monkeypatch.setattr(db, "flush", tracked_flush)

    def fail_network(*args, **kwargs):
        raise RuntimeError("LLAMADA DE RED NO PERMITIDA")

    monkeypatch.setattr("requests.get", fail_network)
    monkeypatch.setattr("httpx.Client.get", fail_network)
    monkeypatch.setattr("httpx.get", fail_network)

    with patch("app.services.inflacion_esperada_service.enviar_alerta_admin"):
        res = calcular_puede_permitirse(
            user_id=u.id,
            precio_total=100000,
            modo="cuotas",
            cantidad_cuotas=6,
            tiene_interes=False,
            tna=0,
            db=db,
        )

    assert res["modo"] == "cuotas"
    assert commit_count == 0
    assert flush_count == 0


# ---------------------------------------------------------------------------
# 4. Integración: bajar_rem con httpx parcheado
# ---------------------------------------------------------------------------

def test_bajar_rem_descarga_exitosa(db: Session):
    fixture_path = Path(__file__).parent / "fixtures" / "rem_2026_09.xlsx"
    contenido = fixture_path.read_bytes()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = contenido

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        bajar_rem(db, hoy=date(2026, 10, 15))

    filas = db.query(ExpectativaInflacion).filter(
        ExpectativaInflacion.relevamiento == "2026-09"
    ).all()
    assert len(filas) == 8  # 7 mensuales + prox12


def test_bajar_rem_falla_con_alerta(db: Session):
    # falla las dos URLs con hoy 12/11/2026 y último relevamiento 2026-08 → alerta con clave "rem_sin_datos"
    ahora = datetime.now(timezone.utc)
    db.add(ExpectativaInflacion(
        id=uuid4(),
        relevamiento="2026-08",
        periodo="2026-09",
        valor=Decimal("2.00"),
        fuente="REM BCRA",
        fecha_actualizacion=ahora,
    ))
    db.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.content = b""

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("app.services.inflacion_esperada_service.enviar_alerta_admin") as mock_alerta:
            bajar_rem(db, hoy=date(2026, 11, 12))
            mock_alerta.assert_called_once()
            assert mock_alerta.call_args[1].get("clave") == "rem_sin_datos"


def test_bajar_rem_falla_antes_del_dia_10_sin_alerta(db: Session):
    # falla con hoy 05/11/2026 → sin alerta
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.content = b""

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("app.services.inflacion_esperada_service.enviar_alerta_admin") as mock_alerta:
            bajar_rem(db, hoy=date(2026, 11, 5))
            mock_alerta.assert_not_called()


def test_bajar_rem_ya_existe_no_llama_httpx(db: Session):
    # ya hay relevamiento 2026-10 con hoy 12/11/2026 → httpx no se llama
    ahora = datetime.now(timezone.utc)
    db.add(ExpectativaInflacion(
        id=uuid4(),
        relevamiento="2026-10",
        periodo="2026-11",
        valor=Decimal("2.00"),
        fuente="REM BCRA",
        fecha_actualizacion=ahora,
    ))
    db.commit()

    with patch("httpx.Client") as mock_client_cls:
        bajar_rem(db, hoy=date(2026, 11, 12))
        mock_client_cls.assert_not_called()
