from typing import List, Optional
from uuid import UUID
from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.models.transaccion import TipoTransaccion
from app.schemas.transaccion import TransaccionCreate, TransaccionUpdate, TransaccionRead
from app.services import transaccion_service

router = APIRouter(prefix="/transacciones", tags=["transacciones"])


@router.get("", response_model=List[TransaccionRead])
def list_transacciones(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    billetera_id: Optional[UUID] = None,
    tipo: Optional[TipoTransaccion] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    categoria_id: Optional[UUID] = None,
    subcategoria_id: Optional[UUID] = None,
    moneda: Optional[str] = None,
    estado_verificacion: Optional[str] = None,
    busqueda: Optional[str] = None,
    es_cuota_hija: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las transacciones del usuario actual con filtros avanzados.
    """
    return transaccion_service.obtener_transacciones(
        db=db,
        usuario_id=current_user.id,
        skip=skip,
        limit=limit,
        billetera_id=billetera_id,
        tipo=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id,
        moneda=moneda,
        estado_verificacion=estado_verificacion,
        busqueda=busqueda,
        es_cuota_hija=es_cuota_hija
    )


@router.get("/pendientes", response_model=List[TransaccionRead])
def get_pendientes_ia(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista todas las transacciones con estado_verificacion='pendiente'.
    """
    return transaccion_service.obtener_pendientes_ia(db, current_user.id)


@router.get("/{transaccion_id}", response_model=TransaccionRead)
def get_transaccion(
    transaccion_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Obtiene el detalle de una transacción específica.
    """
    return transaccion_service.obtener_transaccion(db, current_user.id, transaccion_id)


@router.post("", response_model=TransaccionRead, status_code=status.HTTP_201_CREATED)
def create_transaccion(
    data: TransaccionCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Crea una nueva transacción (normal o padre de cuotas).
    """
    return transaccion_service.crear_transaccion(db, current_user.id, data)


@router.patch("/{transaccion_id}", response_model=TransaccionRead)
def update_transaccion(
    transaccion_id: UUID,
    data: TransaccionUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Actualiza una transacción existente.
    """
    return transaccion_service.actualizar_transaccion(db, current_user.id, transaccion_id, data)


@router.delete("/{transaccion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaccion(
    transaccion_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Elimina una transacción (con cascada para cuotas).
    """
    transaccion_service.eliminar_transaccion(db, current_user.id, transaccion_id)
    return


@router.post("/{transaccion_id}/confirmar", response_model=TransaccionRead)
def confirmar_transaccion(
    transaccion_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Confirma una transacción detectada por IA, impactando el saldo.
    """
    return transaccion_service.confirmar_transaccion_ia(db, current_user.id, transaccion_id)
