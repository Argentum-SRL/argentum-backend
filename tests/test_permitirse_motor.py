"""
tests/test_permitirse_motor.py â€” Pruebas puras y de integraci\u00f3n del motor de "Â¿Me lo puedo permitir?".
"""
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch
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
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import fin_de_mes_service
from app.services.compromisos_service import (
    ResultadoCompromisos,
    calcular_compromisos_mensuales,
    calcular_gasto_variable_tipico,
)
from app.services.frescura_service import Frescura
from app.services.permitirse_service import (
    calcular_puede_permitirse,
    desvio_absoluto_mediano,
    elegir_rubro,
    evaluar_contado,
    evaluar_mensual,
    liberacion_por_mes,
    mes_liberacion,
    recolectar,
    redondear_arriba_mil,
    compensar_tope_50,
)


# ==============================================================================
# 1. PRUEBAS PURAS
# ==============================================================================

def test_evaluar_contado_exactos():
    finales_3 = (Decimal("250000"), Decimal("200000"), Decimal("180000"))
    libre_300 = Decimal("300000")

    # precio 1.000 ahorros 0 â†’ bien, veces 0
    res, veces, n, falta = evaluar_contado(Decimal("1000"), libre_300, finales_3, Decimal("0"))
    assert res == "bien"
    assert veces == 0

    # precio 190.000 â†’ justo, veces 1, n 3
    res, veces, n, falta = evaluar_contado(Decimal("190000"), libre_300, finales_3, Decimal("0"))
    assert res == "justo"
    assert veces == 1
    assert n == 3

    # precio 280.000 ahorros 500.000 â†’ ahorros, falta 80.000
    res, veces, n, falta = evaluar_contado(Decimal("280000"), libre_300, finales_3, Decimal("500000"))
    assert res == "ahorros"
    assert falta == Decimal("80000.00")

    # precio 280.000 ahorros 50.000 â†’ no_alcanza, falta 30.000
    res, veces, n, falta = evaluar_contado(Decimal("280000"), libre_300, finales_3, Decimal("50000"))
    assert res == "no_alcanza"
    assert falta == Decimal("30000.00")

    # precio 300.000 ahorros 500.000 â†’ ahorros, falta 100.000 (regla c: veces 3 de 3, mediana 200.000)
    res, veces, n, falta = evaluar_contado(Decimal("300000"), libre_300, finales_3, Decimal("500000"))
    assert res == "ahorros"
    assert falta == Decimal("100000.00")

    # finales (400.000, 300.000, 200.000, 100.000) y precio 250.000 â†’ justo, veces 2, n 4
    finales_4 = (Decimal("400000"), Decimal("300000"), Decimal("200000"), Decimal("100000"))
    res, veces, n, falta = evaluar_contado(Decimal("250000"), libre_300, finales_4, Decimal("0"))
    assert res == "justo"
    assert veces == 2
    assert n == 4

    # precio 350.000, libre 300.000, ahorros 500.000 â†’ ahorros, falta 50.000
    res, veces, n, falta = evaluar_contado(Decimal("350000"), libre_300, finales_3, Decimal("500000"))
    assert res == "ahorros"
    assert falta == Decimal("50000.00")

    # precio 900.000, libre 300.000, ahorros 500.000 â†’ no_alcanza, falta 100.000
    res, veces, n, falta = evaluar_contado(Decimal("900000"), libre_300, finales_3, Decimal("500000"))
    assert res == "no_alcanza"
    assert falta == Decimal("100000.00")

    # finales None, precio 1.000, libre 300.000 â†’ justo
    res, veces, n, falta = evaluar_contado(Decimal("1000"), libre_300, None, Decimal("0"))
    assert res == "justo"

    # libre âˆ’20.000, precio 10.000, ahorros 0 â†’ no_alcanza, falta 30.000
    res, veces, n, falta = evaluar_contado(Decimal("10000"), Decimal("-20000"), finales_3, Decimal("0"))
    assert res == "no_alcanza"
    assert falta == Decimal("30000.00")


