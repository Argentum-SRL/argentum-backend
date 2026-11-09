# -*- coding: utf-8 -*-
"""
tests/test_consejos_presupuestos.py
Pruebas puras y de base para consejos de ritmo y sin_presupuesto:
- evaluar_ritmo y evaluar_sin_presupuesto (puras)
- evaluar_todos (puras)
- comparar_medicion y TEXTO_ACCION (puras)
- Medición en base (R1 y S)
- GET /consejos/actual (rutas)
- renovar_presupuestos sin notificación de sugerencia (base)
- Paridad y lectura de recolectar + evaluar_todos sin red ni commits
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timezone
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
from app.models.consejo import ConsejoEmitido
from app.models.notificacion import Notificacion
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
from app.services import fin_de_mes_service, presupuesto_service
from app.services.consejos_datos import recolectar
from app.services.consejos_reglas import (
    TEXTO_ACCION,
    DatosConsejos,
    FilaGasto,
    RitmoPresupuesto,
    RubroSinPresupuesto,
    comparar_medicion,
    evaluar_ritmo,
    evaluar_sin_presupuesto,
    evaluar_todos,
)
from app.services.consejos_service import medir_pendientes
from app.services.frescura_service import Frescura

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


# ==============================================================================
# 1. Puras: evaluar_ritmo y evaluar_sin_presupuesto
# ==============================================================================

def test_evaluar_ritmo_y_sin_presupuesto():
    # evaluar_ritmo(RitmoPresupuesto("p-1", "Casa", "ARS", I, F, 402.000, 400.000))
    # -> texto "A este ritmo llegás a unos $402.000 en Casa (límite $400.000).", clave "ritmo/p-1/2026-11-01", accion "ver_presupuestos", ahorro 2.000, valores {"proyeccion": "402000.00", "limite": "400000.00", "presupuesto_id": "p-1", "inicio": "2026-11-01"}; en USD (402, 400) -> "A este ritmo llegás a unos US$402 en Casa (límite US$400)."
    r_ars = RitmoPresupuesto("p-1", "Casa", "ARS", I, F, Decimal("402000"), Decimal("400000"))
    c_ritmo = evaluar_ritmo(r_ars)
    assert c_ritmo.texto == "A este ritmo llegás a unos $402.000 en Casa (límite $400.000)."
    assert c_ritmo.clave == "ritmo/p-1/2026-11-01"
    assert c_ritmo.accion == "ver_presupuestos"
    assert c_ritmo.ahorro == Decimal("2000")
    assert c_ritmo.valores == {
        "proyeccion": "402000.00",
        "limite": "400000.00",
        "presupuesto_id": "p-1",
        "inicio": "2026-11-01",
    }

    r_usd = RitmoPresupuesto("p-1", "Casa", "USD", I, F, Decimal("402"), Decimal("400"))
    c_ritmo_usd = evaluar_ritmo(r_usd)
    assert c_ritmo_usd.texto == "A este ritmo llegás a unos US$402 en Casa (límite US$400)."

    # evaluar_sin_presupuesto(el rubro base, "id-gastro", 02/11/2026)
    # -> texto "Gastronomía no tiene presupuesto. Gastás unos $50.000 por mes.", clave "sin_presupuesto/ARS/Gastronomía/2026-T4", accion "armar_presupuesto", accion_datos {"categoria_id": "id-gastro", "nombre": "Gastronomía", "monto": "50000", "moneda": "ARS"}, ahorro None, valores {"mediana": "50000.00", "categoria": "Gastronomía", "categoria_id": "id-gastro"}; en USD -> "Gastronomía no tiene presupuesto. Gastás unos US$50 por mes."
    rubro_base = RubroSinPresupuesto("Gastronomía", "ARS", Decimal("50000"), Decimal("50000"))
    c_sin = evaluar_sin_presupuesto(rubro_base, "id-gastro", date(2026, 11, 2))
    assert c_sin.texto == "Gastronomía no tiene presupuesto. Gastás unos $50.000 por mes."
    assert c_sin.clave == "sin_presupuesto/ARS/Gastronomía/2026-T4"
    assert c_sin.accion == "armar_presupuesto"
    assert c_sin.accion_datos == {
        "categoria_id": "id-gastro",
        "nombre": "Gastronomía",
        "monto": "50000",
        "moneda": "ARS",
    }
    assert c_sin.ahorro is None
    assert c_sin.valores == {
        "mediana": "50000.00",
        "categoria": "Gastronomía",
        "categoria_id": "id-gastro",
    }

    rubro_usd = RubroSinPresupuesto("Gastronomía", "USD", Decimal("50"), Decimal("50"))
    c_sin_usd = evaluar_sin_presupuesto(rubro_usd, "id-gastro", date(2026, 11, 2))
    assert c_sin_usd.texto == "Gastronomía no tiene presupuesto. Gastás unos US$50 por mes."


# ==============================================================================
# 2. Puras: evaluar_todos con DatosConsejos
# ==============================================================================

def test_evaluar_todos():
    cerrados3 = [
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 8, 1), date(2026, 8, 31)),
    ]

    base_filas = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        base_filas.append(FilaGasto(fecha=d, monto=Decimal("950000"), moneda="ARS", categoria="Alimentación", subcategoria="Supermercado"))
        base_filas.append(FilaGasto(fecha=d, monto=Decimal("25000"), moneda="ARS", categoria="Gastronomía", subcategoria="Restaurantes"))
        base_filas.append(FilaGasto(fecha=d, monto=Decimal("25000"), moneda="ARS", categoria="Gastronomía", subcategoria="Restaurantes"))

    rubro_base = RubroSinPresupuesto("Gastronomía", "ARS", Decimal("50000"), Decimal("50000"))
    sin_presupuesto = evaluar_sin_presupuesto(rubro_base, "id-gastro", date(2026, 11, 2))
    ritmo = evaluar_ritmo(RitmoPresupuesto("p-1", "Casa", "ARS", I, F, Decimal("402000"), Decimal("400000")))

    # evaluar_todos con DatosConsejos(...) -> exactamente [sin_presupuesto]
    datos = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(I, F),
        cerrados=cerrados3,
        filas=base_filas,
        ingresos_45=1,
        libre_ars=Decimal("0"),
        saldo_hoy_ars=Decimal("0"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=None,
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=True,
        factor_cobertura=Decimal("1"),
        ids_categorias={"Gastronomía": "id-gastro"},
    )
    assert evaluar_todos(datos) == [sin_presupuesto]

    # con ritmos = (el de "Casa",) -> exactamente [ritmo, sin_presupuesto]
    datos_ritmo = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(I, F),
        cerrados=cerrados3,
        filas=base_filas,
        ingresos_45=1,
        libre_ars=Decimal("0"),
        saldo_hoy_ars=Decimal("0"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=None,
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=True,
        factor_cobertura=Decimal("1"),
        ids_categorias={"Gastronomía": "id-gastro"},
        ritmos=(RitmoPresupuesto("p-1", "Casa", "ARS", I, F, Decimal("402000"), Decimal("400000")),),
    )
    assert evaluar_todos(datos_ritmo) == [ritmo, sin_presupuesto]

    # con solo los dos primeros cerrados -> []
    datos_2c = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(I, F),
        cerrados=cerrados3[:2],
        filas=base_filas,
        ingresos_45=1,
        libre_ars=Decimal("0"),
        saldo_hoy_ars=Decimal("0"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=None,
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=True,
        factor_cobertura=Decimal("1"),
        ids_categorias={"Gastronomía": "id-gastro"},
    )
    assert evaluar_todos(datos_2c) == []

    # con categorias_con_presupuesto {"Gastronomía"} -> []
    datos_cub = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(I, F),
        cerrados=cerrados3,
        filas=base_filas,
        ingresos_45=1,
        libre_ars=Decimal("0"),
        saldo_hoy_ars=Decimal("0"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=None,
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=True,
        factor_cobertura=Decimal("1"),
        ids_categorias={"Gastronomía": "id-gastro"},
        categorias_con_presupuesto=frozenset({"Gastronomía"}),
    )
    assert evaluar_todos(datos_cub) == []

    # con datos_viejos True -> []
    datos_viejos = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=True,
        cobertura=100,
        ciclo_actual=(I, F),
        cerrados=cerrados3,
        filas=base_filas,
        ingresos_45=1,
        libre_ars=Decimal("0"),
        saldo_hoy_ars=Decimal("0"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=None,
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=True,
        factor_cobertura=Decimal("1"),
        ids_categorias={"Gastronomía": "id-gastro"},
    )
    assert evaluar_todos(datos_viejos) == []

    # con ids_categorias vacío -> []
    datos_sin_ids = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(I, F),
        cerrados=cerrados3,
        filas=base_filas,
        ingresos_45=1,
        libre_ars=Decimal("0"),
        saldo_hoy_ars=Decimal("0"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=None,
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=True,
        factor_cobertura=Decimal("1"),
        ids_categorias={},
    )
    assert evaluar_todos(datos_sin_ids) == []


# ==============================================================================
# 3. Puras: comparar_medicion y TEXTO_ACCION
# ==============================================================================

def test_comparar_medicion_y_texto_accion():
    assert comparar_medicion("ritmo", Decimal("400000"), Decimal("334000")) == "mejoro"
    assert comparar_medicion("ritmo", Decimal("400000"), Decimal("400000")) == "mejoro"
    assert comparar_medicion("ritmo", Decimal("400000"), Decimal("400001")) == "igual"

    assert comparar_medicion("sin_presupuesto", Decimal("0"), Decimal("1")) == "mejoro"
    assert comparar_medicion("sin_presupuesto", Decimal("0"), Decimal("0")) == "igual"

    assert TEXTO_ACCION["ver_presupuestos"] == "Ver presupuestos"
    assert TEXTO_ACCION["armar_presupuesto"] == "Armar uno"


# ==============================================================================
# 4. Medición (base): R1 y S
# ==============================================================================

def test_medicion_base(db: Session):
    cat_map = _crear_catalogo(db)

    # Setup R1
    u_r1 = Usuario(
        id=uuid4(),
        email="r1@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u_r1)
    db.commit()

    b_r1 = Billetera(
        id=uuid4(),
        usuario_id=u_r1.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_r1)
    db.commit()

    # Presupuesto "Casa" ARS mensual 400.000 con Vivienda (categoría entera) y Alimentación > Supermercado
    presu_casa = Presupuesto(
        id=uuid4(),
        usuario_id=u_r1.id,
        nombre="Casa",
        monto=Decimal("400000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu_casa)
    db.commit()

    sub_super = db.query(Subcategoria).filter(Subcategoria.nombre == "Supermercado").first()
    sub_alquiler = db.query(Subcategoria).filter(Subcategoria.nombre == "Alquiler").first()

    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu_casa.id, categoria_id=cat_map["Vivienda"].id, subcategoria_id=None))
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu_casa.id, categoria_id=cat_map["Alimentación"].id, subcategoria_id=sub_super.id))

    per_nov = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu_casa.id,
        fecha_inicio=date(2026, 11, 1),
        fecha_fin=date(2026, 11, 30),
        monto_limite=Decimal("400000"),
    )
    db.add(per_nov)
    db.commit()

    # Vivienda > Alquiler 300.000 el 01/11 y Supermercado 34.000 el 05/11
    tx_alq = Transaccion(
        id=uuid4(),
        usuario_id=u_r1.id,
        billetera_id=b_r1.id,
        categoria_id=cat_map["Vivienda"].id,
        subcategoria_id=sub_alquiler.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("300000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 1),
        descripcion="Alquiler",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx_sup = Transaccion(
        id=uuid4(),
        usuario_id=u_r1.id,
        billetera_id=b_r1.id,
        categoria_id=cat_map["Alimentación"].id,
        subcategoria_id=sub_super.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("34000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 5),
        descripcion="Supermercado",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add_all([tx_alq, tx_sup])
    db.commit()

    # ConsejoEmitido ritmo emitido el 10/11/2026 (valores del presupuesto "Casa")
    c_emitido_r1 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u_r1.id,
        tipo="ritmo",
        clave=f"ritmo/{presu_casa.id}/2026-11-01",
        moneda="ARS",
        texto="A este ritmo llegás a unos $402.000 en Casa (límite $400.000).",
        accion="ver_presupuestos",
        valores={
            "proyeccion": "402000.00",
            "limite": "400000.00",
            "presupuesto_id": str(presu_casa.id),
            "inicio": "2026-11-01",
        },
        fecha_emision=date(2026, 11, 10),
        fecha_vence=date(2026, 11, 17),
    )
    db.add(c_emitido_r1)
    db.commit()

    # con hoy 31/01/2027 -> medicion None
    medir_pendientes(db, u_r1, date(2027, 1, 31))
    db.refresh(c_emitido_r1)
    assert c_emitido_r1.medicion is None

    # con hoy 01/02/2027 -> {"resultado": "mejoro", "antes": "400000.00", "despues": "334000.00"}
    medir_pendientes(db, u_r1, date(2027, 2, 1))
    db.refresh(c_emitido_r1)
    assert c_emitido_r1.medicion == {"resultado": "mejoro", "antes": "400000.00", "despues": "334000.00"}

    # con un Supermercado extra de 70.000 el 20/11 -> {"resultado": "igual", "antes": "400000.00", "despues": "404000.00"}
    tx_extra = Transaccion(
        id=uuid4(),
        usuario_id=u_r1.id,
        billetera_id=b_r1.id,
        categoria_id=cat_map["Alimentación"].id,
        subcategoria_id=sub_super.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("70000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 20),
        descripcion="Supermercado extra",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_extra)
    c_emitido_r1.medicion = None
    c_emitido_r1.fecha_medicion = None
    db.commit()

    medir_pendientes(db, u_r1, date(2027, 2, 1))
    db.refresh(c_emitido_r1)
    assert c_emitido_r1.medicion == {"resultado": "igual", "antes": "400000.00", "despues": "404000.00"}

    # con el período de noviembre borrado -> {"resultado": "sin_dato", "antes": "400000.00", "despues": "0.00"}
    db.delete(per_nov)
    c_emitido_r1.medicion = None
    c_emitido_r1.fecha_medicion = None
    db.commit()

    medir_pendientes(db, u_r1, date(2027, 2, 1))
    db.refresh(c_emitido_r1)
    assert c_emitido_r1.medicion == {"resultado": "sin_dato", "antes": "400000.00", "despues": "0.00"}

    # Setup Usuario S con un sin_presupuesto emitido el 02/11/2026 (categoria_id de Gastronomía)
    u_s = Usuario(
        id=uuid4(),
        email="s@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u_s)
    db.commit()

    c_emitido_s = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u_s.id,
        tipo="sin_presupuesto",
        clave="sin_presupuesto/ARS/Gastronomía/2026-T4",
        moneda="ARS",
        texto="Gastronomía no tiene presupuesto. Gastás unos $50.000 por mes.",
        accion="armar_presupuesto",
        valores={
            "mediana": "50000.00",
            "categoria": "Gastronomía",
            "categoria_id": str(cat_map["Gastronomía"].id),
        },
        fecha_emision=date(2026, 11, 2),
        fecha_vence=date(2026, 11, 9),
    )
    db.add(c_emitido_s)
    db.commit()

    # sin presupuesto -> hoy 01/02/2027 "igual"
    medir_pendientes(db, u_s, date(2027, 2, 1))
    db.refresh(c_emitido_s)
    assert c_emitido_s.medicion == {"resultado": "igual", "antes": "0.00", "despues": "0.00"}

    # con un presupuesto creado el 05/11/2026 que incluye Gastronomía -> hoy 01/02/2027 {"resultado": "mejoro", "antes": "0.00", "despues": "1.00"}
    presu_gastro = Presupuesto(
        id=uuid4(),
        usuario_id=u_s.id,
        nombre="Comidas afuera",
        monto=Decimal("50000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc),
    )
    db.add(presu_gastro)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu_gastro.id, categoria_id=cat_map["Gastronomía"].id, subcategoria_id=None))
    c_emitido_s.medicion = None
    c_emitido_s.fecha_medicion = None
    db.commit()

    medir_pendientes(db, u_s, date(2027, 2, 1))
    db.refresh(c_emitido_s)
    assert c_emitido_s.medicion == {"resultado": "mejoro", "antes": "0.00", "despues": "1.00"}


# ==============================================================================
# 5. GET /consejos/actual con accion_texto
# ==============================================================================

def test_ruta_consejos_actual(db: Session, monkeypatch):
    monkeypatch.setattr("app.services.consejos_service.hoy_argentina", lambda: date(2026, 11, 3))
    usuario = Usuario(
        id=uuid4(),
        email="consejo_test@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(usuario)
    db.commit()

    # con accion "armar_presupuesto" -> accion_texto "Armar uno"
    c_armar = ConsejoEmitido(
        id=uuid4(),
        usuario_id=usuario.id,
        tipo="sin_presupuesto",
        clave="sin_presupuesto/ARS/Gastronomía/2026-T4",
        moneda="ARS",
        texto="Gastronomía no tiene presupuesto.",
        accion="armar_presupuesto",
        accion_datos={"categoria_id": "abc"},
        valores={},
        fecha_emision=date(2026, 11, 2),
        fecha_vence=date(2026, 11, 9),
    )
    db.add(c_armar)
    db.commit()

    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as client:
        # fecha_ref dentro de vigencia (2026-11-03)
        res = client.get("/consejos/actual?fecha_ref=2026-11-03")
        assert res.status_code == 200
        data = res.json()
        assert data["consejo"]["accion"] == "armar_presupuesto"
        assert data["consejo"]["accion_texto"] == "Armar uno"

        # con "ver_presupuestos" -> "Ver presupuestos"
        c_armar.accion = "ver_presupuestos"
        c_armar.tipo = "ritmo"
        db.commit()

        res2 = client.get("/consejos/actual?fecha_ref=2026-11-03")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["consejo"]["accion"] == "ver_presupuestos"
        assert data2["consejo"]["accion_texto"] == "Ver presupuestos"

    app.dependency_overrides.clear()


# ==============================================================================
# 6. renovar_presupuestos con Salidas de R4
# ==============================================================================

def test_renovar_presupuestos_sin_notificacion(db: Session, monkeypatch):
    cat_map = _crear_catalogo(db)
    sub_salidas = db.query(Subcategoria).filter(Subcategoria.nombre == "Salidas").first()

    usuario = Usuario(
        id=uuid4(),
        email="r4_renovar@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(usuario)
    db.commit()

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(billetera)
    db.commit()

    # Presupuesto "Salidas" ARS mensual 50.000 con Ocio
    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Salidas",
        monto=Decimal("50000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id))

    # Períodos de agosto, septiembre y octubre con límite 50.000 (sin período de noviembre)
    for m, d_fin in [(8, 31), (9, 30), (10, 31)]:
        db.add(PeriodoPresupuesto(
            id=uuid4(),
            presupuesto_id=presu.id,
            fecha_inicio=date(2026, m, 1),
            fecha_fin=date(2026, m, d_fin),
            monto_limite=Decimal("50000"),
        ))

    # Ocio > Salidas: agosto 51.000 (1 compra), septiembre 3 x 20.000, octubre 30.000 + 22.300
    db.add(Transaccion(
        id=uuid4(), usuario_id=usuario.id, billetera_id=billetera.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("51000"), moneda=Moneda.ARS, fecha=date(2026, 8, 15),
        descripcion="Salida 1", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    for i in range(3):
        db.add(Transaccion(
            id=uuid4(), usuario_id=usuario.id, billetera_id=billetera.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("20000"), moneda=Moneda.ARS, fecha=date(2026, 9, 10 + i),
            descripcion=f"Salida sep {i}", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        ))
    db.add(Transaccion(
        id=uuid4(), usuario_id=usuario.id, billetera_id=billetera.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("30000"), moneda=Moneda.ARS, fecha=date(2026, 10, 5),
        descripcion="Salida oct 1", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    db.add(Transaccion(
        id=uuid4(), usuario_id=usuario.id, billetera_id=billetera.id, categoria_id=cat_map["Ocio"].id, subcategoria_id=sub_salidas.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("22300"), moneda=Moneda.ARS, fecha=date(2026, 10, 20),
        descripcion="Salida oct 2", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    db.commit()

    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 11, 10))

    # Ejecutar renovar_presupuestos
    presupuesto_service.renovar_presupuestos(db)

    # Crea el período de noviembre
    per_nov = db.query(PeriodoPresupuesto).filter(
        PeriodoPresupuesto.presupuesto_id == presu.id,
        PeriodoPresupuesto.fecha_inicio == date(2026, 11, 1),
    ).first()
    assert per_nov is not None
    assert per_nov.fecha_fin == date(2026, 11, 30)
    assert per_nov.monto_limite == Decimal("50000")

    # No crea ninguna notificación con grupo que empiece con "presupuestos/sugerencia/"
    notifs = db.query(Notificacion).filter(
        Notificacion.usuario_id == usuario.id,
        Notificacion.grupo_agrupacion.like("presupuestos/sugerencia/%"),
    ).all()
    assert len(notifs) == 0


def test_renovar_presupuestos_sin_periodos_no_crea_periodo(db: Session, monkeypatch):
    usuario = Usuario(
        id=uuid4(),
        email="sin_periodos@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(usuario)

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Sin Períodos",
        monto=Decimal("50000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu)
    db.commit()

    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 11, 10))

    presupuesto_service.renovar_presupuestos(db)

    # Sigue sin períodos
    periodos = db.query(PeriodoPresupuesto).filter(
        PeriodoPresupuesto.presupuesto_id == presu.id
    ).all()
    assert len(periodos) == 0

    # Sin notificaciones
    notifs = db.query(Notificacion).filter(
        Notificacion.usuario_id == usuario.id
    ).all()
    assert len(notifs) == 0


# ==============================================================================
# 7. Paridad y lectura: R1 y S sin commits ni red
# ==============================================================================

def test_paridad_y_lectura(db: Session, monkeypatch):
    cat_map = _crear_catalogo(db)
    sub_super = db.query(Subcategoria).filter(Subcategoria.nombre == "Supermercado").first()
    sub_alquiler = db.query(Subcategoria).filter(Subcategoria.nombre == "Alquiler").first()
    sub_rest = db.query(Subcategoria).filter(Subcategoria.nombre == "Restaurantes").first()

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

    # R1: hoy 10/11/2026
    u_r1 = Usuario(
        id=uuid4(),
        email="r1_paridad@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u_r1)
    db.commit()

    b_r1 = Billetera(
        id=uuid4(),
        usuario_id=u_r1.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_r1)

    presu_casa = Presupuesto(
        id=uuid4(),
        usuario_id=u_r1.id,
        nombre="Casa",
        monto=Decimal("400000"),
        moneda=Moneda.ARS,
        periodo="mensual",
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    db.add(presu_casa)
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu_casa.id, categoria_id=cat_map["Vivienda"].id, subcategoria_id=None))
    db.add(PresupuestoCategoria(id=uuid4(), presupuesto_id=presu_casa.id, categoria_id=cat_map["Alimentación"].id, subcategoria_id=sub_super.id))
    db.add(PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu_casa.id,
        fecha_inicio=date(2026, 11, 1),
        fecha_fin=date(2026, 11, 30),
        monto_limite=Decimal("400000"),
    ))

    db.add(Transaccion(
        id=uuid4(), usuario_id=u_r1.id, billetera_id=b_r1.id, categoria_id=cat_map["Vivienda"].id, subcategoria_id=sub_alquiler.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("300000"), moneda=Moneda.ARS, fecha=date(2026, 11, 1),
        descripcion="Alquiler", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    db.add(Transaccion(
        id=uuid4(), usuario_id=u_r1.id, billetera_id=b_r1.id, categoria_id=cat_map["Alimentación"].id, subcategoria_id=sub_super.id,
        tipo=TipoTransaccion.EGRESO, monto=Decimal("34000"), moneda=Moneda.ARS, fecha=date(2026, 11, 5),
        descripcion="Supermercado", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    ))
    db.commit()

    # Recolectar de R1 con commit y flush contados = 0 y la red bloqueada
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

    monkeypatch.setattr("socket.socket.connect", fail_network)

    datos_r1 = recolectar(db, u_r1, hoy=date(2026, 11, 10))
    consejos_r1 = evaluar_todos(datos_r1)

    assert commit_count == 0
    assert flush_count == 0

    # incluye un ritmo con texto "A este ritmo llegás a unos $402.000 en Casa (límite $400.000)."
    ritmo_c = next((c for c in consejos_r1 if c.tipo == "ritmo"), None)
    assert ritmo_c is not None
    assert ritmo_c.texto == "A este ritmo llegás a unos $402.000 en Casa (límite $400.000)."

    # Setup Usuario S: hoy 02/11/2026
    # Alimentación > Supermercado 950.000 el 01/08, 01/09 y 01/10 de 2026,
    # y Gastronomía > Restaurantes 25.000 los días 10 y 20 de agosto, septiembre y octubre.
    u_s = Usuario(
        id=uuid4(),
        email="s_paridad@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u_s)
    db.commit()

    b_s = Billetera(
        id=uuid4(),
        usuario_id=u_s.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("5000000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_s)

    for m in [8, 9, 10]:
        db.add(Transaccion(
            id=uuid4(), usuario_id=u_s.id, billetera_id=b_s.id, categoria_id=cat_map["Alimentación"].id, subcategoria_id=sub_super.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("950000"), moneda=Moneda.ARS, fecha=date(2026, m, 1),
            descripcion="Super", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        ))
        db.add(Transaccion(
            id=uuid4(), usuario_id=u_s.id, billetera_id=b_s.id, categoria_id=cat_map["Gastronomía"].id, subcategoria_id=sub_rest.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("25000"), moneda=Moneda.ARS, fecha=date(2026, m, 10),
            descripcion="Resto 1", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        ))
        db.add(Transaccion(
            id=uuid4(), usuario_id=u_s.id, billetera_id=b_s.id, categoria_id=cat_map["Gastronomía"].id, subcategoria_id=sub_rest.id,
            tipo=TipoTransaccion.EGRESO, monto=Decimal("25000"), moneda=Moneda.ARS, fecha=date(2026, m, 20),
            descripcion="Resto 2", metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        ))
    db.commit()

    datos_s = recolectar(db, u_s, hoy=date(2026, 11, 2))
    consejos_s = evaluar_todos(datos_s)

    # incluye un sin_presupuesto con texto "Gastronomía no tiene presupuesto. Gastás unos $50.000 por mes."
    # y accion_datos con el mismo categoria_id que da la ruta de S
    sin_c = next((c for c in consejos_s if c.tipo == "sin_presupuesto"), None)
    assert sin_c is not None
    assert sin_c.texto == "Gastronomía no tiene presupuesto. Gastás unos $50.000 por mes."
    assert sin_c.accion_datos["categoria_id"] == str(cat_map["Gastronomía"].id)
