"""
Detector puro de gastos fijos y clasificador de costumbre y día a día para el motor de Argentum.
Ubicación: app/utils/patrones.py

Este módulo opera en memoria sin depender de la base de datos ni de sesiones SQLAlchemy.
Detecta recurrencias de egresos fijos (mensuales, bimestrales y anuales), calculando
frecuencia, fuerza, presencia, dispersión de montos, día típico y fecha próxima estimada.
Además clasifica los gastos frecuentes restantes en las cajas de 'costumbre' y 'dia_a_dia'.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from typing import Any, Iterable

from dateutil.relativedelta import relativedelta

from app.services.definiciones_service import es_gasto
from app.services.memoria_comercio_service import clave_comercio
from app.utils.finanzas import (
    _distancia_circular_dias,
    _indice_por_mes,
    _normalizar_descripcion,
    deflactar_monto,
    mad,
    mediana,
)
from app.utils.texto import normalizar_texto

# Ventanas de días entre pagos observados: las mismas de finanzas._inferir_frecuencia_y_estado.
VENTANAS_DIAS = {
    "mensual": (21, 38),
    "bimestral": (53, 67),
    "anual": (350, 380),
}

# Días teóricos por período para cálculo de ciclos esperados y vencimiento (365.25 / 12, etc.).
PERIODO_DIAS = {
    "mensual": Decimal("30.44"),
    "bimestral": Decimal("60.88"),
    "anual": Decimal("365.25"),
}

# Tolerancia de días permitida para pagos retrasados: las de finanzas._inferir_frecuencia_y_estado.
TOLERANCIA_DIAS = {
    "mensual": 7,
    "bimestral": 7,
    "anual": 15,
}

# Máximo múltiplo de intervalo permitido entre dos pagos consecutivos (hasta dos períodos sin cargar; diario de gastos BLS, persona P09).
MULTIPLO_MAX = {
    "mensual": 3,
    "bimestral": 2,
    "anual": 1,
}

# Cantidad mínima de ocurrencias para clasificar un patrón como fuerte: criterio MATURE de Plaid.
MIN_OCURRENCIAS_FUERTE = {
    "mensual": 3,
    "bimestral": 3,
    "anual": 2,
}

# Distancia mínima entre dos pagos para no considerarlos múltiples dentro de un mismo período.
# Alquiler y expensas se pagan entre el 1 y el 10: del 10 de febrero al 1 de marzo hay 19 días.
DISTANCIA_MIN_DIAS = 15

# Piso de días para un período único (k = 1): medio período.
PISO_UN_PERIODO = {
    "mensual": 15,
    "bimestral": 30,
    "anual": 180,
}

# Cortes de presencia mínima y dispersión máxima de monto: finanzas.py (líneas 522-523); mediana y MAD por robustez (Leys y otros 2013).
PRESENCIA_MIN_FUERTE = Decimal("0.80")
DISPERSION_MAX_MONTO = Decimal("0.10")

# Constantes para clasificador de frecuentes
MIN_OCURRENCIAS_FRECUENTE = 3  # Criterio MATURE de Plaid, el mismo que en los fijos
MESES_VENTANA_FRECUENTE = 3  # Encuesta de gastos por entrevista del BLS releva los últimos 3 meses


@dataclass(frozen=True)
class PatronFijo:
    """Representa un patrón de gasto fijo recurrente detectado en la historia del usuario."""

    clave: str
    descripcion: str
    categoria_id: str | None
    subcategoria_id: str | None
    billetera_id: Any | None
    moneda: Any
    frecuencia: str
    fuerza: str
    ocurrencias: int
    presencia: Decimal
    dispersion_monto: Decimal
    monto_mediano_deflactado: Decimal
    ultimo_monto: Decimal
    dia_tipico: int
    ultima_fecha: date
    proxima_fecha: date
    transacciones_ids: tuple[Any, ...]


@dataclass(frozen=True)
class Descartado:
    """Registro de una agrupación o serie descartada con su motivo técnico."""

    clave: str
    moneda: Any
    ocurrencias: int
    motivo: str
    dispersion_monto: Decimal | None = None


@dataclass(frozen=True)
class ResultadoFijos:
    """Resultado global que encapsula patrones detectados y agrupaciones descartadas."""

    fijos: list[PatronFijo]
    descartados: list[Descartado]


def clave_patron(tx: Any) -> str:
    """
    Calcula la clave de agrupación para un movimiento.
    Usa clave_comercio normalizada; si resulta vacía, recurre a subcategoría y categoría.
    """
    desc = getattr(tx, "descripcion", None)
    c = None
    if desc:
        c = clave_comercio(_normalizar_descripcion(desc))
    if not c:
        scid = getattr(tx, "subcategoria_id", None)
        if scid:
            return f"sub:{scid}"
        cid = getattr(tx, "categoria_id", None)
        return f"cat:{cid}"
    return c


def movimientos_elegibles(transacciones: Iterable[Any], ctx: Any) -> list[Any]:
    """
    Filtra los movimientos que cumplen condición de gasto, no son cuotas hijas
    y no pertenecen a suscripciones activas declaradas.
    """
    elegibles = []
    for tx in transacciones:
        if not es_gasto(tx, ctx):
            continue
        if getattr(tx, "es_cuota_hija", False):
            continue
        if getattr(tx, "suscripcion_id", None) is not None:
            continue
        elegibles.append(tx)
    return elegibles


def _calcular_proxima_fecha(ultima_fecha: date, dia_tipico: int, frecuencia: str) -> date:
    """
    Calcula la próxima fecha estimada respetando día típico observado y fin de mes.
    Mensual y bimestral suman 1 o 2 meses ajustando el día a dia_tipico (o último día del mes).
    Anual suma 1 año sin alterar el día original.
    """
    if frecuencia in ("mensual", "bimestral"):
        meses_adelante = 1 if frecuencia == "mensual" else 2
        primer_dia_base = ultima_fecha.replace(day=1)
        mes_destino = primer_dia_base + relativedelta(months=meses_adelante)
        ultimo_dia_mes = calendar.monthrange(mes_destino.year, mes_destino.month)[1]
        dia_final = min(dia_tipico, ultimo_dia_mes)
        return date(mes_destino.year, mes_destino.month, dia_final)
    elif frecuencia == "anual":
        return ultima_fecha + relativedelta(years=1)
    else:
        return ultima_fecha + relativedelta(months=1)


def detectar_fijos(
    transacciones: Iterable[Any],
    ipc_records: Any,
    fecha_destino: date,
    *,
    ctx: Any,
    dispersion_max: Decimal = DISPERSION_MAX_MONTO,
    presencia_min: Decimal = PRESENCIA_MIN_FUERTE,
) -> ResultadoFijos:
    """
    Detecta patrones de gastos fijos recurrentes agrupando por (clave_patron, moneda).
    Aplica partición por monto ante cobros cercanos, verificación de cadencia,
    análisis de dispersión de monto, verificación de vigencia y asignación de fuerza.
    """
    elegibles = movimientos_elegibles(transacciones, ctx)
    ipc_map = ipc_records if isinstance(ipc_records, dict) else _indice_por_mes(ipc_records)

    grupos: dict[tuple[str, Any], list[Any]] = defaultdict(list)
    for tx in elegibles:
        c = clave_patron(tx)
        m = getattr(tx, "moneda", None)
        grupos[(c, m)].append(tx)

    fijos: list[PatronFijo] = []
    descartados: list[Descartado] = []

    for (clave, moneda), txs_grupo in grupos.items():
        # Ordenar movimientos del grupo por fecha (e id como desempate)
        txs_ordenados = sorted(
            txs_grupo,
            key=lambda x: (x.fecha, str(getattr(x, "id", ""))),
        )

        # a. Montos deflactados
        deflactados: dict[Any, Decimal] = {}
        for tx in txs_ordenados:
            tx_id = getattr(tx, "id", None) or id(tx)
            deflactados[tx_id] = deflactar_monto(
                tx.monto,
                tx.fecha,
                fecha_destino,
                ipc_map,
                tx.moneda,
            ).monto

        def _obtener_monto(tx_elem: Any) -> Decimal:
            tid = getattr(tx_elem, "id", None) or id(tx_elem)
            return deflactados[tid]

        def _calcular_dispersion_grupo(lista_txs: list[Any]) -> Decimal | None:
            if len(lista_txs) < 2:
                return None
            montos = [_obtener_monto(t) for t in lista_txs]
            med = mediana(montos)
            if med is None or med == Decimal("0"):
                return Decimal("1.0000")
            m_val = mad(montos)
            if m_val is None:
                return Decimal("0.0000")
            return (m_val / med).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        # b. Con 1 movimiento: Descartado "una_ocurrencia"
        if len(txs_ordenados) == 1:
            descartados.append(
                Descartado(
                    clave=clave,
                    moneda=moneda,
                    ocurrencias=1,
                    motivo="una_ocurrencia",
                    dispersion_monto=None,
                )
            )
            continue

        # c. Si dos movimientos consecutivos están a menos de DISTANCIA_MIN_DIAS días, partir el grupo por monto
        hay_cercanos = any(
            (txs_ordenados[i].fecha - txs_ordenados[i - 1].fecha).days < DISTANCIA_MIN_DIAS
            for i in range(1, len(txs_ordenados))
        )

        if hay_cercanos:
            ordenados_monto = sorted(txs_ordenados, key=_obtener_monto)
            partes: list[list[Any]] = []
            actual: list[Any] = [ordenados_monto[0]]
            for tx in ordenados_monto[1:]:
                m_ant = _obtener_monto(actual[-1])
                m_act = _obtener_monto(tx)
                corte = False
                if m_ant > Decimal("0"):
                    if (m_act - m_ant) / m_ant > Decimal("2") * dispersion_max:
                        corte = True
                elif m_act > m_ant:
                    corte = True

                if corte:
                    partes.append(actual)
                    actual = [tx]
                else:
                    actual.append(tx)
            partes.append(actual)
        else:
            partes = [txs_ordenados]

        # Procesar cada parte resultante
        for parte in partes:
            parte_ordenada = sorted(
                parte,
                key=lambda x: (x.fecha, str(getattr(x, "id", ""))),
            )

            # Volver a b como grupo propio si tiene 1 movimiento
            if len(parte_ordenada) == 1:
                descartados.append(
                    Descartado(
                        clave=clave,
                        moneda=moneda,
                        ocurrencias=1,
                        motivo="una_ocurrencia",
                        dispersion_monto=None,
                    )
                )
                continue

            # Si una parte todavía tiene dos movimientos a menos de DISTANCIA_MIN_DIAS días: Descartado "varias_por_periodo"
            if any(
                (parte_ordenada[i].fecha - parte_ordenada[i - 1].fecha).days < DISTANCIA_MIN_DIAS
                for i in range(1, len(parte_ordenada))
            ):
                disp_val = _calcular_dispersion_grupo(parte_ordenada)
                descartados.append(
                    Descartado(
                        clave=clave,
                        moneda=moneda,
                        ocurrencias=len(parte_ordenada),
                        motivo="varias_por_periodo",
                        dispersion_monto=disp_val,
                    )
                )
                continue

            # d. Cadencia y frecuencia
            deltas = [
                (parte_ordenada[i].fecha - parte_ordenada[i - 1].fecha).days
                for i in range(1, len(parte_ordenada))
            ]
            med_deltas = mediana([Decimal(d) for d in deltas])
            frecuencia = None
            for frec_cand, (v_min, v_max) in VENTANAS_DIAS.items():
                if Decimal(v_min) <= med_deltas <= Decimal(v_max):
                    frecuencia = frec_cand
                    break

            if frecuencia is None:
                descartados.append(
                    Descartado(
                        clave=clave,
                        moneda=moneda,
                        ocurrencias=len(parte_ordenada),
                        motivo="cadencia_irregular",
                        dispersion_monto=_calcular_dispersion_grupo(parte_ordenada),
                    )
                )
                continue

            v_min, v_max = VENTANAS_DIAS[frecuencia]
            k_max = MULTIPLO_MAX[frecuencia]
            cadencia_valida = True
            for d in deltas:
                if not any(
                    (PISO_UN_PERIODO[frecuencia] if k == 1 else k * v_min) <= d <= k * v_max
                    for k in range(1, k_max + 1)
                ):
                    cadencia_valida = False
                    break

            if not cadencia_valida:
                descartados.append(
                    Descartado(
                        clave=clave,
                        moneda=moneda,
                        ocurrencias=len(parte_ordenada),
                        motivo="cadencia_irregular",
                        dispersion_monto=_calcular_dispersion_grupo(parte_ordenada),
                    )
                )
                continue

            # e. Dispersión de monto
            montos_parte = [_obtener_monto(tx) for tx in parte_ordenada]
            med_monto = mediana(montos_parte)
            if med_monto is None or med_monto == Decimal("0"):
                dispersion = Decimal("1.0000")
            else:
                mad_monto = mad(montos_parte)
                if mad_monto is None:
                    dispersion = Decimal("0.0000")
                else:
                    dispersion = (mad_monto / med_monto).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )

            if dispersion > dispersion_max:
                descartados.append(
                    Descartado(
                        clave=clave,
                        moneda=moneda,
                        ocurrencias=len(parte_ordenada),
                        motivo="monto_distinto",
                        dispersion_monto=dispersion,
                    )
                )
                continue

            # f. Detección de finalizado
            ultima_fecha = parte_ordenada[-1].fecha
            dias_desde_ultima = Decimal((fecha_destino - ultima_fecha).days)
            limite_terminado = (
                Decimal("2") * PERIODO_DIAS[frecuencia]
                + Decimal(TOLERANCIA_DIAS[frecuencia])
            )
            if dias_desde_ultima > limite_terminado:
                descartados.append(
                    Descartado(
                        clave=clave,
                        moneda=moneda,
                        ocurrencias=len(parte_ordenada),
                        motivo="terminado",
                        dispersion_monto=dispersion,
                    )
                )
                continue

            # g. Cálculo de presencia
            primera_fecha = parte_ordenada[0].fecha
            dias_span = Decimal((ultima_fecha - primera_fecha).days)
            esperadas = (
                int(
                    (dias_span / PERIODO_DIAS[frecuencia]).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                )
                + 1
            )
            ocurrencias = len(parte_ordenada)
            presencia_calc = Decimal(ocurrencias) / Decimal(esperadas)
            presencia = min(Decimal("1.00"), presencia_calc).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            # h. Determinación de fuerza
            if (
                ocurrencias >= MIN_OCURRENCIAS_FUERTE[frecuencia]
                and presencia >= presencia_min
            ):
                fuerza = "fuerte"
            else:
                fuerza = "debil"

            # i. Día típico observado
            dias_observados = sorted(set(tx.fecha.day for tx in parte_ordenada))
            mejor_dia = dias_observados[0]
            menor_suma = None
            for d_cand in dias_observados:
                suma_dist = sum(
                    _distancia_circular_dias(d_cand, tx.fecha.day)
                    for tx in parte_ordenada
                )
                if (
                    menor_suma is None
                    or suma_dist < menor_suma
                    or (suma_dist == menor_suma and d_cand < mejor_dia)
                ):
                    menor_suma = suma_dist
                    mejor_dia = d_cand
            dia_tipico = mejor_dia

            # j. Próxima fecha estimada
            proxima_fecha = _calcular_proxima_fecha(ultima_fecha, dia_tipico, frecuencia)

            # Construir PatronFijo
            desc_final = next(
                (
                    tx.descripcion
                    for tx in reversed(parte_ordenada)
                    if getattr(tx, "descripcion", None)
                ),
                "",
            )
            cat_id = getattr(parte_ordenada[-1], "categoria_id", None)
            subcat_id = getattr(parte_ordenada[-1], "subcategoria_id", None)
            billetera_id = getattr(parte_ordenada[-1], "billetera_id", None)
            tx_ids = tuple(
                getattr(tx, "id", None) or id(tx) for tx in parte_ordenada
            )

            patron = PatronFijo(
                clave=clave,
                descripcion=desc_final,
                categoria_id=cat_id,
                subcategoria_id=subcat_id,
                billetera_id=billetera_id,
                moneda=moneda,
                frecuencia=frecuencia,
                fuerza=fuerza,
                ocurrencias=ocurrencias,
                presencia=presencia,
                dispersion_monto=dispersion,
                monto_mediano_deflactado=med_monto,
                ultimo_monto=parte_ordenada[-1].monto,
                dia_tipico=dia_tipico,
                ultima_fecha=ultima_fecha,
                proxima_fecha=proxima_fecha,
                transacciones_ids=tx_ids,
            )
            fijos.append(patron)

    # Ordenar fijos de mayor a menor monto mediano deflactado
    fijos.sort(key=lambda p: p.monto_mediano_deflactado, reverse=True)

    return ResultadoFijos(fijos=fijos, descartados=descartados)


# =============================================================================
# C. CLASIFICADOR DE COSTUMBRE Y DÍA A DÍA
# =============================================================================

def _extraer_nombre(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    return getattr(obj, "nombre", None)


def _obtener_nombres_cat_subcat(tx: Any) -> tuple[str | None, str | None]:
    cat_obj = getattr(tx, "categoria", None)
    sub_obj = getattr(tx, "subcategoria", None)
    cat_nom = _extraer_nombre(cat_obj)
    sub_nom = _extraer_nombre(sub_obj)
    if not cat_nom:
        cat_nom = getattr(tx, "categoria_nombre", None)
    if not sub_nom:
        sub_nom = getattr(tx, "subcategoria_nombre", None)
    return cat_nom, sub_nom


PALABRAS_COSTUMBRE = [
    "delivery", "cafe", "cafeteria", "restaurante", "restaurantes", "restaurant",
    "bar", "bares", "salida", "salidas", "taxi", "apps", "cuidado personal",
    "peluqueria", "hobby", "hobbies", "deportes", "gimnasio", "entretenimiento",
    "recreativo", "recreacion", "ocio", "gastronomia",
]

PALABRAS_DIA_A_DIA = [
    "supermercado", "almacen", "verduleria", "carniceria", "kiosco",
    "farmacia", "transporte publico", "combustible", "nafta", "peaje",
    "peajes", "indumentaria", "ropa", "alimentacion",
]

_FRASES_COSTUMBRE = [
    re.findall(r"[a-z]+", normalizar_texto(p)) for p in PALABRAS_COSTUMBRE
]
_FRASES_DIA_A_DIA = [
    re.findall(r"[a-z]+", normalizar_texto(p)) for p in PALABRAS_DIA_A_DIA
]


def _contiene_frase(palabras: list[str], frase: list[str]) -> bool:
    if not frase or not palabras or len(frase) > len(palabras):
        return False
    n = len(frase)
    for i in range(len(palabras) - n + 1):
        if palabras[i : i + n] == frase:
            return True
    return False


def _matchea_lista(palabras: list[str], frases: list[list[str]]) -> bool:
    return any(_contiene_frase(palabras, f) for f in frases)


# Comentario: las listas salen de la decisión de Sebastián del 08/10.
# Deportes y gimnasio entran como hobbies. Lo que no está en ninguna lista
# va a día a día, para no sugerir recortar algo que el usuario no marcó como gusto.
def rubro_de(tx: Any) -> str:
    """
    Clasifica un movimiento en 'costumbre' o 'dia_a_dia' a partir de los nombres
    de su subcategoría y categoría según la decisión del 08/10.
    """
    cat_nom, subcat_nom = _obtener_nombres_cat_subcat(tx)

    # 1. Subcategoría: primero costumbre, después día a día
    if subcat_nom:
        palabras_sub = re.findall(r"[a-z]+", normalizar_texto(subcat_nom))
        if palabras_sub:
            if _matchea_lista(palabras_sub, _FRASES_COSTUMBRE):
                return "costumbre"
            if _matchea_lista(palabras_sub, _FRASES_DIA_A_DIA):
                return "dia_a_dia"

    # 2. Categoría: primero costumbre, después día a día
    if cat_nom:
        palabras_cat = re.findall(r"[a-z]+", normalizar_texto(cat_nom))
        if palabras_cat:
            if _matchea_lista(palabras_cat, _FRASES_COSTUMBRE):
                return "costumbre"
            if _matchea_lista(palabras_cat, _FRASES_DIA_A_DIA):
                return "dia_a_dia"

    # 3. Fallback: dia_a_dia
    return "dia_a_dia"


@dataclass(frozen=True)
class GrupoFrecuente:
    """Representa una agrupación de gastos frecuentes (costumbre o día a día)."""

    caja: str
    clave: str
    nombre: str
    moneda: Any
    ocurrencias: int
    meses_con_movimiento: int
    monto_mensual_mediano: Decimal
    transacciones_ids: tuple[Any, ...]


@dataclass(frozen=True)
class ResultadoCajas:
    """Resultado global que distribuye egresos entre fijos, descartados_fijos, costumbre y día a día."""

    fijos: list[PatronFijo]
    descartados_fijos: list[Descartado]
    costumbre: list[GrupoFrecuente]
    dia_a_dia: list[GrupoFrecuente]


def clasificar_cajas(
    transacciones: Iterable[Any],
    ipc_records: Any,
    fecha_destino: date,
    *,
    ctx: Any,
    min_ocurrencias: int = MIN_OCURRENCIAS_FRECUENTE,
    meses_ventana: int = MESES_VENTANA_FRECUENTE,
) -> ResultadoCajas:
    """
    Clasifica egresos recurrentes entre fijos (detectar_fijos) y, para los restantes
    que se repiten, los reparte en las cajas de 'costumbre' y 'dia_a_dia'.
    """
    # a. Correr detectar_fijos. Sacar de movimientos_elegibles todos los que quedaron en algún fijo.
    res_fijos = detectar_fijos(transacciones, ipc_records, fecha_destino, ctx=ctx)
    ids_fijos: set[Any] = set()
    for f in res_fijos.fijos:
        ids_fijos.update(f.transacciones_ids)

    elegibles = movimientos_elegibles(transacciones, ctx)
    restantes = [
        tx for tx in elegibles
        if (getattr(tx, "id", None) or id(tx)) not in ids_fijos
    ]

    # b. Ventana: los meses_ventana meses calendario completos anteriores al mes de fecha_destino.
    # Ejemplo: fecha_destino 2026-09-05 y meses_ventana 3 -> junio, julio y agosto 2026.
    meses_ventana_lista: list[tuple[int, int]] = []
    primer_dia_mes_destino = fecha_destino.replace(day=1)
    for i in range(meses_ventana, 0, -1):
        m_dt = primer_dia_mes_destino - relativedelta(months=i)
        meses_ventana_lista.append((m_dt.year, m_dt.month))

    if meses_ventana_lista:
        primer_y, primer_m = meses_ventana_lista[0]
        ultimo_y, ultimo_m = meses_ventana_lista[-1]
        fecha_inicio_ventana = date(primer_y, primer_m, 1)
        ultimo_dia = calendar.monthrange(ultimo_y, ultimo_m)[1]
        fecha_fin_ventana = date(ultimo_y, ultimo_m, ultimo_dia)
    else:
        fecha_inicio_ventana = date.min
        fecha_fin_ventana = date.min

    movimientos_en_ventana = [
        tx for tx in restantes
        if fecha_inicio_ventana <= tx.fecha <= fecha_fin_ventana
    ]

    # c. Agrupar por (clave, moneda).
    # Clave: "sub:<subcategoria_id>". Si no hay id pero sí nombre, "sub:<nombre normalizado>".
    # Sin subcategoría, lo mismo con "cat:". Sin ninguna de las dos, "sin_rubro".
    grupos: dict[tuple[str, Any], list[Any]] = defaultdict(list)
    for tx in movimientos_en_ventana:
        sc_id = getattr(tx, "subcategoria_id", None) or getattr(getattr(tx, "subcategoria", None), "id", None)
        c_id = getattr(tx, "categoria_id", None) or getattr(getattr(tx, "categoria", None), "id", None)
        cat_nom, subcat_nom = _obtener_nombres_cat_subcat(tx)

        if sc_id:
            clave = f"sub:{sc_id}"
        elif subcat_nom:
            clave = f"sub:{normalizar_texto(subcat_nom)}"
        elif c_id:
            clave = f"cat:{c_id}"
        elif cat_nom:
            clave = f"cat:{normalizar_texto(cat_nom)}"
        else:
            clave = "sin_rubro"

        moneda = getattr(tx, "moneda", None)
        grupos[(clave, moneda)].append(tx)

    costumbre: list[GrupoFrecuente] = []
    dia_a_dia: list[GrupoFrecuente] = []

    ipc_map = ipc_records if isinstance(ipc_records, dict) else _indice_por_mes(ipc_records)
    meses_minimos = math.ceil(meses_ventana / 2)

    for (clave, moneda), txs_grupo in grupos.items():
        # d. Un grupo se repite si cumple ambas condiciones:
        # 1. Tiene al menos min_ocurrencias movimientos.
        # 2. Tiene movimientos en al menos la mitad de los meses de la ventana (redondeo hacia arriba).
        if len(txs_grupo) < min_ocurrencias:
            continue

        meses_presentes = set((tx.fecha.year, tx.fecha.month) for tx in txs_grupo)
        meses_con_movimiento = len(meses_presentes)
        if meses_con_movimiento < meses_minimos:
            continue

        # Ordenar movimientos del grupo por fecha (e id como desempate)
        txs_ordenados = sorted(
            txs_grupo,
            key=lambda x: (x.fecha, str(getattr(x, "id", ""))),
        )

        # Nombre: el de la subcategoría, si no el de la categoría, si no "Sin rubro".
        nombre_grupo = "Sin rubro"
        for tx in reversed(txs_ordenados):
            cat_n, sub_n = _obtener_nombres_cat_subcat(tx)
            if sub_n:
                nombre_grupo = str(sub_n)
                break
            elif cat_n and nombre_grupo == "Sin rubro":
                nombre_grupo = str(cat_n)

        # e. Caja: rubro_de del movimiento más reciente del grupo.
        caja = rubro_de(txs_ordenados[-1])

        # f. monto_mensual_mediano: mediana de la suma deflactada a fecha_destino de cada mes
        # de la ventana. Los meses sin movimientos suman 0.
        sumas_mensuales: list[Decimal] = []
        for y, m in meses_ventana_lista:
            txs_del_mes = [tx for tx in txs_ordenados if tx.fecha.year == y and tx.fecha.month == m]
            if txs_del_mes:
                suma_mes = sum(
                    deflactar_monto(
                        tx.monto,
                        tx.fecha,
                        fecha_destino,
                        ipc_map,
                        tx.moneda,
                    ).monto
                    for tx in txs_del_mes
                )
            else:
                suma_mes = Decimal("0")
            sumas_mensuales.append(suma_mes)

        med_monto = mediana(sumas_mensuales)
        if med_monto is None:
            med_monto = Decimal("0.00")
        else:
            med_monto = med_monto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        tx_ids = tuple(getattr(tx, "id", None) or id(tx) for tx in txs_ordenados)

        grupo_frecuente = GrupoFrecuente(
            caja=caja,
            clave=clave,
            nombre=nombre_grupo,
            moneda=moneda,
            ocurrencias=len(txs_grupo),
            meses_con_movimiento=meses_con_movimiento,
            monto_mensual_mediano=med_monto,
            transacciones_ids=tx_ids,
        )

        if caja == "costumbre":
            costumbre.append(grupo_frecuente)
        else:
            dia_a_dia.append(grupo_frecuente)

    # h. Cada caja ordenada de mayor a menor monto_mensual_mediano.
    costumbre.sort(key=lambda g: g.monto_mensual_mediano, reverse=True)
    dia_a_dia.sort(key=lambda g: g.monto_mensual_mediano, reverse=True)

    return ResultadoCajas(
        fijos=res_fijos.fijos,
        descartados_fijos=res_fijos.descartados,
        costumbre=costumbre,
        dia_a_dia=dia_a_dia,
    )
