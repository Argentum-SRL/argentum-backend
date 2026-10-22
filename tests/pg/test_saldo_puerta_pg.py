"""
tests/pg/test_saldo_puerta_pg.py — TD.2, TD.3, TD.4, TD.5, TD.6, TD.13, TD.14, TD.R
Pruebas exhaustivas de la puerta de saldos en PostgreSQL.
"""
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.meta import Meta, EstadoMeta
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
)
from app.models.movimiento_meta import TipoMovimientoMeta
from app.models.usuario import Usuario, Moneda
from app.models.grupo_cuotas import GrupoCuotas
from app.schemas.grupos_cuotas import GrupoCuotasUpdate
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.schemas.suscripcion import SuscripcionCreate
from app.schemas.transaccion import TransaccionCreate
from app.services import (
    ajuste_saldo_service,
    cobro_suscripcion_service,
    conciliacion_service,
    cuotas_service,
    definiciones_service,
    fecha_saldo_service,
    historial_service,
    meta_service,
    rendimiento_billetera_service,
    saldo_billetera_service,
    saldo_programado_service,
    suscripcion_service,
    transaccion_service,
)
from app.utils.fecha import hoy_argentina
from tests.pg.conftest import (
    crear_fixture_a,
    crear_fixture_b,
    crear_tarjeta_para_billetera,
    fijar_hoy,
)


def assert_saldo_cierra(db: Session, billetera: Billetera, hoy: date):
    """Comprueba que saldo_actual sea exactamente igual a calcular_saldo_teorico(hasta=hoy)."""
    db.refresh(billetera)
    teorico = conciliacion_service.calcular_saldo_teorico(db, billetera.id, hasta=hoy)
    assert billetera.saldo_actual == teorico, (
        f"Descuadre en {billetera.nombre}: saldo_actual={billetera.saldo_actual} != teorico={teorico}"
    )


# =============================================================================
# TD.2 SUSCRIPCIÓN
# =============================================================================
def test_td2_cobro_suscripcion(db: Session, usuario_test: Usuario):
    hoy = hoy_argentina()
    billetera_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD2")
    assert_saldo_cierra(db, billetera_a, hoy)

    # Suscripción mensual de $5.000 con próximo cobro futuro
    sub_data = SuscripcionCreate(
        nombre="Netflix Test",
        monto=Decimal("5000.00"),
        moneda="ARS",
        frecuencia="mensual",
        billetera_id=billetera_a.id,
        proximo_cobro=hoy + timedelta(days=10),
    )
    sub = suscripcion_service.crear_suscripcion(db, usuario_test.id, sub_data)

    # 1. proximo_cobro = hoy-25, fecha_creacion = hoy-40
    sub.proximo_cobro = hoy - timedelta(days=25)
    fc_40 = datetime.combine(hoy - timedelta(days=40), time(12, 0)).replace(tzinfo=timezone.utc)
    sub.fecha_creacion = fc_40
    db.commit()

    cobro_suscripcion_service.procesar_cobros_suscripciones(db)
    db.refresh(billetera_a)
    # Cobro anterior a la foto (hoy-20): no mueve saldo_actual, mueve saldo_inicial
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert billetera_a.saldo_inicial == Decimal("275000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    # 2. Con proximo_cobro = hoy-5 sobre Fixture A
    billetera_a2 = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD2 parte 2")
    sub2_data = SuscripcionCreate(
        nombre="Spotify Test",
        monto=Decimal("5000.00"),
        moneda="ARS",
        frecuencia="mensual",
        billetera_id=billetera_a2.id,
        proximo_cobro=hoy + timedelta(days=10),
    )
    sub2 = suscripcion_service.crear_suscripcion(db, usuario_test.id, sub2_data)
    sub2.proximo_cobro = hoy - timedelta(days=5)
    sub2.fecha_creacion = fc_40
    db.commit()

    cobro_suscripcion_service.procesar_cobros_suscripciones(db)
    db.refresh(billetera_a2)
    # Cobro posterior a la foto: resta de saldo_actual
    assert billetera_a2.saldo_actual == Decimal("265000.00")
    assert billetera_a2.saldo_inicial == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a2, hoy)


