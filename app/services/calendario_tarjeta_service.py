"""
Módulo calendario_tarjeta_service.
Único lugar centralizado para el cálculo de fechas de cierre y vencimiento de tarjetas de crédito.
Soporta reglas fijas ("dia_fijo"), reglas por día de la semana ("dia_semana") y excepciones
puntuales almacenadas en la tabla fechas_resumen_tarjeta.
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, timedelta
from typing import TYPE_CHECKING
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import object_session

from app.models.fecha_resumen_tarjeta import FechaResumenTarjeta
from app.services.dias_habiles_service import ajustar_fecha_habil_sync
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.tarjeta_credito import TarjetaCredito

ORDINALES_ES = {
    1: "primer",
    2: "segundo",
    3: "tercer",
    4: "cuarto",
    -1: "último",
}

DIAS_SEMANA_ES = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
    6: "domingo",
}


def deducir_regla_tarjeta(
    proximo_cierre: date,
    proximo_vencimiento: date,
    ultimo_cierre: date | None = None,
) -> dict:
    """
    Deduce la regla de cierre y vencimiento para una tarjeta (Decisión 4):
    a. Si viene ultimo_cierre con el mismo día del mes que proximo_cierre:
       "dia_fijo" con dia_cierre = ese día y dia_vencimiento = proximo_vencimiento.day.
    b. Si no, y los dos son el mismo N° día de la semana (N de 1 a 4):
       "dia_semana" con ese N y ese día. Si no coinciden en N pero los dos son el último
       de ese día en su mes: semana_cierre -1.
       En ambos casos dias_hasta_vencimiento = (proximo_vencimiento - proximo_cierre).days.
    c. Sin ultimo_cierre, o si no se cumple a ni b:
       "dia_fijo" con los días de proximo_cierre y proximo_vencimiento.
    Valida que proximo_vencimiento > proximo_cierre y <= 31 días.
    """
    if proximo_vencimiento <= proximo_cierre or (proximo_vencimiento - proximo_cierre).days > 31:
        raise HTTPException(
            status_code=400,
            detail="El vencimiento tiene que ser posterior al cierre y dentro del mes siguiente.",
        )

    # a. Mismo día del mes
    if ultimo_cierre is not None and ultimo_cierre.day == proximo_cierre.day:
        return {
            "regla_cierre": "dia_fijo",
            "dia_cierre": proximo_cierre.day,
            "dia_vencimiento": proximo_vencimiento.day,
            "semana_cierre": None,
            "dia_semana_cierre": None,
            "dias_hasta_vencimiento": None,
        }

    # b. Mismo día de la semana y mismo ordinal N o ambos últimos
    if ultimo_cierre is not None and ultimo_cierre.weekday() == proximo_cierre.weekday():
        dia_sem = proximo_cierre.weekday()
        n_ult = (ultimo_cierre.day - 1) // 7 + 1
        n_prox = (proximo_cierre.day - 1) // 7 + 1

        ult_es_ultimo = (ultimo_cierre + timedelta(days=7)).month != ultimo_cierre.month
        prox_es_ultimo = (proximo_cierre + timedelta(days=7)).month != proximo_cierre.month

        dias_hasta = (proximo_vencimiento - proximo_cierre).days

        if n_ult == n_prox and 1 <= n_prox <= 4:
            return {
                "regla_cierre": "dia_semana",
                "dia_cierre": proximo_cierre.day,
                "dia_vencimiento": proximo_vencimiento.day,
                "semana_cierre": n_prox,
                "dia_semana_cierre": dia_sem,
                "dias_hasta_vencimiento": dias_hasta,
            }
        elif ult_es_ultimo and prox_es_ultimo:
            return {
                "regla_cierre": "dia_semana",
                "dia_cierre": proximo_cierre.day,
                "dia_vencimiento": proximo_vencimiento.day,
                "semana_cierre": -1,
                "dia_semana_cierre": dia_sem,
                "dias_hasta_vencimiento": dias_hasta,
            }

    # c. Sin ultimo_cierre o no coincide a ni b
    return {
        "regla_cierre": "dia_fijo",
        "dia_cierre": proximo_cierre.day,
        "dia_vencimiento": proximo_vencimiento.day,
        "semana_cierre": None,
        "dia_semana_cierre": None,
        "dias_hasta_vencimiento": None,
    }


def _obtener_overrides_fechas(tarjeta: TarjetaCredito) -> list[FechaResumenTarjeta]:
    """Obtiene las excepciones puntuales de fechas_resumen_tarjeta para la tarjeta."""
    # Si la relación ya está cargada o en memoria, usarla
    if hasattr(tarjeta, "fechas_resumen") and tarjeta.fechas_resumen:
        return list(tarjeta.fechas_resumen)

    # Si hay una sesión activa de SQLAlchemy asociada al objeto, consultar la BD
    session = object_session(tarjeta)
    if session is not None and getattr(tarjeta, "id", None) is not None:
        try:
            return (
                session.query(FechaResumenTarjeta)
                .filter(FechaResumenTarjeta.tarjeta_id == tarjeta.id)
                .all()
            )
        except Exception:
            logger.warning("No se pudieron consultar las excepciones de fechas de resumen de la tarjeta", exc_info=True)
            return []

    return []


def _calcular_cierre_regla(tarjeta: TarjetaCredito, anio: int, mes: int) -> date:
    """Calcula la fecha de cierre nominal de una tarjeta para un año y mes según su regla."""
    regla = getattr(tarjeta, "regla_cierre", "dia_fijo") or "dia_fijo"

    if regla == "dia_semana":
        w = getattr(tarjeta, "dia_semana_cierre", 0)
        s = getattr(tarjeta, "semana_cierre", 1)
        if s == -1:
            # Último ocurrencia del día de semana w en el mes
            ult_dia = monthrange(anio, mes)[1]
            cur = date(anio, mes, ult_dia)
            while cur.weekday() != w:
                cur -= timedelta(days=1)
            return cur
        else:
            # N-ésima ocurrencia (1 a 4)
            cur = date(anio, mes, 1)
            days_ahead = (w - cur.weekday()) % 7
            first_occ = cur + timedelta(days=days_ahead)
            return first_occ + timedelta(days=(s - 1) * 7)

    # dia_fijo
    dia_cierre = getattr(tarjeta, "dia_cierre", 1)
    ult_dia = monthrange(anio, mes)[1]
    dia_real = min(dia_cierre, ult_dia)
    return date(anio, mes, dia_real)


def _calcular_vencimiento_regla(tarjeta: TarjetaCredito, cierre_regla: date) -> date:
    """Calcula la fecha de vencimiento (ajustada a día hábil posterior) a partir del cierre nominal."""
    regla = getattr(tarjeta, "regla_cierre", "dia_fijo") or "dia_fijo"

    if regla == "dia_semana":
        dias_hasta = getattr(tarjeta, "dias_hasta_vencimiento", 0) or 0
        venc_nom = cierre_regla + timedelta(days=dias_hasta)
        return ajustar_fecha_habil_sync(venc_nom, direccion="posterior")

    # dia_fijo: mismo mes si dia_vencimiento > dia_cierre, si no el mes siguiente
    dia_cierre = getattr(tarjeta, "dia_cierre", 1)
    dia_venc = getattr(tarjeta, "dia_vencimiento", 1)

    if dia_venc > dia_cierre:
        mes_venc = date(cierre_regla.year, cierre_regla.month, 1)
    else:
        mes_venc = date(cierre_regla.year, cierre_regla.month, 1) + relativedelta(months=1)

    ult_dia_venc = monthrange(mes_venc.year, mes_venc.month)[1]
    venc_nom = date(mes_venc.year, mes_venc.month, min(dia_venc, ult_dia_venc))
    return ajustar_fecha_habil_sync(venc_nom, direccion="posterior")


def resumenes(tarjeta: TarjetaCredito, desde: date, hasta: date) -> list[tuple[date, date]]:
    """
    Lista ordenada de (cierre, vencimiento). Un resumen por mes de cierre entre desde y hasta.
    - Cierre según regla: 'dia_fijo' = min(dia_cierre, último día del mes);
      'dia_semana' = N° dia_semana_cierre del mes (o último si semana_cierre es -1).
    - Vencimiento: 'dia_fijo' = mismo mes si dia_vencimiento > dia_cierre, si no mes siguiente;
      'dia_semana' = cierre + dias_hasta_vencimiento. Ambos ajustados a día hábil posterior.
    - Si hay fila en fechas_resumen_tarjeta con fecha_cierre en el mismo mes y año que el
      cierre de la regla, se usan sus dos fechas tal cual.
    """
    overrides = _obtener_overrides_fechas(tarjeta)
    override_dict = {(o.fecha_cierre.year, o.fecha_cierre.month): o for o in overrides}

    # Iterar por cada mes desde 'desde' hasta 'hasta' (por mes de cierre)
    cur_year = desde.year
    cur_month = desde.month
    end_year = hasta.year
    end_month = hasta.month

    resultado: list[tuple[date, date]] = []

    while (cur_year < end_year) or (cur_year == end_year and cur_month <= end_month):
        cierre_regla = _calcular_cierre_regla(tarjeta, cur_year, cur_month)

        # Verificar si hay excepción para este mes de cierre
        override = override_dict.get((cierre_regla.year, cierre_regla.month))
        if override:
            cierre_final = override.fecha_cierre
            venc_final = override.fecha_vencimiento
        else:
            cierre_final = cierre_regla
            venc_final = _calcular_vencimiento_regla(tarjeta, cierre_regla)

        resultado.append((cierre_final, venc_final))

        # Avanzar al siguiente mes
        if cur_month == 12:
            cur_month = 1
            cur_year += 1
        else:
            cur_month += 1

    resultado.sort(key=lambda r: r[0])
    return resultado


def resumen_de_compra(
    tarjeta: TarjetaCredito, fecha_compra: date, proximo_resumen: bool = False
) -> tuple[date, date]:
    """
    Retorna (cierre, vencimiento) del resumen en el que ingresa una compra:
    el primer resumen con cierre >= fecha_compra (el día del cierre entra en ese resumen).
    Con proximo_resumen=True, retorna el resumen inmediatamente siguiente.
    """
    # Generar resúmenes desde el mes previo a la compra hasta 24 meses hacia adelante
    mes_inicio = date(fecha_compra.year, fecha_compra.month, 1) - relativedelta(months=1)
    mes_fin = date(fecha_compra.year, fecha_compra.month, 1) + relativedelta(months=24)
    lista = resumenes(tarjeta, mes_inicio, mes_fin)

    idx_match = None
    for i, (cierre, _) in enumerate(lista):
        if cierre >= fecha_compra:
            idx_match = i
            break

    if idx_match is None:
        # Fallback de seguridad extendiendo el rango
        lista = resumenes(tarjeta, mes_inicio, mes_fin + relativedelta(months=36))
        for i, (cierre, _) in enumerate(lista):
            if cierre >= fecha_compra:
                idx_match = i
                break

    if idx_match is None:
        raise ValueError(f"No se pudo determinar el resumen para la compra en fecha {fecha_compra}")

    if proximo_resumen:
        return lista[idx_match + 1]
    return lista[idx_match]


def vencimiento_proximo(tarjeta: TarjetaCredito, hoy: date | None = None) -> date:
    """Retorna la fecha del primer vencimiento >= hoy."""
    if hoy is None:
        hoy = hoy_argentina()

    mes_inicio = date(hoy.year, hoy.month, 1) - relativedelta(months=2)
    mes_fin = date(hoy.year, hoy.month, 1) + relativedelta(months=14)
    lista = resumenes(tarjeta, mes_inicio, mes_fin)

    for _, venc in lista:
        if venc >= hoy:
            return venc

    # Si ninguno superó hoy (ej. fin de ventana), generar más meses
    lista_extendida = resumenes(tarjeta, mes_inicio, mes_fin + relativedelta(months=24))
    for _, venc in lista_extendida:
        if venc >= hoy:
            return venc

    return lista[-1][1]


def cierre_de_vencimiento(tarjeta: TarjetaCredito, vencimiento: date) -> date:
    """
    Retorna el cierre del resumen con ese vencimiento. Si ninguno coincide exacto,
    el del resumen cuyo vencimiento cae en el mismo mes y año.
    """
    mes_inicio = date(vencimiento.year, vencimiento.month, 1) - relativedelta(months=6)
    mes_fin = date(vencimiento.year, vencimiento.month, 1) + relativedelta(months=6)
    lista = resumenes(tarjeta, mes_inicio, mes_fin)

    # 1. Coincidencia exacta de fecha de vencimiento
    for c, v in lista:
        if v == vencimiento:
            return c

    # 2. Coincidencia por mes y año de vencimiento
    for c, v in lista:
        if v.year == vencimiento.year and v.month == vencimiento.month:
            return c

    # 3. Fallback: calcular el cierre regla nominal para el mes del vencimiento
    return _calcular_cierre_regla(tarjeta, vencimiento.year, vencimiento.month)


def texto_fechas(tarjeta: TarjetaCredito) -> str:
    """
    Retorna el texto representativo de las fechas de la tarjeta:
    - 'dia_semana': 'Cierra el {ordinal} {día} de cada mes y vence {k} días después'
    - 'dia_fijo': 'Cierra el {dia_cierre} y vence el {dia_vencimiento} de cada mes'
    """
    regla = getattr(tarjeta, "regla_cierre", "dia_fijo") or "dia_fijo"

    if regla == "dia_semana":
        s = getattr(tarjeta, "semana_cierre", 1)
        w = getattr(tarjeta, "dia_semana_cierre", 0)
        k = getattr(tarjeta, "dias_hasta_vencimiento", 0)

        ordinal = ORDINALES_ES.get(s, "")
        dia_nom = DIAS_SEMANA_ES.get(w, "")
        return f"Cierra el {ordinal} {dia_nom} de cada mes y vence {k} días después"

    dia_c = getattr(tarjeta, "dia_cierre", 1)
    dia_v = getattr(tarjeta, "dia_vencimiento", 1)
    return f"Cierra el {dia_c} y vence el {dia_v} de cada mes"
