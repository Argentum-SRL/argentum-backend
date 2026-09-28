"""
Tests unitarios y de paridad para app/services/definiciones_service.py.
Verifica las 15 reglas canónicas con paridad estricta entre SQL y Python en SQLite en memoria.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Generator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Override JSONB type compilation for SQLite in tests
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.grupo_cuotas import GrupoCuotas
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
from app.services.definiciones_service import (
    cargar_contexto,
    condicion_gasto,
    condicion_ingreso,
    es_gasto,
    es_ingreso,
)

engine_test = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine_test)


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine_test)
    yield
    Base.metadata.drop_all(bind=engine_test)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def base_fixture(db: Session):
    """Crea usuario, catálogo base y billeteras para los tests."""
    hoy = date(2026, 9, 27)

    usuario = Usuario(
        id=uuid4(),
        email=f"test_{uuid4().hex[:8]}@argentum.com",
        nombre="Test",
        apellido="User",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor=28,
    )
    db.add(usuario)

    billetera_comun = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Corriente",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000"),
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
    )
    db.add(billetera_comun)

    billetera_inv = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Fondo Inversion",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000000"),
        estado=EstadoBilletera.ACTIVA,
        es_inversion=True,
    )
    db.add(billetera_inv)

    cat_general = Categoria(id=uuid4(), nombre="General", tipo=TipoCategoria.EGRESO)
    db.add(cat_general)

    cat_ahorro_egr = Categoria(id=uuid4(), nombre="Ahorro", tipo=TipoCategoria.EGRESO)
    db.add(cat_ahorro_egr)

    cat_ahorro_ing = Categoria(id=uuid4(), nombre="Ahorro", tipo=TipoCategoria.INGRESO)
    db.add(cat_ahorro_ing)

    cat_sueldo = Categoria(id=uuid4(), nombre="Sueldo", tipo=TipoCategoria.INGRESO)
    db.add(cat_sueldo)

    cat_banco = Categoria(id=uuid4(), nombre="Banco", tipo=TipoCategoria.EGRESO)
    db.add(cat_banco)

    subcat_tc = Subcategoria(id=uuid4(), categoria_id=cat_banco.id, nombre="Tarjeta de crédito")
    db.add(subcat_tc)

    subcat_prestamos = Subcategoria(id=uuid4(), categoria_id=cat_banco.id, nombre="Préstamos")
    db.add(subcat_prestamos)

    db.commit()

    return {
        "usuario": usuario,
        "hoy": hoy,
        "billetera": billetera_comun,
        "billetera_inv": billetera_inv,
        "cat_general": cat_general,
        "cat_ahorro_egr": cat_ahorro_egr,
        "cat_ahorro_ing": cat_ahorro_ing,
        "cat_sueldo": cat_sueldo,
        "cat_banco": cat_banco,
        "subcat_tc": subcat_tc,
        "subcat_prestamos": subcat_prestamos,
    }


def _crear_tx(db: Session, usuario, billetera, **kwargs) -> Transaccion:
    defaults = {
        "id": uuid4(),
        "usuario_id": usuario.id,
        "billetera_id": billetera.id,
        "tipo": TipoTransaccion.EGRESO,
        "monto": Decimal("10000"),
        "moneda": Moneda.ARS,
        "fecha": date(2026, 9, 27),
        "descripcion": "Transacción Test",
        "origen": OrigenTransaccion.MANUAL,
        "estado_verificacion": EstadoVerificacionTransaccion.CONFIRMADA,
        "es_recurrente": False,
        "es_cuota_hija": False,
        "es_padre_cuotas": False,
    }
    defaults.update(kwargs)
    tx = Transaccion(**defaults)
    db.add(tx)
    db.commit()
    return tx


def _evaluar_paridad_gasto(db: Session, usuario_id, tx: Transaccion, hoy: date, esperado: bool):
    ctx = cargar_contexto(db, usuario_id, hoy)
    py_res = es_gasto(tx, ctx)

    stmt = select(Transaccion.id).where(condicion_gasto(usuario_id, hoy=hoy))
    ids_sql = set(db.execute(stmt).scalars().all())
    sql_res = tx.id in ids_sql

    assert py_res == esperado, f"Python falló para tx {tx.id}: esperado={esperado}, obtenido={py_res}"
    assert sql_res == esperado, f"SQL falló para tx {tx.id}: esperado={esperado}, obtenido={sql_res}"
    assert py_res == sql_res, f"Discrepancia SQL vs Python para tx {tx.id}: SQL={sql_res}, Python={py_res}"


def _evaluar_paridad_ingreso(db: Session, usuario_id, tx: Transaccion, hoy: date, esperado: bool):
    ctx = cargar_contexto(db, usuario_id, hoy)
    py_res = es_ingreso(tx, ctx)

    stmt = select(Transaccion.id).where(condicion_ingreso(usuario_id, hoy=hoy))
    ids_sql = set(db.execute(stmt).scalars().all())
    sql_res = tx.id in ids_sql

    assert py_res == esperado, f"Python falló para tx {tx.id}: esperado={esperado}, obtenido={py_res}"
    assert sql_res == esperado, f"SQL falló para tx {tx.id}: esperado={esperado}, obtenido={sql_res}"
    assert py_res == sql_res, f"Discrepancia SQL vs Python para tx {tx.id}: SQL={sql_res}, Python={py_res}"


# ------------------------------------------------------------------------------
# 15 CASOS DE PRUEBA CANÓNICOS
# ------------------------------------------------------------------------------

from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas


def _crear_grupo(db: Session, usuario, cantidad_cuotas: int, padre_id=None) -> GrupoCuotas:
    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        transaccion_padre_id=padre_id or uuid4(),
        cantidad_cuotas=cantidad_cuotas,
        descripcion=f"Compra en {cantidad_cuotas} cuotas",
        monto_total=Decimal("60000"),
        total_financiado=Decimal("60000"),
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
    )
    db.add(grupo)
    db.commit()
    return grupo


def test_01_padre_1_pago_cuenta(db: Session, base_fixture):
    """Regla 6: Compra con tarjeta en 1 pago: transacción padre cuenta si cantidad_cuotas = 1."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]
    tx_id = uuid4()
    grupo = _crear_grupo(db, u, cantidad_cuotas=1, padre_id=tx_id)

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        id=tx_id,
        categoria_id=base_fixture["cat_general"].id,
        es_padre_cuotas=True, es_cuota_hija=False, grupo_cuotas_id=grupo.id,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=True)


