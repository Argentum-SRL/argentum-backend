# -*- coding: utf-8 -*-
"""
app/routers/whatsapp/aviso_personas_wpp.py — Aviso contextual de una línea para movimientos
de Pagos a personas y Cobros de personas. Decisión 12.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.subcategoria import Subcategoria
from app.models.transaccion import OrigenTransaccion, Transaccion
from app.models.usuario import Usuario
from app.services import whatsapp_service

logger = logging.getLogger(__name__)

TEXTO_AVISO_PAGOS = "Si te acordás para qué era, decime «era el súper» y lo cambio."
TEXTO_AVISO_COBROS = "Si te acordás qué era, decime «era un regalo» y lo cambio."


def agregar_aviso_personas(
    db: Session,
    usuario: Usuario,
    dt_inicio: datetime,
) -> bool:
    """
    Agrega el aviso de contextualización si el mensaje registró transacciones
    en 'Pagos a personas' o 'Cobros de personas'.
    
    Prioridad:
    1. Si hubo al menos un movimiento en 'Pagos a personas' -> TEXTO_AVISO_PAGOS.
    2. Si hubo 'Cobros de personas' y ninguno en 'Pagos a personas' -> TEXTO_AVISO_COBROS.
    3. Ninguno -> no se agrega aviso.
    
    El recordatorio de impago tiene prioridad: buzon.agregar_aviso descarta el aviso
    si ya hay otro aviso asignado.
    """
    buzon = whatsapp_service.buzon_actual()
    if buzon is None:
        return False

    if dt_inicio.tzinfo is None:
        dt_inicio_comp = dt_inicio.replace(tzinfo=timezone.utc)
    else:
        dt_inicio_comp = dt_inicio

    try:
        # Consultar transacciones registradas desde el inicio de este mensaje con origen WhatsApp
        stmt = (
            select(Subcategoria.nombre)
            .join(Transaccion, Transaccion.subcategoria_id == Subcategoria.id)
            .where(
                Transaccion.usuario_id == usuario.id,
                Transaccion.origen.in_([OrigenTransaccion.IA_WPP, OrigenTransaccion.IA_PDF]),
                Transaccion.fecha_creacion >= dt_inicio_comp,
            )
        )
        subcategorias_registradas = set(db.execute(stmt).scalars().all())

        if "Pagos a personas" in subcategorias_registradas:
            buzon.agregar_aviso(TEXTO_AVISO_PAGOS, "aviso_personas")
            return True
        elif "Cobros de personas" in subcategorias_registradas:
            buzon.agregar_aviso(TEXTO_AVISO_COBROS, "aviso_personas")
            return True

        return False
    except Exception as exc:
        logger.error("Error al procesar agregar_aviso_personas: %s", exc, exc_info=True)
        return False
