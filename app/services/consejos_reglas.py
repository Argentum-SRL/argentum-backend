# -*- coding: utf-8 -*-
"""
app/services/consejos_reglas.py
Funciones puras y constantes para el motor de consejos de Argentum.
Sin acceso a base de datos, sin red, sin hoy_argentina.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING
from typing import Any

from app.models.usuario import Moneda
from app.utils.finanzas import mediana, mad, deflactar_monto
from app.utils.formato import formatear_monto

# ==============================================================================
# B-C. CONSTANTES
# ==============================================================================

ESENCIALES_CATEGORIA: frozenset[str] = frozenset({
    "Vivienda",
    "Salud",
    "Educación",
    "Impuestos",
    "Banco",
})

ESENCIALES_SUBCATEGORIA: frozenset[tuple[str, str]] = frozenset({
    ("Alimentación", "Supermercado"),
    ("Alimentación", "Verdulería"),
    ("Alimentación", "Carnicería"),
    ("Alimentación", "Panadería"),
    ("Transporte", "Transporte público"),
    ("Transporte", "Combustible"),
})

EXCLUIDAS_CONSEJOS: frozenset[tuple[str, str]] = frozenset({
    ("Otros", "Pagos a personas"),
    ("Otros", "Ayuda familiar"),
    ("Hogar", "Servicio doméstico"),
})

CATEGORIAS_PICO: frozenset[str] = frozenset({
    "Gastronomía",
    "Hogar",
    "Cuidado personal",
    "Ocio",
    "Indumentaria",
    "Tecnología",
    "Otros",
})

CATEGORIAS_SIN_PRESUPUESTO: frozenset[str] = frozenset(
    CATEGORIAS_PICO - {"Otros"}
)

NUMERO_CATALOGO: dict[str, int] = {
    "hormiga": 1,
    "plata_quieta": 2,
    "colchon": 3,
    "comisiones": 4,
    "pico": 5,
    "ritmo": 6,
    "sin_presupuesto": 7,
    "carga": 8,
    "extra": 9,
    "meta": 10,
    "subio": 11,
    "arrastre": 12,
    "dolares": 13,
    "sin_rubro": 14,
    "sin_ingresos": 15,
}

PUNTOS_PRIORIDAD: dict[int, dict[str, int]] = {
    1: {"sin_rubro": 1, "sin_ingresos": 1},
    2: {"colchon": 1, "arrastre": 1},
    3: {"plata_quieta": 1},
    4: {"meta": 1},
    5: {"arrastre": 1, "carga": 1},
    6: {},
    7: {"hormiga": -1, "pico": -1},
    8: {"colchon": 1, "carga": 1},
}

ESPERA_DIAS: dict[str, int] = {
    "sin_rubro": 60,
    "sin_ingresos": 60,
}

TEXTO_ACCION: dict[str, str] = {
    "crear_meta": "Crear meta",
    "ver_movimientos": "Ver movimientos",
    "cargar_ingreso": "Cargar ingreso",
    "ver_presupuestos": "Ver presupuestos",
    "armar_presupuesto": "Armar uno",
}


def fuera_de_consejos(f: FilaGasto) -> bool:
    """Indica si una fila de gasto queda excluida de los cálculos de consejos."""
    if f.categoria in ESENCIALES_CATEGORIA:
        return True
    if f.subcategoria is not None:
        par = (f.categoria or "", f.subcategoria)
        if par in ESENCIALES_SUBCATEGORIA or par in EXCLUIDAS_CONSEJOS:
            return True
    return f.es_cuota


def es_compra(f: FilaGasto) -> bool:
    """Indica si una fila representa una compra efectiva (no espejo y monto > 0)."""
    return not f.es_espejo and f.monto > Decimal("0")


def redondeo(x: Decimal, moneda: str = "ARS") -> Decimal:
    """
    Redondeo estándar:
    - ARS: al múltiplo de 1.000 más cercano (ROUND_HALF_UP).
    - USD: al entero más cercano (ROUND_HALF_UP).
    """
    if moneda == "USD":
        return x.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (x / Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 1000


def redondeo_arriba(x: Decimal, moneda: str = "ARS") -> Decimal:
    """
    Redondeo hacia arriba:
    - ARS: al múltiplo de 1.000 superior (ROUND_CEILING).
    - USD: al entero superior (ROUND_CEILING).
    """
    if moneda == "USD":
        return x.quantize(Decimal("1"), rounding=ROUND_CEILING)
    return (x / Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_CEILING) * 1000


def m(x: Decimal | int | float, moneda: str = "ARS") -> str:
    """Formatea un monto como texto argentino sin decimales."""
    return formatear_monto(x, moneda, con_decimales=False)


# ==============================================================================
# B-D. ESTRUCTURAS
# ==============================================================================

@dataclass(frozen=True)
class FilaGasto:
    fecha: date
    monto: Decimal
    moneda: str  # "ARS" | "USD"
    categoria: str | None
    subcategoria: str | None
    es_espejo: bool = False
    es_cuota: bool = False


@dataclass(frozen=True)
class Consejo:
    tipo: str
    clave: str
    moneda: str | None
    texto: str
    accion: str | None = None
    accion_datos: dict[str, Any] | None = None
    valores: dict[str, Any] = field(default_factory=dict)
    ahorro: Decimal | None = None


@dataclass(frozen=True)
class FilaHistorial:
    tipo: str
    clave: str
    fecha_emision: date
    respuesta: str | None
    fecha_vence: date


@dataclass(frozen=True)
class RitmoPresupuesto:
    presupuesto_id: str
    nombre: str
    moneda: str
    inicio: date
    fin: date
    proyeccion: Decimal
    limite: Decimal


@dataclass(frozen=True)
class RubroSinPresupuesto:
    categoria: str
    moneda: str
    monto: Decimal
    mediana: Decimal


@dataclass(frozen=True)
class DatosConsejos:
    hoy: date
    hay_datos: bool
    moneda_principal: str
    datos_viejos: bool
    cobertura: int | None
    ciclo_actual: tuple[date, date]
    cerrados: list[tuple[date, date]]  # más nuevo primero, hasta 6
    filas: list[FilaGasto]
    ingresos_45: int
    libre_ars: Decimal
    saldo_hoy_ars: Decimal
    inversiones_ars: Decimal
    usd_total: Decimal
    cotizacion_compra: Decimal | None
    tasa_inflacion: Decimal | None
    mes_inflacion: str | None
    fuente_inflacion: str | None
    billetera_inversion: str | None
    tiene_meta_colchon: bool
    factor_cobertura: Decimal = Decimal("1")
    ipc: dict[str, Decimal] = field(default_factory=dict)
    ritmos: tuple[RitmoPresupuesto, ...] = ()
    categorias_con_presupuesto: frozenset[str] = frozenset()
    nombres_presupuestos: frozenset[str] = frozenset()
    ids_categorias: dict[str, str] = field(default_factory=dict)


# ==============================================================================
# B-E. CONSEJOS (Funciones puras)
# ==============================================================================

def tickets_90(filas: list[FilaGasto], moneda: str, hoy: date) -> list[Decimal]:
    """Montos de las compras de esa moneda que no son cuota, con hoy - 90 < fecha <= hoy."""
    limite = hoy - timedelta(days=90)
    return [
        f.monto for f in filas
        if f.moneda == moneda and es_compra(f) and not f.es_cuota and limite < f.fecha <= hoy
    ]


def evaluar_hormiga(
    filas_ciclo: list[FilaGasto],
    tickets90: list[Decimal],
    moneda: str,
    inicio_ciclo: date,
) -> Consejo | None:
    """C1: Gastos chicos y seguidos (hormiga). Solo filas de la moneda especificada."""
    compras = [f for f in filas_ciclo if f.moneda == moneda and es_compra(f)]
    if len(compras) < 20:
        return None

    gasto = sum((f.monto for f in filas_ciclo if f.moneda == moneda), Decimal("0"))
    med90 = mediana(tickets90)
    if med90 is None:
        return None

    # Agrupar por (categoria, subcategoria) sin fuera_de_consejos ni subcategoria None
    grupos: dict[tuple[str, str], list[FilaGasto]] = {}
    for f in filas_ciclo:
        if f.moneda != moneda or f.categoria is None or f.subcategoria is None:
            continue
        if fuera_de_consejos(f):
            continue
        grupos.setdefault((f.categoria, f.subcategoria), []).append(f)

    califican: list[tuple[tuple[str, str], Decimal, list[FilaGasto]]] = []
    for par, lista in grupos.items():
        compras_grupo = [f for f in lista if es_compra(f)]
        if len(compras_grupo) < 4:
            continue
        med_grupo = mediana([f.monto for f in compras_grupo])
        if med_grupo is not None and med_grupo * 2 <= med90:
            tot_grupo = sum((f.monto for f in lista), Decimal("0"))
            califican.append((par, tot_grupo, lista))

    if not califican:
        return None

    total = sum((t for _, t, _ in califican), Decimal("0"))
    if total <= Decimal("0") or total * Decimal("100") < gasto * Decimal("5"):
        return None

    tr = redondeo(total, moneda)
    if tr == Decimal("0"):
        return None

    anual = tr * Decimal("12")

    # Orden de grupos: total desc; empate por nombre de subcategoría asc
    califican.sort(key=lambda item: (-item[1], item[0][1]))

    subcategorias_ordenadas = [par[1] for par, _, _ in califican]
    pares_ordenados = [[par[0], par[1]] for par, _, _ in califican]

    cant = len(subcategorias_ordenadas)
    if cant == 1:
        texto = f"Lo que gastás en {subcategorias_ordenadas[0]}, de a poco, suma unos {m(tr, moneda)} por mes: {m(anual, moneda)} al año."
    elif cant == 2:
        texto = f"Los gastos chicos y seguidos ({subcategorias_ordenadas[0]} y {subcategorias_ordenadas[1]}) suman unos {m(tr, moneda)} por mes: {m(anual, moneda)} al año."
    else:
        texto = f"Los gastos chicos y seguidos ({subcategorias_ordenadas[0]}, {subcategorias_ordenadas[1]} y otros) suman unos {m(tr, moneda)} por mes: {m(anual, moneda)} al año."

    ahorro = anual * Decimal("0.2")
    valores = {
        "total": str(total.quantize(Decimal("0.01"))),
        "subcategorias": pares_ordenados,
    }
    clave = f"hormiga/{moneda}/{inicio_ciclo.isoformat()}"

    return Consejo(
        tipo="hormiga",
        clave=clave,
        moneda=moneda,
        texto=texto,
        valores=valores,
        ahorro=ahorro,
    )


def evaluar_plata_quieta(
    libre_ars: Decimal,
    gasto_tipico_ars: Decimal | None,
    tasa: Decimal | None,
    mes: str | None,
    fuente: str | None,
    billetera_inversion: str | None,
    hoy: date,
    inversiones_ars: Decimal,
) -> Consejo | None:
    """C2: Plata quieta que pierde contra la inflación. Solo pesos."""
    if gasto_tipico_ars is None or gasto_tipico_ars <= Decimal("0") or tasa is None:
        return None

    sobrante = libre_ars - gasto_tipico_ars
    if sobrante <= Decimal("0") or sobrante * Decimal("2") < gasto_tipico_ars:
        return None

    perdida = sobrante * tasa / Decimal("100")
    sr = redondeo(sobrante, "ARS")
    pr = redondeo(perdida, "ARS")
    if sr == Decimal("0") or pr == Decimal("0"):
        return None

    tasa_fmt = str(tasa.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)).replace(".", ",")

    tail = (
        f" Podés pasar una parte a {billetera_inversion}."
        if billetera_inversion
        else " Fijate si tu billetera virtual tiene la opción de rendimiento o intereses."
    )
    texto = (
        f"Tenés unos {m(sr, 'ARS')} que este mes no vas a necesitar. "
        f"Con la inflación esperada ({tasa_fmt}% en {mes}, {fuente}), "
        f"pierden unos {m(pr, 'ARS')} por mes si quedan quietos.{tail}"
    )

    clave = f"plata_quieta/ARS/{hoy:%Y-%m}"
    ahorro = perdida * Decimal("12")
    valores = {
        "sobrante": str(sobrante.quantize(Decimal("0.01"))),
        "inversiones": str(inversiones_ars.quantize(Decimal("0.01"))),
    }

    return Consejo(
        tipo="plata_quieta",
        clave=clave,
        moneda="ARS",
        texto=texto,
        valores=valores,
        ahorro=ahorro,
    )


def evaluar_colchon(
    disponible: Decimal,
    gasto_tipico: Decimal | None,
    moneda: str,
    tiene_meta_colchon: bool,
    plata_quieta_elegible: bool,
    hoy: date,
    convertido: bool,
) -> Consejo | None:
    """C3: Colchón de seguridad / fondo para imprevistos."""
    if gasto_tipico is None or gasto_tipico <= Decimal("0"):
        return None
    if tiene_meta_colchon or plata_quieta_elegible:
        return None
    if disponible >= gasto_tipico:
        return None

    dias = int(max(disponible, Decimal("0")) * 30 // gasto_tipico)
    medio = redondeo_arriba(gasto_tipico / Decimal("2"), moneda)

    nota = ""
    if convertido:
        nota = " (con tus dólares a dólar oficial)" if moneda == "ARS" else " (con tus pesos a dólar oficial)"

    if dias == 0:
        texto = f"Si un mes no entrara plata, hoy no tendrías con qué cubrir tus gastos{nota}. Un primer objetivo es medio mes: {m(medio, moneda)}."
    elif dias == 1:
        texto = f"Si un mes no entrara plata, lo que tenés alcanza para 1 día de tus gastos{nota}. Un primer objetivo es medio mes: {m(medio, moneda)}."
    else:
        texto = f"Si un mes no entrara plata, lo que tenés alcanza para unos {dias} días de tus gastos{nota}. Un primer objetivo es medio mes: {m(medio, moneda)}."

    clave = f"colchon/{hoy:%Y-%m}"
    accion = "crear_meta"
    accion_datos = {
        "nombre": "Imprevistos",
        "monto": str(int(medio)),
        "moneda": moneda,
    }
    valores = {
        "disponible": str(disponible.quantize(Decimal("0.01"))),
        "gasto_tipico": str(gasto_tipico.quantize(Decimal("0.01"))),
        "medio_mes": str(medio.quantize(Decimal("0.01"))),
    }

    return Consejo(
        tipo="colchon",
        clave=clave,
        moneda=moneda,
        texto=texto,
        accion=accion,
        accion_datos=accion_datos,
        valores=valores,
        ahorro=None,
    )


def evaluar_comisiones(
    filas_por_ciclo: list[list[FilaGasto]],
    hoy: date,
) -> Consejo | None:
    """C4: Comisiones bancarias evitables. Solo pesos."""
    n = len(filas_por_ciclo)
    if n == 0:
        return None

    tot = sum(
        f.monto for ciclo in filas_por_ciclo for f in ciclo
        if f.moneda == "ARS" and f.categoria == "Banco" and f.subcategoria == "Comisiones"
    )
    g = sum(
        f.monto for ciclo in filas_por_ciclo for f in ciclo
        if f.moneda == "ARS"
    )

    if tot <= Decimal("0") or tot * Decimal("100") < g:
        return None

    tr = redondeo(tot, "ARS")
    anual = redondeo(tot / Decimal(n) * Decimal("12"), "ARS")
    inicio = "En el último mes" if n == 1 else f"En los últimos {n} meses"

    texto = (
        f"{inicio} pagaste {m(tr, 'ARS')} en comisiones del banco: {m(anual, 'ARS')} al año. "
        f"Una caja de ahorro en pesos básica no tiene costo de mantenimiento: "
        f"preguntá en tu banco si te conviene pasarte."
    )

    trimestre = (hoy.month - 1) // 3 + 1
    clave = f"comisiones/ARS/{hoy.year}-T{trimestre}"
    ahorro = tot / Decimal(n) * Decimal("12")
    valores = {
        "total": str(tot.quantize(Decimal("0.01"))),
        "n": n,
    }

    return Consejo(
        tipo="comisiones",
        clave=clave,
        moneda="ARS",
        texto=texto,
        valores=valores,
        ahorro=ahorro,
    )


def evaluar_pico(
    filas_actual: list[FilaGasto],
    cerrados: list[list[FilaGasto]],
    gasto_tipico: Decimal | None,
    moneda: str,
    inicio_ciclo_actual: date,
) -> Consejo | None:
    """C5: Salto inusual de gasto en una categoría no esencial."""
    if len(cerrados) < 4 or gasto_tipico is None:
        return None

    califican: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for cat in sorted(CATEGORIAS_PICO):
        hist = [
            sum(
                (f.monto for f in c if f.moneda == moneda and f.categoria == cat and not fuera_de_consejos(f)),
                Decimal("0"),
            )
            for c in cerrados
        ]
        if sum(1 for x in hist if x > Decimal("0")) < 4:
            continue

        me = mediana(hist)
        d = mad(hist)
        if me is None or d is None:
            continue

        a = sum(
            (f.monto for f in filas_actual if f.moneda == moneda and f.categoria == cat and not fuera_de_consejos(f)),
            Decimal("0"),
        )
        if a > me + Decimal("3") * d and (a - me) * Decimal("100") >= gasto_tipico:
            califican.append((cat, a, me, a - me))

    if not califican:
        return None

    # Se queda con la de mayor (a - me); empate por orden alfabético ya garantizado
    califican.sort(key=lambda x: -x[3])
    cat_elegida, a_val, me_val, diff = califican[0]

    ar = redondeo(a_val, moneda)
    mr = redondeo(me_val, moneda)
    if ar == mr:
        return None

    texto = f"Este mes llevás {m(ar, moneda)} en {cat_elegida}; lo normal tuyo es {m(mr, moneda)}."
    clave = f"pico/{moneda}/{cat_elegida}/{inicio_ciclo_actual.isoformat()}"
    ahorro = diff
    valores = {
        "mediana": str(me_val.quantize(Decimal("0.01"))),
        "actual": str(a_val.quantize(Decimal("0.01"))),
        "categoria": cat_elegida,
    }

    return Consejo(
        tipo="pico",
        clave=clave,
        moneda=moneda,
        texto=texto,
        valores=valores,
        ahorro=ahorro,
    )


def evaluar_sin_rubro(
    filas_ciclo: list[FilaGasto],
    moneda: str,
    inicio_ciclo: date,
) -> Consejo | None:
    """C14: Demasiados movimientos sin rubro clasificado."""
    filas_m = [f for f in filas_ciclo if f.moneda == moneda]
    g = sum((f.monto for f in filas_m), Decimal("0"))

    sin_rubro = [
        f for f in filas_m
        if f.categoria is None or (f.categoria == "Otros" and f.subcategoria is None)
    ]
    s = sum((f.monto for f in sin_rubro), Decimal("0"))
    n = sum(1 for f in sin_rubro if es_compra(f))

    if g <= Decimal("0") or n < 5 or s * Decimal("100") < g * Decimal("15"):
        return None

    pct = int((s * Decimal("100") / g).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    texto = (
        f"El {pct}% de lo que gastaste el mes pasado quedó sin rubro. "
        f"Si lo clasificás, tus números y estos consejos te van a servir más."
    )
    accion = "ver_movimientos"
    clave = f"sin_rubro/{inicio_ciclo.isoformat()}"
    valores = {"pct": pct}

    return Consejo(
        tipo="sin_rubro",
        clave=clave,
        moneda=moneda,
        texto=texto,
        accion=accion,
        valores=valores,
        ahorro=None,
    )


def evaluar_sin_ingresos(
    egresos_45: int,
    ingresos_45: int,
    hoy: date,
) -> Consejo | None:
    """C15: Gastos cargados pero ningún ingreso."""
    if egresos_45 < 10 or ingresos_45 > 0:
        return None

    texto = (
        "Cargaste tus gastos pero ningún ingreso. Cuando cobres, registralo "
        "(también por WhatsApp): sin eso no puedo decirte cuánto te queda libre."
    )
    accion = "cargar_ingreso"
    clave = f"sin_ingresos/{hoy:%Y-%m}"
    valores = {"egresos_45": egresos_45}

    return Consejo(
        tipo="sin_ingresos",
        clave=clave,
        moneda=None,
        texto=texto,
        accion=accion,
        valores=valores,
        ahorro=None,
    )


def elegir_rubro_sin_presupuesto(
    filas: list[FilaGasto],
    cerrados3: list[tuple[date, date]],
    cubiertas: frozenset[str] | set[str],
    nombres: frozenset[str] | set[str],
    moneda: str = "ARS",
) -> RubroSinPresupuesto | None:
    """
    Elige el rubro candidato sin presupuesto analizando los 3 ciclos cerrados más nuevos.
    Pura: sin base, sin red.
    """
    if len(cerrados3) < 3:
        return None

    c3 = cerrados3[:3]

    # 2. Gasto típico: mediana de Σ monto de todas las filas de cada ciclo
    totales_ciclos = [
        sum((f.monto for f in filas if f.moneda == moneda and c[0] <= f.fecha <= c[1]), Decimal("0"))
        for c in c3
    ]
    gasto_tipico = mediana(totales_ciclos)

    # 3. Categorías candidatas en orden alfabético
    nombres_lower = {n.lower() for n in nombres}
    cats_candidatas = sorted(
        c for c in CATEGORIAS_SIN_PRESUPUESTO
        if c not in cubiertas and c.lower() not in nombres_lower
    )

    mejor_cat: str | None = None
    mejor_mediana: Decimal | None = None

    for cat in cats_candidatas:
        totales_k: list[Decimal] = []
        consumos_k: list[Decimal] = []
        califica = True

        for c in c3:
            filas_cat = [
                f for f in filas
                if f.moneda == moneda and f.categoria == cat and c[0] <= f.fecha <= c[1]
            ]
            # total_k: Σ monto de TODAS sus filas (también cuotas, espejos y subcategorías excluidas)
            total_k = sum((f.monto for f in filas_cat), Decimal("0"))
            # compras_k: cantidad de filas con es_compra y no fuera_de_consejos
            compras_k = sum(1 for f in filas_cat if es_compra(f) and not fuera_de_consejos(f))
            consumo_k = sum(
                ((-f.monto if f.es_espejo else f.monto) for f in filas_cat if not fuera_de_consejos(f)),
                Decimal("0"),
            )

            if compras_k < 1 or total_k <= Decimal("0"):
                califica = False
                break

            totales_k.append(total_k)
            consumos_k.append(consumo_k)

        if not califica:
            continue

        mt = mediana(totales_k)
        mc = mediana(consumos_k)
        if mc * Decimal("100") < gasto_tipico * Decimal("5"):
            continue
        if mc * Decimal("2") < mt:
            continue

        # Se queda la de mayor mediana; empate: la primera en orden alfabético
        if mejor_mediana is None or mt > mejor_mediana:
            mejor_cat = cat
            mejor_mediana = mt

    if mejor_cat is None or mejor_mediana is None:
        return None

    x = redondeo_arriba(mejor_mediana, moneda)
    if x == Decimal("0"):
        return None

    return RubroSinPresupuesto(mejor_cat, moneda, x, mejor_mediana)


def evaluar_ritmo(r: RitmoPresupuesto) -> Consejo:
    """C6: Proyección de ritmo de gasto que supera el límite del presupuesto."""
    m_proy = m(redondeo(r.proyeccion, r.moneda), r.moneda)
    m_lim = m(r.limite, r.moneda)
    texto = f"A este ritmo llegás a unos {m_proy} en {r.nombre} (límite {m_lim})."
    clave = f"ritmo/{r.presupuesto_id}/{r.inicio.isoformat()}"
    ahorro = r.proyeccion - r.limite
    valores = {
        "proyeccion": str(r.proyeccion.quantize(Decimal("0.01"))),
        "limite": str(r.limite.quantize(Decimal("0.01"))),
        "presupuesto_id": r.presupuesto_id,
        "inicio": r.inicio.isoformat(),
    }
    return Consejo(
        tipo="ritmo",
        clave=clave,
        moneda=r.moneda,
        texto=texto,
        accion="ver_presupuestos",
        valores=valores,
        ahorro=ahorro,
    )


def evaluar_sin_presupuesto(rubro: RubroSinPresupuesto, categoria_id: str, hoy: date) -> Consejo:
    """C7: Rubro frecuente sin presupuesto."""
    m_monto = m(rubro.monto, rubro.moneda)
    texto = f"{rubro.categoria} no tiene presupuesto. Gastás unos {m_monto} por mes."
    trimestre = (hoy.month - 1) // 3 + 1
    clave = f"sin_presupuesto/{rubro.moneda}/{rubro.categoria}/{hoy.year}-T{trimestre}"
    accion_datos = {
        "categoria_id": categoria_id,
        "nombre": rubro.categoria,
        "monto": str(int(rubro.monto)),
        "moneda": rubro.moneda,
    }
    valores = {
        "mediana": str(rubro.mediana.quantize(Decimal("0.01"))),
        "categoria": rubro.categoria,
        "categoria_id": categoria_id,
    }
    return Consejo(
        tipo="sin_presupuesto",
        clave=clave,
        moneda=rubro.moneda,
        texto=texto,
        accion="armar_presupuesto",
        accion_datos=accion_datos,
        valores=valores,
        ahorro=None,
    )


def a_precios_de_hoy(
    filas: list[FilaGasto],
    ipc: dict[str, Decimal],
    hoy: date,
) -> list[FilaGasto]:
    """
    Lleva las filas de gasto ARS a precios de 'hoy' usando el IPC acumulado.
    Las filas USD quedan sin cambios. Con IPC vacío, devuelve la lista sin modificar.
    """
    if not ipc:
        return list(filas)

    resultado: list[FilaGasto] = []
    for f in filas:
        if f.moneda == "ARS":
            nuevo_monto = deflactar_monto(f.monto, f.fecha, hoy, ipc, Moneda.ARS).monto
            resultado.append(
                FilaGasto(
                    fecha=f.fecha,
                    monto=nuevo_monto,
                    moneda=f.moneda,
                    categoria=f.categoria,
                    subcategoria=f.subcategoria,
                    es_espejo=f.es_espejo,
                    es_cuota=f.es_cuota,
                )
            )
        else:
            resultado.append(f)
    return resultado


# ==============================================================================
# B-F. EVALUAR TODOS
# ==============================================================================

def evaluar_todos(datos: DatosConsejos) -> list[Consejo]:
    """Evalúa todos los consejos candidatos a partir de los datos recolectados (pura)."""
    # 0. Sin hay_datos, con datos_viejos o con cobertura no None y < 50 -> []
    if not datos.hay_datos or datos.datos_viejos:
        return []
    if datos.cobertura is not None and datos.cobertura < 50:
        return []

    candidatos: list[Consejo] = []

    # Deflactar filas una sola vez para C2, C3 y C5
    filas_hoy = a_precios_de_hoy(datos.filas, datos.ipc, datos.hoy)

    def filas_de(inicio: date, fin: date) -> list[FilaGasto]:
        return [f for f in datos.filas if inicio <= f.fecha <= fin]

    def filas_de_hoy(inicio: date, fin: date) -> list[FilaGasto]:
        return [f for f in filas_hoy if inicio <= f.fecha <= fin]

    def gasto_k(moneda: str, inicio: date, fin: date) -> Decimal:
        return sum((f.monto for f in filas_de(inicio, fin) if f.moneda == moneda), Decimal("0"))

    def gasto_k_hoy(moneda: str, inicio: date, fin: date) -> Decimal:
        return sum((f.monto for f in filas_de_hoy(inicio, fin) if f.moneda == moneda), Decimal("0"))

    # 1. C1 para "ARS" y para "USD" si hay cerrados
    if datos.cerrados:
        c0 = datos.cerrados[0]
        filas_c0 = filas_de(c0[0], c0[1])
        c1_ars = evaluar_hormiga(filas_c0, tickets_90(datos.filas, "ARS", datos.hoy), "ARS", c0[0])
        if c1_ars:
            candidatos.append(c1_ars)
        c1_usd = evaluar_hormiga(filas_c0, tickets_90(datos.filas, "USD", datos.hoy), "USD", c0[0])
        if c1_usd:
            candidatos.append(c1_usd)

    # 2. C2 (Plata quieta)
    gasto_tipico_ars = (
        mediana([gasto_k_hoy("ARS", c[0], c[1]) for c in datos.cerrados])
        if datos.cerrados
        else None
    )
    if gasto_tipico_ars is not None:
        gasto_tipico_ars = gasto_tipico_ars * datos.factor_cobertura

    c2 = evaluar_plata_quieta(
        libre_ars=datos.libre_ars,
        gasto_tipico_ars=gasto_tipico_ars,
        tasa=datos.tasa_inflacion,
        mes=datos.mes_inflacion,
        fuente=datos.fuente_inflacion,
        billetera_inversion=datos.billetera_inversion,
        hoy=datos.hoy,
        inversiones_ars=datos.inversiones_ars,
    )
    if c2:
        candidatos.append(c2)

    # 3. C3 (Colchón) en la moneda principal
    c_compra = datos.cotizacion_compra
    principal = datos.moneda_principal or "ARS"

    convertido = False
    if principal == "ARS":
        if datos.usd_total != Decimal("0") or any(gasto_k_hoy("USD", c[0], c[1]) != Decimal("0") for c in datos.cerrados):
            convertido = True
    else:  # USD
        total_pesos = datos.saldo_hoy_ars + datos.inversiones_ars
        if total_pesos != Decimal("0") or any(gasto_k_hoy("ARS", c[0], c[1]) != Decimal("0") for c in datos.cerrados):
            convertido = True

    c3_habilitado = True
    if convertido and c_compra is None:
        c3_habilitado = False

    if c3_habilitado:
        if principal == "ARS":
            disp = datos.saldo_hoy_ars + datos.inversiones_ars + (datos.usd_total * c_compra if c_compra is not None else Decimal("0"))
            gastos_cerrados = [
                gasto_k_hoy("ARS", c[0], c[1]) + (gasto_k_hoy("USD", c[0], c[1]) * c_compra if c_compra is not None else Decimal("0"))
                for c in datos.cerrados
            ]
        else:  # USD
            disp = datos.usd_total + ((datos.saldo_hoy_ars + datos.inversiones_ars) / c_compra if c_compra is not None and c_compra > Decimal("0") else Decimal("0"))
            gastos_cerrados = [
                gasto_k_hoy("USD", c[0], c[1]) + (gasto_k_hoy("ARS", c[0], c[1]) / c_compra if c_compra is not None and c_compra > Decimal("0") else Decimal("0"))
                for c in datos.cerrados
            ]

        gt_colchon = (
            mediana(gastos_cerrados) * datos.factor_cobertura
            if gastos_cerrados
            else None
        )
        plata_quieta_elegible = (c2 is not None)
        c3 = evaluar_colchon(
            disponible=disp,
            gasto_tipico=gt_colchon,
            moneda=principal,
            tiene_meta_colchon=datos.tiene_meta_colchon,
            plata_quieta_elegible=plata_quieta_elegible,
            hoy=datos.hoy,
            convertido=convertido,
        )
        if c3:
            candidatos.append(c3)

    # 4. C4 (Comisiones)
    if datos.cerrados:
        filas_c4 = [filas_de(c[0], c[1]) for c in datos.cerrados[:3]]
        c4 = evaluar_comisiones(filas_c4, datos.hoy)
        if c4:
            candidatos.append(c4)

    # 5. C5 (Pico) para ARS y USD
    filas_actual = filas_de_hoy(datos.ciclo_actual[0], datos.hoy)
    filas_cerrados = [filas_de_hoy(c[0], c[1]) for c in datos.cerrados]
    gt_pico_ars = (
        mediana([gasto_k_hoy("ARS", c[0], c[1]) for c in datos.cerrados])
        if datos.cerrados
        else None
    )
    c5_ars = evaluar_pico(filas_actual, filas_cerrados, gt_pico_ars, "ARS", datos.ciclo_actual[0])
    if c5_ars:
        candidatos.append(c5_ars)

    gt_pico_usd = (
        mediana([gasto_k_hoy("USD", c[0], c[1]) for c in datos.cerrados])
        if datos.cerrados
        else None
    )
    c5_usd = evaluar_pico(filas_actual, filas_cerrados, gt_pico_usd, "USD", datos.ciclo_actual[0])
    if c5_usd:
        candidatos.append(c5_usd)

    # 6. C14 (Sin rubro)
    if datos.cerrados:
        c0 = datos.cerrados[0]
        filas_c0 = filas_de(c0[0], c0[1])
        c14_ars = evaluar_sin_rubro(filas_c0, "ARS", c0[0])
        if c14_ars:
            candidatos.append(c14_ars)
        else:
            c14_usd = evaluar_sin_rubro(filas_c0, "USD", c0[0])
            if c14_usd:
                candidatos.append(c14_usd)

    # 7. C15 (Sin ingresos)
    limite_45 = datos.hoy - timedelta(days=45)
    egresos_45 = sum(1 for f in datos.filas if es_compra(f) and limite_45 < f.fecha <= datos.hoy)
    c15 = evaluar_sin_ingresos(egresos_45, datos.ingresos_45, datos.hoy)
    if c15:
        candidatos.append(c15)

    # 8. C6 (Ritmo de presupuesto)
    for r in datos.ritmos:
        candidatos.append(evaluar_ritmo(r))

    # 9. C7 (Rubro sin presupuesto)
    if datos.cerrados:
        rubro_ars = elegir_rubro_sin_presupuesto(
            filas_hoy,
            datos.cerrados[:3],
            datos.categorias_con_presupuesto,
            datos.nombres_presupuestos,
            "ARS",
        )
        rubro_elegido = rubro_ars
        if rubro_elegido is None:
            rubro_usd = elegir_rubro_sin_presupuesto(
                filas_hoy,
                datos.cerrados[:3],
                datos.categorias_con_presupuesto,
                datos.nombres_presupuestos,
                "USD",
            )
            rubro_elegido = rubro_usd

        if rubro_elegido is not None and rubro_elegido.categoria in datos.ids_categorias:
            cat_id_egreso = datos.ids_categorias[rubro_elegido.categoria]
            candidatos.append(evaluar_sin_presupuesto(rubro_elegido, cat_id_egreso, datos.hoy))

    return candidatos


# ==============================================================================
# B-G. ELEGIR (Puras)
# ==============================================================================

def ahorro_en_pesos(c: Consejo, cotizacion_compra: Decimal | None) -> Decimal | None:
    """Calcula el valor anual estimado del ahorro en pesos argentinos."""
    if c.ahorro is None:
        return None
    if c.moneda != "USD":
        return c.ahorro
    if cotizacion_compra is None:
        return None
    return c.ahorro * cotizacion_compra


def grupo(c: Consejo, cot: Decimal | None) -> int:
    """Grupo de ordenamiento: 0 arrastre, 1 colchon, 2 ahorro no nulo, 3 resto."""
    if c.tipo == "arrastre":
        return 0
    if c.tipo == "colchon":
        return 1
    if ahorro_en_pesos(c, cot) is not None:
        return 2
    return 3


def ordenar(
    consejos: list[Consejo],
    prioridades: list[int],
    cotizacion_compra: Decimal | None,
) -> list[Consejo]:
    """Ordena los consejos candidatos según prioridades, grupo, ahorro y catálogo."""
    def clave_orden(c: Consejo):
        puntos = sum(PUNTOS_PRIORIDAD.get(p, {}).get(c.tipo, 0) for p in prioridades)
        grp = grupo(c, cotizacion_compra)
        ah_pesos = ahorro_en_pesos(c, cotizacion_compra)
        ah_val = ah_pesos if ah_pesos is not None else Decimal("0")
        num_cat = NUMERO_CATALOGO.get(c.tipo, 99)
        moneda_str = c.moneda or ""
        return (-puntos, grp, -ah_val, num_cat, moneda_str, c.clave)

    return sorted(consejos, key=clave_orden)


def elegir(
    consejos: list[Consejo],
    historial: list[FilaHistorial],
    silenciados: set[str],
    prioridades: list[int],
    hoy: date,
    cotizacion_compra: Decimal | None,
) -> Consejo | None:
    """Elige el único consejo a emitir para el usuario si corresponde."""
    # 1. Hay uno activo (respuesta None y fecha_vence > hoy) -> None
    for h in historial:
        if h.respuesta is None and h.fecha_vence > hoy:
            return None

    # 2. Hay uno con fecha_emision == hoy -> None
    for h in historial:
        if h.fecha_emision == hoy:
            return None

    # 3. Hay 2 o más con fecha_emision > hoy - 7 días -> None
    limite_semana = hoy - timedelta(days=7)
    emisiones_semana = sum(1 for h in historial if h.fecha_emision > limite_semana)
    if emisiones_semana >= 2:
        return None

    # 4. Descartar de candidatos
    candidatos_validos: list[Consejo] = []
    for c in consejos:
        if c.tipo in silenciados:
            continue
        # (tipo, clave) ya emitido alguna vez
        if any(h.tipo == c.tipo and h.clave == c.clave for h in historial):
            continue
        # tipo con una emisión hace menos de ESPERA_DIAS[tipo] días
        espera = ESPERA_DIAS.get(c.tipo, 30)
        ultimas_del_tipo = [h.fecha_emision for h in historial if h.tipo == c.tipo]
        if ultimas_del_tipo:
            ultima_emision = max(ultimas_del_tipo)
            if (hoy - ultima_emision).days < espera:
                continue
        candidatos_validos.append(c)

    if not candidatos_validos:
        return None

    # 5. Devuelve el primero de ordenar
    ordenados = ordenar(candidatos_validos, prioridades, cotizacion_compra)
    return ordenados[0] if ordenados else None


def comparar_medicion(tipo: str, antes: Decimal, despues: Decimal) -> str:
    """Evalúa si el consejo mejoró la situación o si quedó igual."""
    if tipo in ("hormiga", "comisiones", "sin_rubro"):
        return "mejoro" if despues < antes else "igual"
    if tipo in ("pico", "ritmo"):
        return "mejoro" if despues <= antes else "igual"
    if tipo in ("plata_quieta", "colchon", "sin_ingresos", "sin_presupuesto"):
        return "mejoro" if despues > antes else "igual"
    return "igual"

