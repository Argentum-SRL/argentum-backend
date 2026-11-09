# -*- coding: utf-8 -*-
"""
tests/test_consejos_datos.py
Pruebas de integración en SQLite para recolectar datos y evaluar consejos (fase_i1_consejos).
"""
from __future__ import annotations

import sqlite3
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
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
)
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, CicloTipo, Moneda
from app.services import fin_de_mes_service
from app.services.fin_de_mes_service import calcular_fin_de_mes
from app.services.frescura_service import Frescura
from app.services.inflacion_esperada_service import InflacionEsperada
from app.services.consejos_datos import recolectar
from app.services.consejos_reglas import evaluar_todos, ordenar
from app.services.consejos_service import emitir_si_corresponde, consejo_activo


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


@pytest.fixture(name="inflacion")
def inflacion_fixture() -> InflacionEsperada:
    return InflacionEsperada(
        tasas=[Decimal("2.1")],
        fuente_texto="REM de octubre",
        es_respaldo=False,
    )


def _crear_categorias(db: Session) -> dict[tuple[str, str], Subcategoria]:
    """Crea categorías y subcategorías reales para pruebas."""
    cats_data = [
        ("Transporte", TipoCategoria.EGRESO, ["Taxi y apps"]),
        ("Alimentación", TipoCategoria.EGRESO, ["Kiosco", "Supermercado", "Verdulería", "Carnicería"]),
        ("Gastronomía", TipoCategoria.EGRESO, ["Cafetería", "Restaurantes", "Delivery"]),
        ("Vivienda", TipoCategoria.EGRESO, ["Luz", "Alquiler"]),
        ("Trabajo", TipoCategoria.INGRESO, ["Sueldo"]),
        ("Banco", TipoCategoria.EGRESO, ["Comisiones"]),
        ("Ocio", TipoCategoria.EGRESO, ["Salidas"]),
        ("Otros", TipoCategoria.EGRESO, []),
    ]
    subcats_map: dict[tuple[str, str], Subcategoria] = {}
    for cat_nom, tipo, sub_noms in cats_data:
        c = Categoria(
            id=uuid4(),
            nombre=cat_nom,
            tipo=tipo,
            estado=EstadoCategoria.ACTIVA,
        )
        db.add(c)
        for sn in sub_noms:
            s = Subcategoria(
                id=uuid4(),
                categoria_id=c.id,
                nombre=sn,
                estado=EstadoSubcategoria.ACTIVA,
            )
            db.add(s)
            subcats_map[(cat_nom, sn)] = s
    db.commit()
    return subcats_map


def _agregar_base(
    db: Session,
    usuario_id: uuid.UUID,
    billetera_id: uuid.UUID,
    subcats_map: dict[tuple[str, str], Subcategoria],
    k: Decimal = Decimal("1"),
    moneda: Moneda = Moneda.ARS,
    fecha: date = date(2026, 10, 10),
):
    """Agrega las 42 filas de egreso de base(k)."""
    filas = [
        ("Transporte", "Taxi y apps", 9, 2500),
        ("Alimentación", "Kiosco", 3, 1500),
        ("Gastronomía", "Cafetería", 5, 3000),
        ("Alimentación", "Supermercado", 10, 20000),
        ("Alimentación", "Verdulería", 4, 8000),
        ("Alimentación", "Carnicería", 4, 15000),
        ("Gastronomía", "Restaurantes", 2, 20000),
        ("Gastronomía", "Delivery", 3, 12000),
        ("Vivienda", "Luz", 1, 25000),
        ("Vivienda", "Alquiler", 1, 300000),
    ]
    for cat_nom, sub_nom, cant, monto in filas:
        sub = subcats_map[(cat_nom, sub_nom)]
        for _ in range(cant):
            db.add(
                Transaccion(
                    id=uuid4(),
                    usuario_id=usuario_id,
                    billetera_id=billetera_id,
                    categoria_id=sub.categoria_id,
                    subcategoria_id=sub.id,
                    monto=Decimal(str(monto)) * k,
                    moneda=moneda,
                    tipo=TipoTransaccion.EGRESO,
                    fecha=fecha,
                    metodo_pago=MetodoPago.DEBITO,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    origen=OrigenTransaccion.MANUAL,
                )
            )
    db.commit()