# =============================================================================
# TD.3 APORTE A META
# =============================================================================
def test_td3_meta_aportes_y_eliminacion(db: Session, usuario_test: Usuario, monkeypatch):
    hoy = hoy_argentina()
    billetera_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD3")
    assert_saldo_cierra(db, billetera_a, hoy)

    meta = Meta(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        nombre="Meta Vacaciones",
        monto_objetivo=Decimal("100000.00"),
        moneda=Moneda.ARS,
        estado=EstadoMeta.ACTIVA,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(meta)
    db.commit()

    # 1. Aporte de $15.000 con fecha hoy-30
    d1 = MovimientoMetaCreate(
        monto=Decimal("15000.00"),
        tipo=TipoMovimientoMeta.APORTE,
        moneda_movimiento=Moneda.ARS,
        fecha=hoy - timedelta(days=30),
        billetera_id=billetera_a.id,
    )
    mov1 = meta_service.registrar_movimiento(db, usuario_test.id, meta.id, d1)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert billetera_a.saldo_inicial == Decimal("285000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    meta_service.eliminar_movimiento(db, usuario_test.id, meta.id, mov1.id)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert billetera_a.saldo_inicial == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    # 2. Aporte con fecha hoy+3
    d2 = MovimientoMetaCreate(
        monto=Decimal("15000.00"),
        tipo=TipoMovimientoMeta.APORTE,
        moneda_movimiento=Moneda.ARS,
        fecha=hoy + timedelta(days=3),
        billetera_id=billetera_a.id,
    )
    mov2 = meta_service.registrar_movimiento(db, usuario_test.id, meta.id, d2)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert billetera_a.saldo_inicial == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    meta_service.eliminar_movimiento(db, usuario_test.id, meta.id, mov2.id)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert billetera_a.saldo_inicial == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    # 3. Otro aporte hoy+3, reloj en hoy+3 y job
    d3 = MovimientoMetaCreate(
        monto=Decimal("15000.00"),
        tipo=TipoMovimientoMeta.APORTE,
        moneda_movimiento=Moneda.ARS,
        fecha=hoy + timedelta(days=3),
        billetera_id=billetera_a.id,
    )
    mov3 = meta_service.registrar_movimiento(db, usuario_test.id, meta.id, d3)

    fijar_hoy(monkeypatch, hoy + timedelta(days=3))
    saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("255000.00")
    assert billetera_a.saldo_inicial == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a, hoy + timedelta(days=3))

    meta_service.eliminar_movimiento(db, usuario_test.id, meta.id, mov3.id)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert billetera_a.saldo_inicial == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a, hoy + timedelta(days=3))


