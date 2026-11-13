# -*- coding: utf-8 -*-
"""
tests/test_analisis_n2.py
Pruebas para fase_n2_analisis (TN17 a TN22).
"""
from __future__ import annotations

import ast
from datetime import date, datetime
from decimal import Decimal
import glob
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.analisis_proyeccion_service import obtener_proyeccion_compromisos
from app.services.analisis_resumen_service import obtener_resumen_analisis
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


def _crear_tarjeta(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID,
    percepcion: Decimal = Decimal("30.00"),
) -> TarjetaCredito:
    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        nombre="Tarjeta Test",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=5,
        percepcion_moneda_extranjera=percepcion,
    )
    db.add(t)
    db.commit()
    return t


def _crear_transaccion(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID,
    tipo: TipoTransaccion,
    monto: Decimal,
    fecha: date,
    moneda: Moneda = Moneda.ARS,
    categoria_id: UUID | None = None,
) -> Transaccion:
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        tipo=tipo,
        monto=monto,
        moneda=moneda,
        fecha=fecha,
        categoria_id=categoria_id,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(tx)
    db.commit()
    return tx


def _crear_cotizacion(
    db: Session,
    fecha: date,
    venta: Decimal = Decimal("1500"),
    tipo: str = "oficial",
) -> CotizacionDolar:
    cot = CotizacionDolar(
        fecha=fecha,
        tipo=tipo,
        compra=venta - Decimal("20"),
        venta=venta,
        promedio=venta - Decimal("10"),
    )
    db.add(cot)
    db.commit()
    return cot


def test_tn17_proyeccion_con_tarjeta(db: Session):
    """
    TN17: oficial vendedor de hoy 1.500; tarjeta con percepcion_moneda_extranjera 30.
    Grupo en pesos con tarjeta, cuota de 150.000 vence ciclo actual -> consolidado_usd: 100.0.
    Grupo en dólares con tarjeta, cuota de US$10 -> consolidado_ars: 19.500.
    """
    hoy = date(2026, 11, 13)
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_ars = _crear_billetera(db, user.id, Moneda.ARS)
    tarjeta = _crear_tarjeta(db, user.id, b_ars.id, percepcion=Decimal("30.00"))
    _crear_cotizacion(db, hoy, venta=Decimal("1500"))

    # 1. Grupo en pesos con tarjeta: cuota 150.000 vence 15/11/2026
    tx_padre_ars = _crear_transaccion(db, user.id, b_ars.id, TipoTransaccion.EGRESO, Decimal("150000"), date(2026, 11, 15))
    g_ars = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx_padre_ars.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Plan Pesos",
        cantidad_cuotas=1,
        monto_total=Decimal("150000"),
        total_financiado=Decimal("150000"),
    )
    db.add(g_ars)
    c_ars = Cuota(
        id=uuid4(),
        grupo_id=g_ars.id,
        transaccion_id=tx_padre_ars.id,
        numero_cuota=1,
        monto_proyectado=Decimal("150000"),
        fecha_vencimiento=date(2026, 11, 15),
        pagada=False,
    )
    db.add(c_ars)
    db.commit()

    res_usd = obtener_proyeccion_compromisos(db, user, moneda_modo="consolidado_usd", meses=6, hoy=hoy)
    assert res_usd["serie_mensual"][0]["monto_cuotas"] == 100.0

    # 2. Grupo en dólares con tarjeta (usuario 2 para aislar cuotas del ciclo actual)
    user2 = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b2_ars = _crear_billetera(db, user2.id, Moneda.ARS)
    tarjeta2 = _crear_tarjeta(db, user2.id, b2_ars.id, percepcion=Decimal("30.00"))
    tx_padre_usd = _crear_transaccion(db, user2.id, b2_ars.id, TipoTransaccion.EGRESO, Decimal("10"), date(2026, 11, 15), moneda=Moneda.USD)

    g_usd = GrupoCuotas(
        id=uuid4(),
        usuario_id=user2.id,
        tarjeta_id=tarjeta2.id,
        transaccion_padre_id=tx_padre_usd.id,
        moneda=Moneda.USD,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Plan Dólares",
        cantidad_cuotas=1,
        monto_total=Decimal("10"),
        total_financiado=Decimal("10"),
    )
    db.add(g_usd)
    c_usd = Cuota(
        id=uuid4(),
        grupo_id=g_usd.id,
        transaccion_id=tx_padre_usd.id,
        numero_cuota=1,
        monto_proyectado=Decimal("10"),
        fecha_vencimiento=date(2026, 11, 15),
        pagada=False,
    )
    db.add(c_usd)
    db.commit()

    res_ars = obtener_proyeccion_compromisos(db, user2, moneda_modo="consolidado_ars", meses=6, hoy=hoy)
    assert res_ars["serie_mensual"][0]["monto_cuotas"] == 19500.0


