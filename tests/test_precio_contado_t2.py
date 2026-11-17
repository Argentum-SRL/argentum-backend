# -*- coding: utf-8 -*-
"""tests/test_precio_contado_t2.py — Pruebas de precio_contado en compras en cuotas (T2)."""
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
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.routers.whatsapp.registro import _registrar_movimiento_directo
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import cuotas_service, transaccion_service
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
        "app.services.transaccion_service",
        "app.services.cuotas_service",
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


@pytest.fixture(name="datos_usuario")
def datos_usuario_fixture(db: Session):
    u = Usuario(
        id=uuid4(),
        email="contado_t2@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=datetime(2026, 3, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
    )
    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Visa Galicia",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=5,
        estado=EstadoTarjeta.ACTIVA,
        moneda=Moneda.ARS,
    )
    cat = Categoria(
        id=uuid4(),
        nombre="Tecnología",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([u, b, t, cat])
    db.commit()
    return u, b, t, cat


@pytest.fixture(name="client")
def client_fixture(db: Session, datos_usuario):
    u, _, _, _ = datos_usuario
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: u
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_crear_y_editar_precio_contado_en_grupo(client: TestClient, db: Session, datos_usuario):
    u, b, t, cat = datos_usuario
    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("960000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 10),
        descripcion="Heladera",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.CREDITO,
        billetera_id=b.id,
        tarjeta_id=t.id,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        primer_vencimiento_manual=date(2026, 12, 5),
        info_cuotas=InfoCuotas(
            cantidad_cuotas=12,
            cuota_inicial=1,
            tiene_interes=False,
            tasa_interes=None,
            monto_total=Decimal("960000.00"),
            precio_contado=Decimal("800000.00"),
            proximo_resumen=False,
        ),
    )
    tx = transaccion_service.crear_transaccion(db, u.id, tx_data, commit=True)
    grupo = db.get(GrupoCuotas, tx.grupo_cuotas_id)
    assert grupo is not None
    assert grupo.precio_contado == Decimal("800000.00")

    res = client.get("/grupos-cuotas")
    assert res.status_code == 200, res.text
    g_api = next(g for g in res.json() if g["id"] == str(grupo.id))
    assert g_api["precio_contado"] == 800000.0
    assert g_api["detalle_precio_contado"] == "Pagás $160.000 más que de contado (20%)."

    # Editar precio_contado igual y luego limpiarlo a None
    r_patch = client.patch(
        f"/grupos-cuotas/{grupo.id}",
        json={"precio_contado": 960000},
    )
    assert r_patch.status_code == 200, r_patch.text
    assert r_patch.json()["detalle_precio_contado"] == "Pagás lo mismo que de contado."

    r_clear = client.patch(
        f"/grupos-cuotas/{grupo.id}",
        json={"precio_contado": None},
    )
    assert r_clear.status_code == 200, r_clear.text
    assert r_clear.json()["precio_contado"] is None
    assert r_clear.json()["detalle_precio_contado"] is None


def test_texto_detalle_precio_contado():
    assert (
        cuotas_service.texto_detalle_precio_contado(Decimal("960000"), Decimal("800000"))
        == "Pagás $160.000 más que de contado (20%)."
    )
    assert cuotas_service.texto_detalle_precio_contado(Decimal("960000"), None) is None
    assert (
        cuotas_service.texto_detalle_precio_contado(Decimal("960000"), Decimal("960000"))
        == "Pagás lo mismo que de contado."
    )
    assert (
        cuotas_service.texto_detalle_precio_contado(Decimal("300000"), Decimal("320000"))
        == "Pagás $20.000 menos que de contado."
    )


def test_whatsapp_guarda_precio_contado(db: Session, datos_usuario):
    u, _, t, cat = datos_usuario
    entidades = {
        "tipo": "egreso",
        "monto": Decimal("960000"),
        "monto_total": Decimal("960000"),
        "monto_cuota": Decimal("80000"),
        "cantidad_cuotas": 12,
        "tarjeta_id": str(t.id),
        "categoria": cat.nombre,
        "fecha": "2026-11-17",
        "descripcion": "Heladera",
    }
    tx, msg = _registrar_movimiento_directo(
        u,
        entidades,
        db,
        mensaje_original="compré una heladera en 12 cuotas de 80 mil, de contado 800 mil",
    )
    assert tx is not None, msg
    assert tx.grupo_cuotas_id is not None
    grupo = db.get(GrupoCuotas, tx.grupo_cuotas_id)
    assert grupo is not None
    assert grupo.precio_contado == Decimal("800000.00")
