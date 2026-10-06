"""
app/routers/memoria_comercios.py — Endpoints para gestión de memoria de categorización por comercio.
"""
from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.categoria import Categoria
from app.models.memoria_comercio import MemoriaComercio
from app.models.subcategoria import Subcategoria
from app.models.usuario import Usuario
from app.schemas.memoria_comercio import (
    MemoriaAplicarRequest,
    MemoriaAplicarResponse,
    MemoriaComercioCreate,
    MemoriaComercioGuardarResponse,
    MemoriaComercioRead,
    MemoriaSugerenciaResponse,
    TransaccionAnteriorItem,
)
from app.services import memoria_comercio_service

router = APIRouter(prefix="/memoria-comercios", tags=["memoria-comercios"])


@router.get("/sugerencia", response_model=MemoriaSugerenciaResponse)
def obtener_sugerencia(
    descripcion: str = Query(..., description="Texto o concepto del gasto/ingreso"),
    tipo: str = Query("egreso", description="Tipo de movimiento: egreso o ingreso"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Retorna la clave normalizada y la categoría recordada si existe para el usuario.
    """
    clave = memoria_comercio_service.clave_comercio(descripcion)
    memoria = memoria_comercio_service.buscar(db, current_user.id, descripcion, tipo)
    return MemoriaSugerenciaResponse(
        clave=clave,
        memoria_id=memoria.id if memoria else None,
        categoria_id=memoria.categoria_id if memoria else None,
        subcategoria_id=memoria.subcategoria_id if memoria else None,
    )


@router.post("", response_model=MemoriaComercioGuardarResponse)
def guardar_memoria(
    data: MemoriaComercioCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Guarda o actualiza la memoria por comercio y retorna los movimientos anteriores que difieren.
    """
    memoria = memoria_comercio_service.guardar(
        db=db,
        usuario_id=current_user.id,
        descripcion=data.descripcion,
        tipo=data.tipo,
        categoria_id=data.categoria_id,
        subcategoria_id=data.subcategoria_id,
        commit=True,
    )

    anteriores = memoria_comercio_service.anteriores_distintos(db, current_user.id, memoria)
    anteriores_items: list[TransaccionAnteriorItem] = []
    for tx in anteriores:
        cat_nom = tx.categoria.nombre if tx.categoria else (
            db.get(Categoria, tx.categoria_id).nombre if tx.categoria_id else ""
        )
        sub_nom = tx.subcategoria.nombre if tx.subcategoria else (
            db.get(Subcategoria, tx.subcategoria_id).nombre if tx.subcategoria_id else None
        )
        anteriores_items.append(
            TransaccionAnteriorItem(
                id=tx.id,
                fecha=tx.fecha,
                monto=tx.monto,
                moneda=tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda),
                descripcion=tx.descripcion,
                categoria_nombre=cat_nom,
                subcategoria_nombre=sub_nom,
            )
        )

    return MemoriaComercioGuardarResponse(
        memoria_id=memoria.id,
        clave=memoria.clave,
        categoria_id=memoria.categoria_id,
        subcategoria_id=memoria.subcategoria_id,
        cantidad_anteriores=len(anteriores_items),
        anteriores=anteriores_items,
    )


@router.post("/aplicar", response_model=MemoriaAplicarResponse)
def aplicar_a_anteriores(
    data: MemoriaAplicarRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Aplica la categoría de la memoria a transacciones anteriores especificadas.
    """
    memoria = db.get(MemoriaComercio, data.memoria_id)
    if not memoria or memoria.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="No encontramos esa memoria de comercio.")

    res = memoria_comercio_service.aplicar_a_anteriores(
        db=db,
        usuario_id=current_user.id,
        memoria=memoria,
        transaccion_ids=data.transaccion_ids,
        commit=True,
    )
    return MemoriaAplicarResponse(**res)


@router.get("", response_model=List[MemoriaComercioRead])
def listar_memorias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Lista las memorias de comercios del usuario con los nombres de categoría y subcategoría.
    """
    stmt = (
        select(MemoriaComercio)
        .options(joinedload(MemoriaComercio.categoria), joinedload(MemoriaComercio.subcategoria))
        .where(MemoriaComercio.usuario_id == current_user.id)
        .order_by(MemoriaComercio.fecha_actualizacion.desc())
    )
    memorias = db.execute(stmt).scalars().all()
    resultado: list[MemoriaComercioRead] = []
    for m in memorias:
        cat_nom = m.categoria.nombre if m.categoria else ""
        sub_nom = m.subcategoria.nombre if m.subcategoria else None
        resultado.append(
            MemoriaComercioRead(
                id=m.id,
                usuario_id=m.usuario_id,
                clave=m.clave,
                tipo=m.tipo,
                categoria_id=m.categoria_id,
                categoria_nombre=cat_nom,
                subcategoria_id=m.subcategoria_id,
                subcategoria_nombre=sub_nom,
                fecha_creacion=m.fecha_creacion,
                fecha_actualizacion=m.fecha_actualizacion,
            )
        )
    return resultado


@router.delete("/{memoria_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_memoria(
    memoria_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Elimina una memoria de comercio del usuario.
    """
    memoria = db.get(MemoriaComercio, memoria_id)
    if not memoria or memoria.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="No encontramos esa memoria de comercio.")

    db.delete(memoria)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
