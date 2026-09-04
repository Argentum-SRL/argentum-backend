from decimal import Decimal
from datetime import date
from sqlalchemy.orm import Session
from dateutil.relativedelta import relativedelta
from app.models.cuota import Cuota
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.services import presupuesto_service
from app.utils.fecha import hoy_argentina

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
    
    monto_base = round(monto_cuota, 2)
    total_financiado_real = grupo.total_financiado if grupo else None
    
    if total_financiado_real is not None:
        total_base = monto_base * cantidad_cuotas
        diferencia = total_financiado_real - total_base
    else:
        diferencia = Decimal('0.00')

    from calendar import monthrange
    from app.services.dias_habiles_service import ajustar_fecha_habil_sync
    from app.models.tarjeta_credito import TarjetaCredito

    tarjeta = None
    t_id = grupo.tarjeta_id if grupo else transaccion_padre.tarjeta_id
    if t_id:
        tarjeta = db.get(TarjetaCredito, t_id)
    dia_nominal = tarjeta.dia_vencimiento if tarjeta else primer_vencimiento.day

    # Empezamos desde la cuota_inicial hasta la total
    for i in range(cuota_inicial, cantidad_cuotas + 1):
        if i == cuota_inicial:
            fecha_cuota = primer_vencimiento
        else:
            # Derivar mes ancla estrictamente desde el calendario, nunca de una fecha ya ajustada
            mes_anchor = date(primer_vencimiento.year, primer_vencimiento.month, 1) + relativedelta(months=i - cuota_inicial)
            ultimo_dia = monthrange(mes_anchor.year, mes_anchor.month)[1]
            f_nom = date(mes_anchor.year, mes_anchor.month, min(dia_nominal, ultimo_dia))
            fecha_cuota = ajustar_fecha_habil_sync(f_nom, direccion="posterior")
        
        # Determinar el monto de esta cuota
        if i == cantidad_cuotas:
            monto_actual = monto_base + diferencia
        else:
            monto_actual = monto_base
            
        # 1. Crear la transacción hija (el movimiento de dinero futuro)
        hija = Transaccion(
            usuario_id=usuario_id,
            tipo=transaccion_padre.tipo,
            monto=monto_actual,
            moneda=transaccion_padre.moneda,
            fecha=fecha_cuota,
            descripcion=f"{transaccion_padre.descripcion} (Cuota {i}/{cantidad_cuotas})".strip() if transaccion_padre.descripcion else f"Cuota {i}/{cantidad_cuotas}",
            categoria_id=transaccion_padre.categoria_id,
            subcategoria_id=transaccion_padre.subcategoria_id,
            metodo_pago=transaccion_padre.metodo_pago,
            billetera_id=transaccion_padre.billetera_id,
            tarjeta_id=transaccion_padre.tarjeta_id, # Link a la tarjeta si existe
            es_cuota_hija=True,
            es_recurrente=transaccion_padre.es_recurrente,
            suscripcion_id=transaccion_padre.suscripcion_id,
            grupo_cuotas_id=grupo.id,
            origen=transaccion_padre.origen,
            estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE
        )
        db.add(hija)
        db.flush()

        # 2. Crear el registro de la cuota vinculada al grupo
        cuota_reg = Cuota(
            grupo_id=grupo.id,
            transaccion_id=hija.id,
            numero_cuota=i,
            monto_proyectado=monto_actual,
            fecha_vencimiento=fecha_cuota,
            pagada=False
        )
        db.add(cuota_reg)
        cuotas.append(cuota_reg)
        
    return cuotas


