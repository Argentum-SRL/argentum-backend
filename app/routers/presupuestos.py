from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from datetime import date

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.schemas.presupuesto import (
    PresupuestoCreate, 
    PresupuestoUpdate, 
    PresupuestoResponse, 
    PeriodoPresupuestoResponse,
    PresupuestoCategoriaResponse
)
from app.services import presupuesto_service

router = APIRouter(tags=["presupuestos"])

def _map_presupuesto_response(p) -> PresupuestoResponse:
    periodo_actual = presupuesto_service.obtener_periodo_activo(None, p)
    
    periodo_actual_resp = None
    if periodo_actual:
        dias_restantes = (periodo_actual.fecha_fin - date.today()).days
        porcentaje_usado = float((periodo_actual.monto_usado / periodo_actual.monto_limite) * 100) if periodo_actual.monto_limite > 0 else 0
        
        periodo_actual_resp = PeriodoPresupuestoResponse(
            id=periodo_actual.id,
            presupuesto_id=periodo_actual.presupuesto_id,
            fecha_inicio=periodo_actual.fecha_inicio,
            fecha_fin=periodo_actual.fecha_fin,
            monto_limite=periodo_actual.monto_limite,
            monto_usado=periodo_actual.monto_usado,
            superado=periodo_actual.superado,
            porcentaje_usado=porcentaje_usado,
            dias_restantes=dias_restantes
        )

    categorias_resp = []
    for pc in p.categorias:
        nombre = pc.subcategoria.nombre if pc.subcategoria else pc.categoria.nombre
        categorias_resp.append(PresupuestoCategoriaResponse(
            categoria_id=pc.categoria_id,
            subcategoria_id=pc.subcategoria_id,
            nombre=nombre,
            es_subcategoria=pc.subcategoria_id is not None
        ))

    # Próxima renovación
    # Si es mensual, el 1 del mes que viene. Si es quincenal, el 16 o el 1 del mes que viene.
    # En realidad, es fecha_fin + 1 día del periodo actual.
    proxima = (periodo_actual.fecha_fin + timedelta(days=1)) if periodo_actual else None

    return PresupuestoResponse(
        id=p.id,
        usuario_id=p.usuario_id,
        nombre=p.nombre,
        monto=p.monto,
        moneda=p.moneda.value,
        periodo=p.periodo.value,
        renovacion=p.renovacion.value,
        estado=p.estado.value,
        fecha_creacion=p.fecha_creacion,
        categorias=categorias_resp,
        periodo_actual=periodo_actual_resp,
        proxima_renovacion=proxima
    )

from datetime import timedelta

@router.get("/", response_model=List[PresupuestoResponse])
def listar_presupuestos(
    estado: Optional[str] = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    presupuestos = presupuesto_service.obtener_presupuestos(db, usuario.id, estado)
    return [_map_presupuesto_response(p) for p in presupuestos]

@router.post("/", response_model=PresupuestoResponse)
def crear_presupuesto(
    data: PresupuestoCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.crear_presupuesto(db, usuario.id, data)
    return _map_presupuesto_response(p)

@router.get("/{id}", response_model=PresupuestoResponse)
def obtener_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.obtener_presupuesto(db, usuario.id, id)
    return _map_presupuesto_response(p)

@router.put("/{id}", response_model=PresupuestoResponse)
def actualizar_presupuesto(
    id: UUID,
    data: PresupuestoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.actualizar_presupuesto(db, usuario.id, id, data)
    return _map_presupuesto_response(p)

@router.post("/{id}/pausar", response_model=PresupuestoResponse)
def pausar_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.pausar_presupuesto(db, usuario.id, id)
    return _map_presupuesto_response(p)

@router.post("/{id}/reanudar", response_model=PresupuestoResponse)
def reanudar_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.reanudar_presupuesto(db, usuario.id, id)
    return _map_presupuesto_response(p)

@router.delete("/{id}")
def eliminar_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    presupuesto_service.eliminar_presupuesto(db, usuario.id, id)
    return {"detail": "Presupuesto finalizado correctamente"}

@router.get("/{id}/historial", response_model=List[PeriodoPresupuestoResponse])
def obtener_historial(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    periodos = presupuesto_service.obtener_historial(db, usuario.id, id)
    resp = []
    for p in periodos:
        dias_restantes = (p.fecha_fin - date.today()).days
        porcentaje_usado = float((p.monto_usado / p.monto_limite) * 100) if p.monto_limite > 0 else 0
        resp.append(PeriodoPresupuestoResponse(
            id=p.id,
            presupuesto_id=p.presupuesto_id,
            fecha_inicio=p.fecha_inicio,
            fecha_fin=p.fecha_fin,
            monto_limite=p.monto_limite,
            monto_usado=p.monto_usado,
            superado=p.superado,
            porcentaje_usado=porcentaje_usado,
            dias_restantes=dias_restantes
        ))
    return resp
