"""
tests/test_tasas.py — Suite de pruebas para tasas automáticas, niveles, topes,
rendimientos por saldo diario y correcciones C1 a C7 de fase4a_2.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.core.entidades import inferir_entidad, opciones_de_entidad

from app.main import app
from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.tasa_entidad import TasaEntidad
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.billetera import BilleteraUpdate
from app.services.ajuste_saldo_service import (
    detectar_huecos,
    eliminar_ajuste,
    es_diferencia_grande,
    mediana_salidas_semanales,
    previsualizar_control,
)
from app.services.rendimiento_billetera_service import calcular_rendimiento_estimado
from app.services.tasas_service import (
    actualizar_tasas,
    rendimiento_por_saldos,
    saldos_diarios,
    tasa_efectiva,
)
from app.routers.billeteras import create_billetera, update_billetera, CrearBilleteraRequest
from app.utils.fecha import hoy_argentina
from scripts.local.paso import verificar_resultado_refresco


# -----------------------------------------------------------------------------
# FIXTURES
# -----------------------------------------------------------------------------
@pytest.fixture(name="db_session", scope="function")
def db_session_fixture(monkeypatch):
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

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


def _crear_usuario(db: Session, email="test@argentum.com") -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email,
        nombre="Usuario",
        apellido="Test",
        telefono="+5491100000000",
        telefono_normalizado="5491100000000",
        password_hash="fakehash",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# -----------------------------------------------------------------------------
# D1. inferir_entidad
# -----------------------------------------------------------------------------
def test_d1_inferir_entidad():
    assert inferir_entidad("Mercado Pago") == "mercadopago"
    assert inferir_entidad("MercadoPago") == "mercadopago"
    assert inferir_entidad("Ualá") == "uala"
    assert inferir_entidad("Naranja X") == "naranjax"
    assert inferir_entidad("MP Sebas") is None


# -----------------------------------------------------------------------------
# D2. actualizar_tasas con MockTransport
# -----------------------------------------------------------------------------
def test_d2_actualizar_tasas_mock(db_session):
    hoy = hoy_argentina()

    data_otros = [
        {"fondo": "UALA", "tna": 0.19, "tope": 1000000, "fecha": str(hoy), "condicionesCorto": "Saldo rinde 19%"},
        {"fondo": "UALA PLUS 2", "tna": 0.24, "tope": 0, "fecha": str(hoy), "condicionesCorto": "Plus 2"},
        {"fondo": "BRUBANK", "tna": 0.27, "tope": 750000, "fecha": "2025-08-06", "condicionesCorto": ""},
    ]

    data_mm_u = [
        {"fondo": "Mercado Fondo - Clase A", "fecha": "2026-10-01", "vcp": 25830.224}
    ]

    data_mm_p = [
        {"fondo": "Mercado Fondo - Clase A", "fecha": "2026-09-30", "vcp": 25816.425}
    ]

    def mock_handler(request: httpx.Request):
        url = str(request.url)
        if "fci/otros/ultimo" in url:
            return httpx.Response(200, json=data_otros)
        elif "mercadoDinero/ultimo" in url:
            return httpx.Response(200, json=data_mm_u)
        elif "mercadoDinero/penultimo" in url:
            return httpx.Response(200, json=data_mm_p)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))

    # Primera corrida
    res1 = actualizar_tasas(db_session, cliente=client, commit=True)
    assert res1["argentinadatos_cuentas"]["nuevas"] == 3
    assert res1["argentinadatos_fci"]["nuevas"] == 1

    # Verificar datos en BD
    fila_uala = db_session.execute(
        select(TasaEntidad).where(TasaEntidad.clave == "UALA")
    ).scalar_one()
    assert fila_uala.tna == Decimal("19.0000")
    assert fila_uala.tope == Decimal("1000000")

    fila_mf = db_session.execute(
        select(TasaEntidad).where(TasaEntidad.clave == "Mercado Fondo - Clase A")
    ).scalar_one()
    assert fila_mf.tna == Decimal("19.5094")

    # Segunda corrida: no agrega filas nuevas
    res2 = actualizar_tasas(db_session, cliente=client, commit=True)
    assert res2["argentinadatos_cuentas"]["nuevas"] == 0
    assert res2["argentinadatos_fci"]["nuevas"] == 0
    assert res2["argentinadatos_cuentas"]["actualizadas"] == 3
    assert res2["argentinadatos_fci"]["actualizadas"] == 1


# -----------------------------------------------------------------------------
# D3. tasa_efectiva
# -----------------------------------------------------------------------------
def test_d3_tasa_efectiva():
    hoy = date(2026, 10, 2)

    tasas_mock = {
        "UALA": TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="UALA",
            tna=Decimal("19.0000"),
            tope=Decimal("1000000.00"),
            fecha_dato=date(2026, 10, 2),
        ),
        "UALA PLUS 2": TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="UALA PLUS 2",
            tna=Decimal("24.0000"),
            tope=None,
            fecha_dato=date(2026, 10, 2),
        ),
        "BRUBANK": TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="BRUBANK",
            tna=Decimal("27.0000"),
            tope=Decimal("750000.00"),
            fecha_dato=date(2025, 8, 6),
        ),
        "SUPERVIELLE": TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="SUPERVIELLE",
            tna=Decimal("14.0000"),
            tope=Decimal("1000000.00"),
            fecha_dato=date(2026, 10, 2),
        ),
    }

    # 1. Manual 27 manda sobre fuente
    b_manual = Billetera(
        nombre="Ualá de Seba",
        entidad_id="uala",
        tna=Decimal("27.00"),
        es_efectivo=False,
    )
    te_manual = tasa_efectiva(b_manual, tasas_mock, Decimal("500000.00"), hoy)
    assert te_manual.tna == Decimal("27.00")
    assert te_manual.origen == "manual"
    assert te_manual.tope is None

    # 2. Ualá base: 19 con tope 1.000.000; con nivel UALA PLUS 2: 24
    b_uala_base = Billetera(nombre="Ualá Base", entidad_id="uala", tna=None, nivel_tasa=None, es_efectivo=False)
    te_u_base = tasa_efectiva(b_uala_base, tasas_mock, Decimal("500000.00"), hoy)
    assert te_u_base.tna == Decimal("19.0000")
    assert te_u_base.tope == Decimal("1000000.00")
    assert te_u_base.origen == "automatica"

    b_uala_plus = Billetera(nombre="Ualá Plus", entidad_id="uala", tna=None, nivel_tasa="UALA PLUS 2", es_efectivo=False)
    te_u_plus = tasa_efectiva(b_uala_plus, tasas_mock, Decimal("500000.00"), hoy)
    assert te_u_plus.tna == Decimal("24.0000")
    assert te_u_plus.origen == "automatica"

    # 3. Supervielle sin nivel: sin tasa
    b_super = Billetera(nombre="Supervielle", entidad_id="supervielle", tna=None, nivel_tasa=None, es_efectivo=False)
    te_super = tasa_efectiva(b_super, tasas_mock, Decimal("500000.00"), hoy)
    assert te_super.tna is None
    assert te_super.origen is None

    # 4. Brubank con fecha 2025-08-06 y hoy 2026-10-02: vieja True y tna None
    b_brubank = Billetera(nombre="Brubank", entidad_id="brubank", tna=None, nivel_tasa=None, es_efectivo=False)
    te_brubank = tasa_efectiva(b_brubank, tasas_mock, Decimal("500000.00"), hoy)
    assert te_brubank.vieja is True
    assert te_brubank.tna is None
    assert te_brubank.fecha_dato == date(2025, 8, 6)

    # 5. Billetera de efectivo: sin tasa
    b_efectivo = Billetera(nombre="Efectivo", es_efectivo=True, tna=Decimal("20.00"))
    te_ef = tasa_efectiva(b_efectivo, tasas_mock, Decimal("100000.00"), hoy)
    assert te_ef.tna is None
    assert te_ef.origen is None


# -----------------------------------------------------------------------------
# D4. saldos_diarios y rendimiento_por_saldos
# -----------------------------------------------------------------------------
def test_d4_saldos_diarios_y_rendimiento():
    hoy = date(2026, 10, 10)
    desde = hoy - timedelta(days=10)  # 2026-09-30

    # Caso 1: saldo de hoy 100.000, ingreso de 20.000 el día hoy - 5, desde hoy - 10, tna 36.5 -> 900.00
    netos = {hoy - timedelta(days=5): Decimal("20000.00")}
    saldos_1 = saldos_diarios(Decimal("100000.00"), netos, desde, hoy)
    rend_1 = rendimiento_por_saldos(saldos_1, tna=Decimal("36.5"), tope=None)
    assert rend_1 == Decimal("900.00")

    # Caso 2: saldo constante 1.500.000, tna 19, tope 1.000.000, 10 días -> 5205.48
    saldos_2 = {hoy - timedelta(days=i): Decimal("1500000.00") for i in range(1, 11)}
    rend_2 = rendimiento_por_saldos(saldos_2, tna=Decimal("19.00"), tope=Decimal("1000000.00"))
    assert rend_2 == Decimal("5205.48")


# -----------------------------------------------------------------------------
# D5. calcular_rendimiento_estimado
# -----------------------------------------------------------------------------
def test_d5_calcular_rendimiento_estimado(db_session):
    u = _crear_usuario(db_session)
    hoy = hoy_argentina()
    hace_10_dias = datetime.combine(hoy - timedelta(days=10), time(12, 0), tzinfo=timezone.utc)

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Santander Ahorro",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        es_principal=False,
        es_efectivo=False,
        es_inversion=False,
        tna=Decimal("36.50"),
        fecha_ultimo_rendimiento=hace_10_dias,
        fecha_creacion=hace_10_dias,
    )
    db_session.add(b)
    db_session.commit()

    resp = calcular_rendimiento_estimado(db_session, u.id, b.id)
    esperado = (Decimal("100000.00") * Decimal("36.50") / Decimal("100") / Decimal("365") * Decimal("10")).quantize(
        Decimal("0.01")
    )
    assert resp.rendimiento_estimado == esperado
    assert resp.dias_transcurridos == 10
    assert resp.origen_tasa == "manual"
    assert resp.tiene_tna is True


# -----------------------------------------------------------------------------
# D6. update_billetera validaciones y preservación de fecha_ultimo_rendimiento
# -----------------------------------------------------------------------------
def test_d6_update_billetera_validaciones(db_session):
    u = _crear_usuario(db_session)
    dt_rend = datetime.now(timezone.utc) - timedelta(days=5)

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Mi Ualá",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        entidad_id="uala",
        tna=Decimal("20.00"),
        fecha_ultimo_rendimiento=dt_rend,
        fecha_creacion=datetime.now(timezone.utc) - timedelta(days=30),
    )
    db_session.add(b)
    db_session.commit()

    # 1. Nivel "UALA PLUS 9" en Ualá da 400 con texto exacto
    with pytest.raises(HTTPException) as exc_info:
        update_billetera(
            str(b.id),
            BilleteraUpdate(nivel_tasa="UALA PLUS 9"),
            db=db_session,
            current_user=u,
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Ese nivel no corresponde a esta billetera."

    # 2. bank_id "inexistente" da 400 "Esa entidad no existe."
    with pytest.raises(HTTPException) as exc_info_bank:
        update_billetera(
            str(b.id),
            BilleteraUpdate(bank_id="inexistente"),
            db=db_session,
            current_user=u,
        )
    assert exc_info_bank.value.status_code == 400
    assert exc_info_bank.value.detail == "Esa entidad no existe."

    # 3. tna None mantiene fecha_ultimo_rendimiento
    b_updated = update_billetera(
        str(b.id),
        BilleteraUpdate(tna=None),
        db=db_session,
        current_user=u,
    )
    assert b_updated.tna is None
    assert b_updated.fecha_ultimo_rendimiento is not None

    # 4. create_billetera con bank_id "inexistente" guarda entidad_id None
    b_creada = create_billetera(
        CrearBilleteraRequest(
            nombre="Billetera Rara",
            moneda=Moneda.ARS,
            bank_id="inexistente",
        ),
        db=db_session,
        current_user=u,
    )
    assert b_creada.entidad_id is None


# -----------------------------------------------------------------------------
# D7. listar_entidades con TestClient
# -----------------------------------------------------------------------------
def test_d7_listar_entidades_http(db_session):
    u = _crear_usuario(db_session)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u

    client = TestClient(app)
    resp = client.get("/billeteras/entidades")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 26  # 26 entidades exactamente como en banks.ts

    ids = [item["id"] for item in data]
    assert "mercadopago" in ids
    assert "uala" in ids
    assert "naranjax" in ids

    # Asegurarse que GET /billeteras/entidades no cayó en get_billetera (que buscaría UUID y daría 404)
    assert all("id" in item and "nombre" in item and "opciones" in item for item in data)

    app.dependency_overrides.clear()


# -----------------------------------------------------------------------------
# D8. Correcciones C1 a C6
# -----------------------------------------------------------------------------
def test_d8_correcciones_c1_a_c6(db_session):
    hoy = hoy_argentina()

    # C1: es_diferencia_grande con casos del test 9
    salida_tipica = Decimal("10500.00")
    assert es_diferencia_grande(Decimal("-9000.00"), semanas_con_historia=4, salida_semanal_tipica=salida_tipica) is False
    assert es_diferencia_grande(Decimal("-11000.00"), semanas_con_historia=4, salida_semanal_tipica=salida_tipica) is True
    assert es_diferencia_grande(Decimal("-9000.00"), semanas_con_historia=1, salida_semanal_tipica=salida_tipica) is True
    assert es_diferencia_grande(Decimal("0.00"), semanas_con_historia=4, salida_semanal_tipica=salida_tipica) is False

    # C2: detectar_huecos con desde = hoy y sin movimientos da []
    assert detectar_huecos([], desde=hoy, hoy=hoy) == []
    # desde = hoy - 5 sin movimientos da [(hoy - 5, hoy)]
    assert detectar_huecos([], desde=hoy - timedelta(days=5), hoy=hoy) == [(hoy - timedelta(days=5), hoy)]

    # C3: mediana de 10.000,01 y 10.000,02 da 10.000,02
    salidas = {
        hoy - timedelta(days=7): Decimal("10000.01"),  # k=1
        hoy - timedelta(days=14): Decimal("10000.02"), # k=2
    }
    mediana_val, sem_count = mediana_salidas_semanales(salidas, primer_movimiento=hoy - timedelta(days=20), hoy=hoy)
    assert sem_count == 2
    assert mediana_val == Decimal("10000.02")

    # C4: un egreso con fecha hoy + 10 no es ultimo_movimiento
    u = _crear_usuario(db_session, email="c4@argentum.com")
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Billetera C4",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        fecha_creacion=datetime.now(timezone.utc) - timedelta(days=30),
    )
    cat = Categoria(id=uuid4(), nombre="Varios", tipo=TipoCategoria.EGRESO)
    db_session.add_all([b, cat])
    db_session.commit()

    tx_futura = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        fecha=hoy + timedelta(days=10),
        descripcion="Gasto futuro",
        metodo_pago=MetodoPago.DEBITO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime.now(timezone.utc),
    )
    tx_pasada = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("500.00"),
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        fecha=hoy - timedelta(days=2),
        descripcion="Gasto pasado",
        metodo_pago=MetodoPago.DEBITO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db_session.add_all([tx_futura, tx_pasada])
    db_session.commit()

    prev = previsualizar_control(db_session, u.id, b.id, saldo_declarado=Decimal("50000.00"), hoy=hoy)
    assert prev["ultimo_movimiento"] == hoy - timedelta(days=2)

    # C6: DELETE con otra billetera_id da 404
    b2 = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Billetera Otra",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("10000.00"),
        saldo_actual=Decimal("10000.00"),
        fecha_creacion=datetime.now(timezone.utc) - timedelta(days=10),
    )
    ajuste = AjusteSaldo(
        id=uuid4(),
        billetera_id=b.id,
        fecha=hoy,
        monto=Decimal("1000.00"),
        saldo_anterior=Decimal("49000.00"),
        saldo_declarado=Decimal("50000.00"),
    )
    db_session.add_all([b2, ajuste])
    db_session.commit()

    with pytest.raises(HTTPException) as exc_eliminar:
        eliminar_ajuste(db_session, u.id, ajuste.id, billetera_id=b2.id)
    assert exc_eliminar.value.status_code == 404
    assert exc_eliminar.value.detail == "No encontramos ese ajuste de saldo."


# -----------------------------------------------------------------------------
# D9. C7: regex de refresco de base local
# -----------------------------------------------------------------------------
def test_d9_regex_refresco_base_c7():
    ok_40, msg_40, n_40 = verificar_resultado_refresco("Total tablas: 40 | Tablas iguales: 40 | Tablas distintas: 0")
    assert ok_40 is True
    assert n_40 == 40

    ok_41, msg_41, n_41 = verificar_resultado_refresco("Total tablas: 41 | Tablas iguales: 41 | Tablas distintas: 0")
    assert ok_41 is True
    assert n_41 == 41

    ok_dist, msg_dist, n_dist = verificar_resultado_refresco("Total tablas: 40 | Tablas iguales: 39 | Tablas distintas: 1")
    assert ok_dist is False
    assert n_dist == 40


# -----------------------------------------------------------------------------
# FASE 4A_4: Tests B1 a B4
# -----------------------------------------------------------------------------
def test_b1_opciones_de_entidad():
    assert opciones_de_entidad("uala") == (
        "cuenta",
        "UALA",
        ["UALA", "UALA PLUS 1", "UALA PLUS 2", "Ualintec Ahorro Pesos - Clase A"],
    )
    assert opciones_de_entidad("supervielle") == (
        None,
        None,
        ["SUPERVIELLE", "SUPERVIELLE HIT IOL", "Premier Renta CP en Pesos - Clase A"],
    )
    assert opciones_de_entidad("mercadopago") == ("fci", "Mercado Fondo - Clase A", ["Mercado Fondo - Clase A"])
    assert opciones_de_entidad("galicia") == (None, None, ["Fima Premium - Clase A"])
    assert opciones_de_entidad("paypal") == (None, None, [])


# -----------------------------------------------------------------------------
# FASE 4A_5: Tests de múltiples opciones, fondos con tope y esquema de entidades
# -----------------------------------------------------------------------------
def test_fase4a_5_nacion_con_y_sin_nivel():
    hoy = date(2026, 10, 5)
    tasas = {
        "BNA": TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="BNA",
            tna=Decimal("22.0000"),
            tope=None,
            fecha_dato=hoy,
        ),
        "Pellegrini Renta Pesos - Clase A": TasaEntidad(
            fuente="argentinadatos_fci",
            clave="Pellegrini Renta Pesos - Clase A",
            tna=Decimal("33.2000"),
            tope=None,
            fecha_dato=hoy,
        ),
    }

    # 1. nacion sin nivel: sin tasa
    b_sin_nivel = Billetera(nombre="Nación Sin Nivel", entidad_id="nacion", tna=None, nivel_tasa=None, es_efectivo=False)
    te_sin_nivel = tasa_efectiva(b_sin_nivel, tasas, Decimal("500000.00"), hoy)
    assert te_sin_nivel.tna is None
    assert te_sin_nivel.origen is None

    # 2. nacion con nivel "Pellegrini Renta Pesos - Clase A": usa la tasa de ese fondo
    b_con_fci = Billetera(
        nombre="Nación FCI",
        entidad_id="nacion",
        tna=None,
        nivel_tasa="Pellegrini Renta Pesos - Clase A",
        es_efectivo=False,
    )
    te_con_fci = tasa_efectiva(b_con_fci, tasas, Decimal("500000.00"), hoy)
    assert te_con_fci.tna == Decimal("33.2000")
    assert te_con_fci.clave == "Pellegrini Renta Pesos - Clase A"
    assert te_con_fci.origen == "automatica"


def test_fase4a_5_lemon_tope_fijo():
    hoy = date(2026, 10, 5)
    tasas = {
        "Vinci Compass Liquidez - Clase F": TasaEntidad(
            fuente="argentinadatos_fci",
            clave="Vinci Compass Liquidez - Clase F",
            tna=Decimal("35.0000"),
            tope=None,  # En DB la fila no tiene tope, pero la opción sí
            fecha_dato=hoy,
        )
    }

    b_lemon = Billetera(
        nombre="Lemon Cash",
        entidad_id="lemon",
        tna=None,
        nivel_tasa=None,
        es_efectivo=False,
    )
    te_lemon = tasa_efectiva(b_lemon, tasas, Decimal("3000000.00"), hoy)
    assert te_lemon.tna == Decimal("35.0000")
    assert te_lemon.tope == Decimal("2000000")
    assert te_lemon.origen == "automatica"

    # Rendimiento con saldo 3.000.000 usa tope 2.000.000
    saldos = {hoy - timedelta(days=1): Decimal("3000000.00")}
    rend = rendimiento_por_saldos(saldos, te_lemon.tna, te_lemon.tope)
    esperado = (Decimal("2000000.00") * Decimal("35.0000") / Decimal("100") / Decimal("365")).quantize(
        Decimal("0.01")
    )
    assert rend == esperado


def test_fase4a_5_uala_sigue_igual():
    hoy = date(2026, 10, 5)
    tasas = {
        "UALA": TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="UALA",
            tna=Decimal("19.0000"),
            tope=Decimal("1000000.00"),
            fecha_dato=hoy,
        ),
        "UALA PLUS 1": TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="UALA PLUS 1",
            tna=Decimal("21.0000"),
            tope=None,
            fecha_dato=hoy,
        ),
    }

    # Ualá base
    b_base = Billetera(nombre="Ualá", entidad_id="uala", tna=None, nivel_tasa=None, es_efectivo=False)
    te_base = tasa_efectiva(b_base, tasas, Decimal("1500000.00"), hoy)
    assert te_base.tna == Decimal("19.0000")
    assert te_base.tope == Decimal("1000000.00")

    # Ualá Plus 1
    b_plus = Billetera(nombre="Ualá", entidad_id="uala", tna=None, nivel_tasa="UALA PLUS 1", es_efectivo=False)
    te_plus = tasa_efectiva(b_plus, tasas, Decimal("1500000.00"), hoy)
    assert te_plus.tna == Decimal("21.0000")
    assert te_plus.tope is None


def test_fase4a_5_listar_entidades_etiqueta_y_tipo(db_session):
    u = _crear_usuario(db_session)
    hoy = hoy_argentina()

    tasas = [
        TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="UALA",
            tna=Decimal("19.0000"),
            tope=Decimal("1000000.00"),
            fecha_dato=hoy,
        ),
        TasaEntidad(
            fuente="argentinadatos_fci",
            clave="Mercado Fondo - Clase A",
            tna=Decimal("36.0000"),
            tope=None,
            fecha_dato=hoy,
        ),
        TasaEntidad(
            fuente="argentinadatos_fci",
            clave="Vinci Compass Liquidez - Clase F",
            tna=Decimal("35.0000"),
            tope=None,
            fecha_dato=hoy,
        ),
    ]
    db_session.add_all(tasas)
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u

    client = TestClient(app)
    resp = client.get("/billeteras/entidades")
    assert resp.status_code == 200
    entidades_res = {item["id"]: item for item in resp.json()}

    # Ualá
    uala = entidades_res["uala"]
    assert uala["clave_base"] == "UALA"
    assert uala["tipo_fuente"] == "cuenta"
    opts_uala = {o["clave"]: o for o in uala["opciones"]}
    assert opts_uala["UALA"]["tipo"] == "cuenta"
    assert opts_uala["UALA"]["etiqueta"] == "Cuenta remunerada"
    assert opts_uala["Ualintec Ahorro Pesos - Clase A"]["tipo"] == "fci"
    assert opts_uala["Ualintec Ahorro Pesos - Clase A"]["etiqueta"] == "Fondo Ualintec Ahorro"

    # Mercado Pago
    mp = entidades_res["mercadopago"]
    assert mp["clave_base"] == "Mercado Fondo - Clase A"
    assert mp["tipo_fuente"] == "fci"
    assert mp["opciones"][0]["tipo"] == "fci"
    assert mp["opciones"][0]["etiqueta"] == "Fondo Mercado Fondo"

    # Lemon
    lemon = entidades_res["lemon"]
    assert lemon["clave_base"] == "Vinci Compass Liquidez - Clase F"
    assert lemon["opciones"][0]["tope"] == 2000000.0
    assert lemon["opciones"][0]["etiqueta"] == "Fondo Vinci Compass Liquidez"

    # Nación
    nacion = entidades_res["nacion"]
    assert nacion["clave_base"] is None
    assert nacion["tipo_fuente"] is None
    opts_nacion = {o["clave"]: o for o in nacion["opciones"]}
    assert opts_nacion["BNA"]["tipo"] == "cuenta"
    assert opts_nacion["BNA"]["etiqueta"] == "Cuenta sueldo"
    assert opts_nacion["Pellegrini Renta Pesos - Clase A"]["tipo"] == "fci"
    assert opts_nacion["Pellegrini Renta Pesos - Clase A"]["etiqueta"] == "Fondo Pellegrini Renta Pesos"

    app.dependency_overrides.clear()


def test_b2_patch_nivel_tasa_infiere_entidad(db_session):
    u = _crear_usuario(db_session)
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Ualá",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("10000.00"),
        saldo_actual=Decimal("10000.00"),
        entidad_id=None,
        nivel_tasa=None,
    )
    db_session.add(b)
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u

    client = TestClient(app)
    resp = client.patch(f"/billeteras/{b.id}", json={"nivel_tasa": "UALA PLUS 2"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["nivel_tasa"] == "UALA PLUS 2"
    assert data["bank_id"] == "uala"
    assert data["entidad_efectiva"] == "uala"

    # Verificar en la base de datos
    db_session.refresh(b)
    assert b.nivel_tasa == "UALA PLUS 2"
    assert b.entidad_id == "uala"

    app.dependency_overrides.clear()


def test_b3_entidad_efectiva():
    b_mp = Billetera(nombre="Mercado Pago", entidad_id=None)
    assert b_mp.entidad_efectiva == "mercadopago"

    b_sebas = Billetera(nombre="MP Sebas", entidad_id=None)
    assert b_sebas.entidad_efectiva is None

    b_uala_exp = Billetera(nombre="Personalizada", entidad_id="uala")
    assert b_uala_exp.entidad_efectiva == "uala"


def test_b4_estimar_rendimiento_endpoint(db_session):
    u = _crear_usuario(db_session)
    hoy = hoy_argentina()

    # Cargar tasas de prueba
    tasas = [
        TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="UALA",
            tna=Decimal("19.0000"),
            tope=Decimal("1000000.00"),
            fecha_dato=hoy,
        ),
        TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="UALA PLUS 2",
            tna=Decimal("24.0000"),
            tope=None,
            fecha_dato=hoy,
        ),
        TasaEntidad(
            fuente="argentinadatos_cuentas",
            clave="BRUBANK",
            tna=Decimal("27.0000"),
            tope=Decimal("750000.00"),
            fecha_dato=date(2025, 8, 6),
        ),
    ]
    db_session.add_all(tasas)
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u

    client = TestClient(app)

    # 1. Entidad uala con saldo 1.500.000: tna 19, tope 1.000.000, por_dia 520.55 y por_mes 15616.44
    r1 = client.get("/billeteras/estimar-rendimiento", params={"saldo": "1500000", "entidad_id": "uala"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["tna"] == 19.0
    assert d1["tope"] == 1000000.0
    assert d1["por_dia"] == 520.55
    assert d1["por_mes"] == 15616.44
    assert d1["vieja"] is False

    # 2. tna 36,5 sin entidad y saldo 100.000: origen "manual", por_dia 100.00 y por_mes 3000.00
    r2 = client.get("/billeteras/estimar-rendimiento", params={"saldo": "100000", "tna": "36.5"})
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["origen"] == "manual"
    assert d2["por_dia"] == 100.00
    assert d2["por_mes"] == 3000.00

    # 3. Entidad galicia: tna None y por_dia None
    r3 = client.get("/billeteras/estimar-rendimiento", params={"saldo": "100000", "entidad_id": "galicia"})
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["tna"] is None
    assert d3["por_dia"] is None
    assert d3["por_mes"] is None

    # 4. brubank con dato del 2025-08-06: vieja True y por_dia None
    r4 = client.get("/billeteras/estimar-rendimiento", params={"saldo": "100000", "entidad_id": "brubank"})
    assert r4.status_code == 200
    d4 = r4.json()
    assert d4["vieja"] is True
    assert d4["por_dia"] is None
    assert d4["por_mes"] is None

    # 5. GET /billeteras/estimar-rendimiento no cae en /{billetera_id} (TestClient)
    # Si cayera en /{billetera_id}, daría 404 porque no existe una billetera con id 'estimar-rendimiento'
    assert r1.status_code == 200
    assert "por_dia" in d1

    app.dependency_overrides.clear()

