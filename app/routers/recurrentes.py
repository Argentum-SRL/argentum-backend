from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.models.transaccion_recurrente import EstadoTransaccionRecurrente
from app.schemas.transaccion_recurrente import TransaccionRecurrenteCreate, TransaccionRecurrenteUpdate, TransaccionRecurrenteRead
from app.services import recurrente_service

router = APIRouter(prefix="/recurrentes", tags=["recurrentes"])


@router.get("", response_model=List[TransaccionRecurrenteRead])
def list_recurrentes(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las transacciones recurrentes del usuario.
    """
    return recurrente_service.obtener_recurrentes(db, current_user.id)


@router.get("/{recurrente_id}", response_model=TransaccionRecurrenteRead)
def get_recurrente(
    recurrente_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Obtiene el detalle de una recurrente.
    """
    return recurrente_service.obtener_recurrente(db, current_user.id, recurrente_id)


@router.post("", response_model=TransaccionRecurrenteRead, status_code=status.HTTP_201_CREATED)
def create_recurrente(
    data: TransaccionRecurrenteCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Crea una plantilla de transacción recurrente.
    """
    return recurrente_service.crear_recurrente(db, current_user.id, data)


@router.patch("/{recurrente_id}", response_model=TransaccionRecurrenteRead)
def update_recurrente(
    recurrente_id: UUID,
    data: TransaccionRecurrenteUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Actualiza una recurrente.
    """
    return recurrente_service.actualizar_recurrente(db, current_user.id, recurrente_id, data)


@router.delete("/{recurrente_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recurrente(
    recurrente_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Elimina una recurrente.
    """
    recurrente_service.eliminar_recurrente(db, current_user.id, recurrente_id)
    return


@router.post("/{recurrente_id}/pausar", response_model=TransaccionRecurrenteRead)
def pausar_recurrente(
    recurrente_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Pausa una recurrente.
    """
    return recurrente_service.cambiar_estado_recurrente(db, current_user.id, recurrente_id, EstadoTransaccionRecurrente.PAUSADA)


@router.post("/{recurrente_id}/reanudar", response_model=TransaccionRecurrenteRead)
def reanudar_recurrente(
    recurrente_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Reanuda una recurrente.
    """
    return recurrente_service.cambiar_estado_recurrente(db, current_user.id, recurrente_id, EstadoTransaccionRecurrente.ACTIVA)
