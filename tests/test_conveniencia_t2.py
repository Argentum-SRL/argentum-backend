# -*- coding: utf-8 -*-
"""tests/test_conveniencia_t2.py — Pruebas unitarias y API de T2 (TC1 a TC9)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import sqlite3
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
from app.core.reglas_texto_web import problemas_texto_web
from app.core.reglas_texto_wpp import problemas_texto_wpp
from app.main import app
from app.models.expectativa_inflacion import ExpectativaInflacion
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.schemas.tools import InstallmentConvenienceRequest
from app.services.conveniencia_service import comparar_cuotas_contado, textos_conveniencia
from app.services.cuotas_service import texto_detalle_precio_contado
from app.services.inflacion_esperada_service import inflacion_esperada
from app.services.tools_service import calcular_conveniencia_cuotas, calcular_plan_cuotas
from app.utils.fecha import TZ_ARGENTINA

HOY_FIJO = date(2026, 11, 17)


@pytest.fixture(autouse=True)
def _compat_sqlite(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    orig_uuid = types.Uuid.bind_processor

    def _safe_uuid(self, dialect):
        proc = orig_uuid(self, dialect)
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

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid)
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    for mod in (
        "app.utils.fecha",
        "app.services.tools_service",
        "app.services.inflacion_esperada_service",
    ):
        monkeypatch.setattr(f"{mod}.hoy_argentina", lambda: HOY_FIJO, raising=False)

    yield

    if not had_sqlite:
        if (
            hasattr(JSONB, "_compiler_dispatcher")
            and hasattr(JSONB._compiler_dispatcher, "specs")
        ):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db")
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


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email="conveniencia_t2@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=datetime(2026, 3, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_test: Usuario):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario_test
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _cargar_expectativas_fijas(db: Session, tasa: Decimal = Decimal("2.0")) -> None:
    """Inserta filas fijas de ExpectativaInflacion para los próximos 12 meses."""
    for m_idx in range(1, 13):
        anio = HOY_FIJO.year + (HOY_FIJO.month + m_idx - 1) // 12
        mes = (HOY_FIJO.month + m_idx - 1) % 12 + 1
        db.add(ExpectativaInflacion(
            relevamiento="2026-10",
            periodo=f"{anio:04d}-{mes:02d}",
            valor=tasa,
        ))
    db.commit()


def test_tc1_conviene_contado():
    res = comparar_cuotas_contado(
        Decimal("500000"),
        12,
        Decimal("50000"),
        [Decimal("2")],
    )
    assert res["total"] == Decimal("600000.00")
    assert res["vp"] == Decimal("528767.06")
    assert res["resultado"] == "conviene_contado"
    assert res["extra"] == Decimal("100000.00")
    assert res["extra_pct"] == 20
    txt = textos_conveniencia(
        res["resultado"],
        12,
        Decimal("50000"),
        res["total"],
        res["extra"],
        res["extra_pct"],
        res["vp"],
        "la inflación que se espera",
    )
    assert txt["titulo"] == "Te conviene de contado."
    assert (
        txt["linea_2"]
        == "12 cuotas de $50.000 son $600.000: $100.000 más que de contado (20%)."
    )
    assert (
        txt["linea_3"]
        == "Con la inflación que se espera, en plata de hoy son unos $529.000."
    )


def test_tc2_mismo_nominal_conviene_cuotas():
    res = comparar_cuotas_contado(
        Decimal("600000"),
        12,
        Decimal("50000"),
        [Decimal("2")],
    )
    assert res["resultado"] == "conviene_cuotas"
    assert res["extra"] == Decimal("0.00")
    txt = textos_conveniencia(
        res["resultado"],
        12,
        Decimal("50000"),
        res["total"],
        res["extra"],
        res["extra_pct"],
        res["vp"],
        "la inflación que se espera",
    )
    assert txt["titulo"] == "Te conviene en cuotas."
    assert txt["linea_2"] == "12 cuotas de $50.000 son $600.000, lo mismo que de contado."
    assert (
        txt["linea_3"]
        == "Con la inflación que se espera, en plata de hoy son unos $529.000."
    )


def test_tc3_inflacion_variable_mes_a_mes():
    inf = [
        Decimal("2.5"),
        Decimal("2.4"),
        Decimal("2.3"),
        Decimal("2.2"),
        Decimal("2.1"),
        Decimal("2.0"),
    ]
    res = comparar_cuotas_contado(Decimal("280000"), 6, Decimal("50000"), inf)
    assert res["vp"] == Decimal("276925.82")
    assert res["resultado"] == "conviene_cuotas"
    assert res["extra"] == Decimal("20000.00")
    assert res["extra_pct"] == 7
    txt = textos_conveniencia(
        res["resultado"],
        6,
        Decimal("50000"),
        res["total"],
        res["extra"],
        res["extra_pct"],
        res["vp"],
        "la inflación que se espera",
    )
    assert (
        txt["linea_2"]
        == "6 cuotas de $50.000 son $300.000: $20.000 más que de contado (7%)."
    )
    assert (
        txt["linea_3"]
        == "Con la inflación que se espera, en plata de hoy son unos $277.000."
    )


def test_tc4_indiferente_bajo_umbral_1_pct():
    inf = [
        Decimal("2.5"),
        Decimal("2.4"),
        Decimal("2.3"),
        Decimal("2.2"),
        Decimal("2.1"),
        Decimal("2.0"),
    ]
    res = comparar_cuotas_contado(Decimal("278000"), 6, Decimal("50000"), inf)
    assert res["resultado"] == "indiferente"
    txt = textos_conveniencia(
        res["resultado"],
        6,
        Decimal("50000"),
        res["total"],
        res["extra"],
        res["extra_pct"],
        res["vp"],
        "la inflación que se espera",
    )
    assert txt["titulo"] == "Da casi lo mismo."
    assert (
        txt["linea_2"]
        == "6 cuotas de $50.000 son $300.000: $22.000 más que de contado (8%)."
    )


def test_tc5_total_menor_que_contado():
    res = comparar_cuotas_contado(
        Decimal("320000"),
        6,
        Decimal("50000"),
        [Decimal("2")],
    )
    assert res["vp"] == Decimal("280071.54")
    assert res["resultado"] == "conviene_cuotas"
    txt = textos_conveniencia(
        res["resultado"],
        6,
        Decimal("50000"),
        res["total"],
        res["extra"],
        res["extra_pct"],
        res["vp"],
        "la inflación que se espera",
    )
    assert (
        txt["linea_2"]
        == "6 cuotas de $50.000 son $300.000: $20.000 menos que de contado."
    )


def test_tc6_sin_interes_completa_valor_cuota():
    req = InstallmentConvenienceRequest(
        precio_contado=300000,
        cantidad_cuotas=6,
        sin_interes=True,
        inflacion_mensual=2,
    )
    res = calcular_conveniencia_cuotas(req)
    assert res["valor_cuota"] == 50000.0
    assert res["precio_total_cuotas_nominal"] == 300000.0


def test_tc7_con_tna_60_usa_calcular_plan_cuotas():
    req = InstallmentConvenienceRequest(
        precio_contado=600000,
        cantidad_cuotas=6,
        tiene_interes=True,
        tna=60,
        inflacion_mensual=2,
    )
    res = calcular_conveniencia_cuotas(req)
    esperada = calcular_plan_cuotas(Decimal("600000"), 6, Decimal("60")).monto_cuota
    assert Decimal(str(res["valor_cuota"])) == esperada


def test_tc8_api_campos_nuevos_y_viejos_e_inflacion_propia(client: TestClient, db: Session):
    _cargar_expectativas_fijas(db, Decimal("2.0"))

    r_auto = client.post(
        "/api/v1/tools/installment-convenience",
        json={
            "precio_contado": 500000,
            "cantidad_cuotas": 12,
            "valor_cuota": 50000,
        },
    )
    assert r_auto.status_code == 200, r_auto.text
    data = r_auto.json()["data"]
    assert data["resultado"] == "conviene_contado"
    assert data["valor_cuota"] == 50000.0
    assert data["extra"] == 100000.0
    assert data["extra_pct"] == 20
    assert data["fuente_inflacion"] == "la inflación que se espera"
    assert data["inflaciones_usadas"] == [2.0] * 12
    assert data["titulo"] == "Te conviene de contado."
    assert data["costo_real_cuotas"] == 528767.06
    assert data["precio_total_cuotas_nominal"] == 600000.0
    assert "ahorro_real" in data
    assert "porcentaje_ahorro" in data
    assert "monto_cuota" in data
    assert "detalle_por_mes" in data

    r_propia = client.post(
        "/api/v1/tools/installment-convenience",
        json={
            "precio_contado": 500000,
            "cantidad_cuotas": 12,
            "valor_cuota": 50000,
            "inflacion_mensual": 3,
        },
    )
    assert r_propia.status_code == 200, r_propia.text
    data_p = r_propia.json()["data"]
    assert data_p["inflaciones_usadas"] == [3.0]
    assert data_p["fuente_inflacion"] == "la inflación que pusiste"


def test_tc9_reglas_texto_wpp_y_web(db: Session):
    _cargar_expectativas_fijas(db, Decimal("2.0"))
    inf_rem = inflacion_esperada(db, HOY_FIJO, 6)
    assert inf_rem is not None
    casos = [
        (Decimal("500000"), 12, Decimal("50000"), [Decimal("2")]),
        (Decimal("600000"), 12, Decimal("50000"), [Decimal("2")]),
        (
            Decimal("280000"),
            6,
            Decimal("50000"),
            [Decimal("2.5"), Decimal("2.4"), Decimal("2.3")],
        ),
        (
            Decimal("278000"),
            6,
            Decimal("50000"),
            [Decimal("2.5"), Decimal("2.4"), Decimal("2.3")],
        ),
        (Decimal("320000"), 6, Decimal("50000"), [Decimal("2")]),
    ]
    fuentes = [
        inf_rem.fuente_texto,
        "la última inflación del INDEC",
        "la inflación que pusiste",
    ]
    for contado, cant, cuota, inf in casos:
        r = comparar_cuotas_contado(contado, cant, cuota, inf)
        for fuente in fuentes:
            txt = textos_conveniencia(
                r["resultado"],
                cant,
                cuota,
                r["total"],
                r["extra"],
                r["extra_pct"],
                r["vp"],
                fuente,
            )
            bloque = "\n".join([txt["titulo"], txt["linea_2"], txt["linea_3"]])
            assert problemas_texto_wpp(bloque) == [], f"WPP: {bloque}"
            assert problemas_texto_web(bloque) == [], f"WEB: {bloque}"
            for ln in bloque.split("\n"):
                assert len(ln) <= 90, f"Línea > 90: {ln}"

    pregunta_wpp = (
        "Decime cuánto es cada cuota o si son sin interés. Por ejemplo: _12 cuotas de 50 mil_"
    )
    assert problemas_texto_wpp(pregunta_wpp) == []
    assert len(pregunta_wpp) <= 90

    detalles_contado = [
        texto_detalle_precio_contado(Decimal("960000"), Decimal("800000")),
        texto_detalle_precio_contado(Decimal("800000"), Decimal("800000")),
        texto_detalle_precio_contado(Decimal("750000"), Decimal("800000")),
    ]
    for det in detalles_contado:
        assert det is not None
        assert problemas_texto_wpp(det) == []
        assert problemas_texto_web(det) == []
        assert len(det) <= 90