# =============================================================================
# TD.4 PAGO DE RESUMEN
# =============================================================================
def test_td4_pago_resumen_confirmar_ia(db: Session, usuario_test: Usuario, monkeypatch):
    hoy = hoy_argentina()
    billetera_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD4")
    tarjeta = crear_tarjeta_para_billetera(db, usuario_test.id, billetera_a.id)
    assert_saldo_cierra(db, billetera_a, hoy)

    tx = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_a.id,
        tarjeta_id=tarjeta.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("40000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        origen=OrigenTransaccion.IA_WPP,
        fecha=hoy + timedelta(days=2),
        pago_resumen_vencimiento=hoy + timedelta(days=2),
        saldo_aplicado=False,
        descripcion="Pago Resumen Tarjeta",
    )
    db.add(tx)
    db.commit()

    # Confirmado con confirmar_transaccion_ia
    transaccion_service.confirmar_transaccion_ia(db, usuario_test.id, tx.id)
    db.refresh(billetera_a)
    db.refresh(tx)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert tx.saldo_aplicado is False
    assert_saldo_cierra(db, billetera_a, hoy)

    # Reloj en hoy+1 y job
    fijar_hoy(monkeypatch, hoy + timedelta(days=1))
    saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a, hoy + timedelta(days=1))

    # Reloj en hoy+2 y job
    fijar_hoy(monkeypatch, hoy + timedelta(days=2))
    saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
    db.refresh(billetera_a)
    db.refresh(tx)
    assert billetera_a.saldo_actual == Decimal("230000.00")
    assert tx.saldo_aplicado is True
    assert_saldo_cierra(db, billetera_a, hoy + timedelta(days=2))

    # Job otra vez: no re-aplica
    saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("230000.00")
    assert_saldo_cierra(db, billetera_a, hoy + timedelta(days=2))

    # Variante: confirmado y borrado antes de hoy+2
    fijar_hoy(monkeypatch, hoy)
    billetera_a2 = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD4 var")
    tx2 = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_a2.id,
        tarjeta_id=tarjeta.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("40000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        origen=OrigenTransaccion.IA_WPP,
        fecha=hoy + timedelta(days=2),
        pago_resumen_vencimiento=hoy + timedelta(days=2),
        saldo_aplicado=False,
        descripcion="Pago Resumen Tarjeta 2",
    )
    db.add(tx2)
    db.commit()

    transaccion_service.confirmar_transaccion_ia(db, usuario_test.id, tx2.id)
    transaccion_service.eliminar_transaccion(db, usuario_test.id, tx2.id)
    db.refresh(billetera_a2)
    assert billetera_a2.saldo_actual == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a2, hoy)

    fijar_hoy(monkeypatch, hoy + timedelta(days=2))
    saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
    db.refresh(billetera_a2)
    assert billetera_a2.saldo_actual == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a2, hoy + timedelta(days=2))


