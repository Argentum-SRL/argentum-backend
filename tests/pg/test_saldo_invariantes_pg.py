"""
tests/pg/test_saldo_invariantes_pg.py — TD.7 Simulación aleatoria con oráculo y TD.8 Concurrencia.
"""
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal
import os
import random
import threading
import time as time_mod
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.categoria import Categoria, TipoCategoria, EstadoCategoria
from app.models.subcategoria import Subcategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
from app.models.meta import Meta, EstadoMeta
from app.models.movimiento_meta import TipoMovimientoMeta
from app.models.tarjeta_credito import TarjetaCredito, RedTarjeta, EstadoTarjeta
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    EstadoVerificacionTransaccion,
)
from app.models.usuario import Usuario, Moneda, AuthProvider
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.schemas.suscripcion import SuscripcionCreate
from app.schemas.transaccion import InfoCuotas, TransaccionCreate, TransaccionUpdate
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import (
    ajuste_saldo_service,
    cobro_suscripcion_service,
    conciliacion_service,
    cuotas_service,
    fecha_saldo_service,
    meta_service,
    pago_resumen_service,
    que_es_plata_service,
    reintegro_service,
    rendimiento_billetera_service,
    saldo_billetera_service,
    saldo_programado_service,
    suscripcion_service,
    transaccion_service,
    transferencia_service,
)
from app.utils.fecha import hoy_argentina
from tests.pg.conftest import crear_fixture_a, crear_fixture_b, fijar_hoy


# =============================================================================
# TD.7 ORÁCULO Y GENERADOR
# =============================================================================
class OraculoBilletera:
    def __init__(self, saldo_inicial: Decimal, foto: date | None):
        self.saldo_esperado = Decimal(str(saldo_inicial))
        self.foto = foto
        # Registro de movimientos aplicados: id -> (fecha, impacto, es_tx_o_tr)
        self.movimientos_aplicados: dict[uuid.UUID, tuple[date, Decimal, bool]] = {}

    def registrar_control(self, declarado: Decimal, fecha: date):
        self.saldo_esperado = Decimal(str(declarado))
        if self.foto is None or fecha > self.foto:
            self.foto = fecha

    def aplicar_movimiento(self, mov_id: uuid.UUID, fecha: date, impacto: Decimal, es_tx_o_tr: bool = True):
        self.movimientos_aplicados[mov_id] = (fecha, impacto, es_tx_o_tr)
        if self.foto is None or fecha >= self.foto:
            self.saldo_esperado += impacto

    def revertir_movimiento(self, mov_id: uuid.UUID, fecha: date, impacto: Decimal):
        if mov_id in self.movimientos_aplicados:
            self.movimientos_aplicados.pop(mov_id, None)
            if self.foto is None or fecha >= self.foto:
                self.saldo_esperado -= impacto

    def mover_fecha_saldo(self, d_nueva: date, d_actual: date):
        # Suma los movimientos en [d_nueva, d_actual)
        for f, imp, _ in self.movimientos_aplicados.values():
            if d_nueva <= f < d_actual:
                self.saldo_esperado += imp
        self.foto = d_nueva


OP_TIPOS = [
    "alta_tx",
    "editar_tx",
    "borrar_tx",
    "confirmar_tx",
    "transferencia",
    "borrar_transferencia",
    "pago_resumen",
    "prepago",
    "cobro_suscripcion",
    "control_saldo",
    "rendimiento",
    "cambiar_fecha",
    "avanzar_reloj",
    "meta_aporte",
    "meta_retiro",
    "borrar_mov_meta",
    "reintegro",
    "borrar_reintegro",
    "reintegro_a_pendiente",
    "devolucion_cuotas",
    "parte_de_gasto",
    "paso_por_mi_cuenta",
    "gasto_usd_desde_pesos",
]