def test_02_hija_1_pago_no_cuenta(db: Session, base_fixture):
    """Regla 6: Cuota hija de compra en 1 pago NO cuenta (cuenta el padre)."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]
    grupo = _crear_grupo(db, u, cantidad_cuotas=1)

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_general"].id,
        es_padre_cuotas=False, es_cuota_hija=True, grupo_cuotas_id=grupo.id,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=False)


def test_03_padre_cuotas_no_cuenta(db: Session, base_fixture):
    """Regla 6: Compra en varias cuotas: padre NO cuenta si cantidad_cuotas > 1."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]
    tx_id = uuid4()
    grupo = _crear_grupo(db, u, cantidad_cuotas=6, padre_id=tx_id)

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        id=tx_id,
        categoria_id=base_fixture["cat_general"].id,
        es_padre_cuotas=True, es_cuota_hija=False, grupo_cuotas_id=grupo.id,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=False)


def test_04_hija_cuotas_cuenta_aunque_pendiente(db: Session, base_fixture):
    """Reglas 6 y 7: Cuota hija de compra en cuotas cuenta aunque esté pendiente."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]
    grupo = _crear_grupo(db, u, cantidad_cuotas=6)

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_general"].id,
        es_padre_cuotas=False, es_cuota_hija=True, grupo_cuotas_id=grupo.id,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=True)


def test_05_hija_fecha_futura_no_cuenta(db: Session, base_fixture):
    """Regla 9: Cuota hija con fecha futura (fecha > hoy) NO cuenta."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]
    futuro = hoy + timedelta(days=5)
    grupo = _crear_grupo(db, u, cantidad_cuotas=6)

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_general"].id,
        es_padre_cuotas=False, es_cuota_hija=True, grupo_cuotas_id=grupo.id,
        fecha=futuro,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=False)


def test_06_pago_resumen_no_cuenta(db: Session, base_fixture):
    """Regla 4: Pago de resumen (pago_resumen_vencimiento no nulo) NO es gasto."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_general"].id,
        pago_resumen_vencimiento=hoy,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=False)


def test_07_percepcion_cuenta(db: Session, base_fixture):
    """Regla 4: Percepción impositiva (pago_origen_id no nulo, pago_resumen_vencimiento nulo) SÍ es gasto."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_general"].id,
        pago_origen_id=uuid4(),
        pago_resumen_vencimiento=None,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=True)


def test_08_prepago_cuotas_cuenta(db: Session, base_fixture):
    """Regla 4: Prepago de cuotas (tarjeta_id no nulo, pago_resumen_vencimiento nulo) SÍ es gasto."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_general"].id,
        tarjeta_id=uuid4(),
        pago_resumen_vencimiento=None,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=True)


def test_09_aporte_retiro_meta_no_cuenta(db: Session, base_fixture):
    """Regla 3: Movimientos con movimiento_meta_id no nulo NO son gasto ni ingreso."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx_egr = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_general"].id,
        tipo=TipoTransaccion.EGRESO,
        movimiento_meta_id=uuid4(),
        fecha=hoy,
    )
    tx_ing = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_sueldo"].id,
        tipo=TipoTransaccion.INGRESO,
        movimiento_meta_id=uuid4(),
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx_egr, hoy, esperado=False)
    _evaluar_paridad_ingreso(db, u.id, tx_ing, hoy, esperado=False)


