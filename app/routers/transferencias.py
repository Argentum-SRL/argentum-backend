from typing import List
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.core.database import get_db
from app.models.usuario import Usuario, Moneda
from app.schemas.transferencia_interna import (
    SimularTransferenciaResponse,
    TransferenciaInternaCreate,
    TransferenciaInternaRead,
)
from app.services import transferencia_service, idempotencia_service

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


@router.get("/simular", response_model=SimularTransferenciaResponse)
def simular_transferencia(
    monto: Decimal = Query(..., gt=0, le=MAX_MONTO_INTEGRIDAD),
    cotizacion: Decimal = Query(..., gt=0),
    moneda_origen: Moneda = Query(...),
    moneda_destino: Moneda = Query(...),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Simula el monto de destino para una transferencia con cambio de moneda.
    """
    try:
        monto_destino = transferencia_service.calcular_monto_destino(
            monto_origen=monto,
            cotizacion=cotizacion,
            moneda_origen=moneda_origen,
            moneda_destino=moneda_destino,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return SimularTransferenciaResponse(
        monto_destino=monto_destino,
        cotizacion=cotizacion,
    )


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
    request: Request,
    data: TransferenciaInternaCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Crea una transferencia interna entre dos billeteras.
    """
    cuerpo = data.model_dump(mode="json")
    return idempotencia_service.ejecutar(
        db=db,
        request=request,
        usuario_id=current_user.id,
        cuerpo=cuerpo,
        operar=lambda: transferencia_service.crear_transferencia(db, current_user.id, data, commit=False),
        serializar=lambda obj: TransferenciaInternaRead.model_validate(obj).model_dump(mode="json"),
        codigo=status.HTTP_201_CREATED,
    )


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
