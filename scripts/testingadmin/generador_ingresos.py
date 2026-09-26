"""
Módulo Generador de Ingresos: Sueldos, Aguinaldos y Trabajos Freelance.

Cumple con:
- Persona: empleado en relación de dependencia en CABA, sueldo inicial $2.600.000 neto.
- Sueldo depositado el día de inicio de cada ciclo (según la función del ciclo).
- Subas en escalones de paritaria entre 3% y 5% cada 2 a 4 meses, con meses sin aumento.
- Aguinaldo (SAC) como movimiento independiente cerca del 18/12 y 30/06 por la mitad
  del mejor sueldo del semestre correspondiente.
- NINGÚN ingreso lleva es_recurrente = True.
- 4 trabajos freelance irregulares con montos distintos, uno de ellos en USD (billetera Efectivo USD).
"""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
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


def generar_ingresos(
    db: Session,
    cat: CatalogoEntidades,
    rng: random.Random,
    fecha_corte: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Genera todos los ingresos para testingadmin durante los 14 ciclos.
    Ningún ingreso se genera con fecha posterior a fecha_corte (por defecto hoy_argentina).
    Retorna diccionario con transacciones generadas y datos para la verdad conocida.
    """
    if fecha_corte is None:
        fecha_corte = hoy_argentina()

    transacciones_creadas: List[Transaccion] = []
    sueldos_por_ciclo: List[Dict[str, Any]] = []

    # Escalones de sueldo por ciclo (neto inicial 2.600.000, paritarias 3%-5% cada 2-4 meses)
    montos_sueldo_ciclo = [
        Decimal("2600000.00"),  # Ciclo 1 (2025-07-30) - Sueldo inicial
        Decimal("2600000.00"),  # Ciclo 2 (2025-08-29) - Sin aumento
        Decimal("2704000.00"),  # Ciclo 3 (2025-09-30) - +4% paritaria
        Decimal("2704000.00"),  # Ciclo 4 (2025-10-30) - Sin aumento
        Decimal("2704000.00"),  # Ciclo 5 (2025-11-28) - Sin aumento
        Decimal("2812160.00"),  # Ciclo 6 (2025-12-30) - +4% paritaria
        Decimal("2812160.00"),  # Ciclo 7 (2026-01-30) - Sin aumento
        Decimal("2924646.40"),  # Ciclo 8 (2026-02-27) - +4% paritaria
        Decimal("2924646.40"),  # Ciclo 9 (2026-03-30) - Sin aumento
        Decimal("3070878.72"),  # Ciclo 10 (2026-04-30) - +5% paritaria
        Decimal("3070878.72"),  # Ciclo 11 (2026-05-29) - Sin aumento
        Decimal("3193713.87"),  # Ciclo 12 (2026-06-30) - +4% paritaria
        Decimal("3193713.87"),  # Ciclo 13 (2026-07-30) - Sin aumento
        Decimal("3321462.42"),  # Ciclo 14 (2026-08-28) - +4% paritaria
    ]

    # 1. Sueldos mensuales
    sueldos_h2_2025: List[Decimal] = []
    sueldos_h1_2026: List[Decimal] = []

    for idx, ciclo in enumerate(cat.ciclos):
        monto = montos_sueldo_ciclo[idx]
        fecha_cobro = ciclo.fecha_sueldo

        # Filtro de fecha de corte para asegurar que no se creen movimientos futuros
        if fecha_cobro > fecha_corte:
            continue

        # Rastrear para cálculo de SAC
        if fecha_cobro.year == 2025:
            sueldos_h2_2025.append(monto)
        elif fecha_cobro.year == 2026 and fecha_cobro.month <= 6:
            sueldos_h1_2026.append(monto)

        tx_data = TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=monto,
            moneda=Moneda.ARS,
            fecha=fecha_cobro,
            descripcion=f"Sueldo neto {fecha_cobro.strftime('%m/%Y')} haberes relación de dependencia",
            categoria_id=cat.cat_empleo.id,
            subcategoria_id=cat.sub_sueldo.id,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            billetera_id=cat.b_galicia.id,
            es_recurrente=False,  # REGLA OBLIGATORIA: NINGÚN ingreso lleva es_recurrente
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        tx = transaccion_service.crear_transaccion(db, cat.user.id, tx_data, commit=False)
        transacciones_creadas.append(tx)

        sueldos_por_ciclo.append({
            "ciclo": ciclo.numero,
            "fecha": fecha_cobro.isoformat(),
            "monto": str(monto),
        })

    # 2. Aguinaldos (SAC)
    # SAC Segundo Semestre 2025 (cerca del 18/12)
    mejor_sueldo_2025 = max(sueldos_h2_2025) if sueldos_h2_2025 else Decimal("2704000.00")
    monto_sac_2025 = (mejor_sueldo_2025 / Decimal("2.0")).quantize(Decimal("0.01"))
    fecha_sac_2025 = date(2025, 12, 18)

    if fecha_sac_2025 <= fecha_corte:
        tx_sac_2025 = transaccion_service.crear_transaccion(
            db,
            cat.user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.INGRESO,
                monto=monto_sac_2025,
                moneda=Moneda.ARS,
                fecha=fecha_sac_2025,
                descripcion="SAC Segundo Semestre 2025 (Medio Aguinaldo)",
                categoria_id=cat.cat_empleo.id,
                subcategoria_id=cat.sub_aguinaldo.id,
                metodo_pago=MetodoPago.TRANSFERENCIA,
                billetera_id=cat.b_galicia.id,
                es_recurrente=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
            commit=False,
        )
        transacciones_creadas.append(tx_sac_2025)

    # SAC Primer Semestre 2026 (cerca del 30/06)
    mejor_sueldo_2026 = max(sueldos_h1_2026) if sueldos_h1_2026 else Decimal("3193713.87")
    monto_sac_2026 = (mejor_sueldo_2026 / Decimal("2.0")).quantize(Decimal("0.01"))
    fecha_sac_2026 = date(2026, 6, 30)

    if fecha_sac_2026 <= fecha_corte:
        tx_sac_2026 = transaccion_service.crear_transaccion(
            db,
            cat.user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.INGRESO,
                monto=monto_sac_2026,
                moneda=Moneda.ARS,
                fecha=fecha_sac_2026,
                descripcion="SAC Primer Semestre 2026 (Medio Aguinaldo)",
                categoria_id=cat.cat_empleo.id,
                subcategoria_id=cat.sub_aguinaldo.id,
                metodo_pago=MetodoPago.TRANSFERENCIA,
                billetera_id=cat.b_galicia.id,
                es_recurrente=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
            commit=False,
        )
        transacciones_creadas.append(tx_sac_2026)

    # 3. Ingresos Extra Irregulares (Freelance): 4 en total, 1 en USD
    freelance_items = [
        {
            "fecha": date(2025, 9, 12),
            "monto": Decimal("320000.00"),
            "moneda": Moneda.ARS,
            "billetera": cat.b_galicia,
            "desc": "Honorarios consultoría técnica backend freelance",
            "subcat": cat.sub_honorarios,
        },
        {
            "fecha": date(2025, 12, 5),
            "monto": Decimal("450000.00"),
            "moneda": Moneda.ARS,
            "billetera": cat.b_galicia,
            "desc": "Desarrollo y entrega de módulo web cliente exterior",
            "subcat": cat.sub_venta,
        },
        {
            "fecha": date(2026, 3, 18),
            "monto": Decimal("500.00"),
            "moneda": Moneda.USD,
            "billetera": cat.b_efectivo_usd,
            "desc": "Cobro freelance diseño de arquitectura cloud USD",
            "subcat": cat.sub_honorarios,
        },
        {
            "fecha": date(2026, 7, 14),
            "monto": Decimal("380000.00"),
            "moneda": Moneda.ARS,
            "billetera": cat.b_galicia,
            "desc": "Auditoría de seguridad y optimización de base de datos",
            "subcat": cat.sub_honorarios,
        },
    ]

    freelance_registrados = []
    for item in freelance_items:
        if item["fecha"] > fecha_corte:
            continue
        tx_f = transaccion_service.crear_transaccion(
            db,
            cat.user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.INGRESO,
                monto=item["monto"],
                moneda=item["moneda"],
                fecha=item["fecha"],
                descripcion=item["desc"],
                categoria_id=cat.cat_indep.id,
                subcategoria_id=item["subcat"].id,
                metodo_pago=MetodoPago.EFECTIVO if item["billetera"].es_efectivo else MetodoPago.TRANSFERENCIA,
                billetera_id=item["billetera"].id,
                es_recurrente=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
            commit=False,
        )
        transacciones_creadas.append(tx_f)
        freelance_registrados.append({
            "fecha": item["fecha"].isoformat(),
            "monto": str(item["monto"]),
            "moneda": item["moneda"].value,
            "descripcion": item["desc"],
            "billetera": item["billetera"].nombre,
        })

    db.flush()

    return {
        "transacciones": transacciones_creadas,
        "verdad_ingresos": {
            "tipo_ingreso_principal": "Relación de dependencia (CABA)",
            "sueldo_neto_inicial": "2600000.00",
            "sueldos_por_ciclo": sueldos_por_ciclo,
            "aguinaldos": [
                {
                    "semestre": "2025-S2",
                    "fecha": fecha_sac_2025.isoformat(),
                    "monto": str(monto_sac_2025),
                },
                {
                    "semestre": "2026-S1",
                    "fecha": fecha_sac_2026.isoformat(),
                    "monto": str(monto_sac_2026),
                },
            ],
            "freelance_extra": freelance_registrados,
            "es_recurrente_en_ingresos": False,
        },
    }
