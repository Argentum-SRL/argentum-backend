# -*- coding: utf-8 -*-
"""
Tests unitarios y de integración para "Me lo puedo permitir" con ingreso temprano (fase_ingreso_temprano).
Ubicación: tests/test_permitirse_temprano.py
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
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
from app.services.ingreso_habitual_service import FuenteIngreso, ResultadoIngresoHabitual
from app.services.permitirse_service import tope_por_ingreso_temprano
from app.services.permitirse_textos import textos_permitirse


# ==============================================================================
# T-9. Función pura tope_por_ingreso_temprano
# ==============================================================================

def test_t9_tope_por_ingreso_temprano():
    # ("bien","cuotas",True) → "justo"
    assert tope_por_ingreso_temprano("bien", "cuotas", True) == "justo"
    # ("bien","mensual",True) → "justo"
    assert tope_por_ingreso_temprano("bien", "mensual", True) == "justo"
    # ("bien","contado",True) → "bien"
    assert tope_por_ingreso_temprano("bien", "contado", True) == "bien"
    # ("bien","cuotas",False) → "bien"
    assert tope_por_ingreso_temprano("bien", "cuotas", False) == "bien"
    # ("ahorros","cuotas",True) → "ahorros"
    assert tope_por_ingreso_temprano("ahorros", "cuotas", True) == "ahorros"
    # ("no_alcanza","mensual",True) → "no_alcanza"
    assert tope_por_ingreso_temprano("no_alcanza", "mensual", True) == "no_alcanza"


# ==============================================================================
# T-10. Textos de permitirse con ingreso temprano y motivo sin ingreso
# ==============================================================================

def test_t10_textos_permitirse_temprano_y_sin_ingreso():
    # {"modo": "cuotas", "resultado": "justo", "motivo": "aprendiendo", "r_min": Decimal("508449")}
    res_aprendiendo = textos_permitirse({
        "modo": "cuotas",
        "resultado": "justo",
        "motivo": "aprendiendo",
        "r_min": Decimal("508449"),
    })
    assert res_aprendiendo["linea_principal"] == "Cada mes te quedarían $508.449, pero todavía estoy aprendiendo tus cobros."

    # {"modo": "mensual", "resultado": "justo", "motivo": "sin_ingreso", "motivo_ingreso": "irregular"}
    res_irregular = textos_permitirse({
        "modo": "mensual",
        "resultado": "justo",
        "motivo": "sin_ingreso",
        "motivo_ingreso": "irregular",
    })
    assert res_irregular["linea_principal"] == "Tus cobros cambian mucho de un mes a otro: no sé si te entra cada mes."

    # con motivo_ingreso "pocos_ciclos", "sin_ingresos" o None
    for mot in ("pocos_ciclos", "sin_ingresos", None):
        res_otros = textos_permitirse({
            "modo": "mensual",
            "resultado": "justo",
            "motivo": "sin_ingreso",
            "motivo_ingreso": mot,
        })
        assert res_otros["linea_principal"] == "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."


# ==============================================================================
# T-11. Integración de ruta /tools/can-afford con ingreso temprano
# ==============================================================================

@pytest.fixture(autouse=True)
def _setup_sqlite_compat(monkeypatch):
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


@pytest.fixture(name="db_t11")
def _db_fixture():
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


@pytest.fixture(name="usuario_caja_t11")
def _usuario_fixture(db_t11: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email="temprano_caja@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        telefono="+5491100000000",
    )
    db_t11.add(u)
    db_t11.flush()

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
    db_t11.add(caja)
    db_t11.commit()
    db_t11.refresh(u)
    return u


def test_t11_ruta_ingreso_temprano(db_t11: Session, usuario_caja_t11: Usuario):
    def override_get_db():
        yield db_t11

    def override_get_current_user():
        return usuario_caja_t11

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    mock_res_ingreso = ResultadoIngresoHabitual(
        moneda="ARS",
        tipo="temprano",
        monto=Decimal("10000000"),
        motivo_sin_datos=None,
        fuentes=[
            FuenteIngreso(
                categoria_id=None,
                subcategoria_id=None,
                categoria_nombre="Empleo",
                subcategoria_nombre="Sueldo",
                tipo="temprano",
                monto=Decimal("10000000"),
                ciclos_presentes=2,
            )
        ],
        es_temprano=True,
    )

    try:
        with patch("app.services.permitirse_service.obtener_ingreso_habitual", return_value=mock_res_ingreso) as mock_obtener:
            with TestClient(app) as client:
                # 1. POST /tools/can-afford {"precio_total": 1000, "modo": "mensual"}
                resp_mensual = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "mensual"})
                assert resp_mensual.status_code == 200
                data_m = resp_mensual.json()["data"]
                assert data_m["resultado"] != "bien"
                assert data_m["motivo"] == "aprendiendo"

                # Verificar que recibió incluir_temprano=True
                assert mock_obtener.called
                _, kwargs = mock_obtener.call_args
                assert kwargs.get("incluir_temprano") is True

                mock_obtener.reset_mock()

                # 2. POST /tools/can-afford {"precio_total": 1000, "modo": "contado"}
                resp_contado = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "contado"})
                assert resp_contado.status_code == 200
                data_c = resp_contado.json()["data"]
                assert data_c["motivo"] != "aprendiendo"
    finally:
        app.dependency_overrides.clear()


# ==============================================================================
# T-12. Fin de mes y otros servicios no usan el ingreso temprano
# ==============================================================================

def test_t12_otros_servicios_no_usan_ingreso_temprano():
    base_dir = Path(__file__).resolve().parents[1]
    serv_dir = base_dir / "app" / "services"

    archivos_sin_temprano = [
        "fin_de_mes_service.py",
        "consejos_datos.py",
        "consejos_reglas.py",
        "consejos_service.py",
        "patrones_service.py",
        "perfil_financiero_service.py",
        "tools_service.py",
    ]

    for nombre in archivos_sin_temprano:
        archivo = serv_dir / nombre
        texto = archivo.read_text(encoding="utf-8")
        assert "incluir_temprano" not in texto, f"{nombre} no debe contener 'incluir_temprano'"

    # permitirse_service.py lo contiene exactamente una vez
    permitirse_file = serv_dir / "permitirse_service.py"
    texto_permitirse = permitirse_file.read_text(encoding="utf-8")
    assert texto_permitirse.count("incluir_temprano") == 1

