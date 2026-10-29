# -*- coding: utf-8 -*-
"""
Servicio para el registro de eventos de uso de la web.
Mide la utilización interna de herramientas sin cookies ni datos personales ni montos.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.evento_uso import EventoUso
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)

EVENTOS = ("visita", "permitirse", "calculadora_cuotas", "descargar_datos")


def registrar_evento(
    db: Session,
    usuario_id: Optional[UUID],
    evento: str,
    commit: bool = True,
) -> bool:
    """
    Registra un evento de uso si no existe aún para el usuario en el día actual (zona horaria argentina).
    Devuelve True si se insertó la fila, False si ya existía o hubo carrera de concurrencia.
    Lanza ValueError si el evento no está en EVENTOS.
    Nunca propaga excepciones inesperadas al llamador (hace rollback si commit=True y devuelve False).
    """
    if evento not in EVENTOS:
        raise ValueError(f"Evento desconocido: {evento}")

    dia = hoy_argentina()

    try:
        # Si ya existe una fila (usuario_id, evento, dia) -> False sin escribir
        stmt = select(EventoUso.id).where(
            EventoUso.usuario_id == usuario_id,
            EventoUso.evento == evento,
            EventoUso.dia == dia,
        )
        if db.execute(stmt).first() is not None:
            return False

        with db.begin_nested():
            nuevo_evento = EventoUso(
                usuario_id=usuario_id,
                evento=evento,
                dia=dia,
            )
            db.add(nuevo_evento)
            db.flush()

        if commit:
            db.commit()
        return True
    except IntegrityError:
        logger.debug(
            "Colisión de concurrencia al registrar evento de uso '%s' para usuario %s",
            evento,
            usuario_id,
        )
        if commit:
            db.rollback()
        return False
    except Exception as e:
        logger.exception("Error inesperado al registrar evento de uso: %s", e)
        if commit:
            db.rollback()
        return False

