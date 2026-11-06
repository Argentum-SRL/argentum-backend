# -*- coding: utf-8 -*-
"""
tests/test_presupuesto_sugerencias.py
Pruebas de base y rutas para sugerencias y ritmo de presupuestos:
- R1: ritmo estándar y caso variable menor
- R2: mixto (USD con cotización aplicada)
- R3: cuotas con hijas en distintos meses y gasto común
- R4: sugerencia por 3 períodos superados y actualización de límite
- R5: escalón (pago único que supera el límite)
- Otro usuario: sin presupuestos ni sugerencias
- S: sugerencia de rubro sin presupuesto y variantes con presupuestos existentes
"""
from __future__ import annotations

from pathlib import Path
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
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    EstadoPresupuesto,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario

I = date(2026, 11, 1)
F = date(2026, 11, 30)


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


def _crear_catalogo(db: Session) -> dict[str, Categoria]:
    cats_data = [
        ("Vivienda", TipoCategoria.EGRESO, ["Alquiler", "Luz"]),
        ("Alimentación", TipoCategoria.EGRESO, ["Supermercado", "Verdulería", "Carnicería"]),
        ("Gastronomía", TipoCategoria.EGRESO, ["Restaurantes", "Delivery", "Cafetería"]),
        ("Ocio", TipoCategoria.EGRESO, ["Salidas"]),
        ("Tecnología", TipoCategoria.EGRESO, ["Equipos"]),
        ("Indumentaria", TipoCategoria.EGRESO, ["Ropa"]),
        ("Hogar", TipoCategoria.EGRESO, ["Servicio doméstico"]),
        ("Salud", TipoCategoria.EGRESO, ["Prepaga y obra social"]),
        ("Trabajo", TipoCategoria.INGRESO, ["Sueldo"]),
        ("Banco", TipoCategoria.EGRESO, ["Comisiones"]),
        ("Otros", TipoCategoria.EGRESO, ["Pagos a personas", "Ayuda familiar"]),
    ]
    mapa: dict[str, Categoria] = {}
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
        mapa[cat_nom] = c
    db.commit()
    return mapa


def _crear_usuario(db: Session, email: str) -> tuple[Usuario, Billetera]:
    u = Usuario(
        id=uuid4(),
        email=email,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u)
    db.commit()

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b)
    db.commit()
    return u, b


def _parchear_hoy(monkeypatch, hoy: date):
    monkeypatch.setattr("app.routers.presupuestos.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.services.presupuesto_sugerencias.hoy_argentina", lambda: hoy)


def _client_para_usuario(db: Session, usuario: Usuario) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario
    return TestClient(app)


# ==============================================================================
# R1 y R2
# ==============================================================================

