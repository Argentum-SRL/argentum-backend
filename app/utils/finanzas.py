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
    clase: str = "HABITO"  # COMPROMISO, HABITO, VARIABLE

    @property
    def promedio_dias_entre_ocurrencias(self) -> Decimal:
        return self.promedio_dias

    @property
    def proxima_fecha(self) -> date | None:
        return self.proxima_fecha_esperada


@dataclass(frozen=True)
class ClasificacionGasto:
    comprometidos: tuple[Any, ...]
    habitos: tuple[Any, ...]
    variables: tuple[Any, ...]
    categorias_recurrentes: frozenset[Any] = frozenset()
    streams: tuple[StreamRecurrente, ...] = ()
    recurrentes_detectados: tuple[Any, ...] = ()

    def __post_init__(self):
        if not self.recurrentes_detectados and self.habitos:
            object.__setattr__(self, "recurrentes_detectados", self.habitos)


# ==============================================================================
# MAPEO DE CATEGORÍAS Y SUBCATEGORÍAS ELEGIBLES PARA COMPROMISOS (CRITERIO CONTRACTUAL)
# ==============================================================================
# Un gasto es COMPROMISO si hay una contraparte y dejar de pagar tiene una consecuencia
# jurídica, financiera o de servicio grave que trasciende el mero cese de consumo inmediato:
# - Alquiler: desalojo por falta de pago / rescisión forzosa de locación.
# - Expensas: demanda ejecutiva de cobro y eventual embargo de inmueble.
# - Luz (Edenor, etc.): corte físico del suministro eléctrico esencial.
# - Gas (Metrogas, etc.): corte del suministro de gas por red.
# - Agua (AySA, etc.): corte o mora en servicio sanitario esencial.
# - Internet y cable (Fibertel, etc.): corte de conectividad digital.
# - Celular: suspensión de línea y eventual bloqueo de equipo/servicio.
# - Seguros: suspensión o caducidad inmediata de la cobertura de póliza.
# - Impuestos (en Servicios, ej. TGI/ABL): mora fiscal, multas y ejecución tributaria.
# - Cuotas (en Educación): suspensión de regularidad académica y pérdida de matrícula.
# - Obra social / Prepaga (en Salud): desafiliación y pérdida de cobertura médica de urgencia.
# - Préstamos / Tarjeta de crédito (en Banco): mora punitoria, ejecución y reporte crediticio (Veraz/BCRA).
#
# Un gasto es HÁBITO si se repite con regularidad pero es elegible / discrecional.
# Si el mes que viene no se realiza, no hay consecuencias jurídicas ni mora alguna:
# - Delivery, salidas, peluquería/cuidado personal, verdulería, carnicería, combustible, transporte.
# ==============================================================================

SUBCATEGORIAS_COMPROMISO_ELEGIBLES: frozenset[str] = frozenset({
    "alquiler",
    "expensas",
    "luz",
    "gas",
    "agua",
    "internet y cable",
    "celular",
    "seguros",
    "impuestos",
    "cuotas",
    "obra social / prepaga",
    "prestamos",
    "préstamos",
    "tarjeta de credito",
    "tarjeta de crédito",
})

CATEGORIAS_COMPROMISO_DIRECTAS: frozenset[str] = frozenset({
    "servicios",
    "comunicacion",
    "comunicación",
})


