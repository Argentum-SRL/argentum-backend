# -*- coding: utf-8 -*-
"""
app/services/costos_service.py — Control y vigilancia de topes mensuales por proveedor (OpenAI y Meta).
Misión: Notifica al 50%, 80% y 100% del tope mensual estimado de gasto sin interrumpir servicios salvo OpenAI al 100% en WhatsApp.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.models.uso_ia import UsoIA
from app.services.email_service import _enviar_email
from app.services.rate_limit_service import verificar_rate_limit
from app.services.reporte_uso_service import destinatarios_reporte
from app.utils.fecha import TZ_ARGENTINA
from app.utils.formato import formatear_monto

logger = logging.getLogger(__name__)

PORCENTAJES_AVISO: tuple[int, ...] = (50, 80, 100)


def inicio_mes_ar(hoy: date) -> datetime:
    """Retorna el inicio del mes calendario en la zona horaria de Argentina."""
    return datetime(hoy.year, hoy.month, 1, 0, 0, 0, tzinfo=TZ_ARGENTINA)


def nivel_alcanzado(gasto: Decimal | float, tope: Decimal | float) -> int | None:
    """
    Determina el mayor nivel de aviso alcanzado (50, 80 o 100%).
    Si tope <= 0 devuelve None.
    """
    gasto_dec = Decimal(str(gasto))
    tope_dec = Decimal(str(tope))
    if tope_dec <= Decimal("0"):
        return None
    for p in reversed(PORCENTAJES_AVISO):
        if gasto_dec * Decimal("100") >= tope_dec * Decimal(str(p)):
            return p
    return None


def _monto(x: Decimal | float, moneda: str) -> str:
    """Formatea montos con o sin decimales según la moneda (USD con decimales, ARS sin decimales)."""
    x_dec = Decimal(str(x))
    if moneda.upper() == "USD":
        return formatear_monto(x_dec, "USD", con_decimales=True)
    return formatear_monto(x_dec, "ARS", con_decimales=False)


def texto_aviso_costo(
    proveedor: str,
    nivel: int,
    gasto: Decimal | float,
    tope: Decimal | float,
    moneda: str,
    hoy: date,
) -> tuple[str, str]:
    """Genera el asunto y cuerpo del email de alerta de costos del mes."""
    gasto_dec = Decimal(str(gasto))
    tope_dec = Decimal(str(tope))
    asunto = f"[Argentum] {proveedor}: se usó el {nivel}% del tope del mes"
    lineas = [
        f"Gasto estimado del mes {hoy:%m/%Y}: {_monto(gasto_dec, moneda)} de un tope de {_monto(tope_dec, moneda)}."
    ]
    if proveedor == "OpenAI" and nivel == 100:
        lineas.append(
            "El bot de WhatsApp dejó de usar la IA hasta el 1° del mes que viene. La web sigue andando."
        )
    elif proveedor == "Meta" and nivel == 100:
        lineas.append("WhatsApp sigue andando: el tope de Meta es solo un aviso.")
    lineas.append(f"Monto estimado por Argentum, no facturado por {proveedor}.")
    return asunto, "\n".join(lineas)


def exento_tope_mensual(usuario: Any) -> bool:
    """
    PLAN PAGO (decisión de Sebastián 06/11/2026): cuando exista el plan pago, esta función tiene que devolver True para quien paga, así el tope mensual de la IA no le corta el bot. Hoy nadie paga: siempre False.
    """
    # PLAN PAGO (decisión de Sebastián 06/11/2026): cuando exista el plan pago, esta función tiene que devolver True para quien paga, así el tope mensual de la IA no le corta el bot. Hoy nadie paga: siempre False.
    return False


def gasto_ia_mes_usd(db: Session, hoy: date) -> Decimal:
    """Suma el gasto en USD registrado en uso_ia desde el inicio del mes actual en Argentina."""
    inicio = inicio_mes_ar(hoy)
    res = db.scalar(
        select(func.coalesce(func.sum(UsoIA.costo_usd), 0)).where(
            UsoIA.fecha >= inicio
        )
    )
    return Decimal(str(res or 0))


def cobrados_meta_mes(db: Session, hoy: date) -> int:
    """Cuenta los mensajes salientes de WhatsApp enviados con tipo_cobro 'regular' en el mes actual en Argentina."""
    inicio = inicio_mes_ar(hoy)
    res = db.scalar(
        select(func.count(MensajeSalienteWpp.id)).where(
            MensajeSalienteWpp.enviado == True,
            MensajeSalienteWpp.tipo_cobro == "regular",
            MensajeSalienteWpp.fecha >= inicio,
        )
    )
    return int(res or 0)


def gasto_meta_mes_ars(db: Session, hoy: date) -> Decimal:
    """Calcula el gasto estimado en ARS de Meta en el mes actual según la tarifa de servicio configurada."""
    cobrados = cobrados_meta_mes(db, hoy)
    return Decimal(str(cobrados)) * settings.WHATSAPP_TARIFA_SERVICIO_ARS


def avisar_costo_si_corresponde(
    proveedor: str,
    gasto: Decimal | float,
    tope: Decimal | float,
    moneda: str,
    hoy: date,
) -> int | None:
    """
    Envía un email al equipo administrativo si el gasto alcanzó uno de los niveles y aún no se notificó este mes.
    Nunca propaga excepciones.
    """
    try:
        gasto_dec = Decimal(str(gasto))
        tope_dec = Decimal(str(tope))
        nivel = nivel_alcanzado(gasto_dec, tope_dec)
        if nivel is None:
            return None

        permitido, _, _ = verificar_rate_limit(
            accion=f"aviso_costo_{proveedor.lower()}_{nivel}",
            identificador=f"{hoy:%Y-%m}",
            max_intentos=1,
            ventana_segundos=40 * 86400,
        )
        if not permitido:
            return None

        asunto, cuerpo = texto_aviso_costo(
            proveedor=proveedor,
            nivel=nivel,
            gasto=gasto_dec,
            tope=tope_dec,
            moneda=moneda,
            hoy=hoy,
        )
        destinatarios = destinatarios_reporte()
        if not destinatarios:
            logger.warning(
                "Sin destinatarios para reporte de costos",
                extra={"proveedor": proveedor, "nivel": nivel},
            )
        else:
            for dest in destinatarios:
                _enviar_email(dest, asunto, cuerpo)

        return nivel
    except Exception:
        logger.exception("Error al avisar costo si corresponde", extra={"proveedor": proveedor})
        return None


def vigilar_costos(db: Session, hoy: date) -> None:
    """Vigila los costos de OpenAI y Meta evaluando si corresponde enviar alertas. No realiza commit."""
    gasto_ia = gasto_ia_mes_usd(db, hoy)
    tope_ia = Decimal(str(settings.IA_PRESUPUESTO_MENSUAL_USD))
    avisar_costo_si_corresponde("OpenAI", gasto_ia, tope_ia, "USD", hoy)

    gasto_meta = gasto_meta_mes_ars(db, hoy)
    tope_meta = settings.META_TOPE_MENSUAL_ARS
    avisar_costo_si_corresponde("Meta", gasto_meta, tope_meta, "ARS", hoy)