def test_evaluar_mensual_exactos():
    # (TH.4) indefinido, cuota 80.000, margen 50.000, colchon 10.000, ahorros 200.000 â†’ ahorros, deficit_mensual 30.000, meses_alcanzan 6
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("80000")] * 12,
        margenes=[Decimal("50000")] * 12,
        colchon=Decimal("10000"),
        ahorros_total=Decimal("200000"),
        indefinido=True,
    )
    assert res == "ahorros"
    assert def_m == Decimal("30000.00")
    assert m_alc == 6

    # con ahorros 20.000 â†’ no_alcanza
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("80000")] * 12,
        margenes=[Decimal("50000")] * 12,
        colchon=Decimal("10000"),
        ahorros_total=Decimal("20000"),
        indefinido=True,
    )
    assert res == "no_alcanza"

    # Indefinido cuota 30.000 margen 50.000 colchon 10.000 â†’ bien
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("30000")] * 12,
        margenes=[Decimal("50000")] * 12,
        colchon=Decimal("10000"),
        ahorros_total=Decimal("0"),
        indefinido=True,
    )
    assert res == "bien"

    # Caso borde mutaci\u00f3n 4: r_k == colchon (margen 50.000, cuota 40.000, colchon 10.000) -> bien
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("40000")] * 12,
        margenes=[Decimal("50000")] * 12,
        colchon=Decimal("10000"),
        ahorros_total=Decimal("0"),
        indefinido=True,
    )
    assert res == "bien"

    # cuota 45.000 â†’ justo, r_min 5.000
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("45000")] * 12,
        margenes=[Decimal("50000")] * 12,
        colchon=Decimal("10000"),
        ahorros_total=Decimal("0"),
        indefinido=True,
    )
    assert res == "justo"
    assert r_min == Decimal("5000.00")

    # Cuotas 12 Ã— 10.000:
    # margen 100.000 colchon 20.000 â†’ bien, r_min 90.000
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("10000")] * 12,
        margenes=[Decimal("100000")] * 12,
        colchon=Decimal("20000"),
        ahorros_total=Decimal("0"),
        indefinido=False,
    )
    assert res == "bien"
    assert r_min == Decimal("90000.00")

    # margen 25.000 â†’ justo, r_min 15.000
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("10000")] * 12,
        margenes=[Decimal("25000")] * 12,
        colchon=Decimal("20000"),
        ahorros_total=Decimal("0"),
        indefinido=False,
    )
    assert res == "justo"
    assert r_min == Decimal("15000.00")

    # margen 5.000 ahorros 100.000 â†’ ahorros, deficit_total 60.000, meses_deficit 12
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("10000")] * 12,
        margenes=[Decimal("5000")] * 12,
        colchon=Decimal("20000"),
        ahorros_total=Decimal("100000"),
        indefinido=False,
    )
    assert res == "ahorros"
    assert def_tot == Decimal("60000.00")
    assert m_def == 12

    # con ahorros 50.000 â†’ no_alcanza
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("10000")] * 12,
        margenes=[Decimal("5000")] * 12,
        colchon=Decimal("20000"),
        ahorros_total=Decimal("50000"),
        indefinido=False,
    )
    assert res == "no_alcanza"

    # 6 Ã— 30.000 con margenes [10.000, 10.000, 50.000, 50.000, 50.000, 50.000], colchon 15.000, ahorros 100.000
    # â†’ ahorros, deficit_total 40.000, meses_deficit 2, mes_liberacion 3
    margenes_lib = [
        Decimal("10000"), Decimal("10000"), Decimal("50000"),
        Decimal("50000"), Decimal("50000"), Decimal("50000")
    ]
    cuotas_lib = [Decimal("30000")] * 6
    colchon_lib = Decimal("15000")
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=cuotas_lib,
        margenes=margenes_lib,
        colchon=colchon_lib,
        ahorros_total=Decimal("100000"),
        indefinido=False,
    )
    assert res == "ahorros"
    assert def_tot == Decimal("40000.00")
    assert m_def == 2
    assert mes_liberacion(margenes_lib, cuotas_lib, colchon_lib) == 3

    # 12 Ã— 10.000 margen 100.000 colchon 20.000 â†’ mes_liberacion None
    assert mes_liberacion([Decimal("100000")] * 12, [Decimal("10000")] * 12, Decimal("20000")) is None

    # colchon None, margen 100.000, cuota 10.000 â†’ justo
    res, r_min, def_tot, m_def, def_m, m_alc = evaluar_mensual(
        cuotas=[Decimal("10000")],
        margenes=[Decimal("100000")],
        colchon=None,
        ahorros_total=Decimal("0"),
        indefinido=False,
    )
    assert res == "justo"