def test_tn18_proyeccion_sin_tarjeta(db: Session):
    """
    TN18: grupo en dólares sin tarjeta, cuota de US$10 -> consolidado_ars: 15.000.
    """
    hoy = date(2026, 11, 13)
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_ars = _crear_billetera(db, user.id, Moneda.ARS)
    _crear_cotizacion(db, hoy, venta=Decimal("1500"))
    tx_padre = _crear_transaccion(db, user.id, b_ars.id, TipoTransaccion.EGRESO, Decimal("10"), date(2026, 11, 15), moneda=Moneda.USD)

    g_usd = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=None,
        transaccion_padre_id=tx_padre.id,
        moneda=Moneda.USD,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Plan sin tarjeta",
        cantidad_cuotas=1,
        monto_total=Decimal("10"),
        total_financiado=Decimal("10"),
    )
    db.add(g_usd)
    c_usd = Cuota(
        id=uuid4(),
        grupo_id=g_usd.id,
        transaccion_id=tx_padre.id,
        numero_cuota=1,
        monto_proyectado=Decimal("10"),
        fecha_vencimiento=date(2026, 11, 15),
        pagada=False,
    )
    db.add(c_usd)
    db.commit()

    res = obtener_proyeccion_compromisos(db, user, moneda_modo="consolidado_ars", meses=6, hoy=hoy)
    assert res["serie_mensual"][0]["monto_cuotas"] == 15000.0


def test_tn19_aviso_cotizacion(db: Session):
    """
    TN19: tabla cotizaciones_dolar vacía.
    Un gasto de US$10 -> consolidado_ars: aviso_cotizacion = "1 movimiento en dólares no se pudo pasar a pesos porque falta la cotización de ese día."
    Dos gastos en pesos en fechas distintas -> consolidado_usd: "2 movimientos en pesos no se pudieron pasar a dólares porque falta la cotización de esos días."
    Con cotizaciones cargadas -> null.
    """
    hoy = date(2026, 11, 13)
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_usd = _crear_billetera(db, user.id, Moneda.USD)

    # 1. Un gasto en USD sin cotización
    _crear_transaccion(db, user.id, b_usd.id, TipoTransaccion.EGRESO, Decimal("10.00"), date(2026, 11, 5), Moneda.USD)

    res_ars = obtener_resumen_analisis(db, user, moneda_modo="consolidado_ars", hoy=hoy)
    assert res_ars["aviso_cotizacion"] == "1 movimiento en dólares no se pudo pasar a pesos porque falta la cotización de ese día."

    # 2. Dos gastos en ARS en fechas distintas sin cotización
    user2 = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b2_ars = _crear_billetera(db, user2.id, Moneda.ARS)
    _crear_transaccion(db, user2.id, b2_ars.id, TipoTransaccion.EGRESO, Decimal("5000.00"), date(2026, 11, 3), Moneda.ARS)
    _crear_transaccion(db, user2.id, b2_ars.id, TipoTransaccion.EGRESO, Decimal("8000.00"), date(2026, 11, 7), Moneda.ARS)

    res_usd = obtener_resumen_analisis(db, user2, moneda_modo="consolidado_usd", hoy=hoy)
    assert res_usd["aviso_cotizacion"] == "2 movimientos en pesos no se pudieron pasar a dólares porque falta la cotización de esos días."

    # 3. Con cotizaciones cargadas -> null
    for f in (date(2026, 11, 5), date(2026, 11, 3), date(2026, 11, 7)):
        _crear_cotizacion(db, f, venta=Decimal("1500"))

    res_ars_ok = obtener_resumen_analisis(db, user, moneda_modo="consolidado_ars", hoy=hoy)
    assert res_ars_ok["aviso_cotizacion"] is None

    res_usd_ok = obtener_resumen_analisis(db, user2, moneda_modo="consolidado_usd", hoy=hoy)
    assert res_usd_ok["aviso_cotizacion"] is None


