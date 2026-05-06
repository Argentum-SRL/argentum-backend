from sqlalchemy.orm import Session
from app.models.transaccion import Transaccion, OrigenTransaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.usuario import Moneda
from decimal import Decimal
from datetime import date
from dateutil.relativedelta import relativedelta
from uuid import UUID

def crear_cuotas(
    db: Session,
    usuario_id: UUID,
    transaccion_padre: Transaccion,
    cantidad_cuotas: int,
    monto_total: Decimal,
    moneda: Moneda,
    primer_vencimiento: date,
    tiene_interes: bool = False,
    tasa_interes: Decimal = Decimal("0"),
    tarjeta_id: UUID = None
):
    # 1. Calcular monto de cuota e interés
    monto_total = Decimal(str(monto_total))
    tasa_interes = Decimal(str(tasa_interes))
    
    if tiene_interes and tasa_interes > 0:
        tasa_mensual = tasa_interes / 100
        monto_cuota = monto_total * (tasa_mensual * (1 + tasa_mensual)**cantidad_cuotas) / ((1 + tasa_mensual)**cantidad_cuotas - 1)
    else:
        monto_cuota = monto_total / cantidad_cuotas
    
    total_financiado = monto_cuota * cantidad_cuotas
    
    grupo = GrupoCuotas(
        usuario_id=usuario_id,
        transaccion_padre_id=transaccion_padre.id,
        descripcion=transaccion_padre.descripcion,
        monto_total=monto_total,
        cantidad_cuotas=cantidad_cuotas,
        tiene_interes=tiene_interes,
        tasa_interes=tasa_interes,
        total_financiado=total_financiado,
        moneda=moneda,
        tarjeta_id=tarjeta_id,
        primer_vencimiento=primer_vencimiento
    )
    db.add(grupo)
    db.flush()

    monto_cuota = total_financiado / cantidad_cuotas

    # 2. Crear las transacciones hijas y los registros de cuota
    for i in range(1, cantidad_cuotas + 1):
        fecha_cuota = primer_vencimiento + relativedelta(months=i-1)
        
        # Transacción hija
        hija = Transaccion(
            usuario_id=usuario_id,
            tipo=transaccion_padre.tipo,
            monto=monto_cuota,
            moneda=moneda,
            fecha=fecha_cuota,
            descripcion=f"{transaccion_padre.descripcion} (Cuota {i}/{cantidad_cuotas})",
            categoria_id=transaccion_padre.categoria_id,
            subcategoria_id=transaccion_padre.subcategoria_id,
            metodo_pago=transaccion_padre.metodo_pago,
            billetera_id=transaccion_padre.billetera_id,
            tarjeta_id=tarjeta_id,
            es_cuota_hija=True,
            grupo_cuotas_id=grupo.id,
            origen=transaccion_padre.origen
        )
        db.add(hija)
        db.flush()

        # Registro de cuota
        cuota_reg = Cuota(
            grupo_id=grupo.id,
            transaccion_id=hija.id,
            numero_cuota=i,
            monto_proyectado=monto_cuota,
            fecha_vencimiento=fecha_cuota,
            pagada=False
        )
        db.add(cuota_reg)

    return grupo
