# -*- coding: utf-8 -*-
"""
frescura_service.py - Servicio para cálculo de frescura de datos de un usuario.

Determina si los datos financieros de un usuario están desactualizados
utilizando un umbral adaptativo basado en su propio historial de cargas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import math
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera
from app.models.transaccion import OrigenTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.services.reporte_uso_service import dia_ar
from app.utils.fecha import hoy_argentina
from app.utils.finanzas import mad, mediana

ORIGENES_CARGA_VALIDOS = (
    OrigenTransaccion.MANUAL,
    OrigenTransaccion.IA_WPP,
    OrigenTransaccion.IA_PDF,
)


def umbral_dias(intervalos: list[int]) -> int:
    """
    Calcula el umbral de días sin cargar antes de considerar que los datos están viejos.
    Con menos de 5 intervalos devuelve 7.
    Con 5 o más: m = mediana(intervalos) y d = mad(intervalos) de app/utils/finanzas.py
    en Decimal (desvío absoluto mediano sin factor);
    umbral = el entero hacia arriba de m + 3 × d; devuelve max(7, umbral).
    """
    if len(intervalos) < 5:
        return 7

    intervalos_dec = [Decimal(str(x)) for x in intervalos]
    m = mediana(intervalos_dec)
    d = mad(intervalos_dec)

    if m is None or d is None:
        return 7

    valor = m + Decimal("3") * d
    # Entero hacia arriba (ceil)
    umbral_entero = int(math.ceil(float(valor)))
    return max(7, umbral_entero)


@dataclass(frozen=True)
class Frescura:
    """
    Representa el estado de frescura de datos del usuario.
    """
    ultima_carga: date | None
    dias_sin_cargar: int | None
    umbral_dias: int
    intervalos: int
    datos_viejos: bool

    def a_dict(self) -> dict[str, Any]:
        """Convierte la frescura a diccionario serializable con fechas en formato ISO."""
        return {
            "ultima_carga": self.ultima_carga.isoformat() if self.ultima_carga else None,
            "dias_sin_cargar": self.dias_sin_cargar,
            "umbral_dias": self.umbral_dias,
            "intervalos": self.intervalos,
            "datos_viejos": self.datos_viejos,
        }


def frescura_desde(dias_carga: list[date], ultima_carga: date | None, hoy: date) -> Frescura:
    """
    Calcula el objeto Frescura a partir de los días de carga observados (función pura).
    - dias: días distintos de dias_carga entre hoy − 180 y hoy, ordenados.
    - intervalos: diferencias en días entre días consecutivos.
    - umbral: umbral_dias(intervalos).
    - dias_sin_cargar: (hoy − ultima_carga).days o None.
    - datos_viejos: ultima_carga no es None y dias_sin_cargar > umbral (estricto).
    - intervalos del dataclass: cantidad de intervalos.
    """
    desde = hoy - timedelta(days=180)
    dias_filtrados = {d for d in dias_carga if desde <= d <= hoy}
    dias_ordenados = sorted(dias_filtrados)

    intervalos = [
        (dias_ordenados[i + 1] - dias_ordenados[i]).days
        for i in range(len(dias_ordenados) - 1)
    ]
    umbral = umbral_dias(intervalos)

    dias_sin_cargar = (hoy - ultima_carga).days if ultima_carga is not None else None
    datos_viejos = ultima_carga is not None and dias_sin_cargar is not None and dias_sin_cargar > umbral

    return Frescura(
        ultima_carga=ultima_carga,
        dias_sin_cargar=dias_sin_cargar,
        umbral_dias=umbral,
        intervalos=len(intervalos),
        datos_viejos=datos_viejos,
    )


def calcular_frescura(db: Session, usuario_id: UUID, hoy: date | None = None) -> Frescura:
    """
    Calcula la frescura de los datos del usuario en la base de datos (solo lectura).
    - hoy = hoy o hoy_argentina().
    - Lee las fecha_creacion de las tres fuentes de ese usuario con un margen de 2 días
      alrededor de la ventana de 180 días (filtro en UTC sin zona) y decide el día con dia_ar en Python.
    - ultima_carga = el día argentino más reciente de las tres fuentes sin límite de antigüedad
      (consulta max por fuente) que sea <= hoy.
    - Llama a frescura_desde.
    """
    if hoy is None:
        hoy = hoy_argentina()

    # Ventana de 180 días con margen de 2 días a cada lado
    desde_ventana = hoy - timedelta(days=180)
    dt_min_utc = datetime(desde_ventana.year, desde_ventana.month, desde_ventana.day) - timedelta(days=2)
    dt_max_utc = datetime(hoy.year, hoy.month, hoy.day, 23, 59, 59) + timedelta(days=2)

    # 1. Transacciones manuales o vía WhatsApp
    stmt_tx = select(Transaccion.fecha_creacion).where(
        Transaccion.usuario_id == usuario_id,
        Transaccion.origen.in_(ORIGENES_CARGA_VALIDOS),
        Transaccion.fecha_creacion >= dt_min_utc,
        Transaccion.fecha_creacion <= dt_max_utc,
    )
    tx_fechas = db.execute(stmt_tx).scalars().all()

    # 2. Transferencias internas
    stmt_tr = select(TransferenciaInterna.fecha_creacion).where(
        TransferenciaInterna.usuario_id == usuario_id,
        TransferenciaInterna.fecha_creacion >= dt_min_utc,
        TransferenciaInterna.fecha_creacion <= dt_max_utc,
    )
    tr_fechas = db.execute(stmt_tr).scalars().all()

    # 3. Ajustes de saldo
    stmt_aj = select(AjusteSaldo.fecha_creacion).join(
        Billetera, AjusteSaldo.billetera_id == Billetera.id
    ).where(
        Billetera.usuario_id == usuario_id,
        AjusteSaldo.fecha_creacion >= dt_min_utc,
        AjusteSaldo.fecha_creacion <= dt_max_utc,
    )
    aj_fechas = db.execute(stmt_aj).scalars().all()

    # Convertir a días locales argentinos dentro de la ventana [hoy - 180, hoy]
    dias_carga: list[date] = []
    for fc in tx_fechas + tr_fechas + aj_fechas:
        d = dia_ar(fc)
        if d is not None and desde_ventana <= d <= hoy:
            dias_carga.append(d)

    # Consultas de fecha más reciente sin límite de antigüedad (max por fuente)
    # Límite superior amplio para no tomar eventos con dia_ar > hoy
    limite_sup_utc = datetime(hoy.year, hoy.month, hoy.day, 23, 59, 59) + timedelta(days=2)

    stmt_max_tx = select(func.max(Transaccion.fecha_creacion)).where(
        Transaccion.usuario_id == usuario_id,
        Transaccion.origen.in_(ORIGENES_CARGA_VALIDOS),
        Transaccion.fecha_creacion <= limite_sup_utc,
    )
    max_tx = db.execute(stmt_max_tx).scalar()

    stmt_max_tr = select(func.max(TransferenciaInterna.fecha_creacion)).where(
        TransferenciaInterna.usuario_id == usuario_id,
        TransferenciaInterna.fecha_creacion <= limite_sup_utc,
    )
    max_tr = db.execute(stmt_max_tr).scalar()

    stmt_max_aj = select(func.max(AjusteSaldo.fecha_creacion)).join(
        Billetera, AjusteSaldo.billetera_id == Billetera.id
    ).where(
        Billetera.usuario_id == usuario_id,
        AjusteSaldo.fecha_creacion <= limite_sup_utc,
    )
    max_aj = db.execute(stmt_max_aj).scalar()

    # Encontrar la fecha de última carga (dia_ar más reciente <= hoy)
    candidatos_max: list[date] = []
    for fc in (max_tx, max_tr, max_aj):
        if fc is not None:
            d = dia_ar(fc)
            if d is not None and d <= hoy:
                candidatos_max.append(d)

    ultima_carga = max(candidatos_max) if candidatos_max else None

    return frescura_desde(dias_carga=dias_carga, ultima_carga=ultima_carga, hoy=hoy)

