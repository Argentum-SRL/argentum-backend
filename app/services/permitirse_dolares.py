# -*- coding: utf-8 -*-
"""
app/services/permitirse_dolares.py — Dólares en "¿Me lo puedo permitir?" (bloque T1, decisión 2B).

Los dólares de las cuentas comunes son plata de todos los días solo para quien cobra o gasta
en dólares seguido. Para quien solo compra y guarda dólares siguen siendo ahorro.
Todo es de solo lectura: sin commit, sin flush y sin llamadas de red.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.grupo_cuotas import GrupoCuotas
from app.models.transaccion import MetodoPago, Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.services.dolar_service import obtener_cotizaciones_por_fechas
from app.services.fin_de_mes_service import rango_fin_de_mes
from app.services.ingreso_habitual_service import obtener_ingreso_habitual

CENTAVO = Decimal("0.01")


@dataclass(frozen=True)
class DatosDolares:
    """Lo que el motor necesita saber de los dólares de la persona."""
    usa: bool
    ingreso_usd: Decimal | None


def _ciclos_cerrados_recientes(
    usuario: Usuario,
    hoy: date,
    cantidad: int,
) -> list[tuple[date, date]]:
    """Los últimos ciclos cerrados antes del ciclo en curso, del más nuevo al más viejo."""
    inicio_actual, _ = get_ciclo_fechas(usuario, hoy)
    ciclos: list[tuple[date, date]] = []
    puntero = inicio_actual - timedelta(days=1)
    for _ in range(cantidad):
        inicio, fin = get_ciclo_fechas(usuario, puntero)
        ciclos.append((inicio, fin))
        puntero = inicio - timedelta(days=1)
    return ciclos


def datos_dolares(db: Session, usuario: Usuario, hoy: date) -> DatosDolares:
    """
    Dice si la persona usa dólares seguido (D-T1.4) y cuánto cobra en dólares.
    Usa dólares seguido si su moneda principal es dólares, si tiene ingreso habitual en dólares
    o si tuvo gastos en dólares en al menos 2 de sus últimos 3 ciclos cerrados.
    """
    principal = usuario.moneda_principal
    principal_str = principal.value if hasattr(principal, "value") else str(principal or "").upper()
    es_principal = principal_str == "USD"

    hay_dolares = db.execute(
        select(func.count(Transaccion.id)).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.moneda == Moneda.USD,
        )
    ).scalar() or 0
    if not hay_dolares:
        return DatosDolares(usa=es_principal, ingreso_usd=None)

    res_usd = obtener_ingreso_habitual(db, usuario, hoy=hoy, moneda="USD")
    ingreso_usd = Decimal(str(res_usd.monto)) if res_usd.monto is not None else None
    if es_principal or ingreso_usd is not None:
        return DatosDolares(usa=True, ingreso_usd=ingreso_usd)

    ciclos_con_gasto = 0
    for inicio, fin in _ciclos_cerrados_recientes(usuario, hoy, 3):
        cond = condicion_gasto(usuario.id, inicio, fin, moneda=Moneda.USD, hoy=hoy)
        cantidad = db.execute(select(func.count(Transaccion.id)).where(cond)).scalar() or 0
        if cantidad > 0:
            ciclos_con_gasto += 1
    return DatosDolares(usa=ciclos_con_gasto >= 2, ingreso_usd=None)


def usa_dolares_seguido(db: Session, usuario: Usuario, hoy: date) -> bool:
    """True si los dólares de las cuentas comunes de la persona son plata de todos los días."""
    return datos_dolares(db, usuario, hoy).usa


def _compra_por_fechas(db: Session, fechas: set[date], compra_hoy: Decimal) -> dict[date, Decimal]:
    """
    Dólar oficial compra de cada día; si ese día no hay, la última anterior.
    Si tampoco hay una anterior, se usa la de hoy (nunca se inventa un valor fijo).
    """
    cotizaciones = obtener_cotizaciones_por_fechas(db, "oficial", fechas)
    salida: dict[date, Decimal] = {}
    for f in fechas:
        cot = cotizaciones.get(f)
        if cot is not None and cot.compra is not None:
            salida[f] = Decimal(str(cot.compra))
        else:
            salida[f] = compra_hoy
    return salida


def movimientos_dolares_en_pesos(
    db: Session,
    usuario: Usuario,
    ciclos: list[tuple[date, date]],
    hoy: date,
    compra_hoy: Decimal,
    ids_excluidos: set[UUID] | None = None,
) -> list[tuple[Decimal, Decimal, int]]:
    """
    Para cada ciclo, (ingresos, gastos variables, cantidad de movimientos) en dólares,
    pasados a pesos con el dólar oficial compra del día de cada movimiento.
    El gasto variable sigue el criterio de gastos_variables_por_ciclo: sin cuotas de compras
    de 2 o más cuotas, sin suscripciones y sin los fijos que se le pasan en ids_excluidos.
    """
    if not ciclos:
        return []
    excluidos = ids_excluidos or set()
    desde = min(c[0] for c in ciclos)
    hasta = max(c[1] for c in ciclos)

    ingresos = db.execute(
        select(Transaccion).where(
            condicion_ingreso(usuario.id, desde, hasta, moneda=Moneda.USD, hoy=hoy)
        )
    ).scalars().all()
    gastos = db.execute(
        select(Transaccion).where(
            condicion_gasto(usuario.id, desde, hasta, moneda=Moneda.USD, hoy=hoy)
        )
    ).scalars().all()
    grupos_varias_cuotas = set(
        db.execute(
            select(GrupoCuotas.id).where(
                GrupoCuotas.usuario_id == usuario.id,
                GrupoCuotas.cantidad_cuotas > 1,
            )
        ).scalars().all()
    )
    gastos_variables = [
        t for t in gastos
        if not (t.es_cuota_hija and t.grupo_cuotas_id in grupos_varias_cuotas)
        and t.suscripcion_id is None
        and t.id not in excluidos
    ]

    fechas = {t.fecha for t in ingresos} | {t.fecha for t in gastos_variables}
    compra = _compra_por_fechas(db, fechas, compra_hoy) if fechas else {}

    def en_pesos(t: Any) -> Decimal:
        return Decimal(str(t.monto)) * compra[t.fecha]

    salida: list[tuple[Decimal, Decimal, int]] = []
    for inicio, fin in ciclos:
        ing = [t for t in ingresos if inicio <= t.fecha <= fin]
        gas = [t for t in gastos_variables if inicio <= t.fecha <= fin]
        total_ing = sum((en_pesos(t) for t in ing), Decimal("0"))
        total_gas = sum((en_pesos(t) for t in gas), Decimal("0"))
        salida.append((
            total_ing.quantize(CENTAVO, rounding=ROUND_HALF_UP),
            total_gas.quantize(CENTAVO, rounding=ROUND_HALF_UP),
            len(ing) + len(gas),
        ))
    return salida


def finales_de_quien_cobra_en_dolares(
    db: Session,
    usuario: Usuario,
    fdm: Any,
    hoy: date,
) -> tuple[Decimal, ...]:
    """
    Finales de fin de mes en pesos para quien cobra en dólares y no tiene ingreso en pesos.
    calcular_fin_de_mes los deja vacíos (estado "sin_ingreso") porque solo mira el ingreso
    en pesos; acá se arman con la misma cuenta (rango_fin_de_mes) y el mismo gasto por ciclo
    (pesos, sin crédito, sin suscripciones y sin cuotas). Quien llama les suma los dólares.
    """
    primera = db.execute(
        select(func.min(Transaccion.fecha)).where(Transaccion.usuario_id == usuario.id)
    ).scalar()
    if primera is None:
        return ()

    ciclos_fechas: list[tuple[date, date]] = []
    fin_k = fdm.inicio_ciclo - timedelta(days=1)
    for _ in range(12):
        inicio_k, fin_k_calc = get_ciclo_fechas(usuario, fin_k)
        if inicio_k < primera:
            break
        ciclos_fechas.append((inicio_k, fin_k_calc))
        fin_k = inicio_k - timedelta(days=1)
    if not ciclos_fechas:
        return ()

    cond = condicion_gasto(
        usuario.id, ciclos_fechas[-1][0], ciclos_fechas[0][1], moneda="ARS", hoy=hoy
    )
    filas = db.execute(
        select(Transaccion.fecha, func.sum(Transaccion.monto))
        .where(
            cond,
            or_(Transaccion.metodo_pago.is_(None), Transaccion.metodo_pago != MetodoPago.CREDITO),
            Transaccion.suscripcion_id.is_(None),
            Transaccion.es_cuota_hija.is_(False),
        )
        .group_by(Transaccion.fecha)
    ).all()

    ciclos: list[tuple[Decimal, int]] = []
    for inicio_k, fin_k_calc in ciclos_fechas:
        gasto_k = sum(
            (Decimal(str(m)) for f, m in filas if inicio_k <= f <= fin_k_calc and m is not None),
            Decimal("0"),
        )
        ciclos.append((gasto_k, (fin_k_calc - inicio_k).days + 1))

    rango = rango_fin_de_mes(
        libre=fdm.libre_ars,
        dias_restantes=(fdm.fin_ciclo - hoy).days,
        ciclos=ciclos,
        cobertura=fdm.cobertura,
        ingreso_deducible=True,
        datos_viejos=bool(fdm.frescura.datos_viejos),
    )
    return tuple(rango.finales or ())
