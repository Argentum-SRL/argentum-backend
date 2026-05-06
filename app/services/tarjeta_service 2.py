from sqlalchemy.orm import Session
from sqlalchemy import select, and_, or_
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.billetera import Billetera
from app.models.transaccion import Transaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.schemas.tarjeta_credito import TarjetaCreditoCreate, TarjetaCreditoUpdate, ResumenTarjeta, CuotaResumen, ResumenFuturo
from fastapi import HTTPException, status
from uuid import UUID
from datetime import date, datetime, timezone
from dateutil.relativedelta import relativedelta
from calendar import monthrange
from decimal import Decimal

def calcular_primer_vencimiento(fecha_compra: date, dia_cierre: int, dia_vencimiento: int) -> date:
    # Si la compra es el mismo día del cierre, entra en este resumen? 
    # Generalmente sí, hasta la hora de corte. Usamos <= dia_cierre.
    if fecha_compra.day <= dia_cierre:
        # Entra en resumen del mes actual -> vence el mes siguiente
        base = fecha_compra + relativedelta(months=1)
    else:
        # Entra en resumen del mes siguiente -> vence dos meses adelante
        base = fecha_compra + relativedelta(months=2)

    ultimo_dia = monthrange(base.year, base.month)[1]
    dia_real = min(dia_vencimiento, ultimo_dia)
    return base.replace(day=dia_real)

def obtener_tarjetas(db: Session, usuario_id: UUID):
    return db.query(TarjetaCredito).filter(
        TarjetaCredito.usuario_id == usuario_id,
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA
    ).all()

def obtener_tarjeta_por_id(db: Session, tarjeta_id: UUID, usuario_id: UUID):
    return db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()

def crear_tarjeta(db: Session, usuario_id: UUID, data: TarjetaCreditoCreate):
    billetera = db.query(Billetera).filter(
        Billetera.id == data.billetera_id,
        Billetera.usuario_id == usuario_id
    ).first()
    
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")
    
    if billetera.es_efectivo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Las billeteras de efectivo no pueden tener tarjetas."
        )
    
    nueva_tarjeta = TarjetaCredito(
        usuario_id=usuario_id,
        **data.model_dump()
    )
    db.add(nueva_tarjeta)
    db.commit()
    db.refresh(nueva_tarjeta)
    return nueva_tarjeta

def actualizar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID, data: TarjetaCreditoUpdate):
    tarjeta = obtener_tarjeta_por_id(db, tarjeta_id, usuario_id)
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tarjeta, key, value)
    
    db.commit()
    db.refresh(tarjeta)
    return tarjeta

def archivar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID):
    tarjeta = obtener_tarjeta_por_id(db, tarjeta_id, usuario_id)
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    tarjeta.estado = EstadoTarjeta.ARCHIVADA
    db.commit()
    return tarjeta

def eliminar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID):
    tarjeta = obtener_tarjeta_por_id(db, tarjeta_id, usuario_id)
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
    # Verificar si tiene transacciones
    tiene_txs = db.query(Transaccion).filter(Transaccion.tarjeta_id == tarjeta_id).first()
    if tiene_txs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede eliminar una tarjeta con transacciones. Archívala en su lugar."
        )
    
    db.delete(tarjeta)
    db.commit()
    return True

def calcular_resumen_actual(db: Session, tarjeta: TarjetaCredito) -> ResumenTarjeta:
    hoy = date.today()
    
    # Calcular fechas próximas
    # Cierre próximo
    if hoy.day <= tarjeta.dia_cierre:
        fecha_cierre_proximo = hoy.replace(day=tarjeta.dia_cierre)
    else:
        fecha_cierre_proximo = (hoy + relativedelta(months=1)).replace(day=tarjeta.dia_cierre)
        
    # Vencimiento próximo (es el del resumen que cierra en fecha_cierre_proximo)
    # Si cierra hoy, vence el mes que viene.
    fecha_vencimiento_proximo = calcular_primer_vencimiento(fecha_cierre_proximo, tarjeta.dia_cierre, tarjeta.dia_vencimiento)
    fecha_vencimiento_siguiente = fecha_vencimiento_proximo + relativedelta(months=1)

    # Buscar todas las cuotas de esta tarjeta
    query = db.query(Cuota, GrupoCuotas.descripcion).join(GrupoCuotas).filter(
        GrupoCuotas.tarjeta_id == tarjeta.id,
        Cuota.pagada == False
    )
    
    todas_las_cuotas = query.all()
    
    resumen_actual = []
    resumen_siguiente = []
    futuros_map = {} # "Mes Año": total
    
    total_actual = Decimal("0")
    total_siguiente = Decimal("0")
    
    for cuota, desc in todas_las_cuotas:
        c_res = CuotaResumen(
            id=cuota.id,
            descripcion=desc,
            numero_cuota=cuota.numero_cuota,
            total_cuotas=cuota.grupo.cantidad_cuotas,
            monto=float(cuota.monto),
            moneda=cuota.grupo.moneda,
            fecha_vencimiento=cuota.fecha_vencimiento
        )
        
        if cuota.fecha_vencimiento == fecha_vencimiento_proximo:
            resumen_actual.append(c_res)
            total_actual += cuota.monto
        elif cuota.fecha_vencimiento == fecha_vencimiento_siguiente:
            resumen_siguiente.append(c_res)
            total_siguiente += cuota.monto
        elif cuota.fecha_vencimiento > fecha_vencimiento_siguiente:
            mes_key = cuota.fecha_vencimiento.strftime("%B %Y")
            if mes_key not in futuros_map:
                futuros_map[mes_key] = {"total": Decimal("0"), "count": 0}
            futuros_map[mes_key]["total"] += cuota.monto
            futuros_map[mes_key]["count"] += 1

    resumenes_futuros = [
        ResumenFuturo(mes=k, total=float(v["total"]), moneda=tarjeta.moneda, cantidad_cuotas=v["count"])
        for k, v in futuros_map.items()
    ]
    # Ordenar futuros por fecha? (mejor si pudiéramos)
    
    return ResumenTarjeta(
        fecha_cierre_proximo=fecha_cierre_proximo,
        fecha_vencimiento_proximo=fecha_vencimiento_proximo,
        total_comprometido_resumen_actual=float(total_actual),
        total_comprometido_resumen_siguiente=float(total_siguiente),
        cuotas_resumen_actual=resumen_actual,
        cuotas_resumen_siguiente=resumen_siguiente,
        resumenes_futuros=resumenes_futuros,
        limite_credito=float(tarjeta.limite_credito) if tarjeta.limite_credito else None
    )
