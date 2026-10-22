"""
app/routers/whatsapp/recordatorio_impago_wpp.py — Agregado de recordatorio de resúmenes de tarjeta impagos
como aviso de última línea dentro del buzón de respuesta de WhatsApp.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.notificacion import Notificacion, TipoNotificacion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.usuario import Usuario
from app.routers.whatsapp.db_lookups import (
    _buscar_propuesta_pendiente,
    _buscar_slot_filling_activo,
)
from app.services import tarjeta_service, whatsapp_service
from app.utils.formato import formatear_monto

logger = logging.getLogger(__name__)
obtener_detalle_resumen_vencimiento = tarjeta_service.obtener_detalle_resumen_vencimiento


def _obtener_fecha_vencimiento(notif: Notificacion) -> date | None:
    """Extrae y parsea la fecha de vencimiento desde datos_template."""
    if not notif.datos_template or not isinstance(notif.datos_template, dict):
        return None
    val = notif.datos_template.get("vencimiento")
    if not val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        val_str = str(val).strip()
        if "T" in val_str or " " in val_str:
            return datetime.fromisoformat(val_str).date()
        return date.fromisoformat(val_str)
    except Exception:
        return None


def agregar_recordatorio_impago(
    db: Session,
    usuario: Usuario,
    commit: bool = True,
) -> bool:
    """
    Agrega el recordatorio de resúmenes de tarjeta sin pagar como aviso al buzón de respuesta activo.
    Reglas:
    - Sin propuesta pendiente ni slot filling.
    - Notificaciones no archivadas, no enviadas por WhatsApp, agrupadas por (tarjeta_id, vencimiento).
    - Descarta y marca inmediatamente notificaciones de tarjetas inexistentes o con deuda <= 0.
    - Un resumen: "Ojo: el resumen de {nombre} de {monto} (venció {dd/mm}) figura sin pagar."
      (nombre recortado a 18 caracteres; si pasa de 90 chars, sin el monto).
    - Dos o más: "Ojo: tenés {n} resúmenes de tarjeta sin pagar; el más viejo venció el {dd/mm}."
    - Registra el aviso vía buzon_actual().agregar_aviso(...) con callback al_enviar.
    - Sin buzón abierto: devuelve False y no hace nada.
    - Ante cualquier excepción: rollback, False, no propaga.
    """
    try:
        if not usuario:
            return False

        buzon = whatsapp_service.buzon_actual()
        if buzon is None:
            return False

        # No incluir aviso si hay propuesta pendiente o slot filling activo
        if _buscar_propuesta_pendiente(usuario.id, db) or _buscar_slot_filling_activo(usuario.id, db):
            return False

        # Obtener notificaciones RESUMEN_IMPAGO pendientes
        notificaciones = (
            db.query(Notificacion)
            .filter(
                Notificacion.usuario_id == usuario.id,
                Notificacion.tipo == TipoNotificacion.RESUMEN_IMPAGO,
                Notificacion.archivada == False,
                Notificacion.enviada_whatsapp == False,
            )
            .all()
        )

        if not notificaciones:
            return False

        # Agrupar por (tarjeta_id, vencimiento)
        grupos: dict[tuple[UUID, date], list[Notificacion]] = {}
        for notif in notificaciones:
            if not notif.entidad_id:
                continue
            venc = _obtener_fecha_vencimiento(notif)
            if not venc:
                continue
            clave = (notif.entidad_id, venc)
            if clave not in grupos:
                grupos[clave] = []
            grupos[clave].append(notif)

        if not grupos:
            return False

        notificaciones_descartadas: list[Notificacion] = []
        resumenes_validos: list[dict[str, Any]] = []

        for (tarjeta_id, venc), notifs_grupo in grupos.items():
            tarjeta = db.query(TarjetaCredito).filter(TarjetaCredito.id == tarjeta_id).first()
            if not tarjeta:
                for n in notifs_grupo:
                    n.enviada_whatsapp = True
                notificaciones_descartadas.extend(notifs_grupo)
                continue

            detalle = obtener_detalle_resumen_vencimiento(db, tarjeta_id, venc)
            if isinstance(detalle, dict):
                deuda = detalle.get("monto_deuda_restante", Decimal("0"))
            elif isinstance(detalle, (list, tuple)):
                deuda = detalle[0]
            else:
                deuda = Decimal("0")

            if deuda is None or deuda <= Decimal("0"):
                for n in notifs_grupo:
                    n.enviada_whatsapp = True
                notificaciones_descartadas.extend(notifs_grupo)
                continue

            resumenes_validos.append({
                "tarjeta": tarjeta,
                "vencimiento": venc,
                "deuda": deuda,
                "notificaciones": notifs_grupo,
            })

        if notificaciones_descartadas:
            if commit:
                db.commit()
            else:
                db.flush()

        if not resumenes_validos:
            return False

        # Ordenar por vencimiento ascendente
        resumenes_validos.sort(key=lambda r: r["vencimiento"])

        if len(resumenes_validos) == 1:
            r = resumenes_validos[0]
            nom = r["tarjeta"].nombre
            if len(nom) > 18:
                nom_recortado = nom[:17].rstrip() + "…"
            else:
                nom_recortado = nom

            monto_fmt = formatear_monto(r["deuda"], r["tarjeta"].moneda)
            venc_fmt = r["vencimiento"].strftime("%d/%m")

            texto = f"Ojo: el resumen de {nom_recortado} de {monto_fmt} (venció {venc_fmt}) figura sin pagar."
            if len(texto) > 90:
                texto = f"Ojo: el resumen de {nom_recortado} (venció {venc_fmt}) figura sin pagar."
        else:
            n = len(resumenes_validos)
            venc_mas_viejo = resumenes_validos[0]["vencimiento"].strftime("%d/%m")
            texto = f"Ojo: tenés {n} resúmenes de tarjeta sin pagar; el más viejo venció el {venc_mas_viejo}."

        ids_notifs = [n.id for r in resumenes_validos for n in r["notificaciones"]]

        def al_enviar():
            for r in resumenes_validos:
                for notif in r["notificaciones"]:
                    notif.enviada_whatsapp = True
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
                        .where(Notificacion.id.in_(ids_notifs))
                        .values(enviada_whatsapp=True)
                    )
                    s.commit()
            except Exception as exc_cb:
                logger.error("Error al persistir enviada_whatsapp en al_enviar: %s", exc_cb)

        buzon.agregar_aviso(texto, "recordatorio_impago", al_enviar=al_enviar)
        return True

    except Exception as exc:
        db.rollback()
        logger.error("Error al procesar agregar_recordatorio_impago: %s", exc, exc_info=True)
        return False
