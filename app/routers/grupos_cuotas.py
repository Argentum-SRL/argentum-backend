from uuid import UUID
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, joinedload

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, MetodoPago
from app.models.billetera import Billetera
from app.schemas.grupos_cuotas import GrupoCuotasResumen, GrupoCuotasUpdate
from app.services.transaccion_service import _hoy_argentina

router = APIRouter(prefix="/grupos-cuotas", tags=["grupos_cuotas"])

def mapear_grupo_resumen(db: Session, grupo: GrupoCuotas) -> dict:
    cuotas = grupo.cuotas
    pagadas = [c for c in cuotas if c.pagada]
    pendientes = [c for c in cuotas if not c.pagada]
    
    cantidad_pagadas = len(pagadas)
    cantidad_pendientes = len(pendientes)
    
    proximo_vencimiento = None
    if pendientes:
        proximo_vencimiento = min(c.fecha_vencimiento for c in pendientes)
        
    if pendientes:
        pendientes_sorted = sorted(pendientes, key=lambda c: c.numero_cuota)
        monto_cuota = pendientes_sorted[0].monto_proyectado
    elif pagadas:
        pagadas_sorted = sorted(pagadas, key=lambda c: c.numero_cuota)
        monto_cuota = pagadas_sorted[-1].monto_proyectado
    else:
        monto_cuota = Decimal(0)
        
    total_pagado = sum(c.monto_proyectado for c in pagadas)
    total_pendiente = sum(c.monto_proyectado for c in pendientes)
    
    tarjeta_nombre = grupo.tarjeta.nombre if grupo.tarjeta else None
    
    return {
        "id": grupo.id,
        "descripcion": grupo.descripcion,
        "monto_total": grupo.monto_total,
        "total_financiado": grupo.total_financiado,
        "cantidad_cuotas": grupo.cantidad_cuotas,
        "cantidad_pagadas": cantidad_pagadas,
        "cantidad_pendientes": cantidad_pendientes,
        "monto_cuota": monto_cuota,
        "proximo_vencimiento": proximo_vencimiento,
        "total_pagado": total_pagado,
        "total_pendiente": total_pendiente,
        "moneda": grupo.moneda.value,
        "tarjeta_nombre": tarjeta_nombre,
        "fecha_compra": grupo.transaccion_padre.fecha if grupo.transaccion_padre else _hoy_argentina(),
        "transaccion_padre_id": grupo.transaccion_padre_id,
        "tiene_interes": grupo.tiene_interes,
        "tasa_interes": grupo.tasa_interes
    }

@router.get("", response_model=list[GrupoCuotasResumen])
def get_grupos_cuotas(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    stmt = (
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas).joinedload(Cuota.transaccion),
            joinedload(GrupoCuotas.transaccion_padre),
            joinedload(GrupoCuotas.tarjeta)
        )
        .where(GrupoCuotas.usuario_id == current_user.id)
    )
    grupos = db.execute(stmt).scalars().all()
    
    resumenes = []
    for g in grupos:
        resumenes.append(mapear_grupo_resumen(db, g))
        
    def get_sort_key(res):
        val = res["proximo_vencimiento"]
        if val is None:
            return (1, date.max)
        return (0, val)
        
    resumenes.sort(key=get_sort_key)
    return resumenes

@router.patch("/{grupo_id}", response_model=GrupoCuotasResumen)
def update_grupo_cuotas(
    grupo_id: UUID,
    data: GrupoCuotasUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    grupo = db.execute(
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas).joinedload(Cuota.transaccion),
            joinedload(GrupoCuotas.transaccion_padre),
            joinedload(GrupoCuotas.tarjeta)
        )
        .where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == current_user.id)
    ).scalar_one_or_none()
    
    if not grupo:
        raise HTTPException(status_code=404, detail="Grupo de cuotas no encontrado")
        
    hoy = _hoy_argentina()
    
    if data.descripcion is not None:
        grupo.descripcion = data.descripcion
        if grupo.transaccion_padre:
            grupo.transaccion_padre.descripcion = data.descripcion
            
        for c in grupo.cuotas:
            tx_hija = c.transaccion
            if tx_hija:
                if " (Cuota" in tx_hija.descripcion:
                    parts = tx_hija.descripcion.split(" (Cuota")
                    suffix = " (Cuota" + parts[-1]
                    tx_hija.descripcion = f"{data.descripcion}{suffix}"
                else:
                    tx_hija.descripcion = f"{data.descripcion} (Cuota {c.numero_cuota}/{grupo.cantidad_cuotas})"
                    
    if data.monto_total_nuevo is not None:
        pagadas = [c for c in grupo.cuotas if c.pagada]
        pendientes = [c for c in grupo.cuotas if not c.pagada]
        
        total_ya_pagado = sum(c.monto_proyectado for c in pagadas)
        monto_pendiente = data.monto_total_nuevo - total_ya_pagado
        
        if monto_pendiente <= 0:
            raise HTTPException(
                status_code=400,
                detail="El monto nuevo es menor o igual a lo que ya pagaste. No podés reducir el monto a menos de lo ya abonado."
            )
            
        cantidad_pendientes = len(pendientes)
        if cantidad_pendientes == 0:
            raise HTTPException(
                status_code=400,
                detail="Ya pagaste todas las cuotas. No hay nada que ajustar."
            )
            
        nuevo_monto_base = round(monto_pendiente / cantidad_pendientes, 2)
        total_con_base = nuevo_monto_base * cantidad_pendientes
        diferencia = monto_pendiente - total_con_base
        
        pendientes_ordenadas = sorted(pendientes, key=lambda c: c.numero_cuota)
        
        for idx, c in enumerate(pendientes_ordenadas):
            is_last = (idx == len(pendientes_ordenadas) - 1)
            monto_actual_cuota = nuevo_monto_base + diferencia if is_last else nuevo_monto_base
            
            old_monto = c.monto_proyectado
            c.monto_proyectado = monto_actual_cuota
            
            tx_hija = c.transaccion
            if tx_hija:
                if tx_hija.metodo_pago != MetodoPago.CREDITO:
                    if tx_hija.fecha <= hoy and tx_hija.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE:
                        billetera = db.get(Billetera, tx_hija.billetera_id)
                        if billetera:
                            if tx_hija.tipo == TipoTransaccion.INGRESO:
                                billetera.saldo_actual = billetera.saldo_actual - old_monto + monto_actual_cuota
                            else:
                                billetera.saldo_actual = billetera.saldo_actual + old_monto - monto_actual_cuota
                                
                tx_hija.monto = monto_actual_cuota
                
        grupo.monto_total = data.monto_total_nuevo
        grupo.total_financiado = data.monto_total_nuevo
        
    db.commit()
    db.refresh(grupo)
    
    return mapear_grupo_resumen(db, grupo)

@router.delete("/{grupo_id}")
def delete_grupo_cuotas(
    grupo_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    grupo = db.execute(
        select(GrupoCuotas)
        .where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == current_user.id)
    ).scalar_one_or_none()
    
    if not grupo:
        raise HTTPException(status_code=404, detail="Grupo de cuotas no encontrado")
        
    padre_id = grupo.transaccion_padre_id
    
    from app.services.transaccion_service import eliminar_transaccion
    eliminar_transaccion(db, current_user.id, padre_id)
    
    db.commit()
    return {"detail": "Grupo eliminado"}
