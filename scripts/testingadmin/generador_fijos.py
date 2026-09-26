"""
Módulo Generador de Gastos Fijos y Servicios.

Cumple con:
4.3 Gastos fijos:
  a. Alquiler con ajuste trimestral según el IPC real guardado en ipc_cache.
  b. Expensas que varían todos los meses.
  c. Luz y gas bimestrales, con más consumo en invierno.
  d. Internet y celular con subas en escalones.
  e. Prepaga que sube casi todos los meses.
4.4 Servicio con subas en escalones: "Clases de guitarra".
    Arranca en 60.000 y sube 5.000 cada 2 o 3 meses.
"""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda
from app.schemas.transaccion import TransaccionCreate
from app.services import transaccion_service
from app.utils.fecha import hoy_argentina
from scripts.testingadmin.datos_base import CatalogoEntidades


def generar_fijos(
    db: Session,
    cat: CatalogoEntidades,
    rng: random.Random,
    fecha_corte: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Genera todos los gastos fijos para testingadmin a lo largo de los 14 ciclos.
    Ninguna transacción se crea con fecha posterior a fecha_corte (por defecto hoy_argentina).
    """
    if fecha_corte is None:
        fecha_corte = hoy_argentina()

    transacciones_creadas: List[Transaccion] = []

    # Estructuras para la verdad conocida
    verdad_alquiler = []
    verdad_expensas = []
    verdad_luz_gas = []
    verdad_comunicaciones = []
    verdad_prepaga = []
    verdad_guitarra = []

    # Cálculo trimestral de alquiler con IPC real
    # Base inicial: $680.000 ARS en 2025-08
    alquiler_base = Decimal("680000.00")
    monto_alquiler_por_mes: Dict[int, Decimal] = {}

    # Trimestre 1 (Meses 1, 2, 3: Agosto, Septiembre, Octubre 2025)
    f_t1 = alquiler_base
    monto_alquiler_por_mes[1] = f_t1
    monto_alquiler_por_mes[2] = f_t1
    monto_alquiler_por_mes[3] = f_t1

    # Trimestre 2 (Meses 4, 5, 6: Noviembre, Diciembre 2025, Enero 2026) -> Ajuste por IPC 2025-10 / 2025-07
    coef_t2 = cat.obtener_coeficiente_ipc("2025-07", "2025-10")
    f_t2 = (alquiler_base * coef_t2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    monto_alquiler_por_mes[4] = f_t2
    monto_alquiler_por_mes[5] = f_t2
    monto_alquiler_por_mes[6] = f_t2

    # Trimestre 3 (Meses 7, 8, 9: Febrero, Marzo, Abril 2026) -> Ajuste por IPC 2026-01 / 2025-10
    coef_t3 = cat.obtener_coeficiente_ipc("2025-10", "2026-01")
    f_t3 = (f_t2 * coef_t3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    monto_alquiler_por_mes[7] = f_t3
    monto_alquiler_por_mes[8] = f_t3
    monto_alquiler_por_mes[9] = f_t3

    # Trimestre 4 (Meses 10, 11, 12: Mayo, Junio, Julio 2026) -> Ajuste por IPC 2026-04 / 2026-01
    coef_t4 = cat.obtener_coeficiente_ipc("2026-01", "2026-04")
    f_t4 = (f_t3 * coef_t4).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    monto_alquiler_por_mes[10] = f_t4
    monto_alquiler_por_mes[11] = f_t4
    monto_alquiler_por_mes[12] = f_t4

    # Trimestre 5 (Meses 13, 14: Agosto, Septiembre 2026) -> Ajuste por IPC 2026-07 / 2026-04
    coef_t5 = cat.obtener_coeficiente_ipc("2026-04", "2026-07")
    f_t5 = (f_t4 * coef_t5).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    monto_alquiler_por_mes[13] = f_t5
    monto_alquiler_por_mes[14] = f_t5

    # Escalones de Clases de Guitarra (Arranca en 60.000 y sube 5.000 cada 2 o 3 meses)
    guitarra_montos = {
        1: Decimal("60000.00"),
        2: Decimal("60000.00"),
        3: Decimal("60000.00"),
        4: Decimal("65000.00"),
        5: Decimal("65000.00"),
        6: Decimal("70000.00"),
        7: Decimal("70000.00"),
        8: Decimal("70000.00"),
        9: Decimal("75000.00"),
        10: Decimal("75000.00"),
        11: Decimal("75000.00"),
        12: Decimal("80000.00"),
        13: Decimal("80000.00"),
        14: Decimal("80000.00"),
    }

    # Iterar por cada ciclo para generar los gastos fijos
    for idx, ciclo in enumerate(cat.ciclos, start=1):
        ini = ciclo.fecha_inicio
        mes = ciclo.mes_ancla
        anio = ciclo.anio_ancla

        # -------------------------------------------------------------
        # a. Alquiler (día 5 del mes calendario)
        # -------------------------------------------------------------
        m_alq = monto_alquiler_por_mes[idx]
        dia_alq = min(5, (ciclo.fecha_fin - ini).days)
        fecha_alq = date(anio, mes, 5) if 1 <= 5 <= 28 else (ini + (ciclo.fecha_fin - ini) // 4)

        if fecha_alq <= fecha_corte:
            tx_alq = transaccion_service.crear_transaccion(
                db,
                cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=m_alq,
                    moneda=Moneda.ARS,
                    fecha=fecha_alq,
                    descripcion=f"Alquiler departamento Almagro {mes:02d}/{anio}",
                    categoria_id=cat.cat_vivienda.id,
                    subcategoria_id=cat.sub_alquiler.id,
                    metodo_pago=MetodoPago.TRANSFERENCIA,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx_alq)
            verdad_alquiler.append({
                "ciclo": idx,
                "periodo": f"{anio}-{mes:02d}",
                "fecha": fecha_alq.isoformat(),
                "monto": str(m_alq),
            })

        # -------------------------------------------------------------
        # b. Expensas (varían todos los meses)
        # -------------------------------------------------------------
        base_exp = Decimal("92000.00") + Decimal(str(idx * 3800))
        ruido_exp = Decimal(str(rng.randint(-2500, 3500)))
        m_exp = (base_exp + ruido_exp).quantize(Decimal("0.01"))
        fecha_exp = date(anio, mes, 10) if 1 <= 10 <= 28 else (ini + (ciclo.fecha_fin - ini) // 3)

        if fecha_exp <= fecha_corte:
            tx_exp = transaccion_service.crear_transaccion(
                db,
                cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=m_exp,
                    moneda=Moneda.ARS,
                    fecha=fecha_exp,
                    descripcion=f"Expensas edificio {mes:02d}/{anio}",
                    categoria_id=cat.cat_vivienda.id,
                    subcategoria_id=cat.sub_expensas.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx_exp)
            verdad_expensas.append({
                "ciclo": idx,
                "fecha": fecha_exp.isoformat(),
                "monto": str(m_exp),
            })

        # -------------------------------------------------------------
        # c. Luz y Gas bimestrales (con mayor consumo en invierno)
        # -------------------------------------------------------------
        # Invierno en Argentina: meses 5 (mayo), 6 (junio), 7 (julio), 8 (agosto)
        es_invierno = mes in (5, 6, 7, 8)

        # Luz en meses impares (1, 3, 5, 7, 9, 11)
        if idx % 2 == 1:
            base_luz = Decimal("46000.00") if es_invierno else Decimal("31000.00")
            base_luz += Decimal(str(idx * 800))
            m_luz = (base_luz + Decimal(str(rng.randint(-2000, 2500)))).quantize(Decimal("0.01"))
            fecha_luz = date(anio, mes, 16) if 1 <= 16 <= 28 else (ini + (ciclo.fecha_fin - ini) // 2)

            if fecha_luz <= fecha_corte:
                tx_luz = transaccion_service.crear_transaccion(
                    db,
                    cat.user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_luz,
                        moneda=Moneda.ARS,
                        fecha=fecha_luz,
                        descripcion=f"Edesur factura luz bimestral {mes:02d}/{anio}",
                        categoria_id=cat.cat_vivienda.id,
                        subcategoria_id=cat.sub_luz.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=cat.b_galicia.id,
                        es_recurrente=False,
                        origen=OrigenTransaccion.MANUAL,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    ),
                    commit=False,
                )
                transacciones_creadas.append(tx_luz)
                verdad_luz_gas.append({
                    "servicio": "Luz (Edesur)",
                    "ciclo": idx,
                    "fecha": fecha_luz.isoformat(),
                    "monto": str(m_luz),
                    "es_invierno": es_invierno,
                })

        # Gas en meses pares (2, 4, 6, 8, 10, 12, 14)
        if idx % 2 == 0:
            # Gas tiene un salto drástico en invierno (calefacción)
            base_gas = Decimal("48000.00") if es_invierno else Decimal("14000.00")
            base_gas += Decimal(str(idx * 700))
            m_gas = (base_gas + Decimal(str(rng.randint(-1500, 2000)))).quantize(Decimal("0.01"))
            fecha_gas = date(anio, mes, 19) if 1 <= 19 <= 28 else (ini + (ciclo.fecha_fin - ini) // 2)

            if fecha_gas <= fecha_corte:
                tx_gas = transaccion_service.crear_transaccion(
                    db,
                    cat.user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_gas,
                        moneda=Moneda.ARS,
                        fecha=fecha_gas,
                        descripcion=f"Metrogas factura gas bimestral {mes:02d}/{anio}",
                        categoria_id=cat.cat_vivienda.id,
                        subcategoria_id=cat.sub_gas.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=cat.b_galicia.id,
                        es_recurrente=False,
                        origen=OrigenTransaccion.MANUAL,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    ),
                    commit=False,
                )
                transacciones_creadas.append(tx_gas)
                verdad_luz_gas.append({
                    "servicio": "Gas (Metrogas)",
                    "ciclo": idx,
                    "fecha": fecha_gas.isoformat(),
                    "monto": str(m_gas),
                    "es_invierno": es_invierno,
                })

        # -------------------------------------------------------------
        # d. Internet y Celular con subas en escalones
        # -------------------------------------------------------------
        if idx <= 4:
            m_internet = Decimal("32000.00")
            m_celular = Decimal("18500.00")
        elif idx <= 8:
            m_internet = Decimal("37500.00")
            m_celular = Decimal("22000.00")
        elif idx <= 11:
            m_internet = Decimal("44000.00")
            m_celular = Decimal("26500.00")
        else:
            m_internet = Decimal("49800.00")
            m_celular = Decimal("31000.00")

        fecha_int = date(anio, mes, 12) if 1 <= 12 <= 28 else (ini + (ciclo.fecha_fin - ini) // 3)
        if fecha_int <= fecha_corte:
            tx_int = transaccion_service.crear_transaccion(
                db,
                cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=m_internet,
                    moneda=Moneda.ARS,
                    fecha=fecha_int,
                    descripcion=f"Fibertel Personal Flow 300MB {mes:02d}/{anio}",
                    categoria_id=cat.cat_comun.id,
                    subcategoria_id=cat.sub_internet.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx_int)

        fecha_cel = date(anio, mes, 14) if 1 <= 14 <= 28 else (ini + (ciclo.fecha_fin - ini) // 2)
        if fecha_cel <= fecha_corte:
            tx_cel = transaccion_service.crear_transaccion(
                db,
                cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=m_celular,
                    moneda=Moneda.ARS,
                    fecha=fecha_cel,
                    descripcion=f"Abono celular plan 15GB {mes:02d}/{anio}",
                    categoria_id=cat.cat_comun.id,
                    subcategoria_id=cat.sub_celular.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx_cel)

        verdad_comunicaciones.append({
            "ciclo": idx,
            "internet": str(m_internet),
            "celular": str(m_celular),
        })

        # -------------------------------------------------------------
        # e. Prepaga (sube casi todos los meses)
        # -------------------------------------------------------------
        base_prepaga = Decimal("125000.00") + Decimal(str(idx * 5500))
        m_prepaga = (base_prepaga + Decimal(str(rng.randint(-1000, 1500)))).quantize(Decimal("0.01"))
        fecha_prepaga = date(anio, mes, 8) if 1 <= 8 <= 28 else (ini + (ciclo.fecha_fin - ini) // 4)

        if fecha_prepaga <= fecha_corte:
            tx_prep = transaccion_service.crear_transaccion(
                db,
                cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=m_prepaga,
                    moneda=Moneda.ARS,
                    fecha=fecha_prepaga,
                    descripcion=f"Swiss Medical cuota prepaga {mes:02d}/{anio}",
                    categoria_id=cat.cat_salud.id,
                    subcategoria_id=cat.sub_farmacia.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx_prep)
            verdad_prepaga.append({
                "ciclo": idx,
                "fecha": fecha_prepaga.isoformat(),
                "monto": str(m_prepaga),
            })

        # -------------------------------------------------------------
        # 4.4 Servicio con escalones: "Clases de guitarra"
        # -------------------------------------------------------------
        m_guitarra = guitarra_montos[idx]
        fecha_guitarra = date(anio, mes, 7) if 1 <= 7 <= 28 else (ini + (ciclo.fecha_fin - ini) // 4)

        if fecha_guitarra <= fecha_corte:
            tx_guit = transaccion_service.crear_transaccion(
                db,
                cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=m_guitarra,
                    moneda=Moneda.ARS,
                    fecha=fecha_guitarra,
                    descripcion=f"Clases de guitarra mensual profesor particular {mes:02d}/{anio}",
                    categoria_id=cat.cat_recreo.id,
                    subcategoria_id=cat.sub_hobbies.id,
                    metodo_pago=MetodoPago.TRANSFERENCIA,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx_guit)
            verdad_guitarra.append({
                "ciclo": idx,
                "fecha": fecha_guitarra.isoformat(),
                "monto": str(m_guitarra),
            })

    db.flush()

    return {
        "transacciones": transacciones_creadas,
        "verdad_fijos": {
            "alquiler": {
                "frecuencia": "mensual",
                "ajuste": "trimestral por IPC acumulado real de ipc_cache",
                "pagos": verdad_alquiler,
            },
            "expensas": {
                "frecuencia": "mensual",
                "pagos": verdad_expensas,
            },
            "servicios_bimestrales": {
                "descripcion": "Luz y Gas bimestrales alternados con mayor consumo en invierno",
                "pagos": verdad_luz_gas,
            },
            "comunicaciones": {
                "descripcion": "Internet y Celular con subas en 4 escalones",
                "escalones": verdad_comunicaciones,
            },
            "prepaga": {
                "descripcion": "Prepaga de salud con suba mensual sostenida",
                "pagos": verdad_prepaga,
            },
            "servicio_escalones_guitarra": {
                "nombre": "Clases de guitarra",
                "descripcion": "Arranca en 60.000 y sube 5.000 cada 2 a 3 meses",
                "pagos": verdad_guitarra,
            },
        },
    }
