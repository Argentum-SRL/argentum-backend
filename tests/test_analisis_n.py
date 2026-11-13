# -*- coding: utf-8 -*-
"""
tests/test_analisis_n.py
Pruebas exhaustivas para el módulo Análisis (TN1 a TN13 y TN16) según decisión fase_n_analisis.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.analisis_informes_service import obtener_proyeccion_compromisos
from app.services.analisis_service import (
    generar_ventana_ciclos,
    obtener_comparativa_ciclos,
    obtener_resumen_analisis,
)
from app.services.dashboard_service import calcular_balance_ciclo, get_ciclo_fechas
from app.utils.fecha import TZ_ARGENTINA


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
                    import uuid
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    import uuid
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


def _crear_usuario(
    db: Session,
    ciclo_tipo: CicloTipo = CicloTipo.DIA_FIJO,
    ciclo_valor: str = "1",
    email: str | None = None,
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email or f"user_{uuid4().hex[:8]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=ciclo_tipo,
        ciclo_valor=ciclo_valor,
        ciclo_ajuste_direccion=None,
        fecha_registro=datetime(2025, 1, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(u)
    db.commit()
    return u


def _crear_billetera(db: Session, usuario_id: UUID, moneda: Moneda = Moneda.ARS) -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre=f"Billetera {moneda.value}",
        moneda=moneda,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=None,
        es_inversion=False,
    )
    db.add(b)
    db.commit()
    return b


def _crear_categoria(db: Session, nombre: str, tipo: TipoCategoria = TipoCategoria.EGRESO) -> Categoria:
    c = Categoria(
        id=uuid4(),
        nombre=nombre,
        tipo=tipo,
        orden=1,
    )
    db.add(c)
    db.commit()
    return c


def _crear_subcategoria(db: Session, categoria_id: UUID, nombre: str) -> Subcategoria:
    s = Subcategoria(
        id=uuid4(),
        categoria_id=categoria_id,
        nombre=nombre,
    )
    db.add(s)
    db.commit()
    return s


def _crear_transaccion(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID,
    monto: Decimal,
    fecha: date,
    tipo: TipoTransaccion = TipoTransaccion.EGRESO,
    moneda: Moneda = Moneda.ARS,
    categoria_id: UUID | None = None,
    subcategoria_id: UUID | None = None,
    reintegro_de_id: UUID | None = None,
    tipo_espejo: str | None = None,
    tarjeta_id: UUID | None = None,
) -> Transaccion:
    t = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        tipo=tipo,
        monto=monto,
        moneda=moneda,
        fecha=fecha,
        descripcion="Movimiento de prueba",
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        reintegro_de_id=reintegro_de_id,
        tipo_espejo=tipo_espejo,
        tarjeta_id=tarjeta_id,
    )
    db.add(t)
    db.commit()
    return t


# ── TN1: Ciclos día fijo 1 ──────────────────────────────────────────────────
def test_tn1_ciclos_dia_fijo_1(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    hoy = date(2026, 11, 13)
    ciclos = generar_ventana_ciclos(usuario, hoy, 3)

    assert len(ciclos) == 3

    c0, c1, c2 = ciclos
    assert c0["fecha_inicio"] == date(2026, 9, 1)
    assert c0["fecha_fin"] == date(2026, 9, 30)
    assert c0["es_actual"] is False

    assert c1["fecha_inicio"] == date(2026, 10, 1)
    assert c1["fecha_fin"] == date(2026, 10, 31)
    assert c1["es_actual"] is False

    assert c2["fecha_inicio"] == date(2026, 11, 1)
    assert c2["fecha_fin"] == date(2026, 11, 30)
    assert c2["es_actual"] is True


# ── TN2: Ciclos con día hábil y último viernes ──────────────────────────────
def test_tn2_ciclos_dia_habil_y_ultimo_viernes(db: Session):
    hoy = date(2026, 11, 13)

    # Caso A: DIA_HABIL "1" (primer_dia_habil)
    usuario_habil = _crear_usuario(db, CicloTipo.REGLA, "primer_dia_habil")
    ciclos_habil = generar_ventana_ciclos(usuario_habil, hoy, 4)
    assert len(ciclos_habil) == 4

    # Verificar contra get_ciclo_fechas secuencial hacia atrás
    esperados_habil = []
    fecha_ref = hoy
    for _ in range(4):
        inicio, fin = get_ciclo_fechas(usuario_habil, fecha_ref)
        esperados_habil.append((inicio, fin))
        fecha_ref = inicio - timedelta(days=1)
    esperados_habil.reverse()

    for c, (ini_esp, fin_esp) in zip(ciclos_habil, esperados_habil):
        assert c["fecha_inicio"] == ini_esp
        assert c["fecha_fin"] == fin_esp

    # Caso B: ULTIMO_VIERNES (ultimo_viernes)
    usuario_viernes = _crear_usuario(db, CicloTipo.REGLA, "ultimo_viernes")
    ciclos_viernes = generar_ventana_ciclos(usuario_viernes, hoy, 4)
    assert len(ciclos_viernes) == 4

    esperados_viernes = []
    fecha_ref = hoy
    for _ in range(4):
        inicio, fin = get_ciclo_fechas(usuario_viernes, fecha_ref)
        esperados_viernes.append((inicio, fin))
        fecha_ref = inicio - timedelta(days=1)
    esperados_viernes.reverse()

    for c, (ini_esp, fin_esp) in zip(ciclos_viernes, esperados_viernes):
        assert c["fecha_inicio"] == ini_esp
        assert c["fecha_fin"] == fin_esp


# ── TN3: Sin anclaje ─────────────────────────────────────────────────────────
def test_tn3_sin_anclaje(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    hoy = date(2026, 11, 13)

    # Sueldo 1.000.000 el 28/09 y el 29/10
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("1000000"), date(2026, 9, 28), TipoTransaccion.INGRESO)
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("1000000"), date(2026, 10, 29), TipoTransaccion.INGRESO)

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="solo_ars", cantidad_ciclos=3, hoy=hoy
    )

    serie = res["serie_ciclos"]
    c_sept = serie[0]
    c_oct = serie[1]

    assert c_sept["fecha_inicio"] in ("2026-09-01", date(2026, 9, 1))
    assert c_sept["fecha_fin"] in ("2026-09-30", date(2026, 9, 30))
    assert c_sept["ingresos"] == 1_000_000.0

    assert c_oct["fecha_inicio"] in ("2026-10-01", date(2026, 10, 1))
    assert c_oct["fecha_fin"] in ("2026-10-31", date(2026, 10, 31))
    assert c_oct["ingresos"] == 1_000_000.0


# ── TN4: Reintegro resta ─────────────────────────────────────────────────────
def test_tn4_reintegro_resta(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    cat_alim = _crear_categoria(db, "Alimentación")
    sub_super = _crear_subcategoria(db, cat_alim.id, "Supermercado")
    hoy = date(2026, 11, 13)

    # Gasto 100.000 el 05/10
    gasto = _crear_transaccion(
        db, usuario.id, billetera.id, Decimal("100000"), date(2026, 10, 5),
        categoria_id=cat_alim.id, subcategoria_id=sub_super.id
    )
    # Espejo reintegro -25.000 el 10/10
    _crear_transaccion(
        db, usuario.id, billetera.id, Decimal("-25000"), date(2026, 10, 10),
        categoria_id=cat_alim.id, subcategoria_id=sub_super.id,
        reintegro_de_id=gasto.id, tipo_espejo="reintegro"
    )

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="solo_ars", cantidad_ciclos=2, hoy=hoy
    )

    c_oct = res["serie_ciclos"][0]  # Octubre (anterior)
    assert c_oct["gastos"] == 75_000.0

    desglose = res["desglose_categorias"]
    alim_item = next(item for item in desglose if item["categoria_id"] == str(cat_alim.id))
    assert alim_item["monto"] == 75_000.0
    assert alim_item["subcategorias"][0]["monto"] == 75_000.0

    # Comprobación de paridad con calcular_balance_ciclo
    bal_app = calcular_balance_ciclo(
        db, usuario, fecha_desde_override=date(2026, 10, 1), fecha_hasta_override=date(2026, 10, 31)
    )
    assert float(bal_app["ars"]["egresos"]) == 75_000.0
    assert c_oct["gastos"] == float(bal_app["ars"]["egresos"])


# ── TN5: Categoría neta negativa ─────────────────────────────────────────────
def test_tn5_categoria_neta_negativa(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    cat_ocio = _crear_categoria(db, "Ocio")
    cat_alim = _crear_categoria(db, "Alimentación")
    hoy = date(2026, 11, 13)

    # Espejo -10.000 en Ocio en octubre
    gasto_padre = _crear_transaccion(
        db, usuario.id, billetera.id, Decimal("20000"), date(2026, 9, 20),
        categoria_id=cat_ocio.id
    )
    _crear_transaccion(
        db, usuario.id, billetera.id, Decimal("-10000"), date(2026, 10, 5),
        categoria_id=cat_ocio.id, reintegro_de_id=gasto_padre.id, tipo_espejo="reintegro"
    )
    # Gasto 50.000 en Alimentación en octubre
    _crear_transaccion(
        db, usuario.id, billetera.id, Decimal("50000"), date(2026, 10, 10),
        categoria_id=cat_alim.id
    )

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="solo_ars", cantidad_ciclos=2, hoy=hoy
    )

    # Gastos totales del período incluyen Ocio: 50.000 - 10.000 = 40.000
    assert res["serie_ciclos"][0]["gastos"] == 40_000.0

    desglose = res["desglose_categorias"]
    # Ocio no aparece porque da <= 0
    assert not any(item["categoria_id"] == str(cat_ocio.id) for item in desglose)

    # Alimentación aparece con 50.000 y porcentaje 100.0
    alim_item = next(item for item in desglose if item["categoria_id"] == str(cat_alim.id))
    assert alim_item["monto"] == 50_000.0
    assert alim_item["porcentaje_del_total"] == 100.0


# ── TN6: Ciclo en curso ──────────────────────────────────────────────────────
def test_tn6_ciclo_en_curso(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    hoy = date(2026, 11, 13)

    # Noviembre (en curso: 13 días): 60.000
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("60000"), date(2026, 11, 5))

    # Octubre: 50.000 en los primeros 13 días (05/10) y 150.000 después (20/10) -> total 200.000
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("50000"), date(2026, 10, 5))
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("150000"), date(2026, 10, 20))

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="solo_ars", cantidad_ciclos=2, hoy=hoy
    )

    kpis = res["kpis"]
    # Compara 60.000 vs 50.000 -> +20.0%
    assert kpis["variacion_gasto_vs_anterior_pct"] == 20.0
    assert kpis["variacion_texto"] == "Comparado con los mismos 13 días del ciclo anterior"

    # Insight variacion-gasto
    insight_var = next(i for i in res["insights"] if i["id"] == "variacion-gasto")
    texto_insight = f"{insight_var.get('titulo', '')} {insight_var.get('descripcion', '')}"
    assert "En lo que va del ciclo gastaste 20% más que en los mismos días del anterior" in texto_insight
    assert not any(
        "Bajaste" in f"{i.get('titulo', '')} {i.get('descripcion', '')}"
        for i in res["insights"]
    )


# ── TN7: Promedios sin el ciclo en curso ─────────────────────────────────────
def test_tn7_promedios_sin_el_ciclo_en_curso(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    hoy = date(2026, 11, 13)

    # Septiembre: 100.000
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("100000"), date(2026, 9, 15))
    # Octubre: 200.000
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("200000"), date(2026, 10, 15))
    # Noviembre (en curso): 60.000
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("60000"), date(2026, 11, 5))

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="solo_ars", cantidad_ciclos=3, hoy=hoy
    )

    # Promedio histórico sólo con septiembre y octubre: (100k + 200k)/2 = 150.000
    assert res["kpis"]["promedio_gasto_ciclo"] == 150_000.0


# ── TN8: Tasa de ahorro null ─────────────────────────────────────────────────
def test_tn8_tasa_ahorro_null(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    hoy = date(2026, 11, 13)

    # Caso 1: ingresos = 0 y gastos = 50.000 en octubre
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("50000"), date(2026, 10, 15))

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="solo_ars", cantidad_ciclos=2, hoy=hoy
    )
    assert res["kpis"]["tasa_ahorro_pct"] is None
    assert res["serie_ciclos"][0]["tasa_ahorro_pct"] is None
    assert not any(i["id"] == "tasa-ahorro" for i in res["insights"])

    # Caso 2: ingresos = 10.000 y gastos = 300.000 (balance = -290.000 < -ingresos)
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("10000"), date(2026, 9, 10), TipoTransaccion.INGRESO)
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("300000"), date(2026, 9, 15), TipoTransaccion.EGRESO)

    res2 = obtener_resumen_analisis(
        db, usuario, moneda_modo="solo_ars", cantidad_ciclos=3, hoy=hoy
    )
    c_sept = res2["serie_ciclos"][0]
    assert c_sept["tasa_ahorro_pct"] is None


# ── TN9: Dólar del día ───────────────────────────────────────────────────────
def test_tn9_dolar_del_dia(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera_usd = _crear_billetera(db, usuario.id, Moneda.USD)
    hoy = date(2026, 11, 13)

    # Dos gastos de US$10 en octubre
    _crear_transaccion(db, usuario.id, billetera_usd.id, Decimal("10"), date(2026, 10, 5), moneda=Moneda.USD)
    _crear_transaccion(db, usuario.id, billetera_usd.id, Decimal("10"), date(2026, 10, 20), moneda=Moneda.USD)

    # Cotizaciones oficiales vendedor
    c1 = CotizacionDolar(fecha=date(2026, 10, 5), tipo="oficial", compra=Decimal("1300"), venta=Decimal("1400"), promedio=Decimal("1350"))
    c2 = CotizacionDolar(fecha=date(2026, 10, 20), tipo="oficial", compra=Decimal("1400"), venta=Decimal("1500"), promedio=Decimal("1450"))
    db.add_all([c1, c2])
    db.commit()

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="consolidado_ars", cantidad_ciclos=2, hoy=hoy
    )

    # Gastos de octubre: 10 * 1400 + 10 * 1500 = 14000 + 15000 = 29000
    assert res["serie_ciclos"][0]["gastos"] == 29_000.0
    assert res["movimientos_sin_cotizacion"] == 0


# ── TN10: Sin cotización ─────────────────────────────────────────────────────
def test_tn10_sin_cotizacion(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera_usd = _crear_billetera(db, usuario.id, Moneda.USD)
    hoy = date(2026, 11, 13)

    # Tabla cotizaciones_dolar vacía y un gasto de US$10
    _crear_transaccion(db, usuario.id, billetera_usd.id, Decimal("10"), date(2026, 10, 5), moneda=Moneda.USD)

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="consolidado_ars", cantidad_ciclos=2, hoy=hoy
    )

    assert res["serie_ciclos"][0]["gastos"] == 0.0
    assert res["movimientos_sin_cotizacion"] == 1

    # Verificar que no hay ningún "1200" inventado
    import json
    res_str = json.dumps(res)
    assert "1200" not in res_str


# ── TN11: Filtro de categoría ────────────────────────────────────────────────
def test_tn11_filtro_de_categoria(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    cat = _crear_categoria(db, "Alimentación")
    hoy = date(2026, 11, 13)

    _crear_transaccion(db, usuario.id, billetera.id, Decimal("50000"), date(2026, 10, 10), categoria_id=cat.id)
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("100000"), date(2026, 10, 1), TipoTransaccion.INGRESO)

    res = obtener_resumen_analisis(
        db, usuario, moneda_modo="solo_ars", cantidad_ciclos=2, categoria_id=cat.id, hoy=hoy
    )

    kpis = res["kpis"]
    assert kpis["ingresos_totales"] is None
    assert kpis["balance_neto"] is None
    assert kpis["tasa_ahorro_pct"] is None

    for c in res["serie_ciclos"]:
        assert c["ingresos"] is None
        assert c["balance"] is None
        assert c["tasa_ahorro_pct"] is None


# ── TN12: Comparativa ciclo a ciclo ──────────────────────────────────────────
def test_tn12_comparativa(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    cat = _crear_categoria(db, "Alimentación")
    hoy = date(2026, 11, 13)

    # Octubre: gasto 100k y reintegro -25k -> 75k
    g1 = _crear_transaccion(db, usuario.id, billetera.id, Decimal("100000"), date(2026, 10, 5), categoria_id=cat.id)
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("-25000"), date(2026, 10, 10), categoria_id=cat.id, reintegro_de_id=g1.id, tipo_espejo="reintegro")

    # Noviembre: gasto 60k
    _crear_transaccion(db, usuario.id, billetera.id, Decimal("60000"), date(2026, 11, 5), categoria_id=cat.id)

    res = obtener_comparativa_ciclos(
        db, usuario,
        moneda_modo="solo_ars",
        ciclo_a_clave=None,  # Por defecto noviembre
        ciclo_b_clave=None,  # Por defecto octubre
        hoy=hoy,
    )

    bal_nov = calcular_balance_ciclo(
        db, usuario, fecha_desde_override=date(2026, 11, 1), fecha_hasta_override=hoy
    )
    bal_oct = calcular_balance_ciclo(
        db, usuario, fecha_desde_override=date(2026, 10, 1), fecha_hasta_override=date(2026, 10, 31)
    )

    assert res["resumen"]["gasto_a"] == float(bal_nov["ars"]["egresos"])
    assert res["resumen"]["gasto_b"] == float(bal_oct["ars"]["egresos"])
    assert res["resumen"]["gasto_b"] == 75_000.0


# ── TN13: Proyección de compromisos por ciclos ───────────────────────────────
def test_tn13_proyeccion_por_ciclos(db: Session):
    usuario = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, usuario.id, Moneda.ARS)
    hoy = date(2026, 11, 13)

    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa Galicia",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=5,
    )
    db.add(tarjeta)
    db.commit()

    tx_padre = _crear_transaccion(
        db, usuario.id, billetera.id, Decimal("30000"), date(2026, 8, 1),
        tarjeta_id=tarjeta.id
    )

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        transaccion_padre_id=tx_padre.id,
        tarjeta_id=tarjeta.id,
        descripcion="Compra 3 cuotas",
        monto_total=Decimal("30000"),
        cantidad_cuotas=3,
        total_financiado=Decimal("30000"),
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
    )
    db.add(grupo)
    db.commit()

    # Cuota 1: vencida el 10/09, sin pagar
    c1 = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_padre.id,
        numero_cuota=1,
        monto_proyectado=Decimal("10000"),
        fecha_vencimiento=date(2026, 9, 10),
        pagada=False,
    )
    # Cuota 2: vencida el 10/10, pagada
    c2 = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_padre.id,
        numero_cuota=2,
        monto_proyectado=Decimal("10000"),
        fecha_vencimiento=date(2026, 10, 10),
        pagada=True,
    )
    # Cuota 3: a vencer el 10/12, sin pagar
    c3 = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_padre.id,
        numero_cuota=3,
        monto_proyectado=Decimal("10000"),
        fecha_vencimiento=date(2026, 12, 10),
        pagada=False,
    )
    db.add_all([c1, c2, c3])
    db.commit()

    res = obtener_proyeccion_compromisos(db, usuario, meses=3, hoy=hoy)

    serie = res["serie_mensual"]
    # Ciclo 0 (01/11 al 30/11): debe sumar la cuota vencida del 10/09
    assert "01/11" in serie[0]["rango_texto"] and "30/11" in serie[0]["rango_texto"]
    assert serie[0]["monto_cuotas"] == 10_000.0

    # Ciclo 1 (01/12 al 31/12): debe sumar la cuota 3
    assert "01/12" in serie[1]["rango_texto"] and "31/12" in serie[1]["rango_texto"]
    assert serie[1]["monto_cuotas"] == 10_000.0


# ── TN16: Estático backend N-8 ───────────────────────────────────────────────
def test_tn16_estatico_backend_n8():
    backend_root = Path(__file__).resolve().parent.parent
    archivos = [
        backend_root / "app" / "services" / "analisis_service.py",
        backend_root / "app" / "services" / "analisis_informes_service.py",
    ]

    palabras_prohibidas = [
        "Flujo neto",
        "Tasa de ahorro sólida",
        "contención",
        "desvío",
        "1200",
    ]

    hallazgos = []
    for arch in archivos:
        contenido = arch.read_text(encoding="utf-8")
        for palabra in palabras_prohibidas:
            if palabra.lower() in contenido.lower():
                hallazgos.append(f"{arch.name}: contiene '{palabra}'")

    assert hallazgos == []