def test_td7_invariantes_oraculo(db: Session, monkeypatch):
    # Fecha fija: con la fecha real el test depende del día del mes (la tarjeta cierra el 20) y falla del 26 a fin de mes
    hoy_base = date(2026, 10, 12)
    num_secuencias = 200 if os.environ.get("ARGENTUM_INVARIANTES_COMPLETO") == "1" else 20
    semillas = [2026, 2027, 2028]

    for semilla in semillas:
        t0 = time_mod.time()
        rng = random.Random(semilla)
        conteo_ejecutadas: dict[str, int] = {op: 0 for op in OP_TIPOS}
        conteo_rechazadas: dict[str, int] = {op: 0 for op in OP_TIPOS}

        for sec in range(num_secuencias):
            # Usuario nuevo
            u = Usuario(
                id=uuid.uuid4(),
                email=f"sim_{semilla}_{sec}_{uuid.uuid4().hex[:6]}@example.com",
                password_hash="fake",
                nombre=f"User {semilla}_{sec}",
                moneda_principal=Moneda.ARS,
                auth_provider=AuthProvider.EMAIL,
            )
            db.add(u)
            db.commit()

            # Categorías
            cat_banco = db.execute(select(Categoria).where(func.lower(Categoria.nombre) == "banco", Categoria.tipo == TipoCategoria.EGRESO)).scalars().first()
            if not cat_banco:
                cat_banco = Categoria(id=uuid.uuid4(), nombre="Banco", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
                db.add(cat_banco)

            cat_tarjeta = db.execute(select(Categoria).where(func.lower(Categoria.nombre) == "tarjeta de crédito", Categoria.tipo == TipoCategoria.EGRESO)).scalars().first()
            if not cat_tarjeta:
                cat_tarjeta = Categoria(id=uuid.uuid4(), nombre="Tarjeta de crédito", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
                db.add(cat_tarjeta)

            cat_general = db.execute(select(Categoria).where(func.lower(Categoria.nombre) == "varios", Categoria.tipo == TipoCategoria.EGRESO)).scalars().first()
            if not cat_general:
                cat_general = Categoria(id=uuid.uuid4(), nombre="Varios", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
                db.add(cat_general)

            cat_otros_ing = db.execute(select(Categoria).where(func.lower(Categoria.nombre) == "otros", Categoria.tipo == TipoCategoria.INGRESO)).scalars().first()
            if not cat_otros_ing:
                cat_otros_ing = Categoria(id=uuid.uuid4(), nombre="Otros", tipo=TipoCategoria.INGRESO, estado=EstadoCategoria.ACTIVA)
                db.add(cat_otros_ing)
            db.flush()

            sub_cobros = db.execute(select(Subcategoria).where(Subcategoria.categoria_id == cat_otros_ing.id, func.lower(Subcategoria.nombre) == "cobros de personas")).scalars().first()
            if not sub_cobros:
                sub_cobros = Subcategoria(id=uuid.uuid4(), categoria_id=cat_otros_ing.id, nombre="Cobros de personas", orden=1)
                db.add(sub_cobros)

            sub_paso_ing = db.execute(select(Subcategoria).where(Subcategoria.categoria_id == cat_otros_ing.id, func.lower(Subcategoria.nombre) == "pasó por mi cuenta")).scalars().first()
            if not sub_paso_ing:
                sub_paso_ing = Subcategoria(id=uuid.uuid4(), categoria_id=cat_otros_ing.id, nombre="Pasó por mi cuenta", orden=3)
                db.add(sub_paso_ing)
            db.commit()

            # Billeteras A y B
            b_a = crear_fixture_a(db, u.id, hoy_base, f"A_{semilla}_{sec}")
            tarjeta = TarjetaCredito(
                id=uuid.uuid4(),
                usuario_id=u.id,
                billetera_id=b_a.id,
                nombre="Visa Sim",
                red=RedTarjeta.VISA,
                dia_cierre=20,
                dia_vencimiento=5,
                limite_credito=Decimal("1000000"),
                moneda=Moneda.ARS,
                estado=EstadoTarjeta.ACTIVA,
            )
            b_b = crear_fixture_b(db, u.id, hoy_base, f"B_{semilla}_{sec}")
            db.add(tarjeta)
            db.commit()

            oraculo = {
                b_a.id: OraculoBilletera(Decimal("270000.00"), b_a.fecha_saldo),
                b_b.id: OraculoBilletera(Decimal("0.00"), None),
            }

            reloj_hoy = hoy_base
            fijar_hoy(monkeypatch, reloj_hoy)

            # Entidades rastreadas en la secuencia
            txs_activas: list[Transaccion] = []
            transfs_activas: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, date, Decimal]] = []  # (id, orig_id, dest_id, fecha, monto)
            metas_activas: list[Meta] = []
            movs_meta: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, date, Decimal]] = []  # (meta_id, mov_id, bill_id, fecha, impacto)
            movs_meta_futuros: list[tuple[uuid.UUID, uuid.UUID, date, Decimal]] = []  # (mov_m_id, b_id, fecha, impacto)
            reintegros_activos: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, date, Decimal]] = {}  # compra_id -> (espejo_id, billetera_id, fecha_recibido, monto_reintegro)
            compras_con_parte: dict[uuid.UUID, uuid.UUID] = {}  # compra_id -> parte_tx_id
            filas_parte: set[uuid.UUID] = set()

            for op_num in range(30):
                op_tipo = rng.choice(OP_TIPOS)
                snapshot_oraculo = {
                    k: (v.saldo_esperado, v.foto, dict(v.movimientos_aplicados)) for k, v in oraculo.items()
                }
                snapshot_movs_meta_futuros = list(movs_meta_futuros)
                snapshot_reintegros = dict(reintegros_activos)
                snapshot_compras_con_parte = dict(compras_con_parte)
                snapshot_filas_parte = set(filas_parte)
                snapshot_transfs = list(transfs_activas)
                snapshot_movs_meta = list(movs_meta)
                snapshot_txs = list(txs_activas)

                try:
                    if op_tipo == "alta_tx":
                        b_target = rng.choice([b_a, b_b])
                        dias_offset = rng.randint(-40, 5)
                        f_tx = reloj_hoy + timedelta(days=dias_offset)
                        es_ingreso = rng.choice([True, False])
                        es_credito = False if (es_ingreso or b_target.id != b_a.id) else rng.choice([True, False])
                        es_pend = rng.choice([True, False])
                        monto = Decimal(str(rng.randint(100, 5000)))

                        data = TransaccionCreate(
                            tipo=TipoTransaccion.INGRESO if es_ingreso else TipoTransaccion.EGRESO,
                            monto=monto,
                            moneda=Moneda.ARS,
                            metodo_pago=MetodoPago.CREDITO if es_credito else MetodoPago.DEBITO,
                            categoria_id=cat_tarjeta.id if es_credito else cat_general.id,
                            billetera_id=b_target.id,
                            tarjeta_id=tarjeta.id if es_credito else None,
                            fecha=f_tx,
                            estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE if es_pend else None,
                            descripcion=f"Op {op_num}",
                        )
                        tx = transaccion_service.crear_transaccion(db, u.id, data)
                        txs_activas.append(tx)

                        if tx.metodo_pago != MetodoPago.CREDITO and tx.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE and f_tx <= reloj_hoy:
                            imp = monto if es_ingreso else -monto
                            oraculo[b_target.id].aplicar_movimiento(tx.id, f_tx, imp, es_tx_o_tr=True)

                    elif op_tipo == "editar_tx":
                        candidatos = [
                            t for t in txs_activas
                            if not t.es_cuota_hija
                            and not t.es_padre_cuotas
                            and t.metodo_pago != MetodoPago.CREDITO
                            and not t.movimiento_meta_id
                            and not t.reintegro_de_id
                            and not t.pago_resumen_vencimiento
                            and t.id not in reintegros_activos
                            and t.id not in filas_parte
                            and t.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE
                            and t.fecha <= reloj_hoy
                        ]
                        if not candidatos:
                            f_tx = reloj_hoy - timedelta(days=rng.randint(0, 10))
                            tx_creada = transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("1500.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.DEBITO,
                                    categoria_id=cat_general.id,
                                    billetera_id=b_a.id,
                                    fecha=f_tx,
                                    descripcion="Tx para editar",
                                )
                            )
                            txs_activas.append(tx_creada)
                            oraculo[b_a.id].aplicar_movimiento(tx_creada.id, f_tx, Decimal("-1500.00"), es_tx_o_tr=True)
                            tx_ed = tx_creada
                        else:
                            tx_ed = rng.choice(candidatos)

                        old_bid = tx_ed.billetera_id
                        old_fecha = tx_ed.fecha
                        old_imp = tx_ed.monto if tx_ed.tipo == TipoTransaccion.INGRESO else -tx_ed.monto
                        was_applied = (tx_ed.id in oraculo[old_bid].movimientos_aplicados)

                        new_monto = Decimal(str(rng.randint(100, 3000)))
                        new_fecha = reloj_hoy - timedelta(days=rng.randint(0, 40))
                        new_b = rng.choice([b_a, b_b])

                        transaccion_service.actualizar_transaccion(
                            db, u.id, tx_ed.id,
                            TransaccionUpdate(monto=new_monto, fecha=new_fecha, billetera_id=new_b.id)
                        )

                        if was_applied:
                            oraculo[old_bid].revertir_movimiento(tx_ed.id, old_fecha, old_imp)

                        new_imp = new_monto if tx_ed.tipo == TipoTransaccion.INGRESO else -new_monto
                        oraculo[new_b.id].aplicar_movimiento(tx_ed.id, new_fecha, new_imp, es_tx_o_tr=True)

                        tx_ed.monto = new_monto
                        tx_ed.fecha = new_fecha
                        tx_ed.billetera_id = new_b.id

                    elif op_tipo == "borrar_tx":
                        if not txs_activas:
                            f_tx = reloj_hoy - timedelta(days=5)
                            tx_temp = transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("500.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.DEBITO,
                                    categoria_id=cat_general.id,
                                    billetera_id=b_a.id,
                                    fecha=f_tx,
                                    descripcion="Tx temporal",
                                )
                            )
                            txs_activas.append(tx_temp)
                            oraculo[b_a.id].aplicar_movimiento(tx_temp.id, f_tx, Decimal("-500.00"))

                        tx_b = txs_activas.pop(rng.randint(0, len(txs_activas) - 1))
                        # Si tenía reintegro, el oráculo deshace el espejo también
                        if tx_b.id in reintegros_activos:
                            esp_id, esp_bid, esp_f, esp_m = reintegros_activos.pop(tx_b.id)
                            oraculo[esp_bid].revertir_movimiento(esp_id, esp_f, esp_m)

                        if tx_b.id in compras_con_parte:
                            compras_con_parte.pop(tx_b.id)

                        f_tx = tx_b.fecha
                        if tx_b.id in filas_parte:
                            filas_parte.remove(tx_b.id)
                            imp = Decimal("1000.00")
                        else:
                            imp = tx_b.monto if tx_b.tipo == TipoTransaccion.INGRESO else -tx_b.monto
                        b_id = tx_b.billetera_id
                        transaccion_service.eliminar_transaccion(db, u.id, tx_b.id)
                        if b_id in oraculo:
                            oraculo[b_id].revertir_movimiento(tx_b.id, f_tx, imp)

                    elif op_tipo == "confirmar_tx":
                        pendientes = [
                            t for t in txs_activas
                            if t.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
                            and t.id not in filas_parte
                        ]
                        if not pendientes:
                            f_tx = reloj_hoy - timedelta(days=2)
                            tx_pend = transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("800.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.DEBITO,
                                    categoria_id=cat_general.id,
                                    billetera_id=b_a.id,
                                    fecha=f_tx,
                                    estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
                                    descripcion="Pendiente para confirmar",
                                )
                            )
                            txs_activas.append(tx_pend)
                            pendientes = [tx_pend]

                        tx_c = rng.choice(pendientes)
                        transaccion_service.confirmar_transaccion_ia(db, u.id, tx_c.id)
                        tx_c.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
                        if tx_c.fecha <= reloj_hoy and tx_c.metodo_pago != MetodoPago.CREDITO:
                            tx_c.saldo_aplicado = True
                            imp = tx_c.monto if tx_c.tipo == TipoTransaccion.INGRESO else -tx_c.monto
                            oraculo[tx_c.billetera_id].aplicar_movimiento(tx_c.id, tx_c.fecha, imp)

                    elif op_tipo == "transferencia":
                        dias_tr = rng.randint(-40, 0)
                        f_tr = reloj_hoy + timedelta(days=dias_tr)
                        m_tr = Decimal(str(rng.randint(50, 1000)))
                        orig, dest = (b_a, b_b) if rng.choice([True, False]) else (b_b, b_a)
                        tr_data = TransferenciaInternaCreate(
                            billetera_origen_id=orig.id,
                            billetera_destino_id=dest.id,
                            monto=m_tr,
                            moneda=Moneda.ARS,
                            fecha=f_tr,
                        )
                        tr_res = transferencia_service.crear_transferencia(db, u.id, tr_data)
                        transfs_activas.append((tr_res.id, orig.id, dest.id, f_tr, m_tr))
                        oraculo[orig.id].aplicar_movimiento(tr_res.id, f_tr, -m_tr)
                        oraculo[dest.id].aplicar_movimiento(tr_res.id, f_tr, m_tr)

                    elif op_tipo == "borrar_transferencia":
                        if not transfs_activas:
                            f_tr = reloj_hoy - timedelta(days=3)
                            tr_temp = transferencia_service.crear_transferencia(
                                db, u.id,
                                TransferenciaInternaCreate(
                                    billetera_origen_id=b_a.id,
                                    billetera_destino_id=b_b.id,
                                    monto=Decimal("150.00"),
                                    moneda=Moneda.ARS,
                                    fecha=f_tr,
                                )
                            )
                            transfs_activas.append((tr_temp.id, b_a.id, b_b.id, f_tr, Decimal("150.00")))
                            oraculo[b_a.id].aplicar_movimiento(tr_temp.id, f_tr, Decimal("-150.00"))
                            oraculo[b_b.id].aplicar_movimiento(tr_temp.id, f_tr, Decimal("150.00"))

                        tr_id, o_id, d_id, f_tr, m_tr = transfs_activas.pop(rng.randint(0, len(transfs_activas) - 1))
                        transferencia_service.eliminar_transferencia(db, u.id, tr_id)
                        oraculo[o_id].revertir_movimiento(tr_id, f_tr, -m_tr)
                        oraculo[d_id].revertir_movimiento(tr_id, f_tr, m_tr)

                    elif op_tipo == "pago_resumen":
                        # Comprobar si hay compras crédito con cuotas sin pagar
                        cuotas_unpaid = db.execute(
                            select(Cuota).join(GrupoCuotas).where(
                                GrupoCuotas.tarjeta_id == tarjeta.id,
                                Cuota.pagada == False,
                            )
                        ).scalars().all()
                        if not cuotas_unpaid:
                            # Crear compra crédito
                            transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("2000.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.CREDITO,
                                    categoria_id=cat_tarjeta.id,
                                    billetera_id=b_a.id,
                                    tarjeta_id=tarjeta.id,
                                    fecha=reloj_hoy - timedelta(days=5),
                                    descripcion="Compra credito para pagar resumen",
                                )
                            )

                        f_pago = reloj_hoy + timedelta(days=rng.randint(0, 3))
                        tx_pago = pago_resumen_service.pagar_resumen_tarjeta(
                            db, u.id, tarjeta.id,
                            fecha_pago=f_pago,
                            billetera_id=b_a.id,
                        )
                        if f_pago <= reloj_hoy:
                            oraculo[b_a.id].aplicar_movimiento(tx_pago.id, f_pago, -tx_pago.monto)
                        txs_activas.append(tx_pago)

                    elif op_tipo == "prepago":
                        # Si hay un grupo de cuotas activo de la tarjeta de A
                        grupo = db.execute(
                            select(GrupoCuotas).join(Cuota).where(
                                GrupoCuotas.tarjeta_id == tarjeta.id,
                                GrupoCuotas.estado == EstadoGrupoCuotas.ACTIVO,
                                Cuota.pagada == False,
                            )
                        ).scalars().first()
                        if not grupo:
                            transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("3000.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.CREDITO,
                                    categoria_id=cat_tarjeta.id,
                                    billetera_id=b_a.id,
                                    tarjeta_id=tarjeta.id,
                                    fecha=reloj_hoy - timedelta(days=5),
                                    descripcion="Compra cuotas prepago sim",
                                    info_cuotas=InfoCuotas(cantidad_cuotas=3, cuota_inicial=1, tiene_interes=False, monto_total=Decimal("3000.00")),
                                )
                            )
                            grupo = db.execute(
                                select(GrupoCuotas).where(
                                    GrupoCuotas.tarjeta_id == tarjeta.id,
                                    GrupoCuotas.estado == EstadoGrupoCuotas.ACTIVO,
                                )
                            ).scalars().first()

                        # Suma de monto_proyectado de cuotas sin pagar antes del prepago
                        pendientes = db.execute(
                            select(Cuota).where(Cuota.grupo_id == grupo.id, Cuota.pagada == False)
                        ).scalars().all()
                        monto_prepago = sum(c.monto_proyectado for c in pendientes)

                        res_prepago = cuotas_service.prepagar_grupo(db, grupo.id, u.id, b_a.id)
                        tx_prepago = res_prepago[0] if isinstance(res_prepago, tuple) else res_prepago
                        tx_id_prep = getattr(tx_prepago, "id", uuid.uuid4())
                        oraculo[b_a.id].aplicar_movimiento(tx_id_prep, reloj_hoy, -monto_prepago)

                    elif op_tipo == "cobro_suscripcion":
                        b_sub = rng.choice([b_a, b_b])
                        m_sub = Decimal(str(rng.randint(300, 2000)))
                        sub = suscripcion_service.crear_suscripcion(
                            db, u.id,
                            SuscripcionCreate(
                                nombre=f"Sub {op_num}_{rng.randint(100, 999)}",
                                monto=m_sub,
                                moneda="ARS",
                                frecuencia="mensual",
                                proximo_cobro=reloj_hoy + timedelta(days=30),
                                billetera_id=b_sub.id,
                                categoria_id=cat_general.id,
                            )
                        )
                        k = rng.randint(0, 25)
                        f_cobro = reloj_hoy - timedelta(days=k)
                        sub.proximo_cobro = f_cobro
                        sub.fecha_creacion = datetime.combine(reloj_hoy - timedelta(days=40), time(12, 0)).replace(tzinfo=timezone.utc)
                        db.commit()

                        cobro_suscripcion_service.procesar_cobros_suscripciones(db)
                        tx_cobro = db.execute(
                            select(Transaccion).where(
                                Transaccion.suscripcion_id == sub.id,
                                Transaccion.fecha == f_cobro,
                            )
                        ).scalars().first()
                        if tx_cobro:
                            oraculo[b_sub.id].aplicar_movimiento(tx_cobro.id, f_cobro, -m_sub)
                            txs_activas.append(tx_cobro)

                    elif op_tipo == "control_saldo":
                        b_ctrl = rng.choice([b_a, b_b])
                        decl = Decimal(str(rng.randint(10000, 300000)))
                        ajuste_saldo_service.registrar_control(db, u.id, b_ctrl.id, decl)
                        oraculo[b_ctrl.id].registrar_control(decl, reloj_hoy)

                    elif op_tipo == "rendimiento":
                        b_rend = rng.choice([b_a, b_b])
                        dias_r = rng.randint(-40, 0)
                        f_r = reloj_hoy + timedelta(days=dias_r)
                        f_r_dt = datetime.combine(f_r, time(12, 0)).replace(tzinfo=timezone.utc)
                        m_r = Decimal(str(rng.randint(10, 500)))
                        rendimiento_billetera_service.confirmar_rendimiento(db, u.id, b_rend.id, m_r, fecha=f_r_dt)
                        r_key = uuid.uuid4()
                        oraculo[b_rend.id].aplicar_movimiento(r_key, f_r, m_r, es_tx_o_tr=False)

                    elif op_tipo == "cambiar_fecha":
                        b_ch = b_a
                        d_actual = saldo_billetera_service.fecha_creacion_billetera(b_ch)
                        d_nueva = d_actual - timedelta(days=rng.randint(1, 10))
                        fecha_saldo_service.cambiar_fecha_saldo(db, u.id, b_ch.id, d_nueva)
                        oraculo[b_ch.id].mover_fecha_saldo(d_nueva, d_actual)

                    elif op_tipo == "avanzar_reloj":
                        reloj_hoy += timedelta(days=1)
                        fijar_hoy(monkeypatch, reloj_hoy)
                        saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
                        for t in txs_activas:
                            if (
                                t.id not in oraculo[t.billetera_id].movimientos_aplicados
                                and t.fecha <= reloj_hoy
                                and t.metodo_pago != MetodoPago.CREDITO
                                and not t.es_padre_cuotas
                                and not t.es_cuota_hija
                                and t.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE
                            ):
                                imp = t.monto if t.tipo == TipoTransaccion.INGRESO else -t.monto
                                oraculo[t.billetera_id].aplicar_movimiento(t.id, t.fecha, imp, es_tx_o_tr=True)
                        for item in list(movs_meta_futuros):
                            mov_id, b_id, d_ap, imp_ap = item
                            if d_ap <= reloj_hoy:
                                if mov_id not in oraculo[b_id].movimientos_aplicados:
                                    oraculo[b_id].aplicar_movimiento(mov_id, d_ap, imp_ap, es_tx_o_tr=True)
                                movs_meta_futuros.remove(item)

                    elif op_tipo == "meta_aporte":
                        if not metas_activas:
                            meta = Meta(
                                id=uuid.uuid4(),
                                usuario_id=u.id,
                                nombre=f"Meta {len(metas_activas)}",
                                monto_objetivo=Decimal("50000"),
                                moneda=Moneda.ARS,
                                estado=EstadoMeta.ACTIVA,
                            )
                            db.add(meta)
                            db.commit()
                            metas_activas.append(meta)

                        meta = rng.choice(metas_activas)
                        d_ap = reloj_hoy + timedelta(days=rng.randint(-40, 5))
                        m_ap = Decimal(str(rng.randint(100, 2000)))
                        mov_m = meta_service.registrar_movimiento(
                            db, u.id, meta.id,
                            MovimientoMetaCreate(
                                monto=m_ap,
                                tipo=TipoMovimientoMeta.APORTE,
                                moneda_movimiento=Moneda.ARS,
                                fecha=d_ap,
                                billetera_id=b_a.id,
                            )
                        )
                        movs_meta.append((meta.id, mov_m.id, b_a.id, d_ap, -m_ap))
                        if d_ap <= reloj_hoy:
                            oraculo[b_a.id].aplicar_movimiento(mov_m.id, d_ap, -m_ap, es_tx_o_tr=True)
                        else:
                            movs_meta_futuros.append((mov_m.id, b_a.id, d_ap, -m_ap))

                    elif op_tipo == "meta_retiro":
                        if not metas_activas:
                            meta = Meta(
                                id=uuid.uuid4(),
                                usuario_id=u.id,
                                nombre=f"Meta {len(metas_activas)}",
                                monto_objetivo=Decimal("50000"),
                                moneda=Moneda.ARS,
                                estado=EstadoMeta.ACTIVA,
                            )
                            db.add(meta)
                            db.commit()
                            metas_activas.append(meta)

                        meta = rng.choice(metas_activas)
                        db.refresh(meta)
                        if meta.monto_actual < Decimal("100.00"):
                            d_ap = reloj_hoy - timedelta(days=10)
                            mov_ap = meta_service.registrar_movimiento(
                                db, u.id, meta.id,
                                MovimientoMetaCreate(
                                    monto=Decimal("1500.00"),
                                    tipo=TipoMovimientoMeta.APORTE,
                                    moneda_movimiento=Moneda.ARS,
                                    fecha=d_ap,
                                    billetera_id=b_a.id,
                                )
                            )
                            movs_meta.append((meta.id, mov_ap.id, b_a.id, d_ap, Decimal("-1500.00")))
                            oraculo[b_a.id].aplicar_movimiento(mov_ap.id, d_ap, Decimal("-1500.00"), es_tx_o_tr=True)
                            db.refresh(meta)

                        max_ret = max(10, int(meta.monto_actual))
                        m_ret = Decimal(str(rng.randint(10, max_ret)))
                        d_ret = reloj_hoy - timedelta(days=rng.randint(0, 40))
                        mov_ret = meta_service.registrar_movimiento(
                            db, u.id, meta.id,
                            MovimientoMetaCreate(
                                monto=m_ret,
                                tipo=TipoMovimientoMeta.RETIRO,
                                moneda_movimiento=Moneda.ARS,
                                fecha=d_ret,
                                billetera_id=b_a.id,
                            )
                        )
                        movs_meta.append((meta.id, mov_ret.id, b_a.id, d_ret, m_ret))
                        oraculo[b_a.id].aplicar_movimiento(mov_ret.id, d_ret, m_ret, es_tx_o_tr=True)

                    elif op_tipo == "borrar_mov_meta":
                        if not movs_meta:
                            if not metas_activas:
                                meta = Meta(
                                    id=uuid.uuid4(),
                                    usuario_id=u.id,
                                    nombre=f"Meta {len(metas_activas)}",
                                    monto_objetivo=Decimal("50000"),
                                    moneda=Moneda.ARS,
                                    estado=EstadoMeta.ACTIVA,
                                )
                                db.add(meta)
                                db.commit()
                                metas_activas.append(meta)
                            meta = rng.choice(metas_activas)
                            d_ap = reloj_hoy - timedelta(days=5)
                            mov_ap = meta_service.registrar_movimiento(
                                db, u.id, meta.id,
                                MovimientoMetaCreate(
                                    monto=Decimal("500.00"),
                                    tipo=TipoMovimientoMeta.APORTE,
                                    moneda_movimiento=Moneda.ARS,
                                    fecha=d_ap,
                                    billetera_id=b_a.id,
                                )
                            )
                            movs_meta.append((meta.id, mov_ap.id, b_a.id, d_ap, Decimal("-500.00")))
                            oraculo[b_a.id].aplicar_movimiento(mov_ap.id, d_ap, Decimal("-500.00"), es_tx_o_tr=True)

                        m_id, mov_id, b_id, d_mov, imp_mov = movs_meta.pop(rng.randint(0, len(movs_meta) - 1))
                        movs_meta_futuros = [x for x in movs_meta_futuros if x[0] != mov_id]
                        meta_service.eliminar_movimiento(db, u.id, m_id, mov_id)
                        oraculo[b_id].revertir_movimiento(mov_id, d_mov, imp_mov)

                    elif op_tipo == "reintegro":
                        candidatos = [
                            t for t in txs_activas
                            if t.tipo == TipoTransaccion.EGRESO
                            and t.metodo_pago == MetodoPago.DEBITO
                            and not t.es_cuota_hija
                            and not t.es_padre_cuotas
                            and not t.movimiento_meta_id
                            and not t.reintegro_de_id
                            and not t.pago_resumen_vencimiento
                            and t.id not in reintegros_activos
                            and t.id not in compras_con_parte
                            and t.id not in filas_parte
                            and t.fecha <= reloj_hoy
                        ]
                        if not candidatos:
                            f_c = reloj_hoy - timedelta(days=rng.randint(1, 10))
                            tx_c = transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("2000.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.DEBITO,
                                    categoria_id=cat_general.id,
                                    billetera_id=b_a.id,
                                    fecha=f_c,
                                    descripcion="Compra para reintegro",
                                )
                            )
                            txs_activas.append(tx_c)
                            oraculo[b_a.id].aplicar_movimiento(tx_c.id, f_c, Decimal("-2000.00"))
                            compra = tx_c
                        else:
                            compra = rng.choice(candidatos)

                        m_reint = Decimal(str(rng.randint(1, max(1, int(compra.monto)))))
                        dias_diff = max(0, (reloj_hoy - compra.fecha).days)
                        f_reint = compra.fecha + timedelta(days=rng.randint(0, dias_diff))
                        reintegro_service.registrar_o_actualizar_reintegro(
                            db, u.id, compra.id,
                            monto=m_reint,
                            recibido=True,
                            fecha_recibido=f_reint,
                        )
                        espejo = db.execute(
                            select(Transaccion).where(
                                Transaccion.reintegro_de_id == compra.id,
                                Transaccion.tipo_espejo == "reintegro",
                            )
                        ).scalars().first()
                        if espejo:
                            oraculo[compra.billetera_id].aplicar_movimiento(espejo.id, f_reint, m_reint)
                            reintegros_activos[compra.id] = (espejo.id, compra.billetera_id, f_reint, m_reint)

                    elif op_tipo == "borrar_reintegro":
                        if not reintegros_activos:
                            f_c = reloj_hoy - timedelta(days=5)
                            tx_c = transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("1000.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.DEBITO,
                                    categoria_id=cat_general.id,
                                    billetera_id=b_a.id,
                                    fecha=f_c,
                                    descripcion="Compra para borrar reintegro",
                                )
                            )
                            txs_activas.append(tx_c)
                            oraculo[b_a.id].aplicar_movimiento(tx_c.id, f_c, Decimal("-1000.00"))
                            reintegro_service.registrar_o_actualizar_reintegro(
                                db, u.id, tx_c.id,
                                monto=Decimal("200.00"),
                                recibido=True,
                                fecha_recibido=f_c,
                            )
                            espejo = db.execute(
                                select(Transaccion).where(
                                    Transaccion.reintegro_de_id == tx_c.id,
                                    Transaccion.tipo_espejo == "reintegro",
                                )
                            ).scalars().first()
                            oraculo[b_a.id].aplicar_movimiento(espejo.id, f_c, Decimal("200.00"))
                            reintegros_activos[tx_c.id] = (espejo.id, b_a.id, f_c, Decimal("200.00"))

                        c_id = rng.choice(list(reintegros_activos.keys()))
                        esp_id, esp_bid, esp_f, esp_m = reintegros_activos.pop(c_id)
                        reintegro_service.eliminar_reintegro(db, u.id, c_id)
                        oraculo[esp_bid].revertir_movimiento(esp_id, esp_f, esp_m)

                    elif op_tipo == "reintegro_a_pendiente":
                        if not reintegros_activos:
                            f_c = reloj_hoy - timedelta(days=5)
                            tx_c = transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("1000.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.DEBITO,
                                    categoria_id=cat_general.id,
                                    billetera_id=b_a.id,
                                    fecha=f_c,
                                    descripcion="Compra para reintegro a pendiente",
                                )
                            )
                            txs_activas.append(tx_c)
                            oraculo[b_a.id].aplicar_movimiento(tx_c.id, f_c, Decimal("-1000.00"))
                            reintegro_service.registrar_o_actualizar_reintegro(
                                db, u.id, tx_c.id,
                                monto=Decimal("200.00"),
                                recibido=True,
                                fecha_recibido=f_c,
                            )
                            espejo = db.execute(
                                select(Transaccion).where(
                                    Transaccion.reintegro_de_id == tx_c.id,
                                    Transaccion.tipo_espejo == "reintegro",
                                )
                            ).scalars().first()
                            oraculo[b_a.id].aplicar_movimiento(espejo.id, f_c, Decimal("200.00"))
                            reintegros_activos[tx_c.id] = (espejo.id, b_a.id, f_c, Decimal("200.00"))

                        c_id = rng.choice(list(reintegros_activos.keys()))
                        esp_id, esp_bid, esp_f, esp_m = reintegros_activos.pop(c_id)
                        reintegro_service.registrar_o_actualizar_reintegro(
                            db, u.id, c_id, monto=esp_m, recibido=False
                        )
                        oraculo[esp_bid].revertir_movimiento(esp_id, esp_f, esp_m)

                    elif op_tipo == "devolucion_cuotas":
                        # compra con la tarjeta de 3.000 en 3 cuotas con fecha entre reloj_hoy − 10 y reloj_hoy,
                        # y devolución de 3.000 recibida en reloj_hoy (crédito: el oráculo no cambia).
                        dias_c = rng.randint(0, 10)
                        f_c = reloj_hoy - timedelta(days=dias_c)
                        tx_compra_cuotas = transaccion_service.crear_transaccion(
                            db, u.id,
                            TransaccionCreate(
                                tipo=TipoTransaccion.EGRESO,
                                monto=Decimal("3000.00"),
                                moneda=Moneda.ARS,
                                metodo_pago=MetodoPago.CREDITO,
                                categoria_id=cat_tarjeta.id,
                                billetera_id=b_a.id,
                                tarjeta_id=tarjeta.id,
                                fecha=f_c,
                                descripcion="Compra tarjeta 3 cuotas",
                                info_cuotas=InfoCuotas(cantidad_cuotas=3, monto_total=Decimal("3000.00")),
                            )
                        )
                        txs_activas.append(tx_compra_cuotas)
                        reintegro_service.registrar_o_actualizar_reintegro(
                            db, u.id, tx_compra_cuotas.id,
                            monto=Decimal("3000.00"),
                            recibido=True,
                            fecha_recibido=reloj_hoy,
                        )

                    elif op_tipo == "parte_de_gasto":
                        # si no hay compra candidata, crea un egreso de 2.000 en débito en A con fecha reloj_hoy − 1 (oráculo −2.000);
                        # crea un ingreso de 1.000 por transferencia en A en reloj_hoy en Cobros de personas (oráculo +1.000)
                        # y lo pasa a parte de esa compra (el oráculo no cambia; anotá la parte para que borrar_tx de esa compra no la revierta;
                        # la fila pasada a parte sale de los candidatos de editar_tx y confirmar_tx, y si borrar_tx la elige a ella el oráculo revierte su +1.000).
                        candidatos_p = [
                            t for t in txs_activas
                            if t.billetera_id == b_a.id
                            and t.tipo == TipoTransaccion.EGRESO
                            and t.metodo_pago == MetodoPago.DEBITO
                            and t.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
                            and not t.es_cuota_hija
                            and not t.es_padre_cuotas
                            and not t.movimiento_meta_id
                            and not t.reintegro_de_id
                            and not t.pago_resumen_vencimiento
                            and t.categoria_id == cat_general.id
                            and t.id not in reintegros_activos
                            and t.id not in compras_con_parte
                            and t.id not in filas_parte
                            and t.fecha <= reloj_hoy
                            and t.monto >= Decimal("1000.00")
                        ]
                        if not candidatos_p:
                            f_c = reloj_hoy - timedelta(days=1)
                            compra_p = transaccion_service.crear_transaccion(
                                db, u.id,
                                TransaccionCreate(
                                    tipo=TipoTransaccion.EGRESO,
                                    monto=Decimal("2000.00"),
                                    moneda=Moneda.ARS,
                                    metodo_pago=MetodoPago.DEBITO,
                                    categoria_id=cat_general.id,
                                    billetera_id=b_a.id,
                                    fecha=f_c,
                                    descripcion="Compra para parte",
                                )
                            )
                            txs_activas.append(compra_p)
                            oraculo[b_a.id].aplicar_movimiento(compra_p.id, f_c, Decimal("-2000.00"), es_tx_o_tr=True)
                        else:
                            compra_p = rng.choice(candidatos_p)

                        ingreso_p = transaccion_service.crear_transaccion(
                            db, u.id,
                            TransaccionCreate(
                                tipo=TipoTransaccion.INGRESO,
                                monto=Decimal("1000.00"),
                                moneda=Moneda.ARS,
                                metodo_pago=MetodoPago.TRANSFERENCIA,
                                categoria_id=cat_otros_ing.id,
                                subcategoria_id=sub_cobros.id,
                                billetera_id=b_a.id,
                                fecha=reloj_hoy,
                                descripcion="Ingreso para pasar a parte",
                            )
                        )
                        txs_activas.append(ingreso_p)
                        oraculo[b_a.id].aplicar_movimiento(ingreso_p.id, reloj_hoy, Decimal("1000.00"), es_tx_o_tr=True)

                        que_es_plata_service.responder(
                            db, u.id, ingreso_p.id, opcion="parte_de_gasto", gasto_id=compra_p.id
                        )
                        compras_con_parte[compra_p.id] = ingreso_p.id
                        filas_parte.add(ingreso_p.id)

                    elif op_tipo == "paso_por_mi_cuenta":
                        # ingreso de 700 en A en reloj_hoy en Pasó por mi cuenta (oráculo +700).
                        tx_paso = transaccion_service.crear_transaccion(
                            db, u.id,
                            TransaccionCreate(
                                tipo=TipoTransaccion.INGRESO,
                                monto=Decimal("700.00"),
                                moneda=Moneda.ARS,
                                metodo_pago=MetodoPago.TRANSFERENCIA,
                                categoria_id=cat_otros_ing.id,
                                subcategoria_id=sub_paso_ing.id,
                                billetera_id=b_a.id,
                                fecha=reloj_hoy,
                                descripcion="Paso por mi cuenta",
                            )
                        )
                        txs_activas.append(tx_paso)
                        oraculo[b_a.id].aplicar_movimiento(tx_paso.id, reloj_hoy, Decimal("700.00"), es_tx_o_tr=True)

                    elif op_tipo == "gasto_usd_desde_pesos":
                        tx_usd = transaccion_service.crear_transaccion(
                            db, u.id,
                            TransaccionCreate(
                                tipo=TipoTransaccion.EGRESO,
                                monto=Decimal("1300.00"),
                                moneda=Moneda.ARS,
                                metodo_pago=MetodoPago.DEBITO,
                                categoria_id=cat_general.id,
                                billetera_id=b_a.id,
                                fecha=reloj_hoy,
                                descripcion="Gasto USD desde pesos",
                                moneda_original=Moneda.USD,
                                monto_original=Decimal("1.00"),
                            )
                        )
                        txs_activas.append(tx_usd)
                        oraculo[b_a.id].aplicar_movimiento(tx_usd.id, reloj_hoy, Decimal("-1300.00"), es_tx_o_tr=True)

                    db.commit()

                except HTTPException as e:
                    if e.status_code in (400, 404, 409, 422):
                        conteo_rechazadas[op_tipo] += 1
                        db.rollback()
                        for k, (s_prev, f_prev, m_prev) in snapshot_oraculo.items():
                            oraculo[k].saldo_esperado = s_prev
                            oraculo[k].foto = f_prev
                            oraculo[k].movimientos_aplicados = dict(m_prev)
                        movs_meta_futuros = list(snapshot_movs_meta_futuros)
                        reintegros_activos = dict(snapshot_reintegros)
                        compras_con_parte = dict(snapshot_compras_con_parte)
                        filas_parte = set(snapshot_filas_parte)
                        transfs_activas = list(snapshot_transfs)
                        movs_meta = list(snapshot_movs_meta)
                        txs_activas = list(snapshot_txs)
                    else:
                        import traceback
                        print(f"\n[FALLO INESPERADO HTTP {e.status_code}] Semilla: {semilla}, Sec: {sec}, Op: {op_num} ({op_tipo})\n")
                        traceback.print_exc()
                        raise
                except Exception as e:
                    import traceback
                    print(f"\n[FALLO INESPERADO] Semilla: {semilla}, Sec: {sec}, Op: {op_num} ({op_tipo}): {e}\n")
                    traceback.print_exc()
                    raise
                else:
                    conteo_ejecutadas[op_tipo] += 1

                # Comprobaciones después de cada operación en cada billetera:
                # (1) saldo_actual == calcular_saldo_teorico
                # (2) saldo_actual == oráculo
                for b_chk in [b_a, b_b]:
                    db.refresh(b_chk)
                    teorico = conciliacion_service.calcular_saldo_teorico(db, b_chk.id, hasta=reloj_hoy)
                    esperado = oraculo[b_chk.id].saldo_esperado

                    if b_chk.saldo_actual != teorico or b_chk.saldo_actual != esperado:
                        print(
                            f"\n[DIFERENCIA] Semilla: {semilla}, Secuencia: {sec}, Op: {op_num} ({op_tipo})\n"
                            f"Billetera: {b_chk.nombre}\n"
                            f"Saldo actual:   {b_chk.saldo_actual}\n"
                            f"Saldo teorico:  {teorico}\n"
                            f"Saldo oraculo:  {esperado}\n"
                            f"Foto oraculo:   {oraculo[b_chk.id].foto}\n"
                            f"Movimientos oraculo:\n"
                        )
                        for mid, (f, imp, flag) in oraculo[b_chk.id].movimientos_aplicados.items():
                            print(f"  mov {mid}: fecha={f}, imp={imp}, flag={flag}")
                        assert b_chk.saldo_actual == teorico
                        assert b_chk.saldo_actual == esperado

                # TD.7 (Decisión 5): después de cada operación, cada meta tiene monto_actual igual a la suma de la decisión 5
                for m_chk in metas_activas:
                    db.refresh(m_chk)
                    from app.models.movimiento_meta import MovimientoMeta
                    from decimal import ROUND_HALF_UP
                    movs_m = db.execute(
                        select(MovimientoMeta).where(MovimientoMeta.meta_id == m_chk.id)
                    ).scalars().all()
                    m_mon = m_chk.moneda.value if hasattr(m_chk.moneda, "value") else str(m_chk.moneda)
                    suma_m = Decimal("0.00")
                    for mv in movs_m:
                        mv_mon = mv.moneda_movimiento.value if hasattr(mv.moneda_movimiento, "value") else str(mv.moneda_movimiento)
                        mv_val = Decimal(str(mv.monto))
                        if mv_mon == m_mon:
                            imp_m = mv_val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        else:
                            cot_m = Decimal(str(mv.cotizacion_usada))
                            if m_mon == "USD":
                                imp_m = (mv_val / cot_m).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            else:
                                imp_m = (mv_val * cot_m).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        t_m = mv.tipo.value if hasattr(mv.tipo, "value") else str(mv.tipo)
                        if t_m == TipoMovimientoMeta.APORTE.value:
                            suma_m += imp_m
                        else:
                            suma_m -= imp_m
                    suma_m = suma_m.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    diff_meta = Decimal(str(m_chk.monto_actual)) - suma_m
                    assert diff_meta == Decimal("0.00"), (
                        f"Meta {m_chk.nombre}: monto_actual={m_chk.monto_actual} != suma={suma_m} (diff={diff_meta})"
                    )

        dur = time_mod.time() - t0
        print(f"\nSemilla {semilla} completada ({num_secuencias} secuencias) en {dur:.2f}s")
        print("Tabla operaciones por semilla:")
        print(f"{'Operacion':<25} | {'Ejecutadas':<10} | {'Rechazadas':<10}")
        print("-" * 50)
        for op in OP_TIPOS:
            print(f"{op:<25} | {conteo_ejecutadas[op]:<10} | {conteo_rechazadas[op]:<10}")
            assert conteo_ejecutadas[op] >= 1, f"Semilla {semilla}: operación '{op}' no tuvo ninguna ejecución"