def es_categoria_elegible_compromiso(
    cat_nombre: str | None,
    subcat_nombre: str | None,
    descripcion: str | None = None,
) -> bool:
    """Evalúa si una categoría/subcategoría/concepto habilita la clasificación como COMPROMISO.

    Aplica el criterio de consecuencia de impago.
    Casos límite resueltos con evidencia:
    - Hogar: conviven alquiler y muebles. Las subcategorías de Hogar (Muebles, Limpieza, Reparaciones)
      NO son compromisos. Si la transacción no tiene subcategoría pero la descripción normalizada
      refiere a 'alquiler' o 'expensa' (evidencia del seed histórico de testingadmin), habilita compromiso.
    - Banco: 'impuestos' de banco (débitos/créditos) no son compromisos; 'impuestos' en Servicios (TGI/ABL) sí.
    - Salud: Farmacia es hábito/variable; sólo 'Obra social / Prepaga' habilita compromiso.
    - Educación: Idiomas y materiales son hábitos/variables; sólo 'Cuotas' habilita compromiso.
    - Transporte: Combustible (YPF), peajes y SUBE son hábitos o variables; no compromisos.
    - Indumentaria: Toda indumentaria es variable o hábito; sin contraparte contractual de impago.
    """
    sub_norm = _normalizar_descripcion(subcat_nombre)
    cat_norm = _normalizar_descripcion(cat_nombre)
    desc_norm = _normalizar_descripcion(descripcion)

    if sub_norm:
        if sub_norm in SUBCATEGORIAS_COMPROMISO_ELEGIBLES:
            if sub_norm == "impuestos" and cat_norm == "banco":
                return False
            return True
        return False

    if cat_norm in CATEGORIAS_COMPROMISO_DIRECTAS:
        return True

    if cat_norm == "hogar":
        if any(k in desc_norm for k in ("alquiler", "expensa")):
            return True
        return False

    return False


