# -*- coding: utf-8 -*-
"""
tests/test_presupuestos_excel_ruta.py
Pruebas para el endpoint GET /presupuestos/exportar-excel.
Usa solo fechas relativas a hoy_argentina().
"""
from __future__ import annotations

import calendar
import io
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types
from starlette.middleware.cors import CORSMiddleware

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.main import app
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.evento_uso import EventoUso
from app.models.transaccion import MetodoPago, TipoTransaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services import transaccion_service
from app.services.presupuestos_excel_datos import nombre_hoja_ciclo
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


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
    monkeypatch.setattr(limiter, "enabled", False)

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


def test_ruta_exportar_excel(db: Session):
    hoy = hoy_argentina()
    ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    primer_dia_mes = date(hoy.year, hoy.month, 1)
    ultimo_dia_mes_date = date(hoy.year, hoy.month, ultimo_dia_mes)

    # Usuario con gasto relativo a hoy_argentina()
    # ciclo_tipo y ciclo_valor None -> mes calendario
    usuario = Usuario(
        id=uuid4(),
        email="test_ruta_excel@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=None,
        ciclo_valor=None,
        ciclo_ajuste_direccion=None,
        fecha_registro=datetime(hoy.year, hoy.month, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(usuario)
    db.commit()

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        fecha_saldo=None,
        es_inversion=False,
    )
    db.add(billetera)
    db.commit()

    cat_gasto = Categoria(id=uuid4(), nombre="Supermercado", tipo=TipoCategoria.EGRESO)
    db.add(cat_gasto)
    db.commit()

    # Gasto de 1.000 hoy en categoría de gasto
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("1000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Gasto de prueba hoy",
            categoria_id=cat_gasto.id,
            billetera_id=billetera.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    # Configurar cliente de prueba con dependency overrides
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    origin_permitido = settings.FRONTEND_URL.rstrip("/")
    if not origin_permitido:
        origin_permitido = "http://localhost:5173"

    with TestClient(app) as client:
        # GET sin parámetros (default 12)
        resp = client.get("/presupuestos/exportar-excel", headers={"Origin": origin_permitido})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        nombre_esperado = f'attachment; filename="Argentum_presupuestos_{primer_dia_mes.isoformat()}_{ultimo_dia_mes_date.isoformat()}.xlsx"'
        assert resp.headers["content-disposition"] == nombre_esperado

        # Verifica cabecera CORS expuesta si vino de origen permitido
        if "access-control-expose-headers" in resp.headers:
            assert "Content-Disposition" in resp.headers["access-control-expose-headers"]
        else:
            # Comprobar kwargs del middleware en app.user_middleware
            cors_mws = [m for m in app.user_middleware if m.cls == CORSMiddleware or m.cls.__name__ == "CORSMiddleware"]
            assert len(cors_mws) > 0
            assert "Content-Disposition" in cors_mws[0].kwargs.get("expose_headers", [])

        # Abre con openpyxl y verifica hojas
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        nombre_hoja_hoy = nombre_hoja_ciclo(primer_dia_mes, ultimo_dia_mes_date)
        assert [s.title for s in wb.worksheets] == ["Comparativo", "Anual", nombre_hoja_hoy, "Cómo leerlo"]

        # Queda exactamente 1 fila en eventos_uso con evento descargar_presupuestos
        eventos = db.execute(
            select(EventoUso).where(
                EventoUso.usuario_id == usuario.id,
                EventoUso.evento == "descargar_presupuestos",
            )
        ).scalars().all()
        assert len(eventos) == 1

        # ?alcance=todo -> 200
        resp_todo = client.get("/presupuestos/exportar-excel?alcance=todo", headers={"Origin": origin_permitido})
        assert resp_todo.status_code == 200

        # ?alcance=13 -> 422 con detail exacto
        resp_invalido = client.get("/presupuestos/exportar-excel?alcance=13")
        assert resp_invalido.status_code == 422
        assert resp_invalido.json()["detail"] == "El alcance tiene que ser 12 o todo."

    # Otro usuario nuevo sin transacciones -> 404 con detail exacto y 0 filas en eventos_uso
    usuario_vacio = Usuario(
        id=uuid4(),
        email="test_vacio_excel@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=None,
        ciclo_valor=None,
        ciclo_ajuste_direccion=None,
        fecha_registro=datetime(hoy.year, hoy.month, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(usuario_vacio)
    db.commit()

    def override_vacio():
        return usuario_vacio

    app.dependency_overrides[get_current_user] = override_vacio

    with TestClient(app) as client_vacio:
        resp_404 = client_vacio.get("/presupuestos/exportar-excel")
        assert resp_404.status_code == 404
        assert resp_404.json()["detail"] == "Todavía no tenés gastos ni ingresos para armar el Excel."

        eventos_vacio = db.execute(
            select(EventoUso).where(
                EventoUso.usuario_id == usuario_vacio.id,
                EventoUso.evento == "descargar_presupuestos",
            )
        ).scalars().all()
        assert len(eventos_vacio) == 0

    app.dependency_overrides.clear()
