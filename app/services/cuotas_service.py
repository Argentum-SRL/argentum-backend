from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from sqlalchemy.orm import Session
from dateutil.relativedelta import relativedelta
from app.models.cuota import Cuota
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.schemas.grupos_cuotas import GrupoCuotasUpdate
from app.services import presupuesto_service
from app.services.saldo_billetera_service import aplicar_impacto_saldo
from app.utils.fecha import hoy_argentina
from app.core.constants import IVA_INTERESES


def calcular_tasas_cuotas(tasa_interes: Decimal | float | None) -> dict[str, Decimal] | None:
    """
    Calcula TNA, TEA y CFT estimado a partir de la tasa_interes mensual nominal (%) de una compra en cuotas.

    Fórmulas:
      - tasa_mensual = tasa_interes / 100
      - TNA = tasa_mensual * 12
      - TEA = (1 + tasa_mensual)^12 - 1
      - CFT estimado = (1 + tasa_mensual * (1 + IVA_INTERESES))^12 - 1

    Los valores se expresan en porcentaje (%) redondeados a 2 decimales para compatibilidad
    con la representación financiera del sistema.
    """
    if tasa_interes is None:
        return None
    try:
        ti = Decimal(str(tasa_interes))
    except Exception:
        return None

    if ti <= Decimal("0"):
        return None

    tasa_mensual = ti / Decimal("100")
    tna = (tasa_mensual * Decimal("12") * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tea = (((Decimal("1") + tasa_mensual) ** 12 - Decimal("1")) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cft_estimado = (((Decimal("1") + tasa_mensual * (Decimal("1") + IVA_INTERESES)) ** 12 - Decimal("1")) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "tna": tna,
        "tea": tea,
        "cft_estimado": cft_estimado,
    }


def crear_cuotas(
    db: Session,
    transaccion_padre: Transaccion,
    grupo: GrupoCuotas,
    cantidad_cuotas: int,
    primer_vencimiento: date,
    monto_cuota: Decimal,
    usuario_id: str,
    cuota_inicial: int = 1,
    desde_cuota: int = 1
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
    from app.services import calendario_tarjeta_service

    tarjeta = None
    t_id = grupo.tarjeta_id if grupo else transaccion_padre.tarjeta_id
    if t_id:
        tarjeta = db.get(TarjetaCredito, t_id)
    dia_nominal = tarjeta.dia_vencimiento if tarjeta else primer_vencimiento.day

    res_stmts: list[tuple[date, date]] = []
    idx_cuota_n = 0
    if tarjeta is not None:
        cierre_cuota_n = calendario_tarjeta_service.cierre_de_vencimiento(tarjeta, primer_vencimiento)
        # Decisión 2: lista de resúmenes desde (cierre de cuota N - (cuota_inicial - 1) meses)
        cierre_base = cierre_cuota_n - relativedelta(months=cuota_inicial - 1)
        desde_c = date(cierre_base.year, cierre_base.month, 1)
        hasta_c = date(cierre_cuota_n.year, cierre_cuota_n.month, 1) + relativedelta(months=cantidad_cuotas + 3)
        res_stmts = calendario_tarjeta_service.resumenes(tarjeta, desde_c, hasta_c)
        for idx, (c, v) in enumerate(res_stmts):
            if v == primer_vencimiento or c == cierre_cuota_n:
                idx_cuota_n = idx
                break

    # Empezamos desde desde_cuota hasta cantidad_cuotas para registrar las cuotas requeridas
    for i in range(desde_cuota, cantidad_cuotas + 1):
        if i == cuota_inicial:
            fecha_cuota = primer_vencimiento
        else:
            k = idx_cuota_n + (i - cuota_inicial)
            if tarjeta is not None and 0 <= k < len(res_stmts):
                fecha_cuota = res_stmts[k][1]
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
            
        # 1. Crear la transacción hija (el movimiento de dinero futuro o retroactivo)
        hija = Transaccion(
            usuario_id=usuario_id,
            tipo=transaccion_padre.tipo,
            monto=monto_actual,
            moneda=transaccion_padre.moneda,
            fecha=fecha_cuota,
            descripcion=(f"{transaccion_padre.descripcion.strip()} (Cuota {i}/{cantidad_cuotas})" if transaccion_padre.descripcion and transaccion_padre.descripcion.strip() else f"Cuota {i}/{cantidad_cuotas}"),
            categoria_id=transaccion_padre.categoria_id,
            subcategoria_id=transaccion_padre.subcategoria_id,
            metodo_pago=transaccion_padre.metodo_pago,
            billetera_id=transaccion_padre.billetera_id,
            tarjeta_id=transaccion_padre.tarjeta_id, # Link a la tarjeta si existe
            es_cuota_hija=True,
            suscripcion_id=transaccion_padre.suscripcion_id,
            grupo_cuotas_id=grupo.id,
            origen=transaccion_padre.origen,
            estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE
        )
        db.add(hija)
        db.flush()

        es_anterior = (i < cuota_inicial)
        # 2. Crear el registro de la cuota vinculada al grupo
        cuota_reg = Cuota(
            grupo_id=grupo.id,
            transaccion_id=hija.id,
            numero_cuota=i,
            monto_proyectado=monto_actual,
            monto_real=monto_actual if es_anterior else None,
            fecha_vencimiento=fecha_cuota,
            pagada=es_anterior
        )
        db.add(cuota_reg)
        cuotas.append(cuota_reg)

    if grupo and cuotas and desde_cuota == 1:
        grupo.primer_vencimiento = cuotas[0].fecha_vencimiento
        
    return cuotas


def _marcar_pagadas_las_aplicadas(db: Session, grupo: GrupoCuotas) -> None:
    """
    Toda cuota no pagada cuya hija movió el saldo (transaccion_service._afecta_saldo y saldo_aplicado True)
    pasa a pagada=True y monto_real = monto de la hija.
    """
    from sqlalchemy.orm import joinedload
    from sqlalchemy import select
    from app.services import transaccion_service

    cuotas = db.execute(
        select(Cuota).options(joinedload(Cuota.transaccion)).where(Cuota.grupo_id == grupo.id)
    ).scalars().all()

    for c in cuotas:
        if not c.pagada:
            tx_hija = c.transaccion
            if tx_hija and transaccion_service._afecta_saldo(tx_hija) and tx_hija.saldo_aplicado:
                c.pagada = True
                c.monto_real = tx_hija.monto

    db.flush()


def cancelar_grupo(
    db: Session,
    grupo_id: any,
    usuario_id: any,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> GrupoCuotas:
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.grupo_cuotas import EstadoGrupoCuotas

    # 1. Buscar el grupo por grupo_id y usuario_id — si no existe, raise HTTPException 404
    grupo = db.execute(
        select(GrupoCuotas).where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo de cuotas.")

    # Marcar pagadas las cuotas cuya hija ya movió el saldo
    _marcar_pagadas_las_aplicadas(db, grupo)

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
                presupuesto_service.registrar_impacto_presupuesto(db, tx_hija, revertir=True, commit=False)
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
    # 6. db.commit() o flush
    if commit:
        db.commit()
    else:
        db.flush()
    # 7. Retornar el grupo actualizado
    return grupo


def prepagar_grupo(
    db: Session,
    grupo_id: any,
    usuario_id: any,
    billetera_id: any,
    categoria_id: any = None,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
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

    # Marcar pagadas las cuotas cuya hija ya movió el saldo
    _marcar_pagadas_las_aplicadas(db, grupo)

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

    if getattr(billetera, "es_inversion", False):
        raise HTTPException(
            status_code=400,
            detail="Esta billetera no admite pagos de tarjeta ni cuotas."
        )

    from app.services.transaccion_service import _validar_moneda_coincide
    _validar_moneda_coincide(grupo.moneda, billetera)

    # 7. Crear una transacción de egreso por el monto total pendiente:
    nueva_transaccion = Transaccion(
        usuario_id=usuario_id,
        tipo=TipoTransaccion.EGRESO,
        monto=monto_total_pendiente,
        moneda=grupo.moneda,
        fecha=hoy_argentina(),
        descripcion=(f"Prepago de {len(cuotas_pendientes)} cuotas restantes: {grupo.descripcion.strip()}" if grupo.descripcion and grupo.descripcion.strip() else f"Prepago de {len(cuotas_pendientes)} cuotas restantes"),
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

    # Debitar billetera mediante la puerta única
    aplicar_impacto_saldo(billetera, -monto_total_pendiente, hoy_argentina())

    # Chequeo de saldo cero manualmente después de debitar
    if billetera.saldo_actual <= 0:
        try:
            from app.services.notificacion_service import obtener_configuracion, resolver_canales_notificacion, crear_notificacion
            from app.models.notificacion import TipoNotificacion, NivelNotificacion
            config = obtener_configuracion(db, usuario_id, commit=False)
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
                    datos_template={"billetera_nombre": billetera.nombre},
                    commit=False,
                )
        except Exception:
            pass

    # Registrar impacto del prepago en el presupuesto
    try:
        presupuesto_service.registrar_impacto_presupuesto(db, nueva_transaccion, revertir=False, commit=False)
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
                presupuesto_service.registrar_impacto_presupuesto(db, tx_hija, revertir=True, commit=False)
            except Exception:
                pass
            tx_hija.monto = Decimal("0.00")
            tx_hija.descripcion = f"{tx_hija.descripcion} (Prepagada)"

    # 9. Marcar grupo.estado = EstadoGrupoCuotas.COMPLETADO
    grupo.estado = EstadoGrupoCuotas.COMPLETADO

    # 10. db.add(nueva_transaccion)
    db.add(nueva_transaccion)

    # 11. db.commit() o flush
    if commit:
        db.commit()
    else:
        db.flush()

    # 12. Retornar el grupo actualizado
    return grupo


def _eliminar_cuota_pendiente(db: Session, cuota: Cuota) -> None:
    """
    Elimina una cuota pendiente puntual y su transacción hija asociada,
    revirtiendo el impacto presupuestario si correspondiera.
    Exclusiva de actualización de grupos: no altera saldos de billetera ni el resto del grupo.
    """
    if cuota.pagada:
        return

    tx_hija = cuota.transaccion or (db.get(Transaccion, cuota.transaccion_id) if cuota.transaccion_id else None)
    if tx_hija:
        try:
            presupuesto_service.registrar_impacto_presupuesto(db, tx_hija, revertir=True, commit=False)
        except Exception:
            pass

    db.delete(cuota)
    db.flush()

    if tx_hija:
        db.delete(tx_hija)
        db.flush()


def eliminar_cuota_individual(
    db: Session,
    usuario_id: any,
    transaccion_id: any,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> None:
    """
    Elimina exclusivamente una cuota pendiente puntual y su transacción hija asociada,
    sin tocar el resto del grupo ni recalcular fechas o montos.
    Valida estrictamente que pagada == False.
    Si era la única cuota pendiente que quedaba en el grupo, el grupo pasa a EstadoGrupoCuotas.COMPLETADO.
    """
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas

    # 1. Buscar si se envió transaccion_id de la cuota hija
    tx = db.execute(
        select(Transaccion).where(
            Transaccion.id == transaccion_id,
            Transaccion.usuario_id == usuario_id
        )
    ).scalar_one_or_none()

    cuota = None
    if tx:
        if not tx.es_cuota_hija:
            raise HTTPException(status_code=400, detail="La transacción indicada no es una cuota.")
        cuota = db.execute(
            select(Cuota).where(Cuota.transaccion_id == tx.id)
        ).scalar_one_or_none()
    else:
        # Por si se envió directamente el id de la cuota
        cuota = db.execute(
            select(Cuota).join(GrupoCuotas).where(
                Cuota.id == transaccion_id,
                GrupoCuotas.usuario_id == usuario_id
            )
        ).scalar_one_or_none()

    if not cuota:
        raise HTTPException(status_code=404, detail="No encontramos esa cuota.")

    grupo = db.execute(
        select(GrupoCuotas).where(
            GrupoCuotas.id == cuota.grupo_id,
            GrupoCuotas.usuario_id == usuario_id
        )
    ).scalar_one_or_none()

    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos el grupo de cuotas.")

    # Marcar pagadas las cuotas cuya hija ya movió el saldo
    _marcar_pagadas_las_aplicadas(db, grupo)

    # 2. Validación estricta: Solo se puede borrar con pagada=False
    if cuota.pagada:
        raise HTTPException(
            status_code=400,
            detail="No se puede borrar una cuota que ya fue pagada."
        )

    # 3. Borrar la cuota pendiente puntual y su transacción hija reusando la función existente
    _eliminar_cuota_pendiente(db, cuota)
    db.flush()

    # 4. Verificar cuotas restantes del grupo
    cuotas_restantes = db.execute(
        select(Cuota).where(Cuota.grupo_id == grupo.id)
    ).scalars().all()

    cuotas_pendientes = [c for c in cuotas_restantes if not c.pagada]
    if not cuotas_pendientes:
        grupo.estado = EstadoGrupoCuotas.COMPLETADO

    if commit:
        db.commit()
    else:
        db.flush()


def actualizar_grupo(
    db: Session,
    grupo: GrupoCuotas,
    data: GrupoCuotasUpdate,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> GrupoCuotas:
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.billetera import Billetera
    from app.models.tarjeta_credito import TarjetaCredito

    hoy = hoy_argentina()
    usuario_id = grupo.usuario_id

    # Marcar pagadas las cuotas cuya hija ya movió el saldo
    _marcar_pagadas_las_aplicadas(db, grupo)

    cambia_tarjeta_o_fecha = (
        data.tarjeta_id is not None or
        data.billetera_id is not None or
        data.fecha_referencia is not None
    )

    if cambia_tarjeta_o_fecha:
        cuotas_pagadas = [c for c in grupo.cuotas if c.pagada]
        n_pagadas = len(cuotas_pagadas)

        if n_pagadas == grupo.cantidad_cuotas:
            raise HTTPException(
                status_code=400,
                detail="Esta compra ya está completamente pagada, no se puede editar tarjeta ni fecha."
            )

        # Validar tarjeta si fue provista
        tarjeta = None
        if data.tarjeta_id is not None:
            tarjeta = db.execute(
                select(TarjetaCredito).where(
                    TarjetaCredito.id == data.tarjeta_id,
                    TarjetaCredito.usuario_id == usuario_id
                )
            ).scalar_one_or_none()
            if not tarjeta:
                raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
        elif grupo.tarjeta_id:
            tarjeta = db.execute(
                select(TarjetaCredito).where(
                    TarjetaCredito.id == grupo.tarjeta_id,
                    TarjetaCredito.usuario_id == usuario_id
                )
            ).scalar_one_or_none()

        # Validar billetera si fue provista
        billetera_final_id = None
        if data.billetera_id is not None:
            billetera = db.execute(
                select(Billetera).where(
                    Billetera.id == data.billetera_id,
                    Billetera.usuario_id == usuario_id
                )
            ).scalar_one_or_none()
            if not billetera:
                raise HTTPException(status_code=404, detail="Billetera no encontrada")
            if tarjeta is not None and billetera.id != tarjeta.billetera_id:
                raise HTTPException(status_code=400, detail="La billetera no coincide con la tarjeta seleccionada.")
            billetera_final_id = billetera.id
        else:
            if tarjeta is not None:
                billetera_final_id = tarjeta.billetera_id
            elif grupo.transaccion_padre:
                billetera_final_id = grupo.transaccion_padre.billetera_id

        # Validar monto_total_nuevo antes de mutaciones si vino junto
        if data.monto_total_nuevo is not None:
            total_ya_pagado = sum(c.monto_proyectado for c in cuotas_pagadas)
            if data.monto_total_nuevo - total_ya_pagado <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="El monto nuevo es menor o igual a lo que ya pagaste. No podés reducir el monto a menos de lo ya abonado."
                )

        fecha_ref = data.fecha_referencia or hoy

        # Calcular fecha del próximo vencimiento
        if tarjeta is not None:
            from app.services import calendario_tarjeta_service
            if n_pagadas > 0:
                proximo_venc = calendario_tarjeta_service.vencimiento_proximo(tarjeta, fecha_ref)
            else:
                _, proximo_venc = calendario_tarjeta_service.resumen_de_compra(tarjeta, fecha_ref)
        else:
            proximo_venc = fecha_ref + relativedelta(months=1)

        # Borrar cuotas pendientes y sus transacciones hijas usando la función local
        cuotas_pendientes = [c for c in list(grupo.cuotas) if not c.pagada]
        for c in cuotas_pendientes:
            _eliminar_cuota_pendiente(db, c)
        db.flush()
        db.expire(grupo, ["cuotas"])

        # Actualizar grupo y transaccion_padre
        if tarjeta is not None:
            grupo.tarjeta_id = tarjeta.id
        elif data.tarjeta_id is not None:
            grupo.tarjeta_id = None

        if grupo.transaccion_padre:
            if tarjeta is not None:
                grupo.transaccion_padre.tarjeta_id = tarjeta.id
            elif data.tarjeta_id is not None:
                grupo.transaccion_padre.tarjeta_id = None

            if billetera_final_id is not None:
                grupo.transaccion_padre.billetera_id = billetera_final_id

            if data.fecha_referencia is not None:
                grupo.transaccion_padre.fecha = data.fecha_referencia
            else:
                grupo.transaccion_padre.fecha = fecha_ref
        db.flush()

        # Regenerar cuotas desde n_pagadas + 1 hasta cantidad_cuotas
        monto_cuota = (grupo.total_financiado / grupo.cantidad_cuotas) if grupo.cantidad_cuotas else Decimal("0.00")
        crear_cuotas(
            db=db,
            transaccion_padre=grupo.transaccion_padre,
            grupo=grupo,
            cantidad_cuotas=grupo.cantidad_cuotas,
            primer_vencimiento=proximo_venc,
            monto_cuota=monto_cuota,
            usuario_id=str(usuario_id),
            cuota_inicial=n_pagadas + 1,
            desde_cuota=n_pagadas + 1
        )
        db.flush()
        db.expire(grupo, ["cuotas"])

    if data.descripcion is not None:
        desc_clean = data.descripcion.strip()
        grupo.descripcion = desc_clean
        if grupo.transaccion_padre:
            grupo.transaccion_padre.descripcion = desc_clean

        for c in grupo.cuotas:
            tx_hija = c.transaccion
            if tx_hija:
                if desc_clean:
                    if " (Cuota" in tx_hija.descripcion:
                        parts = tx_hija.descripcion.split(" (Cuota")
                        suffix = f" (Cuota{parts[-1]}"
                        tx_hija.descripcion = f"{desc_clean}{suffix}"
                    else:
                        tx_hija.descripcion = f"{desc_clean} (Cuota {c.numero_cuota}/{grupo.cantidad_cuotas})"
                else:
                    tx_hija.descripcion = f"Cuota {c.numero_cuota}/{grupo.cantidad_cuotas}"

    if data.categoria_id is not None:
        if grupo.transaccion_padre:
            grupo.transaccion_padre.categoria_id = data.categoria_id
            grupo.transaccion_padre.subcategoria_id = data.subcategoria_id

        for c in grupo.cuotas:
            if not c.pagada and c.transaccion:
                c.transaccion.categoria_id = data.categoria_id
                c.transaccion.subcategoria_id = data.subcategoria_id

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
                from app.services import transaccion_service
                if transaccion_service._afecta_saldo(tx_hija) and tx_hija.saldo_aplicado:
                    billetera = db.get(Billetera, tx_hija.billetera_id)
                    if billetera:
                        delta = monto_actual_cuota - old_monto
                        impacto = delta if tx_hija.tipo == TipoTransaccion.INGRESO else -delta
                        aplicar_impacto_saldo(billetera, impacto, tx_hija.fecha)

                tx_hija.monto = monto_actual_cuota

        grupo.monto_total = data.monto_total_nuevo
        grupo.total_financiado = data.monto_total_nuevo
        if grupo.transaccion_padre:
            grupo.transaccion_padre.monto = data.monto_total_nuevo

    if commit:
        db.commit()
        db.expire_all()
        db.refresh(grupo)
    else:
        db.flush()
    return grupo


def adelantar_grupo(
    db: Session,
    grupo_id: any,
    usuario_id: any,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> GrupoCuotas:
    """
    Adelanta las cuotas restantes de una compra con tarjeta al resumen destino
    (el primero que todavía no cerró).
    Toda cuota no pagada con fecha_vencimiento posterior al vencimiento destino pasa
    a ese vencimiento, y su transacción hija también.
    No cambia ningún monto, no se crea ninguna transacción y no se mueve ningún saldo.
    """
    from fastapi import HTTPException
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload, joinedload
    from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
    from app.models.tarjeta_credito import TarjetaCredito
    from app.models.transaccion import Transaccion
    from app.services import calendario_tarjeta_service
    from app.utils.fecha import hoy_argentina

    # 1. Buscar el grupo por grupo_id y usuario_id
    grupo = db.execute(
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas).joinedload(Cuota.transaccion),
            joinedload(GrupoCuotas.tarjeta)
        )
        .where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo de cuotas.")

    # 2. Solo para grupos con tarjeta_id y estado activo
    if not grupo.tarjeta_id:
        raise HTTPException(status_code=400, detail="Esta opción es solo para compras con tarjeta.")

    if grupo.estado != EstadoGrupoCuotas.ACTIVO:
        raise HTTPException(status_code=400, detail="El grupo no está activo")

    tarjeta = grupo.tarjeta or db.get(TarjetaCredito, grupo.tarjeta_id)
    if not tarjeta:
        raise HTTPException(status_code=400, detail="Esta opción es solo para compras con tarjeta.")

    # 3. Resumen destino = calendario_tarjeta_service.resumen_de_compra(tarjeta, hoy_argentina())
    hoy = hoy_argentina()
    _, vencimiento_destino = calendario_tarjeta_service.resumen_de_compra(tarjeta, hoy)

    # 4. Toda cuota no pagada con fecha_vencimiento posterior al vencimiento destino
    cuotas_a_mover = [
        c for c in grupo.cuotas
        if not c.pagada and c.fecha_vencimiento > vencimiento_destino
    ]

    # 5. Si no hay ninguna cuota para mover: 400 "No hay cuotas para adelantar."
    if not cuotas_a_mover:
        raise HTTPException(status_code=400, detail="No hay cuotas para adelantar.")

    for cuota in cuotas_a_mover:
        cuota.fecha_vencimiento = vencimiento_destino
        tx_hija = cuota.transaccion or (db.get(Transaccion, cuota.transaccion_id) if cuota.transaccion_id else None)
        if tx_hija:
            tx_hija.fecha = vencimiento_destino

    if commit:
        db.commit()
        db.refresh(grupo)
    else:
        db.flush()

    return grupo


