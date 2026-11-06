# -*- coding: utf-8 -*-
"""
tests/test_hormiga_unica.py — Pruebas de la unificación de la regla de gastos hormiga (C1 y resumen de ciclo).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
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
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.consejos_reglas import (
    FilaGasto,
    evaluar_hormiga,
    tickets_90,
)
from app.services.notificacion_scheduler_service import _gastos_hormiga


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
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _crear_catalogos(db: Session):
    cat_alim = Categoria(id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    cat_gastro = Categoria(id=uuid4(), nombre="Gastronomía", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    cat_trans = Categoria(id=uuid4(), nombre="Transporte", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    db.add_all([cat_alim, cat_gastro, cat_trans])
    db.flush()

    sub_super = Subcategoria(id=uuid4(), categoria_id=cat_alim.id, nombre="Supermercado", estado=EstadoSubcategoria.ACTIVA, orden=1)
    sub_kiosco = Subcategoria(id=uuid4(), categoria_id=cat_alim.id, nombre="Kiosco", estado=EstadoSubcategoria.ACTIVA, orden=2)
    sub_cafe = Subcategoria(id=uuid4(), categoria_id=cat_gastro.id, nombre="Cafetería", estado=EstadoSubcategoria.ACTIVA, orden=1)
    sub_trans_pub = Subcategoria(id=uuid4(), categoria_id=cat_trans.id, nombre="Transporte público", estado=EstadoSubcategoria.ACTIVA, orden=1)
    db.add_all([sub_super, sub_kiosco, sub_cafe, sub_trans_pub])
    db.flush()

    return {
        "cat_alim": cat_alim,
        "cat_gastro": cat_gastro,
        "cat_trans": cat_trans,
        "sub_super": sub_super,
        "sub_kiosco": sub_kiosco,
        "sub_cafe": sub_cafe,
        "sub_trans_pub": sub_trans_pub,
    }


def _crear_usuario(db: Session, email: str, moneda: Moneda = Moneda.ARS) -> tuple[Usuario, Billetera]:
    u = Usuario(
        id=uuid4(),
        email=email,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=moneda,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(u)
    db.flush()

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Principal",
        moneda=moneda,
        saldo_actual=Decimal("1000000"),
        saldo_inicial=Decimal("1000000"),
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
    )
    db.add(b)
    db.flush()
    return u, b


def _agregar_egreso(
    db: Session,
    u_id: uuid.UUID,
    b_id: uuid.UUID,
    cat_id: uuid.UUID,
    subcat_id: uuid.UUID,
    monto: Decimal,
    moneda: Moneda,
    fecha: date,
    descripcion: str = "Gasto",
    reintegro_de_id: uuid.UUID | None = None,
) -> Transaccion:
    tx = Transaccion(
        id=uuid4(),
        usuario_id=u_id,
        billetera_id=b_id,
        categoria_id=cat_id,
        subcategoria_id=subcat_id,
        tipo=TipoTransaccion.EGRESO,
        monto=monto,
        moneda=moneda,
        fecha=fecha,
        descripcion=descripcion,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        saldo_aplicado=True,
        es_cuota_hija=False,
        es_padre_cuotas=False,
        reintegro_de_id=reintegro_de_id,
    )
    db.add(tx)
    return tx


def test_escenario_h_ars_y_paridad_c1(db: Session):
    cats = _crear_catalogos(db)
    u_p, b_p = _crear_usuario(db, "usuario_p@argentum.com", Moneda.ARS)

    # Escenario H (ARS):
    # Alimentación > Supermercado 16 × 10.000
    for i in range(16):
        _agregar_egreso(db, u_p.id, b_p.id, cats["cat_alim"].id, cats["sub_super"].id, Decimal("10000.00"), Moneda.ARS, date(2026, 10, 2 + (i % 25)), f"Super {i}")

    # Gastronomía > Cafetería 6 × 2.000 y un espejo de reintegro de −500 de una de ellas
    cafe_txs = []
    for i in range(6):
        tx = _agregar_egreso(db, u_p.id, b_p.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("2000.00"), Moneda.ARS, date(2026, 10, 5 + i), f"Café {i}")
        cafe_txs.append(tx)
    db.flush()
    _agregar_egreso(db, u_p.id, b_p.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("-500.00"), Moneda.ARS, date(2026, 10, 6), "Reintegro café", reintegro_de_id=cafe_txs[0].id)

    # Alimentación > Kiosco 4 × 1.500
    for i in range(4):
        _agregar_egreso(db, u_p.id, b_p.id, cats["cat_alim"].id, cats["sub_kiosco"].id, Decimal("1500.00"), Moneda.ARS, date(2026, 10, 10 + i), f"Kiosco {i}")

    # Transporte > Transporte público 6 × 1.000
    for i in range(6):
        _agregar_egreso(db, u_p.id, b_p.id, cats["cat_trans"].id, cats["sub_trans_pub"].id, Decimal("1000.00"), Moneda.ARS, date(2026, 10, 15 + i), f"Bondi {i}")

    db.commit()

    # 1. _gastos_hormiga con Moneda.ARS
    res_ars = _gastos_hormiga(db, u_p.id, date(2026, 10, 1), date(2026, 10, 31), Moneda.ARS, date(2026, 10, 31))
    esperado = [
        {"categoria": "Cafetería", "cantidad": 6, "total": 11500.0},
        {"categoria": "Kiosco", "cantidad": 4, "total": 6000.0},
    ]
    assert res_ars == esperado

    # T-2: gasto_filas.hormiga_del_ciclo da dos GrupoHormiga exactos en orden
    from app.services import gasto_filas
    from app.services.consejos_reglas import GrupoHormiga
    gh = gasto_filas.hormiga_del_ciclo(db, u_p.id, date(2026, 10, 1), date(2026, 10, 31), "ARS", date(2026, 10, 31))
    assert gh == [
        GrupoHormiga(categoria="Gastronomía", subcategoria="Cafetería", cantidad=6, total=Decimal("11500.00")),
        GrupoHormiga(categoria="Alimentación", subcategoria="Kiosco", cantidad=4, total=Decimal("6000.00")),
    ]

    # Con "ars" en minúscula
    res_ars_min = _gastos_hormiga(db, u_p.id, date(2026, 10, 1), date(2026, 10, 31), "ars", date(2026, 10, 31))
    assert res_ars_min == esperado

    # Con Moneda.USD → []
    res_usd = _gastos_hormiga(db, u_p.id, date(2026, 10, 1), date(2026, 10, 31), Moneda.USD, date(2026, 10, 31))
    assert res_usd == []

    # 2. Paridad con C1: evaluar_hormiga con las mismas filas (FilaGasto)
    filas_c1: list[FilaGasto] = []
    for _ in range(16):
        filas_c1.append(FilaGasto(fecha=date(2026, 10, 5), monto=Decimal("10000"), moneda="ARS", categoria="Alimentación", subcategoria="Supermercado"))
    for _ in range(6):
        filas_c1.append(FilaGasto(fecha=date(2026, 10, 6), monto=Decimal("2000"), moneda="ARS", categoria="Gastronomía", subcategoria="Cafetería"))
    filas_c1.append(FilaGasto(fecha=date(2026, 10, 6), monto=Decimal("-500"), moneda="ARS", categoria="Gastronomía", subcategoria="Cafetería", es_espejo=True))
    for _ in range(4):
        filas_c1.append(FilaGasto(fecha=date(2026, 10, 10), monto=Decimal("1500"), moneda="ARS", categoria="Alimentación", subcategoria="Kiosco"))
    for _ in range(6):
        filas_c1.append(FilaGasto(fecha=date(2026, 10, 15), monto=Decimal("1000"), moneda="ARS", categoria="Transporte", subcategoria="Transporte público"))

    t90 = tickets_90(filas_c1, "ARS", date(2026, 10, 31))
    consejo = evaluar_hormiga(filas_c1, t90, "ARS", date(2026, 10, 1))
    assert consejo is not None
    assert consejo.valores["subcategorias"] == [["Gastronomía", "Cafetería"], ["Alimentación", "Kiosco"]]
    assert consejo.texto == "Los gastos chicos y seguidos (Cafetería y Kiosco) suman unos $18.000 por mes: $216.000 al año."


def test_borde_20_compras(db: Session):
    cats = _crear_catalogos(db)

    # Usuario con 20 compras exactas: Supermercado 10 × 10.000, Cafetería 6 × 2.000 (con espejo −500), Kiosco 4 × 1.500
    u20, b20 = _crear_usuario(db, "u20@argentum.com", Moneda.ARS)
    for i in range(10):
        _agregar_egreso(db, u20.id, b20.id, cats["cat_alim"].id, cats["sub_super"].id, Decimal("10000.00"), Moneda.ARS, date(2026, 10, 2 + i))
    cafe20 = []
    for i in range(6):
        tx = _agregar_egreso(db, u20.id, b20.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("2000.00"), Moneda.ARS, date(2026, 10, 5 + i))
        cafe20.append(tx)
    db.flush()
    _agregar_egreso(db, u20.id, b20.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("-500.00"), Moneda.ARS, date(2026, 10, 6), reintegro_de_id=cafe20[0].id)
    for i in range(4):
        _agregar_egreso(db, u20.id, b20.id, cats["cat_alim"].id, cats["sub_kiosco"].id, Decimal("1500.00"), Moneda.ARS, date(2026, 10, 15 + i))
    db.commit()

    res20 = _gastos_hormiga(db, u20.id, date(2026, 10, 1), date(2026, 10, 31), Moneda.ARS, date(2026, 10, 31))
    assert res20 == [
        {"categoria": "Cafetería", "cantidad": 6, "total": 11500.0},
        {"categoria": "Kiosco", "cantidad": 4, "total": 6000.0},
    ]

    # Usuario con 19 compras (Cafetería 5 en vez de 6) → []
    u19, b19 = _crear_usuario(db, "u19@argentum.com", Moneda.ARS)
    for i in range(10):
        _agregar_egreso(db, u19.id, b19.id, cats["cat_alim"].id, cats["sub_super"].id, Decimal("10000.00"), Moneda.ARS, date(2026, 10, 2 + i))
    cafe19 = []
    for i in range(5):
        tx = _agregar_egreso(db, u19.id, b19.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("2000.00"), Moneda.ARS, date(2026, 10, 5 + i))
        cafe19.append(tx)
    db.flush()
    _agregar_egreso(db, u19.id, b19.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("-500.00"), Moneda.ARS, date(2026, 10, 6), reintegro_de_id=cafe19[0].id)
    for i in range(4):
        _agregar_egreso(db, u19.id, b19.id, cats["cat_alim"].id, cats["sub_kiosco"].id, Decimal("1500.00"), Moneda.ARS, date(2026, 10, 15 + i))
    db.commit()

    res19 = _gastos_hormiga(db, u19.id, date(2026, 10, 1), date(2026, 10, 31), Moneda.ARS, date(2026, 10, 31))
    assert res19 == []


def test_escenario_d_usd(db: Session):
    cats = _crear_catalogos(db)
    u_d, b_d = _crear_usuario(db, "usuario_d@argentum.com", Moneda.USD)

    # Supermercado 16 × 10
    for i in range(16):
        _agregar_egreso(db, u_d.id, b_d.id, cats["cat_alim"].id, cats["sub_super"].id, Decimal("10.00"), Moneda.USD, date(2026, 10, 2 + (i % 25)))
    # Cafetería 6 × 2 con espejo −0,5
    cafe_usd = []
    for i in range(6):
        tx = _agregar_egreso(db, u_d.id, b_d.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("2.00"), Moneda.USD, date(2026, 10, 5 + i))
        cafe_usd.append(tx)
    db.flush()
    _agregar_egreso(db, u_d.id, b_d.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("-0.50"), Moneda.USD, date(2026, 10, 6), reintegro_de_id=cafe_usd[0].id)
    # Kiosco 4 × 1,5
    for i in range(4):
        _agregar_egreso(db, u_d.id, b_d.id, cats["cat_alim"].id, cats["sub_kiosco"].id, Decimal("1.50"), Moneda.USD, date(2026, 10, 10 + i))
    # Transporte público 6 × 1
    for i in range(6):
        _agregar_egreso(db, u_d.id, b_d.id, cats["cat_trans"].id, cats["sub_trans_pub"].id, Decimal("1.00"), Moneda.USD, date(2026, 10, 15 + i))

    db.commit()

    res_usd = _gastos_hormiga(db, u_d.id, date(2026, 10, 1), date(2026, 10, 31), Moneda.USD, date(2026, 10, 31))
    assert res_usd == [
        {"categoria": "Cafetería", "cantidad": 6, "total": 11.5},
        {"categoria": "Kiosco", "cantidad": 4, "total": 6.0},
    ]

    res_ars = _gastos_hormiga(db, u_d.id, date(2026, 10, 1), date(2026, 10, 31), Moneda.ARS, date(2026, 10, 31))
    assert res_ars == []


def test_escenario_m_y_aislamiento(db: Session):
    cats = _crear_catalogos(db)

    # Usuario M: Escenario H en pesos + 3 compras de US$2 en Cafetería
    u_m, b_ars = _crear_usuario(db, "usuario_m@argentum.com", Moneda.ARS)
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=u_m.id,
        nombre="Billetera USD",
        moneda=Moneda.USD,
        saldo_actual=Decimal("5000"),
        saldo_inicial=Decimal("5000"),
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
    )
    db.add(b_usd)
    db.flush()

    # H en pesos:
    for i in range(16):
        _agregar_egreso(db, u_m.id, b_ars.id, cats["cat_alim"].id, cats["sub_super"].id, Decimal("10000.00"), Moneda.ARS, date(2026, 10, 2 + (i % 25)))
    cafe_m = []
    for i in range(6):
        tx = _agregar_egreso(db, u_m.id, b_ars.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("2000.00"), Moneda.ARS, date(2026, 10, 5 + i))
        cafe_m.append(tx)
    db.flush()
    _agregar_egreso(db, u_m.id, b_ars.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("-500.00"), Moneda.ARS, date(2026, 10, 6), reintegro_de_id=cafe_m[0].id)
    for i in range(4):
        _agregar_egreso(db, u_m.id, b_ars.id, cats["cat_alim"].id, cats["sub_kiosco"].id, Decimal("1500.00"), Moneda.ARS, date(2026, 10, 10 + i))
    for i in range(6):
        _agregar_egreso(db, u_m.id, b_ars.id, cats["cat_trans"].id, cats["sub_trans_pub"].id, Decimal("1000.00"), Moneda.ARS, date(2026, 10, 15 + i))

    # Más 3 compras de US$2 en Cafetería
    for i in range(3):
        _agregar_egreso(db, u_m.id, b_usd.id, cats["cat_gastro"].id, cats["sub_cafe"].id, Decimal("2.00"), Moneda.USD, date(2026, 10, 20 + i))

    # Otro usuario con compras idénticas a H para probar aislamiento
    u_otro, b_otro = _crear_usuario(db, "usuario_otro@argentum.com", Moneda.ARS)
    for i in range(16):
        _agregar_egreso(db, u_otro.id, b_otro.id, cats["cat_alim"].id, cats["sub_super"].id, Decimal("10000.00"), Moneda.ARS, date(2026, 10, 2 + (i % 25)))

    db.commit()

    # M con ARS da exactamente lo mismo que H (los dólares no se suman)
    res_m = _gastos_hormiga(db, u_m.id, date(2026, 10, 1), date(2026, 10, 31), Moneda.ARS, date(2026, 10, 31))
    assert res_m == [
        {"categoria": "Cafetería", "cantidad": 6, "total": 11500.0},
        {"categoria": "Kiosco", "cantidad": 4, "total": 6000.0},
    ]

    # u_otro no afecta a u_m
    res_otro = _gastos_hormiga(db, u_otro.id, date(2026, 10, 1), date(2026, 10, 31), Moneda.ARS, date(2026, 10, 31))
    assert res_otro == []
