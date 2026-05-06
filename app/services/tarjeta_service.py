from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID
from calendar import monthrange
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.billetera import Billetera
from app.models.transaccion import Transaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.schemas.tarjeta_credito import (
    TarjetaCreditoCreate, 
    TarjetaCreditoUpdate,
    ResumenTarjeta,
    CuotaResumen,
    ResumenFuturo
)

MESES_ES = {
    "January": "Enero", "February": "Febrero", "March": "Marzo",
    "April": "Abril", "May": "Mayo", "June": "Junio",
    "July": "Julio", "August": "Agosto", "September": "Septiembre",
    "October": "Octubre", "November": "Noviembre", "December": "Diciembre"
}


def calcular_primer_vencimiento(
    fecha_compra: date,
    dia_cierre: int,
    dia_vencimiento: int
) -> date:
    """
    Calcula la fecha del primer vencimiento de una compra con tarjeta.
    Si la compra es antes o el mismo día del cierre, vence el mes siguiente.
    Si es después del cierre, vence a los dos meses.
    """
    if fecha_compra.day <= dia_cierre:
        base = fecha_compra + relativedelta(months=1)
    else:
        base = fecha_compra + relativedelta(months=2)

    ultimo_dia = monthrange(base.year, base.month)[1]
    dia_real = min(dia_vencimiento, ultimo_dia)

    return base.replace(day=dia_real)


def obtener_tarjetas(db: Session, usuario_id: UUID) -> list[TarjetaCredito]:
    return db.query(TarjetaCredito).filter(
        TarjetaCredito.usuario_id == usuario_id,
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA
    ).all()


def obtener_tarjetas_por_billetera(db: Session, usuario_id: UUID, billetera_id: UUID) -> list[TarjetaCredito]:
    return db.query(TarjetaCredito).filter(
        TarjetaCredito.usuario_id == usuario_id,
        TarjetaCredito.billetera_id == billetera_id,
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA
    ).all()


def crear_tarjeta(db: Session, usuario_id: UUID, data: TarjetaCreditoCreate) -> TarjetaCredito:
    # Validar que la billetera pertenece al usuario
    billetera = db.query(Billetera).filter(
        Billetera.id == data.billetera_id,
        Billetera.usuario_id == usuario_id
    ).first()
    
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")
    
    # Validar que la billetera no sea de efectivo
    if billetera.es_efectivo:
        raise HTTPException(
            status_code=400, 
            detail="Las billeteras de efectivo no pueden tener tarjetas."
        )

    nueva_tarjeta = TarjetaCredito(
        usuario_id=usuario_id,
        billetera_id=data.billetera_id,
        nombre=data.nombre,
        red=data.red,
        dia_cierre=data.dia_cierre,
        dia_vencimiento=data.dia_vencimiento,
        limite_credito=data.limite_credito,
        moneda=data.moneda,
        color=data.color
    )
    
    db.add(nueva_tarjeta)
    db.commit()
    db.refresh(nueva_tarjeta)
    return nueva_tarjeta


def actualizar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID, data: TarjetaCreditoUpdate) -> TarjetaCredito:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tarjeta, key, value)
    
    db.commit()
    db.refresh(tarjeta)
    return tarjeta


def archivar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID) -> TarjetaCredito:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
    tarjeta.estado = EstadoTarjeta.ARCHIVADA
    db.commit()
    db.refresh(tarjeta)
    return tarjeta


def desarchivar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID) -> TarjetaCredito:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
    tarjeta.estado = EstadoTarjeta.ACTIVA
    db.commit()
    db.refresh(tarjeta)
    return tarjeta


def eliminar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID) -> None:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
    # Verificar si tiene transacciones registradas
    tiene_transacciones = db.query(Transaccion).filter(Transaccion.tarjeta_id == tarjeta_id).first()
    if tiene_transacciones:
        raise HTTPException(
            status_code=400, 
            detail="Esta tarjeta tiene transacciones registradas. Podés archivarla pero no eliminarla."
        )
    
    db.delete(tarjeta)
    db.commit()