def test_r1_y_r2(db: Session, monkeypatch):
    cat_map = _crear_catalogo(db)
    sub_super = db.query(Subcategoria).filter(Subcategoria.nombre == "Supermercado").first()
    sub_alquiler = db.query(Subcategoria).filter(Subcategoria.nombre == "Alquiler").first()

    u_r1, b_r1 = _crear_usuario(db, "r1@test.com")
    _parchear_hoy(monkeypatch, date(2026, 11, 10))

    # Presupuesto "Casa" ARS mensual 400.000 con Vivienda (categoría entera) y Alimentación > Supermercado
    presu = Presupuesto(
        id=uuid4(),
        usuario_id=u_r1.id,
        nombre="Casa",
        monto=Decimal("400000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu.id, categoria_id=cat_map["Vivienda"].id, subcategoria_id=None))
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu.id, categoria_id=cat_map["Alimentación"].id, subcategoria_id=sub_super.id))
    db.add(PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=I,
        fecha_fin=F,
        monto_limite=Decimal("400000"),
    ))

    # Vivienda > Alquiler 300.000 el 01/11 y Supermercado 34.000 el 05/11
    tx_alq = Transaccion(
        id=uuid4(), usuario_id=u_r1.id, billetera_id=b_r1.id, categoria_id=cat_map["Vivienda"].id, subcategoria_id=sub_alquiler.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("300000"), moneda=Moneda.ARS, fecha=date(2026, 11, 1),
        descripcion="Alquiler", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx_sup = Transaccion(
        id=uuid4(), usuario_id=u_r1.id, billetera_id=b_r1.id, categoria_id=cat_map["Alimentación"].id, subcategoria_id=sub_super.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("34000"), moneda=Moneda.ARS, fecha=date(2026, 11, 5),
        descripcion="Supermercado", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add_all([tx_alq, tx_sup])
    db.commit()

    client = _client_para_usuario(db, u_r1)
    res = client.get("/presupuestos")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    p_data = data[0]
    assert p_data["ritmo_texto"] == "A este ritmo llegás a unos $402.000 (límite $400.000)."
    assert p_data["sugerencia_limite"] is None

    # Con Supermercado 10.000 en vez de 34.000 -> ritmo_texto null
    tx_sup.monto = Decimal("10000")
    db.commit()
    res_10k = client.get("/presupuestos")
    assert res_10k.json()[0]["ritmo_texto"] is None

    # R2 mixto: igual que R1 pero el Supermercado es de US$34 con cotizacion_aplicada 1.000 -> el mismo ritmo_texto
    tx_sup.monto = Decimal("34")
    tx_sup.moneda = Moneda.USD
    tx_sup.cotizacion_aplicada = Decimal("1000")
    db.commit()

    res_r2 = client.get("/presupuestos")
    assert res_r2.json()[0]["ritmo_texto"] == "A este ritmo llegás a unos $402.000 (límite $400.000)."
    app.dependency_overrides.clear()


# ==============================================================================
# R3 cuotas
# ==============================================================================

def test_r3_cuotas(db: Session, monkeypatch):
    cat_map = _crear_catalogo(db)
    sub_equipos = db.query(Subcategoria).filter(Subcategoria.nombre == "Equipos").first()

    u_r3, b_r3 = _crear_usuario(db, "r3@test.com")
    _parchear_hoy(monkeypatch, date(2026, 11, 10))

    # Presupuesto "Tecno" ARS mensual 70.000 con Tecnología, período I a F
    presu = Presupuesto(
        id=uuid4(),
        usuario_id=u_r3.id,
        nombre="Tecno",
        monto=Decimal("70000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu.id, categoria_id=cat_map["Tecnología"].id, subcategoria_id=None))
    db.add(PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=I,
        fecha_fin=F,
        monto_limite=Decimal("70000"),
    ))

    # Un grupo de 3 cuotas de Tecnología > Equipos de 30.000 con hijas el 05/11, el 25/11 y el 25/12
    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=u_r3.id,
        billetera_id=b_r3.id,
        categoria_id=cat_map["Tecnología"].id,
        subcategoria_id=sub_equipos.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("90000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 5),
        descripcion="Notebook",
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
        usuario_id=u_r3.id,
        transaccion_padre_id=tx_padre.id,
        descripcion="Notebook",
        monto_total=Decimal("90000"),
        total_financiado=Decimal("90000"),
        cantidad_cuotas=3,
        tiene_interes=False,
        moneda=Moneda.ARS,
    )
    db.add(grupo)
    db.flush()

    fechas_cuotas = [date(2026, 11, 5), date(2026, 11, 25), date(2026, 12, 25)]
    for n, f_cuota in enumerate(fechas_cuotas, start=1):
        estado_v = EstadoVerificacionTransaccion.CONFIRMADA if n == 1 else EstadoVerificacionTransaccion.PENDIENTE
        tx_h = Transaccion(
            id=uuid4(),
            usuario_id=u_r3.id,
            billetera_id=b_r3.id,
            categoria_id=cat_map["Tecnología"].id,
            subcategoria_id=sub_equipos.id,
            grupo_cuotas_id=grupo.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("30000"),
            moneda=Moneda.ARS,
            fecha=f_cuota,
            descripcion=f"Notebook (Cuota {n}/3)",
            metodo_pago=MetodoPago.DEBITO,
            es_padre_cuotas=False,
            es_cuota_hija=True,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=estado_v,
        )
        db.add(tx_h)
        db.flush()

        c = Cuota(
            id=uuid4(),
            grupo_id=grupo.id,
            transaccion_id=tx_h.id,
            numero_cuota=n,
            monto_proyectado=Decimal("30000"),
            monto_real=Decimal("30000") if n == 1 else None,
            fecha_vencimiento=f_cuota,
            pagada=(n == 1),
        )
        db.add(c)

    # Tecnología > Equipos común 4.000 el 08/11
    tx_comun = Transaccion(
        id=uuid4(),
        usuario_id=u_r3.id,
        billetera_id=b_r3.id,
        categoria_id=cat_map["Tecnología"].id,
        subcategoria_id=sub_equipos.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("4000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 8),
        descripcion="Cable",
        metodo_pago=MetodoPago.DEBITO,
        es_padre_cuotas=False,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_comun)
    db.commit()

    client = _client_para_usuario(db, u_r3)
    res = client.get("/presupuestos")
    assert res.status_code == 200
    p_data = res.json()[0]
    # ritmo_texto "A este ritmo llegás a unos $72.000 (límite $70.000)."
    assert p_data["ritmo_texto"] == "A este ritmo llegás a unos $72.000 (límite $70.000)."
    app.dependency_overrides.clear()


# ==============================================================================
# R4 y actualización PUT
# ==============================================================================

def test_r4_y_put(db: Session, monkeypatch):
    cat_map = _crear_catalogo(db)
    sub_salidas = db.query(Subcategoria).filter(Subcategoria.nombre == "Salidas").first()

    u_r4, b_r4 = _crear_usuario(db, "r4@test.com")
    _parchear_hoy(monkeypatch, date(2026, 11, 10))

    # Presupuesto "Salidas" ARS mensual 50.000 con Ocio
    presu = Presupuesto(
        id=uuid4(),
        usuario_id=u_r4.id,
        nombre="Salidas",
        monto=Decimal("50000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id))

    # Períodos de agosto, septiembre y octubre con límite 50.000 y el de noviembre con 50.000
    for m, d_fin in [(8, 31), (9, 30), (10, 31), (11, 30)]:
        db.add(PeriodoPresupuesto(
            id=uuid4(),
            presupuesto_id=presu.id,
            fecha_inicio=date(2026, m, 1),
            fecha_fin=date(2026, m, d_fin),
            monto_limite=Decimal("50000"),
        ))

    # Ocio > Salidas: agosto 51.000 (1 compra), septiembre 3 x 20.000, octubre 30.000 + 22.300, noviembre 20.000 el 05/11
    db.add(Transaccion(
        id=uuid4(), usuario_id=u_r4.id, billetera_id=b_r4.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("51000"), moneda=Moneda.ARS, fecha=date(2026, 8, 15),
        descripcion="Salida 1", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    for i in range(3):
        db.add(Transaccion(
            id=uuid4(), usuario_id=u_r4.id, billetera_id=b_r4.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("20000"), moneda=Moneda.ARS, fecha=date(2026, 9, 10 + i),
            descripcion=f"Salida sep {i}", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        ))
    db.add(Transaccion(
        id=uuid4(), usuario_id=u_r4.id, billetera_id=b_r4.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("30000"), moneda=Moneda.ARS, fecha=date(2026, 10, 5),
        descripcion="Salida oct 1", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    db.add(Transaccion(
        id=uuid4(), usuario_id=u_r4.id, billetera_id=b_r4.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("22300"), moneda=Moneda.ARS, fecha=date(2026, 10, 20),
        descripcion="Salida oct 2", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    db.add(Transaccion(
        id=uuid4(), usuario_id=u_r4.id, billetera_id=b_r4.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("20000"), moneda=Moneda.ARS, fecha=date(2026, 11, 5),
        descripcion="Salida nov", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    db.commit()

    client = _client_para_usuario(db, u_r4)
    res = client.get("/presupuestos")
    assert res.status_code == 200
    p_data = res.json()[0]
    assert p_data["sugerencia_limite"] == {
        "texto": "Te pasaste los últimos 3 meses. Lo normal tuyo es $53.000.",
        "monto": 53000.0,
        "boton": "Subir a $53.000",
    }
    assert p_data["ritmo_texto"] == "A este ritmo llegás a unos $60.000 (límite $50.000)."

    # después PUT /presupuestos/{id} {"monto": 53000} -> GET: sugerencia_limite null, periodo_actual.monto_limite 53000.0 y ritmo_texto "A este ritmo llegás a unos $60.000 (límite $53.000)."
    res_put = client.put(f"/presupuestos/{presu.id}", json={"monto": 53000})
    assert res_put.status_code == 200

    res_after = client.get("/presupuestos")
    p_after = res_after.json()[0]
    assert p_after["sugerencia_limite"] is None
    assert p_after["periodo_actual"]["monto_limite"] == 53000.0
    assert p_after["ritmo_texto"] == "A este ritmo llegás a unos $60.000 (límite $53.000)."

    # R4 pausado -> los dos null
    presu.estado = EstadoPresupuesto.PAUSADO
    db.commit()
    res_paused = client.get("/presupuestos")
    p_paused = res_paused.json()[0]
    assert p_paused["sugerencia_limite"] is None
    assert p_paused["ritmo_texto"] is None
    app.dependency_overrides.clear()


# ==============================================================================
# R5 escalón
# ==============================================================================

def test_r5_escalon(db: Session, monkeypatch):
    cat_map = _crear_catalogo(db)
    sub_prepaga = db.query(Subcategoria).filter(Subcategoria.nombre == "Prepaga y obra social").first()

    u_r5, b_r5 = _crear_usuario(db, "r5@test.com")
    _parchear_hoy(monkeypatch, date(2026, 11, 10))

    # Presupuesto "Prepaga" ARS mensual 85.000 con Salud > Prepaga y obra social
    presu = Presupuesto(
        id=uuid4(),
        usuario_id=u_r5.id,
        nombre="Prepaga",
        monto=Decimal("85000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu.id, categoria_id=cat_map["Salud"].id, subcategoria_id=sub_prepaga.id))

    # Períodos de agosto a noviembre con límite 85.000
    for m, d_fin in [(8, 31), (9, 30), (10, 31), (11, 30)]:
        db.add(PeriodoPresupuesto(
            id=uuid4(),
            presupuesto_id=presu.id,
            fecha_inicio=date(2026, m, 1),
            fecha_fin=date(2026, m, d_fin),
            monto_limite=Decimal("85000"),
        ))

    # Un pago de 80.000 el día 3 de agosto, septiembre y octubre y uno de 92.000 el 05/11
    for m in [8, 9, 10]:
        db.add(Transaccion(
            id=uuid4(), usuario_id=u_r5.id, billetera_id=b_r5.id, categoria_id=cat_map["Salud"].id, subcategoria_id=sub_prepaga.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("80000"), moneda=Moneda.ARS, fecha=date(2026, m, 3),
            descripcion="Prepaga", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        ))
    db.add(Transaccion(
        id=uuid4(), usuario_id=u_r5.id, billetera_id=b_r5.id, categoria_id=cat_map["Salud"].id, subcategoria_id=sub_prepaga.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("92000"), moneda=Moneda.ARS, fecha=date(2026, 11, 5),
        descripcion="Prepaga nov", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    db.commit()

    client = _client_para_usuario(db, u_r5)
    res = client.get("/presupuestos")
    assert res.status_code == 200
    p_data = res.json()[0]
    # sugerencia_limite {"texto": "Este mes el pago subió a $92.000, más que tu límite de $85.000.", "monto": 92000.0, "boton": "Subir a $92.000"} y ritmo_texto null
    assert p_data["sugerencia_limite"] == {
        "texto": "Este mes el pago subió a $92.000, más que tu límite de $85.000.",
        "monto": 92000.0,
        "boton": "Subir a $92.000",
    }
    assert p_data["ritmo_texto"] is None
    app.dependency_overrides.clear()


# ==============================================================================
# Otro usuario
# ==============================================================================

def test_otro_usuario(db: Session, monkeypatch):
    u_otro, _ = _crear_usuario(db, "otro@test.com")
    _parchear_hoy(monkeypatch, date(2026, 11, 10))

    client = _client_para_usuario(db, u_otro)
    res1 = client.get("/presupuestos")
    assert res1.status_code == 200
    assert res1.json() == []

    res2 = client.get("/presupuestos/sugerencia-rubro")
    assert res2.status_code == 200
    assert res2.json() == {"sugerencia": None}
    app.dependency_overrides.clear()


# ==============================================================================
# S y sugerencia-rubro
# ==============================================================================

def test_s_sugerencia_rubro(db: Session, monkeypatch):
    cat_map = _crear_catalogo(db)
    sub_super = db.query(Subcategoria).filter(Subcategoria.nombre == "Supermercado").first()
    sub_rest = db.query(Subcategoria).filter(Subcategoria.nombre == "Restaurantes").first()
    sub_delivery = db.query(Subcategoria).filter(Subcategoria.nombre == "Delivery").first()
    sub_salidas = db.query(Subcategoria).filter(Subcategoria.nombre == "Salidas").first()

    u_s, b_s = _crear_usuario(db, "s_rubro@test.com")
    _parchear_hoy(monkeypatch, date(2026, 11, 2))

    # Alimentación > Supermercado 950.000 el 01/08, 01/09 y 01/10 de 2026,
    # y Gastronomía > Restaurantes 25.000 los días 10 y 20 de agosto, septiembre y octubre.
    txs_agosto = []
    for m in [8, 9, 10]:
        t1 = Transaccion(
            id=uuid4(), usuario_id=u_s.id, billetera_id=b_s.id, categoria_id=cat_map["Alimentación"].id, subcategoria_id=sub_super.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("950000"), moneda=Moneda.ARS, fecha=date(2026, m, 1),
            descripcion="Super", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        t2 = Transaccion(
            id=uuid4(), usuario_id=u_s.id, billetera_id=b_s.id, categoria_id=cat_map["Gastronomía"].id, subcategoria_id=sub_rest.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("25000"), moneda=Moneda.ARS, fecha=date(2026, m, 10),
            descripcion="Resto 1", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        t3 = Transaccion(
            id=uuid4(), usuario_id=u_s.id, billetera_id=b_s.id, categoria_id=cat_map["Gastronomía"].id, subcategoria_id=sub_rest.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("25000"), moneda=Moneda.ARS, fecha=date(2026, m, 20),
            descripcion="Resto 2", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        db.add_all([t1, t2, t3])
        if m == 8:
            txs_agosto.extend([t1, t2, t3])
    db.commit()

    client = _client_para_usuario(db, u_s)
    res = client.get("/presupuestos/sugerencia-rubro")
    assert res.status_code == 200
    esperado = {
        "sugerencia": {
            "categoria_id": str(cat_map["Gastronomía"].id),
            "categoria": "Gastronomía",
            "moneda": "ARS",
            "monto": 50000.0,
            "titulo": "Gastronomía no tiene presupuesto",
            "texto": "Gastás unos $50.000 por mes.",
        }
    }
    assert res.json() == esperado

    # Con un presupuesto activo de Gastronomía > Delivery -> {"sugerencia": null}
    presu_delivery = Presupuesto(
        id=uuid4(),
        usuario_id=u_s.id,
        nombre="Delivery",
        monto=Decimal("10000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu_delivery)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu_delivery.id, categoria_id=None, subcategoria_id=sub_delivery.id))
    db.commit()

    assert client.get("/presupuestos/sugerencia-rubro").json() == {"sugerencia": None}

    # Con un presupuesto pausado de Gastronomía -> null
    db.delete(presu_delivery)
    db.commit()

    presu_pausado = Presupuesto(
        id=uuid4(),
        usuario_id=u_s.id,
        nombre="Comidas",
        monto=Decimal("20000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.PAUSADO,
    )
    db.add(presu_pausado)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu_pausado.id, categoria_id=cat_map["Gastronomía"].id, subcategoria_id=None))
    db.commit()

    assert client.get("/presupuestos/sugerencia-rubro").json() == {"sugerencia": None}

    # Con un presupuesto activo llamado "gastronomía" que cubre Ocio -> null
    db.delete(presu_pausado)
    db.commit()

    presu_nombre = Presupuesto(
        id=uuid4(),
        usuario_id=u_s.id,
        nombre="gastronomía",
        monto=Decimal("30000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu_nombre)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu_nombre.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id))
    db.commit()

    assert client.get("/presupuestos/sugerencia-rubro").json() == {"sugerencia": None}

    # Sin las filas de agosto -> null
    db.delete(presu_nombre)
    for t in txs_agosto:
        db.delete(t)
    db.commit()

    assert client.get("/presupuestos/sugerencia-rubro").json() == {"sugerencia": None}
    app.dependency_overrides.clear()


def test_t3_estatico_sin_getattr_ni_calcular():
    """T-3: app/services/presupuesto_sugerencias.py no contiene 'getattr(' ni 'calcular_'."""
    ruta = Path(__file__).resolve().parent.parent / "app" / "services" / "presupuesto_sugerencias.py"
    contenido = ruta.read_text(encoding="utf-8")
    assert "getattr(" not in contenido, "presupuesto_sugerencias.py contiene 'getattr('"
    assert "calcular_" not in contenido, "presupuesto_sugerencias.py contiene 'calcular_'"