def test_liberacion_por_mes_th9():
    # (TH.9) liberacion_por_mes con ciclos oct, nov y dic de 2026 (mes calendario)
    ciclos = [
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 11, 30)),
        (date(2026, 12, 1), date(2026, 12, 31)),
    ]
    cuotas = [
        (date(2026, 10, 10), Decimal("10000")),
        (date(2026, 11, 10), Decimal("10000")),
        (date(2026, 12, 10), Decimal("10000")),
        (date(2026, 10, 10), Decimal("20000")),
    ]
    assert liberacion_por_mes(cuotas, ciclos) == [Decimal("30000.00"), Decimal("10000.00"), Decimal("10000.00")]

    # sumando (31/10, 5.000) â†’ [35.000, 10.000, 10.000] (el \u00faltimo d\u00eda del ciclo cuenta)
    cuotas_con_fin = cuotas + [(date(2026, 10, 31), Decimal("5000"))]
    assert liberacion_por_mes(cuotas_con_fin, ciclos) == [Decimal("35000.00"), Decimal("10000.00"), Decimal("10000.00")]

    # una cuota del 10/01/2027 no suma en ninguno
    cuotas_con_fuera = cuotas_con_fin + [(date(2027, 1, 10), Decimal("50000"))]
    assert liberacion_por_mes(cuotas_con_fuera, ciclos) == [Decimal("35000.00"), Decimal("10000.00"), Decimal("10000.00")]


def test_desvio_absoluto_mediano_exactos():
    assert desvio_absoluto_mediano([
        Decimal("100000"), Decimal("110000"), Decimal("90000"),
        Decimal("130000"), Decimal("95000")
    ]) == Decimal("10000.00")
    assert desvio_absoluto_mediano([Decimal("100000")] * 3) == Decimal("0.00")


def test_elegir_rubro_exactos():
    # elegir_rubro({"Alimentaci\u00f3n": 200.000, "Ocio": 80.000, "Vivienda": 300.000}) â†’ "Alimentaci\u00f3n"
    meds = {"Alimentaci\u00f3n": Decimal("200000"), "Ocio": Decimal("80000"), "Vivienda": Decimal("300000")}
    assert elegir_rubro(meds) == "Alimentaci\u00f3n"

    # con X 30.000 hay l\u00ednea; con X 150.000 no (150.000 > 100.000)
    rubro = elegir_rubro(meds)
    mediana_sel = meds[rubro]
    assert Decimal("30000") <= (mediana_sel * Decimal("0.50"))
    assert not (Decimal("150000") <= (mediana_sel * Decimal("0.50")))

    # {"Vivienda": 300.000, "Salud": 100.000} â†’ None
    assert elegir_rubro({"Vivienda": Decimal("300000"), "Salud": Decimal("100000")}) is None

    # Empate {"Ocio": 80.000, "Hogar": 80.000} â†’ "Hogar"
    assert elegir_rubro({"Ocio": Decimal("80000"), "Hogar": Decimal("80000")}) == "Hogar"


def test_compensar_exactos():
    # contado precio 190.000 finales (250.000, 200.000, 180.000) â†’ X 10.000
    finales = (Decimal("250000"), Decimal("200000"), Decimal("180000"))
    precio = Decimal("190000")
    X_contado = redondear_arriba_mil(precio - min(finales))
    assert X_contado == Decimal("10000.00")

    # cuotas r_min 15.000 colchon 20.000 â†’ X 5.000
    r_min = Decimal("15000")
    colchon = Decimal("20000")
    X_cuotas = redondear_arriba_mil(colchon - r_min)
    assert X_cuotas == Decimal("5000.00")

    # X 1.200 â†’ 2.000 (hacia arriba a miles)
    assert redondear_arriba_mil(Decimal("1200")) == Decimal("2000.00")
    assert compensar_tope_50(Decimal("30000"), Decimal("200000")) is True
    assert compensar_tope_50(Decimal("150000"), Decimal("200000")) is False


