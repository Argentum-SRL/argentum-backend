"""
app/services/permitirse_service.py — Motor unificado de "¿Me lo puedo permitir?".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Any, Set
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import EstadoMeta, Meta
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.utils.cuotas import monto_efectivo_cuota_sql
from app.services.compromisos_service import (
    calcular_compromisos_mensuales,
    calcular_gasto_variable_tipico,
    gastos_variables_por_ciclo,
    normalizar_moneda,
)
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_ingreso
from app.services.dolar_service import obtener_cotizacion_por_fecha
from app.services.fin_de_mes_service import calcular_fin_de_mes, redondear_mil
from app.services.inflacion_esperada_service import inflacion_esperada
from app.services.ingreso_habitual_service import obtener_ingreso_habitual
from app.services.permitirse_dolares import (
    datos_dolares,
    finales_de_quien_cobra_en_dolares,
    movimientos_dolares_en_pesos,
)
from app.services.permitirse_evaluacion import (
    evaluar_cuotas_cuentas,
    evaluar_mensual_cuentas,
    piso_por_contado,
    plata_de_cuentas,
    sobrante_prudente,
)
from app.services.permitirse_rubros import (
    elegir_rubro_recortable,
    medianas_recortables,
    reintegro_del_rubro,
)
from app.services.permitirse_textos import textos_permitirse
from app.services.presupuestos_excel_datos import nombre_hoja_ciclo
from app.services.tools_service import PlanCuotas, calcular_plan_cuotas
from app.utils.fecha import hoy_argentina


# ==============================================================================
# FUNCIONES PURAS DE CÁLCULO
# ==============================================================================

def tope_por_ingreso_temprano(resultado: str, modo: str, temprano: bool) -> str:
    """
    Tope de resultado para ingreso temprano: nunca responde 'bien' en cuotas o mensual.
    Pura.
    """
    if temprano and modo in ("cuotas", "mensual") and resultado == "bien":
        return "justo"
    return resultado


def tope_por_datos_viejos(resultado: str, motivo: str | None) -> str:
    """
    Tope de resultado para datos viejos: nunca responde 'bien' (como máximo 'justo').
    Pura.
    """
    if motivo == "datos_viejos" and resultado == "bien":
        return "justo"
    return resultado


def mediana(valores: list[Decimal] | tuple[Decimal, ...]) -> Decimal:
    """Calcula la mediana de una secuencia de Decimals."""
    if not valores:
        raise ValueError("No se puede calcular mediana de lista vacía")
    s = sorted(valores)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return ((s[mid - 1] + s[mid]) / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def desvio_absoluto_mediano(valores: list[Decimal] | tuple[Decimal, ...]) -> Decimal | None:
    """
    Mediana de |v - mediana(valores)| (Leys y otros 2013).
    Si hay menos de 3 valores, devuelve None. Pura.
    """
    if len(valores) < 3:
        return None
    med = mediana(valores)
    desvios = [abs(v - med) for v in valores]
    return mediana(desvios)


def liberacion_por_mes(
    cuotas: list[tuple[date, Decimal]],
    ciclos: list[tuple[date, date]],
) -> list[Decimal]:
    """
    Suma de las cuotas cuya fecha cae en cada ciclo.
    Pura.
    """
    totales: list[Decimal] = []
    for c_ini, c_fin in ciclos:
        suma_c = sum(
            (monto for f_venc, monto in cuotas if c_ini <= f_venc <= c_fin),
            Decimal("0.00"),
        )
        totales.append(suma_c.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return totales


def mes_liberacion(
    margenes: list[Decimal],
    cuotas: list[Decimal],
    colchon: Decimal | None,
    liberaciones: list[Decimal] | None = None,
) -> int | None:
    """
    El primer j >= 2 tal que r_k >= colchon para todo k >= j y r_{j-1} < colchon,
    y en el que además bajan las cuotas existentes (liberaciones[j-1] < liberaciones[j-2]).
    Solo mira los ciclos de `cuotas` (las de esta compra, o el horizonte en "todos los meses"):
    no suma ceros después de la última cuota de la compra.
    Si no existe, colchon es None o no hay cuotas existentes, devuelve None.
    j es 1-based (k=1 ciclo siguiente, k=2 ciclo posterior).
    Pura.
    """
    if colchon is None or not margenes or not cuotas or not liberaciones:
        return None

    H = min(len(margenes), len(cuotas), len(liberaciones))
    if H < 2:
        return None

    r = [margenes[k] - cuotas[k] for k in range(H)]

    for j in range(2, H + 1):  # j en 2..H (1-based)
        idx = j - 1  # 0-based
        bajan_existentes = liberaciones[idx] < liberaciones[idx - 1]
        if bajan_existentes and r[idx - 1] < colchon:
            if all(r_k >= colchon for r_k in r[idx:]):
                return j
    return None


CAT_EXCLUIDAS_COMPENSAR = {
    "vivienda", "salud", "educación", "educacion", "impuestos", "banco", "ahorro", "inversiones"
}


def elegir_rubro(medianas: dict[str, Decimal]) -> str | None:
    """
    Elige el rubro de mayor mediana entre los elegibles.
    En caso de empate en la mediana, el primero en orden alfabético.
    Excluye siempre Vivienda, Salud, Educación, Impuestos, Banco, Ahorro, Inversiones.
    Pura.
    """
    if not medianas:
        return None
    elegibles = {
        k: v for k, v in medianas.items()
        if k.strip().lower() not in CAT_EXCLUIDAS_COMPENSAR
    }
    if not elegibles:
        return None
    items = sorted(elegibles.items(), key=lambda x: (-x[1], x[0]))
    return items[0][0]


def redondear_arriba_mil(valor: Decimal) -> Decimal:
    """Redondea hacia arriba al múltiplo de 1.000 más cercano."""
    if valor <= Decimal("0"):
        return Decimal("0.00")
    millares = math.ceil(float(valor) / 1000.0)
    return Decimal(str(millares * 1000)).quantize(Decimal("0.01"))


def compensar_tope_50(monto_x: Decimal, mediana_rubro: Decimal) -> bool:
    """Verifica que el monto a compensar no supere el 50% de la mediana del rubro."""
    return monto_x <= (Decimal("0.5") * mediana_rubro)


def evaluar_contado(
    precio: Decimal,
    libre: Decimal,
    finales: tuple[Decimal, ...] | None,
    ahorros_total: Decimal,
) -> tuple[str, int, int, Decimal]:
    """
    Evalúa la compra de contado.
    Devuelve (resultado, veces, n, falta).
    Pura.
    """
    precio_dec = Decimal(str(precio))
    libre_dec = Decimal(str(libre))
    ahorros_dec = Decimal(str(ahorros_total))

    # a. precio > libre
    if precio_dec > libre_dec:
        falta = precio_dec - libre_dec
        if falta <= ahorros_dec:
            return ("ahorros", 0, 0, falta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        else:
            falta_neta = falta - ahorros_dec if ahorros_dec > Decimal("0") else falta
            return ("no_alcanza", 0, 0, falta_neta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    # b. finales None o vacío (estado distinto de "completo")
    if not finales:
        return ("justo", 0, 0, Decimal("0.00"))

    # c. finales presentes
    n = len(finales)
    veces = sum(1 for f in finales if (f - precio_dec) < Decimal("0"))
    if veces == 0:
        return ("bien", veces, n, Decimal("0.00"))
    elif veces * 2 <= n:
        return ("justo", veces, n, Decimal("0.00"))
    else:
        med = mediana(finales)
        falta = precio_dec - med
        if falta <= ahorros_dec:
            return ("ahorros", veces, n, falta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        else:
            falta_neta = falta - ahorros_dec if ahorros_dec > Decimal("0") else falta
            return ("no_alcanza", veces, n, falta_neta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def evaluar_mensual(
    cuotas: list[Decimal],
    margenes: list[Decimal],
    colchon: Decimal | None,
    ahorros_total: Decimal,
    indefinido: bool,
) -> tuple[str, Decimal, Decimal, int, Decimal, int]:
    """
    Evalúa cuotas o gasto mensual recurrente.
    Devuelve (resultado, r_min, deficit_total, meses_deficit, deficit_mensual, meses_alcanzan).
    Pura.
    """
    H = min(len(cuotas), len(margenes))
    r_k = [margenes[k] - cuotas[k] for k in range(H)]
    r_min = min(r_k) if r_k else Decimal("0.00")

    if colchon is not None and all(r >= colchon for r in r_k):
        return ("bien", r_min, Decimal("0.00"), 0, Decimal("0.00"), 0)
    elif all(r >= Decimal("0.00") for r in r_k):
        return ("justo", r_min, Decimal("0.00"), 0, Decimal("0.00"), 0)
    else:
        if indefinido:
            d = max(Decimal("0.00"), -r_k[0]) if r_k else Decimal("0.00")
            if d > Decimal("0"):
                meses = math.floor(float(ahorros_total) / float(d))
            else:
                meses = 0
            if meses >= 1:
                resultado = "ahorros"
            else:
                resultado = "no_alcanza"
            return (resultado, r_min, d, 1, d, meses)
        else:
            total_deficit = sum((max(Decimal("0.00"), -r) for r in r_k), Decimal("0.00"))
            meses_def = sum(1 for r in r_k if r < Decimal("0"))
            if total_deficit <= ahorros_total:
                resultado = "ahorros"
            else:
                resultado = "no_alcanza"
            return (resultado, r_min, total_deficit, meses_def, Decimal("0.00"), 0)


# ==============================================================================
# RECOLECCIÓN DE DATOS (SOLO LECTURA)
# ==============================================================================

@dataclass(frozen=True)
class DatosPermitirse:
    libre: Decimal
    fin_ciclo: date
    estado_fdm: str
    finales: tuple[Decimal, ...]
    dias_sin_cargar: int | None
    ingreso: Decimal | None
    es_admin: bool
    suscripciones: Decimal
    fijos: Decimal
    gasto_variable: Decimal
    cuotas_futuras: list[tuple[date, Decimal]]
    ciclos_futuros: list[tuple[date, date]]
    liberaciones: list[Decimal]
    margenes: list[Decimal]
    colchon: Decimal | None
    inversiones: Decimal
    dolares_usd: Decimal
    dolares_en_pesos: Decimal
    metas: Decimal
    ahorros_total: Decimal
    rubros_medianas: dict[str, Decimal]
    ingreso_temprano: bool = False
    motivo_ingreso: str | None = None
    # Bloque T1: lo que le sobra por mes (None si no se pudo calcular), si faltan ciclos
    # para calcularlo, si usa dólares seguido y el dólar oficial venta de hoy.
    sobrante: Decimal | None = None
    sin_sobrante: bool = False
    usa_dolares: bool = False
    cotizacion_venta: Decimal = Decimal("1.00")


def _ids_de_fijos(
    db: Session,
    usuario: Usuario,
    ref_hoy: date,
    es_admin: bool,
) -> dict[str, Set[UUID]]:
    """
    Movimientos de los gastos fijos maduros, por moneda, para dejarlos afuera del gasto
    variable. Los fijos solo se usan con admins: para los demás, conjuntos vacíos.
    """
    ids: dict[str, Set[UUID]] = {"ARS": set(), "USD": set()}
    if not es_admin:
        return ids

    from app.services.datos_motor_service import cargar_datos_motor, ciclos_anteriores
    from app.utils.finanzas import clasificar_gastos

    data = cargar_datos_motor(db, usuario, ref_hoy)
    ciclos_12 = ciclos_anteriores(usuario, ref_hoy, 12)
    comprometidos_ext = [
        *(c for c, _ in data["cuotas"] if not c.pagada and c.fecha_vencimiento >= ref_hoy),
        *data["suscripciones"],
    ]
    res_clasif = clasificar_gastos(
        data["txs"],
        ciclos_12,
        data["ipc"],
        ref_hoy,
        comprometidos_ext,
        ctx=data["ctx"],
    )
    for s in res_clasif.streams:
        if s.clase == "COMPROMISO" and s.estado == "MADURO" and s.senal != "DECLARADO":
            moneda_s = normalizar_moneda(s.moneda)
            if moneda_s in ids:
                ids[moneda_s].update(s.transacciones_ids)
    return ids


def _ciclos_cerrados_con_historia(
    db: Session,
    usuario: Usuario,
    ref_hoy: date,
    max_ciclos: int = 12,
) -> list[tuple[date, date]]:
    """
    Los mismos ciclos que usa gastos_variables_por_ciclo: cerrados, con historia,
    del más nuevo al más viejo.
    """
    c_ini_curr, _ = get_ciclo_fechas(usuario, ref_hoy)
    fecha_fin_c1 = c_ini_curr - timedelta(days=1)

    primera_tx = db.execute(
        select(func.min(Transaccion.fecha)).where(Transaccion.usuario_id == usuario.id)
    ).scalar()
    if primera_tx is None:
        primera_tx = usuario.fecha_registro.date()
    if primera_tx > fecha_fin_c1:
        return []

    ciclos: list[tuple[date, date]] = []
    p = fecha_fin_c1
    while p >= primera_tx and len(ciclos) < max_ciclos:
        inicio, fin = get_ciclo_fechas(usuario, p)
        if fin <= fecha_fin_c1:
            ciclos.append((inicio, fin))
            p = inicio - timedelta(days=1)
        else:
            break
    return ciclos


def _netos_por_ciclo(
    db: Session,
    usuario: Usuario,
    ref_hoy: date,
    gastos_ciclos: list[Decimal],
    usa_dolares: bool,
    compra_hoy: Decimal,
    ids_fijos_usd: Set[UUID],
) -> list[Decimal]:
    """
    Para cada ciclo cerrado de los últimos 12 que tuvo movimientos: ingreso del ciclo menos
    gasto variable del ciclo (qué es ingreso y qué es gasto lo decide definiciones_service).
    Con dólares de todos los días se suman los ingresos y gastos en dólares, pasados a pesos
    con el dólar oficial compra del día de cada movimiento. El saldo inicial nunca entra.
    """
    ciclos = _ciclos_cerrados_con_historia(db, usuario, ref_hoy, 12)[: len(gastos_ciclos)]
    if not ciclos:
        return []

    cond_ing = condicion_ingreso(
        usuario.id, ciclos[-1][0], ciclos[0][1], moneda=Moneda.ARS, hoy=ref_hoy
    )
    filas = db.execute(select(Transaccion.fecha, Transaccion.monto).where(cond_ing)).all()

    if usa_dolares:
        en_dolares = movimientos_dolares_en_pesos(
            db, usuario, ciclos, ref_hoy, compra_hoy, ids_fijos_usd
        )
    else:
        en_dolares = [(Decimal("0"), Decimal("0"), 0)] * len(ciclos)

    netos: list[Decimal] = []
    for (inicio, fin), gasto, usd in zip(ciclos, gastos_ciclos, en_dolares):
        ing_usd, gas_usd, cant_usd = usd
        cobros = [Decimal(str(m)) for f, m in filas if inicio <= f <= fin]
        gasto_dec = Decimal(str(gasto))
        if not cobros and gasto_dec == Decimal("0") and cant_usd == 0:
            continue  # ciclo sin movimientos
        neto = sum(cobros, Decimal("0")) + ing_usd - gasto_dec - gas_usd
        netos.append(neto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return netos


def recolectar(db: Session, usuario: Usuario, hoy: date | None = None) -> DatosPermitirse:
    """
    Recolecta todos los datos financieros necesarios para el cálculo de "¿Me lo puedo permitir?".
    Solo lectura.
    """
    ref_hoy = hoy or hoy_argentina()

    # 1. Fin de mes
    fdm = calcular_fin_de_mes(db, usuario, hoy=ref_hoy)
    libre = fdm.libre_ars
    fin_ciclo = fdm.fin_ciclo
    estado_fdm = fdm.estado
    finales = getattr(fdm.rango, "finales", ()) or ()
    dias_sin_cargar = fdm.dias_sin_cargar

    # 2. Ingreso habitual
    res_ingreso = obtener_ingreso_habitual(db, usuario, hoy=ref_hoy, moneda="ARS", incluir_temprano=True)
    ingreso = res_ingreso.monto
    ingreso_temprano = res_ingreso.es_temprano
    motivo_ingreso = res_ingreso.motivo_sin_datos

    # 2b. Dólares (decisión 2B): si los usa seguido y cuánto cobra en dólares
    info_dolares = datos_dolares(db, usuario, ref_hoy)
    usa_dolares = info_dolares.usa

    # 3. Compromisos mensuales
    es_admin = bool(usuario.is_admin)
    comp_res = calcular_compromisos_mensuales(db, usuario, hoy=ref_hoy, moneda=Moneda.ARS)
    suscripciones = comp_res.suscripciones
    fijos = comp_res.fijos if es_admin else Decimal("0.00")

    if es_admin:
        gasto_variable = calcular_gasto_variable_tipico(db, usuario, hoy=ref_hoy, moneda=Moneda.ARS)
    else:
        gasto_variable = calcular_gasto_variable_tipico(db, usuario, hoy=ref_hoy, moneda=Moneda.ARS, streams_c=[])

    # 4. Cotizaciones dólar oficial (venta para pesificar cuotas, compra para pesificar ahorros)
    cot_obj = obtener_cotizacion_por_fecha(db, "oficial", ref_hoy)
    if cot_obj and cot_obj.venta is not None:
        cot_oficial_venta = Decimal(str(cot_obj.venta))
    else:
        last_c = db.execute(
            select(CotizacionDolar)
            .where(CotizacionDolar.tipo == "oficial", CotizacionDolar.venta.isnot(None))
            .order_by(desc(CotizacionDolar.fecha))
        ).scalars().first()
        cot_oficial_venta = Decimal(str(last_c.venta)) if (last_c and last_c.venta) else Decimal("1.00")

    if cot_obj and cot_obj.compra is not None:
        cot_oficial_compra = Decimal(str(cot_obj.compra))
    else:
        last_c = db.execute(
            select(CotizacionDolar)
            .where(CotizacionDolar.tipo == "oficial", CotizacionDolar.compra.isnot(None))
            .order_by(desc(CotizacionDolar.fecha))
        ).scalars().first()
        cot_oficial_compra = Decimal(str(last_c.compra)) if (last_c and last_c.compra) else Decimal("1.00")

    # Calendario de cuotas existentes hijas no pagadas que vencen después del fin de ciclo actual
    tx_cuotas = db.execute(
        select(Cuota.fecha_vencimiento, monto_efectivo_cuota_sql(), GrupoCuotas.moneda)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .where(
            GrupoCuotas.usuario_id == usuario.id,
            Cuota.pagada.is_(False),
            Cuota.fecha_vencimiento > fin_ciclo,
        )
    ).all()

    cuotas_futuras: list[tuple[date, Decimal]] = []
    for f_venc, m_proy, mon in tx_cuotas:
        m_dec = Decimal(str(m_proy))
        mon_str = mon.value if hasattr(mon, "value") else str(mon).upper()
        if mon_str == "USD":
            m_dec = (m_dec * cot_oficial_venta).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        cuotas_futuras.append((f_venc, m_dec))

    # Ciclos futuros k = 1..H (al menos 36 ciclos para horizonte amplio)
    ciclos_futuros: list[tuple[date, date]] = []
    p = fin_ciclo + timedelta(days=1)
    for _ in range(36):
        ini_c, fin_c = get_ciclo_fechas(usuario, p)
        ciclos_futuros.append((ini_c, fin_c))
        p = fin_c + timedelta(days=1)

    liberaciones = liberacion_por_mes(cuotas_futuras, ciclos_futuros)

    # 6. Colchón
    gastos_ciclos = gastos_variables_por_ciclo(
        db, usuario, hoy=ref_hoy, moneda=Moneda.ARS, max_ciclos=12, streams_c=(None if es_admin else [])
    )
    colchon = desvio_absoluto_mediano(gastos_ciclos)

    # 6b. Quien cobra en dólares: su ingreso sale de ahí y fin de mes se arma con sus gastos
    if ingreso is None and info_dolares.ingreso_usd is not None:
        ingreso = (info_dolares.ingreso_usd * cot_oficial_compra).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        ingreso_temprano = False
        motivo_ingreso = None
        if estado_fdm == "sin_ingreso":
            finales = finales_de_quien_cobra_en_dolares(db, usuario, fdm, ref_hoy)

    # 6c. Lo que le sobra por mes (decisión 4A) y margen de cada ciclo futuro
    ids_fijos = _ids_de_fijos(db, usuario, ref_hoy, es_admin)
    netos = _netos_por_ciclo(
        db, usuario, ref_hoy, gastos_ciclos, usa_dolares, cot_oficial_compra, ids_fijos["USD"]
    )
    sobrante = sobrante_prudente(netos)
    if ingreso is not None:
        # Sin 3 ciclos para saber cuánto le sobra, queda la cuenta de antes (ingreso - gasto)
        base_margen = sobrante if sobrante is not None else ingreso - gasto_variable
        margenes = [
            (base_margen - lib - suscripciones - fijos).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            for lib in liberaciones
        ]
    else:
        margenes = []

    # 7. Ahorros
    billeteras_inv = db.execute(
        select(Billetera).where(
            Billetera.usuario_id == usuario.id,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.es_inversion.is_(True),
        )
    ).scalars().all()

    inversiones_total = Decimal("0.00")
    for b in billeteras_inv:
        s = Decimal(str(b.saldo_actual))
        mon_b = b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda).upper()
        if mon_b == "USD":
            s = (s * cot_oficial_compra).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        inversiones_total += s

    dolares_usd = max(fdm.libre_usd, Decimal("0.00"))
    dolares_en_pesos = (dolares_usd * cot_oficial_compra).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    metas_activas = db.execute(
        select(Meta).where(
            Meta.usuario_id == usuario.id,
            Meta.estado == EstadoMeta.ACTIVA,
        )
    ).scalars().all()

    metas_total = Decimal("0.00")
    for m in metas_activas:
        m_act = Decimal(str(m.monto_actual))
        mon_m = m.moneda.value if hasattr(m.moneda, "value") else str(m.moneda).upper()
        if mon_m == "USD":
            m_act = (m_act * cot_oficial_compra).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        metas_total += m_act

    # Los dólares libres son ahorro salvo para quien los usa seguido (decisión 2B)
    dolares_ahorro = Decimal("0.00") if usa_dolares else dolares_en_pesos
    ahorros_total = (inversiones_total + dolares_ahorro + metas_total).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # 8. Rubros recortables para compensar (decisión 3 A+B; hasta 6 ciclos cerrados, mínimo 3)
    rubros_medianas = medianas_recortables(db, usuario, ref_hoy, ids_fijos["ARS"])

    return DatosPermitirse(
        libre=libre,
        fin_ciclo=fin_ciclo,
        estado_fdm=estado_fdm,
        finales=finales,
        dias_sin_cargar=dias_sin_cargar,
        ingreso=ingreso,
        es_admin=es_admin,
        suscripciones=suscripciones,
        fijos=fijos,
        gasto_variable=gasto_variable,
        cuotas_futuras=cuotas_futuras,
        ciclos_futuros=ciclos_futuros,
        liberaciones=liberaciones,
        margenes=margenes,
        colchon=colchon,
        inversiones=inversiones_total,
        dolares_usd=dolares_usd,
        dolares_en_pesos=dolares_en_pesos,
        metas=metas_total,
        ahorros_total=ahorros_total,
        rubros_medianas=rubros_medianas,
        ingreso_temprano=ingreso_temprano,
        motivo_ingreso=motivo_ingreso,
        sobrante=sobrante,
        sin_sobrante=sobrante is None,
        usa_dolares=usa_dolares,
        cotizacion_venta=cot_oficial_venta,
    )


# ==============================================================================
# FUNCIÓN PRINCIPAL DEL MOTOR
# ==============================================================================

def _moneda_del_precio(moneda: str | None, usuario: Usuario) -> str:
    """La moneda del precio: la que llega o, si no llega, la moneda principal de la persona."""
    if moneda:
        return "USD" if str(moneda).upper() == "USD" else "ARS"
    principal = usuario.moneda_principal
    principal_str = principal.value if hasattr(principal, "value") else str(principal or "")
    return "USD" if principal_str.upper() == "USD" else "ARS"


def calcular_puede_permitirse(
    user_id: str | UUID,
    precio_total: float | Decimal,
    modo: str,
    cantidad_cuotas: int = 1,
    tiene_interes: bool = False,
    tna: float | Decimal | None = None,
    ingreso_manual: float | Decimal | None = None,
    db: Session | None = None,
    hoy: date | None = None,
    moneda: str | None = None,
) -> dict[str, Any]:
    """
    Función canónica de "¿Me lo puedo permitir?".
    Mantiene nombres y orden de argumentos. Devuelve el dict estructurado de la respuesta.
    `moneda` es la moneda del precio ("ARS" o "USD"); sin ella vale la moneda principal.
    """
    if db is None:
        raise ValueError("Se requiere una sesión de base de datos activa")

    usuario = db.get(Usuario, user_id)
    if not usuario:
        raise ValueError(f"Usuario con id {user_id} no encontrado")

    ref_hoy = hoy or hoy_argentina()

    # Recolectar datos financieros
    datos = recolectar(db, usuario, hoy=ref_hoy)
    return resolver_consulta(
        db, usuario, datos, precio_total, modo, cantidad_cuotas, tiene_interes, tna, ref_hoy, moneda
    )


def resolver_consulta(
    db: Session,
    usuario: Usuario,
    datos: DatosPermitirse,
    precio_total: float | Decimal,
    modo: str,
    cantidad_cuotas: int = 1,
    tiene_interes: bool = False,
    tna: float | Decimal | None = None,
    hoy: date | None = None,
    moneda: str | None = None,
) -> dict[str, Any]:
    """
    Resuelve una consulta con los datos ya recolectados (una misma recolección sirve para
    varias consultas). Las cuentas se hacen siempre en pesos.
    """
    ref_hoy = hoy or hoy_argentina()
    moneda_precio = _moneda_del_precio(moneda, usuario)
    precio_ingresado = Decimal(str(precio_total))
    if moneda_precio == "USD":
        # Un precio en dólares se pasa a pesos con el oficial venta de hoy
        precio_dec = (precio_ingresado * datos.cotizacion_venta).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        precio_dec = precio_ingresado
    tna_dec = Decimal(str(tna)) if (tiene_interes and tna is not None) else None

    # Normalizar modo
    modo_norm = str(modo).lower().strip()
    if modo_norm not in ("contado", "cuotas", "mensual"):
        modo_norm = "contado"

    if cantidad_cuotas < 1:
        cantidad_cuotas = 1

    # Plata de todos los días: lo libre en pesos y, para quien los usa seguido, sus dólares
    dolares_dia = datos.dolares_en_pesos if datos.usa_dolares else Decimal("0.00")
    libre_c = datos.libre + dolares_dia
    finales_c = tuple(f + dolares_dia for f in datos.finales)
    plata_cuentas = plata_de_cuentas(datos.finales, datos.libre, dolares_dia)
    # Sin lo que le sobra por mes no se puede decir "bien" en cuotas ni en todos los meses
    colchon_eval = None if datos.sin_sobrante else datos.colchon

    # Determinar motivo
    if datos.estado_fdm == "datos_viejos":
        motivo = "datos_viejos"
    elif datos.ingreso is None:
        motivo = "sin_ingreso"
    elif modo_norm in ("cuotas", "mensual") and datos.ingreso_temprano:
        motivo = "aprendiendo"
    elif modo_norm == "contado" and not finales_c:
        motivo = "pocos_datos"
    elif modo_norm in ("cuotas", "mensual") and colchon_eval is None:
        motivo = "pocos_datos"
    else:
        motivo = None

    plan: PlanCuotas | None = None
    fuente_inflacion: str | None = None
    veces = 0
    n = 0
    falta = Decimal("0.00")
    falta_bruta: Decimal | None = None
    min_finales: Decimal | None = None
    max_finales: Decimal | None = None
    r_min = Decimal("0.00")
    deficit_mensual = Decimal("0.00")
    usa_cuentas = False
    uso_mensual = Decimal("0.00")
    meses_cuentas = 0
    meses_total = 0
    cuotas_eval: list[Decimal] = []

    # 1. MODO CONTADO
    if modo_norm == "contado":
        resultado, veces, n, falta = evaluar_contado(
            precio=precio_dec,
            libre=libre_c,
            finales=finales_c,
            ahorros_total=datos.ahorros_total,
        )
        if finales_c:
            min_finales = min(finales_c)
            max_finales = max(finales_c)
        if precio_dec > libre_c:
            falta_bruta = precio_dec - libre_c
        elif finales_c:
            falta_bruta = precio_dec - mediana(finales_c)

    # 2. MODO CUOTAS O MENSUAL
    else:
        if modo_norm == "cuotas":
            inf_res = inflacion_esperada(db, ref_hoy, cantidad_cuotas)
            tasas_inf = inf_res.tasas if inf_res else None
            fuente_inflacion = inf_res.fuente_texto if inf_res else None

            plan = calcular_plan_cuotas(
                precio=precio_dec,
                cantidad=cantidad_cuotas,
                tna=tna_dec,
                inflacion_mensual=tasas_inf,
            )
            monto_cuota = plan.monto_cuota
            cuotas_eval = [monto_cuota] * cantidad_cuotas
        else:
            # Modo mensual ("Todos los meses")
            monto_cuota = precio_dec
            cuotas_eval = [monto_cuota] * len(datos.margenes)

        if datos.ingreso is None:
            # 3. Sin ingreso (modos cuotas y mensual): compara con lo libre y los ahorros
            monto_comparar = plan.total if (modo_norm == "cuotas" and plan) else monto_cuota
            if monto_comparar <= libre_c:
                resultado = "justo"
                falta = Decimal("0.00")
            elif monto_comparar <= libre_c + datos.ahorros_total:
                resultado = "ahorros"
                falta = monto_comparar - libre_c
            elif datos.ahorros_total > Decimal("0"):
                resultado = "no_alcanza"
                falta = monto_comparar - libre_c - datos.ahorros_total
            else:
                resultado = "no_alcanza"
                falta = monto_comparar - libre_c
        elif modo_norm == "cuotas":
            ev = evaluar_cuotas_cuentas(
                cuotas=cuotas_eval,
                margenes=datos.margenes,
                colchon=colchon_eval,
                B=plata_cuentas,
                ahorros=datos.ahorros_total,
            )
            resultado = ev["resultado"]
            r_min = ev["r_min"]
            falta = ev["falta"]
            usa_cuentas = ev["usa_cuentas"]
            uso_mensual = ev["uso_mensual"]
            con_interes = plan is not None and plan.interes > Decimal("0")
            if not con_interes:
                # Pagar lo mismo más tarde nunca puede ser más difícil que pagarlo de contado
                res_contado = evaluar_contado(
                    precio_dec, libre_c, finales_c, datos.ahorros_total
                )[0]
                con_piso = piso_por_contado(resultado, res_contado, con_interes)
                if con_piso != resultado:
                    resultado = con_piso
                    falta = Decimal("0.00")
                    usa_cuentas = True
                    if ev["meses_deficit"] > 0:
                        uso_mensual = redondear_mil(
                            ev["deficit"] / Decimal(ev["meses_deficit"])
                        )
        else:
            ev = evaluar_mensual_cuentas(
                precio=precio_dec,
                margenes=datos.margenes,
                colchon=colchon_eval,
                B=plata_cuentas,
                ahorros=datos.ahorros_total,
            )
            resultado = ev["resultado"]
            r_min = ev["r_min"]
            deficit_mensual = ev["deficit_mensual"]
            usa_cuentas = ev["usa_cuentas"]
            meses_cuentas = ev["meses_cuentas"]
            meses_total = ev["meses_total"]

    resultado = tope_por_ingreso_temprano(resultado, modo_norm, datos.ingreso_temprano)
    resultado = tope_por_datos_viejos(resultado, motivo)

    # 4. Mes de liberación: solo dentro de las cuotas de esta compra (o del horizonte en
    # "todos los meses") y solo si en ese ciclo bajan las cuotas que ya tenía
    mes_liberacion_nombre: str | None = None
    hay_margen = datos.colchon is not None and datos.ingreso is not None
    if modo_norm in ("cuotas", "mensual") and hay_margen and resultado != "bien":
        j = mes_liberacion(datos.margenes, cuotas_eval, datos.colchon, datos.liberaciones)
        if j is not None and 1 <= j <= len(datos.ciclos_futuros):
            c_ini_j, c_fin_j = datos.ciclos_futuros[j - 1]
            nombre_mes_bruto = nombre_hoja_ciclo(c_ini_j, c_fin_j)
            mes_liberacion_nombre = nombre_mes_bruto.split()[0].lower()

    # 5. Compensar: solo con rubros recortables (decisión 3 A+B)
    rubro_compensar: str | None = None
    monto_compensar: Decimal | None = None
    reintegro: tuple[Decimal, str] | None = None

    if resultado in ("justo", "ahorros"):
        cand_rubro = elegir_rubro_recortable(datos.rubros_medianas)
        X: Decimal | None = None
        if modo_norm == "contado":
            if precio_dec <= libre_c and finales_c:
                X = redondear_arriba_mil(precio_dec - min(finales_c))
        elif hay_margen:
            H_eval = min(len(cuotas_eval), len(datos.margenes))
            if H_eval > 0:
                r_eval = [datos.margenes[k] - cuotas_eval[k] for k in range(H_eval)]
                X_raw = max((datos.colchon - r for r in r_eval), default=Decimal("0.00"))
                X = redondear_arriba_mil(X_raw)
        if cand_rubro and X is not None:
            if compensar_tope_50(X, datos.rubros_medianas[cand_rubro]):
                rubro_compensar = cand_rubro
                monto_compensar = X
        if rubro_compensar and monto_compensar:
            reintegro = reintegro_del_rubro(db, usuario, ref_hoy, rubro_compensar)

    # Ensamblar textos unificados
    datos_dict_texto = {
        "modo": modo_norm,
        "resultado": resultado,
        "motivo": motivo,
        "motivo_ingreso": datos.motivo_ingreso,
        "sin_ingreso": datos.ingreso is None,
        "precio": precio_dec,
        "libre": libre_c,
        "fin_ciclo": datos.fin_ciclo,
        "dias_sin_cargar": datos.dias_sin_cargar,
        "ahorros_total": datos.ahorros_total,
        "veces": veces,
        "n": n,
        "falta": falta,
        "min_finales": min_finales,
        "max_finales": max_finales,
        "r_min": r_min,
        "deficit_mensual": deficit_mensual,
        "plan": plan,
        "cantidad_cuotas": cantidad_cuotas,
        "fuente_inflacion": fuente_inflacion,
        "mes_liberacion_nombre": mes_liberacion_nombre,
        "rubro_compensar": rubro_compensar,
        "monto_compensar": monto_compensar,
        "moneda": moneda_precio,
        "cotizacion": datos.cotizacion_venta if moneda_precio == "USD" else None,
        "usa_cuentas": usa_cuentas,
        "uso_mensual": uso_mensual,
        "meses_cuentas": meses_cuentas,
        "meses_total": meses_total,
        "sobrante": datos.sobrante,
        "falta_bruta": falta_bruta,
        "reintegro": reintegro,
    }

    textos = textos_permitirse(datos_dict_texto)

    # Estructura del bloque ahorros para la API
    bloque_ahorros: dict[str, float] | None = None
    if resultado in ("ahorros", "no_alcanza"):
        # Los dólares de quien los usa seguido no son ahorro: no van en el detalle
        dolares_ahorro_usd = Decimal("0.00") if datos.usa_dolares else datos.dolares_usd
        dolares_ahorro_pesos = Decimal("0.00") if datos.usa_dolares else datos.dolares_en_pesos
        bloque_ahorros = {
            "inversiones": float(datos.inversiones),
            "dolares_usd": float(dolares_ahorro_usd),
            "dolares_en_pesos": float(dolares_ahorro_pesos),
            "metas": float(datos.metas),
            "total": float(datos.ahorros_total),
        }

    return {
        "modo": modo_norm,
        "resultado": resultado,
        "motivo": motivo,
        "titulo": textos["titulo"],
        "linea_principal": textos["linea_principal"],
        "lineas_plan": textos["lineas_plan"],
        "linea_ahorros": textos["linea_ahorros"],
        "ahorros": bloque_ahorros,
        "linea_liberacion": textos["linea_liberacion"],
        "linea_compensar": textos["linea_compensar"],
        "linea_frescura": textos["linea_frescura"],
        "linea_espera": textos["linea_espera"],
        "linea_cotizacion": textos["linea_cotizacion"],
        "linea_cuentas": textos["linea_cuentas"],
        "linea_reintegro": textos["linea_reintegro"],
        "linea_juntar": textos["linea_juntar"],
        "libre": float(datos.libre),
        "precio_total": float(precio_ingresado),
        "moneda": moneda_precio,
        "fuente_inflacion": fuente_inflacion,
    }
