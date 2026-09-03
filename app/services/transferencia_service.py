import logging
from decimal import Decimal
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.transferencia_interna import TransferenciaInterna
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.models.usuario import Moneda

logger = logging.getLogger(__name__)


def obtener_transferencias(db: Session, usuario_id: UUID):
    return db.execute(
        select(TransferenciaInterna)
        .where(TransferenciaInterna.usuario_id == usuario_id)
        .order_by(desc(TransferenciaInterna.fecha), desc(TransferenciaInterna.fecha_creacion))
    ).scalars().all()


def obtener_transferencia(db: Session, usuario_id: UUID, transferencia_id: UUID) -> TransferenciaInterna:
    tr = db.execute(
        select(TransferenciaInterna).where(
            TransferenciaInterna.id == transferencia_id, 
            TransferenciaInterna.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not tr:
        raise HTTPException(status_code=404, detail="Transferencia no encontrada")
    return tr


def crear_transferencia(db: Session, usuario_id: UUID, data: TransferenciaInternaCreate) -> TransferenciaInterna:
    # 1. Validar billeteras
    b_origen = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_origen_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()
    
    b_destino = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_destino_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not b_origen:
        raise HTTPException(status_code=404, detail="No encontramos la billetera de origen.")
    if not b_destino:
        raise HTTPException(status_code=404, detail="No encontramos la billetera de destino.")

    # Validar que las billeteras estén activas
    if b_origen.estado != EstadoBilletera.ACTIVA:
        raise HTTPException(
            status_code=400,
            detail=f"La billetera de origen '{b_origen.nombre}' está archivada y no puede usarse para transferencias."
        )
    if b_destino.estado != EstadoBilletera.ACTIVA:
        raise HTTPException(
            status_code=400,
            detail=f"La billetera de destino '{b_destino.nombre}' está archivada y no puede usarse para transferencias."
        )

    # 2. Validar montos y monedas
    from app.services.transaccion_service import _validar_moneda_coincide
    _validar_moneda_coincide(data.moneda, b_origen)

    monto_origen = data.monto_origen if data.monto_origen is not None else data.monto
    if monto_origen <= Decimal("0"):
        raise HTTPException(status_code=400, detail="El monto de origen debe ser mayor a 0.")

    es_misma_moneda = (b_origen.moneda == b_destino.moneda)

    if es_misma_moneda:
        # Camino transferencias misma moneda (comportamiento intacto)
        monto_destino = data.monto_destino if data.monto_destino is not None else monto_origen
        if data.monto_destino is not None and data.monto_destino != monto_origen:
            raise HTTPException(
                status_code=400,
                detail="En transferencias entre billeteras de la misma moneda, el monto de destino debe ser igual al monto de origen."
            )
        moneda_origen = b_origen.moneda
        moneda_destino = b_destino.moneda
        cotizacion = None
    else:
        # Transferencia entre monedas distintas (Compra/Venta de moneda extranjera)
        if data.monto_destino is None or data.monto_destino <= Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail="Para transferencias entre billeteras de distinta moneda debes ingresar el monto recibido en destino."
            )
        monto_destino = data.monto_destino
        if data.moneda_destino:
            _validar_moneda_coincide(data.moneda_destino, b_destino)

        moneda_origen = b_origen.moneda
        moneda_destino = b_destino.moneda

        # Convención de cotización implícita: siempre ARS / USD (pesos por cada dólar)
        if moneda_origen == Moneda.ARS and moneda_destino == Moneda.USD:
            # Compra de dólares: pesos que salen / dólares que entran
            cotizacion = (monto_origen / monto_destino).quantize(Decimal("0.0001"))
        elif moneda_origen == Moneda.USD and moneda_destino == Moneda.ARS:
            # Venta de dólares: pesos que entran / dólares que salen
            cotizacion = (monto_destino / monto_origen).quantize(Decimal("0.0001"))
        else:
            cotizacion = (monto_origen / monto_destino).quantize(Decimal("0.0001"))

    # 3. Manejo de comisión opcional (gasto real)
    tx_comision_id = None
    monto_comision = None
    moneda_comision = None

    if data.monto_comision is not None and data.monto_comision > Decimal("0"):
        monto_comision = data.monto_comision
        moneda_comision = data.moneda_comision or b_origen.moneda

        # Determinar billetera para debitar la comisión
        if moneda_comision == b_origen.moneda:
            billetera_comision = b_origen
        elif moneda_comision == b_destino.moneda:
            billetera_comision = b_destino
        else:
            raise HTTPException(
                status_code=400,
                detail="La moneda de la comisión debe coincidir con la billetera de origen o de destino."
            )

        # Validar saldo suficiente considerando comisión
        if billetera_comision.id == b_origen.id:
            if b_origen.saldo_actual < (monto_origen + monto_comision):
                raise HTTPException(
                    status_code=400,
                    detail=f"Saldo insuficiente en {b_origen.nombre}. Disponible: {b_origen.saldo_actual}, Requerido total (transferencia + comisión): {monto_origen + monto_comision}."
                )
        else:
            if b_origen.saldo_actual < monto_origen:
                raise HTTPException(
                    status_code=400,
                    detail=f"Saldo insuficiente en {b_origen.nombre}. Disponible: {b_origen.saldo_actual}, Solicitado: {monto_origen}."
                )
            if (b_destino.saldo_actual + monto_destino) < monto_comision:
                raise HTTPException(
                    status_code=400,
                    detail=f"Saldo insuficiente en {b_destino.nombre} para cubrir la comisión de {monto_comision} {moneda_comision.value}."
                )

        # Generar transacción de egreso por la comisión (Categoría Banco -> Subcategoría Comisiones y gastos bancarios)
        from app.models.categoria import Categoria
        from app.models.subcategoria import Subcategoria
        from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion
        from app.schemas.transaccion import TransaccionCreate
        from app.services import transaccion_service

        cat_banco = db.query(Categoria).filter(Categoria.nombre.ilike("Banco")).first()
        subcat_comision = None
        if cat_banco:
            subcat_comision = db.query(Subcategoria).filter(
                Subcategoria.categoria_id == cat_banco.id,
                Subcategoria.nombre.ilike("%Comisiones%")
            ).first()

        tx_create = TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=monto_comision,
            moneda=moneda_comision,
            fecha=data.fecha,
            descripcion="Comisión por operación bancaria",
            categoria_id=cat_banco.id if cat_banco else None,
            subcategoria_id=subcat_comision.id if subcat_comision else None,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=billetera_comision.id,
            origen=OrigenTransaccion.MANUAL
        )
        # crear_transaccion descuenta el saldo de billetera_comision automáticamente
        tx_comision = transaccion_service.crear_transaccion(db, usuario_id, tx_create, commit=False)
        tx_comision_id = tx_comision.id
    else:
        # Sin comisión: validar saldo solo por monto_origen
        if b_origen.saldo_actual < monto_origen:
            raise HTTPException(
                status_code=400,
                detail=f"Saldo insuficiente en {b_origen.nombre}. Disponible: {b_origen.saldo_actual}, Solicitado: {monto_origen}."
            )

    # 4. Crear registro de TransferenciaInterna
    nueva_tr = TransferenciaInterna(
        usuario_id=usuario_id,
        billetera_origen_id=b_origen.id,
        billetera_destino_id=b_destino.id,
        monto=monto_origen,
        moneda=moneda_origen,
        monto_origen=monto_origen,
        monto_destino=monto_destino,
        moneda_origen=moneda_origen,
        moneda_destino=moneda_destino,
        cotizacion=cotizacion,
        transaccion_comision_id=tx_comision_id,
        monto_comision=monto_comision,
        moneda_comision=moneda_comision,
        fecha=data.fecha,
        notas=data.notas,
    )

    # 5. Impactar saldos de la transferencia propiamente dicha
    # Se descuenta de origen en su moneda y se suma a destino en la suya
    b_origen.saldo_actual -= monto_origen
    b_destino.saldo_actual += monto_destino

    db.add(nueva_tr)
    db.commit()
    db.refresh(nueva_tr)
    return nueva_tr


