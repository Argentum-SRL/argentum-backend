"""
Módulo Canónico de Compromisos Mensuales de Argentum.
Ubicación: app/services/compromisos_service.py

PROPÓSITO Y REGLA DE NEGOCIO (Fase 2c):
Crea un cálculo único y determinístico de "compromisos mensuales" sin doble cómputo,
agrupando tres componentes independientes por moneda:
  a) Cuotas: próxima cuota impaga de compras financiadas en múltiples cuotas (> 1).
     Se excluyen compras en 1 pago y cuotas que corresponden a cobros de suscripciones.
  b) Suscripciones: equivalente mensual al precio vigente de cada suscripción activa.
  c) Gastos fijos detectados: último monto convertido a equivalente mensual de los streams
     que el clasificador marca como COMPROMISO y MADURO (y señal distinta de DECLARADO).

Definiciones derivadas:
- Deudas y suscripciones = a + b.
- Compromisos mensuales totales = a + b + c.
- Gasto variable típico: promedio del gasto devengado en los ciclos con historia
  (hasta 3 ciclos completos) según definiciones_service, deduciendo cuotas de compras
  en varias cuotas, cobros de suscripciones y transacciones de los gastos fijos (c).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional, Set
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.utils.cuotas import monto_efectivo_cuota
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import (
    ContextoDefiniciones,
    condicion_gasto,
)
from app.services.suscripcion_service import calcular_costo_mensual
from app.utils.fecha import hoy_argentina

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class ItemCompromisoCuota:
    grupo_id: UUID
    descripcion: str
    numero_cuota: int
    cantidad_cuotas: int
    fecha_vencimiento: date
    monto: Decimal
    moneda: str


@dataclass(frozen=True)
class ItemCompromisoSuscripcion:
    suscripcion_id: UUID
    nombre: str
    frecuencia: str
    monto_original: Decimal
    monto_mensual_equivalente: Decimal
    moneda: str
    vigente_desde: Optional[date]


@dataclass(frozen=True)
class ItemCompromisoFijo:
    descripcion: str
    frecuencia: str
    ultimo_monto: Decimal
    monto_mensual_equivalente: Decimal
    moneda: str


@dataclass(frozen=True)
class ResultadoCompromisos:
    cuotas: Decimal
    suscripciones: Decimal
    fijos: Decimal
    total: Decimal
    deudas_y_suscripciones: Decimal
    moneda: str
    detalle_cuotas: list[ItemCompromisoCuota] = field(default_factory=list)
    detalle_suscripciones: list[ItemCompromisoSuscripcion] = field(default_factory=list)
    detalle_fijos: list[ItemCompromisoFijo] = field(default_factory=list)


def normalizar_moneda(m: Any) -> str:
    if hasattr(m, "value"):
        return str(m.value).upper()
    return str(m).upper()


def convertir_frecuencia_stream_a_mensual(frecuencia: str, monto: Decimal) -> Decimal:
    """Convierte el último monto de un stream a su equivalente mensual."""
    frec = (frecuencia or "").lower()
    if frec == "mensual":
        return monto
    elif frec == "quincenal":
        return monto * Decimal("2")
    elif frec == "semanal":
        return (monto * Decimal("52") / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif frec in ("bimensual", "bimestral"):
        return (monto / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif frec == "anual":
        return (monto / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return monto


def obtener_precio_vigente_memoria(
    suscripcion_id: UUID,
    historial_subs: list[Any],
    fecha_referencia: date,
    moneda_str: str,
) -> Optional[Any]:
    """Obtiene el último precio vigente (vigente_desde <= fecha_referencia) para una suscripción y moneda."""
    precios = [
        h for h in historial_subs
        if getattr(h, "suscripcion_id", None) == suscripcion_id
        and normalizar_moneda(getattr(h, "moneda", "")) == moneda_str
        and getattr(h, "vigente_desde", date.max) <= fecha_referencia
    ]
    if not precios:
        return None
    precios.sort(
        key=lambda x: (
            getattr(x, "vigente_desde", date.min),
            getattr(x, "fecha_creacion", date.min),
        ),
        reverse=True,
    )
    return precios[0]


def calcular_compromisos_memoria(
    cuotas_con_grupo: list[Any],
    suscripciones: list[Any],
    historial_subs: list[Any],
    streams: list[Any],
    hoy: date,
    moneda: Moneda | str = Moneda.ARS,
) -> ResultadoCompromisos:
    """
    Función pura que calcula los compromisos mensuales a partir de estructuras en memoria.
    No realiza consultas I/O ni llamadas a bases de datos ni servicios externos.
    """
    moneda_str = normalizar_moneda(moneda)

    # --------------------------------------------------------------------------
    # PARTE A: Cuotas de compras financiadas (> 1 cuota)
    # --------------------------------------------------------------------------
    # Agrupar cuotas impagas por grupo_cuotas
    cuotas_por_grupo: dict[UUID, list[Any]] = {}
    info_grupo: dict[UUID, Any] = {}

    for item in cuotas_con_grupo:
        if hasattr(item, "__getitem__") and not isinstance(item, (str, bytes, dict)):
            try:
                cuota, grupo = item[0], item[1]
            except (IndexError, TypeError):
                # Se ignora: si el ítem no tiene formato de par (cuota, grupo), se omite.
                continue
        elif hasattr(item, "grupo"):
            cuota, grupo = item, getattr(item, "grupo")
        else:
            continue

        if not grupo:
            continue

        g_id = getattr(grupo, "id", None)
        if not g_id:
            continue

        info_grupo[g_id] = grupo

        if not getattr(cuota, "pagada", False):
            cuotas_por_grupo.setdefault(g_id, []).append(cuota)

    detalle_cuotas: list[ItemCompromisoCuota] = []
    total_cuotas = Decimal("0")

    for g_id, c_lista in cuotas_por_grupo.items():
        grupo = info_grupo[g_id]

        # Regla: solo compras en varias cuotas (cantidad_cuotas > 1)
        cant_cuotas = getattr(grupo, "cantidad_cuotas", 1) or 1
        if cant_cuotas <= 1:
            continue

        # Regla: misma moneda
        g_moneda = normalizar_moneda(getattr(grupo, "moneda", ""))
        if g_moneda != moneda_str:
            continue

        # Regla: excluir grupos asociados a cobros de suscripción
        tp = getattr(grupo, "transaccion_padre", None)
        if tp is not None and getattr(tp, "suscripcion_id", None) is not None:
            continue

        # Seleccionar la próxima cuota impaga (menor numero_cuota o menor fecha_vencimiento)
        c_lista.sort(
            key=lambda c: (
                getattr(c, "numero_cuota", 999999),
                getattr(c, "fecha_vencimiento", date.max),
            )
        )
        prox_cuota = c_lista[0]
        monto_val = monto_efectivo_cuota(prox_cuota)
        monto_dec = Decimal(str(monto_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_cuotas += monto_dec

        detalle_cuotas.append(
            ItemCompromisoCuota(
                grupo_id=g_id,
                descripcion=getattr(grupo, "descripcion", ""),
                numero_cuota=getattr(prox_cuota, "numero_cuota", 1),
                cantidad_cuotas=cant_cuotas,
                fecha_vencimiento=getattr(prox_cuota, "fecha_vencimiento", hoy),
                monto=monto_dec,
                moneda=moneda_str,
            )
        )

    # --------------------------------------------------------------------------
    # PARTE B: Suscripciones activas al precio vigente
    # --------------------------------------------------------------------------
    detalle_subs: list[ItemCompromisoSuscripcion] = []
    total_subs = Decimal("0")

    for s in suscripciones:
        # Regla: suscripción activa
        estado = getattr(s, "estado", None)
        if hasattr(estado, "value"):
            estado = estado.value
        if str(estado).lower() != "activa":
            continue

        s_id = getattr(s, "id", None)
        # Buscar precio vigente en historial
        historial = getattr(s, "historial", None) or getattr(s, "historial_precios", None) or historial_subs
        pv = obtener_precio_vigente_memoria(s_id, historial, hoy, moneda_str)
        if not pv:
            continue

        frec_val = getattr(s, "frecuencia", "mensual")
        if hasattr(frec_val, "value"):
            frec_val = frec_val.value
        frec_str = str(frec_val).lower()

        monto_orig = Decimal(str(getattr(pv, "monto", ZERO)))
        equiv = calcular_costo_mensual(frec_str, monto_orig)
        total_subs += equiv

        detalle_subs.append(
            ItemCompromisoSuscripcion(
                suscripcion_id=s_id,
                nombre=getattr(s, "nombre", ""),
                frecuencia=frec_str,
                monto_original=monto_orig,
                monto_mensual_equivalente=equiv,
                moneda=moneda_str,
                vigente_desde=getattr(pv, "vigente_desde", None),
            )
        )

    # --------------------------------------------------------------------------
    # PARTE C: Gastos fijos detectados (Streams COMPROMISO y MADURO no declarados)
    # --------------------------------------------------------------------------
    detalle_fijos: list[ItemCompromisoFijo] = []
    total_fijos = Decimal("0")

    for st in streams:
        clase = getattr(st, "clase", "")
        estado = getattr(st, "estado", "")
        senal = getattr(st, "senal", "")
        st_moneda = normalizar_moneda(getattr(st, "moneda", ""))

        if (
            clase == "COMPROMISO"
            and estado == "MADURO"
            and senal != "DECLARADO"
            and st_moneda == moneda_str
        ):
            frec_val = getattr(st, "frecuencia", "mensual")
            if hasattr(frec_val, "value"):
                frec_val = frec_val.value
            frec_str = str(frec_val).lower()

            ult_monto = Decimal(str(getattr(st, "ultimo_monto", ZERO)))
            equiv = convertir_frecuencia_stream_a_mensual(frec_str, ult_monto)
            total_fijos += equiv

            detalle_fijos.append(
                ItemCompromisoFijo(
                    descripcion=getattr(st, "descripcion", ""),
                    frecuencia=frec_str,
                    ultimo_monto=ult_monto,
                    monto_mensual_equivalente=equiv,
                    moneda=moneda_str,
                )
            )

    total_cuotas = total_cuotas.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_subs = total_subs.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_fijos = total_fijos.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (total_cuotas + total_subs + total_fijos).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    deudas_y_subs = (total_cuotas + total_subs).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return ResultadoCompromisos(
        cuotas=total_cuotas,
        suscripciones=total_subs,
        fijos=total_fijos,
        total=total,
        deudas_y_suscripciones=deudas_y_subs,
        moneda=moneda_str,
        detalle_cuotas=detalle_cuotas,
        detalle_suscripciones=detalle_subs,
        detalle_fijos=detalle_fijos,
    )


def calcular_compromisos_mensuales(
    db: Session,
    usuario: Usuario,
    hoy: Optional[date] = None,
    moneda: Moneda | str = Moneda.ARS,
    clasificacion: Optional[Any] = None,
    ctx: Optional[ContextoDefiniciones] = None,
) -> ResultadoCompromisos:
    """
    Función de orquestación que carga datos desde la base de datos (con transacciones padre cargadas)
    y ejecuta el cálculo puro de compromisos mensuales.
    """
    ref_hoy = hoy or hoy_argentina()
    moneda_str = normalizar_moneda(moneda)

    # 1. Cuotas con grupo y transacción padre
    cuotas_con_grupo = db.execute(
        select(Cuota, GrupoCuotas)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .options(joinedload(GrupoCuotas.transaccion_padre))
        .where(GrupoCuotas.usuario_id == usuario.id)
    ).all()

    # 2. Suscripciones activas
    suscripciones = db.execute(
        select(Suscripcion)
        .where(
            Suscripcion.usuario_id == usuario.id,
            Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        )
    ).scalars().all()

    # 3. Historial de suscripciones
    historial_subs = db.execute(
        select(HistorialSuscripcion)
        .join(Suscripcion, HistorialSuscripcion.suscripcion_id == Suscripcion.id)
        .where(Suscripcion.usuario_id == usuario.id)
    ).scalars().all()

    # 4. Streams clasificados
    if clasificacion is not None:
        streams = getattr(clasificacion, "streams", [])
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
        streams = res_clasif.streams

    return calcular_compromisos_memoria(
        cuotas_con_grupo=cuotas_con_grupo,
        suscripciones=suscripciones,
        historial_subs=historial_subs,
        streams=streams,
        hoy=ref_hoy,
        moneda=moneda_str,
    )


def gastos_variables_por_ciclo(
    db: Session,
    usuario: Usuario,
    hoy: Optional[date] = None,
    moneda: Moneda | str = Moneda.ARS,
    max_ciclos: int = 12,
    streams_c: Optional[list[Any]] = None,
) -> list[Decimal]:
    """
    Calcula para cada uno de los últimos max_ciclos ciclos cerrados con historia
    el total gastado en transacciones variables (excluyendo cuotas > 1, suscripciones,
    y movimientos de streams_c), con el mismo criterio que calcular_gasto_variable_tipico.
    Devuelve la lista de montos por ciclo ordenados del más reciente al más antiguo.
    """
    ref_hoy = hoy or hoy_argentina()
    moneda_enum = Moneda[normalizar_moneda(moneda)]

    c_ini_curr, c_fin_curr = get_ciclo_fechas(usuario, ref_hoy)
    fecha_fin_c1 = c_ini_curr - timedelta(days=1)

    primera_tx = db.execute(
        select(Transaccion.fecha)
        .where(Transaccion.usuario_id == usuario.id)
        .order_by(Transaccion.fecha.asc())
        .limit(1)
    ).scalar_one_or_none()

    if primera_tx is None:
        primera_tx = usuario.fecha_registro.date()

    if primera_tx > fecha_fin_c1:
        return []

    ciclos_cerrados: list[tuple[date, date]] = []
    p = fecha_fin_c1
    while p >= primera_tx and len(ciclos_cerrados) < max_ciclos:
        inicio, fin = get_ciclo_fechas(usuario, p)
        if fin <= fecha_fin_c1:
            ciclos_cerrados.append((inicio, fin))
            p = inicio - timedelta(days=1)
        else:
            break

    if not ciclos_cerrados:
        return []

    grupos_varias_cuotas = set(
        db.execute(
            select(GrupoCuotas.id).where(
                GrupoCuotas.usuario_id == usuario.id,
                GrupoCuotas.cantidad_cuotas > 1,
            )
        ).scalars().all()
    )

    if streams_c is None:
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
        if normalizar_moneda(getattr(s, "moneda", "")) == normalizar_moneda(moneda):
            tx_ids_streams_c.update(getattr(s, "transacciones_ids", []))

    start_range = ciclos_cerrados[-1][0]
    end_range = ciclos_cerrados[0][1]

    cond_gas = condicion_gasto(usuario.id, start_range, end_range, hoy=ref_hoy)
    txs_gastos = db.execute(
        select(Transaccion).where(cond_gas, Transaccion.moneda == moneda_enum)
    ).scalars().all()

    gastos_por_ciclo: list[Decimal] = []
    for c_ini, c_fin in ciclos_cerrados:
        suma_c = Decimal("0")
        for t in txs_gastos:
            if c_ini <= t.fecha <= c_fin:
                if t.es_cuota_hija and t.grupo_cuotas_id in grupos_varias_cuotas:
                    continue
                if t.suscripcion_id is not None:
                    continue
                if t.id in tx_ids_streams_c:
                    continue
                suma_c += Decimal(str(t.monto))
        gastos_por_ciclo.append(suma_c.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return gastos_por_ciclo


def calcular_gasto_variable_tipico(
    db: Session,
    usuario: Usuario,
    hoy: Optional[date] = None,
    moneda: Moneda | str = Moneda.ARS,
    ctx: Optional[ContextoDefiniciones] = None,
    streams_c: Optional[list[Any]] = None,
) -> Decimal:
    """
    Calcula el gasto variable típico según las especificaciones de la Fase 2c:
    Promedio, en los ciclos con historia usados por obtener_contexto_financiero (hasta 3 ciclos completos),
    del gasto de cada ciclo devengado según definiciones_service, menos:
      - cuotas de compras en varias cuotas (> 1);
      - cobros de suscripciones;
      - movimientos de los streams clasificados como compromisos fijos maduros (c).
    """
    ref_hoy = hoy or hoy_argentina()
    moneda_enum = Moneda[normalizar_moneda(moneda)]

    gastos_ciclos = gastos_variables_por_ciclo(
        db, usuario, hoy=ref_hoy, moneda=moneda, max_ciclos=3, streams_c=streams_c
    )
    if gastos_ciclos:
        suma = sum(gastos_ciclos)
        return (suma / Decimal(str(len(gastos_ciclos)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Si no hay ciclos con historia cerrados, se usa el ciclo actual parcial
    c_ini_curr, _ = get_ciclo_fechas(usuario, ref_hoy)
    start_range = c_ini_curr
    end_range = ref_hoy

    cond_gas = condicion_gasto(usuario.id, start_range, end_range, hoy=ref_hoy)
    txs_gastos = db.execute(
        select(Transaccion).where(cond_gas, Transaccion.moneda == moneda_enum)
    ).scalars().all()

    grupos_varias_cuotas = set(
        db.execute(
            select(GrupoCuotas.id).where(
                GrupoCuotas.usuario_id == usuario.id,
                GrupoCuotas.cantidad_cuotas > 1,
            )
        ).scalars().all()
    )

    if streams_c is None:
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
        if normalizar_moneda(getattr(s, "moneda", "")) == normalizar_moneda(moneda):
            tx_ids_streams_c.update(getattr(s, "transacciones_ids", []))

    suma_variables = Decimal("0")
    for t in txs_gastos:
        if t.es_cuota_hija and t.grupo_cuotas_id in grupos_varias_cuotas:
            continue
        if t.suscripcion_id is not None:
            continue
        if t.id in tx_ids_streams_c:
            continue

        suma_variables += Decimal(str(t.monto))

    return suma_variables.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