def test_tn20_texto_del_porcentaje(db: Session):
    """
    TN20: ciclo día fijo 1, hoy 13/11; ingresos 100.000 y gastos 50.000 en octubre y en septiembre:
    - 3 ciclos: tasa_ahorro_texto "Sin contar el ciclo en curso."
    - 1 ciclo: "Aparece cuando termine el ciclo."
    - con filtro de categoría: null.
    """
    hoy = date(2026, 11, 13)
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b = _crear_billetera(db, user.id, Moneda.ARS)

    cat = Categoria(id=uuid4(), nombre="Supermercado", tipo=TipoCategoria.EGRESO, orden=1)
    db.add(cat)
    db.commit()

    # Septiembre 2026
    _crear_transaccion(db, user.id, b.id, TipoTransaccion.INGRESO, Decimal("100000"), date(2026, 9, 5))
    _crear_transaccion(db, user.id, b.id, TipoTransaccion.EGRESO, Decimal("50000"), date(2026, 9, 10), categoria_id=cat.id)

    # Octubre 2026
    _crear_transaccion(db, user.id, b.id, TipoTransaccion.INGRESO, Decimal("100000"), date(2026, 10, 5))
    _crear_transaccion(db, user.id, b.id, TipoTransaccion.EGRESO, Decimal("50000"), date(2026, 10, 10), categoria_id=cat.id)

    # 3 ciclos
    res_3 = obtener_resumen_analisis(db, user, cantidad_ciclos=3, hoy=hoy)
    assert res_3["kpis"]["tasa_ahorro_texto"] == "Sin contar el ciclo en curso."

    # 1 ciclo (solo en curso)
    res_1 = obtener_resumen_analisis(db, user, cantidad_ciclos=1, hoy=hoy)
    assert res_1["kpis"]["tasa_ahorro_texto"] == "Aparece cuando termine el ciclo."

    # Con filtro de categoría
    res_cat = obtener_resumen_analisis(db, user, cantidad_ciclos=3, categoria_id=cat.id, hoy=hoy)
    assert res_cat["kpis"]["tasa_ahorro_texto"] is None


def test_tn21_tamanio_y_orden():
    """
    TN21 (estático, con ast):
    - no existe app/services/analisis_service.py
    - cada app/services/analisis_*.py tiene <= 600 líneas
    - ninguna función de esos archivos pasa de 150 líneas
    - ningún import adentro de una función salvo de openpyxl
    - los imports entre archivos analisis_* no forman ningún ciclo
    - ningún archivo de app/, tests/ ni scripts/ contiene "analisis_service import" ni "import analisis_service"
    """
    backend_root = Path(__file__).resolve().parent.parent

    # 1. No existe analisis_service.py
    assert not (backend_root / "app" / "services" / "analisis_service.py").exists()

    analisis_files = sorted(glob.glob(str(backend_root / "app" / "services" / "analisis_*.py")))
    assert len(analisis_files) >= 7

    module_deps: dict[str, set[str]] = {}

    for fpath in analisis_files:
        mod_name = Path(fpath).stem
        module_deps[mod_name] = set()

        with open(fpath, "r", encoding="utf-8") as f:
            code = f.read()

        lines = code.splitlines()
        assert len(lines) <= 600, f"{mod_name} tiene {len(lines)} líneas (máximo 600)"

        tree = ast.parse(code, filename=fpath)

        for node in ast.walk(tree):
            # Imports directos a nivel módulo para graficar dependencias
            if isinstance(node, ast.ImportFrom) and node.module:
                for sub in ("analisis_ciclos_service", "analisis_resumen_service", "analisis_insights_service",
                            "analisis_comparativa_service", "analisis_proyeccion_service",
                            "analisis_informes_service", "analisis_excel_service"):
                    if sub in node.module:
                        module_deps[mod_name].add(sub)

            # Verificar funciones
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_len = node.end_lineno - node.lineno + 1
                assert fn_len <= 150, f"Función {node.name} en {mod_name} tiene {fn_len} líneas (máximo 150)"

                # Ningún import adentro de una función salvo openpyxl
                for inner in ast.walk(node):
                    if inner is node:
                        continue
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        if isinstance(inner, ast.Import):
                            for alias in inner.names:
                                assert "openpyxl" in alias.name, (
                                    f"Import prohibido '{alias.name}' dentro de función {node.name} en {mod_name}"
                                )
                        elif isinstance(inner, ast.ImportFrom):
                            assert inner.module and "openpyxl" in inner.module, (
                                f"Import prohibido 'from {inner.module}' dentro de función {node.name} en {mod_name}"
                            )

    # 4. Verificar que no haya ciclos entre módulos analisis_*
    def has_cycle(u, visited, rec_stack):
        visited.add(u)
        rec_stack.add(u)
        for v in module_deps.get(u, set()):
            if v not in visited:
                if has_cycle(v, visited, rec_stack):
                    return True
            elif v in rec_stack:
                return True
        rec_stack.remove(u)
        return False

    visited = set()
    rec_stack = set()
    for mod in module_deps:
        if mod not in visited:
            assert not has_cycle(mod, visited, rec_stack), f"Ciclo de dependencias detectado involucrando a {mod}"

    # 5. Ningún archivo en app/, tests/, scripts/ contiene analisis_service import ni import analisis_service
    pat1 = "analisis_service" + " import"
    pat2 = "import " + "analisis_service"
    current_test_name = Path(__file__).name

    for scan_dir in ("app", "tests", "scripts"):
        for root, _, files in os.walk(backend_root / scan_dir):
            for file in files:
                if not file.endswith(".py") or file == current_test_name:
                    continue
                p = Path(root) / file
                with open(p, "r", encoding="utf-8", errors="replace") as sf:
                    txt = sf.read()
                assert pat1 not in txt, f"Encontrado '{pat1}' en {p}"
                assert pat2 not in txt, f"Encontrado '{pat2}' en {p}"


