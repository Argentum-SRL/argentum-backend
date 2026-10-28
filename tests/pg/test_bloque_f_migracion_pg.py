"""
tests/pg/test_bloque_f_migracion_pg.py — Pruebas de la migración f8a9b0c1d2e3_bloque_f_espejos en PostgreSQL.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario


def _cargar_modulo_migracion():
    ruta = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "f8a9b0c1d2e3_bloque_f_espejos.py"
    )
    spec = importlib.util.spec_from_file_location("mig_f8a9b0c1d2e3", ruta)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_migracion_bloque_f_datos(db: Session):
    """
    con las dos categorías Otros:
    - _migrar_datos crea las dos subcategorías con orden 4 (egreso) y 3 (ingreso);
    - correrla otra vez no duplica (siguen 2);
    - _revertir_datos las borra.
    Con una transacción que usa la de ingreso:
    - _revertir_datos lanza RuntimeError con el texto exacto y no borra nada.
    Sin la categoría Otros de ingreso:
    - _migrar_datos lanza RuntimeError("Falta la categoría Otros de ingreso.") y no crea ninguna.
    """
    mod = _cargar_modulo_migracion()
    _migrar_datos = mod._migrar_datos
    _revertir_datos = mod._revertir_datos

    conn = db.connection()

    # Limpiar cualquier subcategoría previa "Pasó por mi cuenta" si existiera en argentum_pruebas_saldo
    conn.execute(text("DELETE FROM subcategorias WHERE nombre = 'Pasó por mi cuenta'"))
    conn.execute(text("DELETE FROM categorias WHERE nombre = 'Otros'"))
    db.commit()

    # Crear categorías Otros de egreso e ingreso
    cat_egr = Categoria(
        id=uuid4(),
        nombre="Otros",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_ing = Categoria(
        id=uuid4(),
        nombre="Otros",
        tipo=TipoCategoria.INGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([cat_egr, cat_ing])
    db.commit()

    conn = db.connection()

    # 1. _migrar_datos crea las dos subcategorías con orden 4 (egreso) y 3 (ingreso)
    _migrar_datos(conn)
    db.commit()

    sub_egr = db.execute(
        sa.select(Subcategoria).where(
            Subcategoria.categoria_id == cat_egr.id,
            Subcategoria.nombre == "Pasó por mi cuenta",
        )
    ).scalar_one()
    assert sub_egr.orden == 4

    sub_ing = db.execute(
        sa.select(Subcategoria).where(
            Subcategoria.categoria_id == cat_ing.id,
            Subcategoria.nombre == "Pasó por mi cuenta",
        )
    ).scalar_one()
    assert sub_ing.orden == 3

    # Correrla otra vez no duplica (siguen 2)
    conn = db.connection()
    _migrar_datos(conn)
    db.commit()

    total_subs = db.execute(
        sa.select(sa.func.count(Subcategoria.id)).where(Subcategoria.nombre == "Pasó por mi cuenta")
    ).scalar()
    assert total_subs == 2

    # _revertir_datos las borra
    conn = db.connection()
    _revertir_datos(conn)
    db.commit()

    total_despues_revertir = db.execute(
        sa.select(sa.func.count(Subcategoria.id)).where(Subcategoria.nombre == "Pasó por mi cuenta")
    ).scalar()
    assert total_despues_revertir == 0

    # 2. Con una transacción que usa la de ingreso: _revertir_datos lanza RuntimeError con el texto exacto y no borra nada
    conn = db.connection()
    _migrar_datos(conn)
    db.commit()

    sub_ing_nueva = db.execute(
        sa.select(Subcategoria).where(
            Subcategoria.categoria_id == cat_ing.id,
            Subcategoria.nombre == "Pasó por mi cuenta",
        )
    ).scalar_one()

    u = Usuario(
        id=uuid4(),
        email=f"pg_bloque_f_{uuid4().hex[:8]}@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Banco Test",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
    )
    tx = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 20),
        descripcion="Ingreso prueba que bloquea revertir",
        categoria_id=cat_ing.id,
        subcategoria_id=sub_ing_nueva.id,
        billetera_id=b.id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add_all([u, b, tx])
    db.commit()

    conn = db.connection()
    with pytest.raises(RuntimeError) as exc_revert:
        _revertir_datos(conn)
    assert str(exc_revert.value) == "No se puede bajar: «Pasó por mi cuenta» tiene movimientos."

    # No borra nada: siguen 2
    total_con_tx = db.execute(
        sa.select(sa.func.count(Subcategoria.id)).where(Subcategoria.nombre == "Pasó por mi cuenta")
    ).scalar()
    assert total_con_tx == 2

    # Limpiar transacción para continuar prueba
    db.delete(tx)
    db.commit()
    conn = db.connection()
    _revertir_datos(conn)
    db.commit()

    # 3. Sin la categoría Otros de ingreso: _migrar_datos lanza RuntimeError("Falta la categoría Otros de ingreso.") y no crea ninguna
    db.delete(cat_ing)
    db.commit()

    conn = db.connection()
    with pytest.raises(RuntimeError) as exc_migrar:
        _migrar_datos(conn)
    assert str(exc_migrar.value) == "Falta la categoría Otros de ingreso."

    # No crea ninguna
    total_sin_cat = db.execute(
        sa.select(sa.func.count(Subcategoria.id)).where(Subcategoria.nombre == "Pasó por mi cuenta")
    ).scalar()
    assert total_sin_cat == 0