def cancelar_grupo(db: Session, grupo_id: any, usuario_id: any) -> GrupoCuotas:
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.grupo_cuotas import EstadoGrupoCuotas

    # 1. Buscar el grupo por grupo_id y usuario_id — si no existe, raise HTTPException 404
    grupo = db.execute(
        select(GrupoCuotas).where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo de cuotas.")

    # 2. Si el grupo ya está COMPLETADO o CANCELADO, fallar con 400
    if grupo.estado == EstadoGrupoCuotas.CANCELADO:
        raise HTTPException(status_code=400, detail="El grupo ya está cancelado")
    if grupo.estado == EstadoGrupoCuotas.COMPLETADO:
        raise HTTPException(status_code=400, detail="El grupo ya está completado")

    # 3. Obtener todas las cuotas del grupo con pagada == False (cuotas pendientes)
    cuotas_pendientes = db.execute(
        select(Cuota).where(Cuota.grupo_id == grupo.id, Cuota.pagada == False)
    ).scalars().all()

    # 4. Para cada cuota pendiente:
    for cuota in cuotas_pendientes:
        tx_hija = cuota.transaccion
        if tx_hija:
            # Revertir impacto de presupuesto si existe la tx hija antes de borrarla
            try:
                presupuesto_service.registrar_impacto_presupuesto(db, tx_hija, revertir=True)
            except Exception:
                pass
            # Marcar la transacción hija con monto cero y descripción cancelada
            tx_hija.monto = Decimal("0.00")
            tx_hija.descripcion = f"{tx_hija.descripcion} (Cancelada)"
        # Marcar la cuota con monto cero
        cuota.pagada = True
        cuota.monto_proyectado = Decimal("0.00")
        cuota.monto_real = Decimal("0.00")

    # 5. Marcar grupo.estado = EstadoGrupoCuotas.CANCELADO
    grupo.estado = EstadoGrupoCuotas.CANCELADO
    # 6. db.commit()
    db.commit()
    # 7. Retornar el grupo actualizado
    return grupo


def prepagar_grupo(
    db: Session,
    grupo_id: any,
    usuario_id: any,
    billetera_id: any,
    categoria_id: any = None
) -> GrupoCuotas:
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.grupo_cuotas import EstadoGrupoCuotas
    from app.models.billetera import Billetera
    from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion, EstadoVerificacionTransaccion

    # 1. Buscar el grupo por grupo_id y usuario_id — si no existe, raise HTTPException 404
    grupo = db.execute(
        select(GrupoCuotas).where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo de cuotas.")

    # 2. Si grupo.estado != EstadoGrupoCuotas.ACTIVO, raise HTTPException 400
    if grupo.estado != EstadoGrupoCuotas.ACTIVO:
        raise HTTPException(status_code=400, detail="El grupo no está activo")

    # 3. Obtener todas las cuotas con pagada == False
    cuotas_pendientes = db.execute(
        select(Cuota).where(Cuota.grupo_id == grupo.id, Cuota.pagada == False)
    ).scalars().all()

    # 4. Si no hay cuotas pendientes, raise HTTPException 400 "No hay cuotas pendientes"
    if not cuotas_pendientes:
        raise HTTPException(status_code=400, detail="No hay cuotas pendientes")

    # 5. Calcular monto_total_pendiente = sum(cuota.monto_proyectado for cuota in cuotas_pendientes)
    monto_total_pendiente = sum(cuota.monto_proyectado for cuota in cuotas_pendientes)

    # 6. Buscar la billetera por billetera_id y usuario_id — si no existe, raise HTTPException 404
    billetera = db.execute(
        select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")

    from app.services.transaccion_service import _validar_moneda_coincide
    _validar_moneda_coincide(grupo.moneda, billetera)

    # 7. Crear una transacción de egreso por el monto total pendiente:
    nueva_transaccion = Transaccion(
        usuario_id=usuario_id,
        tipo=TipoTransaccion.EGRESO,
        monto=monto_total_pendiente,
        moneda=grupo.moneda,
        fecha=hoy_argentina(),
        descripcion=f"Prepago de {len(cuotas_pendientes)} cuotas restantes: {grupo.descripcion}",
        categoria_id=categoria_id if categoria_id else (grupo.transaccion_padre.categoria_id if grupo.transaccion_padre else None),
        subcategoria_id=(grupo.transaccion_padre.subcategoria_id if grupo.transaccion_padre else None),
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_id,
        tarjeta_id=grupo.tarjeta_id,
        es_cuota_hija=False,
        es_padre_cuotas=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA
    )

    # Debitar billetera.saldo_actual -= monto_total_pendiente
    billetera.saldo_actual -= monto_total_pendiente

    # Chequeo de saldo cero manualmente después de debitar
    if billetera.saldo_actual <= 0:
        try:
            from app.services.notificacion_service import obtener_configuracion, resolver_canales_notificacion, crear_notificacion
            from app.models.notificacion import TipoNotificacion, NivelNotificacion
            config = obtener_configuracion(db, usuario_id)
            canales = resolver_canales_notificacion(config, TipoNotificacion.SALDO_CERO)
            if canales is not None:
                canal_web, canal_whatsapp = canales
                crear_notificacion(
                    db=db,
                    usuario_id=usuario_id,
                    tipo=TipoNotificacion.SALDO_CERO,
                    nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                    mensaje=f"Tu billetera '{billetera.nombre}' quedó sin saldo disponible.",
                    entidad_tipo="billetera",
                    entidad_id=billetera.id,
                    deep_link="/app/billeteras",
                    canal_web=canal_web,
                    canal_whatsapp=canal_whatsapp,
                )
        except Exception:
            pass

    # Registrar impacto del prepago en el presupuesto
    try:
        presupuesto_service.registrar_impacto_presupuesto(db, nueva_transaccion, revertir=False)
    except Exception:
        pass

    # 8. Para cada cuota pendiente:
    # - cuota.pagada = True
    # - cuota.monto_real = cuota.monto_proyectado
    for cuota in cuotas_pendientes:
        cuota.pagada = True
        cuota.monto_real = cuota.monto_proyectado
        # Revertimos impacto de presupuesto de la cuota futura y ponemos su monto a 0 para no duplicar
        tx_hija = cuota.transaccion
        if tx_hija:
            try:
                presupuesto_service.registrar_impacto_presupuesto(db, tx_hija, revertir=True)
            except Exception:
                pass
            tx_hija.monto = Decimal("0.00")
            tx_hija.descripcion = f"{tx_hija.descripcion} (Prepagada)"

    # 9. Marcar grupo.estado = EstadoGrupoCuotas.COMPLETADO
    grupo.estado = EstadoGrupoCuotas.COMPLETADO

    # 10. db.add(nueva_transaccion)
    db.add(nueva_transaccion)

    # 11. db.commit()
    db.commit()

    # 12. Retornar el grupo actualizado
    return grupo

