# -*- coding: utf-8 -*-
"""
tests/test_permitirse_espera.py — Pruebas de la regla D11: línea de espera de 24 horas en "¿Me lo puedo permitir?".
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.permitirse_service import DatosPermitirse
from app.services.permitirse_textos import (
    LINEA_ESPERA,
    armar_respuesta_permitirse,
    textos_permitirse,
)


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


@pytest.fixture(name="usuario_caja_270k")
def usuario_caja_270k_fixture(db: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email="espera_caja@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        telefono="+5491100000000",
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
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_caja_270k: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_caja_270k

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_textos_permitirse_espera():
    # contado "justo" (n 6, veces 2, fin 30/11/2026) → linea_espera
    res_justo = textos_permitirse({
        "modo": "contado",
        "resultado": "justo",
        "n": 6,
        "veces": 2,
        "fin_ciclo": date(2026, 11, 30),
    })
    assert res_justo["linea_espera"] == "Si no es urgente, esperá 24 horas y volvé a fijarte."
    assert res_justo["linea_espera"] == LINEA_ESPERA

    # contado "ahorros" → la misma
    res_ahorros = textos_permitirse({
        "modo": "contado",
        "resultado": "ahorros",
        "deficit": Decimal("10000"),
    })
    assert res_ahorros["linea_espera"] == "Si no es urgente, esperá 24 horas y volvé a fijarte."

    # contado "bien" → None
    res_bien = textos_permitirse({
        "modo": "contado",
        "resultado": "bien",
        "libre": Decimal("500000"),
        "precio": Decimal("1000"),
    })
    assert res_bien["linea_espera"] is None

    # contado "no_alcanza" → None
    res_no = textos_permitirse({
        "modo": "contado",
        "resultado": "no_alcanza",
        "deficit": Decimal("50000"),
    })
    assert res_no["linea_espera"] is None

    # cuotas "justo" con motivo "sin_ingreso" → la línea
    res_cuotas_justo = textos_permitirse({
        "modo": "cuotas",
        "resultado": "justo",
        "motivo": "sin_ingreso",
    })
    assert res_cuotas_justo["linea_espera"] == "Si no es urgente, esperá 24 horas y volvé a fijarte."

    # mensual "ahorros" → la línea
    res_mensual_ahorros = textos_permitirse({
        "modo": "mensual",
        "resultado": "ahorros",
        "deficit_mensual": Decimal("30000"),
        "meses_alcanzan": 6,
    })
    assert res_mensual_ahorros["linea_espera"] == "Si no es urgente, esperá 24 horas y volvé a fijarte."

    # cuotas "bien" → None
    res_cuotas_bien = textos_permitirse({
        "modo": "cuotas",
        "resultado": "bien",
        "cantidad_cuotas": 6,
    })
    assert res_cuotas_bien["linea_espera"] is None


def test_armar_respuesta_permitirse_espera():
    resultado = {
        "titulo": "Te entra justo",
        "lineas_plan": ["Son 6 cuotas de $100.000."],
        "linea_principal": "Te quedarían solo $15.000 por mes después de tus gastos de siempre.",
        "linea_espera": "Si no es urgente, esperá 24 horas y volvé a fijarte.",
    }
    consulta = {"cantidad_cuotas": 6, "nota_interes": True}
    esperado = (
        "Te entra justo.\n"
        "Son 6 cuotas de $100.000.\n"
        "Te quedarían solo $15.000 por mes después de tus gastos de siempre.\n"
        "Si no es urgente, esperá 24 horas y volvé a fijarte.\n"
        "Lo calculé sin interés. Si tiene interés, decime la tasa anual (TNA)."
    )
    obtenido = armar_respuesta_permitirse(resultado, consulta)
    assert obtenido == esperado

    # Con linea_frescura y linea_espera, la frescura va antes que la espera
    resultado_frescura = {
        **resultado,
        "linea_frescura": "Ojo: hace 13 días que no cargás nada, puede no estar al día.",
    }
    obtenido_f = armar_respuesta_permitirse(resultado_frescura, consulta)
    idx_frescura = obtenido_f.index("Ojo: hace 13 días que no cargás nada, puede no estar al día.")
    idx_espera = obtenido_f.index("Si no es urgente, esperá 24 horas y volvé a fijarte.")
    assert idx_frescura < idx_espera

    # Ninguna línea de más de 90 caracteres
    for linea in obtenido_f.split("\n"):
        assert len(linea) <= 90, f"Línea excede 90 caracteres ({len(linea)}): {linea}"


def test_ruta_can_afford_espera(client: TestClient, monkeypatch):
    # Caso que da "justo"
    resp_justo = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "contado"})
    assert resp_justo.status_code == 200
    data_justo = resp_justo.json()["data"]
    assert data_justo["resultado"] == "justo"
    assert data_justo["linea_espera"] == "Si no es urgente, esperá 24 horas y volvé a fijarte."

    consulta = {
        "precio": Decimal("1000"),
        "modo": "contado",
        "cantidad_cuotas": 1,
        "tiene_interes": False,
        "tna": None,
    }
    wpp_justo = armar_respuesta_permitirse(data_justo, consulta)
    assert wpp_justo.endswith("\nSi no es urgente, esperá 24 horas y volvé a fijarte.")

    # Caso "bien"
    datos_mock_bien = DatosPermitirse(
        libre=Decimal("500000"),
        fin_ciclo=date(2026, 11, 30),
        estado_fdm="completo",
        finales=(Decimal("500000"), Decimal("500000")),
        dias_sin_cargar=0,
        ingreso=Decimal("600000"),
        es_admin=False,
        suscripciones=Decimal("0"),
        fijos=Decimal("0"),
        gasto_variable=Decimal("0"),
        cuotas_futuras=[],
        ciclos_futuros=[],
        liberaciones=[],
        margenes=[Decimal("500000")] * 12,
        colchon=Decimal("100000"),
        inversiones=Decimal("0"),
        dolares_usd=Decimal("0"),
        dolares_en_pesos=Decimal("0"),
        metas=Decimal("0"),
        ahorros_total=Decimal("0"),
        rubros_medianas={},
        ingreso_temprano=False,
        motivo_ingreso=None,
    )
    monkeypatch.setattr(
        "app.services.permitirse_service.recolectar",
        lambda *a, **k: datos_mock_bien,
    )

    resp_bien = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "contado"})
    assert resp_bien.status_code == 200
    data_bien = resp_bien.json()["data"]
    assert data_bien["resultado"] == "bien"
    assert data_bien["linea_espera"] is None

    wpp_bien = armar_respuesta_permitirse(data_bien, consulta)
    assert "Si no es urgente, esperá 24 horas y volvé a fijarte." not in wpp_bien