def test_10_billetera_inversion_no_cuenta(db: Session, base_fixture):
    """Regla 2: Transacciones en billeteras de inversión NO son gasto ni ingreso."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]
    b_inv = base_fixture["billetera_inv"]

    tx_egr = _crear_tx(
        db, u, b_inv,
        categoria_id=base_fixture["cat_general"].id,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy,
    )
    tx_ing = _crear_tx(
        db, u, b_inv,
        categoria_id=base_fixture["cat_sueldo"].id,
        tipo=TipoTransaccion.INGRESO,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx_egr, hoy, esperado=False)
    _evaluar_paridad_ingreso(db, u.id, tx_ing, hoy, esperado=False)


def test_11_categoria_ahorro_no_cuenta(db: Session, base_fixture):
    """Regla 5 y Decisión d: Categoría 'Ahorro' excluida tanto de egresos como de ingresos."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx_egr = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_ahorro_egr"].id,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy,
    )
    tx_ing = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_ahorro_ing"].id,
        tipo=TipoTransaccion.INGRESO,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx_egr, hoy, esperado=False)
    _evaluar_paridad_ingreso(db, u.id, tx_ing, hoy, esperado=False)


def test_12_subcategoria_tarjeta_credito_no_cuenta(db: Session, base_fixture):
    """Regla 5: Subcategoría 'Tarjeta de crédito' de la categoría 'Banco' NO es gasto."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_banco"].id,
        subcategoria_id=base_fixture["subcat_tc"].id,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=False)


def test_13_subcategoria_prestamos_cuenta(db: Session, base_fixture):
    """Regla 5 y DECISIONES: 'Banco / Préstamos' SÍ es gasto (no se excluye por palabra)."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_banco"].id,
        subcategoria_id=base_fixture["subcat_prestamos"].id,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=True)


def test_14_egreso_pendiente_no_cuenta(db: Session, base_fixture):
    """Regla 7: Egreso normal (no cuota hija) pendiente NO cuenta como gasto."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_general"].id,
        tipo=TipoTransaccion.EGRESO,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        fecha=hoy,
    )
    _evaluar_paridad_gasto(db, u.id, tx, hoy, esperado=False)


def test_15_ingreso_normal_cuenta(db: Session, base_fixture):
    """Regla de ingreso: Ingreso confirmado, fecha <= hoy, no inversión, no ahorro, no meta SÍ cuenta."""
    u = base_fixture["usuario"]
    hoy = base_fixture["hoy"]

    tx = _crear_tx(
        db, u, base_fixture["billetera"],
        categoria_id=base_fixture["cat_sueldo"].id,
        tipo=TipoTransaccion.INGRESO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha=hoy,
    )
    _evaluar_paridad_ingreso(db, u.id, tx, hoy, esperado=True)


def test_16_catalogo_sin_ahorro_ni_tarjeta_credito():
    """Caso catálogo sin Ahorro ni Tarjeta de crédito: cargar_contexto no falla, y SQL y Python dan lo mismo."""
    isolated_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=isolated_engine)
    IsolatedSession = sessionmaker(bind=isolated_engine)
    session = IsolatedSession()
    try:
        hoy = date(2026, 9, 27)
        u = Usuario(
            id=uuid4(),
            email=f"test_nocat_{uuid4().hex[:8]}@argentum.com",
            auth_provider=AuthProvider.EMAIL,
            rol=RolUsuario.USUARIO,
            estado=EstadoUsuario.ACTIVO,
            moneda_principal=Moneda.ARS,
        )
        session.add(u)
        billetera = Billetera(
            id=uuid4(),
            usuario_id=u.id,
            nombre="Billetera",
            moneda=Moneda.ARS,
            saldo_actual=Decimal("100000"),
            estado=EstadoBilletera.ACTIVA,
            es_inversion=False,
        )
        session.add(billetera)
        cat_varios = Categoria(id=uuid4(), nombre="Varios", tipo=TipoCategoria.EGRESO)
        subcat_varios = Subcategoria(id=uuid4(), categoria_id=cat_varios.id, nombre="Otros")
        session.add_all([cat_varios, subcat_varios])
        session.commit()

        # 1. cargar_contexto no falla y devuelve conjuntos vacíos
        ctx = cargar_contexto(session, u.id, hoy)
        assert ctx.categoria_ahorro_ids == set()
        assert ctx.subcategoria_tarjeta_ids == set()
        assert ctx.subcategoria_tarjeta_id is None

        # 2. Transacciones con y sin subcategoría
        tx_egreso_subcat = _crear_tx(
            session, u, billetera,
            categoria_id=cat_varios.id,
            subcategoria_id=subcat_varios.id,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
        )
        tx_egreso_sin_subcat = _crear_tx(
            session, u, billetera,
            categoria_id=cat_varios.id,
            subcategoria_id=None,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
        )
        tx_ingreso = _crear_tx(
            session, u, billetera,
            categoria_id=cat_varios.id,
            tipo=TipoTransaccion.INGRESO,
            fecha=hoy,
        )

        # 3. Paridad SQL vs Python
        _evaluar_paridad_gasto(session, u.id, tx_egreso_subcat, hoy, esperado=True)
        _evaluar_paridad_gasto(session, u.id, tx_egreso_sin_subcat, hoy, esperado=True)
        _evaluar_paridad_ingreso(session, u.id, tx_ingreso, hoy, esperado=True)
    finally:
        session.close()
        Base.metadata.drop_all(bind=isolated_engine)