def test_tn22_router(db: Session):
    """
    TN22 router:
    - app/routers/analisis.py no contiene "analisis_service."
    - TestClient con usuario de prueba:
      GET /resumen, /drilldown-movimientos, /comparativa-ciclos, /proyeccion-compromisos, /informes, /exportar-excel -> 200
      POST /informes -> 200 y aparece en GET /informes
      GET, PUT, DELETE /informes/{id} inexistente -> 404
      con id creado -> 200.
    """
    backend_root = Path(__file__).resolve().parent.parent
    router_file = backend_root / "app" / "routers" / "analisis.py"
    with open(router_file, "r", encoding="utf-8") as f:
        router_code = f.read()

    assert "analisis_service." not in router_code

    from app.main import app

    test_user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")

    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_db] = lambda: db

    try:
        client = TestClient(app)
        prefix = "/analisis"

        r = client.get(f"{prefix}/resumen")
        assert r.status_code == 200, f"GET {prefix}/resumen -> {r.status_code}"

        r = client.get(f"{prefix}/drilldown-movimientos")
        assert r.status_code == 200, f"GET {prefix}/drilldown-movimientos -> {r.status_code}"

        r = client.get(f"{prefix}/comparativa-ciclos")
        assert r.status_code == 200, f"GET {prefix}/comparativa-ciclos -> {r.status_code}"

        r = client.get(f"{prefix}/proyeccion-compromisos")
        assert r.status_code == 200, f"GET {prefix}/proyeccion-compromisos -> {r.status_code}"

        r = client.get(f"{prefix}/informes")
        assert r.status_code == 200, f"GET {prefix}/informes -> {r.status_code}"

        r = client.get(f"{prefix}/exportar-excel")
        assert r.status_code == 200, f"GET {prefix}/exportar-excel -> {r.status_code}"

        # POST /informes -> 200
        payload = {
            "nombre": "Informe Auditoría",
            "descripcion": "Descripción del informe",
            "icono": "pie-chart",
            "es_favorito": False,
            "configuracion": {"moneda_modo": "solo_ars", "ciclos": 6},
        }
        r_post = client.post(f"{prefix}/informes", json=payload)
        assert r_post.status_code == 200, f"POST {prefix}/informes -> {r_post.status_code}"
        creado = r_post.json()
        inf_id = creado["id"]

        # Aparece en GET /informes
        r_list = client.get(f"{prefix}/informes")
        assert r_list.status_code == 200
        ids_en_lista = [i["id"] for i in r_list.json()["items"]]
        assert inf_id in ids_en_lista

        # Inexistente -> 404
        fake_id = str(uuid4())
        assert client.get(f"{prefix}/informes/{fake_id}").status_code == 404
        assert client.put(f"{prefix}/informes/{fake_id}", json={"nombre": "Nuevo"}).status_code == 404
        assert client.delete(f"{prefix}/informes/{fake_id}").status_code == 404

        # Con el id creado -> 200
        assert client.get(f"{prefix}/informes/{inf_id}").status_code == 200
        r_put = client.put(f"{prefix}/informes/{inf_id}", json={"nombre": "Nombre Actualizado"})
        assert r_put.status_code == 200
        assert client.delete(f"{prefix}/informes/{inf_id}").status_code == 200
    finally:
        app.dependency_overrides.clear()
