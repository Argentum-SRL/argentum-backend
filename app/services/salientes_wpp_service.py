"""
app/services/salientes_wpp_service.py — Servicio de auditoría y control de mensajes salientes de WhatsApp.
Comentario: No guarda textos de mensajes ni números de teléfono en claro.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina

logger = logging.getLogger(__name__)

ORDEN_ESTADOS_META = {
    "sent": 1,
    "delivered": 2,
    "read": 3,
}


def hash_telefono(numero: str | None) -> str | None:
    """
    Genera el HMAC-SHA256 del número en formato E.164 canónico usando settings.SECRET_KEY.
    Retorna el resumen hexadecimal en minúsculas (64 caracteres) o None si el número es nulo.
    """
    if not numero:
        return None
    from app.services.whatsapp_service import formatear_numero_whatsapp
    tel_formateado = formatear_numero_whatsapp(numero)
    if not tel_formateado:
        return None
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, tel_formateado.encode("utf-8"), hashlib.sha256).hexdigest()


def salientes_hoy(telefono_hash: str | None, fabrica_sesion=None) -> int:
    """
    Retorna la cantidad de mensajes efectivamente enviados hoy (desde las 00:00 ART) para el hash dado.
    """
    if not telefono_hash:
        return 0
    sesion_maker = fabrica_sesion if fabrica_sesion is not None else SessionLocal
    try:
        hoy = hoy_argentina()
        inicio_hoy_ar = datetime(hoy.year, hoy.month, hoy.day, 0, 0, 0, tzinfo=TZ_ARGENTINA)
        with sesion_maker() as db:
            cant = db.scalar(
                select(func.count(MensajeSalienteWpp.id)).where(
                    MensajeSalienteWpp.telefono_hash == telefono_hash,
                    MensajeSalienteWpp.enviado == True,
                    MensajeSalienteWpp.fecha >= inicio_hoy_ar,
                )
            )
            return cant or 0
    except Exception as exc:
        logger.error("Error al consultar salientes_hoy para hash %s: %s", telefono_hash, exc)
        return 0


def registrar_saliente(
    usuario_id: UUID | None,
    telefono_hash: str | None,
    tipo: str,
    es_plantilla: bool,
    partes: int,
    aviso_tipo: str | None,
    wamid_entrante: str | None,
    enviado: bool,
    motivo_no_enviado: str | None,
    http_status: int | None,
    codigo_error_meta: int | None,
    wamid_meta: str | None,
    estado_meta: str | None = None,
    tipo_cobro: str | None = None,
    categoria_cobro: str | None = None,
    fabrica_sesion=None,
) -> None:
    """
    Registra un intento de mensaje saliente en la base de datos en su propia sesión. Nunca lanza excepciones.
    """
    sesion_maker = fabrica_sesion if fabrica_sesion is not None else SessionLocal
    try:
        with sesion_maker() as db:
            registro = MensajeSalienteWpp(
                usuario_id=usuario_id,
                telefono_hash=telefono_hash,
                tipo=tipo,
                es_plantilla=es_plantilla,
                partes=partes,
                aviso_tipo=aviso_tipo,
                wamid_entrante=wamid_entrante,
                enviado=enviado,
                motivo_no_enviado=motivo_no_enviado,
                http_status=http_status,
                codigo_error_meta=codigo_error_meta,
                wamid_meta=wamid_meta,
                estado_meta=estado_meta,
                tipo_cobro=tipo_cobro,
                categoria_cobro=categoria_cobro,
                actualizado_en=datetime.now(timezone.utc) if estado_meta else None,
            )
            db.add(registro)
            db.commit()
    except Exception as exc:
        logger.error("Error al registrar_saliente (tipo=%s, enviado=%s): %s", tipo, enviado, exc)


def registrar_estados_meta(statuses: list[dict], fabrica_sesion=None) -> int:
    """
    Actualiza el estado de entrega y cobro de mensajes enviados a partir de webhooks de estado de Meta.
    Reglas:
    - estado_meta nunca baja (sent < delivered < read).
    - 'failed' solo pisa a 'sent' o vacío y guarda errors[0].code en codigo_error_meta.
    - Si viene pricing y la fila no lo tiene, guarda pricing.type en tipo_cobro y pricing.category en categoria_cobro.
    - wamid desconocido: no hace nada.
    Nunca lanza excepciones.
    """
    if not statuses:
        return 0

    actualizados = 0
    sesion_maker = fabrica_sesion if fabrica_sesion is not None else SessionLocal
    try:
        with sesion_maker() as db:
            for st in statuses:
                if not isinstance(st, dict):
                    continue
                wamid = st.get("id")
                if not wamid:
                    continue

                fila = db.execute(
                    select(MensajeSalienteWpp).where(MensajeSalienteWpp.wamid_meta == wamid)
                ).scalar_one_or_none()

                if not fila:
                    continue

                nuevo_estado = st.get("status")
                ahora_utc = datetime.now(timezone.utc)
                modificado = False

                if nuevo_estado == "failed":
                    if fila.estado_meta in (None, "", "sent"):
                        fila.estado_meta = "failed"
                        errors = st.get("errors", [])
                        if errors and isinstance(errors, list) and isinstance(errors[0], dict):
                            fila.codigo_error_meta = errors[0].get("code")
                        fila.actualizado_en = ahora_utc
                        modificado = True
                elif nuevo_estado in ORDEN_ESTADOS_META:
                    nivel_nuevo = ORDEN_ESTADOS_META[nuevo_estado]
                    nivel_actual = ORDEN_ESTADOS_META.get(fila.estado_meta or "", 0)
                    if nivel_nuevo > nivel_actual:
                        fila.estado_meta = nuevo_estado
                        fila.actualizado_en = ahora_utc
                        modificado = True

                pricing = st.get("pricing")
                if isinstance(pricing, dict) and not fila.tipo_cobro:
                    tipo_cobro_val = pricing.get("type")
                    cat_cobro_val = pricing.get("category")
                    if tipo_cobro_val:
                        fila.tipo_cobro = tipo_cobro_val
                        fila.categoria_cobro = cat_cobro_val
                        fila.actualizado_en = ahora_utc
                        modificado = True

                if modificado:
                    actualizados += 1

            if actualizados > 0:
                db.commit()
    except Exception as exc:
        logger.error("Error al registrar_estados_meta: %s", exc)

    return actualizados


def resumen_salientes(db: Session, dias: int, ahora: datetime | None = None) -> dict:
    """
    Genera el resumen de salientes de WhatsApp para el panel administrativo.
    Estructura de salida:
    - mes: métricas del mes calendario UTC actual.
    - por_dia: métricas por día de Argentina en la ventana de días (solo días con enviados).
    - no_enviados_por_motivo: desglose en la ventana de días.
    - entrantes_con_mas_de_una_respuesta: cantidad de wamid_entrante con > 1 fila enviada en la ventana.
    """
    if ahora is None:
        ahora_utc = datetime.now(timezone.utc)
    else:
        if ahora.tzinfo is None:
            ahora_utc = ahora.replace(tzinfo=timezone.utc)
        else:
            ahora_utc = ahora.astimezone(timezone.utc)

    # 1. Ventana de mes calendario en UTC
    inicio_mes_utc = datetime(ahora_utc.year, ahora_utc.month, 1, 0, 0, 0, tzinfo=timezone.utc)
    if ahora_utc.month == 12:
        fin_mes_utc = datetime(ahora_utc.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        fin_mes_utc = datetime(ahora_utc.year, ahora_utc.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    filas_mes = db.scalars(
        select(MensajeSalienteWpp).where(
            MensajeSalienteWpp.fecha >= inicio_mes_utc,
            MensajeSalienteWpp.fecha < fin_mes_utc,
        )
    ).all()

    enviados_mes = [f for f in filas_mes if f.enviado]
    no_enviados_mes = [f for f in filas_mes if not f.enviado]

    plantillas_mes = sum(1 for f in enviados_mes if f.es_plantilla)
    cobrados_meta = sum(1 for f in enviados_mes if f.tipo_cobro == "regular")
    gratis_meta = sum(1 for f in enviados_mes if f.tipo_cobro and f.tipo_cobro.startswith("free"))
    sin_dato_meta = sum(1 for f in enviados_mes if not f.tipo_cobro)
    cupo_gratis = settings.WHATSAPP_CUPO_GRATIS_MES
    cupo_usado = sum(
        1 for f in enviados_mes
        if not f.es_plantilla and f.estado_meta in ("delivered", "read")
    )

    mes_dict = {
        "enviados": len(enviados_mes),
        "plantillas": plantillas_mes,
        "cobrados_meta": cobrados_meta,
        "gratis_meta": gratis_meta,
        "sin_dato_meta": sin_dato_meta,
        "cupo_gratis": cupo_gratis,
        "cupo_usado": cupo_usado,
        "no_enviados": len(no_enviados_mes),
    }

    # 2. Ventana de días de Argentina
    ahora_ar = ahora_utc.astimezone(TZ_ARGENTINA)
    dia_inicio_ar = ahora_ar.date() - timedelta(days=dias - 1)
    desde_ventana = datetime(dia_inicio_ar.year, dia_inicio_ar.month, dia_inicio_ar.day, 0, 0, 0, tzinfo=TZ_ARGENTINA)

    filas_ventana = db.scalars(
        select(MensajeSalienteWpp).where(
            MensajeSalienteWpp.fecha >= desde_ventana,
            MensajeSalienteWpp.fecha <= ahora_utc,
        )
    ).all()

    # Agrupar por día (solo días con enviados)
    dias_dict: dict[date, list[MensajeSalienteWpp]] = defaultdict(list)
    no_enviados_por_motivo: dict[str, int] = defaultdict(int)
    entrantes_conteo: dict[str, int] = defaultdict(int)

    for f in filas_ventana:
        if not f.enviado:
            motivo = f.motivo_no_enviado or "desconocido"
            no_enviados_por_motivo[motivo] += 1
        else:
            fecha_ar = f.fecha.astimezone(TZ_ARGENTINA).date()
            dias_dict[fecha_ar].append(f)
            if f.wamid_entrante:
                entrantes_conteo[f.wamid_entrante] += 1

    por_dia_list = []
    for d_val in sorted(dias_dict.keys()):
        filas_dia = dias_dict[d_val]
        total_dia = len(filas_dia)
        respuestas_dia = sum(1 for f in filas_dia if f.tipo in ("respuesta", "no_registrado"))
        plantillas_dia = sum(1 for f in filas_dia if f.es_plantilla)
        otros_dia = sum(1 for f in filas_dia if not f.es_plantilla and f.tipo not in ("respuesta", "no_registrado"))

        por_dia_list.append({
            "fecha": d_val.strftime("%d/%m"),
            "total": total_dia,
            "respuestas": respuestas_dia,
            "plantillas": plantillas_dia,
            "otros": otros_dia,
        })

    entrantes_mas_de_uno = sum(1 for c in entrantes_conteo.values() if c > 1)

    return {
        "mes": mes_dict,
        "por_dia": por_dia_list,
        "no_enviados_por_motivo": dict(no_enviados_por_motivo),
        "entrantes_con_mas_de_una_respuesta": entrantes_mas_de_uno,
    }


def purgar_salientes_antiguos(db: Session) -> int:
    """
    Elimina registros de mensajes salientes con más de 400 días de antigüedad.
    Retorna la cantidad de filas eliminadas.
    """
    limite = datetime.now(timezone.utc) - timedelta(days=400)
    borrados = db.execute(
        delete(MensajeSalienteWpp).where(MensajeSalienteWpp.fecha < limite)
    ).rowcount
    db.commit()
    logger.info(
        "Purga de mensajes_salientes_wpp ejecutada: %d filas eliminadas (anteriores a %s).",
        borrados,
        limite.isoformat(),
    )
    return borrados