# =============================================================================
# TD.5 CUOTAS Y GRUPOS
# =============================================================================
def test_td5_cuotas_y_grupos(db: Session, usuario_test: Usuario, categoria_test: Categoria):
    hoy = hoy_argentina()
    billetera_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD5 Tarjeta")
    tarjeta = crear_tarjeta_para_billetera(db, usuario_test.id, billetera_a.id)

    # Compra con tarjeta en 3 cuotas de $10.000 con cuota_inicial 2 (cuota 1 nace pagada)
    tx_tarj_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.CREDITO,
        categoria_id=categoria_test.id,
        tarjeta_id=tarjeta.id,
        billetera_id=billetera_a.id,
        fecha=hoy,
        descripcion="Compra Tarjeta 3 cuotas",
        info_cuotas={
            "cantidad_cuotas": 3,
            "monto_cuota": Decimal("10000.00"),
            "cuota_inicial": 2,
            "monto_total": Decimal("30000.00"),
        },
    )
    tx_tarj = transaccion_service.crear_transaccion(db, usuario_test.id, tx_tarj_data)
    grupo_id = tx_tarj.grupo_cuotas_id

    # Prepagar grupo desde billetera A baja exactamente $20.000,00 -> 250.000,00
    cuotas_service.prepagar_grupo(db, grupo_id, usuario_test.id, billetera_id=billetera_a.id)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("250000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    # Grupo sin tarjeta sobre otra Fixture A
    billetera_a2 = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD5 Sin Tarjeta")
    tx_deb_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        billetera_id=billetera_a2.id,
        fecha=hoy,
        descripcion="Compra Debito 3 cuotas",
        es_padre_cuotas=True,
        primer_vencimiento_manual=hoy - timedelta(days=2),
        info_cuotas={
            "cantidad_cuotas": 3,
            "monto_cuota": Decimal("10000.00"),
            "monto_total": Decimal("30000.00"),
        },
    )
    tx_deb = transaccion_service.crear_transaccion(db, usuario_test.id, tx_deb_data)
    grupo_deb_id = tx_deb.grupo_cuotas_id

    # Buscar hijas del grupo
    hijas = db.query(Transaccion).filter(Transaccion.grupo_cuotas_id == grupo_deb_id, Transaccion.es_cuota_hija.is_(True)).order_by(Transaccion.fecha.asc()).all()
    # Confirmar hija 1
    transaccion_service.confirmar_transaccion_ia(db, usuario_test.id, hijas[0].id)
    db.refresh(billetera_a2)
    assert billetera_a2.saldo_actual == Decimal("260000.00")
    assert_saldo_cierra(db, billetera_a2, hoy)

    # Actualizar grupo con monto_total_nuevo 36.000
    grupo_obj = db.query(GrupoCuotas).filter(GrupoCuotas.id == grupo_deb_id).first()
    cuotas_service.actualizar_grupo(db, grupo_obj, GrupoCuotasUpdate(monto_total_nuevo=Decimal("36000.00")))
    db.refresh(billetera_a2)
    assert billetera_a2.saldo_actual == Decimal("260000.00")
    assert_saldo_cierra(db, billetera_a2, hoy)

    hijas_act = db.query(Transaccion).filter(Transaccion.grupo_cuotas_id == grupo_deb_id, Transaccion.es_cuota_hija.is_(True)).order_by(Transaccion.fecha.asc()).all()
    assert hijas_act[0].monto == Decimal("10000.00")
    assert hijas_act[1].monto == Decimal("13000.00")
    assert hijas_act[2].monto == Decimal("13000.00")

    # Prepagar grupo: baja 26.000 -> 234.000,00
    cuotas_service.prepagar_grupo(db, grupo_deb_id, usuario_test.id, billetera_id=billetera_a2.id)
    db.refresh(billetera_a2)
    assert billetera_a2.saldo_actual == Decimal("234000.00")
    assert_saldo_cierra(db, billetera_a2, hoy)

    # Variante cancelar_grupo sobre otra Fixture A
    billetera_a3 = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD5 Cancelar")
    tx_canc_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        billetera_id=billetera_a3.id,
        fecha=hoy,
        descripcion="Compra Para Cancelar",
        es_padre_cuotas=True,
        primer_vencimiento_manual=hoy - timedelta(days=2),
        info_cuotas={
            "cantidad_cuotas": 3,
            "monto_cuota": Decimal("10000.00"),
            "monto_total": Decimal("30000.00"),
        },
    )
    tx_canc = transaccion_service.crear_transaccion(db, usuario_test.id, tx_canc_data)
    g3_id = tx_canc.grupo_cuotas_id
    hijas3 = db.query(Transaccion).filter(Transaccion.grupo_cuotas_id == g3_id, Transaccion.es_cuota_hija.is_(True)).order_by(Transaccion.fecha.asc()).all()
    transaccion_service.confirmar_transaccion_ia(db, usuario_test.id, hijas3[0].id)
    db.refresh(billetera_a3)
    assert billetera_a3.saldo_actual == Decimal("260000.00")
    assert_saldo_cierra(db, billetera_a3, hoy)

    cuotas_service.cancelar_grupo(db, g3_id, usuario_test.id)
    db.refresh(billetera_a3)
    assert billetera_a3.saldo_actual == Decimal("260000.00")
    assert_saldo_cierra(db, billetera_a3, hoy)


# =============================================================================
# TD.6 EGRESO FUTURO
# =============================================================================
def test_td6_egreso_futuro(db: Session, usuario_test: Usuario, categoria_test: Categoria, monkeypatch):
    hoy = hoy_argentina()
    billetera_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD6")

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("7000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        billetera_id=billetera_a.id,
        fecha=hoy + timedelta(days=2),
        descripcion="Egreso Futuro",
    )
    transaccion_service.crear_transaccion(db, usuario_test.id, tx_data)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    fijar_hoy(monkeypatch, hoy + timedelta(days=2))
    saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("263000.00")
    assert_saldo_cierra(db, billetera_a, hoy + timedelta(days=2))