def calcular_resumen_actual(db: Session, tarjeta: TarjetaCredito) -> ResumenTarjeta:
    hoy = date.today()

    # ── Calcular fecha de cierre próximo ──────────────────
    cierre = date(hoy.year, hoy.month, tarjeta.dia_cierre)
    if hoy > cierre:
        cierre = cierre + relativedelta(months=1)
    fecha_cierre_proximo = cierre

    # ── Calcular fecha de vencimiento próximo ─────────────
    # Usar el último día del mes si dia_vencimiento es mayor
    ultimo_dia_mes = monthrange(hoy.year, hoy.month)[1]
    dia_venc = min(tarjeta.dia_vencimiento, ultimo_dia_mes)
    
    venc = date(hoy.year, hoy.month, dia_venc)
    if hoy > venc:
        # Si ya pasó el vencimiento de este mes, ir al siguiente
        proximo_mes = hoy + relativedelta(months=1)
        ultimo_dia_proximo = monthrange(proximo_mes.year, proximo_mes.month)[1]
        dia_venc_proximo = min(tarjeta.dia_vencimiento, ultimo_dia_proximo)
        venc = date(proximo_mes.year, proximo_mes.month, dia_venc_proximo)
    
    fecha_vencimiento_proximo = venc

    # ── Obtener todas las cuotas futuras de esta tarjeta ──
    # Cuota -> GrupoCuotas (tarjeta_id) -> filtrar por tarjeta
    cuotas = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(
            GrupoCuotas.tarjeta_id == tarjeta.id,
            Cuota.pagada == False,
            Cuota.fecha_vencimiento >= hoy
        )
        .order_by(Cuota.fecha_vencimiento)
        .all()
    )

    # ── Obtener descripción de la transacción padre ────────
    def get_descripcion(cuota: Cuota) -> str:
        tx_padre = db.query(Transaccion).filter(
            Transaccion.id == cuota.transaccion_id
        ).first()
        return tx_padre.descripcion if tx_padre else "Sin descripción"

    # ── Agrupar cuotas por resumen ─────────────────────────
    venc_siguiente = fecha_vencimiento_proximo + relativedelta(months=1)
    # Ajustar dia de vencimiento del mes siguiente
    ultimo_dia_siguiente = monthrange(venc_siguiente.year, venc_siguiente.month)[1]
    venc_siguiente = venc_siguiente.replace(day=min(tarjeta.dia_vencimiento, ultimo_dia_siguiente))

    cuotas_actual = []
    cuotas_siguiente = []
    futuros_dict: dict[str, dict] = {}

    for cuota in cuotas:
        grupo = cuota.grupo
        total_cuotas = grupo.cantidad_cuotas if grupo else 1

        cuota_data = CuotaResumen(
            id=cuota.transaccion_id,
            descripcion=get_descripcion(cuota),
            numero_cuota=cuota.numero_cuota,
            total_cuotas=total_cuotas,
            monto=cuota.monto_real if cuota.monto_real is not None else cuota.monto_proyectado,
            moneda=tarjeta.moneda.value,
            fecha_vencimiento=cuota.fecha_vencimiento
        )

        if cuota.fecha_vencimiento <= fecha_vencimiento_proximo:
            cuotas_actual.append(cuota_data)
        elif cuota.fecha_vencimiento <= venc_siguiente:
            cuotas_siguiente.append(cuota_data)
        else:
            # Agrupar por mes
            mes_key = cuota.fecha_vencimiento.strftime("%Y-%m")
            # Traducir mes a español
            nombre_mes_en = cuota.fecha_vencimiento.strftime("%B")
            nombre_mes_es = MESES_ES.get(nombre_mes_en, nombre_mes_en)
            mes_label = f"{nombre_mes_es} {cuota.fecha_vencimiento.year}"
            
            if mes_key not in futuros_dict:
                futuros_dict[mes_key] = {
                    "mes": mes_label,
                    "mes_fecha": date(cuota.fecha_vencimiento.year,
                                      cuota.fecha_vencimiento.month, 1),
                    "total": Decimal(0),
                    "moneda": tarjeta.moneda.value,
                    "cantidad_cuotas": 0
                }
            futuros_dict[mes_key]["total"] += cuota_data.monto
            futuros_dict[mes_key]["cantidad_cuotas"] += 1

    resumenes_futuros = [
        ResumenFuturo(**v)
        for v in sorted(futuros_dict.values(), key=lambda x: x["mes_fecha"])
    ]

    return ResumenTarjeta(
        fecha_cierre_proximo=fecha_cierre_proximo,
        fecha_vencimiento_proximo=fecha_vencimiento_proximo,
        total_comprometido_resumen_actual=sum(c.monto for c in cuotas_actual),
        total_comprometido_resumen_siguiente=sum(c.monto for c in cuotas_siguiente),
        cuotas_resumen_actual=cuotas_actual,
        cuotas_resumen_siguiente=cuotas_siguiente,
        resumenes_futuros=resumenes_futuros
    )
