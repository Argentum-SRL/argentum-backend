from decimal import Decimal
from datetime import date
from sqlalchemy.orm import Session
from dateutil.relativedelta import relativedelta
from app.models.cuota import Cuota
from app.models.transaccion import Transaccion, TipoTransaccion
from app.models.grupo_cuotas import GrupoCuotas

def crear_cuotas(
    db: Session,
    transaccion_padre: Transaccion,
    grupo: GrupoCuotas,
    cantidad_cuotas: int,
    primer_vencimiento: date,
    monto_cuota: Decimal,
    usuario_id: str,
    cuota_inicial: int = 1
) -> list[Cuota]:
    """
    Crea las transacciones hijas y los registros de cuotas para un grupo.
    """
    cuotas = []
    # Empezamos desde la cuota_inicial hasta la total
    for i in range(cuota_inicial, cantidad_cuotas + 1):
        # La primera cuota que creamos (que es la i) debe tener la fecha del primer_vencimiento
        # El offset es i - cuota_inicial (si i=cuota_inicial, offset=0)
        fecha_cuota = primer_vencimiento + relativedelta(months=i - cuota_inicial)
        
        # 1. Crear la transacción hija (el movimiento de dinero futuro)
        hija = Transaccion(
            usuario_id=usuario_id,
            tipo=transaccion_padre.tipo,
            monto=monto_cuota,
            moneda=transaccion_padre.moneda,
            fecha=fecha_cuota,
            descripcion=f"{transaccion_padre.descripcion} (Cuota {i}/{cantidad_cuotas})",
            categoria_id=transaccion_padre.categoria_id,
            subcategoria_id=transaccion_padre.subcategoria_id,
            metodo_pago=transaccion_padre.metodo_pago,
            billetera_id=transaccion_padre.billetera_id,
            tarjeta_id=transaccion_padre.tarjeta_id, # Link a la tarjeta si existe
            es_cuota_hija=True,
            grupo_cuotas_id=grupo.id,
            origen=transaccion_padre.origen
        )
        db.add(hija)
        db.flush()

        # 2. Crear el registro de la cuota vinculada al grupo
        cuota_reg = Cuota(
            grupo_id=grupo.id,
            transaccion_id=hija.id,
            numero_cuota=i,
            monto_proyectado=monto_cuota,
            fecha_vencimiento=fecha_cuota,
            pagada=False
        )
        db.add(cuota_reg)
        cuotas.append(cuota_reg)
        
    return cuotas