# =============================================================================
# TD.13 LISTAR MOVIMIENTOS Y CUENTA_EN_SALDO
# =============================================================================
def test_td13_listar_movimientos(db: Session, usuario_test: Usuario, categoria_test: Categoria):
    hoy = hoy_argentina()
    billetera_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TD13")
    tarjeta = crear_tarjeta_para_billetera(db, usuario_test.id, billetera_a.id)

    # 1. Egreso $5.000 hoy-25 -> cuenta_en_saldo False
    t1 = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_a.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        origen=OrigenTransaccion.MANUAL,
        fecha=hoy - timedelta(days=25),
        descripcion="Egreso Anterior",
        fecha_creacion=datetime.now(timezone.utc),
    )
    # 2. Egreso $3.000 hoy-10 -> cuenta_en_saldo True
    t2 = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_a.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("3000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        origen=OrigenTransaccion.MANUAL,
        fecha=hoy - timedelta(days=10),
        descripcion="Egreso En Saldo",
        fecha_creacion=datetime.now(timezone.utc),
    )
    # 3. Egreso PENDIENTE $2.000 hoy-10 -> cuenta_en_saldo False
    t3 = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_a.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("2000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        fecha=hoy - timedelta(days=10),
        descripcion="Egreso Pendiente",
        fecha_creacion=datetime.now(timezone.utc),
    )
    # 4. Compra con crédito de $4.000 hoy-10 -> en modo general cuenta_en_saldo False
    t4 = Transaccion(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_a.id,
        tarjeta_id=tarjeta.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("4000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.CREDITO,
        categoria_id=categoria_test.id,
        origen=OrigenTransaccion.MANUAL,
        fecha=hoy - timedelta(days=10),
        descripcion="Compra Tarjeta",
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add_all([t1, t2, t3, t4])
    billetera_a.saldo_actual = Decimal("262000.00")
    db.commit()

    # Modo general
    res_gen = historial_service.listar_movimientos(db, usuario_test.id, modo="general")
    items_by_id = {it.id: it for it in res_gen.items}
    assert items_by_id[f"transaccion:{t1.id}"].cuenta_en_saldo is False
    assert items_by_id[f"transaccion:{t2.id}"].cuenta_en_saldo is True
    assert items_by_id[f"transaccion:{t3.id}"].cuenta_en_saldo is False
    assert items_by_id[f"transaccion:{t4.id}"].cuenta_en_saldo is False

    # Modo billetera
    res_bill = historial_service.listar_movimientos(db, usuario_test.id, modo="billetera", billetera_id=billetera_a.id)
    # En modo billetera, el saldo_despues del movimiento aplicado más nuevo es igual a saldo_actual
    movs_con_saldo = [m for m in res_bill.items if m.saldo_despues is not None]
    assert len(movs_con_saldo) > 0
    # Como la lista viene desc por fecha, el primero de movs_con_saldo es el más nuevo
    assert movs_con_saldo[0].saldo_despues == billetera_a.saldo_actual


# =============================================================================
# TD.14 D-H3: CONTROL DE SALDO Y FECHA DE LA FOTO
# =============================================================================
def test_td14_dh3_control_saldo_y_foto(db: Session, usuario_test: Usuario, categoria_test: Categoria, monkeypatch):
    f_01 = date(2026, 10, 1)
    fc_01 = datetime.combine(f_01, time(12, 0)).replace(tzinfo=timezone.utc)
    billetera_1 = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera DH3",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("310000.00"),
        saldo_inicial=Decimal("310000.00"),
        fecha_saldo=f_01,
        tiene_punto_partida=True,
        fecha_creacion=fc_01,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(billetera_1)
    db.commit()
    db.refresh(billetera_1)

    # Reloj 20/10/2026: registrar_control a 300.000,00 -> fecha_saldo_efectiva 20/10/2026
    f_20 = date(2026, 10, 20)
    fijar_hoy(monkeypatch, f_20)
    ajuste_saldo_service.registrar_control(db, usuario_test.id, billetera_1.id, Decimal("300000.00"))
    db.refresh(billetera_1)
    assert billetera_1.fecha_saldo_efectiva == f_20
    assert_saldo_cierra(db, billetera_1, f_20)

    # Reloj 22/10/2026
    f_22 = date(2026, 10, 22)
    fijar_hoy(monkeypatch, f_22)

    # Gasto de $10.000 con fecha 19/10/2026
    tx_19_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        billetera_id=billetera_1.id,
        fecha=date(2026, 10, 19),
        descripcion="Gasto 19",
    )
    tx_19 = transaccion_service.crear_transaccion(db, usuario_test.id, tx_19_data)
    db.refresh(billetera_1)
    assert billetera_1.saldo_actual == Decimal("300000.00")
    ctx = definiciones_service.cargar_contexto(db, usuario_test.id, hoy=f_22)
    assert definiciones_service.es_gasto(tx_19, ctx) is True
    assert saldo_billetera_service.movimiento_anterior_al_saldo(billetera_1, tx_19.fecha, tx_19.metodo_pago) is True
    from app.routers.whatsapp.aviso_saldo_wpp import obtener_aviso_saldo
    aviso = obtener_aviso_saldo([tx_19], db=db)
    assert aviso == f"Ojo: es anterior a tu saldo de «{billetera_1.nombre}» (20/10) y no lo modifica."
    assert_saldo_cierra(db, billetera_1, f_22)

    # Gasto de $10.000 con fecha 20/10/2026 -> 290.000,00
    tx_20_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        billetera_id=billetera_1.id,
        fecha=date(2026, 10, 20),
        descripcion="Gasto 20",
    )
    transaccion_service.crear_transaccion(db, usuario_test.id, tx_20_data)
    db.refresh(billetera_1)
    assert billetera_1.saldo_actual == Decimal("290000.00")
    assert_saldo_cierra(db, billetera_1, f_22)

    # Gasto de $10.000 con fecha 21/10/2026 -> 280.000,00
    tx_21_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.DEBITO,
        categoria_id=categoria_test.id,
        billetera_id=billetera_1.id,
        fecha=date(2026, 10, 21),
        descripcion="Gasto 21",
    )
    transaccion_service.crear_transaccion(db, usuario_test.id, tx_21_data)
    db.refresh(billetera_1)
    assert billetera_1.saldo_actual == Decimal("280000.00")
    assert_saldo_cierra(db, billetera_1, f_22)

    # previsualizar_cambio_fecha a 25/09/2026 -> 422
    with pytest.raises(HTTPException) as exc_info:
        fecha_saldo_service.previsualizar_cambio_fecha(db, usuario_test.id, billetera_1.id, date(2026, 9, 25))
    assert exc_info.value.status_code == 422
    msg_detail = exc_info.value.detail.get("detail") if isinstance(exc_info.value.detail, dict) else str(exc_info.value.detail)
    assert msg_detail == "Este saldo ya lo actualizaste el 20/10/2026: los movimientos anteriores no lo cambian."

    # Fixture B con control el 20/10/2026 a 50.000,00
    billetera_b = crear_fixture_b(db, usuario_test.id, f_20, "Billetera B DH3")
    fijar_hoy(monkeypatch, f_20)
    ajuste_saldo_service.registrar_control(db, usuario_test.id, billetera_b.id, Decimal("50000.00"))
    db.refresh(billetera_b)

    fijar_hoy(monkeypatch, f_22)
    # Gasto de $1.000 con fecha 19/10/2026 -> 50.000,00
    tx_b19_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.EFECTIVO,
        categoria_id=categoria_test.id,
        billetera_id=billetera_b.id,
        fecha=date(2026, 10, 19),
        descripcion="Gasto B 19",
    )
    transaccion_service.crear_transaccion(db, usuario_test.id, tx_b19_data)
    db.refresh(billetera_b)
    assert billetera_b.saldo_actual == Decimal("50000.00")
    assert_saldo_cierra(db, billetera_b, f_22)

    # Gasto de $1.000 con fecha 21/10/2026 -> 49.000,00
    tx_b21_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        metodo_pago=MetodoPago.EFECTIVO,
        categoria_id=categoria_test.id,
        billetera_id=billetera_b.id,
        fecha=date(2026, 10, 21),
        descripcion="Gasto B 21",
    )
    transaccion_service.crear_transaccion(db, usuario_test.id, tx_b21_data)
    db.refresh(billetera_b)
    assert billetera_b.saldo_actual == Decimal("49000.00")
    assert_saldo_cierra(db, billetera_b, f_22)

    # Otra billetera como la primera: control el 20/10/2026 a 300.000,00 y eliminar_ajuste ese mismo día
    billetera_otra = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera DH3 Otra",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("310000.00"),
        saldo_inicial=Decimal("310000.00"),
        fecha_saldo=f_01,
        tiene_punto_partida=True,
        fecha_creacion=fc_01,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(billetera_otra)
    db.commit()

    fijar_hoy(monkeypatch, f_20)
    ajuste = ajuste_saldo_service.registrar_control(db, usuario_test.id, billetera_otra.id, Decimal("300000.00"))
    db.refresh(billetera_otra)
    assert billetera_otra.saldo_actual == Decimal("300000.00")

    ajuste_saldo_service.eliminar_ajuste(db, usuario_test.id, ajuste.id)
    db.refresh(billetera_otra)
    assert billetera_otra.saldo_actual == Decimal("310000.00")
    assert billetera_otra.fecha_saldo_efectiva == f_01
    assert_saldo_cierra(db, billetera_otra, f_20)


