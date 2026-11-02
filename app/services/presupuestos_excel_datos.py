# -*- coding: utf-8 -*-
"""
Servicio de extracción y estructuración de datos para el Excel de presupuestos.
Ubicación: app/services/presupuestos_excel_datos.py
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Tuple, Dict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.categoria import Categoria
from app.models.presupuesto import Presupuesto, PeriodoPresupuestoTipo
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.services.presupuesto_service import uso_periodo

MESES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
)


def nombre_hoja_ciclo(inicio: date, fin: date) -> str:
    """
    Calcula el nombre de la hoja según X-3: el mes que más días abarca el ciclo.
    Empate de días: el mes de la fecha de fin.
    """
    conteo: Counter[Tuple[int, int]] = Counter()
    curr = inicio
    while curr <= fin:
        conteo[(curr.year, curr.month)] += 1
        curr += timedelta(days=1)

    max_dias = -1
    mejor_anio, mejor_mes = fin.year, fin.month
    for (anio, mes), dias in conteo.items():
        if dias > max_dias:
            max_dias = dias
            mejor_anio, mejor_mes = anio, mes
        elif dias == max_dias:
            if (anio, mes) == (fin.year, fin.month):
                mejor_anio, mejor_mes = anio, mes

    return f"{MESES[mejor_mes - 1]} {mejor_anio}"


def nombres_unicos(nombres: list[str]) -> list[str]:
    """
    Desambigua nombres repetidos agregando ' (2)', ' (3)' según X-3.
    """
    resultado = []
    conteo: Counter[str] = Counter()
    for nom in nombres:
        conteo[nom] += 1
        if conteo[nom] == 1:
            resultado.append(nom)
        else:
            resultado.append(f"{nom} ({conteo[nom]})")
    return resultado


@dataclass(frozen=True)
class FilaPresupuesto:
    nombre: str
    incluye: str
    periodo: str
    moneda: str
    inicio: date
    limite: Decimal
    gastado: Decimal


@dataclass(frozen=True)
class FilaCategoria:
    nombre: str
    gastado: Decimal
    mes_anterior: Decimal


@dataclass(frozen=True)
class FilaSubcategoria:
    categoria: str
    subcategoria: str
    gastado: Decimal


@dataclass(frozen=True)
class FilaUsd:
    nombre: str
    gastado: Decimal


@dataclass(frozen=True)
class CicloExcel:
    inicio: date
    fin: date
    nombre_hoja: str
    en_curso: bool
    ingresos_ars: Decimal
    gastos_ars: Decimal
    ingresos_usd: Decimal
    gastos_usd: Decimal
    presupuestos: Tuple[FilaPresupuesto, ...]
    categorias: Tuple[FilaCategoria, ...]
    subcategorias: Tuple[FilaSubcategoria, ...]
    categorias_usd: Tuple[FilaUsd, ...]
    gasto_ars_por_categoria: Dict[str, Decimal]
    gasto_usd_por_categoria: Dict[str, Decimal]


@dataclass(frozen=True)
class LibroDatos:
    hoy: date
    ciclos: Tuple[CicloExcel, ...]
    tiene_dolares: bool
    uso_mensual: Tuple[Tuple[str, Tuple[Optional[Decimal], ...]], ...]


def ciclos_a_exportar(db: Session, usuario: Usuario, alcance: str, hoy: date) -> list[tuple[date, date]]:
    """
    Calcula los ciclos candidatos según X-1 (ordenados del más viejo al más nuevo).
    """
    if alcance not in ("12", "todo"):
        raise ValueError("El alcance tiene que ser 12 o todo.")

    ciclo_actual = get_ciclo_fechas(usuario, hoy)

    if alcance == "12":
        ciclos = [ciclo_actual]
        curr_ini, _ = ciclo_actual
        for _ in range(11):
            prev_ciclo = get_ciclo_fechas(usuario, curr_ini - timedelta(days=1))
            ciclos.append(prev_ciclo)
            curr_ini, _ = prev_ciclo
        ciclos.reverse()
        return ciclos

    # todo
    cond = or_(
        condicion_gasto(usuario_id=usuario.id, hoy=hoy),
        condicion_ingreso(usuario_id=usuario.id, hoy=hoy),
    )
    min_fecha = db.execute(select(func.min(Transaccion.fecha)).where(cond)).scalar()
    if min_fecha is None:
        return [ciclo_actual]

    ciclo_mas_viejo = get_ciclo_fechas(usuario, min_fecha)
    ciclos = [ciclo_actual]
    curr_ini, _ = ciclo_actual
    tope = 120

    while len(ciclos) < tope:
        if curr_ini <= ciclo_mas_viejo[0]:
            break
        prev_ciclo = get_ciclo_fechas(usuario, curr_ini - timedelta(days=1))
        ciclos.append(prev_ciclo)
        curr_ini, _ = prev_ciclo
        if prev_ciclo == ciclo_mas_viejo:
            break

    ciclos.reverse()
    return ciclos


def armar_datos_excel(db: Session, usuario: Usuario, alcance: str, hoy: date) -> Optional[LibroDatos]:
    """
    Construye la estructura inmutable de datos para el libro de Excel.
    Solo realiza lecturas (nunca crea períodos ni modifica la base de datos).
    Devuelve None si ningún ciclo tiene datos.
    """
    candidatos = ciclos_a_exportar(db, usuario, alcance, hoy)

    # Cargar presupuestos del usuario activos/pausados no eliminados con sus relaciones
    stmt_pres = (
        select(Presupuesto)
        .options(
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.categoria),
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.subcategoria),
            selectinload(Presupuesto.periodos),
            joinedload(Presupuesto.usuario),
        )
        .where(
            Presupuesto.usuario_id == usuario.id,
            Presupuesto.eliminado_en.is_(None),
        )
    )
    presupuestos_usuario: List[Presupuesto] = db.execute(stmt_pres).scalars().all()

    # Filtrar ciclos con datos (X-2)
    ciclos_validos_info = []
    for inicio, fin in candidatos:
        hasta = min(fin, hoy)
        cond = or_(
            condicion_gasto(usuario_id=usuario.id, desde=inicio, hasta=hasta, hoy=hoy),
            condicion_ingreso(usuario_id=usuario.id, desde=inicio, hasta=hasta, hoy=hoy),
        )
        cnt = db.execute(select(func.count(Transaccion.id)).where(cond)).scalar() or 0
        if cnt > 0:
            ciclos_validos_info.append((inicio, fin))

    if not ciclos_validos_info:
        return None

    # Asignar nombres únicos a las hojas
    nombres_base = [nombre_hoja_ciclo(ini, fin) for ini, fin in ciclos_validos_info]
    nombres_hojas = nombres_unicos(nombres_base)

    ciclos_excel: List[CicloExcel] = []

    for (inicio, fin), nombre_hoja in zip(ciclos_validos_info, nombres_hojas):
        hasta = min(fin, hoy)
        en_curso = fin > hoy

        # Ingresos agrupados por moneda
        ing_stmt = (
            select(
                Transaccion.moneda,
                func.sum(Transaccion.monto).label("total"),
            )
            .where(condicion_ingreso(usuario_id=usuario.id, desde=inicio, hasta=hasta, hoy=hoy))
            .group_by(Transaccion.moneda)
        )
        ing_rows = db.execute(ing_stmt).all()
        ingresos_ars = Decimal("0")
        ingresos_usd = Decimal("0")
        for r in ing_rows:
            moneda_str = r.moneda.value if hasattr(r.moneda, "value") else str(r.moneda)
            monto_val = Decimal(str(r.total or 0)).quantize(Decimal("0.01"))
            if moneda_str == "ARS":
                ingresos_ars += monto_val
            elif moneda_str == "USD":
                ingresos_usd += monto_val

        # Gastos del ciclo actual agrupados por categoría y moneda
        cat_stmt = (
            select(
                Transaccion.categoria_id,
                Categoria.nombre.label("categoria_nombre"),
                Transaccion.moneda,
                func.sum(Transaccion.monto).label("total"),
            )
            .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
            .where(condicion_gasto(usuario_id=usuario.id, desde=inicio, hasta=hasta, hoy=hoy))
            .group_by(Transaccion.categoria_id, Categoria.nombre, Transaccion.moneda)
        )
        cat_rows = db.execute(cat_stmt).all()

        gasto_ars_por_categoria: Dict[str, Decimal] = {}
        gasto_usd_por_categoria: Dict[str, Decimal] = {}

        for r in cat_rows:
            nom = r.categoria_nombre or "Sin categoría"
            monto_val = Decimal(str(r.total or 0)).quantize(Decimal("0.01"))
            moneda_str = r.moneda.value if hasattr(r.moneda, "value") else str(r.moneda)

            if moneda_str == "ARS":
                gasto_ars_por_categoria[nom] = gasto_ars_por_categoria.get(nom, Decimal("0")) + monto_val
            elif moneda_str == "USD":
                gasto_usd_por_categoria[nom] = gasto_usd_por_categoria.get(nom, Decimal("0")) + monto_val

        gasto_ars_por_categoria = {k: v for k, v in gasto_ars_por_categoria.items() if v != Decimal("0")}
        gasto_usd_por_categoria = {k: v for k, v in gasto_usd_por_categoria.items() if v != Decimal("0")}

        gastos_ars = sum(gasto_ars_por_categoria.values(), Decimal("0"))
        gastos_usd = sum(gasto_usd_por_categoria.values(), Decimal("0"))

        # Gastos del mes anterior en pesos (Y-4: ciclo anterior real con hasta = min(fin, hoy))
        inicio_ant, fin_ant = get_ciclo_fechas(usuario, inicio - timedelta(days=1))
        hasta_ant = min(fin_ant, hoy)

        cat_ant_stmt = (
            select(
                Categoria.nombre.label("categoria_nombre"),
                func.sum(Transaccion.monto).label("total"),
            )
            .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
            .where(condicion_gasto(usuario_id=usuario.id, desde=inicio_ant, hasta=hasta_ant, hoy=hoy, moneda=Moneda.ARS))
            .group_by(Categoria.nombre)
        )
        cat_ant_rows = db.execute(cat_ant_stmt).all()
        gasto_ars_ant_por_categoria: Dict[str, Decimal] = {}
        for r in cat_ant_rows:
            nom = r.categoria_nombre or "Sin categoría"
            monto_val = Decimal(str(r.total or 0)).quantize(Decimal("0.01"))
            if monto_val != Decimal("0"):
                gasto_ars_ant_por_categoria[nom] = monto_val

        # Filas de categorías para "En qué gastaste" (Y-4)
        todas_cats = set(gasto_ars_por_categoria.keys()) | set(gasto_ars_ant_por_categoria.keys())
        filas_cat_lista = []
        for c_nom in todas_cats:
            g_act = gasto_ars_por_categoria.get(c_nom, Decimal("0"))
            g_ant = gasto_ars_ant_por_categoria.get(c_nom, Decimal("0"))
            filas_cat_lista.append(FilaCategoria(
                nombre=c_nom,
                gastado=g_act,
                mes_anterior=g_ant,
            ))
        filas_cat_lista.sort(key=lambda f: (-f.gastado, f.nombre))
        categorias_tupla = tuple(filas_cat_lista)

        # Filas de subcategorías (Y-5)
        subcat_stmt = (
            select(
                Categoria.nombre.label("categoria_nombre"),
                Subcategoria.nombre.label("subcategoria_nombre"),
                func.sum(Transaccion.monto).label("total"),
            )
            .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
            .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
            .where(condicion_gasto(usuario_id=usuario.id, desde=inicio, hasta=hasta, hoy=hoy, moneda=Moneda.ARS))
            .group_by(Categoria.nombre, Subcategoria.nombre)
        )
        subcat_rows = db.execute(subcat_stmt).all()
        subcats_por_cat: Dict[str, List[Tuple[str, Decimal]]] = {}
        for r in subcat_rows:
            c_nom = r.categoria_nombre or "Sin categoría"
            s_nom = r.subcategoria_nombre or "Sin subcategoría"
            m_val = Decimal(str(r.total or 0)).quantize(Decimal("0.01"))
            if m_val != Decimal("0"):
                subcats_por_cat.setdefault(c_nom, []).append((s_nom, m_val))

        filas_subcat_lista = []
        for fc in filas_cat_lista:
            if fc.gastado != Decimal("0") and fc.nombre in subcats_por_cat:
                lista_subs = subcats_por_cat[fc.nombre]
                lista_subs.sort(key=lambda s: (-s[1], s[0]))
                for s_nom, s_monto in lista_subs:
                    filas_subcat_lista.append(FilaSubcategoria(
                        categoria=fc.nombre,
                        subcategoria=s_nom,
                        gastado=s_monto,
                    ))
        subcategorias_tupla = tuple(filas_subcat_lista)

        # Filas en dólares (Y-1 / DISEÑO)
        filas_usd_lista = []
        for c_nom, g_val in gasto_usd_por_categoria.items():
            filas_usd_lista.append(FilaUsd(
                nombre=c_nom,
                gastado=g_val,
            ))
        filas_usd_lista.sort(key=lambda f: (-f.gastado, f.nombre))
        categorias_usd_tupla = tuple(filas_usd_lista)

        # "Tus presupuestos" (Y-3)
        filas_presu_lista = []
        for presu in presupuestos_usuario:
            for per in presu.periodos:
                if inicio <= per.fecha_inicio <= fin:
                    uso = uso_periodo(db, presu, per, hoy=hoy, usuario=usuario)
                    lineas_desc = []
                    for pc in presu.categorias:
                        c_nom = pc.categoria.nombre if pc.categoria else "Sin categoría"
                        if pc.subcategoria:
                            lineas_desc.append(f"{c_nom} > {pc.subcategoria.nombre}")
                        else:
                            lineas_desc.append(c_nom)
                    incluye_str = ", ".join(lineas_desc)

                    if per.fecha_inicio == inicio and per.fecha_fin == fin:
                        periodo_str = "Todo el ciclo"
                    else:
                        periodo_str = f"Del {per.fecha_inicio.strftime('%d/%m/%Y')} al {per.fecha_fin.strftime('%d/%m/%Y')}"

                    moneda_presu = presu.moneda.value if hasattr(presu.moneda, "value") else str(presu.moneda)
                    filas_presu_lista.append(FilaPresupuesto(
                        nombre=presu.nombre,
                        incluye=incluye_str,
                        periodo=periodo_str,
                        moneda=moneda_presu,
                        inicio=per.fecha_inicio,
                        limite=Decimal(str(per.monto_limite)),
                        gastado=Decimal(str(uso.mostrado)),
                    ))

        filas_presu_lista.sort(key=lambda p: (p.inicio, p.nombre))
        presupuestos_tupla = tuple(filas_presu_lista)

        ciclos_excel.append(CicloExcel(
            inicio=inicio,
            fin=fin,
            nombre_hoja=nombre_hoja,
            en_curso=en_curso,
            ingresos_ars=ingresos_ars,
            gastos_ars=gastos_ars,
            ingresos_usd=ingresos_usd,
            gastos_usd=gastos_usd,
            presupuestos=presupuestos_tupla,
            categorias=categorias_tupla,
            subcategorias=subcategorias_tupla,
            categorias_usd=categorias_usd_tupla,
            gasto_ars_por_categoria=gasto_ars_por_categoria,
            gasto_usd_por_categoria=gasto_usd_por_categoria,
        ))

    tiene_dolares = any(
        c.ingresos_usd != Decimal("0") or c.gastos_usd != Decimal("0")
        for c in ciclos_excel
    )

    # Presupuestos de cobro a cobro para Comparativo (Y-6)
    ciclos_fechas = {(c.inicio, c.fin) for c in ciclos_excel}
    presupuestos_y6 = []
    for presu in presupuestos_usuario:
        periodo_tipo_str = presu.periodo.value if hasattr(presu.periodo, "value") else str(presu.periodo)
        if periodo_tipo_str != PeriodoPresupuestoTipo.MENSUAL.value:
            continue
        tiene_coincidencia = any(
            (per.fecha_inicio, per.fecha_fin) in ciclos_fechas
            for per in presu.periodos
        )
        if tiene_coincidencia:
            presupuestos_y6.append(presu)

    presupuestos_y6.sort(key=lambda p: p.nombre)

    uso_mensual_lista = []
    for presu in presupuestos_y6:
        mapa_periodos = {(per.fecha_inicio, per.fecha_fin): per for per in presu.periodos}
        valores_ciclos = []
        for c in ciclos_excel:
            per = mapa_periodos.get((c.inicio, c.fin))
            if per is None or per.monto_limite <= Decimal("0"):
                valores_ciclos.append(None)
            else:
                uso = uso_periodo(db, presu, per, hoy=hoy, usuario=usuario)
                limite = Decimal(str(per.monto_limite))
                pct = (Decimal(str(uso.mostrado)) / limite).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                valores_ciclos.append(pct)
        uso_mensual_lista.append((presu.nombre, tuple(valores_ciclos)))

    uso_mensual = tuple(uso_mensual_lista)

    return LibroDatos(
        hoy=hoy,
        ciclos=tuple(ciclos_excel),
        tiene_dolares=tiene_dolares,
        uso_mensual=uso_mensual,
    )


def nombre_archivo(datos: LibroDatos) -> str:
    """
    Genera el nombre del archivo según X-8:
    Argentum_presupuestos_<inicio primer ciclo>_<fin último ciclo>.xlsx
    """
    inicio_str = datos.ciclos[0].inicio.isoformat()
    fin_str = datos.ciclos[-1].fin.isoformat()
    return f"Argentum_presupuestos_{inicio_str}_{fin_str}.xlsx"