# =============================================================================
# TD.8 CONCURRENCIA
# =============================================================================
def test_td8_concurrencia(db: Session, SessionTesting, usuario_test: Usuario):
    hoy = hoy_argentina()
    t_start = time_mod.time()

    # Parte 1: Billetera sin foto con $1.000.000; dos hilos con 100 egresos de $100 cada uno
    b_sin_foto = crear_fixture_b(db, usuario_test.id, hoy, "Billetera Concurrencia 1")
    b_sin_foto.saldo_actual = Decimal("1000000.00")
    b_sin_foto.saldo_inicial = Decimal("1000000.00")
    cat_conc = Categoria(id=uuid.uuid4(), nombre="Conc Cat", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    db.add(cat_conc)
    db.commit()

    u_id = usuario_test.id
    b_sin_foto_id = b_sin_foto.id
    cat_conc_id = cat_conc.id

    errores_hilos = []

    def trabajador_egresos(hilo_id: int):
        session = SessionTesting()
        try:
            for i in range(100):
                data = TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=Decimal("100.00"),
                    moneda=Moneda.ARS,
                    metodo_pago=MetodoPago.DEBITO,
                    categoria_id=cat_conc_id,
                    billetera_id=b_sin_foto_id,
                    fecha=hoy,
                    descripcion=f"Egreso conc H{hilo_id} #{i}",
                )
                transaccion_service.crear_transaccion(session, u_id, data, commit=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            errores_hilos.append(e)
        finally:
            session.close()

    t1 = threading.Thread(target=trabajador_egresos, args=(1,))
    t2 = threading.Thread(target=trabajador_egresos, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(errores_hilos) == 0, f"Errores en hilos de egresos: {errores_hilos}"

    db.refresh(b_sin_foto)
    assert b_sin_foto.saldo_actual == Decimal("980000.00")
    teorico1 = conciliacion_service.calcular_saldo_teorico(db, b_sin_foto.id, hasta=hoy)
    assert b_sin_foto.saldo_actual == teorico1

    # Parte 2: Billeteras A y B con $1.000.000 cada una;
    # un hilo hace 50 transferencias de $100 de A a B y otro 50 de B a A
    b_conc_a = crear_fixture_b(db, usuario_test.id, hoy, "Billetera Conc A")
    b_conc_b = crear_fixture_b(db, usuario_test.id, hoy, "Billetera Conc B")
    b_conc_a.saldo_actual = Decimal("1000000.00")
    b_conc_a.saldo_inicial = Decimal("1000000.00")
    b_conc_b.saldo_actual = Decimal("1000000.00")
    b_conc_b.saldo_inicial = Decimal("1000000.00")
    db.commit()

    id_a = b_conc_a.id
    id_b = b_conc_b.id

    errores_transf = []

    def trabajador_transf(origen_id: uuid.UUID, destino_id: uuid.UUID, hilo_num: int):
        session = SessionTesting()
        try:
            for i in range(50):
                data = TransferenciaInternaCreate(
                    billetera_origen_id=origen_id,
                    billetera_destino_id=destino_id,
                    monto=Decimal("100.00"),
                    moneda=Moneda.ARS,
                    fecha=hoy,
                    notas=f"Transf conc H{hilo_num} #{i}",
                )
                transferencia_service.crear_transferencia(session, u_id, data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            errores_transf.append(e)
        finally:
            session.close()

    th_ab = threading.Thread(target=trabajador_transf, args=(id_a, id_b, 1))
    th_ba = threading.Thread(target=trabajador_transf, args=(id_b, id_a, 2))
    th_ab.start()
    th_ba.start()
    th_ab.join()
    th_ba.join()

    assert len(errores_transf) == 0, f"Errores en transferencias cruzadas: {errores_transf}"

    db.refresh(b_conc_a)
    db.refresh(b_conc_b)
    assert b_conc_a.saldo_actual == Decimal("1000000.00")
    assert b_conc_b.saldo_actual == Decimal("1000000.00")

    teorico_a = conciliacion_service.calcular_saldo_teorico(db, b_conc_a.id, hasta=hoy)
    teorico_b = conciliacion_service.calcular_saldo_teorico(db, b_conc_b.id, hasta=hoy)
    assert b_conc_a.saldo_actual == teorico_a
    assert b_conc_b.saldo_actual == teorico_b

    duracion = time_mod.time() - t_start
    print(f"\nTD.8 Concurrencia completada con éxito en {duracion:.2f}s")