# =============================================================================
# TD.R RENDIMIENTOS Y FECHA DE SALDO
# =============================================================================
def test_td_r_rendimientos_y_cambio_fecha(db: Session, usuario_test: Usuario, monkeypatch):
    hoy = hoy_argentina()
    billetera_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera A TDR")
    assert_saldo_cierra(db, billetera_a, hoy)

    # Rendimiento de $1.000 con fecha hoy-25
    f_25 = hoy - timedelta(days=25)
    f_25_dt = datetime.combine(f_25, time(12, 0)).replace(tzinfo=timezone.utc)
    rendimiento_billetera_service.confirmar_rendimiento(db, usuario_test.id, billetera_a.id, Decimal("1000.00"), fecha=f_25_dt)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("270000.00")
    assert billetera_a.saldo_inicial == Decimal("269000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    # previsualizar_cambio_fecha a hoy-30: saldo_actual 270.000,00, saldo_nuevo 271.000,00 y 1 movimiento
    f_30 = hoy - timedelta(days=30)
    prev = fecha_saldo_service.previsualizar_cambio_fecha(db, usuario_test.id, billetera_a.id, f_30)
    assert prev["saldo_actual"] == Decimal("270000.00")
    assert prev["saldo_nuevo"] == Decimal("271000.00")
    assert prev["cantidad_movimientos"] == 1

    # cambiar_fecha_saldo: 271.000,00 y 270.000,00
    fecha_saldo_service.cambiar_fecha_saldo(db, usuario_test.id, billetera_a.id, f_30)
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("271000.00")
    assert billetera_a.saldo_inicial == Decimal("270000.00")
    assert_saldo_cierra(db, billetera_a, hoy)

    # Rendimiento con fecha hoy+1: 400 "La fecha del rendimiento no puede ser futura." y nada cambia
    f_futura = hoy + timedelta(days=1)
    f_futura_dt = datetime.combine(f_futura, time(12, 0)).replace(tzinfo=timezone.utc)
    with pytest.raises(HTTPException) as exc_info:
        rendimiento_billetera_service.confirmar_rendimiento(
            db, usuario_test.id, billetera_a.id, Decimal("500.00"), fecha=f_futura_dt
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "La fecha del rendimiento no puede ser futura."
    db.refresh(billetera_a)
    assert billetera_a.saldo_actual == Decimal("271000.00")
    assert_saldo_cierra(db, billetera_a, hoy)
