from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.schemas.tarjeta_credito import (
    TarjetaCreditoCreate, 
    TarjetaCreditoUpdate, 
    TarjetaCreditoResponse,
    ResumenTarjeta,
    PagarTarjetaBody,
    PresionFuturaResponse
)
from app.schemas.transaccion import TransaccionRead
from app.services import tarjeta_service

router = APIRouter()

@router.get("", response_model=list[TarjetaCreditoResponse])
def listar_tarjetas(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.obtener_tarjetas(db, current_user.id)

@router.get("/billetera/{billetera_id}", response_model=list[TarjetaCreditoResponse])
def listar_tarjetas_por_billetera(
    billetera_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.obtener_tarjetas_por_billetera(db, current_user.id, billetera_id)

@router.post("", response_model=TarjetaCreditoResponse, status_code=status.HTTP_201_CREATED)
def crear_tarjeta(
    data: TarjetaCreditoCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.crear_tarjeta(db, current_user.id, data)

@router.put("/{tarjeta_id}", response_model=TarjetaCreditoResponse)
def actualizar_tarjeta(
    tarjeta_id: UUID,
    data: TarjetaCreditoUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.actualizar_tarjeta(db, current_user.id, tarjeta_id, data)

@router.post("/{tarjeta_id}/archivar", response_model=TarjetaCreditoResponse)
def archivar_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.archivar_tarjeta(db, current_user.id, tarjeta_id)

@router.post("/{tarjeta_id}/desarchivar", response_model=TarjetaCreditoResponse)
def desarchivar_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return tarjeta_service.desarchivar_tarjeta(db, current_user.id, tarjeta_id)

@router.delete("/{tarjeta_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    tarjeta_service.eliminar_tarjeta(db, current_user.id, tarjeta_id)
    return None


@router.get("/presion-futura", response_model=PresionFuturaResponse)
def get_presion_futura(
    meses: int = Query(default=6, ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Devuelve el total comprometido en cuotas de tarjeta para los próximos N meses,
    desglosado por tarjeta y agrupado por mes de vencimiento del resumen.
    """
    resultado = tarjeta_service.calcular_presion_futura(db, current_user, meses)
    return {"success": True, "data": resultado}


@router.get("/{tarjeta_id}/resumen", response_model=ResumenTarjeta)
def get_resumen_tarjeta(
    tarjeta_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    tarjeta = db.query(tarjeta_service.TarjetaCredito).filter(
        tarjeta_service.TarjetaCredito.id == tarjeta_id,
        tarjeta_service.TarjetaCredito.usuario_id == current_user.id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="No encontramos esa tarjeta.")

    return tarjeta_service.calcular_resumen_actual(db, tarjeta)


@router.post("/{tarjeta_id}/pagar", response_model=TransaccionRead)
def pagar_tarjeta(
    tarjeta_id: UUID,
    body: PagarTarjetaBody | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    fecha_pago = body.fecha_pago if body else None
    fecha_resumen = body.fecha_resumen if body else None
    return tarjeta_service.pagar_resumen_tarjeta(db, current_user.id, tarjeta_id, fecha_pago, fecha_resumen)

