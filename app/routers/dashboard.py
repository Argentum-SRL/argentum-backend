from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Any

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.services import dashboard_service, proyeccion_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@router.get("/resumen")
async def get_resumen(
    desde: str | None = None,
    hasta: str | None = None,
    billetera_ids: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna el resumen del dashboard para el usuario autenticado.
    """
    from datetime import date
    import uuid
    fecha_desde = date.fromisoformat(desde) if desde else None
    fecha_hasta = date.fromisoformat(hasta) if hasta else None
    ids_lista = [uuid.UUID(i.strip()) for i in billetera_ids.split(',')] if billetera_ids else None
    
    return dashboard_service.get_dashboard_resumen(db, current_user, fecha_desde, fecha_hasta, billetera_ids=ids_lista)

@router.get("/resumen-completo")
async def get_resumen_completo(
    desde: str | None = None,
    hasta: str | None = None,
    billetera_ids: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna billeteras, resumen y cotización en una sola llamada (Optimizado).
    """
    from datetime import date
    import uuid
    fecha_desde = date.fromisoformat(desde) if desde else None
    fecha_hasta = date.fromisoformat(hasta) if hasta else None
    ids_lista = [uuid.UUID(i.strip()) for i in billetera_ids.split(',')] if billetera_ids else None
    
    return await dashboard_service.get_resumen_completo(db, current_user, fecha_desde, fecha_hasta, billetera_ids=ids_lista)

@router.get("/cotizacion")
async def get_cotizacion(
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna la cotizacion del dolar segun la preferencia del usuario.
    """
    return await dashboard_service.get_cotizacion_usuario(current_user)

@router.get("/proyeccion")
def get_proyeccion(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna la proyección financiera para el ciclo actual.
    """
    return proyeccion_service.calcular_proyeccion(db, current_user)

@router.get("/categorias/{categoria_id}/subcategorias")
def get_subcategorias_gasto(
    categoria_id: str,
    billetera_ids: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
) -> Any:
    """
    Retorna los gastos por subcategoría de una categoría específica en el ciclo actual.
    """
    import uuid
    ids_lista = [uuid.UUID(i.strip()) for i in billetera_ids.split(',')] if billetera_ids else None
    return dashboard_service.get_subcategorias_gasto(db, current_user, categoria_id, billetera_ids=ids_lista)

