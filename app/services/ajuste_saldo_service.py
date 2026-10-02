"""
Servicio oficial de ajustes de saldo ("Actualizar saldo") para billeteras.

Gestiona las operaciones de control de saldo en su propia tabla (ajustes_saldo),
ofreciendo vista previa con detección de anomalías (huecos, posibles duplicados,
salidas atípicas) y cálculo de métricas de cobertura histórica.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import logging
import statistics
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.transaccion import EstadoVerificacionTransaccion, MetodoPago, TipoTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.services.conciliacion_service import movimientos_por_dia
from app.services.rendimiento_billetera_service import (
    calcular_rendimiento_estimado,
    confirmar_rendimiento,
)
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)


# =============================================================================
# FUNCIONES PURAS (LÓGICA MATEMÁTICA Y ESTADÍSTICA SIN ACCESO A BASE DE DATOS)
# =============================================================================

def mediana_salidas_semanales(
    salidas_por_dia: dict[date, Decimal],
    primer_movimiento: Optional[date],
    hoy: date,
) -> tuple[Optional[Decimal], int]:
    """
    Calcula la mediana de las salidas semanales de las últimas 8 semanas (k=1..8).
    Cada semana k comprende 7 días: [hoy − 7k, hoy − 7k + 6].
    Cuentan únicamente las semanas cuyo inicio es >= primer_movimiento.
    Retorna: (mediana, semanas_con_historia). Con 0 semanas retorna (None, 0).
    """
    if primer_movimiento is None:
        return None, 0

    totales_semanas: list[Decimal] = []

    for k in range(1, 9):
        inicio_semana = hoy - timedelta(days=7 * k)

        if inicio_semana >= primer_movimiento:
            total_sem = sum(
                salidas_por_dia.get(inicio_semana + timedelta(days=i), Decimal("0.00"))
                for i in range(7)
            )
            totales_semanas.append(Decimal(str(total_sem)))

    if not totales_semanas:
        return None, 0

    mediana_val = Decimal(str(statistics.median(totales_semanas)))
    return mediana_val, len(totales_semanas)


def detectar_huecos(
    dias_con_movimiento: list[date],
    desde: date,
    hoy: date,
) -> list[tuple[date, date]]:
    """
    Detecta períodos atípicos de inactividad (huecos sin movimiento).

    - intervalo típico = mediana de las distancias entre días consecutivos con movimiento
      (calculada sobre al menos 3 días con movimiento; si no, None).
    - umbral = max(3, 2 × intervalo típico), o 3 si es None.
    - hueco = días sin movimiento estrictamente entre dos días con movimiento dentro de [desde, hoy],
      o desde el último día con movimiento + 1 hasta hoy, con largo >= umbral.
    - si no hay movimientos en la ventana [desde, hoy], retorna un hueco [(desde, hoy)].
    - retorna los 3 más largos ordenados cronológicamente por fecha de inicio.
    """
    dias_sorted = sorted(list(set(dias_con_movimiento)))

    # Cálculo de intervalo típico sobre los días provistos
    if len(dias_sorted) >= 3:
        distancias = [(dias_sorted[i + 1] - dias_sorted[i]).days for i in range(len(dias_sorted) - 1)]
        intervalo_tipico = statistics.median(distancias) if distancias else None
    else:
        intervalo_tipico = None

    if intervalo_tipico is None:
        umbral = 3
    else:
        umbral = max(3, int(2 * intervalo_tipico))

    # Movimientos estrictamente dentro de la ventana de análisis
    dias_en_ventana = [d for d in dias_sorted if desde <= d <= hoy]

    if not dias_en_ventana:
        if (hoy - desde).days >= 0:
            return [(desde, hoy)]
        return []

    candidatos: list[tuple[date, date]] = []

    # Huecos entre días consecutivos en ventana
    for i in range(len(dias_en_ventana) - 1):
        d_curr = dias_en_ventana[i]
        d_next = dias_en_ventana[i + 1]
        inicio_hueco = d_curr + timedelta(days=1)
        fin_hueco = d_next - timedelta(days=1)
        largo = (fin_hueco - inicio_hueco).days + 1
        if largo >= umbral:
            candidatos.append((inicio_hueco, fin_hueco))

    # Hueco desde el último día con movimiento hasta hoy
    ultimo_dia = dias_en_ventana[-1]
    if ultimo_dia < hoy:
        inicio_hueco = ultimo_dia + timedelta(days=1)
        fin_hueco = hoy
        largo = (fin_hueco - inicio_hueco).days + 1
        if largo >= umbral:
            candidatos.append((inicio_hueco, fin_hueco))

    # Conservar los 3 más largos y ordenarlos por fecha de inicio
    top3 = sorted(candidatos, key=lambda g: (g[1] - g[0]).days, reverse=True)[:3]
    return sorted(top3, key=lambda g: g[0])


def calcular_cobertura_desde(
    controles: list[tuple[date, Decimal]],
    salidas_por_dia: dict[date, Decimal],
    hoy: date,
) -> dict:
    """
    Calcula la métrica de cobertura de gastos registrados según el historial de controles de saldo.
    Retorna un diccionario: {mostrar, por_cada_100, desde, hasta}.

    - controles: lista ordenada de tuplas (fecha, monto).
    - mostrar=False si hay menos de 2 controles o si entre el primero y el último hay menos de 30 días.
    - cargado = salidas con fecha en (primero, último].
    - faltante = suma de max(0, −monto) de los controles posteriores al primero.
    - si cargado + faltante == 0, mostrar=False.
    - si no, por_cada_100 = 100 × cargado / (cargado + faltante), redondeado al entero (ROUND_HALF_UP).
    - desde y hasta corresponden al primer y último control.
    """
    if len(controles) < 2:
        return {"mostrar": False, "por_cada_100": None, "desde": None, "hasta": None}

    fecha_primero = controles[0][0]
    fecha_ultimo = controles[-1][0]

    if (fecha_ultimo - fecha_primero).days < 30:
        return {"mostrar": False, "por_cada_100": None, "desde": None, "hasta": None}

    # Salidas registradas estrictamente en el intervalo (fecha_primero, fecha_ultimo]
    cargado = sum(
        monto
        for f, monto in salidas_por_dia.items()
        if fecha_primero < f <= fecha_ultimo
    )

    # Faltante: diferencias negativas detectadas en controles posteriores al inicial
    faltante = sum(
        max(Decimal("0.00"), -monto)
        for _, monto in controles[1:]
    )

    total_base = cargado + faltante
    if total_base == Decimal("0.00"):
        return {"mostrar": False, "por_cada_100": None, "desde": None, "hasta": None}

    ratio = (Decimal("100") * cargado / total_base).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    por_cada_100 = int(ratio)

    return {
        "mostrar": True,
        "por_cada_100": por_cada_100,
        "desde": fecha_primero,
        "hasta": fecha_ultimo,
    }


# =============================================================================
# OPERACIONES DE CONSULTA Y ESCRITURA EN BASE DE DATOS
# =============================================================================

def previsualizar_control(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID | str,
    saldo_declarado: Decimal,
    hoy: Optional[date] = None,
) -> dict:
    """
    Genera la vista previa de un control de saldo antes de su confirmación (solo lectura).
    Evalúa diferencia, rendimiento sugerido, huecos de registro y posibles duplicados.
    """
    billetera = db.get(Billetera, billetera_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    if hoy is None:
        hoy = hoy_argentina()

    saldo_registrado = billetera.saldo_actual
    diferencia = saldo_declarado - saldo_registrado

    # 1. Rendimiento propuesto
    rendimiento_propuesto: Optional[Decimal] = None
    if diferencia > Decimal("0.00"):
        resp_rend = calcular_rendimiento_estimado(db, usuario_id, billetera.id)
        if resp_rend.rendimiento_estimado is not None and resp_rend.rendimiento_estimado > Decimal("0.00"):
            rendimiento_propuesto = min(resp_rend.rendimiento_estimado, diferencia)

    # 2. Historial de fechas de movimientos de la billetera
    fechas_movimientos: set[date] = set()

    # Transacciones confirmadas no crédito
    tx_fechas = db.execute(
        select(Transaccion.fecha).where(
            Transaccion.billetera_id == billetera.id,
            (Transaccion.metodo_pago != MetodoPago.CREDITO) | (Transaccion.metodo_pago.is_(None)),
            Transaccion.es_padre_cuotas.is_(False),
            Transaccion.es_cuota_hija.is_(False),
            (Transaccion.estado_verificacion.is_(None)) | (Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE),
        )
    ).scalars().all()
    fechas_movimientos.update(tx_fechas)

    # Transferencias
    tr_fechas = db.execute(
        select(TransferenciaInterna.fecha).where(
            (TransferenciaInterna.billetera_origen_id == billetera.id) |
            (TransferenciaInterna.billetera_destino_id == billetera.id)
        )
    ).scalars().all()
    fechas_movimientos.update(tr_fechas)

    # Rendimientos
    rend_fechas = db.execute(
        select(func.date(RendimientoBilletera.fecha)).where(
            RendimientoBilletera.billetera_id == billetera.id
        )
    ).scalars().all()
    for rf in rend_fechas:
        if rf is not None:
            d_obj = rf if isinstance(rf, date) else date.fromisoformat(str(rf))
            fechas_movimientos.add(d_obj)

    primer_movimiento = min(fechas_movimientos) if fechas_movimientos else None
    ultimo_movimiento = max(fechas_movimientos) if fechas_movimientos else None

    # 3. Salida semanal típica y semanas con historia
    desde_analisis = hoy - timedelta(days=60)
    movs = movimientos_por_dia(db, billetera.id, desde=desde_analisis, hasta=hoy)
    salidas_por_dia = {d: v["salidas"] for d, v in movs.items()}
    salida_semanal_tipica, semanas_con_historia = mediana_salidas_semanales(
        salidas_por_dia, primer_movimiento, hoy
    )

    # 4. Clasificación de diferencia grande
    if diferencia == Decimal("0.00"):
        es_grande = False
    elif semanas_con_historia < 2:
        es_grande = True
    else:
        assert salida_semanal_tipica is not None
        es_grande = abs(diferencia) > salida_semanal_tipica

    # 5. Detección de huecos
    # previsualizar_control usa desde = max(hoy − 30, fecha del último control, primer movimiento)
    # y distancias de los últimos 90 días.
    ultimo_control_fecha = db.execute(
        select(AjusteSaldo.fecha).where(
            AjusteSaldo.billetera_id == billetera.id
        ).order_by(AjusteSaldo.fecha.desc(), AjusteSaldo.fecha_creacion.desc()).limit(1)
    ).scalar_one_or_none()

    candidatos_desde = [hoy - timedelta(days=30)]
    if ultimo_control_fecha is not None:
        candidatos_desde.append(ultimo_control_fecha)
    if primer_movimiento is not None:
        candidatos_desde.append(primer_movimiento)
    desde_huecos = max(candidatos_desde)

    dias_ultimos_90 = [d for d in fechas_movimientos if d >= hoy - timedelta(days=90)]
    huecos_tuplas = detectar_huecos(dias_ultimos_90, desde_huecos, hoy)
    huecos = [
        {"desde": h_ini, "hasta": h_fin, "dias": (h_fin - h_ini).days + 1}
        for h_ini, h_fin in huecos_tuplas
    ]

    # 6. Posibles duplicados (solo si diferencia > 0)
    posibles_duplicados = []
    if diferencia > Decimal("0.00"):
        posibles_duplicados = _buscar_posibles_duplicados(db, billetera.id, hoy)

    return {
        "billetera_id": billetera.id,
        "moneda": billetera.moneda,
        "saldo_registrado": saldo_registrado,
        "saldo_declarado": saldo_declarado,
        "diferencia": diferencia,
        "rendimiento_propuesto": rendimiento_propuesto,
        "es_grande": es_grande,
        "salida_semanal_tipica": salida_semanal_tipica,
        "semanas_con_historia": semanas_con_historia,
        "ultimo_movimiento": ultimo_movimiento,
        "huecos": huecos,
        "posibles_duplicados": posibles_duplicados,
    }


def _buscar_posibles_duplicados(
    db: Session,
    billetera_id: UUID,
    hoy: date,
) -> list[dict]:
    """
    Busca grupos de egresos con idéntica fecha y monto en los últimos 30 días con cantidad >= 2.
    """
    desde = hoy - timedelta(days=30)
    stmt = (
        select(
            Transaccion.fecha,
            Transaccion.monto,
            func.count(Transaccion.id).label("cantidad"),
        )
        .where(
            Transaccion.billetera_id == billetera_id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            (Transaccion.metodo_pago != MetodoPago.CREDITO) | (Transaccion.metodo_pago.is_(None)),
            Transaccion.es_padre_cuotas.is_(False),
            Transaccion.es_cuota_hija.is_(False),
            (Transaccion.estado_verificacion.is_(None)) | (Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE),
            Transaccion.fecha >= desde,
            Transaccion.fecha <= hoy,
        )
        .group_by(Transaccion.fecha, Transaccion.monto)
        .having(func.count(Transaccion.id) >= 2)
        .order_by(func.count(Transaccion.id).desc(), Transaccion.fecha.desc())
        .limit(3)
    )

    grupos = db.execute(stmt).all()
    duplicados = []
    for g_fecha, g_monto, g_cant in grupos:
        stmt_desc = (
            select(Transaccion.descripcion)
            .where(
                Transaccion.billetera_id == billetera_id,
                Transaccion.tipo == TipoTransaccion.EGRESO,
                (Transaccion.metodo_pago != MetodoPago.CREDITO) | (Transaccion.metodo_pago.is_(None)),
                Transaccion.es_padre_cuotas.is_(False),
                Transaccion.es_cuota_hija.is_(False),
                (Transaccion.estado_verificacion.is_(None)) | (Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE),
                Transaccion.fecha == g_fecha,
                Transaccion.monto == g_monto,
            )
            .order_by(Transaccion.fecha_creacion.asc())
            .limit(2)
        )
        descs = [r[0] for r in db.execute(stmt_desc).fetchall()]
        duplicados.append({
            "fecha": g_fecha,
            "monto": g_monto,
            "cantidad": g_cant,
            "descripciones": descs,
        })
    return duplicados


def registrar_control(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID | str,
    saldo_declarado: Decimal,
    rendimiento: Optional[Decimal] = None,
    commit: bool = True,
) -> AjusteSaldo:
    """
    Registra el control de saldo de una billetera:
    - Lee la billetera bloqueando fila (with_for_update).
    - Valida que pertenezca al usuario y no esté archivada.
    - Si se incluye rendimiento, lo valida y confirma.
    - Crea la fila en ajustes_saldo con la diferencia resultante y actualiza saldo_actual.
    - Si commit=True hace commit único; con commit=False realiza flush.
    """
    billetera = db.execute(
        select(Billetera)
        .where(Billetera.id == billetera_id, Billetera.usuario_id == usuario_id)
        .with_for_update()
    ).scalar_one_or_none()

    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    if billetera.estado != EstadoBilletera.ACTIVA:
        raise HTTPException(
            status_code=400,
            detail="No se puede actualizar el saldo de una billetera archivada."
        )

    # Procesar rendimiento opcional antes de calcular la fila de ajuste
    if rendimiento is not None:
        diferencia_previa = saldo_declarado - billetera.saldo_actual
        if rendimiento > diferencia_previa:
            raise HTTPException(
                status_code=400,
                detail="El rendimiento no puede ser mayor que la diferencia."
            )
        confirmar_rendimiento(db, usuario_id, billetera.id, monto=rendimiento, commit=False)

    ahora_dt = datetime.now(timezone.utc)
    saldo_anterior = billetera.saldo_actual
    monto_ajuste = saldo_declarado - saldo_anterior

    ajuste = AjusteSaldo(
        billetera_id=billetera.id,
        monto=monto_ajuste,
        saldo_anterior=saldo_anterior,
        saldo_declarado=saldo_declarado,
        fecha=hoy_argentina(),
        fecha_creacion=ahora_dt,
    )
    db.add(ajuste)

    billetera.saldo_actual = saldo_declarado
    if billetera.fecha_ultimo_rendimiento is not None:
        billetera.fecha_ultimo_rendimiento = ahora_dt

    if commit:
        db.commit()
        db.refresh(ajuste)
    else:
        db.flush()

    return ajuste


def eliminar_ajuste(
    db: Session,
    usuario_id: UUID,
    ajuste_id: UUID | str,
    commit: bool = True,
) -> dict:
    """
    Revierte un ajuste de saldo:
    - Valida que el ajuste pertenezca a una billetera del usuario.
    - Bloquea la billetera con with_for_update, descuenta el monto del saldo_actual y elimina la fila.
    - Mantiene el esquema de atomicidad transaccional con commit/flush.
    """
    ajuste = db.get(AjusteSaldo, ajuste_id)
    if not ajuste:
        raise HTTPException(status_code=404, detail="No encontramos ese ajuste de saldo.")

    billetera = db.execute(
        select(Billetera)
        .where(Billetera.id == ajuste.billetera_id, Billetera.usuario_id == usuario_id)
        .with_for_update()
    ).scalar_one_or_none()

    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos ese ajuste de saldo.")

    billetera.saldo_actual -= ajuste.monto
    db.delete(ajuste)

    if commit:
        db.commit()
    else:
        db.flush()

    return {"detail": "Ajuste de saldo eliminado exitosamente"}


def listar_ajustes(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID | str,
) -> list[AjusteSaldo]:
    """
    Retorna la lista de ajustes de una billetera, ordenados por fecha y fecha_creacion descendentes.
    """
    billetera = db.execute(
        select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    return list(
        db.execute(
            select(AjusteSaldo)
            .where(AjusteSaldo.billetera_id == billetera.id)
            .order_by(AjusteSaldo.fecha.desc(), AjusteSaldo.fecha_creacion.desc())
        ).scalars().all()
    )


def calcular_cobertura(
    db: Session,
    billetera_id: UUID | str,
    hoy: Optional[date] = None,
) -> dict:
    """
    Carga los controles de los últimos 180 días y sus salidas por día para calcular la cobertura histórica.
    """
    if hoy is None:
        hoy = hoy_argentina()

    desde_180 = hoy - timedelta(days=180)

    controles_res = db.execute(
        select(AjusteSaldo.fecha, AjusteSaldo.monto)
        .where(
            AjusteSaldo.billetera_id == billetera_id,
            AjusteSaldo.fecha >= desde_180,
            AjusteSaldo.fecha <= hoy,
        )
        .order_by(AjusteSaldo.fecha.asc(), AjusteSaldo.fecha_creacion.asc())
    ).fetchall()
    controles = [(r[0], r[1]) for r in controles_res]

    movs = movimientos_por_dia(db, billetera_id, desde=desde_180, hasta=hoy)
    salidas_por_dia = {d: v["salidas"] for d, v in movs.items()}

    return calcular_cobertura_desde(controles, salidas_por_dia, hoy)
