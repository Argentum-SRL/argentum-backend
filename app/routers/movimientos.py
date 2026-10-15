from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.transaccion import TipoTransaccion
from app.models.usuario import Usuario
from app.schemas.movimiento import MovimientosResponse
from app.services import historial_service

router = APIRouter(prefix="/movimientos", tags=["movimientos"])


@router.get("", response_model=MovimientosResponse)
def get_movimientos(
    modo: str = Query("general", pattern="^(general|billetera)$", description="Modo general o detalle de billetera"),
    billetera_id: Optional[UUID] = Query(None, description="Filtrar por ID de billetera"),
    tipo: Optional[TipoTransaccion] = Query(None, description="Filtrar por ingreso o egreso"),
    fecha_desde: Optional[date] = Query(None, description="Fecha de inicio (inclusiva)"),
    fecha_hasta: Optional[date] = Query(None, description="Fecha de fin (inclusiva)"),
    categoria_id: Optional[UUID] = Query(None, description="ID de categoría principal"),
    categoria_ids: Optional[str] = Query(None, description="IDs de categorías separados por coma"),
    subcategoria_id: Optional[UUID] = Query(None, description="ID de subcategoría"),
    moneda: Optional[str] = Query(None, description="Filtrar por moneda ('ARS', 'USD')"),
    estado_verificacion: Optional[str] = Query(None, description="Estado de verificación"),
    busqueda: Optional[str] = Query(None, description="Término de búsqueda de texto"),
    skip: int = Query(0, ge=0, description="Cantidad de registros a omitir"),
    limit: int = Query(100, ge=1, le=5000, description="Cantidad máxima de registros a retornar"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Retorna el historial unificado de movimientos financieros del usuario.
    Permite consultar en modo general o modo billetera con totales consolidados y paginación.
    """
    parsed_cat_ids: Optional[List[UUID]] = None
    if categoria_ids:
        parsed_cat_ids = []
        for raw_id in categoria_ids.split(","):
            raw_id = raw_id.strip()
            if raw_id:
                try:
                    parsed_cat_ids.append(UUID(raw_id))
                except ValueError:
                    pass

    return historial_service.listar_movimientos(
        db=db,
        usuario_id=current_user.id,
        modo=modo,
        billetera_id=billetera_id,
        tipo=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        categoria_id=categoria_id,
        categoria_ids=parsed_cat_ids,
        subcategoria_id=subcategoria_id,
        moneda=moneda,
        estado_verificacion=estado_verificacion,
        busqueda=busqueda,
        skip=skip,
        limit=limit,
    )
