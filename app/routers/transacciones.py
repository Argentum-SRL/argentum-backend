from typing import List, Optional
from uuid import UUID
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario, Moneda
from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.models.transaccion import TipoTransaccion
from app.schemas.transaccion import TransaccionCreate, TransaccionUpdate, TransaccionRead, ReintegroPayload, GastoParaVincular, QueEsEstaPlataPayload
from app.services import transaccion_service, dolar_service, idempotencia_service, que_es_plata_service
from app.utils.fecha import hoy_argentina

router = APIRouter(prefix="/transacciones", tags=["transacciones"])


@router.get("", response_model=List[TransaccionRead])
def list_transacciones(
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    billetera_id: Optional[UUID] = None,
    tipo: Optional[TipoTransaccion] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    categoria_id: Optional[UUID] = None,
    categoria_ids: Optional[str] = Query(None, description="IDs de categorías separados por coma"),
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
        categoria_ids=parsed_cat_ids,
        subcategoria_id=subcategoria_id,
        moneda=moneda,
        estado_verificacion=estado_verificacion,
        busqueda=busqueda,
        es_cuota_hija=es_cuota_hija
    )


@router.get("/pendientes", response_model=List[TransaccionRead])
def get_pendientes_ia(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista transacciones con estado_verificacion='pendiente' con límites.
    """
    return transaccion_service.obtener_pendientes_ia(db, current_user.id, skip=skip, limit=limit)


@router.get("/sugerencia-pesos")
def sugerencia_pesos(
    monto_usd: Decimal = Query(..., gt=0),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Sugerencia de cotización, percepción y monto en pesos para un gasto en dólares.
    """
    sug = dolar_service.sugerir_pesos_para_usd(db, monto_usd, fecha=hoy_argentina())
    return {
        "cotizacion": float(sug["cotizacion"]) if sug["cotizacion"] is not None else None,
        "percepcion": sug["percepcion"],
        "monto_pesos": float(sug["monto_pesos"]) if sug["monto_pesos"] is not None else None,
        "cotizacion_tarjeta": float(sug["cotizacion_tarjeta"]) if sug["cotizacion_tarjeta"] is not None else None,
    }


@router.get("/gastos-para-vincular", response_model=List[GastoParaVincular])
def gastos_para_vincular(
    moneda: Moneda = Query(...),
    fecha: date = Query(...),
    monto: Decimal = Query(..., gt=0, le=MAX_MONTO_INTEGRIDAD),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Lista gastos candidatos para vincular un ingreso devuelto o aporte de tercero.
    """
    return que_es_plata_service.gastos_para_vincular(
        db=db,
        usuario_id=current_user.id,
        moneda=moneda,
        fecha=fecha,
        monto=monto,
    )


@router.post("/{transaccion_id}/que-es-esta-plata", response_model=TransaccionRead, status_code=status.HTTP_200_OK)
def que_es_esta_plata(
    transaccion_id: UUID,
    request: Request,
    data: QueEsEstaPlataPayload,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Registra la respuesta a «¿Qué es esta plata?» para un ingreso o devolución.
    """
    cuerpo = data.model_dump(mode="json")
    return idempotencia_service.ejecutar(
        db=db,
        request=request,
        usuario_id=current_user.id,
        cuerpo=cuerpo,
        operar=lambda: que_es_plata_service.responder(
            db=db,
            usuario_id=current_user.id,
            transaccion_id=transaccion_id,
            opcion=data.opcion,
            gasto_id=data.gasto_id,
            commit=False,
        ),
        serializar=lambda obj: TransaccionRead.model_validate(obj).model_dump(mode="json"),
        codigo=status.HTTP_200_OK,
    )


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
    request: Request,
    data: TransaccionCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Crea una nueva transacción (normal o padre de cuotas).
    """
    cuerpo = data.model_dump(mode="json")
    return idempotencia_service.ejecutar(
        db=db,
        request=request,
        usuario_id=current_user.id,
        cuerpo=cuerpo,
        operar=lambda: transaccion_service.crear_transaccion(db, current_user.id, data, commit=False),
        serializar=lambda obj: TransaccionRead.model_validate(obj).model_dump(mode="json"),
        codigo=status.HTTP_201_CREATED,
    )


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


@router.delete("/{transaccion_id}/cuota", status_code=status.HTTP_204_NO_CONTENT)
def delete_cuota_individual(
    transaccion_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Elimina exclusivamente una cuota pendiente individual sin tocar el resto del grupo.
    """
    from app.services.cuotas_service import eliminar_cuota_individual
    eliminar_cuota_individual(db, current_user.id, transaccion_id)
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


@router.put("/{transaccion_id}/reintegro", response_model=TransaccionRead)
def registrar_reintegro(
    transaccion_id: UUID,
    data: ReintegroPayload,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Registra o actualiza el reintegro de una compra (D6).
    """
    from app.services import reintegro_service
    return reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=current_user.id,
        transaccion_id=transaccion_id,
        monto=data.monto,
        recibido=data.recibido,
        fecha_recibido=data.fecha_recibido,
    )


@router.delete("/{transaccion_id}/reintegro", response_model=TransaccionRead)
def eliminar_reintegro(
    transaccion_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Elimina el reintegro de una compra (D6).
    """
    from app.services import reintegro_service
    return reintegro_service.eliminar_reintegro(
        db=db,
        usuario_id=current_user.id,
        transaccion_id=transaccion_id,
    )

