"""
app/routers/whatsapp/recordatorio_impago_wpp.py

Envío de recordatorio de resúmenes de tarjeta impagos tras la respuesta normal
del bot de WhatsApp dentro de la ventana de 24 horas del usuario.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
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


def enviar_recordatorio_impago_si_corresponde(
    db: Session,
    usuario: Usuario,
    from_number: str,
    commit: bool = True,
) -> bool:
    """
    Envía un mensaje recordatorio si el usuario tiene resúmenes de tarjeta sin pagar.
    Solo se envía si el bot no está esperando datos o propuestas pendientes.
    Menciona hasta 3 resúmenes ordenados por vencimiento del más viejo al más nuevo.
    """
    try:
        if not usuario or not from_number:
            return False

        # No enviar recordatorio si hay una propuesta pendiente o slot filling activo
        if _buscar_propuesta_pendiente(usuario.id, db) or _buscar_slot_filling_activo(usuario.id, db):
            return False

        # Obtener notificaciones RESUMEN_IMPAGO pendientes del usuario
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

        # Agrupar notificaciones por (tarjeta_id, vencimiento)
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
                # La tarjeta ya no existe: marcar notificaciones y descartar del mensaje
                for n in notifs_grupo:
                    n.enviada_whatsapp = True
                notificaciones_descartadas.extend(notifs_grupo)
                continue

            detalle = tarjeta_service.obtener_detalle_resumen_vencimiento(db, tarjeta_id, venc)
            deuda = detalle.get("monto_deuda_restante", Decimal("0"))
            if deuda is None or deuda <= Decimal("0"):
                # La deuda fue saldada: marcar notificaciones y descartar del mensaje
                for n in notifs_grupo:
                    n.enviada_whatsapp = True
                notificaciones_descartadas.extend(notifs_grupo)
                continue

            # Construir link y textos formateados
            deep_link = notifs_grupo[0].deep_link or f"/app/billeteras/{tarjeta.billetera_id}?tab=credito&tarjeta_id={tarjeta.id}"
            if not deep_link.startswith("/"):
                deep_link = "/" + deep_link
            link = f"{settings.FRONTEND_URL}{deep_link}"

            monto_fmt = formatear_monto(deuda, tarjeta.moneda)
            venc_fmt = venc.strftime("%d/%m")

            resumenes_validos.append({
                "tarjeta_id": tarjeta_id,
                "nombre_tarjeta": tarjeta.nombre,
                "vencimiento": venc,
                "venc_fmt": venc_fmt,
                "monto": monto_fmt,
                "link": link,
                "notificaciones": notifs_grupo,
            })

        if not resumenes_validos:
            # Si solo hubo notificaciones descartadas (deuda 0 o sin tarjeta), persistir su estado
            if notificaciones_descartadas:
                if commit:
                    db.commit()
                else:
                    db.flush()
            return False

        # Ordenar resúmenes por vencimiento del más viejo al más nuevo
        resumenes_validos.sort(key=lambda r: r["vencimiento"])

        # Mencionar hasta un máximo de 3 resúmenes
        mencionados = resumenes_validos[:3]

        if len(mencionados) == 1:
            r = mencionados[0]
            mensaje = (
                f"Ojo: el resumen de {r['nombre_tarjeta']} de {r['monto']} "
                f"que venció el {r['venc_fmt']} figura sin pagar. "
                f"Si ya lo pagaste, marcalo acá: {r['link']}"
            )
        else:
            lineas = [
                f"- {r['nombre_tarjeta']}: {r['monto']}, venció el {r['venc_fmt']}. {r['link']}"
                for r in mencionados
            ]
            cuerpo = "\n".join(lineas)
            mensaje = (
                f"Ojo: estos resúmenes figuran sin pagar:\n"
                f"{cuerpo}\n"
                f"Si ya los pagaste, marcalos desde esos links."
            )

        # Enviar mensaje por WhatsApp
        exito = whatsapp_service.enviar_whatsapp(from_number, mensaje)
        if exito:
            for r in mencionados:
                for n in r["notificaciones"]:
                    n.enviada_whatsapp = True
            if commit:
                db.commit()
            else:
                db.flush()
            return True
        else:
            db.rollback()
            return False

    except Exception as e:
        db.rollback()
        logger.error("Error al procesar recordatorio de resumen impago por WhatsApp: %s", e, exc_info=True)
        return False
