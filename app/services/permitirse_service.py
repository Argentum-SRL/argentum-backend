"""
app/services/permitirse_service.py â€” Motor unificado de "Â¿Me lo puedo permitir?".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Any, Set
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
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
from app.services.definiciones_service import condicion_gasto
from app.services.dolar_service import obtener_cotizacion_por_fecha
from app.services.fin_de_mes_service import calcular_fin_de_mes
from app.services.inflacion_esperada_service import inflacion_esperada
from app.services.ingreso_habitual_service import obtener_ingreso_habitual
from app.services.permitirse_textos import textos_permitirse
from app.services.presupuestos_excel_datos import nombre_hoja_ciclo
from app.services.tools_service import PlanCuotas, calcular_plan_cuotas
from app.utils.fecha import hoy_argentina


# ==============================================================================
# FUNCIONES PURAS DE CÃLCULO
# ==============================================================================

def mediana(valores: list[Decimal] | tuple[Decimal, ...]) -> Decimal:
    """Calcula la mediana de una secuencia de Decimals."""
    if not valores:
        raise ValueError("No se puede calcular mediana de lista vacÃ­a")
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
) -> int | None:
    """
    El primer j >= 2 tal que r_k >= colchon para todo k >= j y r_{j-1} < colchon.
    Si no existe o colchon es None, devuelve None.
    j es 1-based (k=1 ciclo siguiente, k=2 ciclo posterior).
    Pura.
    """
    if colchon is None or not margenes or not cuotas:
        return None

    H = min(len(margenes), len(cuotas))
    if H < 2:
        return None

    r = [margenes[k] - cuotas[k] for k in range(H)]

    for j in range(2, H + 1):  # j en 2..H (1-based)
        idx = j - 1  # 0-based
        if r[idx - 1] < colchon:
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
    """Redondea hacia arriba al mÃºltiplo de 1.000 mÃ¡s cercano."""
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
    EvalÃºa la compra de contado.
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

    # b. finales None o vacÃ­o (estado distinto de "completo")
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
    EvalÃºa cuotas o gasto mensual recurrente.
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
# RECOLECCIÃ“N DE DATOS (SOLO LECTURA)
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


def recolectar(db: Session, usuario: Usuario, hoy: date | None = None) -> DatosPermitirse:
    """
    Recolecta todos los datos financieros necesarios para el cÃ¡lculo de "Â¿Me lo puedo permitir?".
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
    res_ingreso = obtener_ingreso_habitual(db, usuario, hoy=ref_hoy, moneda="ARS")
    ingreso = res_ingreso.monto

    # 3. Compromisos mensuales
    es_admin = bool(usuario.is_admin)
    comp_res = calcular_compromisos_mensuales(db, usuario, hoy=ref_hoy, moneda=Moneda.ARS)
    suscripciones = comp_res.suscripciones
    fijos = comp_res.fijos if es_admin else Decimal("0.00")

    if es_admin:
        gasto_variable = calcular_gasto_variable_tipico(db, usuario, hoy=ref_hoy, moneda=Moneda.ARS)
    else:
        gasto_variable = calcular_gasto_variable_tipico(db, usuario, hoy=ref_hoy, moneda=Moneda.ARS, streams_c=[])

    # 4. Cotizaciones dÃ³lar oficial (venta para pesificar cuotas, compra para pesificar ahorros)
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

    # Calendario de cuotas existentes hijas no pagadas que vencen despuÃ©s del fin de ciclo actual
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
    if ingreso is not None:
        margenes = [
            (ingreso - lib - suscripciones - fijos - gasto_variable).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            for lib in liberaciones
        ]
    else:
        margenes = []

    # 6. ColchÃ³n
    gastos_ciclos = gastos_variables_por_ciclo(
        db, usuario, hoy=ref_hoy, moneda=Moneda.ARS, max_ciclos=12, streams_c=(None if es_admin else [])
    )
    colchon = desvio_absoluto_mediano(gastos_ciclos)

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

    ahorros_total = (inversiones_total + dolares_en_pesos + metas_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 8. Rubros para compensar (Ãºltimos hasta 6 ciclos cerrados, mÃ­nimo 3)
    c_ini_curr, _ = get_ciclo_fechas(usuario, ref_hoy)
    fecha_fin_c1 = c_ini_curr - timedelta(days=1)

    primera_tx = db.execute(
        select(Transaccion.fecha)
        .where(Transaccion.usuario_id == usuario.id)
        .order_by(Transaccion.fecha.asc())
        .limit(1)
    ).scalar_one_or_none()

    if primera_tx is None:
        primera_tx = usuario.fecha_registro.date()

    ciclos_rubros: list[tuple[date, date]] = []
    if primera_tx <= fecha_fin_c1:
        p_c = fecha_fin_c1
        while p_c >= primera_tx and len(ciclos_rubros) < 6:
            ini_r, fin_r = get_ciclo_fechas(usuario, p_c)
            if fin_r <= fecha_fin_c1:
                ciclos_rubros.append((ini_r, fin_r))
                p_c = ini_r - timedelta(days=1)
            else:
                break

    rubros_medianas: dict[str, Decimal] = {}
    if len(ciclos_rubros) >= 3:
        EXCLUIDAS = {"vivienda", "salud", "educacion", "educaciÃ³n", "impuestos", "banco", "ahorro", "inversiones"}

        grupos_varias_cuotas = set(
            db.execute(
                select(GrupoCuotas.id).where(
                    GrupoCuotas.usuario_id == usuario.id,
                    GrupoCuotas.cantidad_cuotas > 1,
                )
            ).scalars().all()
        )

        streams_c = None
        if not es_admin:
            streams_c = []
        else:
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
            streams_c = [
                s for s in res_clasif.streams
                if s.clase == "COMPROMISO" and s.estado == "MADURO" and s.senal != "DECLARADO"
            ]

        tx_ids_streams_c: Set[UUID] = set()
        for s in streams_c:
            if normalizar_moneda(getattr(s, "moneda", "")) == "ARS":
                tx_ids_streams_c.update(getattr(s, "transacciones_ids", []))

        start_rubros = ciclos_rubros[-1][0]
        end_rubros = ciclos_rubros[0][1]

        cond_gas = condicion_gasto(usuario.id, start_rubros, end_rubros, hoy=ref_hoy)
        txs_rubros = db.execute(
            select(Transaccion).where(cond_gas, Transaccion.moneda == Moneda.ARS)
        ).scalars().all()

        categorias_map = {
            c.id: c.nombre
            for c in db.execute(select(Categoria.id, Categoria.nombre)).all()
        }

        # Agrupar transacciones por categorÃ­a y por ciclo
        cat_por_ciclo: dict[str, list[Decimal]] = {}
        for c_ini, c_fin in ciclos_rubros:
            gastos_cat_este_ciclo: dict[str, Decimal] = {}
            for t in txs_rubros:
                if c_ini <= t.fecha <= c_fin:
                    if t.es_cuota_hija and t.grupo_cuotas_id in grupos_varias_cuotas:
                        continue
                    if t.suscripcion_id is not None:
                        continue
                    if t.id in tx_ids_streams_c:
                        continue
                    cat_nom = categorias_map.get(t.categoria_id)
                    if not cat_nom or cat_nom.strip().lower() in EXCLUIDAS:
                        continue
                    gastos_cat_este_ciclo[cat_nom] = gastos_cat_este_ciclo.get(cat_nom, Decimal("0.00")) + Decimal(str(t.monto))

            # Para todas las categorÃ­as conocidas, anotar el gasto de este ciclo (0 si no hubo)
            for cat_nom in set(gastos_cat_este_ciclo.keys()) | set(cat_por_ciclo.keys()):
                if cat_nom not in cat_por_ciclo:
                    cat_por_ciclo[cat_nom] = []
                cat_por_ciclo[cat_nom].append(gastos_cat_este_ciclo.get(cat_nom, Decimal("0.00")))

        for cat_nom, valores in cat_por_ciclo.items():
            if valores:
                med_cat = mediana(valores)
                if med_cat > Decimal("0.00"):
                    rubros_medianas[cat_nom] = med_cat

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
    )


