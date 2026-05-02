from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.schemas.transferencia_interna import TransferenciaInternaCreate, TransferenciaInternaRead
from app.services import transferencia_service

router = APIRouter(prefix="/transferencias", tags=["transferencias"])


@router.get("", response_model=List[TransferenciaInternaRead])
def list_transferencias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las transferencias internas del usuario.
    """
    return transferencia_service.obtener_transferencias(db, current_user.id)


@router.get("/{transferencia_id}", response_model=TransferenciaInternaRead)
def get_transferencia(
    transferencia_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Obtiene el detalle de una transferencia específica.
    """
    return transferencia_service.obtener_transferencia(db, current_user.id, transferencia_id)


@router.post("", response_model=TransferenciaInternaRead, status_code=status.HTTP_201_CREATED)
def create_transferencia(
    data: TransferenciaInternaCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Crea una transferencia interna entre dos billeteras.
    """
    return transferencia_service.crear_transferencia(db, current_user.id, data)


@router.delete("/{transferencia_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transferencia(
    transferencia_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Elimina una transferencia interna y revierte los saldos.
    """
    transferencia_service.eliminar_transferencia(db, current_user.id, transferencia_id)
    return
