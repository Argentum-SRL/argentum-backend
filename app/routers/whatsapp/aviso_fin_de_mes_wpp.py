# -*- coding: utf-8 -*-
"""
app/routers/whatsapp/aviso_fin_de_mes_wpp.py — Agregado de aviso de fin de mes
como última línea dentro del buzón de respuesta de WhatsApp. Modelo: recordatorio_impago_wpp.
"""
from __future__ import annotations

from datetime import date
import logging

from sqlalchemy.orm import Session

from app.models.notificacion import Notificacion, TipoNotificacion
from app.models.usuario import Usuario
from app.routers.whatsapp.db_lookups import (
    _buscar_propuesta_pendiente,
    _buscar_slot_filling_activo,
)
from app.services import whatsapp_service
from app.services.aviso_fin_de_mes_service import (
    avisos_que_corresponden,
    monedas_con_saldo,
)
from app.services.dashboard_service import get_ciclo_fechas
from app.services.fin_de_mes_service import calcular_fin_de_mes
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)


def agregar_aviso_fin_de_mes(
    db: Session,
    usuario: Usuario,
    hoy: date | None = None,
    commit: bool = True,
) -> bool:
    """
    Agrega el aviso de fin de mes si el libre proyectado no alcanza para lo que vence en el ciclo.
    Reglas:
    - Sin buzón abierto o buzón con aviso previo -> False.
    - Sin propuesta pendiente ni slot filling activo.
    - Notificaciones PROYECCION_NEGATIVA no archivadas, canal_whatsapp=True, no enviadas por WhatsApp,
      cuyo datos_template pertenezca al ciclo actual.
    - Recalcula fin de mes con fecha de hoy. Si datos_viejos -> False.
    - Si hoy ya le alcanza (faltante None) se descarta marcando enviada_whatsapp=True.
    - Primera con faltante no None (ARS antes que USD) es la elegida.
    - Registra el aviso vía buzon.agregar_aviso(...) con callback al_enviar.
    - Ante cualquier excepción: rollback, logger.error con exc_info, False.
    """
    try:
        if not usuario:
            return False

        buzon = whatsapp_service.buzon_actual()
        if buzon is None:
            return False

        if buzon.tiene_aviso():
            return False

        if _buscar_propuesta_pendiente(usuario.id, db) or _buscar_slot_filling_activo(usuario.id, db):
            return False

        fecha_hoy = hoy or hoy_argentina()
        inicio, _ = get_ciclo_fechas(usuario, fecha_hoy)
        inicio_iso = inicio.isoformat()

        notificaciones = (
            db.query(Notificacion)
            .filter(
                Notificacion.usuario_id == usuario.id,
                Notificacion.tipo == TipoNotificacion.PROYECCION_NEGATIVA,
                Notificacion.archivada == False,
                Notificacion.enviada_whatsapp == False,
                Notificacion.canal_whatsapp == True,
            )
            .all()
        )

        pendientes: list[Notificacion] = []
        for n in notificaciones:
            dt = n.datos_template
            if (
                isinstance(dt, dict)
                and dt.get("inicio_ciclo") == inicio_iso
                and dt.get("moneda") in ("ARS", "USD")
            ):
                pendientes.append(n)

        if not pendientes:
            return False

        r = calcular_fin_de_mes(db, usuario, hoy=fecha_hoy)
        if getattr(r, "datos_viejos", False):
            return False

        def _clave_orden(n: Notificacion):
            m = n.datos_template.get("moneda") if isinstance(n.datos_template, dict) else ""
            prio = 0 if m == "ARS" else 1
            return (prio, n.created_at)

        pendientes.sort(key=_clave_orden)

        elegida: Notificacion | None = None
        texto_elegido: str | None = None
        descartes: list[Notificacion] = []

        con_saldo = monedas_con_saldo(db, usuario.id)

        for notif in pendientes:
            dt = notif.datos_template or {}
            moneda = str(dt.get("moneda", "ARS"))
            avisos_mon = avisos_que_corresponden(r, (moneda,), con_saldo)
            if not avisos_mon:
                notif.enviada_whatsapp = True
                descartes.append(notif)
            else:
                elegida = notif
                _, _, texto_elegido = avisos_mon[0]
                break

        if descartes:
            if commit:
                db.commit()
            else:
                db.flush()

        if elegida is None or texto_elegido is None:
            return False

        texto = texto_elegido
        elegida_id = elegida.id

        def al_enviar():
            elegida.enviada_whatsapp = True
            try:
                if commit:
                    db.commit()
                else:
                    db.flush()
            except Exception:
                pass

            from sqlalchemy import update
            from app.core.database import SessionLocal
            try:
                with SessionLocal() as s:
                    s.execute(
                        update(Notificacion)
                        .where(Notificacion.id == elegida_id)
                        .values(enviada_whatsapp=True)
                    )
                    s.commit()
            except Exception as exc_cb:
                logger.error("Error al persistir enviada_whatsapp en al_enviar de aviso_fin_de_mes: %s", exc_cb)

        return buzon.agregar_aviso(texto, "fin_de_mes", al_enviar=al_enviar)

    except Exception as exc:
        db.rollback()
        logger.error("Error al procesar agregar_aviso_fin_de_mes: %s", exc, exc_info=True)
        return False
