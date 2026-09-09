from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import difflib
import re
from typing import Any, Iterable
import unicodedata

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
class StreamRecurrente:
    descripcion: str
    categoria: str
    billetera: str
    frecuencia: str  # semanal, quincenal, bimensual, mensual, anual, desconocida
    estado: str  # NUEVO, EN_DETECCION, MADURO, MUERTO
    cantidad_ocurrencias: int
    promedio_dias: Decimal
    monto_mediano_deflactado: Decimal
    ultimo_monto: Decimal
    proxima_fecha_esperada: date | None
    senal: str = "DECLARADO"  # DECLARADO, DESCRIPCION_SIMILAR, GEOMETRIA
    categoria_id: Any = None
    subcategoria_id: Any = None
    billetera_id: Any = None
    moneda: Moneda = Moneda.ARS
    transacciones_ids: tuple[Any, ...] = ()

    @property
    def promedio_dias_entre_ocurrencias(self) -> Decimal:
        return self.promedio_dias

    @property
    def proxima_fecha(self) -> date | None:
        return self.proxima_fecha_esperada


@dataclass(frozen=True)
class ClasificacionGasto:
    comprometidos: tuple[Any, ...]
    recurrentes_detectados: tuple[Any, ...]
    variables: tuple[Any, ...]
    categorias_recurrentes: frozenset[Any]
    streams: tuple[StreamRecurrente, ...] = ()


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


def _normalizar_descripcion(desc: str | None) -> str:
    """Normaliza texto para comparacion difflib.

    Elimina tildes, prefijos sinteticos del seed ([historico]), referencias de fechas,
    indicadores de cuotas y numeros varios para aislar el nombre del comercio o servicio.
    """
    if not desc:
        return ""
    texto = unicodedata.normalize("NFKD", desc)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).casefold()
    texto = re.sub(r"\[?\s*historico\s*\]?", "", texto)
    texto = re.sub(r"\b\d{1,2}[/-]\d{2,4}\b", "", texto)
    texto = re.sub(r"\b\d{4}[/-]\d{1,2}\b", "", texto)
    texto = re.sub(r"\(?\s*cuota\s*\d+\s*/\s*\d+\s*\)?", "", texto)
    texto = re.sub(r"\d+", "", texto)
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    return texto


def _distancia_circular_dias(d1: int, d2: int) -> int:
    """Calcula la distancia minima entre dos dias del mes en anillo de 31 dias.

    Por ejemplo, el dia 31 y el dia 1 estan a 1 dia de distancia, no a 30.
    """
    diff = abs(d1 - d2)
    return min(diff, 31 - diff)


