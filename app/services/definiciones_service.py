"""
Módulo Unificado de Definiciones Financieras de Argentum.
Ubicación: app/services/definiciones_service.py

PROPÓSITO Y RESPALDO CONCEPTUAL:
Establece UN SOLO criterio determinístico y universal para definir qué es gasto,
qué es ingreso y qué períodos temporales rigen en todas las pantallas (Dashboard,
Presupuestos, Análisis Financiero, Notificaciones, Transacciones) y en WhatsApp.

RESPALDO METODOLÓGICO (Filosofía YNAB / Contabilidad de Devengamiento):
1. Las compras realizadas con tarjeta de crédito se devengan y contabilizan como
   gasto en el momento exacto de la compra (cuando se adquiere el bien/servicio
   y se contrae la obligación financiera), no cuando se liquida el resumen bancario.
2. El pago posterior del resumen de tarjeta de crédito es una transferencia patrimonial
   o movimiento entre cuentas (cancelación de pasivo financiero mediante activos líquidos
   a la vista), por lo que NO constituye un nuevo gasto de consumo (evitando doble cómputo).
3. Se elimina cualquier heurística basada en adivinar palabras en la descripción.
   Todo egreso operativo que no esté taxativamente excluido es gasto de consumo
   (por ejemplo, los préstamos bancarios pasan a ser gasto en lugar de ser ignorados).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional, Set, Dict, Tuple
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.grupo_cuotas import GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda, Usuario
from app.utils.fecha import hoy_argentina


# ==============================================================================
# 4.1 CONDICIONES SQLALCHEMY PARA CONSULTAS EN BASE DE DATOS
# ==============================================================================

def condicion_gasto(
    usuario_id: UUID,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    moneda: Optional[Moneda | str] = None,
    hoy: Optional[date] = None,
) -> BooleanClauseList:
    """
    Construye la expresión SQLAlchemy (.where()) que filtra qué transacciones
    son gastos de consumo válidos. Utiliza subconsultas desacopladas para no
    exigir joins previos a quien invoque la función.

    REGLAS EXACTAS DE GASTO:
    1. tipo == egreso.
    2. Su billetera no es de inversión (billeteras.es_inversion = false).
    3. No es aporte ni retiro de meta (movimiento_meta_id IS NULL).
    4. No es pago de resumen de tarjeta (pago_resumen_vencimiento IS NULL).
       La percepción por moneda extranjera y el prepago de cuotas sí son gasto.
    5. Categoría y Subcategoría:
       - Su categoría no es 'Ahorro' (resuelto dinámicamente por ID del catálogo).
       - Su subcategoría no es 'Tarjeta de crédito' de la categoría 'Banco'.
    6. Tarjeta y cuotas:
       - Padre de cuotas (es_padre_cuotas): cuenta solo si cantidad_cuotas == 1.
       - Cuota hija (es_cuota_hija): cuenta solo si cantidad_cuotas > 1.
       - Transacciones normales (no padre ni hija): cuentan siempre.
    7. Estado de verificación:
       - Si es cuota hija: cuenta siempre (incluso pendiente).
       - Si no es cuota hija: estado nulo o 'confirmada'.
    8. Fecha:
       - Cuenta en la fecha registrada en la fila (vencimiento de la cuota o compra del padre).
    9. Fecha devengada:
       - Solo cuenta si fecha <= hoy en Argentina (no transacciones futuras).
    """
    ref_hoy = hoy or hoy_argentina()

    billeteras_validas_sq = (
        select(Billetera.id).where(Billetera.es_inversion.is_(False))
    )
    cat_ahorro_sq = (
        select(Categoria.id).where(func.lower(Categoria.nombre) == "ahorro")
    )
    subcat_tc_sq = (
        select(Subcategoria.id)
        .join(Categoria, Subcategoria.categoria_id == Categoria.id)
        .where(
            func.lower(Categoria.nombre) == "banco",
            func.lower(Subcategoria.nombre) == "tarjeta de crédito",
        )
    )
    cant_cuotas_sq = (
        select(GrupoCuotas.cantidad_cuotas)
        .where(GrupoCuotas.id == Transaccion.grupo_cuotas_id)
        .scalar_subquery()
    )

    conds = [
        Transaccion.usuario_id == usuario_id,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.fecha <= ref_hoy,
        Transaccion.billetera_id.in_(billeteras_validas_sq),
        Transaccion.movimiento_meta_id.is_(None),
        Transaccion.pago_resumen_vencimiento.is_(None),
        or_(
            Transaccion.categoria_id.is_(None),
            Transaccion.categoria_id.not_in(cat_ahorro_sq),
        ),
        or_(
            Transaccion.subcategoria_id.is_(None),
            Transaccion.subcategoria_id.not_in(subcat_tc_sq),
        ),
        or_(
            and_(
                Transaccion.es_padre_cuotas.is_(False),
                Transaccion.es_cuota_hija.is_(False),
            ),
            and_(Transaccion.es_padre_cuotas.is_(True), cant_cuotas_sq == 1),
            and_(Transaccion.es_cuota_hija.is_(True), cant_cuotas_sq > 1),
        ),
        or_(
            Transaccion.es_cuota_hija.is_(True),
            Transaccion.estado_verificacion.is_(None),
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
        ),
    ]

    if desde is not None:
        conds.append(Transaccion.fecha >= desde)
    if hasta is not None:
        conds.append(Transaccion.fecha <= hasta)
    if moneda is not None:
        moneda_val = moneda.value if hasattr(moneda, "value") else moneda
        conds.append(Transaccion.moneda == moneda_val)

    return and_(*conds)


def condicion_ingreso(
    usuario_id: UUID,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    moneda: Optional[Moneda | str] = None,
    hoy: Optional[date] = None,
) -> BooleanClauseList:
    """
    Construye la expresión SQLAlchemy (.where()) que filtra qué transacciones
    son ingresos genuinos válidos.

    REGLAS EXACTAS DE INGRESO:
    1. tipo == ingreso.
    2. Su billetera no es de inversión (billeteras.es_inversion = false).
    3. No es aporte ni retiro de meta (movimiento_meta_id IS NULL).
    7. Estado: estado nulo o 'confirmada'.
    9. Solo cuenta si fecha <= hoy en Argentina.
    """
    ref_hoy = hoy or hoy_argentina()
    billeteras_validas_sq = (
        select(Billetera.id).where(Billetera.es_inversion.is_(False))
    )
    cat_ahorro_sq = (
        select(Categoria.id).where(func.lower(Categoria.nombre) == "ahorro")
    )

    conds = [
        Transaccion.usuario_id == usuario_id,
        Transaccion.tipo == TipoTransaccion.INGRESO,
        Transaccion.fecha <= ref_hoy,
        Transaccion.billetera_id.in_(billeteras_validas_sq),
        Transaccion.movimiento_meta_id.is_(None),
        or_(
            Transaccion.categoria_id.is_(None),
            Transaccion.categoria_id.not_in(cat_ahorro_sq),
        ),
        or_(
            Transaccion.estado_verificacion.is_(None),
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
        ),
    ]

    if desde is not None:
        conds.append(Transaccion.fecha >= desde)
    if hasta is not None:
        conds.append(Transaccion.fecha <= hasta)
    if moneda is not None:
        moneda_val = moneda.value if hasattr(moneda, "value") else moneda
        conds.append(Transaccion.moneda == moneda_val)

    return and_(*conds)


# ==============================================================================
# 4.2 CONTEXTO Y EVALUACIÓN EN MEMORIA (PYTHON)
# ==============================================================================

@dataclass(frozen=True)
class ContextoDefiniciones:
    """
    Almacena metadatos cacheados para evaluar clasificaciones financieras en memoria.
    Permite desacoplar cálculos en memoria de consultas SQL recurrentes.
    """
    grupos_cuotas_cantidades: Dict[UUID, int]
    billeteras_inversion_ids: Set[UUID]
    categoria_ahorro_ids: Set[UUID]
    subcategoria_tarjeta_id: Optional[UUID]
    subcategoria_tarjeta_ids: Set[UUID]
    hoy: date


def cargar_contexto(
    db: Session,
    usuario_id: UUID,
    hoy: Optional[date] = None,
) -> ContextoDefiniciones:
    """
    Carga el contexto con los metadatos necesarios para evaluar transacciones
    en memoria con idénticas reglas a las de SQL.
    """
    ref_hoy = hoy or hoy_argentina()

    # 1. Cantidades de cuotas por grupo
    grupos = db.execute(
        select(GrupoCuotas.id, GrupoCuotas.cantidad_cuotas).where(
            GrupoCuotas.usuario_id == usuario_id
        )
    ).all()
    grupos_map = {g[0]: g[1] for g in grupos}

    # 2. Billeteras de inversión
    b_inv = db.execute(
        select(Billetera.id).where(
            Billetera.usuario_id == usuario_id,
            Billetera.es_inversion.is_(True),
        )
    ).scalars().all()
    b_inv_set = set(b_inv)

    # 3. Categorías excluidas del catálogo
    # Si la base de datos no tiene catálogo cargado (ej. tests unitarios aislados),
    # no se lanza error y se usan conjuntos vacíos, garantizando paridad con SQL donde
    # NOT IN sobre subconsultas vacías no excluye ninguna fila.
    cat_ahorro = db.execute(
        select(Categoria.id).where(func.lower(Categoria.nombre) == "ahorro")
    ).scalars().all()
    cat_ahorro_set = set(cat_ahorro)

    subcat_tc_rows = db.execute(
        select(Subcategoria.id)
        .join(Categoria, Subcategoria.categoria_id == Categoria.id)
        .where(
            func.lower(Categoria.nombre) == "banco",
            func.lower(Subcategoria.nombre) == "tarjeta de crédito",
        )
    ).scalars().all()
    subcat_tc_set = set(subcat_tc_rows)
    subcat_tc = subcat_tc_rows[0] if subcat_tc_rows else None

    return ContextoDefiniciones(
        grupos_cuotas_cantidades=grupos_map,
        billeteras_inversion_ids=b_inv_set,
        categoria_ahorro_ids=cat_ahorro_set,
        subcategoria_tarjeta_id=subcat_tc,
        subcategoria_tarjeta_ids=subcat_tc_set,
        hoy=ref_hoy,
    )


def es_gasto(tx: Any, ctx: ContextoDefiniciones) -> bool:
    """
    Determina si un objeto Transaccion en memoria cumple la regla estricta de gasto.
    Garantiza 100% de paridad con condicion_gasto de SQLAlchemy.
    """
    # 1. tipo == egreso
    tipo = getattr(tx, "tipo", None)
    tipo_val = tipo.value if hasattr(tipo, "value") else str(tipo)
    if tipo_val != "egreso":
        return False

    # 2. Billetera no es de inversión
    bid = getattr(tx, "billetera_id", None)
    if bid in ctx.billeteras_inversion_ids:
        return False
    billetera = getattr(tx, "billetera", None)
    if billetera is not None and getattr(billetera, "es_inversion", False):
        return False

    # 3. No es aporte ni retiro de meta
    if getattr(tx, "movimiento_meta_id", None) is not None:
        return False

    # 4. No es pago de resumen
    if getattr(tx, "pago_resumen_vencimiento", None) is not None:
        return False

    # 5. Categoría y Subcategoría
    cid = getattr(tx, "categoria_id", None)
    if cid in ctx.categoria_ahorro_ids:
        return False
    scid = getattr(tx, "subcategoria_id", None)
    if scid is not None and (scid in ctx.subcategoria_tarjeta_ids or scid == ctx.subcategoria_tarjeta_id):
        return False

    # 6. Tarjeta y cuotas
    es_padre = getattr(tx, "es_padre_cuotas", False)
    es_hija = getattr(tx, "es_cuota_hija", False)
    gid = getattr(tx, "grupo_cuotas_id", None)
    cant_cuotas = ctx.grupos_cuotas_cantidades.get(gid) if gid else None

    if es_padre:
        if cant_cuotas != 1:
            return False
    elif es_hija:
        if cant_cuotas is None or cant_cuotas <= 1:
            return False

    # 7. Estado de verificación
    if not es_hija:
        est = getattr(tx, "estado_verificacion", None)
        est_val = (
            est.value if hasattr(est, "value") else (str(est) if est is not None else None)
        )
        if est_val not in (None, "confirmada"):
            return False

    # 9. Solo cuenta si su fecha ya llegó (fecha <= hoy en Argentina)
    f = getattr(tx, "fecha", None)
    if f is not None and f > ctx.hoy:
        return False

    return True


def es_ingreso(tx: Any, ctx: ContextoDefiniciones) -> bool:
    """
    Determina si un objeto Transaccion en memoria cumple la regla estricta de ingreso.
    Garantiza 100% de paridad con condicion_ingreso de SQLAlchemy.
    """
    # 1. tipo == ingreso
    tipo = getattr(tx, "tipo", None)
    tipo_val = tipo.value if hasattr(tipo, "value") else str(tipo)
    if tipo_val != "ingreso":
        return False

    # 2. Billetera no es de inversión
    bid = getattr(tx, "billetera_id", None)
    if bid in ctx.billeteras_inversion_ids:
        return False
    billetera = getattr(tx, "billetera", None)
    if billetera is not None and getattr(billetera, "es_inversion", False):
        return False

    # 3. No es aporte ni retiro de meta
    if getattr(tx, "movimiento_meta_id", None) is not None:
        return False

    # 5. Categoría no es "Ahorro"
    cid = getattr(tx, "categoria_id", None)
    if cid in ctx.categoria_ahorro_ids:
        return False

    # 7. Estado de verificación
    est = getattr(tx, "estado_verificacion", None)
    est_val = (
        est.value if hasattr(est, "value") else (str(est) if est is not None else None)
    )
    if est_val not in (None, "confirmada"):
        return False

    # 9. Solo cuenta si su fecha ya llegó (fecha <= hoy en Argentina)
    f = getattr(tx, "fecha", None)
    if f is not None and f > ctx.hoy:
        return False

    return True


# ==============================================================================
# 4.3 PERÍODOS DE CICLO
# ==============================================================================

def rango_ciclo(
    usuario: Usuario,
    hoy: Optional[date] = None,
) -> Tuple[date, date]:
    """
    Retorna la tupla (fecha_inicio, fecha_fin) del ciclo al que pertenece hoy
    según la parametrización de ciclo del usuario en get_ciclo_fechas.
    """
    from app.services.dashboard_service import get_ciclo_fechas

    ref_hoy = hoy or hoy_argentina()
    return get_ciclo_fechas(usuario, ref_hoy)


def rango_ciclo_anterior(
    usuario: Usuario,
    hoy: Optional[date] = None,
) -> Tuple[date, date]:
    """
    Retorna la tupla (fecha_inicio, fecha_fin) del ciclo inmediato anterior
    al ciclo al que pertenece hoy.
    """
    from app.services.dashboard_service import get_ciclo_fechas

    ref_hoy = hoy or hoy_argentina()
    inicio_act, _ = get_ciclo_fechas(usuario, ref_hoy)
    return get_ciclo_fechas(usuario, inicio_act - timedelta(days=1))
