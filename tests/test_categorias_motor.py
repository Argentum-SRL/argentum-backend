# -*- coding: utf-8 -*-
"""
tests/test_categorias_motor.py - Tests T.6 a T.12 para el motor financiero, seed y API.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
from app.models.transaccion import TipoTransaccion, EstadoVerificacionTransaccion
from app.models.usuario import Moneda
from app.services import categoria_service
from app.services.definiciones_service import (
    ContextoDefiniciones,
    es_ingreso,
    condicion_ingreso,
)
from app.services.ingreso_habitual_service import (
    _calcular_ingreso_habitual_moneda_pura,
)
from app.services.pago_resumen_service import (
    _subcategoria_impuestos_banco,
    _subcategoria_intereses_banco,
)
from app.utils.finanzas import es_categoria_elegible_compromiso
from app.utils.patrones import rubro_de


# ==============================================================================
# T.6: es_ingreso y condición SQL para Reintegros vs Cobros de personas
# ==============================================================================

def test_t6_es_ingreso_reintegros_vs_cobros_personas():
    user_id = uuid4()
    cat_otros_id = uuid4()
    sub_reintegros_id = uuid4()
    sub_cobros_id = uuid4()
    billetera_id = uuid4()

    ctx = ContextoDefiniciones(
        grupos_cuotas_cantidades={},
        billeteras_inversion_ids=set(),
        categoria_ahorro_ids=set(),
        subcategoria_tarjeta_id=None,
        subcategoria_tarjeta_ids=set(),
        hoy=date(2026, 10, 26),
        subcategoria_reintegros_ids={sub_reintegros_id},
    )

    class FakeTx:
        def __init__(self, sub_id):
            self.tipo = TipoTransaccion.INGRESO
            self.billetera_id = billetera_id
            self.movimiento_meta_id = None
            self.categoria_id = cat_otros_id
            self.subcategoria_id = sub_id
            self.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
            self.fecha = date(2026, 10, 20)
            self.moneda = Moneda.ARS
            self.billetera = MagicMock(es_inversion=False)

    tx_reintegros = FakeTx(sub_reintegros_id)
    tx_cobros = FakeTx(sub_cobros_id)

    # Evaluación en memoria
    assert es_ingreso(tx_reintegros, ctx) is False
    assert es_ingreso(tx_cobros, ctx) is True

    # Paridad con condición SQL
    cond = condicion_ingreso(user_id, hoy=date(2026, 10, 26))
    cond_str = str(cond).lower()
    # Debe filtrar por subcategorias de reintegros
    assert "reintegros" in cond_str or "subcategoria_id not in" in cond_str


# ==============================================================================
# T.7: Fuentes habituales y aguinaldo
# ==============================================================================

def test_t7_ingreso_habitual_fuentes_y_aguinaldo():
    hoy = date(2026, 10, 26)
    ctx = ContextoDefiniciones(
        grupos_cuotas_cantidades={},
        billeteras_inversion_ids=set(),
        categoria_ahorro_ids=set(),
        subcategoria_tarjeta_id=None,
        subcategoria_tarjeta_ids=set(),
        hoy=hoy,
        subcategoria_reintegros_ids=set(),
    )

    # 6 ciclos completos mensuales
    ciclos = [
        (date(2026, 4, 1), date(2026, 4, 30)),
        (date(2026, 5, 1), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 8, 1), date(2026, 8, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 31)),  # actual
    ]

    class FakeIngresoTx:
        def __init__(self, cat_nombre, subcat_nombre, monto, fecha):
            self.id = uuid4()
            self.tipo = TipoTransaccion.INGRESO
            self.moneda = Moneda.ARS
            self.monto = Decimal(str(monto))
            self.fecha = fecha
            self.billetera_id = uuid4()
            self.movimiento_meta_id = None
            self.categoria_id = uuid4()
            self.subcategoria_id = uuid4()
            self.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
            self.billetera = MagicMock(es_inversion=False)
            self.categoria = MagicMock(nombre=cat_nombre)
            self.subcategoria = MagicMock(nombre=subcat_nombre)

    # 1. 6 ciclos con 50.000 en Cobros de personas: sin fuente habitual por él
    txs_cobros = [
        FakeIngresoTx("Otros", "Cobros de personas", 50000, date(2026, m, 15))
        for m in range(4, 10)
    ]
    res_cobros = _calcular_ingreso_habitual_moneda_pura(txs_cobros, ciclos, hoy, ctx, "ARS")
    fuentes_cobros = [f for f in res_cobros.fuentes if f.subcategoria_nombre == "Cobros de personas"]
    assert len(fuentes_cobros) == 0

    # 2. Lo mismo en Ayuda familiar de ingreso: fuente habitual regular de 50.000
    txs_ayuda = [
        FakeIngresoTx("Otros", "Ayuda familiar", 50000, date(2026, m, 15))
        for m in range(4, 10)
    ]
    res_ayuda = _calcular_ingreso_habitual_moneda_pura(txs_ayuda, ciclos, hoy, ctx, "ARS")
    fuentes_ayuda = [f for f in res_ayuda.fuentes if f.subcategoria_nombre == "Ayuda familiar"]
    assert len(fuentes_ayuda) == 1
    assert fuentes_ayuda[0].tipo == "regular"
    assert fuentes_ayuda[0].monto == Decimal("50000")

    # 3. Aguinaldo con fuente regular en Trabajo > Sueldo de 800.000: aguinaldo esperado 400.000
    txs_sueldo = [
        FakeIngresoTx("Trabajo", "Sueldo", 800000, date(2026, m, 5))
        for m in range(4, 10)
    ]
    # Sumar un aguinaldo cobrado en los últimos 12 meses (junio 2026)
    tx_ag = FakeIngresoTx("Trabajo", "Aguinaldo", 350000, date(2026, 6, 28))
    res_sueldo = _calcular_ingreso_habitual_moneda_pura(txs_sueldo + [tx_ag], ciclos, hoy, ctx, "ARS")
    assert res_sueldo.tiene_aguinaldo is True
    assert res_sueldo.proximo_aguinaldo_monto == Decimal("400000.00")


# ==============================================================================
# T.8: es_categoria_elegible_compromiso
# ==============================================================================

def test_t8_es_categoria_elegible_compromiso():
    # Casos verdaderos
    verdaderos = [
        ("Vivienda", None),
        ("Impuestos", None),
        ("Impuestos", "Patente"),
        ("Impuestos", "Monotributo"),
        ("Impuestos", "ABL e inmobiliario"),
        ("Hogar", "Servicio doméstico"),
        ("Tecnología", "Internet"),
        ("Tecnología", "Celular"),
        ("Salud", "Prepaga y obra social"),
        ("Educación", "Colegio y facultad"),
        ("Banco", "Seguros"),
        # Nombres viejos conservados
        ("Comunicación", "Internet y cable"),
        ("Salud", "Obra social / Prepaga"),
        ("Educación", "Cuotas"),
        ("Vivienda", "Impuestos"),
    ]
    for cat, sub in verdaderos:
        assert es_categoria_elegible_compromiso(cat, sub) is True, f"Fallo esperado True para: ({cat}, {sub})"

    # Casos falsos
    falsos = [
        ("Tecnología", None),
        ("Tecnología", "Apps y programas"),
        ("Ocio", "Streaming"),
        ("Otros", "Pagos a personas"),
        ("Banco", "Impuestos"),
        ("Hogar", "Muebles y electro"),
    ]
    for cat, sub in falsos:
        assert es_categoria_elegible_compromiso(cat, sub) is False, f"Fallo esperado False para: ({cat}, {sub})"


# ==============================================================================
# T.9: rubro_de (costumbre vs dia_a_dia)
# ==============================================================================

def test_t9_rubro_de():
    class FakeTx:
        def __init__(self, cat, sub):
            self.categoria = MagicMock(nombre=cat)
            self.subcategoria = MagicMock(nombre=sub)

    # Costumbre
    assert rubro_de(FakeTx("Ocio", "Streaming")) == "costumbre"
    assert rubro_de(FakeTx("Cuidado personal", "Peluquería y belleza")) == "costumbre"
    assert rubro_de(FakeTx("Ocio", "Deportes y gimnasio")) == "costumbre"
    assert rubro_de(FakeTx("Transporte", "Taxi y apps")) == "costumbre"

    # Día a día
    assert rubro_de(FakeTx("Otros", "Pagos a personas")) == "dia_a_dia"
    assert rubro_de(FakeTx("Alimentación", "Panadería")) == "dia_a_dia"
    assert rubro_de(FakeTx("Tecnología", "Internet")) == "dia_a_dia"


# ==============================================================================
# T.10: Seed categorías y sus guardas
# ==============================================================================

def test_t10_seed_categorias_guardas(caplog):
    from scripts.seed_categorias import seed_categorias

    # Usar sqlite en memoria
    engine = create_engine("sqlite:///:memory:")
    Categoria.__table__.create(engine)
    Subcategoria.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # Caso 1: Guarda 1 -> categoría activa 'Recreativo' sin 'Ocio' -> no hace nada y loguea mensaje
        cat_rec = Categoria(
            nombre="Recreativo",
            tipo=TipoCategoria.EGRESO,
            orden=8,
            icono="entretenimiento",
            color="#EC4899",
            estado=EstadoCategoria.ACTIVA,
        )
        db.add(cat_rec)
        db.commit()

        seed_categorias(db)
        # Comprobar que no creó nada y dejó el log exacto
        assert any("Seed de categorías salteado: falta la migración a5f6a7b8c9d0." in r.message for r in caplog.records)
        assert db.query(Categoria).filter(Categoria.nombre == "Ocio").first() is None

        # Borrar recreativo para seguir
        db.delete(cat_rec)
        db.commit()

        # Caso 2: Seed sobre árbol nuevo completo
        seed_categorias(db)
        cats_count = db.query(Categoria).filter(Categoria.estado == EstadoCategoria.ACTIVA).count()
        assert cats_count == 20

        # Caso 3: Categoría fuera del catálogo sin uso -> se archiva
        cat_fantasma = Categoria(
            nombre="Fantasma",
            tipo=TipoCategoria.EGRESO,
            orden=99,
            estado=EstadoCategoria.ACTIVA,
        )
        db.add(cat_fantasma)
        db.commit()

        with patch("scripts.seed_categorias._categoria_en_uso", return_value=False):
            seed_categorias(db)
        db.refresh(cat_fantasma)
        assert cat_fantasma.estado == EstadoCategoria.ARCHIVADA

        # Caso 4: Categoría fuera del catálogo con uso -> queda activa y loguea error
        cat_en_uso = Categoria(
            nombre="Inarchivada",
            tipo=TipoCategoria.EGRESO,
            orden=98,
            estado=EstadoCategoria.ACTIVA,
        )
        db.add(cat_en_uso)
        db.flush()

        # Simular uso en _categoria_en_uso
        with patch("scripts.seed_categorias._categoria_en_uso", return_value=True):
            seed_categorias(db)
            db.refresh(cat_en_uso)
            assert cat_en_uso.estado == EstadoCategoria.ACTIVA
            assert any("Categoría activa fuera del catálogo en uso no puede archivarse: Inarchivada" in r.message for r in caplog.records)
    finally:
        db.close()


# ==============================================================================
# T.11: Pago resumen subcategorías intereses e impuestos
# ==============================================================================

def test_t11_subcategoria_intereses_e_impuestos_banco():
    engine = create_engine("sqlite:///:memory:")
    Categoria.__table__.create(engine)
    Subcategoria.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        cat_banco = Categoria(
            nombre="Banco",
            tipo=TipoCategoria.EGRESO,
            orden=13,
            estado=EstadoCategoria.ACTIVA,
        )
        db.add(cat_banco)
        db.commit()

        # 1. _subcategoria_intereses_banco devuelve si existe, o crea "Intereses"
        sub_int = _subcategoria_intereses_banco(db, cat_banco)
        assert sub_int is not None
        assert sub_int.nombre == "Intereses"
        assert sub_int.es_sistema is False

        # Segunda llamada devuelve la existente
        sub_int_2 = _subcategoria_intereses_banco(db, cat_banco)
        assert sub_int_2.id == sub_int.id

        # 2. _subcategoria_impuestos_banco crea "Impuestos" con es_sistema True
        sub_imp = _subcategoria_impuestos_banco(db, cat_banco)
        assert sub_imp is not None
        assert sub_imp.nombre == "Impuestos"
        assert sub_imp.es_sistema is True
    finally:
        db.close()


# ==============================================================================
# T.12: GET /categorias trae orden y subcategorías traen es_sistema
# ==============================================================================

def test_t12_api_categorias_orden_y_es_sistema():
    categoria_service.invalidar_cache_categorias_globales()

    db_mock = MagicMock()
    cat1 = MagicMock(id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO, orden=1, icono="alim", color="#F97316", estado=EstadoCategoria.ACTIVA)
    cat2 = MagicMock(id=uuid4(), nombre="Trabajo", tipo=TipoCategoria.INGRESO, orden=1, icono="sal", color="#16A34A", estado=EstadoCategoria.ACTIVA)

    sub1 = MagicMock(id=uuid4(), categoria_id=cat1.id, nombre="Supermercado", orden=0, es_sistema=False, estado=EstadoSubcategoria.ACTIVA)
    sub2 = MagicMock(id=uuid4(), categoria_id=cat1.id, nombre="Impuestos", orden=5, es_sistema=True, estado=EstadoSubcategoria.ACTIVA)

    exec_res_cats = MagicMock()
    exec_res_cats.scalars.return_value.all.return_value = [cat1, cat2]

    exec_res_subs = MagicMock()
    exec_res_subs.scalars.return_value.all.return_value = [sub1, sub2]

    db_mock.execute.side_effect = [exec_res_cats, exec_res_subs]

    cats_data, subs_data = categoria_service.obtener_categorias_globales(db_mock)

    # Verifica campos requeridos
    assert "orden" in cats_data[0]
    assert cats_data[0]["orden"] == 1

    assert "es_sistema" in subs_data[0]
    assert subs_data[0]["es_sistema"] is False
    assert subs_data[1]["es_sistema"] is True
