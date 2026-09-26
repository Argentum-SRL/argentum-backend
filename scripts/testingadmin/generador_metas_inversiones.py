"""
Módulo Generador de Metas, Billetera de Inversión, Transferencias y Presupuestos.

Cumple con:
4.9 Billetera de inversión "Ahorro con rendimiento":
    - Transferencia mensual desde Galicia de entre 100.000 y 150.000.
    - Rendimiento mensual registrado con el servicio real `confirmar_rendimiento`.
4.10 Metas de ahorro:
    - Fondo de Emergencia: objetivo $3.500.000, aporte mensual de $100.000 con algún mes sin aporte.
    - Viaje a Bariloche: objetivo $1.400.000 (límite 2027-01-31), aporte mensual de $50.000 con algún mes sin aporte.
    - Registrados con el servicio real `meta_service.registrar_movimiento`.
4.11 Presupuestos:
    - Gastronomía y Salidas ($200.000): excedido en 3 de los últimos 4 ciclos.
    - Indumentaria ($150.000): casi siempre por debajo.
4.14 Movimientos entre cuentas:
    - Extracciones de efectivo Galicia -> Efectivo ARS.
    - Transferencias bancarias Galicia -> Santander.
- Recálculo de saldos de billeteras y metas, perfil financiero y calibración.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import func, text, case
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    PeriodoPresupuestoTipo,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import Moneda
from app.schemas.meta import MetaCreate
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.schemas.presupuesto import PresupuestoCategoriaInput, PresupuestoCreate
from app.schemas.transaccion import TransaccionCreate
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import (
    calibracion_service,
    meta_service,
    perfil_financiero_service,
    presupuesto_service,
    rendimiento_billetera_service,
    transaccion_service,
    transferencia_service,
)
from app.utils.fecha import hoy_argentina
from scripts.testingadmin.datos_base import CatalogoEntidades


def generar_metas_e_inversiones(
    db: Session,
    cat: CatalogoEntidades,
    rng: random.Random,
    fecha_corte: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Crea las metas, aportes, billetera de inversión, transferencias internas,
    presupuestos, calibraciones y perfiles financieros de testingadmin.
    Ninguna fila se crea con fecha posterior a fecha_corte (por defecto hoy_argentina).
    """
    if fecha_corte is None:
        fecha_corte = hoy_argentina()
    transacciones_creadas: List[Transaccion] = []
    transferencias_creadas: List[TransferenciaInterna] = []
    movimientos_meta_creados: List[MovimientoMeta] = []

    # =========================================================================
    # 1. METAS DE AHORRO
    # =========================================================================
    # 1.1 Fondo de Emergencia
    meta_emergencia = meta_service.crear_meta(
        db,
        cat.user.id,
        MetaCreate(
            nombre="Fondo de Emergencia",
            monto_objetivo=Decimal("3500000.00"),
            moneda=Moneda.ARS,
            monto_actual=Decimal("0.00"),
            fecha_limite=None,
            color="#10B981",
            nota="Fondo de resguardo para emergencias e imprevistos",
        ),
    )

    # 1.2 Viaje a Bariloche
    meta_bariloche = meta_service.crear_meta(
        db,
        cat.user.id,
        MetaCreate(
            nombre="Viaje a Bariloche",
            monto_objetivo=Decimal("1400000.00"),
            moneda=Moneda.ARS,
            monto_actual=Decimal("0.00"),
            fecha_limite=date(2027, 1, 31),
            color="#3B82F6",
            nota="Vacaciones de verano en el sur",
        ),
    )
    db.commit()

    # Aportes mensuales a las metas:
    # Fondo de Emergencia: $100.000 mensual (omitido en Enero 2026 por vacaciones)
    # Viaje a Bariloche: $50.000 mensual (omitido en Mayo 2026 por mes austero)
    verdad_aportes_emergencia = []
    verdad_aportes_bariloche = []

    for idx, ciclo in enumerate(cat.ciclos, start=1):
        ini = ciclo.fecha_inicio
        mes = ciclo.mes_ancla
        anio = ciclo.anio_ancla

        # Día de aporte: 6 de cada mes (tras el cobro del sueldo)
        fecha_aporte = date(anio, mes, 6) if 1 <= 6 <= 28 else (ini + (ciclo.fecha_fin - ini) // 5)

        if fecha_aporte <= fecha_corte:
            # Fondo de Emergencia ($100.000) - Se omite en Enero 2026 (Ciclo 7)
            if not (anio == 2026 and mes == 1):
                mov_em = meta_service.registrar_movimiento(
                    db,
                    cat.user.id,
                    meta_emergencia.id,
                    MovimientoMetaCreate(
                        tipo=TipoMovimientoMeta.APORTE,
                        monto=Decimal("100000.00"),
                        moneda_movimiento=Moneda.ARS,
                        billetera_id=cat.b_galicia.id,
                        fecha=fecha_aporte,
                    ),
                )
                movimientos_meta_creados.append(mov_em)
                verdad_aportes_emergencia.append({
                    "ciclo": idx,
                    "fecha": fecha_aporte.isoformat(),
                    "monto": "100000.00",
                })

            # Viaje a Bariloche ($50.000) - Se omite en Mayo 2026 (Ciclo 10)
            if not (anio == 2026 and mes == 5):
                mov_ba = meta_service.registrar_movimiento(
                    db,
                    cat.user.id,
                    meta_bariloche.id,
                    MovimientoMetaCreate(
                        tipo=TipoMovimientoMeta.APORTE,
                        monto=Decimal("50000.00"),
                        moneda_movimiento=Moneda.ARS,
                        billetera_id=cat.b_galicia.id,
                        fecha=fecha_aporte,
                    ),
                )
                movimientos_meta_creados.append(mov_ba)
                verdad_aportes_bariloche.append({
                    "ciclo": idx,
                    "fecha": fecha_aporte.isoformat(),
                    "monto": "50000.00",
                })

    db.commit()

    # =========================================================================
    # 2. BILLETERA DE INVERSIÓN "Ahorro con rendimiento"
    # =========================================================================
    # Transferencia mensual desde Galicia de entre $100.000 y $150.000
    # Más rendimiento mensual registrado con `confirmar_rendimiento`
    verdad_depositos_inversion = []
    verdad_rendimientos = []

    saldo_inversion_acumulado = Decimal("0.00")
    tna_anual = Decimal("34.00") / Decimal("100.00")

    for idx, ciclo in enumerate(cat.ciclos, start=1):
        mes = ciclo.mes_ancla
        anio = ciclo.anio_ancla

        # Día de transferencia: 10 de cada mes
        f_dep = date(anio, mes, 10) if 1 <= 10 <= 28 else ciclo.fecha_inicio
        if f_dep <= fecha_corte:
            monto_dep = Decimal(str(rng.randint(100, 150) * 1000))  # entre 100.000 y 150.000
            tr_inv = transferencia_service.crear_transferencia(
                db,
                cat.user.id,
                TransferenciaInternaCreate(
                    billetera_origen_id=cat.b_galicia.id,
                    billetera_destino_id=cat.b_inversion.id,
                    monto=monto_dep,
                    moneda=Moneda.ARS,
                    fecha=f_dep,
                    notas=f"Depósito mensual ahorro remunerado {mes:02d}/{anio}",
                ),
                commit=True,
            )
            transferencias_creadas.append(tr_inv)
            saldo_inversion_acumulado += monto_dep
            verdad_depositos_inversion.append({
                "ciclo": idx,
                "fecha": f_dep.isoformat(),
                "monto": str(monto_dep),
            })

        # Rendimiento mensual devengado (a fin de mes)
        # Rendimiento = saldo * (TNA / 365) * 30 días
        dias_mes = Decimal(str(ciclo.dias))
        rend_mes = (saldo_inversion_acumulado * (tna_anual / Decimal("365.0")) * dias_mes).quantize(Decimal("0.01"))
        f_rend = ciclo.fecha_fin
        if f_rend <= fecha_corte:
            f_rend_dt = datetime(f_rend.year, f_rend.month, f_rend.day, 23, 59, 59, tzinfo=timezone.utc)

            rendimiento_billetera_service.confirmar_rendimiento(
                db=db,
                usuario_id=cat.user.id,
                billetera_id=cat.b_inversion.id,
                monto=rend_mes,
                fecha=f_rend_dt,
            )
            saldo_inversion_acumulado += rend_mes
            verdad_rendimientos.append({
                "ciclo": idx,
                "fecha": f_rend.isoformat(),
                "monto": str(rend_mes),
            })

    db.commit()

    # =========================================================================
    # 3. MOVIMIENTOS ENTRE CUENTAS (TRANSFERENCIAS INTERNAS)
    # =========================================================================
    # a) Extracciones de Galicia a Efectivo ARS (1-2 por ciclo para gastos en cash)
    # b) Transferencias Galicia a Santander (en meses clave para cubrir gastos)
    for idx, ciclo in enumerate(cat.ciclos, start=1):
        mes = ciclo.mes_ancla
        anio = ciclo.anio_ancla

        # Extracción a efectivo día ~3
        f_ext1 = date(anio, mes, 3) if 1 <= 3 <= 28 else ciclo.fecha_inicio
        if f_ext1 <= fecha_corte:
            m_ext1 = Decimal(str(rng.randint(60, 90) * 1000))
            tr_e1 = transferencia_service.crear_transferencia(
                db,
                cat.user.id,
                TransferenciaInternaCreate(
                    billetera_origen_id=cat.b_galicia.id,
                    billetera_destino_id=cat.b_efectivo_ars.id,
                    monto=m_ext1,
                    moneda=Moneda.ARS,
                    fecha=f_ext1,
                    notas="Extracción de cajero automático Banelco efectivo",
                ),
                commit=True,
            )
            transferencias_creadas.append(tr_e1)

        # Segunda extracción a mediados de mes
        f_ext2 = date(anio, mes, 17) if 1 <= 17 <= 28 else (ciclo.fecha_inicio + (ciclo.fecha_fin - ciclo.fecha_inicio) // 2)
        if f_ext2 <= fecha_corte:
            m_ext2 = Decimal(str(rng.randint(50, 80) * 1000))
            tr_e2 = transferencia_service.crear_transferencia(
                db,
                cat.user.id,
                TransferenciaInternaCreate(
                    billetera_origen_id=cat.b_galicia.id,
                    billetera_destino_id=cat.b_efectivo_ars.id,
                    monto=m_ext2,
                    moneda=Moneda.ARS,
                    fecha=f_ext2,
                    notas="Extracción cajero automático efectivo quincena",
                ),
                commit=True,
            )
            transferencias_creadas.append(tr_e2)

        # Transferencia Galicia -> Santander (cada 2 meses o cuando Santander necesita fondos)
        if idx in (2, 5, 8, 11, 13, 14):
            f_san = date(anio, mes, 25) if 1 <= 25 <= 28 else ciclo.fecha_fin
            if f_san <= fecha_corte:
                m_san = Decimal(str(rng.randint(90, 140) * 1000))
                tr_s = transferencia_service.crear_transferencia(
                    db,
                    cat.user.id,
                    TransferenciaInternaCreate(
                        billetera_origen_id=cat.b_galicia.id,
                        billetera_destino_id=cat.b_santander.id,
                        monto=m_san,
                        moneda=Moneda.ARS,
                        fecha=f_san,
                        notas="Transferencia a Santander cobertura tarjeta y gastos",
                    ),
                    commit=True,
                )
                transferencias_creadas.append(tr_s)

    db.commit()

    # =========================================================================
    # 4. PRESUPUESTOS (GASTRONOMÍA Y SALIDAS / INDUMENTARIA)
    # =========================================================================
    # Presupuesto 1: Gastronomía y Salidas ($200.000)
    pres_gastro = presupuesto_service.crear_presupuesto(
        db,
        cat.user.id,
        PresupuestoCreate(
            nombre="Gastronomía y Salidas",
            monto=Decimal("200000.00"),
            moneda=Moneda.ARS,
            periodo=PeriodoPresupuestoTipo.MENSUAL,
            renovacion=RenovacionPresupuesto.AUTOMATICA,
            categorias=[
                PresupuestoCategoriaInput(categoria_id=cat.cat_gastro.id),
                PresupuestoCategoriaInput(categoria_id=cat.cat_recreo.id),
            ],
        ),
    )

    # Presupuesto 2: Indumentaria ($150.000)
    pres_indum = presupuesto_service.crear_presupuesto(
        db,
        cat.user.id,
        PresupuestoCreate(
            nombre="Indumentaria",
            monto=Decimal("150000.00"),
            moneda=Moneda.ARS,
            periodo=PeriodoPresupuestoTipo.MENSUAL,
            renovacion=RenovacionPresupuesto.AUTOMATICA,
            categorias=[PresupuestoCategoriaInput(categoria_id=cat.cat_indum.id)],
        ),
    )
    db.commit()

    # REGLA 4.11: Gastronomía y Salidas excedido en 3 de los últimos 4 ciclos.
    # Los últimos 4 ciclos son Ciclos 11, 12, 13, 14.
    # Vamos a verificar el gasto real de cada ciclo y calibrar para que en 11, 12 y 14 supere 200.000.
    excedidos_gastro = []
    for c_idx in [11, 12, 13, 14]:
        cic = cat.ciclos[c_idx - 1]
        gasto_actual = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
            Transaccion.usuario_id == cat.user.id,
            Transaccion.categoria_id.in_([cat.cat_gastro.id, cat.cat_recreo.id]),
            Transaccion.fecha >= cic.fecha_inicio,
            Transaccion.fecha <= cic.fecha_fin,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.es_padre_cuotas == False,
        ).scalar()

        # En ciclos 11, 12 y 14 queremos que supere $200.000 (ej. $215.000 - $230.000)
        # En ciclo 13 queremos que quede por debajo (ej. $185.000)
        if c_idx in (11, 12, 14):
            if gasto_actual <= Decimal("200000.00"):
                diff = (Decimal("215000.00") - gasto_actual).quantize(Decimal("0.01"))
                f_ajuste = cic.fecha_inicio + (cic.fecha_fin - cic.fecha_inicio) // 2
                if f_ajuste <= fecha_corte:
                    tx_aj = transaccion_service.crear_transaccion(
                        db,
                        cat.user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=diff,
                            moneda=Moneda.ARS,
                            fecha=f_ajuste,
                            descripcion="Cena festejo especial amigos restaurante",
                            categoria_id=cat.cat_gastro.id,
                            subcategoria_id=cat.sub_resto.id,
                            metodo_pago=MetodoPago.DEBITO,
                            billetera_id=cat.b_galicia.id,
                            es_recurrente=False,
                            origen=OrigenTransaccion.MANUAL,
                            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                        ),
                        commit=True,
                    )
                    transacciones_creadas.append(tx_aj)
                    gasto_actual += diff
            excedidos_gastro.append({"ciclo": c_idx, "gasto": str(gasto_actual), "excedido": True})
        else:
            # Ciclo 13: por debajo
            excedidos_gastro.append({"ciclo": c_idx, "gasto": str(gasto_actual), "excedido": gasto_actual > Decimal("200000.00")})

    # Crear los períodos históricos para los presupuestos
    for ciclo in cat.ciclos:
        if ciclo.fecha_inicio > fecha_corte:
            continue
        gasto_g = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
            Transaccion.usuario_id == cat.user.id,
            Transaccion.categoria_id.in_([cat.cat_gastro.id, cat.cat_recreo.id]),
            Transaccion.fecha >= ciclo.fecha_inicio,
            Transaccion.fecha <= ciclo.fecha_fin,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.es_padre_cuotas == False,
        ).scalar()
        p_g = PeriodoPresupuesto(
            presupuesto_id=pres_gastro.id,
            fecha_inicio=ciclo.fecha_inicio,
            fecha_fin=ciclo.fecha_fin,
            monto_limite=Decimal("200000.00"),
            monto_usado=gasto_g,
            superado=(gasto_g > Decimal("200000.00")),
        )
        db.add(p_g)

        gasto_i = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
            Transaccion.usuario_id == cat.user.id,
            Transaccion.categoria_id == cat.cat_indum.id,
            Transaccion.fecha >= ciclo.fecha_inicio,
            Transaccion.fecha <= ciclo.fecha_fin,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.es_padre_cuotas == False,
        ).scalar()
        p_i = PeriodoPresupuesto(
            presupuesto_id=pres_indum.id,
            fecha_inicio=ciclo.fecha_inicio,
            fecha_fin=ciclo.fecha_fin,
            monto_limite=Decimal("150000.00"),
            monto_usado=gasto_i,
            superado=(gasto_i > Decimal("150000.00")),
        )
        db.add(p_i)

    db.commit()

    # NOTA: Los saldos de billeteras salen exclusivamente de los servicios reales de la app
    # (no se pisa saldo_actual manualmente).

    # Actualizar monto_actual de metas
    for meta in [meta_emergencia, meta_bariloche]:
        tot_calc = db.query(
            func.coalesce(
                func.sum(
                    case(
                        (MovimientoMeta.tipo == TipoMovimientoMeta.APORTE, MovimientoMeta.monto),
                        else_=-MovimientoMeta.monto
                    )
                ),
                Decimal("0.00")
            )
        ).filter(MovimientoMeta.meta_id == meta.id).scalar()
        meta.monto_actual = tot_calc

    db.commit()

    # Recalcular perfil financiero y calibración de testingadmin
    perfil_financiero_service.calcular_y_persistir_perfil(db, cat.user.id)
    calibracion_service.calcular_y_guardar_calibracion_usuario(db, cat.user.id, Moneda.ARS)
    db.commit()

    return {
        "transacciones": transacciones_creadas,
        "transferencias": transferencias_creadas,
        "movimientos_meta": movimientos_meta_creados,
        "verdad_metas_inversiones": {
            "meta_fondo_emergencia": {
                "nombre": "Fondo de Emergencia",
                "objetivo": "3500000.00",
                "monto_alcanzado": str(meta_emergencia.monto_actual),
                "aportes": verdad_aportes_emergencia,
                "mes_sin_aporte": "Enero 2026 (Ciclo 7)",
            },
            "meta_viaje_bariloche": {
                "nombre": "Viaje a Bariloche",
                "objetivo": "1400000.00",
                "monto_alcanzado": str(meta_bariloche.monto_actual),
                "aportes": verdad_aportes_bariloche,
                "mes_sin_aporte": "Mayo 2026 (Ciclo 10)",
            },
            "billetera_inversion": {
                "nombre": "Ahorro con rendimiento",
                "tna": "34.00%",
                "depositos_mensuales": verdad_depositos_inversion,
                "rendimientos_mensuales": verdad_rendimientos,
            },
            "presupuestos": {
                "gastronomia_y_salidas": {
                    "limite": "200000.00",
                    "excedido_en_3_de_ultimos_4": excedidos_gastro,
                },
                "indumentaria": {
                    "limite": "150000.00",
                    "comportamiento": "Casi siempre por debajo del límite",
                },
            },
        },
    }