def _proxima_fecha_esperada(ultima_fecha: date, frecuencia: str) -> date | None:
    """Calcula la fecha de vencimiento o cobro esperada segun la frecuencia regular."""
    if frecuencia == "semanal":
        return ultima_fecha + timedelta(days=7)
    elif frecuencia == "quincenal":
        return ultima_fecha + timedelta(days=15)
    elif frecuencia == "mensual":
        anio = ultima_fecha.year + (1 if ultima_fecha.month == 12 else 0)
        mes = 1 if ultima_fecha.month == 12 else ultima_fecha.month + 1
        dia = min(ultima_fecha.day, 28 if mes == 2 else (30 if mes in (4, 6, 9, 11) else 31))
        return date(anio, mes, dia)
    elif frecuencia == "bimensual":
        anio = ultima_fecha.year + ((ultima_fecha.month + 1) // 12)
        mes = (ultima_fecha.month + 1) % 12 + 1
        dia = min(ultima_fecha.day, 28 if mes == 2 else (30 if mes in (4, 6, 9, 11) else 31))
        return date(anio, mes, dia)
    elif frecuencia == "anual":
        try:
            return date(ultima_fecha.year + 1, ultima_fecha.month, ultima_fecha.day)
        except ValueError:
            return date(ultima_fecha.year + 1, ultima_fecha.month, 28)
    return None


def _inferir_frecuencia_y_estado(
    fechas: list[date],
    fecha_referencia: date,
    total_txs_usuario: int,
) -> tuple[str, str, Decimal, date | None]:
    """Infiere la frecuencia y el estado del stream de acuerdo con las ventanas de tolerancia.

    Frecuencias permitidas (conjunto cerrado):
      - semanal, quincenal, bimensual, mensual, anual, desconocida.

    Ventanas de tolerancia (IBM Research):
      - semanal y quincenal: tolerancia de 2 dias (absorbe feriados y fines de semana).
      - mensual y bimensual: tolerancia de 7 dias (absorbe meses de 28 a 31 dias).
      - anual: tolerancia de 15 dias.

    Estados del stream:
      - NUEVO: una sola ocurrencia o frecuencia desconocida.
      - EN_DETECCION: dos ocurrencias con cadencia compatible (o >= 3 si historial < 40 txs).
      - MADURO: tres o mas ocurrencias con cadencia regular (para anual, dos alcanzan). Requiere >= 40 txs totales.
      - MUERTO: estaba en deteccion o maduro y no aparecio en la fecha esperada mas la ventana de tolerancia.
    """
    n = len(fechas)
    if n <= 1:
        return "desconocida", "NUEVO", Decimal("0"), None

    deltas = [(fechas[i + 1] - fechas[i]).days for i in range(n - 1)]
    if any(d < 5 for d in deltas):
        # Multiples ocurrencias en menos de 5 dias indican consumo variable agrupado
        promedio_invalido = Decimal(sum(deltas)) / Decimal(len(deltas))
        return "desconocida", "NUEVO", promedio_invalido.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), None

    promedio_dias = (Decimal(sum(deltas)) / Decimal(len(deltas))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    frecuencia = "desconocida"
    tolerancia = 7
    if n == 2:
        d = deltas[0]
        if 5 <= d <= 9:
            frecuencia = "semanal"
            tolerancia = 2
        elif 12 <= d <= 18:
            frecuencia = "quincenal"
            tolerancia = 2
        elif 21 <= d <= 38:
            frecuencia = "mensual"
            tolerancia = 7
        elif 53 <= d <= 67:
            frecuencia = "bimensual"
            tolerancia = 7
        elif 350 <= d <= 380:
            frecuencia = "anual"
            tolerancia = 15
    else:
        # n >= 3
        if 5 <= promedio_dias <= 9 and all(5 <= d <= 9 for d in deltas):
            frecuencia = "semanal"
            tolerancia = 2
        elif 12 <= promedio_dias <= 18 and all(12 <= d <= 18 for d in deltas):
            frecuencia = "quincenal"
            tolerancia = 2
        elif 21 <= promedio_dias <= 38 and all(21 <= d <= 38 for d in deltas):
            frecuencia = "mensual"
            tolerancia = 7
        elif 53 <= promedio_dias <= 67 and all(53 <= d <= 67 for d in deltas):
            frecuencia = "bimensual"
            tolerancia = 7
        elif 350 <= promedio_dias <= 380 and all(350 <= d <= 380 for d in deltas):
            frecuencia = "anual"
            tolerancia = 15

    if frecuencia == "desconocida":
        return "desconocida", "NUEVO", promedio_dias, None

    proxima = _proxima_fecha_esperada(fechas[-1], frecuencia)
    if proxima is not None and fecha_referencia > proxima + timedelta(days=tolerancia):
        estado = "MUERTO"
    elif frecuencia == "anual" and n >= 2:
        estado = "MADURO" if total_txs_usuario >= 40 else "EN_DETECCION"
    elif n >= 3:
        estado = "MADURO" if total_txs_usuario >= 40 else "EN_DETECCION"
    else:
        estado = "EN_DETECCION"

    return frecuencia, estado, promedio_dias, proxima


def _es_recurrente_declarado(tx: Any) -> bool:
    return bool(getattr(tx, "es_recurrente", False) or getattr(tx, "recurrente_id", None))


def clasificar_gastos(
    transacciones: Iterable[Any],
    ciclos: Iterable[tuple[date, date]],
    ipc_records: Iterable[Any],
    fecha_destino: date,
    recurrentes_declarados: Iterable[Any] = (),
    total_transacciones_usuario: int | None = None,
) -> ClasificacionGasto:
    """Clasifica gastos mediante cascada de tres senales (Declarado, Similaridad, Geometria).

    Criterios y fuentes de umbrales:
      1. SENAL DECLARADO:
         - Certeza absoluta: suscripciones activas, cuotas y gastos con flag o ID recurrente.
      2. SENAL DESCRIPCION SIMILAR:
         - SequenceMatcher de difflib con ratio >= 0.75 sobre descripcion normalizada.
           Este umbral proviene del trabajo de IBM Research en transacciones con historial corto,
           tolerando numeros de factura, fechas y pequenas variaciones ortograficas.
         - Requiere misma moneda y misma categoria.
         - Si una descripcion ocurre multiples veces dentro de un mismo mes y no posee cadencia
           semanal/quincenal, se clasifica como consumo variable recurrente (ej: supermercados).
      3. SENAL GEOMETRIA SIN DESCRIPCION:
         - Para gastos con descripcion vacia o sin match textual.
         - Misma moneda y misma billetera (medio de pago consistente).
         - Misma subcategoria (o misma categoria si ambas no tienen subcategoria).
         - Distancia circular de dia del mes <= 7 dias (min(|d1 - d2|, 31 - |d1 - d2|) <= 7).
         - Banda de monto deflactado relativo <= 20% (abs(m1 - m2) / max(m1, m2) <= 0.20).
         - Filtro anti-falsos positivos: en cadencias mensuales/bimensuales, maximo 1 gasto por
           mes en el stream, y exclusion si la categoria/subcategoria promedia >= 3 txs/mes.

    Reglas de madurez y proyeccion:
      - Solo los streams MADUROS (>= 3 ocurrencias regulares, o >= 2 para anual, y con usuario
        teniendo al menos 40 transacciones de historial) ingresan en recurrentes_detectados.
      - Los streams EN_DETECCION, NUEVO y MUERTO se exponen en streams pero sus transacciones
        permanecen en variables para no comprometer indebidamente la proyeccion.
    """
    txs_todos = list(transacciones)
    total_txs = total_transacciones_usuario if total_transacciones_usuario is not None else len(txs_todos)
    txs = [tx for tx in txs_todos if es_gasto_consumo(tx)]
    if not txs:
        return ClasificacionGasto(tuple(recurrentes_declarados), (), (), frozenset(), ())

    ipc_lista = list(ipc_records)
    deflactados: dict[Any, Decimal] = {
        tx.id: deflactar_monto(tx.monto, tx.fecha, fecha_destino, ipc_lista, tx.moneda).monto
        for tx in txs
    }

    asignadas: set[Any] = set()
    streams: list[StreamRecurrente] = []

    # 1. SENAL DECLARADO
    txs_declaradas = [tx for tx in txs if _es_recurrente_declarado(tx)]
    por_decl = defaultdict(list)
    for tx in txs_declaradas:
        k = getattr(tx, "recurrente_id", None) or tx.descripcion or "recurrente"
        por_decl[k].append(tx)

    for _, cluster in por_decl.items():
        cluster_sorted = sorted(cluster, key=lambda x: x.fecha)
        fechas = [x.fecha for x in cluster_sorted]
        frec, est, avg_d, prox = _inferir_frecuencia_y_estado(fechas, fecha_destino, total_txs)
        med_monto = mediana([deflactados[x.id] for x in cluster_sorted]) or ZERO
        nombre_cat = getattr(cluster_sorted[-1].categoria, "nombre", None) or "Sin categoría"
        nombre_bil = getattr(cluster_sorted[-1].billetera, "nombre", None) or "Sin billetera"
        s = StreamRecurrente(
            descripcion=cluster_sorted[-1].descripcion or "Gasto recurrente declarado",
            categoria=nombre_cat,
            billetera=nombre_bil,
            frecuencia=frec,
            estado=est,
            cantidad_ocurrencias=len(cluster_sorted),
            promedio_dias=avg_d,
            monto_mediano_deflactado=med_monto,
            ultimo_monto=cluster_sorted[-1].monto,
            proxima_fecha_esperada=prox,
            senal="DECLARADO",
            categoria_id=cluster_sorted[-1].categoria_id,
            subcategoria_id=getattr(cluster_sorted[-1], "subcategoria_id", None),
            billetera_id=cluster_sorted[-1].billetera_id,
            moneda=cluster_sorted[-1].moneda,
            transacciones_ids=tuple(x.id for x in cluster_sorted),
        )
        streams.append(s)
        asignadas.update(x.id for x in cluster_sorted)

    # 2. SENAL DESCRIPCION SIMILAR (difflib SequenceMatcher >= 0.75)
    pendientes_s2 = [tx for tx in txs if tx.id not in asignadas]
    candidatos_s2 = [tx for tx in pendientes_s2 if _normalizar_descripcion(tx.descripcion)]
    usadas_s2: set[Any] = set()

    for tx in candidatos_s2:
        if tx.id in usadas_s2:
            continue
        d1 = _normalizar_descripcion(tx.descripcion)
        cluster = [tx]
        for otro in candidatos_s2:
            if otro.id == tx.id or otro.id in usadas_s2:
                continue
            if otro.moneda != tx.moneda or otro.categoria_id != tx.categoria_id:
                continue
            d2 = _normalizar_descripcion(otro.descripcion)
            ratio = difflib.SequenceMatcher(None, d1, d2).ratio()
            if ratio >= 0.75:
                cluster.append(otro)

        if len(cluster) >= 2:
            for x in cluster:
                usadas_s2.add(x.id)
                asignadas.add(x.id)

            cluster_sorted = sorted(cluster, key=lambda x: x.fecha)
            por_mes = defaultdict(list)
            for x in cluster_sorted:
                por_mes[x.fecha.strftime("%Y-%m")].append(x)
            max_mes = max(len(items) for items in por_mes.values())

            fechas = [x.fecha for x in cluster_sorted]
            frec, est, avg_d, prox = _inferir_frecuencia_y_estado(fechas, fecha_destino, total_txs)

            # Si ocurre mas de una vez por mes y no es semanal ni quincenal, es consumo variable frecuente
            if max_mes > 1 and frec not in ("semanal", "quincenal"):
                continue

            if frec != "desconocida" and est in ("EN_DETECCION", "MADURO", "MUERTO"):
                med_monto = mediana([deflactados[x.id] for x in cluster_sorted]) or ZERO
                nombre_cat = getattr(cluster_sorted[-1].categoria, "nombre", None) or "Sin categoría"
                nombre_bil = getattr(cluster_sorted[-1].billetera, "nombre", None) or "Sin billetera"
                s = StreamRecurrente(
                    descripcion=cluster_sorted[-1].descripcion or d1,
                    categoria=nombre_cat,
                    billetera=nombre_bil,
                    frecuencia=frec,
                    estado=est,
                    cantidad_ocurrencias=len(cluster_sorted),
                    promedio_dias=avg_d,
                    monto_mediano_deflactado=med_monto,
                    ultimo_monto=cluster_sorted[-1].monto,
                    proxima_fecha_esperada=prox,
                    senal="DESCRIPCION_SIMILAR",
                    categoria_id=cluster_sorted[-1].categoria_id,
                    subcategoria_id=getattr(cluster_sorted[-1], "subcategoria_id", None),
                    billetera_id=cluster_sorted[-1].billetera_id,
                    moneda=cluster_sorted[-1].moneda,
                    transacciones_ids=tuple(x.id for x in cluster_sorted),
                )
                streams.append(s)

    # 3. SENAL GEOMETRIA SIN DESCRIPCION
    pendientes_s3 = [tx for tx in txs if tx.id not in asignadas]
    usadas_s3: set[Any] = set()

    for tx in pendientes_s3:
        if tx.id in usadas_s3:
            continue
        m1 = deflactados[tx.id]
        cluster = [tx]
        for otro in pendientes_s3:
            if otro.id == tx.id or otro.id in usadas_s3:
                continue
            if otro.moneda != tx.moneda or otro.billetera_id != tx.billetera_id:
                continue

            sub1 = getattr(tx, "subcategoria_id", None)
            sub2 = getattr(otro, "subcategoria_id", None)
            if sub1 is not None and sub2 is not None:
                if sub1 != sub2:
                    continue
            elif sub1 is None and sub2 is None:
                if tx.categoria_id != otro.categoria_id:
                    continue
            else:
                continue

            # Distancia circular <= 7 dias
            if _distancia_circular_dias(tx.fecha.day, otro.fecha.day) > 7:
                continue

            # Banda de monto deflactado <= 20%
            m2 = deflactados[otro.id]
            max_m = max(m1, m2)
            if max_m > ZERO and abs(m1 - m2) / max_m <= Decimal("0.20"):
                cluster.append(otro)

        if len(cluster) >= 2:
            cluster_sorted = sorted(cluster, key=lambda x: x.fecha)
            por_mes = defaultdict(list)
            for x in cluster_sorted:
                por_mes[x.fecha.strftime("%Y-%m")].append(x)
            max_mes = max(len(items) for items in por_mes.values())

            fechas = [x.fecha for x in cluster_sorted]
            frec, est, avg_d, prox = _inferir_frecuencia_y_estado(fechas, fecha_destino, total_txs)

            if max_mes > 1 and frec not in ("semanal", "quincenal"):
                continue

            # Exclusion de subcategorias/categorias de alta frecuencia variable
            sub_id = getattr(cluster_sorted[0], "subcategoria_id", None)
            cat_id = cluster_sorted[0].categoria_id
            if sub_id is not None:
                txs_grupo = [t for t in txs if getattr(t, "subcategoria_id", None) == sub_id]
            else:
                txs_grupo = [t for t in txs if t.categoria_id == cat_id]

            por_mes_global = defaultdict(list)
            for t in txs_grupo:
                por_mes_global[t.fecha.strftime("%Y-%m")].append(t)
            meses_cluster = set(por_mes.keys())
            max_global = max((len(por_mes_global[m]) for m in meses_cluster), default=0)
            if max_global >= 3 and frec in ("mensual", "bimensual"):
                continue

            if frec != "desconocida" and est in ("EN_DETECCION", "MADURO", "MUERTO"):
                med_monto = mediana([deflactados[x.id] for x in cluster_sorted]) or ZERO
                nombre_sub = getattr(cluster_sorted[-1].subcategoria, "nombre", None) if getattr(cluster_sorted[-1], "subcategoria", None) else None
                nombre_cat = getattr(cluster_sorted[-1].categoria, "nombre", None) if cluster_sorted[-1].categoria else "Sin categoría"
                nombre_bil = getattr(cluster_sorted[-1].billetera, "nombre", None) or "Sin billetera"
                descs_reales = [x.descripcion for x in cluster_sorted if x.descripcion and x.descripcion.strip()]
                desc_rep = descs_reales[-1] if descs_reales else (nombre_sub or nombre_cat)
                s = StreamRecurrente(
                    descripcion=desc_rep,
                    categoria=nombre_cat,
                    billetera=nombre_bil,
                    frecuencia=frec,
                    estado=est,
                    cantidad_ocurrencias=len(cluster_sorted),
                    promedio_dias=avg_d,
                    monto_mediano_deflactado=med_monto,
                    ultimo_monto=cluster_sorted[-1].monto,
                    proxima_fecha_esperada=prox,
                    senal="GEOMETRIA",
                    categoria_id=cluster_sorted[-1].categoria_id,
                    subcategoria_id=getattr(cluster_sorted[-1], "subcategoria_id", None),
                    billetera_id=cluster_sorted[-1].billetera_id,
                    moneda=cluster_sorted[-1].moneda,
                    transacciones_ids=tuple(x.id for x in cluster_sorted),
                )
                streams.append(s)
                for x in cluster:
                    usadas_s3.add(x.id)
                    asignadas.add(x.id)

    maduros_ids = {tx_id for s in streams if s.estado == "MADURO" for tx_id in s.transacciones_ids}
    categorias_recurrentes = {s.categoria_id for s in streams if s.estado == "MADURO"}

    comprometidos = tuple(recurrentes_declarados)
    recurrentes = tuple(tx for tx in txs if tx.id in maduros_ids and not _es_recurrente_declarado(tx))
    variables = tuple(tx for tx in txs if tx not in comprometidos and tx not in recurrentes)
    return ClasificacionGasto(comprometidos, recurrentes, variables, frozenset(categorias_recurrentes), tuple(streams))


def center_or_zero(value: Decimal | None) -> Decimal:
    return value if value is not None else ZERO
