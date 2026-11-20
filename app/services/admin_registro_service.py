# -*- coding: utf-8 -*-
"""
Servicio de registro de auditoría de acciones del panel de administración.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.accion_admin import AccionAdmin
from app.models.usuario import Usuario

ACCIONES_TEXTO: dict[str, str] = {
    "suspender": "Suspendió la cuenta",
    "reactivar": "Reactivó la cuenta",
    "mail_contrasena": "Mandó el mail para cambiar la contraseña",
    "cerrar_sesiones": "Cerró las sesiones",
    "desconectar_whatsapp": "Desconectó WhatsApp",
    "hacer_admin": "Le dio permisos de admin",
    "quitar_admin": "Le quitó los permisos de admin",
    "eliminar_cuenta": "Eliminó la cuenta",
    "correr_tarea": "Corrió una tarea a mano",
    "verificacion_fallida": "No pudo confirmar su identidad",
}


def _normalizar_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def nombre_visible(usuario: Usuario | None) -> str:
    """Devuelve 'Nombre Apellido' limpio; si falta, el email; si tampoco, 'Sin nombre'."""
    if usuario is None:
        return "Sin nombre"
    partes = " ".join(
        f"{usuario.nombre or ''} {usuario.apellido or ''}".split()
    )
    if partes:
        return partes
    email = (usuario.email or "").strip()
    if email:
        return email
    return "Sin nombre"


def registrar_accion(
    db: Session,
    admin: Usuario,
    accion: str,
    usuario: Usuario | None = None,
    detalle: str | None = None,
    usuario_texto: str | None = None,
) -> AccionAdmin:
    """Agrega una fila en acciones_admin sin hacer commit."""
    if accion not in ACCIONES_TEXTO:
        raise ValueError(f"Acción de admin desconocida: {accion}")

    if accion == "eliminar_cuenta":
        target_id = None
        target_texto = "Cuenta eliminada"
    else:
        target_id = usuario.id if usuario is not None else None
        target_texto = usuario_texto or (nombre_visible(usuario) if usuario is not None else None)

    fila = AccionAdmin(
        admin_id=admin.id,
        admin_nombre=nombre_visible(admin)[:120],
        accion=accion,
        usuario_id=target_id,
        usuario_texto=target_texto[:160] if target_texto else None,
        detalle=detalle[:200] if detalle else None,
    )
    db.add(fila)
    return fila


def listar_acciones(
    db: Session,
    page: int = 1,
    limit: int = 20,
    accion: str | None = None,
) -> dict:
    """Lista las acciones registradas de la más nueva a la más vieja, paginadas y sin IDs."""
    page = max(1, page)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    conditions = []
    if accion:
        conditions.append(AccionAdmin.accion == accion)

    base = select(AccionAdmin).where(*conditions)
    total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    pages = (total + limit - 1) // limit if total > 0 else 0

    filas = db.scalars(
        base.order_by(AccionAdmin.fecha.desc(), AccionAdmin.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    items = [
        {
            "fecha": _normalizar_utc(f.fecha),
            "admin": f.admin_nombre,
            "accion": f.accion,
            "accion_texto": ACCIONES_TEXTO.get(f.accion, f.accion),
            "usuario": f.usuario_texto,
            "detalle": f.detalle,
        }
        for f in filas
    ]

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
        "items": items,
    }


def purgar_acciones_admin(db: Session, ahora: datetime) -> int:
    """Elimina acciones de admin con más de 365 días de antigüedad y confirma."""
    corte = _normalizar_utc(ahora) - timedelta(days=365)
    res = db.execute(delete(AccionAdmin).where(AccionAdmin.fecha < corte))
    db.commit()
    return int(res.rowcount or 0)


def fallas_recientes(db: Session, admin_id: UUID, ahora: datetime) -> int:
    """Cuenta las verificaciones fallidas de ese admin en los últimos 15 minutos."""
    desde = _normalizar_utc(ahora) - timedelta(minutes=15)
    res = db.scalar(
        select(func.count(AccionAdmin.id)).where(
            AccionAdmin.admin_id == admin_id,
            AccionAdmin.accion == "verificacion_fallida",
            AccionAdmin.fecha >= desde,
        )
    )
    return int(res or 0)
