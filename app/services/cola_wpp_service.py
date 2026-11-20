"""
Cola durable de mensajes de WhatsApp sobre la tabla mensajes_whatsapp_procesados
y pool de hilos dedicado para no quitarle hilos a los endpoints web.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado

logger = logging.getLogger(__name__)

PENDIENTE = "pendiente"
PROCESANDO = "procesando"
LISTO = "listo"
FALLO = "fallo"
INTERRUMPIDO = "interrumpido"
VENCIDO = "vencido"

ESPERA_RESCATE = timedelta(seconds=60)
MAXIMO_ATRASO = timedelta(minutes=60)
PROCESANDO_COLGADO = timedelta(minutes=10)
LOTE_RESCATE = 50
HILOS_WPP = 8

# Grupo propio para WhatsApp, así una ráfaga de mensajes no usa los hilos de la web (B16).
POOL_WPP = ThreadPoolExecutor(max_workers=HILOS_WPP, thread_name_prefix="wpp")


async def ejecutar_en_pool_wpp(funcion: Callable[..., Any], *args: Any) -> None:
    """Ejecuta el procesamiento de un mensaje de WhatsApp en el grupo propio de hilos."""
    await asyncio.get_running_loop().run_in_executor(
        POOL_WPP,
        functools.partial(funcion, *args),
    )


def payload_de(datos_mensaje: dict) -> dict:
    """Extrae los campos mínimos del mensaje de Meta para persistir en la cola."""
    return {
        "msg": datos_mensaje["msg"],
        "from_number": datos_mensaje["from_number"],
        "msg_type": datos_mensaje["msg_type"],
    }


def datos_desde_fila(wamid: str, payload: dict) -> dict:
    """Reconstruye el diccionario datos_mensaje a partir de la fila en cola."""
    return {
        "msg": payload["msg"],
        "wamid": wamid,
        "from_number": payload["from_number"],
        "msg_type": payload["msg_type"],
        "t_inicio": None,
    }


def _normalizar_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def tomar_mensaje(db: Session, wamid: str, ahora: datetime) -> bool:
    """
    Toma de forma atómica un mensaje en estado pendiente pasándolo a procesando.
    Devuelve True solo si esta llamada ganó la toma (rowcount == 1).
    """
    ahora_utc = _normalizar_utc(ahora)
    stmt = (
        update(MensajeWhatsappProcesado)
        .where(
            MensajeWhatsappProcesado.wamid == wamid,
            MensajeWhatsappProcesado.estado == PENDIENTE,
        )
        .values(
            estado=PROCESANDO,
            tomado_en=ahora_utc,
            intentos=MensajeWhatsappProcesado.intentos + 1,
        )
    )
    res = db.execute(stmt)
    db.commit()
    return res.rowcount == 1


def cerrar_mensaje(db: Session, wamid: str, estado_final: str) -> None:
    """
    Cierra el mensaje en la cola con su estado final y limpia el payload.
    Nunca lanza excepciones para no interferir con el flujo principal.
    """
    try:
        stmt = (
            update(MensajeWhatsappProcesado)
            .where(MensajeWhatsappProcesado.wamid == wamid)
            .values(estado=estado_final, payload=None)
        )
        db.execute(stmt)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("No se pudo cerrar el mensaje de la cola de WhatsApp.")


def rescatar_pendientes(
    db: Session,
    procesar: Callable[[dict], None],
    ahora: datetime,
) -> dict[str, int]:
    """
    Marca mensajes vencidos o interrumpidos y procesa en orden los mensajes pendientes
    que quedaron sin empezar tras un reinicio dentro de la ventana válida.
    """
    ahora_utc = _normalizar_utc(ahora)
    limite_atraso = ahora_utc - MAXIMO_ATRASO
    limite_colgado = ahora_utc - PROCESANDO_COLGADO
    limite_espera = ahora_utc - ESPERA_RESCATE

    # 1. Vencidos: pendientes con más de MAXIMO_ATRASO sin procesarse
    res_vencidos = db.execute(
        update(MensajeWhatsappProcesado)
        .where(
            MensajeWhatsappProcesado.estado == PENDIENTE,
            MensajeWhatsappProcesado.fecha_recepcion < limite_atraso,
        )
        .values(estado=VENCIDO, payload=None)
    )
    n_vencidos = int(res_vencidos.rowcount or 0)

    # 2. Interrumpidos: nunca se vuelven a procesar (puede haber quedado un movimiento cargado a medias)
    res_interrumpidos = db.execute(
        update(MensajeWhatsappProcesado)
        .where(
            MensajeWhatsappProcesado.estado == PROCESANDO,
            MensajeWhatsappProcesado.tomado_en < limite_colgado,
        )
        .values(estado=INTERRUMPIDO, payload=None)
    )
    n_interrumpidos = int(res_interrumpidos.rowcount or 0)

    # 3. Persistir vencidos e interrumpidos antes de listar candidatos
    db.commit()

    # 4. Candidatos pendientes dentro de la ventana de rescate
    candidatos = db.execute(
        select(MensajeWhatsappProcesado.wamid, MensajeWhatsappProcesado.payload)
        .where(
            MensajeWhatsappProcesado.estado == PENDIENTE,
            MensajeWhatsappProcesado.payload.isnot(None),
            MensajeWhatsappProcesado.fecha_recepcion >= limite_atraso,
            MensajeWhatsappProcesado.fecha_recepcion <= limite_espera,
        )
        .order_by(MensajeWhatsappProcesado.fecha_recepcion.asc())
        .limit(LOTE_RESCATE)
    ).all()

    # 5. Procesar uno por uno; un error no frena a los demás
    n_listos = 0
    n_fallos = 0
    for wamid, payload in candidatos:
        if not tomar_mensaje(db, wamid, ahora_utc):
            continue
        try:
            procesar(datos_desde_fila(wamid, payload))
            cerrar_mensaje(db, wamid, LISTO)
            n_listos += 1
        except Exception:
            logger.exception("Falló el rescate de un mensaje de WhatsApp.")
            cerrar_mensaje(db, wamid, FALLO)
            n_fallos += 1

    return {
        "rescatados": n_listos,
        "fallados": n_fallos,
        "vencidos": n_vencidos,
        "interrumpidos": n_interrumpidos,
    }
