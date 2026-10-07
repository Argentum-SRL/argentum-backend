from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.factura import Factura
from app.models.usuario import Usuario
from app.schemas.factura import FacturaRead
from app.services import factura_service
from app.utils.fecha import hoy_argentina

router = APIRouter(prefix="/facturas", tags=["facturas"])


def _to_read(f: Factura) -> FacturaRead:
    hoy = hoy_argentina()
    return FacturaRead(
        id=f.id,
        descripcion=f.descripcion,
        monto=f.monto,
        moneda=f.moneda,
        fecha_vencimiento=f.fecha_vencimiento,
        estado=f.estado,
        pagada_automaticamente=f.pagada_automaticamente,
        transaccion_id=f.transaccion_id,
        categoria_id=f.categoria_id,
        subcategoria_id=f.subcategoria_id,
        categoria_nombre=f.categoria.nombre if f.categoria else None,
        subcategoria_nombre=f.subcategoria.nombre if f.subcategoria else None,
        vencida=(f.estado == "pendiente" and f.fecha_vencimiento < hoy),
    )


@router.get("", response_model=List[FacturaRead])
def listar_facturas(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Lista facturas pendientes y pagadas automáticamente con vencimiento de hoy en adelante,
    ordenadas por fecha_vencimiento ascendente.
    """
    facturas = factura_service.listar_facturas(db, current_user.id)
    return [_to_read(f) for f in facturas]


@router.post("/{id}/desmarcar", response_model=FacturaRead)
def desmarcar_factura(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Desmarca una factura que estaba pagada, devolviéndola a pendiente.
    """
    f = factura_service.desmarcar(db, id, current_user.id, commit=True)
    return _to_read(f)


@router.post("/{id}/descartar", response_model=FacturaRead)
def descartar_factura(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Descarta una factura pendiente.
    """
    f = factura_service.descartar(db, id, current_user.id, commit=True)
    return _to_read(f)
