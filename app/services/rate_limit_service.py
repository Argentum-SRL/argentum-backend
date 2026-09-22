"""
app/services/rate_limit_service.py — Servicio unificado de límites anti-ráfaga y almacenamiento persistente en Postgres.
Implementa operaciones atómicas mediante PostgreSQL ON CONFLICT DO UPDATE (UPSERT) para garantizar
tolerancia total a concurrencia extrema sin condiciones de carrera ni violaciones de unicidad.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, delete, case
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.codigo_verificacion import CodigoVerificacion
from app.models.conversacion_wpp import ConversacionWpp
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado
from app.models.rate_limit import RateLimit
from limits.storage import Storage

logger = logging.getLogger(__name__)


def verificar_rate_limit(
    accion: str,
    identificador: str,
    max_intentos: int,
    ventana_segundos: int,
    db: Session | None = None,
    detalles: dict[str, Any] | None = None,
) -> tuple[bool, int, float]:
    """
    Verifica y actualiza el contador de rate limit en la tabla rate_limits de Postgres.
    Ejecuta un UPSERT atómico (INSERT ... ON CONFLICT DO UPDATE) en una sola sentencia SQL,
    garantizando seguridad contra concurrencia extrema sin condiciones de carrera ni
    errores de restricción única.

    Retorna: (permitido: bool, cantidad_actual: int, segundos_restantes: float)
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        ahora = datetime.now(timezone.utc)
        expira_inicial = ahora + timedelta(seconds=ventana_segundos)

        stmt = pg_insert(RateLimit).values(
            id=uuid4(),
            accion=accion,
            identificador=identificador,
            cantidad=1,
            ventana_segundos=ventana_segundos,
            ventana_inicio=ahora,
            expira_en=expira_inicial,
            detalles=detalles,
            actualizado_en=ahora,
        )

        stmt = stmt.on_conflict_do_update(
            constraint="uq_rate_limits_accion_identificador",
            set_={
                "cantidad": case(
                    (RateLimit.expira_en <= stmt.excluded.ventana_inicio, 1),
                    else_=RateLimit.cantidad + 1,
                ),
                "ventana_inicio": case(
                    (RateLimit.expira_en <= stmt.excluded.ventana_inicio, stmt.excluded.ventana_inicio),
                    else_=RateLimit.ventana_inicio,
                ),
                "expira_en": case(
                    (RateLimit.expira_en <= stmt.excluded.ventana_inicio, stmt.excluded.expira_en),
                    else_=RateLimit.expira_en,
                ),
                "ventana_segundos": stmt.excluded.ventana_segundos,
                "detalles": case(
                    (stmt.excluded.detalles.isnot(None), stmt.excluded.detalles),
                    else_=RateLimit.detalles,
                ),
                "actualizado_en": stmt.excluded.actualizado_en,
            },
        ).returning(RateLimit.cantidad, RateLimit.expira_en)

        res = db.execute(stmt).fetchone()
        db.commit()

        cantidad_asignada = res[0]
        expira_en = res[1]

        if expira_en.tzinfo is None:
            expira_en = expira_en.replace(tzinfo=timezone.utc)

        segundos_restantes = max(0.0, (expira_en - ahora).total_seconds())
        permitido = cantidad_asignada <= max_intentos

        return permitido, cantidad_asignada, segundos_restantes

    finally:
        if should_close:
            db.close()


def limpiar_codigos_y_rate_limits_expirados(db: Session) -> dict[str, int]:
    """
    Elimina registros vencidos en Postgres:
    - codigos_verificacion: códigos cuya fecha de expiración ya pasó o que fueron consumidos hace más de 1 hora.
    - rate_limits: ventanas cuya fecha de expiración ya pasó.
    """
    ahora = datetime.now(timezone.utc)
    hace_una_hora = ahora - timedelta(hours=1)

    codigos_borrados = db.execute(
        delete(CodigoVerificacion).where(
            (CodigoVerificacion.expiracion <= ahora)
            | ((CodigoVerificacion.consumido == True) & (CodigoVerificacion.consumido_en <= hace_una_hora))
        )
    ).rowcount

    rate_limits_borrados = db.execute(
        delete(RateLimit).where(RateLimit.expira_en <= ahora)
    ).rowcount

    db.commit()
    logger.info(
        "Limpieza periódica ejecutada: %d códigos y %d rate limits eliminados.",
        codigos_borrados,
        rate_limits_borrados,
    )
    return {
        "codigos_verificacion_eliminados": codigos_borrados,
        "rate_limits_eliminados": rate_limits_borrados,
    }


