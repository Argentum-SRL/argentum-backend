# -*- coding: utf-8 -*-
"""
tests/test_contrato_numero.py - Pruebas del contrato de número para Disponible libre
(fase_g2_saldo_confianza).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app as fastapi_app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.enriquecedores import _fmt, enriquecer_respuesta_por_intent
from app.services.contrato_numero_service import (
    cobertura_usuario,
    contrato_disponible_libre,
)
from app.services.frescura_service import calcular_frescura
from app.utils.fecha import hoy_argentina
import app.services.resumen_tarjeta_service
import app.utils.fecha
app.services.resumen_tarjeta_service.hoy_argentina = lambda: app.utils.fecha.hoy_argentina()


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


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db: Session) -> Usuario:
    usuario = Usuario(
        id=uuid4(),
        email="test_contrato@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_test: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_test

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


def test_contrato_numero_resumen_completo_y_whatsapp(
    client: TestClient, db: Session, usuario_test: Usuario
):
    """
    Usuario con "Caja" (ARS, $270.000, activa) y una billetera de inversión con $50.000, sin compromisos.
    GET /dashboard/resumen-completo →
      resumen.saldo_disponible.ars.saldo_disponible = 270000 y contrato_disponible.ars.valor = 270000,
      base_hasta = resumen.periodo.fecha_fin, base_desde = resumen.periodo.fecha_inicio,
      fecha_corte = hoy_argentina() en ISO,
      supuestos = ["Suma solo billeteras activas que no son de inversión.", "Descuenta lo que vence hasta el <dd/mm de periodo.fecha_fin>."],
      cobertura null y contrato_disponible.ars.frescura = frescura = calcular_frescura(db, usuario.id).a_dict().
    enriquecer_respuesta_por_intent("consultar_saldo", ...) contiene _fmt(valor del contrato).
    """
    hoy = hoy_argentina()

    caja = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Caja",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("270000.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=hoy,
    )
    inversion = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="FCI",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000.00"),
        es_inversion=True,
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=hoy,
    )
    db.add_all([caja, inversion])
    db.commit()

    resp = client.get("/dashboard/resumen-completo")
    assert resp.status_code == 200
    data = resp.json()

    # Claves de primer nivel de la respuesta
    assert "saldo_disponible" in data
    assert "contrato_disponible" in data
    assert "frescura" in data
    assert "resumen" in data

    # Verificación de montos
    assert Decimal(str(data["saldo_disponible"]["ars"]["saldo_disponible"])) == Decimal("270000")
    assert Decimal(str(data["contrato_disponible"]["ars"]["valor"])) == Decimal("270000")

    # Verificación de fechas
    periodo_fin = data["resumen"]["periodo"]["fecha_fin"]
    periodo_inicio = data["resumen"]["periodo"]["fecha_inicio"]
    assert data["contrato_disponible"]["ars"]["base_hasta"] == periodo_fin
    assert data["contrato_disponible"]["ars"]["base_desde"] == periodo_inicio
    assert data["contrato_disponible"]["ars"]["fecha_corte"] == hoy.isoformat()

    # Supuestos
    fecha_fin_date = date.fromisoformat(periodo_fin)
    dd_mm = fecha_fin_date.strftime("%d/%m")
    esperado_supuestos = [
        "Suma solo billeteras activas que no son de inversión.",
        f"Descuenta lo que vence hasta el {dd_mm}.",
    ]
    assert data["contrato_disponible"]["ars"]["supuestos"] == esperado_supuestos

    # Cobertura null
    assert data["contrato_disponible"]["ars"]["cobertura"] is None

    # Frescura consistente
    frescura_esperada = calcular_frescura(db, usuario_test.id).a_dict()
    assert data["contrato_disponible"]["ars"]["frescura"] == frescura_esperada
    assert data["frescura"] == frescura_esperada

    # WhatsApp: enriquecer_respuesta_por_intent("consultar_saldo", ...)
    resultado_ia: dict = {}
    enriquecer_respuesta_por_intent(
        intent_detectado="consultar_saldo",
        resultado_ia=resultado_ia,
        mensaje_texto="cuanto saldo tengo",
        usuario=usuario_test,
        db=db,
    )
    fmt_contrato = _fmt(float(data["contrato_disponible"]["ars"]["valor"]))
    assert fmt_contrato in resultado_ia["respuesta_usuario"]


def test_contrato_disponible_libre_cero_llamadas_recalculo(
    db: Session, usuario_test: Usuario
):
    """
    contrato_disponible_libre llamado directo con un dict disponible armado a mano y
    con calcular_disponible_libre y listar_pagos_proximos parcheados con contadores
    → 0 llamadas a los dos.
    """
    from app.services.frescura_service import Frescura

    hoy = hoy_argentina()
    disponible_mock = {
        "ars": {"saldo_disponible": Decimal("150000.00")},
        "usd": {"saldo_disponible": Decimal("300.00")},
    }
    frescura_mock = Frescura(
        ultima_carga=hoy,
        dias_sin_cargar=0,
        umbral_dias=7,
        intervalos=5,
        datos_viejos=False,
    )

    with patch("app.services.pagos_proximos_service.calcular_disponible_libre") as mock_calc_disp, \
         patch("app.services.pagos_proximos_service.listar_pagos_proximos") as mock_listar_pagos:

        res = contrato_disponible_libre(
            db=db,
            usuario=usuario_test,
            hoy=hoy,
            disponible=disponible_mock,
            frescura=frescura_mock,
        )

        assert mock_calc_disp.call_count == 0
        assert mock_listar_pagos.call_count == 0

    assert res["ars"].valor == Decimal("150000.00")
    assert res["usd"].valor == Decimal("300.00")


def test_cobertura_usuario_filtro_y_minimo(db: Session, usuario_test: Usuario):
    """
    cobertura_usuario con calcular_cobertura parcheada para devolver:
      - {mostrar: True, por_cada_100: 80}
      - {mostrar: True, por_cada_100: 60}
      - {mostrar: False, por_cada_100: 20}
    en tres billeteras activas no de inversión, y:
      - {mostrar: True, por_cada_100: 10}
    en una de inversión → resultado esperado: 60 (menor porcentaje entre las activas no inversión con mostrar True).
    """
    hoy = hoy_argentina()

    b1 = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera 1",
        moneda=Moneda.ARS,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b2 = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera 2",
        moneda=Moneda.ARS,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b3 = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera 3",
        moneda=Moneda.ARS,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_inv = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Inversión",
        moneda=Moneda.ARS,
        es_inversion=True,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b1, b2, b3, b_inv])
    db.commit()

    mapa_cobertura = {
        b1.id: {"mostrar": True, "por_cada_100": 80},
        b2.id: {"mostrar": True, "por_cada_100": 60},
        b3.id: {"mostrar": False, "por_cada_100": 20},
        b_inv.id: {"mostrar": True, "por_cada_100": 10},
    }

    def fake_calcular_cobertura(session: Session, billetera_id: uuid.UUID, hoy: date):
        return mapa_cobertura.get(billetera_id, {"mostrar": False, "por_cada_100": None})

    with patch("app.services.ajuste_saldo_service.calcular_cobertura", side_effect=fake_calcular_cobertura):
        resultado = cobertura_usuario(db, usuario_test.id, hoy)
        assert resultado == 60

