# -*- coding: utf-8 -*-
"""
app/services/costos_service.py — Control y vigilancia de topes mensuales por proveedor (OpenAI y Meta).
Misión: Notifica al 50%, 80% y 100% del tope mensual estimado de gasto sin interrumpir servicios salvo OpenAI al 100% en WhatsApp.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from sqlalchemy import Numeric, func, literal, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.models.uso_ia import UsoIA
from app.services import dolar_service, openai_costos_service
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


@dataclass(frozen=True)
class CostoUsuarioMes:
    ia_usd: Decimal
    ia_ars: int | None
    whatsapp_cobrados: int
    whatsapp_ars: int
    total_ars: int
    dolar_oficial: Decimal | None
    dolar_fecha: date | None
    sin_dolar: bool


def dolar_oficial_venta(db: Session, hoy: date) -> tuple[Decimal | None, date | None]:
    """Devuelve (venta, fecha) del dólar oficial vigente para la fecha dada, o (None, None)."""
    cot = dolar_service.obtener_cotizacion_por_fecha(db, "oficial", hoy)
    if cot is None or cot.venta is None:
        return None, None
    return Decimal(str(cot.venta)), cot.fecha


def costo_mes_por_usuario(
    db: Session,
    hoy: date,
    usuario_ids: list[UUID] | None = None,
) -> dict[UUID, CostoUsuarioMes]:
    """
    Calcula el costo estimado del mes (IA en dólares pasada a pesos + WhatsApp cobrados por Meta)
    para cada usuario pedido (o para todos los que tuvieron consumo este mes).
    """
    if usuario_ids is not None and not usuario_ids:
        return {}

    inicio = inicio_mes_ar(hoy)

    stmt_ia = (
        select(
            UsoIA.usuario_id,
            func.coalesce(func.sum(UsoIA.costo_usd), 0),
        )
        .where(
            UsoIA.fecha >= inicio,
            UsoIA.usuario_id.is_not(None),
        )
        .group_by(UsoIA.usuario_id)
    )
    if usuario_ids is not None:
        stmt_ia = stmt_ia.where(UsoIA.usuario_id.in_(usuario_ids))
    ia_map: dict[UUID, Decimal] = {
        uid: Decimal(str(total or 0))
        for uid, total in db.execute(stmt_ia).all()
        if uid is not None
    }

    stmt_wpp = (
        select(
            MensajeSalienteWpp.usuario_id,
            func.count(MensajeSalienteWpp.id),
        )
        .where(
            MensajeSalienteWpp.fecha >= inicio,
            MensajeSalienteWpp.enviado == True,
            MensajeSalienteWpp.tipo_cobro == "regular",
            MensajeSalienteWpp.usuario_id.is_not(None),
        )
        .group_by(MensajeSalienteWpp.usuario_id)
    )
    if usuario_ids is not None:
        stmt_wpp = stmt_wpp.where(MensajeSalienteWpp.usuario_id.in_(usuario_ids))
    wpp_map: dict[UUID, int] = {
        uid: int(cnt or 0)
        for uid, cnt in db.execute(stmt_wpp).all()
        if uid is not None
    }

    venta, dolar_fecha = dolar_oficial_venta(db, hoy)
    tarifa = settings.WHATSAPP_TARIFA_SERVICIO_ARS

    if usuario_ids is not None:
        ids_destino = list(dict.fromkeys(usuario_ids))
    else:
        ids_destino = list(dict.fromkeys([*ia_map.keys(), *wpp_map.keys()]))

    resultado: dict[UUID, CostoUsuarioMes] = {}
    for uid in ids_destino:
        ia_raw = ia_map.get(uid, Decimal("0"))
        cobrados = wpp_map.get(uid, 0)
        ia_usd = ia_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        whatsapp_exact = Decimal(cobrados) * tarifa
        whatsapp_ars = int(whatsapp_exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        if venta is not None:
            ia_exact = ia_raw * venta
            ia_ars = int(ia_exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            total_dec = ia_exact + whatsapp_exact
            total_ars = int(total_dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            sin_dolar = False
        else:
            ia_ars = None
            total_ars = whatsapp_ars
            sin_dolar = True

        resultado[uid] = CostoUsuarioMes(
            ia_usd=ia_usd,
            ia_ars=ia_ars,
            whatsapp_cobrados=cobrados,
            whatsapp_ars=whatsapp_ars,
            total_ars=total_ars,
            dolar_oficial=venta,
            dolar_fecha=dolar_fecha,
            sin_dolar=sin_dolar,
        )

    return resultado


def expresion_costo_mes_ars(hoy: date, venta: Decimal | None):
    """
    Devuelve (subq_ia, subq_wpp, expr_total_ars) para ordenar usuarios por costo en SQL.
    Tiene que dar el mismo orden que costo_mes_por_usuario (lo controla el test C4).
    """
    inicio = inicio_mes_ar(hoy)
    subq_ia = (
        select(
            UsoIA.usuario_id.label("usuario_id"),
            func.coalesce(func.sum(UsoIA.costo_usd), 0).label("ia_usd"),
        )
        .where(
            UsoIA.fecha >= inicio,
            UsoIA.usuario_id.is_not(None),
        )
        .group_by(UsoIA.usuario_id)
        .subquery()
    )
    subq_wpp = (
        select(
            MensajeSalienteWpp.usuario_id.label("usuario_id"),
            func.count(MensajeSalienteWpp.id).label("wpp_cobrados"),
        )
        .where(
            MensajeSalienteWpp.fecha >= inicio,
            MensajeSalienteWpp.enviado == True,
            MensajeSalienteWpp.tipo_cobro == "regular",
            MensajeSalienteWpp.usuario_id.is_not(None),
        )
        .group_by(MensajeSalienteWpp.usuario_id)
        .subquery()
    )

    expr_wpp = (
        func.coalesce(subq_wpp.c.wpp_cobrados, 0)
        * literal(settings.WHATSAPP_TARIFA_SERVICIO_ARS, type_=Numeric(18, 6))
    )
    if venta is not None:
        expr_ia = (
            func.coalesce(subq_ia.c.ia_usd, 0)
            * literal(venta, type_=Numeric(18, 6))
        )
        expr_total = expr_ia + expr_wpp
    else:
        expr_total = expr_wpp

    return subq_ia, subq_wpp, expr_total


def costo_mes_total_ars(db: Session, hoy: date) -> tuple[int, bool]:
    """Devuelve (total_ars, sin_dolar) del mes calendario actual para todo Argentum."""
    ia_usd = gasto_ia_mes_usd(db, hoy)
    meta_ars = gasto_meta_mes_ars(db, hoy)
    venta, _ = dolar_oficial_venta(db, hoy)
    if venta is not None:
        total_dec = ia_usd * venta + meta_ars
        return int(total_dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), False
    return int(meta_ars.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), True


def costo_ia_mes_usd(
    db: Session,
    hoy: date,
    ahora: datetime,
) -> tuple[Decimal, str]:
    """
    Devuelve (costo_usd, fuente) del mes actual.
    El día de hoy de OpenAI todavía no está cerrado; el borde del mes se toma en UTC
    (3 horas de diferencia con Argentina).
    """
    primer_dia = date(hoy.year, hoy.month, 1)
    dias = openai_costos_service.costos_por_dia(primer_dia, hoy, ahora)
    if dias is None:
        return gasto_ia_mes_usd(db, hoy), "estimado"

    real = sum((monto for dia, monto in dias.items() if dia < hoy), Decimal("0"))
    inicio_hoy_utc = datetime(hoy.year, hoy.month, hoy.day, 0, 0, 0, tzinfo=timezone.utc)
    hoy_estimado_raw = db.scalar(
        select(func.coalesce(func.sum(UsoIA.costo_usd), 0)).where(
            UsoIA.fecha >= inicio_hoy_utc
        )
    )
    hoy_estimado = Decimal(str(hoy_estimado_raw or 0))
    return real + hoy_estimado, "openai"


def costo_mes_total_con_fuente(
    db: Session,
    hoy: date,
    ahora: datetime,
) -> tuple[int, bool, str]:
    """
    Calcula el total mensual en pesos con la mejor fuente disponible ("openai" o "estimado").
    Devuelve (total_ars, sin_dolar, fuente).
    """
    ia_usd, fuente = costo_ia_mes_usd(db, hoy, ahora)
    meta_ars = gasto_meta_mes_ars(db, hoy)
    venta, _ = dolar_oficial_venta(db, hoy)
    if venta is not None:
        total_dec = ia_usd * venta + meta_ars
        return int(total_dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), False, fuente
    return int(meta_ars.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), True, fuente

