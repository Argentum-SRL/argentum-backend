"""
Tests de monto_efectivo_cuota y monto_efectivo_cuota_sql (TE.7 y E.2).
"""
from __future__ import annotations

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.utils.cuotas import monto_efectivo_cuota, monto_efectivo_cuota_sql


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
    orig_nullable = Cuota.__table__.c.monto_proyectado.nullable
    Cuota.__table__.c.monto_proyectado.nullable = True
    try:
        Base.metadata.create_all(bind=engine)
        TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()
    finally:
        Cuota.__table__.c.monto_proyectado.nullable = orig_nullable


def test_te7_monto_efectivo_cuota_python_y_sql(db: Session):
    u = Usuario(
        id=uuid4(),
        email=f"te7_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Principal",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        es_principal=True,
        es_inversion=False,
        estado="activa",
    )
    db.add(b)

    cat = Categoria(
        id=uuid4(),
        nombre="Compras",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(cat)
    db.flush()

    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 1),
        descripcion="Compra cuotas",
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
        usuario_id=u.id,
        transaccion_padre_id=tx_padre.id,
        descripcion="Compra cuotas",
        monto_total=Decimal("50000.00"),
        total_financiado=Decimal("50000.00"),
        cantidad_cuotas=5,
        tiene_interes=False,
        moneda=Moneda.ARS,
    )
    db.add(grupo)
    db.flush()

    casos = [
        # 1. proyectado 10.000 y real None -> 10.000,00
        (1, Decimal("10000.00"), None, Decimal("10000.00")),
        # 2. real 10.350 -> 10.350,00
        (2, Decimal("10000.00"), Decimal("10350.00"), Decimal("10350.00")),
        # 3. real 0,00 y proyectado 10.000 -> 0,00
        (3, Decimal("10000.00"), Decimal("0.00"), Decimal("0.00")),
        # 4. cuota de reintegro con proyectado -2.500 y real None -> -2.500,00
        (4, Decimal("-2500.00"), None, Decimal("-2500.00")),
        # 5. los dos None -> 0,00
        (5, None, None, Decimal("0.00")),
    ]

    cuotas_creadas = []
    for num, proy, real, _ in casos:
        tx_hija = Transaccion(
            id=uuid4(),
            usuario_id=u.id,
            billetera_id=b.id,
            categoria_id=cat.id,
            grupo_cuotas_id=grupo.id,
            tipo=TipoTransaccion.EGRESO,
            monto=proy if proy is not None else Decimal("0.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 15),
            descripcion=f"Cuota {num}",
            metodo_pago=MetodoPago.DEBITO,
            es_padre_cuotas=False,
            es_cuota_hija=True,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        )
        db.add(tx_hija)
        db.flush()

        c = Cuota(
            id=uuid4(),
            grupo_id=grupo.id,
            transaccion_id=tx_hija.id,
            numero_cuota=num,
            monto_proyectado=proy,
            monto_real=real,
            fecha_vencimiento=date(2026, 10, 15),
            pagada=False,
        )
        db.add(c)
        cuotas_creadas.append(c)

    db.commit()

    # Verificar en Python
    for c, (_, _, _, esperado) in zip(cuotas_creadas, casos):
        obtenido = monto_efectivo_cuota(c)
        assert obtenido == esperado
        assert Decimal(str(obtenido)).quantize(Decimal("0.01")) == esperado

    # Verificar expresión SQL sobre las cinco filas
    filas_sql = db.execute(
        select(Cuota.numero_cuota, monto_efectivo_cuota_sql().label("monto"))
        .where(Cuota.grupo_id == grupo.id)
        .order_by(Cuota.numero_cuota)
    ).all()

    assert len(filas_sql) == 5
    for row, (_, _, _, esperado) in zip(filas_sql, casos):
        val_sql = Decimal(str(row.monto)).quantize(Decimal("0.01"))
        assert val_sql == esperado


def test_e2_ast_sin_lecturas_directas_monto_cuota():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    attrs = {"monto_real", "monto_proyectado"}
    violaciones = []

    for py_file in sorted(app_dir.rglob("*.py")):
        rel = py_file.relative_to(app_dir.parent).as_posix()
        if (
            rel == "app/utils/cuotas.py"
            or rel.startswith("app/services/importacion/")
            or rel.startswith("app/models/")
            or rel.startswith("app/schemas/")
        ):
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
        aug_targets = {id(n.target) for n in ast.walk(tree) if isinstance(n, ast.AugAssign)}

        for nodo in ast.walk(tree):
            if isinstance(nodo, ast.Attribute) and nodo.attr in attrs:
                if id(nodo) in aug_targets or isinstance(getattr(nodo, "ctx", None), ast.Store):
                    continue
                if isinstance(getattr(nodo, "ctx", None), ast.Load):
                    violaciones.append(f"{rel}:{nodo.lineno} (.{nodo.attr})")
            elif (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Name)
                and nodo.func.id == "getattr"
                and len(nodo.args) >= 2
                and isinstance(nodo.args[1], ast.Constant)
                and nodo.args[1].value in attrs
            ):
                violaciones.append(f"{rel}:{nodo.lineno} (getattr {nodo.args[1].value})")

    assert violaciones == [], f"Lecturas fuera de lugares permitidos: {violaciones}"
