# -*- coding: utf-8 -*-
"""
app/services/permitirse_rubros.py — Rubros recortables y reintegros de "¿Me lo puedo permitir?"
(bloque T1, decisión 3 A+B).

Solo se sugiere recortar de la lista RECORTABLES (nunca comida ni esenciales). Si la persona ya
tuvo reintegros en ese rubro se le dice con sus datos; si no, va un consejo general.
Todo es de solo lectura: sin commit, sin flush y sin llamadas de red.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.grupo_cuotas import GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import MetodoPago, Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto

# Subcategorías del catálogo que se pueden sugerir para recortar (nombre exacto).
RECORTABLES: tuple[str, ...] = (
    "Kiosco",
    "Restaurantes",
    "Delivery",
    "Cafetería",
    "Taxi y apps",
    "Salidas",
    "Hobbies y juegos",
    "Viajes",
    "Ropa",
    "Calzado",
    "Accesorios",
    "Peluquería y belleza",
    "Perfumería",
    "Equipos",
    "Apps y programas",
    "Muebles y electro",
)


def _mediana(valores: list[Decimal]) -> Decimal:
    """Mediana de una lista no vacía, en centavos."""
    orden = sorted(valores)
    n = len(orden)
    medio = n // 2
    if n % 2 == 1:
        return orden[medio]
    return ((orden[medio - 1] + orden[medio]) / Decimal("2")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def elegir_rubro_recortable(medianas: dict[str, Decimal]) -> str | None:
    """
    De las medianas por subcategoría, elige la recortable de mayor mediana
    (en empate, la primera en orden alfabético). Si no hay ninguna recortable, None.
    """
    elegibles = {
        nombre: valor for nombre, valor in medianas.items()
        if nombre in RECORTABLES and valor > Decimal("0")
    }
    if not elegibles:
        return None
    return sorted(elegibles.items(), key=lambda par: (-par[1], par[0]))[0][0]


def _ciclos_cerrados(usuario: Usuario, hoy: date, maximo: int) -> list[tuple[date, date]]:
    """Los últimos ciclos cerrados antes del ciclo en curso, del más nuevo al más viejo."""
    inicio_actual, _ = get_ciclo_fechas(usuario, hoy)
    ciclos: list[tuple[date, date]] = []
    puntero = inicio_actual - timedelta(days=1)
    for _ in range(maximo):
        inicio, fin = get_ciclo_fechas(usuario, puntero)
        ciclos.append((inicio, fin))
        puntero = inicio - timedelta(days=1)
    return ciclos


def medianas_recortables(
    db: Session,
    usuario: Usuario,
    hoy: date,
    ids_excluidos: set[UUID] | None = None,
) -> dict[str, Decimal]:
    """
    Mediana por ciclo de lo gastado en cada subcategoría recortable, en pesos, en los últimos
    hasta 6 ciclos cerrados con historia (mínimo 3). Deja afuera las cuotas de compras de 2 o
    más cuotas, las suscripciones y los fijos que se le pasan en ids_excluidos.
    """
    primera = db.execute(
        select(Transaccion.fecha)
        .where(Transaccion.usuario_id == usuario.id)
        .order_by(Transaccion.fecha.asc())
        .limit(1)
    ).scalar_one_or_none()
    if primera is None:
        return {}

    ciclos = [c for c in _ciclos_cerrados(usuario, hoy, 6) if c[1] >= primera]
    if len(ciclos) < 3:
        return {}

    excluidos = ids_excluidos or set()
    grupos_varias_cuotas = set(
        db.execute(
            select(GrupoCuotas.id).where(
                GrupoCuotas.usuario_id == usuario.id,
                GrupoCuotas.cantidad_cuotas > 1,
            )
        ).scalars().all()
    )
    cond = condicion_gasto(usuario.id, ciclos[-1][0], ciclos[0][1], hoy=hoy)
    filas = db.execute(
        select(Transaccion, Subcategoria.nombre)
        .join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
        .where(cond, Transaccion.moneda == Moneda.ARS, Subcategoria.nombre.in_(RECORTABLES))
    ).all()

    por_rubro: dict[str, list[Decimal]] = {}
    for indice, (inicio, fin) in enumerate(ciclos):
        for tx, nombre in filas:
            if not (inicio <= tx.fecha <= fin):
                continue
            if tx.es_cuota_hija and tx.grupo_cuotas_id in grupos_varias_cuotas:
                continue
            if tx.suscripcion_id is not None or tx.id in excluidos:
                continue
            totales = por_rubro.setdefault(nombre, [Decimal("0.00")] * len(ciclos))
            totales[indice] += Decimal(str(tx.monto))

    medianas: dict[str, Decimal] = {}
    for nombre, totales in por_rubro.items():
        valor = _mediana(totales)
        if valor > Decimal("0.00"):
            medianas[nombre] = valor
    return medianas


def reintegro_del_rubro(
    db: Session,
    usuario: Usuario,
    hoy: date,
    rubro: str,
) -> tuple[Decimal, str] | None:
    """
    Reintegros que la persona ya recibió en ese rubro en los últimos 6 ciclos cerrados más el
    actual: devuelve (total, medio) del medio de pago con más total, o None si no tuvo.
    El medio es el nombre de la tarjeta si el reintegro fue con crédito; si no, el de la billetera.
    """
    desde = _ciclos_cerrados(usuario, hoy, 6)[-1][0]
    filas = db.execute(
        select(Transaccion, Billetera.nombre, TarjetaCredito.nombre)
        .join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
        .join(Billetera, Transaccion.billetera_id == Billetera.id)
        .outerjoin(TarjetaCredito, Transaccion.tarjeta_id == TarjetaCredito.id)
        .where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo_espejo == "reintegro",
            Transaccion.reintegro_de_id.isnot(None),
            Transaccion.es_cuota_hija.is_(False),
            Transaccion.moneda == Moneda.ARS,
            Transaccion.fecha >= desde,
            Transaccion.fecha <= hoy,
            Subcategoria.nombre == rubro,
        )
    ).all()

    por_medio: dict[str, Decimal] = {}
    for tx, nombre_billetera, nombre_tarjeta in filas:
        es_credito = tx.metodo_pago == MetodoPago.CREDITO and nombre_tarjeta
        medio = nombre_tarjeta if es_credito else nombre_billetera
        por_medio[medio] = por_medio.get(medio, Decimal("0.00")) + abs(Decimal(str(tx.monto)))
    if not por_medio:
        return None
    medio, total = sorted(por_medio.items(), key=lambda par: (-par[1], par[0]))[0]
    return (total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), medio)
