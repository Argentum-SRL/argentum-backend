from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.informe_personalizado import InformePersonalizado
from app.models.usuario import Usuario


def _serializar_informe(inf: InformePersonalizado) -> Dict[str, Any]:
    return {
        "id": str(inf.id),
        "nombre": inf.nombre,
        "descripcion": inf.descripcion,
        "icono": inf.icono,
        "es_favorito": bool(inf.es_favorito),
        "configuracion": inf.configuracion or {},
        "fecha_creacion": inf.fecha_creacion.isoformat() if inf.fecha_creacion else None,
        "fecha_actualizacion": inf.fecha_actualizacion.isoformat() if inf.fecha_actualizacion else None,
    }


def listar_informes_usuario(db: Session, usuario: Usuario) -> List[Dict[str, Any]]:
    """
    Lista todos los informes personalizados creados por el usuario, ordenados por favoritos
    y fecha de última actualización.
    """
    rows = (
        db.execute(
            select(InformePersonalizado)
            .where(InformePersonalizado.usuario_id == usuario.id)
            .order_by(
                InformePersonalizado.es_favorito.desc(),
                InformePersonalizado.fecha_actualizacion.desc(),
            )
        )
        .scalars()
        .all()
    )
    return [_serializar_informe(r) for r in rows]


def crear_informe_usuario(
    db: Session,
    usuario: Usuario,
    nombre: str,
    descripcion: Optional[str],
    icono: str,
    es_favorito: bool,
    configuracion: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Crea un nuevo informe personalizado para el usuario actual.
    """
    nuevo = InformePersonalizado(
        usuario_id=usuario.id,
        nombre=(nombre or "Informe sin título").strip()[:120],
        descripcion=(descripcion.strip()[:300] if descripcion else None),
        icono=(icono or "pie-chart")[:40],
        es_favorito=bool(es_favorito),
        configuracion=configuracion or {},
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return _serializar_informe(nuevo)


def obtener_informe_usuario(
    db: Session,
    usuario: Usuario,
    informe_id: UUID,
) -> Optional[Dict[str, Any]]:
    """
    Obtiene un informe personalizado por ID, validando que pertenezca al usuario autenticado.
    """
    inf = (
        db.execute(
            select(InformePersonalizado).where(
                InformePersonalizado.id == informe_id,
                InformePersonalizado.usuario_id == usuario.id,
            )
        )
        .scalars()
        .first()
    )
    if not inf:
        return None
    return _serializar_informe(inf)


def actualizar_informe_usuario(
    db: Session,
    usuario: Usuario,
    informe_id: UUID,
    datos: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Actualiza atributos de un informe personalizado del usuario.
    """
    inf = (
        db.execute(
            select(InformePersonalizado).where(
                InformePersonalizado.id == informe_id,
                InformePersonalizado.usuario_id == usuario.id,
            )
        )
        .scalars()
        .first()
    )
    if not inf:
        return None

    if "nombre" in datos and datos["nombre"] is not None:
        inf.nombre = str(datos["nombre"]).strip()[:120] or inf.nombre
    if "descripcion" in datos:
        inf.descripcion = str(datos["descripcion"]).strip()[:300] if datos["descripcion"] else None
    if "icono" in datos and datos["icono"]:
        inf.icono = str(datos["icono"])[:40]
    if "es_favorito" in datos and datos["es_favorito"] is not None:
        inf.es_favorito = bool(datos["es_favorito"])
    if "configuracion" in datos and isinstance(datos["configuracion"], dict):
        inf.configuracion = datos["configuracion"]

    inf.fecha_actualizacion = datetime.now(timezone.utc)
    db.commit()
    db.refresh(inf)
    return _serializar_informe(inf)


def eliminar_informe_usuario(db: Session, usuario: Usuario, informe_id: UUID) -> bool:
    """
    Elimina un informe personalizado del usuario.
    """
    inf = (
        db.execute(
            select(InformePersonalizado).where(
                InformePersonalizado.id == informe_id,
                InformePersonalizado.usuario_id == usuario.id,
            )
        )
        .scalars()
        .first()
    )
    if not inf:
        return False
    db.delete(inf)
    db.commit()
    return True
