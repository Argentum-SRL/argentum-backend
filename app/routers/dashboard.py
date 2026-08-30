import uuid
from datetime import date
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.services import dashboard_service, proyeccion_service
from app.schemas.dashboard import (
    DashboardResumenResponse,
    ResumenCompletoResponse,
    CotizacionDolarResponse,
    ProyeccionesResponse,
    SubcategoriaGastoResponse,
    PeriodoActualResponse
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _parse_billetera_ids(billetera_ids: str | None) -> list[uuid.UUID] | None:
    if not billetera_ids or not billetera_ids.strip():
        return None
    try:
        return [uuid.UUID(i.strip()) for i in billetera_ids.split(',') if i.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de ID de billetera inválido")


def _parse_date(date_str: str | None, param_name: str) -> date | None:
    if not date_str or not date_str.strip():
        return None
    try:
        return date.fromisoformat(date_str.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Formato de fecha inválido para '{param_name}' (se requiere YYYY-MM-DD)")


def _validate_date_range(desde: str | None, hasta: str | None) -> tuple[date | None, date | None]:
    fecha_desde = _parse_date(desde, "desde")
    fecha_hasta = _parse_date(hasta, "hasta")
    if (fecha_desde and not fecha_hasta) or (fecha_hasta and not fecha_desde):
        raise HTTPException(
            status_code=400,
            detail="Debes especificar ambas fechas ('desde' y 'hasta') para consultar un rango personalizado."
        )
    if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        raise HTTPException(
            status_code=400,
            detail="La fecha de inicio ('desde') no puede ser posterior a la fecha de fin ('hasta')."
        )
    return fecha_desde, fecha_hasta


def _validate_billetera_ids(db: Session, user_id: uuid.UUID, billetera_ids: str | None) -> list[uuid.UUID] | None:
    ids_lista = _parse_billetera_ids(billetera_ids)
    if not ids_lista:
        return None
    from app.models.billetera import Billetera
    from sqlalchemy import select
    valid_ids = set(
        db.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == user_id,
                Billetera.id.in_(ids_lista)
            )
        ).scalars().all()
    )
    invalid = [str(i) for i in ids_lista if i not in valid_ids]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail="Una o más billeteras seleccionadas no pertenecen a tu cuenta o no existen."
        )
    return ids_lista


@router.get("/resumen", response_model=DashboardResumenResponse)
def get_resumen(
    desde: str | None = None,
    hasta: str | None = None,
    billetera_ids: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna el resumen del dashboard para el usuario autenticado.
    """
    fecha_desde, fecha_hasta = _validate_date_range(desde, hasta)
    ids_lista = _validate_billetera_ids(db, current_user.id, billetera_ids)
    
    return dashboard_service.get_dashboard_resumen(db, current_user, fecha_desde, fecha_hasta, billetera_ids=ids_lista)


@router.get("/resumen-completo", response_model=ResumenCompletoResponse)
def get_resumen_completo(
    desde: str | None = None,
    hasta: str | None = None,
    billetera_ids: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna billeteras, resumen y cotización en una sola llamada (Optimizado).
    """
    fecha_desde, fecha_hasta = _validate_date_range(desde, hasta)
    ids_lista = _validate_billetera_ids(db, current_user.id, billetera_ids)
    
    return dashboard_service.get_resumen_completo(db, current_user, fecha_desde, fecha_hasta, billetera_ids=ids_lista)


@router.get("/cotizacion", response_model=CotizacionDolarResponse)
def get_cotizacion(
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna la cotizacion del dolar segun la preferencia del usuario.
    """
    return dashboard_service.get_cotizacion_usuario(current_user)


@router.get("/proyeccion", response_model=ProyeccionesResponse)
def get_proyeccion(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna la proyección financiera para el ciclo actual.
    """
    return proyeccion_service.calcular_proyeccion(db, current_user)


@router.get("/categorias/{categoria_id}/subcategorias", response_model=List[SubcategoriaGastoResponse])
def get_subcategorias_gasto(
    categoria_id: str,
    billetera_ids: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna los gastos por subcategoría de una categoría específica en el ciclo actual.
    """
    ids_lista = _validate_billetera_ids(db, current_user.id, billetera_ids)
    return dashboard_service.get_subcategorias_gasto(db, current_user, categoria_id, billetera_ids=ids_lista)


@router.get("/periodo-actual", response_model=PeriodoActualResponse)
def get_periodo_actual(
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna el rango de fechas (fecha_inicio, fecha_fin) del ciclo financiero actual del usuario autenticado.
    """
    from app.utils.fecha import hoy_argentina
    fecha_inicio, fecha_fin = dashboard_service.get_ciclo_fechas(current_user, hoy_argentina())
    return {
        "fecha_inicio": fecha_inicio.isoformat(),
        "fecha_fin": fecha_fin.isoformat()
    }