# ==============================================================================
# FUNCIÃ“N PRINCIPAL DEL MOTOR
# ==============================================================================

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
) -> dict[str, Any]:
    """
    FunciÃ³n canÃ³nica de "Â¿Me lo puedo permitir?".
    Mantiene nombres y orden de argumentos. Devuelve el dict estructurado de la respuesta.
    """
    if db is None:
        raise ValueError("Se requiere una sesiÃ³n de base de datos activa")

    usuario = db.get(Usuario, user_id)
    if not usuario:
        raise ValueError(f"Usuario con id {user_id} no encontrado")

    ref_hoy = hoy or hoy_argentina()
    precio_dec = Decimal(str(precio_total))
    tna_dec = Decimal(str(tna)) if (tiene_interes and tna is not None) else None

    # Normalizar modo
    modo_norm = str(modo).lower().strip()
    if modo_norm not in ("contado", "cuotas", "mensual"):
        modo_norm = "contado"

    if cantidad_cuotas < 1:
        cantidad_cuotas = 1

    # Recolectar datos financieros
    datos = recolectar(db, usuario, hoy=ref_hoy)

    # Determinar motivo
    if datos.estado_fdm == "datos_viejos":
        motivo = "datos_viejos"
    elif datos.ingreso is None:
        motivo = "sin_ingreso"
    elif modo_norm == "contado" and not datos.finales:
        motivo = "pocos_datos"
    elif modo_norm in ("cuotas", "mensual") and datos.colchon is None:
        motivo = "pocos_datos"
    else:
        motivo = None

    plan: PlanCuotas | None = None
    fuente_inflacion: str | None = None
    veces = 0
    n = 0
    falta = Decimal("0.00")
    min_finales: Decimal | None = None
    max_finales: Decimal | None = None
    r_min = Decimal("0.00")
    deficit_total = Decimal("0.00")
    meses_deficit = 0
    deficit_mensual = Decimal("0.00")
    meses_alcanzan = 0
    cuotas_eval: list[Decimal] = []

    # 1. MODO CONTADO
    if modo_norm == "contado":
        resultado, veces, n, falta = evaluar_contado(
            precio=precio_dec,
            libre=datos.libre,
            finales=datos.finales,
            ahorros_total=datos.ahorros_total,
        )
        if datos.finales:
            min_finales = min(datos.finales)
            max_finales = max(datos.finales)

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
            indefinido = False
        else:
            # Modo mensual ("Todos los meses")
            monto_cuota = precio_dec
            cuotas_eval = [monto_cuota] * len(datos.margenes)
            indefinido = True

        if datos.ingreso is None:
            # 3. Sin ingreso (modos cuotas y mensual)
            monto_comparar = plan.total if (modo_norm == "cuotas" and plan) else monto_cuota
            if monto_comparar <= datos.libre:
                resultado = "justo"
                falta = Decimal("0.00")
            elif monto_comparar <= datos.libre + datos.ahorros_total:
                resultado = "ahorros"
                falta = monto_comparar - datos.libre
            else:
                resultado = "no_alcanza"
                falta = (monto_comparar - datos.libre - datos.ahorros_total) if datos.ahorros_total > Decimal("0") else (monto_comparar - datos.libre)
        else:
            resultado, r_min, deficit_total, meses_deficit, deficit_mensual, meses_alcanzan = evaluar_mensual(
                cuotas=cuotas_eval,
                margenes=datos.margenes,
                colchon=datos.colchon,
                ahorros_total=datos.ahorros_total,
                indefinido=indefinido,
            )

    # 4. Mes de liberaciÃ³n
    mes_liberacion_nombre: str | None = None
    if modo_norm in ("cuotas", "mensual") and datos.colchon is not None and resultado != "bien" and datos.ingreso is not None:
        # Construir cuotas para el horizonte completo
        H_total = min(len(datos.margenes), len(datos.ciclos_futuros))
        if modo_norm == "cuotas":
            cuotas_horizonte = [plan.monto_cuota if plan else Decimal("0.00")] * cantidad_cuotas + [Decimal("0.00")] * max(0, H_total - cantidad_cuotas)
        else:
            cuotas_horizonte = [precio_dec] * H_total

        j = mes_liberacion(datos.margenes[:H_total], cuotas_horizonte[:H_total], datos.colchon)
        if j is not None and 1 <= j <= len(datos.ciclos_futuros):
            c_ini_j, c_fin_j = datos.ciclos_futuros[j - 1]
            nombre_mes_bruto = nombre_hoja_ciclo(c_ini_j, c_fin_j)
            mes_liberacion_nombre = nombre_mes_bruto.split()[0].lower()

    # 5. Compensar
    rubro_compensar: str | None = None
    monto_compensar: Decimal | None = None

    if resultado in ("justo", "ahorros"):
        if modo_norm == "contado":
            if precio_dec <= datos.libre and datos.finales:
                X_raw = precio_dec - min(datos.finales)
                X = redondear_arriba_mil(X_raw)
                cand_rubro = elegir_rubro(datos.rubros_medianas)
                if cand_rubro and compensar_tope_50(X, datos.rubros_medianas[cand_rubro]):
                    rubro_compensar = cand_rubro
                    monto_compensar = X
        else:
            if datos.colchon is not None and datos.ingreso is not None:
                H_eval = min(len(cuotas_eval), len(datos.margenes))
                if H_eval > 0:
                    r_eval = [datos.margenes[k] - cuotas_eval[k] for k in range(H_eval)]
                    X_raw = max((datos.colchon - r for r in r_eval), default=Decimal("0.00"))
                    X = redondear_arriba_mil(X_raw)
                    cand_rubro = elegir_rubro(datos.rubros_medianas)
                    if cand_rubro and compensar_tope_50(X, datos.rubros_medianas[cand_rubro]):
                        rubro_compensar = cand_rubro
                        monto_compensar = X

    # Ensamblar textos unificados
    datos_dict_texto = {
        "modo": modo_norm,
        "resultado": resultado,
        "motivo": motivo,
        "precio": precio_dec,
        "libre": datos.libre,
        "fin_ciclo": datos.fin_ciclo,
        "dias_sin_cargar": datos.dias_sin_cargar,
        "ahorros_total": datos.ahorros_total,
        "veces": veces,
        "n": n,
        "falta": falta,
        "min_finales": min_finales,
        "max_finales": max_finales,
        "r_min": r_min,
        "deficit_total": deficit_total,
        "meses_deficit": meses_deficit,
        "deficit_mensual": deficit_mensual,
        "meses_alcanzan": meses_alcanzan,
        "plan": plan,
        "cantidad_cuotas": cantidad_cuotas,
        "fuente_inflacion": fuente_inflacion,
        "mes_liberacion_nombre": mes_liberacion_nombre,
        "rubro_compensar": rubro_compensar,
        "monto_compensar": monto_compensar,
    }

    textos = textos_permitirse(datos_dict_texto)

    # Estructura del bloque ahorros para la API
    bloque_ahorros: dict[str, float] | None = None
    if resultado in ("ahorros", "no_alcanza"):
        bloque_ahorros = {
            "inversiones": float(datos.inversiones),
            "dolares_usd": float(datos.dolares_usd),
            "dolares_en_pesos": float(datos.dolares_en_pesos),
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
        "libre": float(datos.libre),
        "precio_total": float(precio_dec),
        "fuente_inflacion": fuente_inflacion,
    }

