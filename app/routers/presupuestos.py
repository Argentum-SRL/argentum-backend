from uuid import UUID
from typing import List, Optional
from datetime import timedelta
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.limiter import limiter
from app.models.usuario import Usuario
from app.schemas.presupuesto import (
    PresupuestoCreate, 
    PresupuestoUpdate, 
    PresupuestoResponse, 
    PeriodoPresupuestoResponse,
    PresupuestoCategoriaResponse,
    RestaurarPresupuestoRequest
)
from app.services import presupuesto_service
from app.services.uso_service import registrar_evento
from app.services.presupuestos_excel_datos import armar_datos_excel, nombre_archivo
from app.services.presupuestos_excel_libro import armar_libro
from app.utils.fecha import hoy_argentina

router = APIRouter(tags=["presupuestos"])

def _map_presupuesto_response(db: Session, p) -> PresupuestoResponse:
    periodo_actual, uso = presupuesto_service.uso_actual(db, p)
    
    periodo_actual_resp = None
    if periodo_actual and uso:
        dias_restantes = max(0, (periodo_actual.fecha_fin - hoy_argentina()).days)
        
        periodo_actual_resp = PeriodoPresupuestoResponse(
            id=periodo_actual.id,
            presupuesto_id=periodo_actual.presupuesto_id,
            fecha_inicio=periodo_actual.fecha_inicio,
            fecha_fin=periodo_actual.fecha_fin,
            monto_limite=periodo_actual.monto_limite,
            monto_usado=uso.mostrado,
            superado=uso.supera_limite,
            porcentaje_usado=uso.porcentaje,
            dias_restantes=dias_restantes
        )

    categorias_resp = []
    for pc in p.categorias:
        nombre = pc.subcategoria.nombre if pc.subcategoria else (pc.categoria.nombre if pc.categoria else "")
        cat_id = pc.categoria_id or (pc.subcategoria.categoria_id if pc.subcategoria else None)
        categorias_resp.append(PresupuestoCategoriaResponse(
            categoria_id=cat_id,
            subcategoria_id=pc.subcategoria_id,
            nombre=nombre,
            es_subcategoria=pc.subcategoria_id is not None
        ))

    # Próxima renovación: fecha_fin + 1 día del periodo actual
    proxima = (periodo_actual.fecha_fin + timedelta(days=1)) if periodo_actual else None

    moneda_val = p.moneda.value if hasattr(p.moneda, "value") else str(p.moneda)
    periodo_val = p.periodo.value if hasattr(p.periodo, "value") else str(p.periodo)
    renovacion_val = p.renovacion.value if hasattr(p.renovacion, "value") else str(p.renovacion)
    estado_val = p.estado.value if hasattr(p.estado, "value") else str(p.estado)

    return PresupuestoResponse(
        id=p.id,
        usuario_id=p.usuario_id,
        nombre=p.nombre,
        monto=p.monto,
        moneda=moneda_val,
        periodo=periodo_val,
        renovacion=renovacion_val,
        estado=estado_val,
        fecha_creacion=p.fecha_creacion,
        categorias=categorias_resp,
        periodo_actual=periodo_actual_resp,
        proxima_renovacion=proxima
    )

@router.get("", response_model=List[PresupuestoResponse])
def listar_presupuestos(
    estado: Optional[str] = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    presupuestos = presupuesto_service.obtener_presupuestos(db, usuario.id, estado)
    return [_map_presupuesto_response(db, p) for p in presupuestos]

@router.post("", response_model=PresupuestoResponse)
def crear_presupuesto(
    data: PresupuestoCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.crear_presupuesto(db, usuario.id, data)
    return _map_presupuesto_response(db, p)

@router.get("/exportar-excel")
@limiter.limit("3/minute")
def exportar_excel(
    request: Request,
    alcance: str = Query("12"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    if alcance not in ("12", "todo"):
        return JSONResponse(status_code=422, content={"detail": "El alcance tiene que ser 12 o todo."})

    hoy = hoy_argentina()
    datos = armar_datos_excel(db, usuario, alcance, hoy)
    if datos is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Todavía no tenés gastos ni ingresos para armar el Excel."},
        )

    contenido_excel = armar_libro(datos)
    registrar_evento(db, usuario.id, "descargar_presupuestos")
    nombre = nombre_archivo(datos)
    return Response(
        content=contenido_excel,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/{id}", response_model=PresupuestoResponse)
def obtener_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.obtener_presupuesto(db, usuario.id, id)
    return _map_presupuesto_response(db, p)

@router.put("/{id}", response_model=PresupuestoResponse)
def actualizar_presupuesto(
    id: UUID,
    data: PresupuestoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.actualizar_presupuesto(db, usuario.id, id, data)
    return _map_presupuesto_response(db, p)

@router.post("/{id}/pausar", response_model=PresupuestoResponse)
def pausar_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.pausar_presupuesto(db, usuario.id, id)
    return _map_presupuesto_response(db, p)

@router.post("/{id}/reanudar", response_model=PresupuestoResponse)
def reanudar_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.reanudar_presupuesto(db, usuario.id, id)
    return _map_presupuesto_response(db, p)

@router.post("/{id}/renovar", response_model=PresupuestoResponse)
def renovar_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.renovar_presupuesto_manual(db, usuario.id, id)
    return _map_presupuesto_response(db, p)

@router.delete("/{id}")
def eliminar_presupuesto(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    presupuesto_service.eliminar_presupuesto(db, usuario.id, id)
    return {"detail": "Presupuesto eliminado."}

@router.post("/{id}/restaurar", response_model=PresupuestoResponse)
def restaurar_presupuesto(
    id: UUID,
    data: RestaurarPresupuestoRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    p = presupuesto_service.restaurar_presupuesto(db, usuario.id, id, data.estado)
    return _map_presupuesto_response(db, p)

@router.get("/{id}/historial", response_model=List[PeriodoPresupuestoResponse])
def obtener_historial(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user)
):
    presu = presupuesto_service.obtener_presupuesto(db, usuario.id, id)
    periodos = presupuesto_service.obtener_historial(db, usuario.id, id)
    hoy = hoy_argentina()
    resp = []
    for p in periodos:
        dias_restantes = max(0, (p.fecha_fin - hoy).days)
        uso = presupuesto_service.uso_periodo(db, presu, p, hoy=hoy, usuario=presu.usuario)
        resp.append(PeriodoPresupuestoResponse(
            id=p.id,
            presupuesto_id=p.presupuesto_id,
            fecha_inicio=p.fecha_inicio,
            fecha_fin=p.fecha_fin,
            monto_limite=p.monto_limite,
            monto_usado=uso.mostrado,
            superado=uso.supera_limite,
            porcentaje_usado=uso.porcentaje,
            dias_restantes=dias_restantes
        ))
    return resp
