"""
app/services/fin_de_mes_service.py — Cálculo del disponible libre y proyección a fin de mes.

Respaldo:
Rango por cuantiles empíricos de la propia historia, sin modelo, porque con 3 a 12 datos los
métodos simples rinden igual o mejor (Makridakis y otros 2020, M4); riesgo como frecuencia
natural (Gigerenzer y Hoffrage 1995); margen por cobertura con el subregistro medido por los
controles de saldo (diario de gastos del BLS).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import MetodoPago, TipoTransaccion, Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.contrato_numero_service import ContratoNumero, cobertura_usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto
from app.services.frescura_service import Frescura, calcular_frescura
from app.services.ingreso_habitual_service import obtener_ingreso_habitual
from app.services.pagos_proximos_service import calcular_disponible_libre
from app.utils.fecha import hoy_argentina


# ==============================================================================
# ESTRUCTURAS DE DATOS
# ==============================================================================

@dataclass(frozen=True)
class ResultadoRango:
    """Resultado del cálculo empírico del rango y riesgo a fin de mes."""
    estado: str
    rango_bajo: Optional[Decimal]
    rango_alto: Optional[Decimal]
    ciclos: int
    veces_no_alcanza: Optional[int]
    factor_cobertura: Decimal

    @property
    def rango(self) -> tuple[Decimal, Decimal] | None:
        if self.rango_bajo is not None and self.rango_alto is not None:
            return (self.rango_bajo, self.rango_alto)
        return None

    @property
    def veces(self) -> Optional[int]:
        return self.veces_no_alcanza

    @property
    def factor(self) -> Decimal:
        return self.factor_cobertura

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, tuple):
            return self.rango == other
        if isinstance(other, ResultadoRango):
            return (
                self.estado == other.estado
                and self.rango_bajo == other.rango_bajo
                and self.rango_alto == other.rango_alto
                and self.ciclos == other.ciclos
                and self.veces_no_alcanza == other.veces_no_alcanza
                and self.factor_cobertura == other.factor_cobertura
            )
        return False


@dataclass(frozen=True)
class ResultadoFinDeMes:
    """Resultado consolidado del disponible libre y proyección a fin de mes."""
    hoy: date
    inicio_ciclo: date
    fin_ciclo: date
    limite: date
    proximo_ingreso: Optional[date]
    hasta_ingreso: bool
    cobra_en_ciclo: bool
    estado: str
    libre_ars: Decimal
    libre_hasta_cobro: Optional[Decimal]
    ingreso_esperado: Decimal
    libre_usd: Decimal
    compromisos_ars: List[Dict[str, Any]]
    compromisos_usd: List[Dict[str, Any]]
    rango: ResultadoRango
    cobertura: Optional[int]
    saldo_inicio: Decimal
    movimiento: Decimal
    saldo_hoy: Decimal
    frescura: Frescura
    contrato: ContratoNumero

    @property
    def mostrar_tarjeta(self) -> bool:
        return self.estado == "completo"

    @property
    def fecha_limite(self) -> date:
        return self.limite

    @property
    def rango_bajo(self) -> Optional[Decimal]:
        return self.rango.rango_bajo

    @property
    def rango_alto(self) -> Optional[Decimal]:
        return self.rango.rango_alto

    @property
    def ciclos(self) -> int:
        return self.rango.ciclos

    @property
    def veces_no_alcanzo(self) -> Optional[int]:
        return self.rango.veces_no_alcanza

    @property
    def veces(self) -> Optional[int]:
        return self.rango.veces_no_alcanza

    @property
    def factor_cobertura(self) -> Decimal:
        return self.rango.factor_cobertura

    @property
    def datos_viejos(self) -> bool:
        return self.frescura.datos_viejos

    @property
    def dias_sin_cargar(self) -> Optional[int]:
        return self.frescura.dias_sin_cargar


# ==============================================================================
# FUNCIONES PURAS (H-A)
# ==============================================================================

def cobra_en_ciclo(proximo_ingreso: date | None, fin_ciclo: date) -> bool:
    """Indica si el próximo ingreso habitual cae dentro del ciclo actual."""
    return proximo_ingreso is not None and proximo_ingreso <= fin_ciclo


def ingresos_en_ciclo(cobros: list[tuple[date, Decimal]], hoy: date, fin_ciclo: date) -> Decimal:
    """Suma los montos de cobros regulares con hoy < fecha <= fin_ciclo."""
    return sum((monto for fecha, monto in cobros if hoy < fecha <= fin_ciclo), Decimal("0"))


def dia_de_cobro(fechas: list[date]) -> int | None:
    """
    Deduce el día del mes habitual de cobro.
    Con menos de 3 fechas devuelve None. Toma las 3 más recientes y devuelve
    el día del medio tras ordenar esos 3 días.
    """
    if len(fechas) < 3:
        return None
    ultimas_3 = sorted(fechas)[-3:]
    dias = sorted([f.day for f in ultimas_3])
    return dias[1]


def proxima_fecha_con_dia(hoy: date, dia: int) -> date:
    """
    Devuelve la primera fecha estrictamente posterior a hoy cuyo día es min(dia, último día de su mes).
    """
    ultimo_dia = calendar.monthrange(hoy.year, hoy.month)[1]
    candidata = date(hoy.year, hoy.month, min(dia, ultimo_dia))
    if candidata > hoy:
        return candidata

    # Avanzar al mes siguiente
    if hoy.month == 12:
        next_year = hoy.year + 1
        next_month = 1
    else:
        next_year = hoy.year
        next_month = hoy.month + 1
    ultimo_dia_next = calendar.monthrange(next_year, next_month)[1]
    return date(next_year, next_month, min(dia, ultimo_dia_next))


def fecha_limite(hoy: date, fin_ciclo: date, proximo_ingreso: date | None) -> tuple[date, bool]:
    """
    Determina la fecha límite del horizonte y si es hasta el próximo ingreso.
    Si proximo_ingreso es None: (fin_ciclo, False).
    Si no: tope = proximo_ingreso − 1 día; si tope <= fin_ciclo: (max(tope, hoy), True); si no: (fin_ciclo, False).
    """
    if proximo_ingreso is None:
        return (fin_ciclo, False)
    tope = proximo_ingreso - timedelta(days=1)
    if tope <= fin_ciclo:
        return (max(tope, hoy), True)
    return (fin_ciclo, False)


def redondear_mil(valor: Decimal) -> Decimal:
    """Redondea un valor Decimal al múltiplo de 1.000 más cercano con ROUND_HALF_UP."""
    return (valor / Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("1000")


def percentil(valores_ordenados: list[Decimal], p: Decimal) -> Decimal:
    """
    Interpolación lineal (tipo 7 de Hyndman y Fan 1996, el de numpy):
    h = (n − 1) × p;
    base = parte entera de h;
    resultado = v[base] + (h − base) × (v[base+1] − v[base]) (si base es el último, v[base]).
    """
    n = len(valores_ordenados)
    if n == 0:
        return Decimal("0")
    if n == 1:
        return valores_ordenados[0]
    p_dec = Decimal(str(p))
    h = Decimal(n - 1) * p_dec
    base = int(h)
    if base >= n - 1:
        return valores_ordenados[-1]
    diff = valores_ordenados[base + 1] - valores_ordenados[base]
    return valores_ordenados[base] + (h - Decimal(base)) * diff


def rango_fin_de_mes(
    libre: Decimal,
    dias_restantes: int,
    ciclos: list[tuple[Decimal, int]],
    cobertura: int | None,
    ingreso_deducible: bool,
    datos_viejos: bool,
) -> ResultadoRango:
    """
    Calcula el rango empírico de saldo a fin de mes y el riesgo como frecuencia de insuficiencia.
    """
    # Factor de cobertura
    if cobertura is None or cobertura >= 100:
        factor_cobertura = Decimal("1")
    else:
        factor_cobertura = Decimal("100") / Decimal(str(max(cobertura, 50)))

    # Determinación del estado por orden estricto de prioridad
    if datos_viejos:
        estado = "datos_viejos"
    elif not ingreso_deducible:
        estado = "sin_ingreso"
    elif len(ciclos) < 3:
        estado = "solo_seguro"
    else:
        estado = "completo"

    if estado != "completo":
        return ResultadoRango(
            estado=estado,
            rango_bajo=None,
            rango_alto=None,
            ciclos=len(ciclos),
            veces_no_alcanza=None,
            factor_cobertura=factor_cobertura,
        )

    # Cálculo para estado "completo"
    dias_rest_dec = Decimal(str(dias_restantes))
    finales: list[Decimal] = []
    for gasto, dias_del_ciclo in ciclos:
        if dias_del_ciclo <= 0:
            continue
        gasto_dec = Decimal(str(gasto))
        dias_ciclo_dec = Decimal(str(dias_del_ciclo))
        restante = (gasto_dec * dias_rest_dec / dias_ciclo_dec * factor_cobertura).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        final = libre - restante
        finales.append(final)

    finales.sort()
    if not finales:
        return ResultadoRango(
            estado=estado,
            rango_bajo=None,
            rango_alto=None,
            ciclos=len(ciclos),
            veces_no_alcanza=None,
            factor_cobertura=factor_cobertura,
        )

    if len(ciclos) < 10:
        rango_bajo = redondear_mil(finales[0])
        rango_alto = redondear_mil(finales[-1])
    else:
        rango_bajo = redondear_mil(percentil(finales, Decimal("0.10")))
        rango_alto = redondear_mil(percentil(finales, Decimal("0.90")))

    veces_no_alcanza = sum(1 for f in finales if f < Decimal("0"))

    return ResultadoRango(
        estado=estado,
        rango_bajo=rango_bajo,
        rango_alto=rango_alto,
        ciclos=len(ciclos),
        veces_no_alcanza=veces_no_alcanza,
        factor_cobertura=factor_cobertura,
    )


# ==============================================================================
# FUNCIÓN PRINCIPAL DE SERVICIO (H-B)
# ==============================================================================

def calcular_fin_de_mes(
    db: Session,
    usuario: Usuario,
    hoy: date | None = None,
    fecha_ref: date | None = None,
) -> ResultadoFinDeMes:
    """
    Calcula los números de decisión para el disponible libre y fin de mes.
    Solo lectura.
    """
    ref = hoy if hoy is not None else fecha_ref
    ref_final = ref if ref is not None else hoy_argentina()
    fecha_ref = ref_final

    # 1. Fechas del ciclo actual
    inicio_ciclo, fin_ciclo = get_ciclo_fechas(usuario, fecha_ref)

    # 2. Ingreso habitual y deducción del próximo cobro
    res_ingreso = obtener_ingreso_habitual(db, usuario, hoy=fecha_ref, moneda="ARS")
    if res_ingreso.monto is None and getattr(res_ingreso, "motivo_sin_datos", None) == "irregular":
        # Si el usuario tiene menos de 6 ciclos completos desde su primer ingreso, la ventana
        # son los ciclos completos desde el primer ingreso (regla de negocio de ingreso habitual)
        min_f = db.query(func.min(Transaccion.fecha)).filter(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo == TipoTransaccion.INGRESO,
            or_(Transaccion.moneda == Moneda.ARS, Transaccion.moneda == "ARS"),
        ).scalar()
        if min_f is not None:
            p = fecha_ref
            c_list: list[tuple[date, date]] = []
            while p >= min_f:
                c_i, c_f = get_ciclo_fechas(usuario, p)
                c_list.append((c_i, c_f))
                p = c_i - timedelta(days=1)
            c_list = sorted(list(set(c_list)), key=lambda x: x[0])
            if len(c_list) >= 3:
                from app.services.ingreso_habitual_service import calcular_ingreso_habitual_en_memoria, cargar_contexto
                res_mem = calcular_ingreso_habitual_en_memoria(
                    movimientos=db.query(Transaccion).filter(Transaccion.usuario_id == usuario.id).all(),
                    ciclos=c_list,
                    hoy=fecha_ref,
                    ctx=cargar_contexto(db, usuario.id, fecha_ref),
                    moneda="ARS",
                )
                if res_mem.monto is not None:
                    res_ingreso = res_mem

    ingreso_deducible = res_ingreso.monto is not None

    cobros: list[tuple[date, Decimal]] = []
    if ingreso_deducible:
        fuentes_por_par: dict[tuple[Any, Any], list[Any]] = {}
        for f in res_ingreso.fuentes:
            if f.tipo == "regular":
                par = (f.categoria_id, f.subcategoria_id)
                fuentes_por_par.setdefault(par, []).append(f)

        fuentes_regulares_elegidas = []
        for par, lista in fuentes_por_par.items():
            elegida = next((f for f in lista if getattr(f, "orden_desdoble", None) == 1), lista[0])
            fuentes_regulares_elegidas.append(elegida)

        for f in fuentes_regulares_elegidas:
            cat_id_val = UUID(f.categoria_id) if isinstance(f.categoria_id, str) and f.categoria_id else f.categoria_id
            subcat_id_val = UUID(f.subcategoria_id) if isinstance(f.subcategoria_id, str) and f.subcategoria_id else f.subcategoria_id
            query_fechas = db.query(Transaccion.fecha).filter(
                Transaccion.usuario_id == usuario.id,
                Transaccion.tipo == TipoTransaccion.INGRESO,
                or_(Transaccion.moneda == Moneda.ARS, Transaccion.moneda == "ARS"),
                Transaccion.categoria_id == cat_id_val,
                Transaccion.fecha >= fecha_ref - timedelta(days=120),
                Transaccion.fecha <= fecha_ref,
            )
            if subcat_id_val is not None:
                query_fechas = query_fechas.filter(Transaccion.subcategoria_id == subcat_id_val)
            else:
                query_fechas = query_fechas.filter(Transaccion.subcategoria_id.is_(None))

            fechas_fuente = []
            for t in query_fechas.all():
                v = t[0] if hasattr(t, "__getitem__") else t
                if isinstance(v, str):
                    v = date.fromisoformat(v)
                fechas_fuente.append(v)
            dia_cobro = dia_de_cobro(fechas_fuente)
            if dia_cobro is not None:
                candidata = proxima_fecha_con_dia(fecha_ref, dia_cobro)
                monto_cobro = Decimal(str(f.monto)) if f.monto is not None else Decimal("0")
                cobros.append((candidata, monto_cobro))

    proximo_ingreso = min(c[0] for c in cobros) if cobros else None
    if not ingreso_deducible:
        proximo_ingreso = None

    # 3. Fecha límite y cobra en ciclo
    limite, hasta_ingreso = fecha_limite(fecha_ref, fin_ciclo, proximo_ingreso)
    en_ciclo = cobra_en_ciclo(proximo_ingreso, fin_ciclo)

    # 4. Disponible libre
    disponible = calcular_disponible_libre(db, usuario, fecha_fin_ciclo=fin_ciclo, hoy=fecha_ref)
    libre_ars = Decimal(str(disponible["ars"]["saldo_disponible"]))
    libre_usd = Decimal(str(disponible["usd"]["saldo_disponible"]))

    if en_ciclo:
        disp_cobro = calcular_disponible_libre(db, usuario, fecha_fin_ciclo=limite, hoy=fecha_ref)
        libre_hasta_cobro = Decimal(str(disp_cobro["ars"]["saldo_disponible"]))
    else:
        libre_hasta_cobro = None

    ingreso_esperado = ingresos_en_ciclo(cobros, fecha_ref, fin_ciclo) if ingreso_deducible else Decimal("0")

    # 5. Historia de ciclos anteriores (hasta 12 ciclos)
    min_fecha = db.query(func.min(Transaccion.fecha)).filter(Transaccion.usuario_id == usuario.id).scalar()

    ciclos_fechas: list[tuple[date, date]] = []
    if min_fecha is not None:
        fin_k = inicio_ciclo - timedelta(days=1)
        for _ in range(12):
            inicio_k, fin_k_calc = get_ciclo_fechas(usuario, fin_k)
            if inicio_k < min_fecha:
                break
            ciclos_fechas.append((inicio_k, fin_k_calc))
            fin_k = inicio_k - timedelta(days=1)

    ciclos: list[tuple[Decimal, int]] = []
    if ciclos_fechas:
        ventana_inicio = ciclos_fechas[-1][0]
        ventana_fin = ciclos_fechas[0][1]

        cond_g = condicion_gasto(
            usuario_id=usuario.id,
            desde=ventana_inicio,
            hasta=ventana_fin,
            moneda="ARS",
            hoy=fecha_ref,
        )
        filas = (
            db.query(Transaccion.fecha, func.sum(Transaccion.monto))
            .filter(
                cond_g,
                or_(Transaccion.metodo_pago.is_(None), Transaccion.metodo_pago != MetodoPago.CREDITO),
                Transaccion.suscripcion_id.is_(None),
                Transaccion.es_cuota_hija.is_(False),
            )
            .group_by(Transaccion.fecha)
            .all()
        )

        for inicio_k, fin_k in ciclos_fechas:
            gasto_k = sum(
                (Decimal(str(m)) for f, m in filas if inicio_k <= f <= fin_k and m is not None),
                Decimal("0"),
            )
            dias_k = (fin_k - inicio_k).days + 1
            ciclos.append((gasto_k, dias_k))

    # 6. Cobertura, frescura y rango
    cobertura = cobertura_usuario(db, usuario.id, fecha_ref)
    frescura = calcular_frescura(db, usuario.id, hoy=fecha_ref)
    dias_restantes = (fin_ciclo - fecha_ref).days
    rango = rango_fin_de_mes(
        libre=libre_ars + ingreso_esperado,
        dias_restantes=dias_restantes,
        ciclos=ciclos,
        cobertura=cobertura,
        ingreso_deducible=ingreso_deducible,
        datos_viejos=frescura.datos_viejos,
    )

    # 7. Saldo del ciclo (ARS, billeteras activas no de inversión)
    saldo_hoy = Decimal(str(disponible["ars"]["saldo_total"]))

    billeteras_activas_ars = (
        db.query(Billetera)
        .filter(
            Billetera.usuario_id == usuario.id,
            Billetera.moneda == Moneda.ARS,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.es_inversion.is_(False),
        )
        .all()
    )

    movimiento = Decimal("0")
    for b in billeteras_activas_ars:
        saldo_hoy_b = calcular_saldo_teorico(db, b.id, hasta=fecha_ref)
        saldo_inicio_b = calcular_saldo_teorico(db, b.id, hasta=inicio_ciclo - timedelta(days=1))
        movimiento += Decimal(str(saldo_hoy_b)) - Decimal(str(saldo_inicio_b))

    saldo_inicio = saldo_hoy - movimiento

    # 8. Contrato de número
    supuestos = [
        "Suma solo billeteras activas que no son de inversión.",
        f"Descuenta lo que vence hasta el {fin_ciclo:%d/%m}.",
    ]
    if en_ciclo:
        supuestos.append("Tu próximo cobro sale de tus últimos 3 cobros.")

    contrato = ContratoNumero(
        valor=libre_ars,
        moneda="ARS",
        base_desde=inicio_ciclo,
        base_hasta=fin_ciclo,
        fecha_corte=fecha_ref,
        cobertura=cobertura,
        frescura=frescura,
        supuestos=tuple(supuestos),
    )

    # 9. Compromisos ordenados por fecha_cobro y nombre
    compromisos_ars = sorted(
        disponible["ars"].get("compromisos", []),
        key=lambda x: (str(x.get("fecha_cobro") or ""), str(x.get("nombre") or "")),
    )
    compromisos_usd = sorted(
        disponible["usd"].get("compromisos", []),
        key=lambda x: (str(x.get("fecha_cobro") or ""), str(x.get("nombre") or "")),
    )

    return ResultadoFinDeMes(
        hoy=fecha_ref,
        inicio_ciclo=inicio_ciclo,
        fin_ciclo=fin_ciclo,
        limite=limite,
        proximo_ingreso=proximo_ingreso,
        hasta_ingreso=hasta_ingreso,
        cobra_en_ciclo=en_ciclo,
        estado=rango.estado,
        libre_ars=libre_ars,
        libre_hasta_cobro=libre_hasta_cobro,
        ingreso_esperado=ingreso_esperado,
        libre_usd=libre_usd,
        compromisos_ars=compromisos_ars,
        compromisos_usd=compromisos_usd,
        rango=rango,
        cobertura=cobertura,
        saldo_inicio=saldo_inicio,
        movimiento=movimiento,
        saldo_hoy=saldo_hoy,
        frescura=frescura,
        contrato=contrato,
    )