# ==============================================================================
# 2. PRUEBAS DE INTEGRACIÃ“N (SQLite)
# ==============================================================================

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


@pytest.fixture(name="db_int")
def db_int_fixture():
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


def test_integracion_permitirse_completo(db_int: Session, monkeypatch):
    # Datos de integraci\u00f3n (idÃ©nticos a test_fin_de_mes.py):
    # ciclo mes calendario, hoy 20/11/2026
    hoy = date(2026, 11, 20)

    # Parchear frescura y cobertura igual que test_fin_de_mes
    frescura_ficticia = Frescura(
        ultima_carga=date(2026, 11, 19),
        dias_sin_cargar=1,
        umbral_dias=7,
        intervalos=5,
        datos_viejos=False,
    )
    monkeypatch.setattr(fin_de_mes_service, "calcular_frescura", lambda *a, **k: frescura_ficticia)
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **k: None)

    # Usuario no admin
    usuario = Usuario(
        id=uuid4(),
        email="test_motor_int@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    usuario.is_admin = False
    db_int.add(usuario)
    db_int.commit()

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
    db_int.add_all([cat_ingreso, subcat_sueldo, cat_gastos])
    db_int.commit()

    cuenta = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_actual=Decimal("500000"),
        saldo_inicial=Decimal("-1595000"),
    )
    db_int.add(cuenta)
    db_int.commit()

    # Sueldo $1.000.000 el 25/07, 24/08, 26/09 y 25/10
    fechas_ingresos = [
        date(2026, 7, 25),
        date(2026, 8, 24),
        date(2026, 9, 26),
        date(2026, 10, 25),
    ]
    for f in fechas_ingresos:
        tx = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
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
        db_int.add(tx)

    # Egresos dÃ©bito: 15/08 $310.000, 15/09 $600.000, 15/10 $930.000 y 01/11 $50.000
    egresos_debito = [
        (date(2026, 8, 15), Decimal("310000")),
        (date(2026, 9, 15), Decimal("600000")),
        (date(2026, 10, 15), Decimal("930000")),
        (date(2026, 11, 1), Decimal("50000")),
    ]
    for f, m in egresos_debito:
        tx = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
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
        db_int.add(tx)

    # CRÃ‰DITO $500.000 en octubre y un egreso con suscripcion_id de $15.000
    tx_credito = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
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
    dummy_sub_id = uuid4()
    tx_sub = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
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
    db_int.add_all([tx_credito, tx_sub])

    # Suscripciones: Gimnasio 22/11 $15.000 y Seguro 28/11 $40.000
    sub_gimnasio = Suscripcion(
        id=uuid4(),
        usuario_id=usuario.id,
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
    sub_seguro = Suscripcion(
        id=uuid4(),
        usuario_id=usuario.id,
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
    db_int.add_all([sub_gimnasio, hist_gimnasio, sub_seguro, hist_seguro])
    db_int.commit()

    # Verificar que finales sea exactamente (1.345.000, 1.245.000, 1.145.000)
    fdm = fin_de_mes_service.calcular_fin_de_mes(db_int, usuario, fecha_ref=hoy)
    assert tuple(int(x) for x in fdm.rango.finales) in ((1345000, 1245000, 1145000), (1145000, 1245000, 1345000))

    # 1. Contado 300.000 â†’ bien
    res_300 = calcular_puede_permitirse(
        user_id=usuario.id,
        precio_total=300000.0,
        modo="contado",
        cantidad_cuotas=1,
        tiene_interes=False,
        tna=None,
        db=db_int,
        hoy=hoy,
    )
    assert res_300["resultado"] == "bien"
    assert res_300["titulo"] == "Te entra bien"
    assert res_300["linea_principal"] == "Despu\u00e9s de pagarlo llegar\u00edas al 30/11 con entre $845.000 y $1.045.000."
    assert res_300["linea_ahorros"] is None

    # 2. Contado 600.000 sin billeteras de inversi\u00f3n ni metas â†’ no_alcanza
    res_600 = calcular_puede_permitirse(
        user_id=usuario.id,
        precio_total=600000.0,
        modo="contado",
        cantidad_cuotas=1,
        tiene_interes=False,
        tna=None,
        db=db_int,
        hoy=hoy,
    )
    assert res_600["resultado"] == "no_alcanza"
    assert res_600["linea_principal"] == "Te faltan $155.000."

    # 3. Con una billetera de inversi\u00f3n ACTIVA "Balanz" de $200.000 â†’ ahorros
    balanz = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Balanz",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=True,
        saldo_actual=Decimal("200000"),
    )
    db_int.add(balanz)
    db_int.commit()

    res_600_ahorros = calcular_puede_permitirse(
        user_id=usuario.id,
        precio_total=600000.0,
        modo="contado",
        cantidad_cuotas=1,
        tiene_interes=False,
        tna=None,
        db=db_int,
        hoy=hoy,
    )
    assert res_600_ahorros["resultado"] == "ahorros"
    assert res_600_ahorros["linea_principal"] == "Con lo libre te faltan $155.000."
    assert res_600_ahorros["linea_ahorros"] == "Tus ahorros suman $200.000."
    assert res_600_ahorros["ahorros"]["inversiones"] == Decimal("200000")

    # 4. Modo mensual 40.000: paridad de m\u00e1rgenes
    from app.services.ingreso_habitual_service import ResultadoIngresoHabitual
    ingreso_ficticio = ResultadoIngresoHabitual(
        moneda="ARS",
        tipo="regular",
        monto=Decimal("1000000"),
        motivo_sin_datos=None,
    )
    with patch("app.services.permitirse_service.obtener_ingreso_habitual", return_value=ingreso_ficticio):
        datos_rec = recolectar(db_int, usuario, hoy=hoy)
        comp = calcular_compromisos_mensuales(db_int, usuario, hoy=hoy, moneda=Moneda.ARS)
        gv_no_admin = calcular_gasto_variable_tipico(db_int, usuario, hoy=hoy, moneda=Moneda.ARS, streams_c=[])
        
        # fijos debe ser 0 para no admin
        assert datos_rec.fijos == Decimal("0.00")
        margen_esperado_mes1 = Decimal("1000000") - datos_rec.liberaciones[0] - comp.suscripciones - Decimal("0.00") - gv_no_admin
        assert datos_rec.margenes[0] == margen_esperado_mes1

        # 5. El mismo usuario con is_admin True
        usuario.is_admin = True
        db_int.commit()
        datos_admin = recolectar(db_int, usuario, hoy=hoy)
        comp_admin = calcular_compromisos_mensuales(db_int, usuario, hoy=hoy, moneda=Moneda.ARS)
        gv_admin = calcular_gasto_variable_tipico(db_int, usuario, hoy=hoy, moneda=Moneda.ARS)
        assert datos_admin.fijos == comp_admin.fijos
        assert datos_admin.gasto_variable == gv_admin

    # 6. Test espec\u00edfico no admin con fijos mockeados para asegurar captura de mutaci\u00f3n 10
    usuario.is_admin = False
    db_int.commit()
    with patch("app.services.permitirse_service.calcular_compromisos_mensuales") as mock_comp:
        mock_comp.return_value = ResultadoCompromisos(
            cuotas=Decimal("0"),
            suscripciones=Decimal("55000"),
            fijos=Decimal("50000"),
            total=Decimal("105000"),
            deudas_y_suscripciones=Decimal("55000"),
            moneda="ARS",
        )
        datos_check_mut10 = recolectar(db_int, usuario, hoy=hoy)
        assert datos_check_mut10.fijos == Decimal("0.00"), "Para no admin los fijos DEBEN ser 0.00"



