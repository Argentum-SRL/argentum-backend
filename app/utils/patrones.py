"""
Detector puro de gastos fijos para el motor financiero de Argentum.
Ubicación: app/utils/patrones.py

Este módulo opera en memoria sin depender de la base de datos ni de sesiones SQLAlchemy.
Detecta recurrencias de egresos fijos (mensuales, bimestrales y anuales), calculando
frecuencia, fuerza, presencia, dispersión de montos, día típico y fecha próxima estimada.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
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

# Cortes de presencia mínima y dispersión máxima de monto: finanzas.py (líneas 522-523); mediana y MAD por robustez (Leys y otros 2013).
PRESENCIA_MIN_FUERTE = Decimal("0.80")
DISPERSION_MAX_MONTO = Decimal("0.10")


@dataclass(frozen=True)
class PatronFijo:
    """Representa un patrón de gasto fijo recurrente detectado en la historia del usuario."""

    clave: str
    descripcion: str
    categoria_id: str | None
    subcategoria_id: str | None
    billetera_id: str
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

        # c. Si dos movimientos consecutivos están a menos de 21 días, partir el grupo por monto
        hay_cercanos = any(
            (txs_ordenados[i].fecha - txs_ordenados[i - 1].fecha).days < 21
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

            # Si una parte todavía tiene dos movimientos a menos de 21 días: Descartado "varias_por_periodo"
            if any(
                (parte_ordenada[i].fecha - parte_ordenada[i - 1].fecha).days < 21
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
                if not any(k * v_min <= d <= k * v_max for k in range(1, k_max + 1)):
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
            if frecuencia == "mensual":
                proxima_fecha = ultima_fecha + relativedelta(months=1)
            elif frecuencia == "bimestral":
                proxima_fecha = ultima_fecha + relativedelta(months=2)
            elif frecuencia == "anual":
                proxima_fecha = ultima_fecha + relativedelta(years=1)
            else:
                proxima_fecha = ultima_fecha + relativedelta(months=1)

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
            billetera_id = str(getattr(parte_ordenada[-1], "billetera_id", "billetera_principal"))
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