def eliminar_transferencia(db: Session, usuario_id: UUID, transferencia_id: UUID):
    tr = obtener_transferencia(db, usuario_id, transferencia_id)

    # Si tenía comisión vinculada, revertir y eliminar la transacción de gasto de comisión
    if tr.transaccion_comision_id:
        try:
            from app.services import transaccion_service
            transaccion_service.eliminar_transaccion(db, usuario_id, tr.transaccion_comision_id)
        except HTTPException as e:
            if e.status_code != 404:
                logger.error(f"Error al revertir comisión {tr.transaccion_comision_id} de transferencia {tr.id}: {e}")
                raise

    # Revertir impactos de saldos entre las billeteras
    b_origen = db.get(Billetera, tr.billetera_origen_id)
    b_destino = db.get(Billetera, tr.billetera_destino_id)

    if not b_origen:
        raise HTTPException(
            status_code=500,
            detail=f"Error crítico: No se encontró la billetera de origen para revertir la transferencia {tr.id}."
        )
    if not b_destino:
        raise HTTPException(
            status_code=500,
            detail=f"Error crítico: No se encontró la billetera de destino para revertir la transferencia {tr.id}."
        )

    monto_origen = tr.monto_origen if tr.monto_origen is not None else tr.monto
    monto_destino = tr.monto_destino if tr.monto_destino is not None else tr.monto

    # Revertir saldos por su moneda
    b_origen.saldo_actual += monto_origen
    b_destino.saldo_actual -= monto_destino

    db.delete(tr)
    db.commit()
    return {"detail": "Transferencia eliminada exitosamente"}
