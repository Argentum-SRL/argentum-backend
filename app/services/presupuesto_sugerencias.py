# -*- coding: utf-8 -*-
"""
app/services/presupuesto_sugerencias.py
Lecturas de ritmo y sugerencias de presupuesto para API y consejos.
Solo lectura: sin commit, flush ni llamadas de red.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.categoria import Categoria, TipoCategoria
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import EstadoPresupuesto, Presupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import Transaccion
from app.models.usuario import Usuario
from app.services.consejos_reglas import (
    RitmoPresupuesto,
    RubroSinPresupuesto,
    a_precios_de_hoy,
    elegir_rubro_sin_presupuesto,
)
from app.services.dashboard_service import get_ciclo_fechas
from app.services.gasto_filas import ciclos_cerrados, filas_gasto, indices_ipc
from app.services.presupuesto_service import (
    contar_compras_en_periodo,
    gasto_fijo_en_periodo,
    obtener_periodo_activo,
    uso_periodo,
)
from app.services.presupuesto_sugerencias_reglas import (
    SugerenciaLimite,
    proyectar_ritmo,
    sugerir_limite,
)
from app.utils.fecha import hoy_argentina


def ritmo_presupuesto(
    db: Session,
    presupuesto: Presupuesto,
    periodo: PeriodoPresupuesto,
    calculado: Decimal,
    hoy: date,
    usuario: Optional[Usuario] = None,
) -> RitmoPresupuesto | None:
    """Calcula la proyección de ritmo para un presupuesto activo."""
    if presupuesto.estado != EstadoPresupuesto.ACTIVO:
        return None

    usr = usuario or presupuesto.usuario or db.get(Usuario, presupuesto.usuario_id)
    moneda_str = (
        presupuesto.moneda.value
        if hasattr(presupuesto.moneda, "value")
        else str(presupuesto.moneda)
    )

    fijo = gasto_fijo_en_periodo(
        db,
        presupuesto,
        periodo.fecha_inicio,
        periodo.fecha_fin,
        hoy=hoy,
        usuario=usr,
    )

    if hoy < periodo.fecha_fin:
        pendiente = gasto_fijo_en_periodo(
            db,
            presupuesto,
            fecha_inicio=hoy + timedelta(days=1),
            fecha_fin=periodo.fecha_fin,
            hoy=periodo.fecha_fin,
            usuario=usr,
        )
    else:
        pendiente = Decimal("0")

    limite = Decimal(str(periodo.monto_limite))
    p = proyectar_ritmo(
        calculado,
        fijo,
        pendiente,
        periodo.fecha_inicio,
        periodo.fecha_fin,
        hoy,
        limite,
        moneda_str,
    )
    if p is None:
        return None

    return RitmoPresupuesto(
        presupuesto_id=str(presupuesto.id),
        nombre=presupuesto.nombre,
        moneda=moneda_str,
        inicio=periodo.fecha_inicio,
        fin=periodo.fecha_fin,
        proyeccion=p,
        limite=limite,
    )


def sugerencia_limite(
    db: Session,
    presupuesto: Presupuesto,
    periodo: PeriodoPresupuesto,
    calculado: Decimal,
    hoy: date,
    usuario: Optional[Usuario] = None,
) -> SugerenciaLimite | None:
    """Evalúa si sugerir un ajuste de límite hacia arriba."""
    if presupuesto.estado != EstadoPresupuesto.ACTIVO:
        return None

    usr = usuario or presupuesto.usuario or db.get(Usuario, presupuesto.usuario_id)
    moneda_str = (
        presupuesto.moneda.value
        if hasattr(presupuesto.moneda, "value")
        else str(presupuesto.moneda)
    )
    periodo_tipo_str = (
        presupuesto.periodo.value
        if hasattr(presupuesto.periodo, "value")
        else str(presupuesto.periodo)
    )

    previos_periodos = [
        p for p in presupuesto.periodos
        if p.fecha_fin < periodo.fecha_inicio
    ]
    previos_periodos.sort(key=lambda p: p.fecha_fin, reverse=True)
    previos_periodos = previos_periodos[:3]

    previos: list[tuple[Decimal, Decimal, int]] = []
    for p in previos_periodos:
        calc = uso_periodo(db, presupuesto, p, hoy=hoy, usuario=usr).calculado
        lim = Decimal(str(p.monto_limite))
        compras = contar_compras_en_periodo(
            db,
            presupuesto.usuario_id,
            presupuesto.categorias,
            p.fecha_inicio,
            p.fecha_fin,
            hoy=hoy,
        )
        previos.append((calc, lim, compras))

    compras_actual = contar_compras_en_periodo(
        db,
        presupuesto.usuario_id,
        presupuesto.categorias,
        periodo.fecha_inicio,
        periodo.fecha_fin,
        hoy=hoy,
    )

    limite_actual = Decimal(str(periodo.monto_limite))
    return sugerir_limite(
        periodo_tipo_str,
        limite_actual,
        calculado,
        compras_actual,
        previos,
        moneda_str,
    )


def ritmos_activos(
    db: Session,
    usuario: Usuario,
    hoy: Optional[date] = None,
) -> tuple[RitmoPresupuesto, ...]:
    """Obtiene los ritmos proyectados para todos los presupuestos activos del usuario."""
    if hoy is None:
        hoy = hoy_argentina()
    presupuestos = (
        db.query(Presupuesto)
        .options(
            selectinload(Presupuesto.periodos),
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.categoria),
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.subcategoria),
        )
        .filter(
            Presupuesto.usuario_id == usuario.id,
            Presupuesto.estado == EstadoPresupuesto.ACTIVO,
            Presupuesto.eliminado_en.is_(None),
        )
        .order_by(Presupuesto.nombre)
        .all()
    )

    ritmos: list[RitmoPresupuesto] = []
    for p in presupuestos:
        periodo = obtener_periodo_activo(db, p, hoy=hoy)
        if periodo is not None:
            uso = uso_periodo(db, p, periodo, hoy=hoy, usuario=usuario)
            r = ritmo_presupuesto(db, p, periodo, uso.calculado, hoy, usuario)
            if r is not None:
                ritmos.append(r)

    ritmos.sort(key=lambda x: x.nombre)
    return tuple(ritmos)


def categorias_con_presupuesto(
    db: Session,
    usuario_id: UUID,
) -> tuple[frozenset[str], frozenset[str]]:
    """Devuelve categorías cubiertas y nombres en minúscula de presupuestos activos/pausados."""
    presupuestos = (
        db.query(Presupuesto)
        .options(
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.categoria),
            selectinload(Presupuesto.categorias)
            .joinedload(PresupuestoCategoria.subcategoria)
            .joinedload(Subcategoria.categoria),
        )
        .filter(
            Presupuesto.usuario_id == usuario_id,
            Presupuesto.estado.in_([EstadoPresupuesto.ACTIVO, EstadoPresupuesto.PAUSADO]),
            Presupuesto.eliminado_en.is_(None),
        )
        .all()
    )

    categorias: set[str] = set()
    nombres: set[str] = set()

    for p in presupuestos:
        nombres.add(p.nombre.lower())
        for pc in p.categorias:
            if pc.subcategoria and pc.subcategoria.categoria:
                categorias.add(pc.subcategoria.categoria.nombre)
            elif pc.categoria:
                categorias.add(pc.categoria.nombre)

    return frozenset(categorias), frozenset(nombres)


def ids_categorias_egreso(db: Session) -> dict[str, str]:
    """Mapea nombre de categoría de egreso a su id como texto."""
    cats = db.query(Categoria).filter(Categoria.tipo == TipoCategoria.EGRESO).all()
    return {c.nombre: str(c.id) for c in cats}


def rubro_sin_presupuesto(
    db: Session,
    usuario: Usuario,
    hoy: Optional[date] = None,
) -> tuple[RubroSinPresupuesto, str] | None:
    """Detecta rubros habituales sin presupuesto asignado para el usuario."""
    if hoy is None:
        hoy = hoy_argentina()
    min_fecha = db.query(func.min(Transaccion.fecha)).filter(
        Transaccion.usuario_id == usuario.id
    ).scalar()
    if min_fecha is None:
        return None

    inicio_actual = get_ciclo_fechas(usuario, hoy)[0]
    cerrados = ciclos_cerrados(usuario, inicio_actual, min_fecha, hasta=3)
    if len(cerrados) < 3:
        return None

    filas = a_precios_de_hoy(
        filas_gasto(db, usuario.id, cerrados[-1][0], cerrados[0][1], hoy),
        indices_ipc(db),
        hoy,
    )
    cubiertas, nombres = categorias_con_presupuesto(db, usuario.id)

    rubro = elegir_rubro_sin_presupuesto(filas, cerrados[:3], cubiertas, nombres, "ARS")
    if rubro is None:
        rubro = elegir_rubro_sin_presupuesto(filas, cerrados[:3], cubiertas, nombres, "USD")

    if rubro is None:
        return None

    ids_egreso = ids_categorias_egreso(db)
    if rubro.categoria in ids_egreso:
        return rubro, ids_egreso[rubro.categoria]

    return None
