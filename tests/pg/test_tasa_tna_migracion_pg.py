"""
TE.4b: Prueba de migración e7f8a9b0c1d2_tasa_interes_tna en PostgreSQL.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.billetera import Billetera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.grupo_cuotas import GrupoCuotas
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario


def _cargar_modulo_migracion():
    ruta = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "e7f8a9b0c1d2_tasa_interes_tna.py"
    )
    spec = importlib.util.spec_from_file_location("mig_e7f8a9b0c1d2", ruta)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _crear_entorno_base(db):
    u = Usuario(
        id=uuid4(),
        email=f"pg_tna_{uuid4().hex[:8]}@test.com",
        nombre="Usuario PG TNA",
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
        nombre="Banco PG",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
    )
    cat = Categoria(
        id=uuid4(),
        nombre=f"CatTNA_{uuid4().hex[:6]}",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([u, b, cat])
    db.flush()
    return u, b, cat


def _crear_tx_y_grupo(db, u, b, cat, tasa_interes: Decimal | None) -> GrupoCuotas:
    tx = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("120000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Compra prueba TNA",
        categoria_id=cat.id,
        billetera_id=b.id,
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=True,
    )
    db.add(tx)
    db.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        transaccion_padre_id=tx.id,
        descripcion="Grupo prueba TNA",
        monto_total=Decimal("120000.00"),
        cantidad_cuotas=12,
        tiene_interes=(tasa_interes is not None and tasa_interes > 0),
        tasa_interes=tasa_interes,
        total_financiado=Decimal("120000.00"),
        moneda=Moneda.ARS,
        primer_vencimiento=date(2026, 11, 10),
    )
    db.add(grupo)
    db.flush()
    return grupo


def test_te4b_migracion_tasa_interes_tna_pg(db, pg_engine):
    """
    TE.4b:
    - Tres grupos con tasa_interes 4.5, NULL y 0:
      después de _migrar_datos 54.0000, NULL y 0.0000;
      después de _revertir_datos 4.5000, NULL y 0.0000.
    - Con un cuarto grupo en 300: _migrar_datos lanza RuntimeError con el texto
      exacto de la decisión 2.a y ninguna tasa cambia.
    """
    mig = _cargar_modulo_migracion()

    u, b, cat = _crear_entorno_base(db)

    # 1. Crear tres grupos con tasa_interes 4.5, NULL y 0
    g1 = _crear_tx_y_grupo(db, u, b, cat, Decimal("4.5"))
    g2 = _crear_tx_y_grupo(db, u, b, cat, None)
    g3 = _crear_tx_y_grupo(db, u, b, cat, Decimal("0"))
    db.commit()

    # 2. Ejecutar _migrar_datos(conn)
    with pg_engine.begin() as conn:
        mig._migrar_datos(conn)

    db.expire_all()
    db.refresh(g1)
    db.refresh(g2)
    db.refresh(g3)

    assert g1.tasa_interes == Decimal("54.0000")
    assert g2.tasa_interes is None
    assert g3.tasa_interes == Decimal("0.0000")

    # 3. Ejecutar _revertir_datos(conn)
    with pg_engine.begin() as conn:
        mig._revertir_datos(conn)

    db.expire_all()
    db.refresh(g1)
    db.refresh(g2)
    db.refresh(g3)

    assert g1.tasa_interes == Decimal("4.5000")
    assert g2.tasa_interes is None
    assert g3.tasa_interes == Decimal("0.0000")

    # 4. Con un cuarto grupo en 300
    g4 = _crear_tx_y_grupo(db, u, b, cat, Decimal("300"))
    db.commit()

    with pg_engine.begin() as conn:
        with pytest.raises(RuntimeError) as exc_info:
            mig._migrar_datos(conn)

    assert str(exc_info.value) == "Hay grupos de cuotas con tasa_interes mayor a 250: revisar antes de pasar a TNA."

    # Verificar que ninguna tasa cambió tras el aborto
    db.expire_all()
    db.refresh(g1)
    db.refresh(g2)
    db.refresh(g3)
    db.refresh(g4)

    assert g1.tasa_interes == Decimal("4.5000")
    assert g2.tasa_interes is None
    assert g3.tasa_interes == Decimal("0.0000")
    assert g4.tasa_interes == Decimal("300.0000")