# ==============================================================================
# TEST USUARIO P
# ==============================================================================

def test_usuario_p(db: Session, inflacion: InflacionEsperada, monkeypatch):
    """
    P (principal ARS, ciclo día fijo 1):
    billetera "Cuenta" ARS 200.000;
    Trabajo > Sueldo 1.000.000 el 01/10/2026;
    base(1).
    recolectar: cerrados == [(01/10/2026, 31/10/2026)], 42 filas, ingresos_45 1, saldo_hoy_ars 200.000 y libre_ars == calcular_fin_de_mes(...).libre_ars.
    evaluar_todos → exactamente hormiga (texto de (a)) y colchon "Si un mes no entrara plata, lo que tenés alcanza para unos 8 días de tus gastos. Un primer objetivo es medio mes: $368.000.".
    emitir_si_corresponde → colchon con fecha_vence 09/11/2026; otra llamada el mismo día → None.
    """
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: None)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=date(2026, 11, 2),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )

    hoy = date(2026, 11, 2)

    p = Usuario(
        id=uuid4(),
        email="p@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(p)
    db.commit()

    cuenta = Billetera(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("200000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(cuenta)
    db.commit()

    subcats = _crear_categorias(db)

    # Trabajo > Sueldo 1.000.000 el 01/10/2026
    sub_sueldo = subcats[("Trabajo", "Sueldo")]
    db.add(
        Transaccion(
            id=uuid4(),
            usuario_id=p.id,
            billetera_id=cuenta.id,
            categoria_id=sub_sueldo.categoria_id,
            subcategoria_id=sub_sueldo.id,
            monto=Decimal("1000000"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.INGRESO,
            fecha=date(2026, 10, 1),
            metodo_pago=MetodoPago.TRANSFERENCIA,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            origen=OrigenTransaccion.MANUAL,
        )
    )
    db.commit()

    # base(1)
    _agregar_base(db, p.id, cuenta.id, subcats, k=Decimal("1"), moneda=Moneda.ARS, fecha=date(2026, 10, 10))

    datos = recolectar(db, p, hoy=hoy, inflacion=inflacion)
    assert datos.cerrados == [(date(2026, 10, 1), date(2026, 10, 31))]
    assert len(datos.filas) == 42
    assert datos.ingresos_45 == 1
    assert datos.saldo_hoy_ars == Decimal("200000")

    fdm = calcular_fin_de_mes(db, p, fecha_ref=hoy)
    assert datos.libre_ars == fdm.libre_ars
    assert datos.factor_cobertura == fdm.factor_cobertura
    assert datos.ipc == {}

    cands = evaluar_todos(datos)
    assert len(cands) == 2
    tipos = {c.tipo for c in cands}
    assert tipos == {"hormiga", "colchon"}

    c_hormiga = next(c for c in cands if c.tipo == "hormiga")
    assert c_hormiga.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos $38.000 por mes: $456.000 al año."

    c_colchon = next(c for c in cands if c.tipo == "colchon")
    assert c_colchon.texto == "Si un mes no entrara plata, lo que tenés alcanza para unos 8 días de tus gastos. Un primer objetivo es medio mes: $368.000."

    # emitir_si_corresponde → colchon con fecha_vence 09/11/2026
    emitido = emitir_si_corresponde(db, p, hoy=hoy, inflacion=inflacion)
    assert emitido is not None
    assert emitido.tipo == "colchon"
    assert emitido.fecha_vence == date(2026, 11, 9)

    # otra llamada el mismo día → None
    assert emitir_si_corresponde(db, p, hoy=hoy, inflacion=inflacion) is None

    # con una fila IPCCache ("2026-09", indice_acumulado 110) → ipc == {"2026-09": Decimal("110")}
    from app.models.tools import IPCCache
    db.add(IPCCache(fecha_dato="2026-09", indice_acumulado=110.0))
    db.commit()
    datos_con_ipc = recolectar(db, p, hoy=hoy, inflacion=inflacion)
    assert datos_con_ipc.ipc == {"2026-09": Decimal("110")}


# ==============================================================================
# TEST USUARIO D
# ==============================================================================

def test_usuario_d(db: Session, inflacion: InflacionEsperada, monkeypatch):
    """
    D (principal USD):
    billeteras "Dólares" USD 200 y "Pesos" ARS 50.000;
    CotizacionDolar oficial del 02/11/2026 compra 1.000 venta 1.050;
    Sueldo USD 1.000 el 01/10; base(0,001) en USD; Kiosco $1.500 en pesos el 10/10.
    evaluar_todos → exactamente hormiga USD ("... US$38 por mes: US$456 al año.") y plata_quieta "... pierden unos $1.000 por mes si quedan quietos...".
    emitir → la hormiga en dólares (vale 91.200 contra 12.222).
    """
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: None)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=date(2026, 11, 2),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )

    hoy = date(2026, 11, 2)

    db.add(
        CotizacionDolar(
            id=uuid4(),
            fecha=hoy,
            tipo="oficial",
            compra=Decimal("1000"),
            venta=Decimal("1050"),
            promedio=Decimal("1025"),
        )
    )
    db.commit()

    d_user = Usuario(
        id=uuid4(),
        email="d@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.USD,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(d_user)
    db.commit()

    b_usd = Billetera(
        id=uuid4(),
        usuario_id=d_user.id,
        nombre="Dólares",
        moneda=Moneda.USD,
        saldo_actual=Decimal("200"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_ars = Billetera(
        id=uuid4(),
        usuario_id=d_user.id,
        nombre="Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_usd, b_ars])
    db.commit()

    subcats = _crear_categorias(db)

    # Sueldo USD 1.000 el 01/10
    sub_sueldo = subcats[("Trabajo", "Sueldo")]
    db.add(
        Transaccion(
            id=uuid4(),
            usuario_id=d_user.id,
            billetera_id=b_usd.id,
            categoria_id=sub_sueldo.categoria_id,
            subcategoria_id=sub_sueldo.id,
            monto=Decimal("1000"),
            moneda=Moneda.USD,
            tipo=TipoTransaccion.INGRESO,
            fecha=date(2026, 10, 1),
            metodo_pago=MetodoPago.TRANSFERENCIA,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            origen=OrigenTransaccion.MANUAL,
        )
    )
    # base(0.001) en USD
    _agregar_base(db, d_user.id, b_usd.id, subcats, k=Decimal("0.001"), moneda=Moneda.USD, fecha=date(2026, 10, 10))

    # Kiosco $1.500 en pesos el 10/10
    sub_kiosco = subcats[("Alimentación", "Kiosco")]
    db.add(
        Transaccion(
            id=uuid4(),
            usuario_id=d_user.id,
            billetera_id=b_ars.id,
            categoria_id=sub_kiosco.categoria_id,
            subcategoria_id=sub_kiosco.id,
            monto=Decimal("1500"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=date(2026, 10, 10),
            metodo_pago=MetodoPago.DEBITO,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            origen=OrigenTransaccion.MANUAL,
        )
    )
    db.commit()

    datos = recolectar(db, d_user, hoy=hoy, inflacion=inflacion)
    cands = evaluar_todos(datos)
    assert len(cands) == 2
    tipos = {c.tipo for c in cands}
    assert tipos == {"hormiga", "plata_quieta"}

    c_hormiga = next(c for c in cands if c.tipo == "hormiga")
    assert c_hormiga.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos US$38 por mes: US$456 al año."

    c_pq = next(c for c in cands if c.tipo == "plata_quieta")
    assert "Tenés unos $49.000 que este mes no vas a necesitar." in c_pq.texto
    assert "Con la inflación esperada (2,1% en diciembre, REM de octubre), pierden unos $1.000 por mes si quedan quietos." in c_pq.texto

    # emitir → la hormiga en dólares (vale 91.200 contra 12.222)
    emitido = emitir_si_corresponde(db, d_user, hoy=hoy, inflacion=inflacion)
    assert emitido is not None
    assert emitido.tipo == "hormiga"
    assert emitido.moneda == "USD"


# ==============================================================================
# TEST USUARIO M
# ==============================================================================

def test_usuario_m(db: Session, inflacion: InflacionEsperada, monkeypatch):
    """
    M (principal ARS con secundaria):
    billeteras "Cuenta" ARS 200.000 y "Dólares" USD 100;
    CotizacionDolar oficial del 02/11/2026 compra 1.000 venta 1.050;
    Sueldo $1.000.000 el 01/10; base(1) en pesos y base(0,002) en dólares.
    evaluar_todos → hormiga ARS, hormiga USD ("... US$75 por mes: US$900 al año.") y colchon "... medio mes: $1.103.000.".
    ordenar → [colchon, hormiga USD, hormiga ARS].
    """
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: None)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=date(2026, 11, 2),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )

    hoy = date(2026, 11, 2)

    db.add(
        CotizacionDolar(
            id=uuid4(),
            fecha=hoy,
            tipo="oficial",
            compra=Decimal("1000"),
            venta=Decimal("1050"),
            promedio=Decimal("1025"),
        )
    )
    db.commit()

    m_user = Usuario(
        id=uuid4(),
        email="m@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        moneda_secundaria_activa=True,
        tipo_dolar="oficial",
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(m_user)
    db.commit()

    b_ars = Billetera(
        id=uuid4(),
        usuario_id=m_user.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("200000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=m_user.id,
        nombre="Dólares",
        moneda=Moneda.USD,
        saldo_actual=Decimal("100"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_ars, b_usd])
    db.commit()

    subcats = _crear_categorias(db)

    # Sueldo $1.000.000 el 01/10
    sub_sueldo = subcats[("Trabajo", "Sueldo")]
    db.add(
        Transaccion(
            id=uuid4(),
            usuario_id=m_user.id,
            billetera_id=b_ars.id,
            categoria_id=sub_sueldo.categoria_id,
            subcategoria_id=sub_sueldo.id,
            monto=Decimal("1000000"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.INGRESO,
            fecha=date(2026, 10, 1),
            metodo_pago=MetodoPago.TRANSFERENCIA,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            origen=OrigenTransaccion.MANUAL,
        )
    )
    # base(1) en pesos
    _agregar_base(db, m_user.id, b_ars.id, subcats, k=Decimal("1"), moneda=Moneda.ARS, fecha=date(2026, 10, 10))
    # base(0.002) en dólares
    _agregar_base(db, m_user.id, b_usd.id, subcats, k=Decimal("0.002"), moneda=Moneda.USD, fecha=date(2026, 10, 10))

    datos = recolectar(db, m_user, hoy=hoy, inflacion=inflacion)
    cands = evaluar_todos(datos)
    assert len(cands) == 3

    c_h_ars = next(c for c in cands if c.tipo == "hormiga" and c.moneda == "ARS")
    assert c_h_ars is not None

    c_h_usd = next(c for c in cands if c.tipo == "hormiga" and c.moneda == "USD")
    assert c_h_usd.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos US$75 por mes: US$900 al año."

    c_colchon = next(c for c in cands if c.tipo == "colchon")
    assert c_colchon.texto == "Si un mes no entrara plata, lo que tenés alcanza para unos 4 días de tus gastos (con tus dólares a dólar oficial). Un primer objetivo es medio mes: $1.103.000."

    # ordenar → [colchon, hormiga USD, hormiga ARS]
    ord_cands = ordenar(cands, [], Decimal("1000"))
    assert [c.tipo for c in ord_cands] == ["colchon", "hormiga", "hormiga"]
    assert [c.moneda for c in ord_cands] == ["ARS", "USD", "ARS"]


# ==============================================================================
# TEST DE AISLAMIENTO, FRESURA Y CERO COMMITS
# ==============================================================================

def test_p_con_datos_viejos(db: Session, inflacion: InflacionEsperada, monkeypatch):
    """(d) P con datos viejos parcheado → []."""
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: None)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=date(2026, 10, 15),
            dias_sin_cargar=18,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=True,
        ),
    )

    p = Usuario(
        id=uuid4(),
        email="p_viejo@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(p)
    db.commit()

    cuenta = Billetera(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("200000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(cuenta)
    db.commit()

    subcats = _crear_categorias(db)
    _agregar_base(db, p.id, cuenta.id, subcats, k=Decimal("1"), moneda=Moneda.ARS)

    datos = recolectar(db, p, hoy=date(2026, 11, 2), inflacion=inflacion)
    assert datos.datos_viejos is True
    assert evaluar_todos(datos) == []


def test_aislamiento_p_y_p2(db: Session, inflacion: InflacionEsperada, monkeypatch):
    """(e) P y P2 con los mismos datos: emitir solo para P → consejo_activo(P2) None."""
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: None)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=date(2026, 11, 2),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )

    hoy = date(2026, 11, 2)

    p1 = Usuario(id=uuid4(), email="p1@argentum.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS, ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1", onboarding_completo=True)
    p2 = Usuario(id=uuid4(), email="p2@argentum.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS, ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1", onboarding_completo=True)
    db.add_all([p1, p2])
    db.commit()

    c1 = Billetera(id=uuid4(), usuario_id=p1.id, nombre="C1", moneda=Moneda.ARS, saldo_actual=Decimal("200000"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    c2 = Billetera(id=uuid4(), usuario_id=p2.id, nombre="C2", moneda=Moneda.ARS, saldo_actual=Decimal("200000"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    db.add_all([c1, c2])
    db.commit()

    subcats = _crear_categorias(db)

    _agregar_base(db, p1.id, c1.id, subcats)
    _agregar_base(db, p2.id, c2.id, subcats)

    emitido = emitir_si_corresponde(db, p1, hoy=hoy, inflacion=inflacion)
    assert emitido is not None
    assert consejo_activo(db, p1.id, hoy=hoy) is not None
    assert consejo_activo(db, p2.id, hoy=hoy) is None


def test_recolectar_sin_red_ni_commit(db: Session, inflacion: InflacionEsperada, monkeypatch):
    """(h) recolectar + evaluar_todos de P con commit y flush contados = 0 y la red bloqueada."""
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: None)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=date(2026, 11, 2),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )

    hoy = date(2026, 11, 2)

    p = Usuario(
        id=uuid4(),
        email="p_seguridad@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(p)
    db.commit()

    cuenta = Billetera(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("200000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(cuenta)
    db.commit()

    subcats = _crear_categorias(db)
    _agregar_base(db, p.id, cuenta.id, subcats)

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

    with patch("socket.socket", fail_network), patch("urllib.request.urlopen", fail_network):
        datos = recolectar(db, p, hoy=hoy, inflacion=inflacion)
        _ = evaluar_todos(datos)

    assert commit_count == 0
    assert flush_count == 0


def test_sin_movimientos(db: Session, inflacion: InflacionEsperada, monkeypatch):
    """Sin movimientos → hay_datos False y []."""
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: None)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=date(2026, 11, 2),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )

    p = Usuario(
        id=uuid4(),
        email="p_sin_mov@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(p)
    db.commit()

    datos = recolectar(db, p, hoy=date(2026, 11, 2), inflacion=inflacion)
    assert datos.hay_datos is False
    assert evaluar_todos(datos) == []
