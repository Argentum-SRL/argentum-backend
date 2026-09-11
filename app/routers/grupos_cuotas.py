from uuid import UUID
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, joinedload

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, MetodoPago
from app.models.billetera import Billetera
from app.schemas.grupos_cuotas import GrupoCuotasResumen, GrupoCuotasUpdate
from app.services.transaccion_service import _hoy_argentina

router = APIRouter(prefix="/grupos-cuotas", tags=["grupos_cuotas"])

def mapear_grupo_resumen(db: Session, grupo: GrupoCuotas) -> dict:
    cuotas = grupo.cuotas
    pagadas = [c for c in cuotas if c.pagada]
    pendientes = [c for c in cuotas if not c.pagada]
    
    cantidad_pagadas = len(pagadas)
    cantidad_pendientes = len(pendientes)
    
    proximo_vencimiento = None
    if pendientes:
        proximo_vencimiento = min(c.fecha_vencimiento for c in pendientes)
        
    if pendientes:
        pendientes_sorted = sorted(pendientes, key=lambda c: c.numero_cuota)
        monto_cuota = pendientes_sorted[0].monto_proyectado
    elif pagadas:
        pagadas_sorted = sorted(pagadas, key=lambda c: c.numero_cuota)
        monto_cuota = pagadas_sorted[-1].monto_proyectado
    else:
        monto_cuota = Decimal(0)
        
    total_pagado = sum(c.monto_proyectado for c in pagadas)
    total_pendiente = sum(c.monto_proyectado for c in pendientes)
    
    tarjeta_nombre = grupo.tarjeta.nombre if grupo.tarjeta else None

    categoria_id = None
    subcategoria_id = None
    if pendientes:
        pendientes_sorted = sorted(pendientes, key=lambda c: c.numero_cuota)
        if pendientes_sorted[0].transaccion:
            categoria_id = pendientes_sorted[0].transaccion.categoria_id
            subcategoria_id = pendientes_sorted[0].transaccion.subcategoria_id
    elif pagadas:
        pagadas_sorted = sorted(pagadas, key=lambda c: c.numero_cuota)
        if pagadas_sorted[-1].transaccion:
            categoria_id = pagadas_sorted[-1].transaccion.categoria_id
            subcategoria_id = pagadas_sorted[-1].transaccion.subcategoria_id
    elif grupo.transaccion_padre:
        categoria_id = grupo.transaccion_padre.categoria_id
        subcategoria_id = grupo.transaccion_padre.subcategoria_id
    
    return {
        "id": grupo.id,
        "descripcion": grupo.descripcion,
        "monto_total": grupo.monto_total,
        "total_financiado": grupo.total_financiado,
        "cantidad_cuotas": grupo.cantidad_cuotas,
        "cantidad_pagadas": cantidad_pagadas,
        "cantidad_pendientes": cantidad_pendientes,
        "monto_cuota": monto_cuota,
        "proximo_vencimiento": proximo_vencimiento,
        "total_pagado": total_pagado,
        "total_pendiente": total_pendiente,
        "moneda": grupo.moneda.value,
        "tarjeta_nombre": tarjeta_nombre,
        "fecha_compra": grupo.transaccion_padre.fecha if grupo.transaccion_padre else _hoy_argentina(),
        "transaccion_padre_id": grupo.transaccion_padre_id,
        "tiene_interes": grupo.tiene_interes,
        "tasa_interes": grupo.tasa_interes,
        "estado": grupo.estado.value if hasattr(grupo.estado, "value") else str(grupo.estado),
        "categoria_id": categoria_id,
        "subcategoria_id": subcategoria_id
    }

@router.get("", response_model=list[GrupoCuotasResumen])
def get_grupos_cuotas(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    stmt = (
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas).joinedload(Cuota.transaccion),
            joinedload(GrupoCuotas.transaccion_padre),
            joinedload(GrupoCuotas.tarjeta)
        )
        .where(GrupoCuotas.usuario_id == current_user.id)
    )
    grupos = db.execute(stmt).scalars().all()
    
    resumenes = []
    for g in grupos:
        resumenes.append(mapear_grupo_resumen(db, g))
        
    def get_sort_key(res):
        val = res["proximo_vencimiento"]
        if val is None:
            return (1, date.max)
        return (0, val)
        
    resumenes.sort(key=get_sort_key)
    return resumenes

@router.patch("/{grupo_id}", response_model=GrupoCuotasResumen)
def update_grupo_cuotas(
    grupo_id: UUID,
    data: GrupoCuotasUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    grupo = db.execute(
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas).joinedload(Cuota.transaccion),
            joinedload(GrupoCuotas.transaccion_padre),
            joinedload(GrupoCuotas.tarjeta)
        )
        .where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == current_user.id)
    ).scalar_one_or_none()
    
    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo de cuotas.")
        
    from app.services.cuotas_service import actualizar_grupo
    grupo = actualizar_grupo(db, grupo, data)
    
    return mapear_grupo_resumen(db, grupo)

@router.delete("/{grupo_id}")
def delete_grupo_cuotas(
    grupo_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    grupo = db.execute(
        select(GrupoCuotas)
        .where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == current_user.id)
    ).scalar_one_or_none()
    
    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo de cuotas.")
        
    padre_id = grupo.transaccion_padre_id
    
    from app.services.transaccion_service import eliminar_transaccion
    eliminar_transaccion(db, current_user.id, padre_id)
    
    db.commit()
    return {"detail": "Grupo eliminado"}


from pydantic import BaseModel
from typing import Optional
from app.services.cuotas_service import cancelar_grupo, prepagar_grupo

class PrepagoDatos(BaseModel):
    billetera_id: UUID
    categoria_id: Optional[UUID] = None

@router.post("/{grupo_id}/cancelar", response_model=GrupoCuotasResumen)
def cancelar_grupo_cuotas(
    grupo_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    grupo = cancelar_grupo(db, grupo_id, current_user.id)
    return mapear_grupo_resumen(db, grupo)

@router.post("/{grupo_id}/prepagar", response_model=GrupoCuotasResumen)
def prepagar_grupo_cuotas(
    grupo_id: UUID,
    datos: PrepagoDatos,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    grupo = prepagar_grupo(db, grupo_id, current_user.id, datos.billetera_id, datos.categoria_id)
    return mapear_grupo_resumen(db, grupo)
