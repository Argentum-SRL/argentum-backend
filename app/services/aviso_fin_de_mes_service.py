# -*- coding: utf-8 -*-
"""
app/services/aviso_fin_de_mes_service.py — Aviso de fin de mes si el libre proyectado
no alcanza para los compromisos hasta el fin del ciclo. Decisión 6A.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.notificacion import NivelNotificacion, TipoNotificacion
from app.services import notificacion_service
from app.services.fin_de_mes_service import calcular_fin_de_mes
from app.services.notificacion_service import crear_notificacion, resolver_canales_notificacion
from app.utils.formato import formatear_monto

logger = logging.getLogger(__name__)

LINEA_MAX = 90


def faltante_aviso(libre: Decimal, moneda: str) -> Decimal | None:
    """
    Calcula el monto faltante positivo redondeado a entero.
    Si libre >= 0 o el faltante redondeado es menor a 1, devuelve None.
    """
    if libre >= Decimal("0"):
        return None
    f = abs(libre).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if f < Decimal("1"):
        return None
    return f


def texto_aviso_fin_de_mes(faltante: Decimal, moneda: str, fin_ciclo: date) -> str:
    """
    Genera el texto de aviso de fin de mes (máximo LINEA_MAX caracteres).
    """
    m_str = formatear_monto(faltante, moneda, con_decimales=False)
    fin_str = fin_ciclo.strftime("%d/%m")

    if moneda == "USD":
        texto = f"Ojo: en dólares te faltan {m_str} para cubrir lo que vence hasta el {fin_str}."
    else:
        texto = f"Ojo: con lo que tenés hoy te faltan {m_str} para cubrir lo que vence hasta el {fin_str}."

    if len(texto) > LINEA_MAX:
        texto = f"Ojo: te faltan {m_str} para cubrir lo que vence hasta el {fin_str}."

    return texto


def monedas_aviso(usuario: Any) -> tuple[str, ...]:
    """
    Determina las monedas a evaluar para el aviso.
    Siempre ("ARS",), y suma "USD" si la moneda principal es USD o secundaria está activa.
    """
    mp = getattr(usuario, "moneda_principal", None)
    mp_val = mp.value if hasattr(mp, "value") else str(mp or "")
    sec_activa = bool(getattr(usuario, "moneda_secundaria_activa", False))

    if mp_val == "USD" or sec_activa:
        return ("ARS", "USD")
    return ("ARS",)


def monedas_con_saldo(db: Session, usuario_id: Any) -> frozenset[str]:
    """
    Monedas ("ARS"/"USD", en mayúscula) en las que el usuario tiene al menos una
    billetera ACTIVA, que NO es de inversión, con tiene_punto_partida True o
    con fecha_ultimo_control no nula (hizo 'Actualizar saldo').
    Solo lectura (sin commit ni flush).
    """
    stmt = (
        select(Billetera.moneda)
        .where(
            Billetera.usuario_id == usuario_id,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.es_inversion == False,
            or_(
                Billetera.tiene_punto_partida == True,
                Billetera.fecha_ultimo_control != None,
            ),
        )
    )
    filas = db.execute(stmt).scalars().all()
    res = set()
    for m in filas:
        val = m.value if hasattr(m, "value") else str(m)
        if val:
            res.add(val.upper())
    return frozenset(res)


def avisos_que_corresponden(
    r: Any,
    monedas: tuple[str, ...] | list[str],
    con_saldo: frozenset[str] = frozenset(),
) -> list[tuple[str, Decimal, str]]:
    """
    Determina qué avisos corresponden según el resultado de fin de mes, monedas y saldo cargado.
    Si hay datos viejos, devuelve lista vacía.
    Una moneda califica solo si está en con_saldo y su lista de compromisos no está vacía.
    """
    if getattr(r, "datos_viejos", False):
        return []

    resultado: list[tuple[str, Decimal, str]] = []
    for mon in monedas:
        if mon not in con_saldo:
            continue
        compromisos = getattr(r, "compromisos_ars", []) if mon == "ARS" else getattr(r, "compromisos_usd", [])
        if not compromisos:
            continue
        libre = r.libre_ars if mon == "ARS" else r.libre_usd
        f = faltante_aviso(libre, mon)
        if f is not None:
            texto = texto_aviso_fin_de_mes(f, mon, r.fin_ciclo)
            resultado.append((mon, f, texto))

    return resultado


def grupo_aviso(moneda: str, inicio_ciclo: date) -> str:
    """
    Genera la clave de agrupación única por ciclo y moneda.
    """
    return f"PROYECCION_NEGATIVA_{moneda}_{inicio_ciclo:%Y%m%d}"


def avisar_fin_de_mes(db: Session, usuario: Any, hoy: date, commit: bool = True) -> int:
    """
    Evalúa y crea las notificaciones de proyección negativa correspondientes.
    Retorna la cantidad de notificaciones creadas.
    """
    config = notificacion_service.obtener_configuracion(db, usuario.id, commit=commit)
    canales = resolver_canales_notificacion(config, TipoNotificacion.PROYECCION_NEGATIVA)
    if canales is None:
        return 0

    canal_web, canal_whatsapp = canales
    if not canal_web and not canal_whatsapp:
        return 0

    r = calcular_fin_de_mes(db, usuario, hoy=hoy)
    monedas = monedas_aviso(usuario)
    con_saldo = monedas_con_saldo(db, usuario.id)
    avisos = avisos_que_corresponden(r, monedas, con_saldo)

    creados = 0
    for mon, f, texto in avisos:
        grupo = grupo_aviso(mon, r.inicio_ciclo)
        notif = crear_notificacion(
            db=db,
            usuario_id=usuario.id,
            tipo=TipoNotificacion.PROYECCION_NEGATIVA,
            nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
            mensaje=texto,
            deep_link="/app/dashboard",
            canal_web=canal_web,
            canal_whatsapp=canal_whatsapp,
            grupo_agrupacion_override=grupo,
            commit=commit,
            datos_template={
                "moneda": mon,
                "inicio_ciclo": r.inicio_ciclo.isoformat(),
                "fin_ciclo": r.fin_ciclo.isoformat(),
                "faltante": str(f),
            },
        )
        if notif is not None:
            creados += 1

    return creados
