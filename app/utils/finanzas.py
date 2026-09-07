from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import re
import unicodedata
from typing import Any, Iterable

from app.models.transaccion import EstadoVerificacionTransaccion, MetodoPago, TipoTransaccion
from app.models.usuario import Moneda


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class Deflactacion:
    monto: Decimal
    aplicada: bool
    motivo: str | None = None
    factor: Decimal = ONE


@dataclass(frozen=True)
class ClasificacionGasto:
    comprometidos: tuple[Any, ...]
    recurrentes_detectados: tuple[Any, ...]
    variables: tuple[Any, ...]
    categorias_recurrentes: frozenset[Any]


@dataclass(frozen=True)
class GastoCiclo:
    filas: tuple[Any, ...]
    nominal: Decimal
    deflactado: Decimal
    factor_medio: Decimal


def _firma_gasto(tx: Any) -> tuple[Any, Any, str]:
    texto = unicodedata.normalize("NFKD", getattr(tx, "descripcion", "") or "")
    texto = "".join(char for char in texto if not unicodedata.combining(char)).casefold()
    texto = re.sub(r"\d+", "", texto)
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    return tx.categoria_id, tx.moneda, texto


def _mes(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    return str(value)[:7]


def _indice_por_mes(ipc_records: Iterable[Any]) -> dict[str, Decimal]:
    return {
        record.fecha_dato: Decimal(str(record.indice_acumulado))
        for record in ipc_records
        if record.indice_acumulado is not None and Decimal(str(record.indice_acumulado)) > ZERO
    }


def deflactar_monto(
    monto: Decimal,
    fecha_origen: date | datetime | str,
    fecha_destino: date | datetime | str,
    ipc_records: Iterable[Any],
    moneda: Moneda = Moneda.ARS,
) -> Deflactacion:
    """Lleva ARS a pesos de la fecha destino usando el indice acumulado del IPC.

    No interpola meses faltantes. Para un destino posterior al ultimo dato disponible,
    usa el ultimo indice observado y lo marca como extrapolacion de serie.
    """
    if moneda != Moneda.ARS:
        return Deflactacion(monto=monto, aplicada=False, motivo="moneda_no_ARS")

    indices = _indice_por_mes(ipc_records)
    if not indices:
        return Deflactacion(monto=monto, aplicada=False, motivo="serie_IPC_vacia")

    origen = _mes(fecha_origen)
    destino = _mes(fecha_destino)
    indice_origen = indices.get(origen)
    if indice_origen is None:
        return Deflactacion(monto=monto, aplicada=False, motivo=f"falta_IPC_{origen}")

    meses_ordenados = sorted(indices)
    ultimo_mes = meses_ordenados[-1]
    mes_destino_usado = destino if destino in indices else ultimo_mes
    indice_destino = indices[mes_destino_usado]
    if destino > ultimo_mes:
        motivo = f"destino_posterior_a_serie_{ultimo_mes}"
    elif destino not in indices:
        return Deflactacion(monto=monto, aplicada=False, motivo=f"falta_IPC_{destino}")
    else:
        motivo = None

    factor = indice_destino / indice_origen
    ajustado = (monto * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Deflactacion(monto=ajustado, aplicada=True, motivo=motivo, factor=factor)


def mediana(valores: Iterable[Decimal]) -> Decimal | None:
    ordenados = sorted(valores)
    if not ordenados:
        return None
    mitad = len(ordenados) // 2
    if len(ordenados) % 2:
        return ordenados[mitad]
    return (ordenados[mitad - 1] + ordenados[mitad]) / Decimal("2")


def mad(valores: Iterable[Decimal]) -> Decimal | None:
    valores_lista = list(valores)
    centro = mediana(valores_lista)
    if centro is None:
        return None
    return mediana([abs(valor - centro) for valor in valores_lista])


def percentil(valores: Iterable[Decimal], porcentaje: Decimal) -> Decimal | None:
    ordenados = sorted(valores)
    if not ordenados:
        return None
    if len(ordenados) == 1:
        return ordenados[0]
    posicion = (len(ordenados) - 1) * porcentaje
    inferior = int(posicion)
    superior = min(inferior + 1, len(ordenados) - 1)
    fraccion = Decimal(str(posicion - inferior))
    return ordenados[inferior] + (ordenados[superior] - ordenados[inferior]) * fraccion


def posicion_relativa(valor: Decimal | None, historia: Iterable[Decimal]) -> Decimal | None:
    valores = sorted(historia)
    if valor is None or not valores:
        return None
    if len(valores) == 1:
        return Decimal("0.50")
    menores = sum(1 for item in valores if item < valor)
    iguales = sum(1 for item in valores if item == valor)
    return (Decimal(menores) + Decimal(iguales) / Decimal("2")) / Decimal(len(valores))


def _es_confirmada(tx: Any) -> bool:
    return tx.estado_verificacion in (None, EstadoVerificacionTransaccion.CONFIRMADA)


def _nombre_categoria(tx: Any) -> str:
    categoria = getattr(tx, "categoria", None)
    return (getattr(categoria, "nombre", "") or "").strip().casefold()


def _nombre_subcategoria(tx: Any) -> str:
    subcategoria = getattr(tx, "subcategoria", None)
    return (getattr(subcategoria, "nombre", "") or "").strip().casefold()


def es_aporte_meta(tx: Any) -> bool:
    if getattr(tx, "movimiento_meta_id", None) is not None:
        return True
    if _nombre_categoria(tx) in {"ahorro", "metas", "meta", "ahorros"}:
        return True
    desc = (getattr(tx, "descripcion", "") or "").casefold()
    return "aporte a la meta" in desc or "aporte meta" in desc or "a la meta:" in desc


def es_pago_resumen(tx: Any) -> bool:
    if getattr(tx, "pago_resumen_vencimiento", None) is not None:
        return True
    desc = (getattr(tx, "descripcion", "") or "").casefold()
    cat = _nombre_categoria(tx)
    subcat = _nombre_subcategoria(tx)
    if any(k in desc for k in ("pago resumen", "pago de resumen", "pago tarjeta", "pago de tarjeta", "pago resumen tarjeta", "tarjeta santiago", "tarjeta santi", "amex")):
        return True
    if "resumen" in desc:
        return True
    if cat == "banco" and (
        "tarjeta" in subcat
        or "préstamos" in subcat
        or "prestamos" in subcat
        or "tarjeta" in desc
        or desc.strip() in ("", "(cuota 1/1)")
    ):
        return True
    return False


def es_transferencia(tx: Any) -> bool:
    desc = (getattr(tx, "descripcion", "") or "").casefold()
    if any(k in desc for k in ("compra usd", "compra dolares", "compra dólares", "dolares", "dólares", "tranf", "transf", "transferencia interna", "balanz")):
        return True
    return False


def es_gasto_consumo(tx: Any) -> bool:
    return (
        tx.tipo == TipoTransaccion.EGRESO
        and _es_confirmada(tx)
        and not getattr(tx, "es_padre_cuotas", False)
        and not es_aporte_meta(tx)
        and not es_pago_resumen(tx)
        and not es_transferencia(tx)
    )


def gasto_ciclo(
    transacciones: Iterable[Any],
    fecha_inicio: date,
    fecha_fin: date,
    fecha_destino: date,
    ipc_records: Iterable[Any],
    moneda: Moneda = Moneda.ARS,
) -> GastoCiclo:
    """Devuelve el gasto elegible de un ciclo con la misma regla en todo el sistema."""
    filas = tuple(
        tx for tx in transacciones
        if fecha_inicio <= tx.fecha <= fecha_fin
        and tx.moneda == moneda
        and es_gasto_consumo(tx)
    )
    nominal = sum((tx.monto for tx in filas), ZERO)
    deflactado = ZERO
    factores = []
    for tx in filas:
        ajuste = deflactar_monto(tx.monto, tx.fecha, fecha_destino, ipc_records, moneda)
        deflactado += ajuste.monto
        factores.append(ajuste.factor)
    factor_medio = sum(factores, ZERO) / Decimal(len(factores)) if factores else ONE
    return GastoCiclo(filas, nominal, deflactado, factor_medio)


def _es_recurrente_declarado(tx: Any) -> bool:
    return bool(getattr(tx, "es_recurrente", False) or getattr(tx, "recurrente_id", None))


def clasificar_gastos(
    transacciones: Iterable[Any],
    ciclos: Iterable[tuple[date, date]],
    ipc_records: Iterable[Any],
    fecha_destino: date,
    recurrentes_declarados: Iterable[Any] = (),
) -> ClasificacionGasto:
    """Clasifica gastos con una sola regla compartida por perfil y proyeccion.

    Un recurrente no declarado se agrupa por categoria, subcategoria, billetera
    y firma textual de la descripcion; dentro de esa estructura se forman bandas
    de monto deflactado con tolerancia del 20%. La banda debe aparecer una vez
    por ciclo en al menos el 60% de los ciclos con datos y tener MAD <= 20%
    de su mediana. Si aparece varias veces en el mismo ciclo, se considera variable.
    """
    txs = [tx for tx in transacciones if es_gasto_consumo(tx)]
    ciclos_lista = list(ciclos)
    if not txs or not ciclos_lista:
        return ClasificacionGasto((), (), tuple(txs), frozenset())

    por_estructura: dict[tuple[Any, Any, Any, str], list[tuple[int, Any, Decimal]]] = {}
    for tx in txs:
        ciclo_idx = next((idx for idx, (inicio, fin) in enumerate(ciclos_lista) if inicio <= tx.fecha <= fin), None)
        if ciclo_idx is None:
            continue
        firma = _firma_gasto(tx)[2]
        if not firma:
            continue
        ajustado = deflactar_monto(tx.monto, tx.fecha, fecha_destino, ipc_records, tx.moneda).monto
        grupo = (tx.categoria_id, getattr(tx, "subcategoria_id", None), tx.billetera_id, firma)
        por_estructura.setdefault(grupo, []).append((ciclo_idx, tx, ajustado))

    ciclos_disponibles = {ciclo_idx for ocurrencias in por_estructura.values() for ciclo_idx, _, _ in ocurrencias}
    denominador_ciclos = Decimal(len(ciclos_disponibles) or len(ciclos_lista))

    declaradas = {(getattr(item, "categoria_id", None), getattr(item, "moneda", None)) for item in recurrentes_declarados}
    categorias_recurrentes: set[Any] = set()
    recurrentes_ids: set[Any] = set()
    for grupo, ocurrencias in por_estructura.items():
        clusters: list[list[tuple[int, Any, Decimal]]] = []
        for ocurrencia in sorted(ocurrencias, key=lambda item: item[2]):
            destino = next((cluster for cluster in clusters if abs(ocurrencia[2] - (mediana([x[2] for x in cluster]) or ZERO)) <= (mediana([x[2] for x in cluster]) or ZERO) * Decimal("0.20")), None)
            if destino is None:
                clusters.append([ocurrencia])
            else:
                destino.append(ocurrencia)
        for cluster in clusters:
            por_ciclo: dict[int, list[tuple[int, Any, Decimal]]] = {}
            for item in cluster:
                por_ciclo.setdefault(item[0], []).append(item)
            if any(len(items) > 1 for items in por_ciclo.values()):
                continue
            valores = [items[0][2] for items in por_ciclo.values()]
            centro = mediana(valores)
            dispersion = mad(valores)
            presencia = Decimal(len(por_ciclo)) / denominador_ciclos
            if centro is not None and presencia >= Decimal("0.60") and (dispersion or ZERO) <= center_or_zero(centro * Decimal("0.20")):
                recurrentes_ids.update(item[1].id for item in cluster)
                categorias_recurrentes.add(grupo[0])

    comprometidos = tuple(recurrentes_declarados)
    recurrentes = tuple(tx for tx in txs if tx.id in recurrentes_ids and not _es_recurrente_declarado(tx))
    variables = tuple(tx for tx in txs if tx not in comprometidos and tx not in recurrentes)
    return ClasificacionGasto(comprometidos, recurrentes, variables, frozenset(categorias_recurrentes))


def center_or_zero(value: Decimal | None) -> Decimal:
    return value if value is not None else ZERO
