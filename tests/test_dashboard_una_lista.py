"""
Tests de una sola lista de pagos en el dashboard y eliminación de calcular_saldo_disponible_ciclo_actual (TE.10).
"""
from __future__ import annotations

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import uuid
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.services import contexto_financiero_service, dashboard_service, pagos_proximos_service


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_jsonb = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_jsonb:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    had_pguuid = (
        hasattr(PGUUID, "_compiler_dispatcher")
        and "sqlite" in getattr(PGUUID._compiler_dispatcher, "specs", {})
    )
    if not had_pguuid:
        compiles(PGUUID, "sqlite")(lambda type_, compiler, **kw: "CHAR(32)")

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

    if not had_jsonb and hasattr(JSONB, "_compiler_dispatcher"):
        JSONB._compiler_dispatcher.specs.pop("sqlite", None)
    if not had_pguuid and hasattr(PGUUID, "_compiler_dispatcher"):
        PGUUID._compiler_dispatcher.specs.pop("sqlite", None)


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


def test_te10_dashboard_una_sola_lista_y_respuesta_identica(db: Session, monkeypatch):
    hoy_fijo = date(2026, 10, 15)
    monkeypatch.setattr(dashboard_service, "hoy_argentina", lambda: hoy_fijo)
    monkeypatch.setattr(pagos_proximos_service, "hoy_argentina", lambda: hoy_fijo)

    usuario = Usuario(
        id=uuid4(),
        email=f"te10_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)

    b_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Banco Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        es_principal=True,
        es_efectivo=False,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Caja Dólares",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("200.00"),
        saldo_actual=Decimal("200.00"),
        es_principal=False,
        es_efectivo=False,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_ars, b_usd])

    cat = Categoria(
        id=uuid4(),
        nombre="Servicios",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(cat)
    db.flush()

    # Una cuota suelta pendiente en ARS por $25.000 con vencimiento el 20/10/2026
    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_ars.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 20),
        descripcion="Mueble",
        metodo_pago=MetodoPago.DEBITO,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_padre)
    db.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        transaccion_padre_id=tx_padre.id,
        descripcion="Mueble",
        monto_total=Decimal("50000.00"),
        total_financiado=Decimal("50000.00"),
        cantidad_cuotas=2,
        tiene_interes=False,
        moneda=Moneda.ARS,
    )
    db.add(grupo)
    db.flush()

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_ars.id,
        categoria_id=cat.id,
        grupo_cuotas_id=grupo.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("25000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 20),
        descripcion="Mueble (Cuota 1/2)",
        metodo_pago=MetodoPago.DEBITO,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
    )
    db.add(tx_hija)
    db.flush()

    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=1,
        monto_proyectado=Decimal("25000.00"),
        monto_real=None,
        fecha_vencimiento=date(2026, 10, 20),
        pagada=False,
    )
    db.add(cuota)

    # Una suscripción activa en ARS por $12.000 con próximo cobro el 22/10/2026
    sub = Suscripcion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_ars.id,
        categoria_id=cat.id,
        nombre="Spotify",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 10, 22),
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(sub)
    db.flush()

    hist_sub = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=sub.id,
        monto=Decimal("12000.00"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 10, 1),
    )
    db.add(hist_sub)
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario

    try:
        with (
            patch.object(
                pagos_proximos_service,
                "listar_pagos_proximos",
                wraps=pagos_proximos_service.listar_pagos_proximos,
            ) as mock_listar,
            patch.object(
                contexto_financiero_service,
                "_calcular_saldo_disponible_sync",
                wraps=contexto_financiero_service._calcular_saldo_disponible_sync,
            ) as mock_ctx_sync,
        ):
            client = TestClient(app)
            resp = client.get("/dashboard/resumen")
            assert resp.status_code == 200
            assert mock_listar.call_count == 1
            assert mock_ctx_sync.call_count == 0

            data = resp.json()
    finally:
        app.dependency_overrides.clear()

    esperado = {
        "periodo": {
            "fecha_inicio": "2026-10-01",
            "fecha_fin": "2026-10-31",
            "primera_transaccion": "2026-09-20",
        },
        "balance": {
            "ars": {
                "ingresos": 0.0,
                "egresos": 0.0,
                "balance": 0.0,
                "variacion_vs_ciclo_anterior": None,
            },
            "usd": {
                "ingresos": 0.0,
                "egresos": 0.0,
                "balance": 0.0,
                "variacion_vs_ciclo_anterior": None,
            },
        },
        "disponible_real": {
            "ars": {
                "saldo_billeteras": 500000.0,
                "cuotas_proximo_ciclo": 25000.0,
                "suscripciones_mensuales": 12000.0,
                "disponible": 463000.0,
            },
            "usd": {
                "saldo_billeteras": 200.0,
                "cuotas_proximo_ciclo": 0.0,
                "suscripciones_mensuales": 0.0,
                "disponible": 200.0,
            },
        },
        "saldo_disponible": {
            "ars": {
                "saldo_total": "500000.00",
                "cuotas_pendientes": "25000.00",
                "suscripciones_pendientes": "12000.00",
                "facturas_pendientes": "0.00",
                "saldo_disponible": "463000.00",
                "compromisos": [
                    {
                        "id": cuota.id.hex,
                        "tipo": "cuota",
                        "nombre": "Mueble",
                        "monto": "25000.0",
                        "fecha_cobro": "2026-10-20",
                        "monto_usd": None,
                        "cotizacion": None,
                        "percepcion": None,
                    },
                    {
                        "id": sub.id.hex,
                        "tipo": "suscripcion",
                        "nombre": "Spotify",
                        "monto": "12000.0",
                        "fecha_cobro": "2026-10-22",
                        "monto_usd": None,
                        "cotizacion": None,
                        "percepcion": None,
                    },
                ],
            },
            "usd": {
                "saldo_total": "200.00",
                "cuotas_pendientes": "0.00",
                "suscripciones_pendientes": "0.00",
                "facturas_pendientes": "0.00",
                "saldo_disponible": "200.00",
                "compromisos": [],
            },
        },
        "gastos_por_categoria": {"ars": [], "usd": []},
        "ultimos_movimientos": [],
        "proximos_pagos": [
            {
                "id": cuota.id.hex,
                "nombre": "Mueble",
                "monto": 25000.0,
                "moneda": "ARS",
                "fecha_cobro": "2026-10-20",
                "dias_restantes": 5,
                "tipo": "cuota",
                "color": None,
                "red": None,
                "billetera_nombre": None,
                "billetera_id": None,
                "es_vencido": False,
                "factura_id": None,
                "estado_factura": None,
            },
            {
                "id": sub.id.hex,
                "nombre": "Spotify",
                "monto": 12000.0,
                "moneda": "ARS",
                "fecha_cobro": "2026-10-22",
                "dias_restantes": 7,
                "tipo": "suscripcion",
                "color": None,
                "red": None,
                "billetera_nombre": None,
                "billetera_id": None,
                "es_vencido": False,
                "factura_id": None,
                "estado_factura": None,
            },
        ],
    }

    assert data == esperado


def test_te10_ast_calcular_saldo_disponible_ciclo_actual_no_existe():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    apariciones = []

    for py_file in sorted(app_dir.rglob("*.py")):
        rel = py_file.relative_to(app_dir.parent).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
        for nodo in ast.walk(tree):
            if (
                isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
                and nodo.name == "calcular_saldo_disponible_ciclo_actual"
            ):
                apariciones.append(f"{rel}:{nodo.lineno}")
            elif (
                isinstance(nodo, ast.Name)
                and nodo.id == "calcular_saldo_disponible_ciclo_actual"
            ):
                apariciones.append(f"{rel}:{nodo.lineno}")
            elif (
                isinstance(nodo, ast.Attribute)
                and nodo.attr == "calcular_saldo_disponible_ciclo_actual"
            ):
                apariciones.append(f"{rel}:{nodo.lineno}")

    assert apariciones == []