def purgar_conversaciones_wpp_antiguas(db: Session) -> int:
    """
    Elimina conversaciones de WhatsApp con más de 90 días de antigüedad.
    Retorna la cantidad de filas eliminadas.
    """
    limite = datetime.now(timezone.utc) - timedelta(days=90)
    borrados = db.execute(
        delete(ConversacionWpp).where(ConversacionWpp.fecha < limite)
    ).rowcount
    db.commit()
    logger.info(
        "Purga de conversaciones_wpp ejecutada: %d filas eliminadas (anteriores a %s).",
        borrados,
        limite.isoformat(),
    )
    return borrados


def purgar_mensajes_whatsapp_procesados_antiguos(db: Session) -> int:
    """
    Elimina registros de mensajes WhatsApp procesados con más de 7 días de antigüedad.
    Retorna la cantidad de filas eliminadas.
    """
    limite = datetime.now(timezone.utc) - timedelta(days=7)
    borrados = db.execute(
        delete(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.fecha_recepcion < limite)
    ).rowcount
    db.commit()
    logger.info(
        "Purga de mensajes_whatsapp_procesados ejecutada: %d filas eliminadas (anteriores a %s).",
        borrados,
        limite.isoformat(),
    )
    return borrados


class PostgresStorage(Storage):
    """
    Storage de SlowAPI / limits respaldado en Postgres (tabla rate_limits).
    Permite que los límites basados en IP sobrevivan reinicios de servidor de forma atómica.
    """
    STORAGE_SCHEME = ["postgres_rate_limit"]

    @property
    def base_exceptions(self) -> tuple[type[Exception], ...]:
        return (Exception,)

    def incr(self, key: str, expiry: int, amount: int = 1) -> int:
        with SessionLocal() as db:
            ahora = datetime.now(timezone.utc)
            expira_inicial = ahora + timedelta(seconds=expiry)

            stmt = pg_insert(RateLimit).values(
                id=uuid4(),
                accion="slowapi",
                identificador=key,
                cantidad=amount,
                ventana_segundos=expiry,
                ventana_inicio=ahora,
                expira_en=expira_inicial,
                actualizado_en=ahora,
            )

            stmt = stmt.on_conflict_do_update(
                constraint="uq_rate_limits_accion_identificador",
                set_={
                    "cantidad": case(
                        (RateLimit.expira_en <= stmt.excluded.ventana_inicio, amount),
                        else_=RateLimit.cantidad + amount,
                    ),
                    "ventana_inicio": case(
                        (RateLimit.expira_en <= stmt.excluded.ventana_inicio, stmt.excluded.ventana_inicio),
                        else_=RateLimit.ventana_inicio,
                    ),
                    "expira_en": case(
                        (RateLimit.expira_en <= stmt.excluded.ventana_inicio, stmt.excluded.expira_en),
                        else_=RateLimit.expira_en,
                    ),
                    "ventana_segundos": stmt.excluded.ventana_segundos,
                    "actualizado_en": stmt.excluded.actualizado_en,
                },
            ).returning(RateLimit.cantidad)

            res = db.execute(stmt).scalar_one()
            db.commit()
            return res

    def get(self, key: str) -> int:
        with SessionLocal() as db:
            ahora = datetime.now(timezone.utc)
            row = db.execute(
                select(RateLimit).where(
                    RateLimit.accion == "slowapi",
                    RateLimit.identificador == key,
                )
            ).scalar_one_or_none()
            if row and ahora < row.expira_en:
                return row.cantidad
            return 0

    def get_expiry(self, key: str) -> float:
        with SessionLocal() as db:
            ahora = datetime.now(timezone.utc)
            row = db.execute(
                select(RateLimit).where(
                    RateLimit.accion == "slowapi",
                    RateLimit.identificador == key,
                )
            ).scalar_one_or_none()
            if row and ahora < row.expira_en:
                return row.expira_en.timestamp()
            return 0.0

    def check(self) -> bool:
        return True

    def reset(self) -> int | None:
        with SessionLocal() as db:
            c = db.execute(delete(RateLimit).where(RateLimit.accion == "slowapi")).rowcount
            db.commit()
            return c

    def clear(self, key: str) -> None:
        with SessionLocal() as db:
            db.execute(delete(RateLimit).where(RateLimit.accion == "slowapi", RateLimit.identificador == key))
            db.commit()
