"""
tests/test_fin_de_mes.py — Pruebas unitarias, integración y paridad para fin de mes y disponible libre.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from decimal import Decimal
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
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.transaccion import EstadoVerificacionTransaccion, MetodoPago, OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import fin_de_mes_service
from app.services.fin_de_mes_service import (
    calcular_fin_de_mes,
    cobra_en_ciclo,
    dia_de_cobro,
    fecha_limite,
    ingresos_en_ciclo,
    proxima_fecha_con_dia,
    rango_fin_de_mes,
    redondear_mil,
)
from app.services.fin_de_mes_textos import linea_saldo, textos_tarjeta
from app.services.frescura_service import Frescura


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
        email="test_fin_de_mes@argentum.com",
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

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ==============================================================================
# 1. Funciones puras
# ==============================================================================

def test_cobra_en_ciclo():
    assert cobra_en_ciclo(date(2026, 11, 25), date(2026, 11, 30)) is True
    assert cobra_en_ciclo(date(2026, 11, 30), date(2026, 11, 30)) is True
    assert cobra_en_ciclo(date(2026, 12, 1), date(2026, 11, 30)) is False
    assert cobra_en_ciclo(None, date(2026, 11, 30)) is False


def test_ingresos_en_ciclo():
    hoy = date(2026, 11, 20)
    fin = date(2026, 11, 30)
    assert ingresos_en_ciclo([(date(2026, 11, 25), Decimal("1000000"))], hoy, fin) == Decimal("1000000")
    assert ingresos_en_ciclo([(date(2026, 12, 5), Decimal("1000000"))], hoy, fin) == Decimal("0")
    assert ingresos_en_ciclo([(date(2026, 11, 25), Decimal("1000000")), (date(2026, 11, 28), Decimal("200000"))], hoy, fin) == Decimal("1200000")
    assert ingresos_en_ciclo([(date(2026, 11, 30), Decimal("5"))], hoy, fin) == Decimal("5")
    assert ingresos_en_ciclo([(date(2026, 11, 20), Decimal("7"))], hoy, fin) == Decimal("0")
    assert ingresos_en_ciclo([], hoy, fin) == Decimal("0")


def test_dia_de_cobro():
    assert dia_de_cobro([]) is None
    assert dia_de_cobro([date(2026, 10, 25), date(2026, 9, 26)]) is None
    assert dia_de_cobro([date(2026, 8, 24), date(2026, 9, 26), date(2026, 10, 25)]) == 25
    assert dia_de_cobro([date(2026, 7, 1), date(2026, 8, 24), date(2026, 9, 26), date(2026, 10, 25)]) == 25
    assert dia_de_cobro([date(2026, 8, 3), date(2026, 9, 5), date(2026, 10, 28)]) == 5


def test_proxima_fecha_con_dia():
    assert proxima_fecha_con_dia(date(2026, 11, 2), 5) == date(2026, 11, 5)
    assert proxima_fecha_con_dia(date(2026, 11, 5), 5) == date(2026, 12, 5)
    assert proxima_fecha_con_dia(date(2026, 11, 2), 31) == date(2026, 11, 30)
    assert proxima_fecha_con_dia(date(2026, 11, 30), 31) == date(2026, 12, 31)
    assert proxima_fecha_con_dia(date(2027, 1, 31), 30) == date(2027, 2, 28)


def test_fecha_limite():
    hoy = date(2026, 11, 20)
    fin = date(2026, 11, 30)
    assert fecha_limite(hoy, fin, date(2026, 11, 25)) == (date(2026, 11, 24), True)
    assert fecha_limite(hoy, fin, None) == (date(2026, 11, 30), False)
    assert fecha_limite(hoy, fin, date(2026, 12, 5)) == (date(2026, 11, 30), False)
    assert fecha_limite(hoy, fin, date(2026, 12, 1)) == (date(2026, 11, 30), True)
    assert fecha_limite(hoy, fin, date(2026, 11, 21)) == (date(2026, 11, 20), True)


def test_redondear_mil():
    assert redondear_mil(Decimal("100400")) == Decimal("100000")
    assert redondear_mil(Decimal("-99600")) == Decimal("-100000")
    assert redondear_mil(Decimal("500")) == Decimal("1000")
    assert redondear_mil(Decimal("0")) == Decimal("0")


def test_rango_fin_de_mes():
    ciclos = [
        (Decimal("600000"), 30),
        (Decimal("900000"), 30),
        (Decimal("1200000"), 30),
    ]
    libre = Decimal("300000")
    dias_restantes = 10

    # Cobertura None, ingreso True, viejos False -> estado "completo", (-100.000, 100.000), 3 ciclos, veces 1, factor 1
    r_base = rango_fin_de_mes(libre, dias_restantes, ciclos, None, True, False)
    assert r_base.estado == "completo"
    assert r_base.rango == (Decimal("-100000"), Decimal("100000"))
    assert r_base.ciclos == 3
    assert r_base.veces == 1
    assert r_base.factor == Decimal("1")

    # Cobertura 80 -> factor 1.25, rango (-200.000, 50.000), veces 2
    r_80 = rango_fin_de_mes(libre, dias_restantes, ciclos, 80, True, False)
    assert r_80.factor == Decimal("1.25")
    assert r_80.rango == (Decimal("-200000"), Decimal("50000"))
    assert r_80.veces == 2

    # Cobertura 40 -> factor 2, rango (-500.000, -100.000), veces 3
    r_40 = rango_fin_de_mes(libre, dias_restantes, ciclos, 40, True, False)
    assert r_40.factor == Decimal("2")
    assert r_40.rango == (Decimal("-500000"), Decimal("-100000"))
    assert r_40.veces == 3

    # Cobertura 100 -> factor 1 y mismo resultado que con None
    r_100 = rango_fin_de_mes(libre, dias_restantes, ciclos, 100, True, False)
    assert r_100.factor == Decimal("1")
    assert r_100.rango == r_base.rango
    assert r_100.veces == r_base.veces

    # Libre 300.400 (cobertura None) -> (-100.000, 100.000) por redondeo y veces 1
    r_redondeo = rango_fin_de_mes(Decimal("300400"), dias_restantes, ciclos, None, True, False)
    assert r_redondeo.rango == (Decimal("-100000"), Decimal("100000"))
    assert r_redondeo.veces == 1

    # Solo los 2 primeros ciclos -> "solo_seguro", rango None, veces None, ciclos 2
    r_solo2 = rango_fin_de_mes(libre, dias_restantes, ciclos[:2], None, True, False)
    assert r_solo2.estado == "solo_seguro"
    assert r_solo2.rango is None
    assert r_solo2.veces is None
    assert r_solo2.ciclos == 2

    # Ingreso False -> "sin_ingreso" (aunque haya 3 ciclos)
    r_sin_ingreso = rango_fin_de_mes(libre, dias_restantes, ciclos, None, False, False)
    assert r_sin_ingreso.estado == "sin_ingreso"

    # Viejos True e ingreso False -> "datos_viejos"
    r_viejos = rango_fin_de_mes(libre, dias_restantes, ciclos, None, False, True)
    assert r_viejos.estado == "datos_viejos"

    # 10 ciclos (gasto 30.000 * k para k=1..10, 30 dias c/u), libre 300.000, 10 dias -> (209.000, 281.000), veces 0
    ciclos_10 = [(Decimal(str(30000 * k)), 30) for k in range(1, 11)]
    r_10 = rango_fin_de_mes(libre, dias_restantes, ciclos_10, None, True, False)
    assert r_10.rango == (Decimal("209000"), Decimal("281000"))
    assert r_10.veces == 0


# ==============================================================================
# 2. Integración
# ==============================================================================

def test_calcular_fin_de_mes_integracion(db: Session, usuario_test: Usuario, monkeypatch):
    hoy = date(2026, 11, 20)

    # Parchear frescura y cobertura en fin_de_mes_service
    frescura_ficticia = Frescura(
        ultima_carga=date(2026, 11, 19),
        dias_sin_cargar=1,
        umbral_dias=7,
        intervalos=5,
        datos_viejos=False,
    )
    monkeypatch.setattr(fin_de_mes_service, "calcular_frescura", lambda *a, **k: frescura_ficticia)
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **k: None)

    # 1. Categoría de ingreso con subcategoría "Sueldo"
    cat_ingreso = Categoria(
        id=uuid4(),
        nombre="Ingresos Laborales",
        tipo=TipoCategoria.INGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    subcat_sueldo = Subcategoria(
        id=uuid4(),
        categoria_id=cat_ingreso.id,
        nombre="Sueldo",
        estado=EstadoSubcategoria.ACTIVA,
    )
    cat_gastos = Categoria(
        id=uuid4(),
        nombre="Gastos Varios",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([cat_ingreso, subcat_sueldo, cat_gastos])
    db.commit()

    # 2. Billetera "Cuenta" ARS activa
    # Con saldo actual 500.000 hoy
    cuenta = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_actual=Decimal("500000"),
        saldo_inicial=Decimal("-1595000"),
    )
    db.add(cuenta)
    db.commit()

    # 3. Ingresos de $1.000.000 en "Sueldo" el 25/07, 24/08, 26/09 y 25/10
    fechas_ingresos = [
        date(2026, 7, 25),
        date(2026, 8, 24),
        date(2026, 9, 26),
        date(2026, 10, 25),
    ]
    transacciones_ingresos = []
    for f in fechas_ingresos:
        tx = Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=cuenta.id,
            categoria_id=cat_ingreso.id,
            subcategoria_id=subcat_sueldo.id,
            tipo=TipoTransaccion.INGRESO,
            moneda=Moneda.ARS,
            monto=Decimal("1000000"),
            fecha=f,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            es_padre_cuotas=False,
            es_cuota_hija=False,
        )
        transacciones_ingresos.append(tx)
    db.add_all(transacciones_ingresos)

    # 4. Egresos débito: 15/08 $310.000, 15/09 $600.000, 15/10 $930.000 y 01/11 $50.000
    egresos_debito = [
        (date(2026, 8, 15), Decimal("310000")),
        (date(2026, 9, 15), Decimal("600000")),
        (date(2026, 10, 15), Decimal("930000")),
        (date(2026, 11, 1), Decimal("50000")),
    ]
    for f, m in egresos_debito:
        tx = Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=cuenta.id,
            categoria_id=cat_gastos.id,
            tipo=TipoTransaccion.EGRESO,
            metodo_pago=MetodoPago.DEBITO,
            moneda=Moneda.ARS,
            monto=m,
            fecha=f,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            es_padre_cuotas=False,
            es_cuota_hija=False,
        )
        db.add(tx)

    # 5. En octubre además:
    # un egreso con metodo_pago CREDITO de $500.000 (16/10)
    tx_credito = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=cuenta.id,
        categoria_id=cat_gastos.id,
        tipo=TipoTransaccion.EGRESO,
        metodo_pago=MetodoPago.CREDITO,
        moneda=Moneda.ARS,
        monto=Decimal("500000"),
        fecha=date(2026, 10, 16),
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
    )
    # y uno con suscripcion_id de $15.000 (17/10)
    dummy_sub_id = uuid4()
    tx_sub = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=cuenta.id,
        categoria_id=cat_gastos.id,
        tipo=TipoTransaccion.EGRESO,
        metodo_pago=MetodoPago.DEBITO,
        moneda=Moneda.ARS,
        monto=Decimal("15000"),
        fecha=date(2026, 10, 17),
        suscripcion_id=dummy_sub_id,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
    )
    db.add_all([tx_credito, tx_sub])

    # 6. Suscripciones:
    # Gimnasio con próximo cobro 22/11 por $15.000
    sub_gimnasio = Suscripcion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=cuenta.id,
        nombre="Gimnasio",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 11, 22),
        estado=EstadoSuscripcion.ACTIVA,
    )
    hist_gimnasio = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=sub_gimnasio.id,
        monto=Decimal("15000"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 1, 1),
    )
    # Seguro con próximo cobro 28/11 por $40.000
    sub_seguro = Suscripcion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=cuenta.id,
        nombre="Seguro",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 11, 28),
        estado=EstadoSuscripcion.ACTIVA,
    )
    hist_seguro = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=sub_seguro.id,
        monto=Decimal("40000"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 1, 1),
    )
    db.add_all([sub_gimnasio, hist_gimnasio, sub_seguro, hist_seguro])
    db.commit()

    # Contar llamadas a calcular_disponible_libre
    real_calcular_disponible = fin_de_mes_service.calcular_disponible_libre
    llamadas_disponible = []

    def spy_disponible(*args, **kwargs):
        llamadas_disponible.append(kwargs)
        return real_calcular_disponible(*args, **kwargs)

    monkeypatch.setattr(fin_de_mes_service, "calcular_disponible_libre", spy_disponible)

    # 7. Ejecutar calcular_fin_de_mes caso completo
    res = calcular_fin_de_mes(db, usuario_test, fecha_ref=hoy)

    # 2 llamadas: la primera con fecha_fin_ciclo 30/11/2026 y la segunda con 24/11/2026
    assert len(llamadas_disponible) == 2
    assert llamadas_disponible[0].get("fecha_fin_ciclo") == date(2026, 11, 30)
    assert llamadas_disponible[1].get("fecha_fin_ciclo") == date(2026, 11, 24)

    assert res.proximo_ingreso == date(2026, 11, 25)
    assert res.cobra_en_ciclo is True
    assert res.limite == date(2026, 11, 24)
    assert res.libre_ars == Decimal("445000")
    assert res.libre_hasta_cobro == Decimal("485000")
    assert res.ingreso_esperado == Decimal("1000000")
    assert res.estado == "completo"
    assert res.mostrar_tarjeta is True
    assert res.ciclos == 3
    assert res.rango == (Decimal("1145000"), Decimal("1345000"))
    assert res.veces == 0
    assert res.saldo_hoy == Decimal("500000")
    assert res.movimiento == Decimal("-50000")
    assert res.saldo_inicio == Decimal("550000")

    # Textos de tarjeta y línea de saldo
    tt = textos_tarjeta(res)
    assert tt is not None
    assert tt["titulo"] == "¿Cómo llegás a fin de mes?"
    assert tt["monto"] == "Entre $1.145.000 y $1.345.000"
    assert tt["subtitulo"] == "Es con lo que llegarías al 30/11, contando tu cobro del 25/11."
    assert tt["linea_riesgo"] is None
    assert tt["linea_cobro"] == "Hasta tu cobro del 25/11 te quedan $485.000 libres."
    assert tt["linea_cobertura"] is None

    assert linea_saldo(res) == "Empezaste el ciclo con $550.000 y se movieron -$50.000: hoy tenés $500.000."

    # Contrato del caso completo
    assert res.contrato.valor == Decimal("445000")
    assert res.contrato.base_desde == date(2026, 11, 1)
    assert res.contrato.base_hasta == date(2026, 11, 30)
    assert len(res.contrato.supuestos) == 3
    assert res.contrato.supuestos[1] == "Descuenta lo que vence hasta el 30/11."
    assert res.contrato.supuestos[2] == "Tu próximo cobro sale de tus últimos 3 cobros."

    # 8. El mismo usuario sin los 4 ingresos
    llamadas_disponible.clear()
    for tx in transacciones_ingresos:
        db.delete(tx)
    db.commit()

    res_sin_ingreso = calcular_fin_de_mes(db, usuario_test, fecha_ref=hoy)
    assert res_sin_ingreso.proximo_ingreso is None
    assert res_sin_ingreso.cobra_en_ciclo is False
    assert res_sin_ingreso.libre_ars == Decimal("445000")
    assert res_sin_ingreso.libre_hasta_cobro is None
    assert res_sin_ingreso.ingreso_esperado == Decimal("0")
    assert res_sin_ingreso.estado == "sin_ingreso"
    assert res_sin_ingreso.mostrar_tarjeta is False
    assert textos_tarjeta(res_sin_ingreso) is None
    assert len(llamadas_disponible) == 1
    assert llamadas_disponible[0].get("fecha_fin_ciclo") == date(2026, 11, 30)

    # 9. Con la Frescura parcheada con datos_viejos True (con ingresos)
    # Reinsertar ingresos para probar frescura vieja con ingresos
    for tx_data in transacciones_ingresos:
        tx_re = Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=cuenta.id,
            categoria_id=tx_data.categoria_id,
            subcategoria_id=tx_data.subcategoria_id,
            tipo=tx_data.tipo,
            moneda=tx_data.moneda,
            monto=tx_data.monto,
            fecha=tx_data.fecha,
            origen=tx_data.origen,
            estado_verificacion=tx_data.estado_verificacion,
            es_padre_cuotas=False,
            es_cuota_hija=False,
        )
        db.add(tx_re)
    db.commit()

    frescura_vieja = Frescura(
        ultima_carga=date(2026, 11, 1),
        dias_sin_cargar=19,
        umbral_dias=7,
        intervalos=5,
        datos_viejos=True,
    )
    monkeypatch.setattr(fin_de_mes_service, "calcular_frescura", lambda *a, **k: frescura_vieja)
    res_viejos = calcular_fin_de_mes(db, usuario_test, fecha_ref=hoy)
    assert res_viejos.estado == "datos_viejos"
    assert res_viejos.mostrar_tarjeta is False


# ==============================================================================
# 3. Paridad y ruta
# ==============================================================================

def test_paridad_y_ruta_fin_de_mes(client: TestClient, db: Session, usuario_test: Usuario):
    # Fechas relativas a hoy_argentina(), horas 12:00
    # Usuario sin ingresos con "Caja" ARS $270.000 y sin compromisos
    caja = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Caja",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_actual=Decimal("270000"),
        saldo_inicial=Decimal("270000"),
    )
    db.add(caja)
    db.commit()

    res_fin = client.get("/dashboard/fin-de-mes")
    assert res_fin.status_code == 200
    data_fin = res_fin.json()

    res_comp = client.get("/dashboard/resumen-completo")
    assert res_comp.status_code == 200
    data_comp = res_comp.json()

    assert data_fin["estado"] == "sin_ingreso"
    assert data_fin["mostrar_tarjeta"] is False
    assert data_fin["tarjeta"] is None
    assert data_fin["libre_ars"] == 270000
    assert Decimal(str(data_fin["libre_ars"])) == Decimal(str(data_comp["saldo_disponible"]["ars"]["saldo_disponible"]))
    assert data_fin["fin_ciclo"] == data_comp["resumen"]["periodo"]["fecha_fin"]
    assert data_fin["linea_saldo"] == "Empezaste el ciclo con $270.000 y se movieron $0: hoy tenés $270.000."
    assert data_fin["mostrar_modal_bienvenida"] is False

    # total_compromisos vale 0 (como número o texto)
    assert Decimal(str(data_comp["saldo_disponible"]["ars"]["total_compromisos"])) == Decimal("0")

    # GET /dashboard/resumen y GET /dashboard/proyeccion siguen respondiendo 200
    assert client.get("/dashboard/resumen").status_code == 200
    assert client.get("/dashboard/proyeccion").status_code == 200
