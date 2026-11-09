# -*- coding: utf-8 -*-
"""
tests/test_permitirse_datos_viejos.py — Pruebas unitarias y de integración para datos viejos en "¿Me lo puedo permitir?".
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
from app.services import fin_de_mes_service
from app.services.frescura_service import Frescura
from app.services.ingreso_habitual_service import ResultadoIngresoHabitual
from app.services.permitirse_service import (
    DatosPermitirse,
    calcular_puede_permitirse,
    tope_por_datos_viejos,
)
from app.services.permitirse_textos import (
    armar_respuesta_permitirse,
    textos_permitirse,
)


# Configuración de compatibilidad SQLite para pruebas unitarias
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
        email="datos_viejos_user@argentum.com",
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


# T-1. tope_por_datos_viejos
def test_t1_tope_por_datos_viejos():
    assert tope_por_datos_viejos("bien", "datos_viejos") == "justo"
    assert tope_por_datos_viejos("bien", None) == "bien"
    assert tope_por_datos_viejos("bien", "aprendiendo") == "bien"
    assert tope_por_datos_viejos("ahorros", "datos_viejos") == "ahorros"
    assert tope_por_datos_viejos("no_alcanza", "datos_viejos") == "no_alcanza"
    assert tope_por_datos_viejos("justo", "datos_viejos") == "justo"


# T-2. textos_permitirse, cuotas, motivo "datos_viejos", sin_ingreso True
def test_t2_textos_cuotas_datos_viejos_sin_ingreso():
    # resultado "ahorros", precio 75000, libre 57500, falta 17500 → linea_principal "Con lo libre te faltan $17.500."
    res1 = textos_permitirse({
        "modo": "cuotas",
        "motivo": "datos_viejos",
        "sin_ingreso": True,
        "resultado": "ahorros",
        "precio": Decimal("75000"),
        "libre": Decimal("57500"),
        "falta": Decimal("17500"),
        "dias_sin_cargar": 13,
    })
    assert res1["linea_principal"] == "Con lo libre te faltan $17.500."
    assert res1["linea_frescura"] == "Ojo: hace 13 días que no cargás nada, puede no estar al día."
    assert "$0 en 0 meses" not in str(res1)

    # resultado "justo", motivo_ingreso None → "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."
    res2 = textos_permitirse({
        "modo": "cuotas",
        "motivo": "datos_viejos",
        "sin_ingreso": True,
        "resultado": "justo",
        "motivo_ingreso": None,
        "dias_sin_cargar": 13,
    })
    assert res2["linea_principal"] == "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."
    assert res2["linea_frescura"] == "Ojo: hace 13 días que no cargás nada, puede no estar al día."
    assert "$0 en 0 meses" not in str(res2)

    # resultado "justo", motivo_ingreso "irregular" → "Tus cobros cambian mucho de un mes a otro: no sé si te entra cada mes."
    res3 = textos_permitirse({
        "modo": "cuotas",
        "motivo": "datos_viejos",
        "sin_ingreso": True,
        "resultado": "justo",
        "motivo_ingreso": "irregular",
        "dias_sin_cargar": 13,
    })
    assert res3["linea_principal"] == "Tus cobros cambian mucho de un mes a otro: no sé si te entra cada mes."
    assert res3["linea_frescura"] == "Ojo: hace 13 días que no cargás nada, puede no estar al día."
    assert "$0 en 0 meses" not in str(res3)

    # resultado "no_alcanza", ahorros_total 0, falta 75000 → "Te faltan $75.000."
    res4 = textos_permitirse({
        "modo": "cuotas",
        "motivo": "datos_viejos",
        "sin_ingreso": True,
        "resultado": "no_alcanza",
        "ahorros_total": Decimal("0"),
        "falta": Decimal("75000"),
        "dias_sin_cargar": 13,
    })
    assert res4["linea_principal"] == "Te faltan $75.000."
    assert res4["linea_frescura"] == "Ojo: hace 13 días que no cargás nada, puede no estar al día."
    assert "$0 en 0 meses" not in str(res4)


# T-3. textos_permitirse con motivo "datos_viejos" y resultado "justo"
def test_t3_textos_justo_datos_viejos():
    # cuotas, sin_ingreso False, r_min 1241311 → "Cada mes te quedarían $1.241.311 después de tus gastos de siempre."
    res_cuotas = textos_permitirse({
        "modo": "cuotas",
        "motivo": "datos_viejos",
        "resultado": "justo",
        "sin_ingreso": False,
        "r_min": Decimal("1241311"),
    })
    assert res_cuotas["linea_principal"] == "Cada mes te quedarían $1.241.311 después de tus gastos de siempre."

    # mensual igual
    res_mensual = textos_permitirse({
        "modo": "mensual",
        "motivo": "datos_viejos",
        "resultado": "justo",
        "sin_ingreso": False,
        "r_min": Decimal("1241311"),
    })
    assert res_mensual["linea_principal"] == "Cada mes te quedarían $1.241.311 después de tus gastos de siempre."

    # contado, veces 0, n 0, libre 300000, precio 75000 → "Hoy te quedarían $225.000 libres."
    res_contado_0 = textos_permitirse({
        "modo": "contado",
        "motivo": "datos_viejos",
        "resultado": "justo",
        "veces": 0,
        "n": 0,
        "libre": Decimal("300000"),
        "precio": Decimal("75000"),
    })
    assert res_contado_0["linea_principal"] == "Hoy te quedarían $225.000 libres."

    # contado con veces 2, n 6 y fin_ciclo date(2026, 11, 30) → "En 2 de tus últimos 6 meses no te habría alcanzado hasta el 30/11."
    res_contado_hist = textos_permitirse({
        "modo": "contado",
        "motivo": "datos_viejos",
        "resultado": "justo",
        "veces": 2,
        "n": 6,
        "fin_ciclo": date(2026, 11, 30),
    })
    assert res_contado_hist["linea_principal"] == "En 2 de tus últimos 6 meses no te habría alcanzado hasta el 30/11."


# T-4. Ruta (copiá el armado de tests\test_permitirse_ruta.py; no importes tests)
def test_t4_ruta_datos_viejos(client: TestClient, monkeypatch):
    frescura_ficticia = Frescura(
        ultima_carga=date(2026, 11, 7),
        dias_sin_cargar=13,
        umbral_dias=7,
        intervalos=5,
        datos_viejos=True,
    )
    monkeypatch.setattr(fin_de_mes_service, "calcular_frescura", lambda *a, **k: frescura_ficticia)

    ingreso_regular = ResultadoIngresoHabitual(
        moneda="ARS",
        tipo="regular",
        monto=Decimal("10000000"),
        motivo_sin_datos=None,
        es_temprano=False,
    )
    monkeypatch.setattr(
        "app.services.permitirse_service.obtener_ingreso_habitual",
        lambda *a, **k: ingreso_regular,
    )

    # POST /tools/can-afford {"precio_total": 1000, "modo": "mensual"} y {"precio_total": 1000, "modo": "contado"}
    # → en los dos, resultado distinto de "bien", motivo "datos_viejos" y linea_frescura no vacía.
    resp_m = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "mensual"})
    assert resp_m.status_code == 200
    data_m = resp_m.json()["data"]
    assert data_m["resultado"] != "bien"
    assert data_m["motivo"] == "datos_viejos"
    assert data_m["linea_frescura"]

    resp_c = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "contado"})
    assert resp_c.status_code == 200
    data_c = resp_c.json()["data"]
    assert data_c["resultado"] != "bien"
    assert data_c["motivo"] == "datos_viejos"
    assert data_c["linea_frescura"]

    # Con obtener_ingreso_habitual devolviendo sin_datos (monto None, motivo "sin_ingresos") y {"precio_total": 1000, "modo": "mensual"}:
    # linea_principal "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."
    ingreso_sin_datos = ResultadoIngresoHabitual(
        moneda="ARS",
        tipo="sin_datos",
        monto=None,
        motivo_sin_datos="sin_ingresos",
        es_temprano=False,
    )
    monkeypatch.setattr(
        "app.services.permitirse_service.obtener_ingreso_habitual",
        lambda *a, **k: ingreso_sin_datos,
    )

    resp_sin_ing = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "mensual"})
    assert resp_sin_ing.status_code == 200
    data_sin_ing = resp_sin_ing.json()["data"]
    assert (
        data_sin_ing["linea_principal"]
        == "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."
    )


# T-4b. Motor con datos armados a mano
def test_t4b_motor_mock_datos_viejos(db: Session, usuario_caja_270k: Usuario, monkeypatch):
    datos_mock = DatosPermitirse(
        libre=Decimal("300000"),
        fin_ciclo=date(2026, 11, 30),
        estado_fdm="datos_viejos",
        finales=(Decimal("300000"),) * 6,
        dias_sin_cargar=13,
        ingreso=Decimal("1000000"),
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
        lambda *a, **k: datos_mock,
    )

    # calcular_puede_permitirse con 1000 en "mensual" y en "contado" → resultado "justo" y motivo "datos_viejos" en los dos.
    res_m = calcular_puede_permitirse(
        user_id=usuario_caja_270k.id,
        precio_total=1000,
        modo="mensual",
        db=db,
    )
    assert res_m["resultado"] == "justo"
    assert res_m["motivo"] == "datos_viejos"

    res_c = calcular_puede_permitirse(
        user_id=usuario_caja_270k.id,
        precio_total=1000,
        modo="contado",
        db=db,
    )
    assert res_c["resultado"] == "justo"
    assert res_c["motivo"] == "datos_viejos"

    # El MISMO DatosPermitirse con estado_fdm "completo" → "bien" en los dos (prueba que el caso distingue).
    datos_mock_completo = DatosPermitirse(
        libre=Decimal("300000"),
        fin_ciclo=date(2026, 11, 30),
        estado_fdm="completo",
        finales=(Decimal("300000"),) * 6,
        dias_sin_cargar=0,
        ingreso=Decimal("1000000"),
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
        lambda *a, **k: datos_mock_completo,
    )

    res_m_comp = calcular_puede_permitirse(
        user_id=usuario_caja_270k.id,
        precio_total=1000,
        modo="mensual",
        db=db,
    )
    assert res_m_comp["resultado"] == "bien"

    res_c_comp = calcular_puede_permitirse(
        user_id=usuario_caja_270k.id,
        precio_total=1000,
        modo="contado",
        db=db,
    )
    assert res_c_comp["resultado"] == "bien"


# T-5. WhatsApp
def test_t5_whatsapp_datos_viejos_sin_ingreso(client: TestClient, monkeypatch):
    frescura_ficticia = Frescura(
        ultima_carga=date(2026, 11, 7),
        dias_sin_cargar=13,
        umbral_dias=7,
        intervalos=5,
        datos_viejos=True,
    )
    monkeypatch.setattr(fin_de_mes_service, "calcular_frescura", lambda *a, **k: frescura_ficticia)

    ingreso_sin_datos = ResultadoIngresoHabitual(
        moneda="ARS",
        tipo="sin_datos",
        monto=None,
        motivo_sin_datos="sin_ingresos",
        es_temprano=False,
    )
    monkeypatch.setattr(
        "app.services.permitirse_service.obtener_ingreso_habitual",
        lambda *a, **k: ingreso_sin_datos,
    )

    resp_sin_ing = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "mensual"})
    assert resp_sin_ing.status_code == 200
    data_sin_ing = resp_sin_ing.json()["data"]

    consulta = {
        "precio": Decimal("1000"),
        "modo": "mensual",
        "cantidad_cuotas": 1,
        "tiene_interes": False,
        "tna": None,
    }
    texto_wpp = armar_respuesta_permitirse(data_sin_ing, consulta)

    # ninguna línea pasa de 90 caracteres y la respuesta contiene la línea de frescura.
    for linea in texto_wpp.split("\n"):
        assert len(linea) <= 90, f"Línea excede 90 caracteres ({len(linea)}): {linea}"
    assert data_sin_ing["linea_frescura"] in texto_wpp
    assert "Ojo: hace 13 días que no cargás nada, puede no estar al día." in texto_wpp