def monto_mensual_deflactado_stream(stream: StreamRecurrente) -> Decimal:
    """Normaliza el monto mediano deflactado a frecuencia mensual estándar."""
    m = stream.monto_mediano_deflactado
    frec = stream.frecuencia
    if frec == "mensual":
        return m
    elif frec == "quincenal":
        return m * Decimal("2")
    elif frec == "semanal":
        return (m * Decimal("52") / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif frec == "bimensual":
        return (m / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif frec == "anual":
        return (m / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return m


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


def _determinar_clase_stream(
    cluster_sorted: list[Any],
    deflactados: dict[Any, Decimal],
    ciclos_con_datos_usuario: list[tuple[date, date]],
    estado: str,
    cat_nombre: str | None,
    subcat_nombre: str | None,
    descripcion: str | None,
    senal: str,
) -> str:
    """Aplica los criterios técnicos para clasificar un stream en COMPROMISO, HABITO o VARIABLE.

    Criterios técnicos exactos:
    - COMPROMISO si:
      a) Es declarado (senal == "DECLARADO"): certeza, sin detección.
      b) Es detectado Y cumple TODAS estas condiciones a la vez:
         - Cae en categoría o subcategoría elegible (criterio contractual de consecuencia).
         - Al menos 3 ocurrencias (len(cluster_sorted) >= 3).
         - Estado MADURO (un compromiso solo puede estar MADURO; EN_DETECCION no es compromiso).
         - El usuario tiene al menos 3 ciclos con datos (len(ciclos_con_datos_usuario) >= 3).
         - Presencia en al menos el 80% de los ciclos con datos de su intervalo activo (>= 0.80).
         - Estabilidad alta del monto deflactado: MAD / Mediana <= 0.10.
    - HÁBITO si:
      - Al menos 3 ocurrencias (len(cluster_sorted) >= 3).
      - Presencia en al menos el 60% de los ciclos con datos de su intervalo activo (>= 0.60).
      - Estado MADURO.
      - No cumple condiciones de compromiso.
    - VARIABLE:
      - Todo lo demás.
    """
    if senal == "DECLARADO":
        return "COMPROMISO"

    ocurrencias = len(cluster_sorted)
    if ocurrencias < 3:
        return "VARIABLE"

    # Presencia en ciclos con datos dentro del intervalo activo del stream
    fechas = [x.fecha for x in cluster_sorted]
    f_min, f_max = fechas[0], fechas[-1]
    ciclos_span = [c for c in ciclos_con_datos_usuario if c[1] >= f_min and c[0] <= f_max]
    ciclos_con_stream = [c for c in ciclos_span if any(c[0] <= tx.fecha <= c[1] for tx in cluster_sorted)]
    presencia = Decimal(len(ciclos_con_stream)) / Decimal(len(ciclos_span)) if ciclos_span else ZERO

    # Estabilidad del monto deflactado (MAD / Mediana <= 0.10)
    montos = [deflactados[x.id] for x in cluster_sorted]
    med = mediana(montos) or ZERO
    d_mad = mad(montos) or ZERO
    mad_ratio = (d_mad / med) if med > ZERO else Decimal("1")

    total_ciclos_datos = len(ciclos_con_datos_usuario)
    elegible = es_categoria_elegible_compromiso(cat_nombre, subcat_nombre, descripcion)
    es_maduro = (estado == "MADURO")

    es_compromiso = (
        elegible
        and es_maduro
        and total_ciclos_datos >= 3
        and ocurrencias >= 3
        and presencia >= Decimal("0.80")
        and mad_ratio <= Decimal("0.10")
    )

    if es_compromiso:
        return "COMPROMISO"

    if es_maduro and ocurrencias >= 3 and presencia >= Decimal("0.60"):
        return "HABITO"

    return "VARIABLE"


def clasificar_gastos(
    transacciones: Iterable[Any],
    ciclos: Iterable[tuple[date, date]],
    ipc_records: Iterable[Any],
    fecha_destino: date,
    recurrentes_declarados: Iterable[Any] = (),
    total_transacciones_usuario: int | None = None,
) -> ClasificacionGasto:
    """Clasifica gastos mediante cascada de tres señales y categorización contractual.

    Devuelve cuatro clases:
      1. comprometidos: Certezas declaradas y gastos detectados con consecuencia jurídica grave.
      2. habitos: Gastos regulares elegibles/discrecionales (>= 3 ocurrencias, >= 60% presencia).
      3. variables: Consumo discrecional o aislado restante.
      4. streams: Todos los streams detectados con su clase asignada (COMPROMISO, HABITO o VARIABLE).
    """
    txs_todos = list(transacciones)
    total_txs = total_transacciones_usuario if total_transacciones_usuario is not None else len(txs_todos)
    txs = [tx for tx in txs_todos if es_gasto_consumo(tx)]
    if not txs:
        return ClasificacionGasto(tuple(recurrentes_declarados), (), (), frozenset(), ())

    ciclos_lista = list(ciclos)
    ciclos_con_datos_usuario = [c for c in ciclos_lista if any(c[0] <= tx.fecha <= c[1] for tx in txs)]

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
        sub_nom = getattr(cluster_sorted[-1].subcategoria, "nombre", None) if getattr(cluster_sorted[-1], "subcategoria", None) else None
        clase_stream = _determinar_clase_stream(
            cluster_sorted, deflactados, ciclos_con_datos_usuario, est, nombre_cat, sub_nom, cluster_sorted[-1].descripcion, "DECLARADO"
        )
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
            clase=clase_stream,
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
                sub_nom = getattr(cluster_sorted[-1].subcategoria, "nombre", None) if getattr(cluster_sorted[-1], "subcategoria", None) else None
                desc_final = cluster_sorted[-1].descripcion or d1
                clase_stream = _determinar_clase_stream(
                    cluster_sorted, deflactados, ciclos_con_datos_usuario, est, nombre_cat, sub_nom, desc_final, "DESCRIPCION_SIMILAR"
                )
                s = StreamRecurrente(
                    descripcion=desc_final,
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
                    clase=clase_stream,
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
                clase_stream = _determinar_clase_stream(
                    cluster_sorted, deflactados, ciclos_con_datos_usuario, est, nombre_cat, nombre_sub, desc_rep, "GEOMETRIA"
                )
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
                    clase=clase_stream,
                )
                streams.append(s)
                for x in cluster:
                    usadas_s3.add(x.id)
                    asignadas.add(x.id)

    compromiso_tx_ids = {
        tx_id
        for s in streams
        if s.clase == "COMPROMISO"
        for tx_id in s.transacciones_ids
    }
    habito_tx_ids = {
        tx_id
        for s in streams
        if s.clase == "HABITO"
        for tx_id in s.transacciones_ids
    }

    # Transacciones declaradas siempre pertenecen a compromisos
    declaradas_tx_ids = {tx.id for tx in txs if _es_recurrente_declarado(tx)}
    compromiso_tx_ids.update(declaradas_tx_ids)

    # Evitar solapamientos
    habito_tx_ids.difference_update(compromiso_tx_ids)

    categorias_recurrentes = {s.categoria_id for s in streams if s.clase in ("COMPROMISO", "HABITO")}

    comprometidos = tuple(recurrentes_declarados) + tuple(tx for tx in txs if tx.id in compromiso_tx_ids)
    habitos = tuple(tx for tx in txs if tx.id in habito_tx_ids)
    variables = tuple(tx for tx in txs if tx.id not in compromiso_tx_ids and tx.id not in habito_tx_ids)

    return ClasificacionGasto(
        comprometidos=comprometidos,
        habitos=habitos,
        variables=variables,
        categorias_recurrentes=frozenset(categorias_recurrentes),
        streams=tuple(streams),
        recurrentes_detectados=habitos,
    )


def center_or_zero(value: Decimal | None) -> Decimal:
    return value if value is not None else ZERO


def pinball_loss(y: Decimal, q: Decimal, tau: Decimal) -> Decimal:
    """Calcula la pérdida pinball (quantile loss) para el cuantil tau y observación y.

    L_tau(y, q) = max(tau * (y - q), (tau - 1) * (y - q)).
    Es una regla de puntuación estrictamente propia (strictly proper scoring rule).
    Averaged over a fine grid of taus, converges to Continuous Ranked Probability Score (CRPS).
    """
    diff = y - q
    if diff >= ZERO:
        return tau * diff
    return (tau - ONE) * diff


def estimar_gasto_diario_basico_robusto(
    txs_variables: Iterable[Any],
    dias_ciclo: int | Decimal,
    destino: date,
    ipc_records: Iterable[Any],
    moneda: Moneda = Moneda.ARS,
) -> tuple[Decimal, Decimal, Decimal]:
    """Estima el gasto diario básico robusto retirando el decil superior solo cuando estadísticamente corresponde.

    CRITERIO ESTADÍSTICO PARA APLICAR EL DESCARTE DEL DECIL SUPERIOR:
    El descarte del decil superior (percentil 90) aísla compras aisladas de gran monto (shocks
    esporádicos como electrodomésticos o pasajes) que distorsionan el ritmo diario estándar.
    Solo tiene sentido estadístico si se cumplen dos condiciones medibles en los datos:
      1. Tamaño de muestra suficiente: N >= 10 transacciones en el ciclo. Con menos de 10 datos,
         descartar observaciones amputa artificialmente la distribución muestral.
      2. Discontinuidad y shock extremo en la cola superior:
         max(montos) >= 3.0 * mediana Y p90 >= 2.5 * mediana.
         Si las transacciones tienen una distribución continua (como compras habituales de supermercado
         o salidas de diferentes montos), el decil superior NO es un shock sino consumo normal de mayor
         cuantía. Amputarlo eliminaría más del 30% del gasto legítimo del usuario (caso Manu).
    Si no se cumplen ambas condiciones, todas las transacciones forman parte de la tasa base
    y no se descarta ninguna observación (shock = []).

    Retorna:
      (tasa_diaria_base, total_base_deflactado, total_shock_deflactado)
    """
    dias = Decimal(str(dias_ciclo)) if dias_ciclo > ZERO else ONE
    montos_deflactados = [
        deflactar_monto(tx.monto, tx.fecha, destino, ipc_records, moneda).monto
        for tx in txs_variables
    ]
    if not montos_deflactados:
        return ZERO, ZERO, ZERO

    n_tx = len(montos_deflactados)
    med = mediana(montos_deflactados) or ZERO
    max_m = max(montos_deflactados)

    # Criterio estadístico: N >= 10 y shock extremo en la cola
    aplica_descarte = False
    p90 = None
    if n_tx >= 10 and med > ZERO:
        p90 = percentil(montos_deflactados, Decimal("0.90"))
        if p90 is not None and max_m >= Decimal("3.0") * med and p90 >= Decimal("2.5") * med:
            aplica_descarte = True

    if aplica_descarte and p90 is not None:
        base = [m for m in montos_deflactados if m < p90]
        shock = [m for m in montos_deflactados if m >= p90]
    else:
        base = montos_deflactados
        shock = []

    total_base = sum(base, ZERO)
    total_shock = sum(shock, ZERO)
    tasa_diaria = (total_base / dias).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return tasa_diaria, total_base, total_shock


def student_t_critical(df: int, level: str) -> Decimal:
    """Valores críticos de la distribución t de Student para grados de libertad df = K - 1.

    Nivel 95%: bilateral 0.05 (t_0.975).
    Nivel 80%: bilateral 0.20 (t_0.90).
    Nivel 50%: bilateral 0.50 (t_0.75).
    """
    if level == "95":
        table = {
            1: Decimal("12.706"), 2: Decimal("4.303"), 3: Decimal("3.182"),
            4: Decimal("2.776"), 5: Decimal("2.571"), 6: Decimal("2.447"),
            7: Decimal("2.365"), 8: Decimal("2.306"), 9: Decimal("2.262"),
            10: Decimal("2.228"), 11: Decimal("2.201"),
        }
        return table.get(max(1, min(11, df)), Decimal("2.100"))
    elif level == "80":
        table = {
            1: Decimal("3.078"), 2: Decimal("1.886"), 3: Decimal("1.638"),
            4: Decimal("1.533"), 5: Decimal("1.476"), 6: Decimal("1.440"),
            7: Decimal("1.415"), 8: Decimal("1.397"), 9: Decimal("1.383"),
            10: Decimal("1.372"), 11: Decimal("1.363"),
        }
        return table.get(max(1, min(11, df)), Decimal("1.340"))
    elif level == "50":
        table = {
            1: Decimal("1.000"), 2: Decimal("0.816"), 3: Decimal("0.765"),
            4: Decimal("0.741"), 5: Decimal("0.727"), 6: Decimal("0.718"),
            7: Decimal("0.711"), 8: Decimal("0.706"), 9: Decimal("0.703"),
            10: Decimal("0.700"), 11: Decimal("0.697"),
        }
        return table.get(max(1, min(11, df)), Decimal("0.680"))
    return Decimal("1.000")


def weighted_quantile(
    values: list[Decimal],
    weights: list[Decimal],
    tau: Decimal,
) -> Decimal:
    """Calcula el cuantil ponderado empírico tau (entre 0 y 1).

    Ordena los valores y acumula el peso normalizado hasta alcanzar tau * sum(weights).
    """
    if not values:
        return ZERO
    paired = sorted(zip(values, weights), key=lambda x: x[0])
    total_w = sum((w for _, w in paired), ZERO)
    if total_w <= ZERO:
        return paired[len(paired) // 2][0]
    target_w = tau * total_w
    cum_w = ZERO
    for val, w in paired:
        cum_w += w
        if cum_w >= target_w:
            return val
    return paired[-1][0]


# ==============================================================================
# PUERTA DE CALIBRACIÓN INDIVIDUAL POR USUARIO
# ==============================================================================
# Umbrales estadísticos fundamentados para declarar una proyección probabilística:
# 1. MINIMO_CICLOS_EVALUABLES_CALIBRACION (N >= 6):
#    Cada ciclo evaluado es un ensayo de Bernoulli. Con N < 6, la resolución
#    discreta (1/N) es >= 20% a 33% y el error estándar SE = sqrt(p*(1-p)/N) supera 0.16.
#    Con N >= 6 (semestre de predicciones evaluadas), cada fallo representa <= 16.7%,
#    permitiendo que 5/6 = 83.3% valide empíricamente el nivel nominal del 80%.
# 2. UMBRAL_COBERTURA_MINIMA_80 (>= 0.80):
#    Regla dura contra sub-cobertura. Sub-cobertura es el error costoso en finanzas
#    personales (hace creer al usuario que tiene margen cuando no lo tiene).
# 3. UMBRAL_ANCHO_MAXIMO_RELATIVO_80 (<= 1.00x del gasto típico):
#    Un intervalo cuyo ancho supera el 100% del gasto habitual carece de nitidez (sharpness)
#    y no brinda información operativa útil para la toma de decisiones presupuestarias.
# ==============================================================================
MINIMO_CICLOS_EVALUABLES_CALIBRACION = 6
UMBRAL_COBERTURA_MINIMA_80 = Decimal("0.80")
UMBRAL_ANCHO_MAXIMO_RELATIVO_80 = Decimal("1.00")
NIVEL_EVALUADO_PUERTA = "80"


def evaluar_puerta_calibracion(
    ciclos_evaluados: int,
    cobertura_80: Decimal | None,
    ancho_medio_80_rel: Decimal | None,
    ancho_ciclo_actual_rel: Decimal | None = None,
) -> tuple[bool, str | None, str | None]:
    """Evalúa si la proyección probabilística de un usuario está demostradamente calibrada e informativa.

    Retorna:
        (pasa_puerta: bool, motivo: str | None, mensaje_explicativo: str | None)

    Motivos posibles de rechazo:
        - 'pocos_ciclos': Menos de 6 ciclos cerrados con proyección previa evaluada en backtest.
        - 'cobertura_insuficiente': Cobertura empírica observada al 80% inferior a 0.80 (sub-cobertura).
        - 'intervalo_no_informativo': Ancho medio relativo > 1.00x del gasto real o rango actual excesivo.
    """
    if ciclos_evaluados < MINIMO_CICLOS_EVALUABLES_CALIBRACION:
        mensaje = (
            f"Tu historial cuenta con {ciclos_evaluados} ciclos evaluables (se requieren al menos "
            f"{MINIMO_CICLOS_EVALUABLES_CALIBRACION} ciclos cerrados con proyección). Mostramos tus "
            f"compromisos ciertos (cuotas y suscripciones). La proyección probabilística se activará "
            f"cuando acumules suficiente historia para validar su calibración."
        )
        return False, "pocos_ciclos", mensaje

    if cobertura_80 is None or cobertura_80 < UMBRAL_COBERTURA_MINIMA_80:
        pct = round(float((cobertura_80 or ZERO) * Decimal("100")), 1)
        mensaje = (
            f"La proyección no superó la prueba de calibración sobre tu historial: cubrió el {pct}% "
            f"de tus ciclos pasados frente al 80% mínimo requerido. Mostramos solo tus compromisos "
            f"ciertos hasta que tu patrón de gasto se estabilice."
        )
        return False, "cobertura_insuficiente", mensaje

    if ancho_medio_80_rel is not None and ancho_medio_80_rel > UMBRAL_ANCHO_MAXIMO_RELATIVO_80:
        ancho_str = round(float(ancho_medio_80_rel), 1)
        mensaje = (
            f"La proyección no es suficientemente informativa: la dispersión de tus gastos genera "
            f"un rango demasiado amplio ({ancho_str} veces tu gasto real). Mostramos tus compromisos "
            f"ciertos para evitar darte una estimación engañosa."
        )
        return False, "intervalo_no_informativo", mensaje

    if ancho_ciclo_actual_rel is not None and ancho_ciclo_actual_rel > UMBRAL_ANCHO_MAXIMO_RELATIVO_80:
        mensaje = (
            "La proyección para este ciclo no es suficientemente informativa: el rango probable "
            "supera el 100% de tu gasto proyectado. Mostramos tus compromisos ciertos."
        )
        return False, "intervalo_no_informativo", mensaje

    return True, None, None

