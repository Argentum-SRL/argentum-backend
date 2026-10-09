"""
Servicio para el pago de resúmenes de tarjeta de crédito y simulación de pesificación.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.saldo_arrastrado import (
    EstadoSaldoArrastrado,
    PagoSaldoArrastrado,
    SaldoArrastradoTarjeta,
)
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda
from app.schemas.tarjeta_credito import (
    CuotaPendienteOtraMoneda,
    ResultadoPagoTarjeta,
    SimularPesificacionResponse,
)
from app.services.resumen_tarjeta_service import (
    _tabla_saldo_arrastrado_existe,
    calcular_resumen_actual,
)
from app.services.tarjeta_service import (
    calcular_fecha_cierre_de_vencimiento,
    calcular_fecha_vencimiento_proximo,
    get_info_transaccion,
)
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto

logger = logging.getLogger(__name__)


def _subcategoria_impuestos_banco(db: Session, categoria_banco: Categoria | None) -> Subcategoria | None:
    """
    Busca o crea la subcategoría 'Impuestos' dentro de la categoría Banco recibida.
    """
    if not categoria_banco:
        return None
    subcat_impuestos = db.query(Subcategoria).filter(
        Subcategoria.categoria_id == categoria_banco.id,
        Subcategoria.nombre.ilike("Impuestos")
    ).first()

    if not subcat_impuestos:
        subcat_impuestos = Subcategoria(
            categoria_id=categoria_banco.id,
            nombre="Impuestos",
            orden=10
        )
        db.add(subcat_impuestos)
        db.flush()

    return subcat_impuestos


def pagar_resumen_tarjeta(
    db: Session,
    usuario_id: UUID,
    tarjeta_id: UUID,
    fecha_pago: date | None = None,
    fecha_resumen: date | None = None,
    monto: Decimal | None = None,
    moneda: Moneda | None = None,
    billetera_id: UUID | None = None,
    pesificar: bool = False,
    cotizacion_personalizada: Decimal | None = None,
    monto_pesos_personalizado: Decimal | None = None,
    monto_percepcion_personalizado: Decimal | None = None,
    diferencia_tipo: Literal["cargos_banco", "compras_no_cargadas"] = "cargos_banco",
    diferencia_categoria_id: UUID | None = None,
    diferencia_subcategoria_id: UUID | None = None,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> Transaccion:
    # 1. Obtener la tarjeta
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    if not tarjeta:
        raise HTTPException(status_code=404, detail="No encontramos esa tarjeta.")

    # Tarea 3.1: Moneda a pagar (por defecto la moneda de la tarjeta si no viene)
    moneda_a_pagar = moneda or tarjeta.moneda
    moneda_str = moneda_a_pagar.value if hasattr(moneda_a_pagar, "value") else str(moneda_a_pagar)
    tarjeta_moneda_str = tarjeta.moneda.value if hasattr(tarjeta.moneda, "value") else str(tarjeta.moneda)

    # 2. Calcular la fecha de vencimiento límite a pagar
    if fecha_resumen is not None:
        limite_vencimiento = fecha_resumen
    else:
        hoy = hoy_argentina()
        limite_vencimiento = calcular_fecha_vencimiento_proximo(tarjeta, hoy)

    # 2.1 Buscar saldos arrastrados activos de la tarjeta de ESTA moneda hasta este vencimiento (Tarea 3.2 y 3.9)
    if _tabla_saldo_arrastrado_existe(db):
        saldos_activos = (
            db.query(SaldoArrastradoTarjeta)
            .filter(
                SaldoArrastradoTarjeta.tarjeta_id == tarjeta.id,
                SaldoArrastradoTarjeta.estado == EstadoSaldoArrastrado.ACTIVO,
                SaldoArrastradoTarjeta.moneda == moneda_a_pagar,
                SaldoArrastradoTarjeta.fecha_vencimiento_resumen <= limite_vencimiento
            )
            .order_by(SaldoArrastradoTarjeta.fecha_vencimiento_resumen.asc())
            .all()
        )
    else:
        saldos_activos = []

    saldo_actual_activo = next((s for s in saldos_activos if s.fecha_vencimiento_resumen == limite_vencimiento), None)
    saldos_anteriores_activos = [s for s in saldos_activos if s.fecha_vencimiento_resumen < limite_vencimiento]

    # 3. Obtener cuotas impagas hasta el límite de vencimiento
    cuotas_a_pagar = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(
            GrupoCuotas.tarjeta_id == tarjeta.id,
            Cuota.pagada == False,
            Cuota.fecha_vencimiento <= limite_vencimiento
        )
        .options(
            joinedload(Cuota.grupo),
            joinedload(Cuota.transaccion).joinedload(Transaccion.subcategoria)
        )
        .all()
    )

    cuotas_coincidentes = []
    cuotas_otra_moneda = []
    for c in cuotas_a_pagar:
        c_moneda = (
            c.grupo.moneda.value if hasattr(c.grupo.moneda, "value") else str(c.grupo.moneda)
        ) if (c.grupo and c.grupo.moneda) else tarjeta_moneda_str
        if c_moneda == moneda_str:
            cuotas_coincidentes.append(c)
        else:
            cuotas_otra_moneda.append(c)

    monto_cuotas = sum(
        (c.monto_real if c.monto_real is not None else c.monto_proyectado)
        for c in cuotas_coincidentes
    )
    monto_saldos_anteriores = sum(s.monto_restante for s in saldos_anteriores_activos)
    monto_saldo_actual = saldo_actual_activo.monto_restante if saldo_actual_activo else Decimal("0")

    # Si ya existe un saldo arrastrado activo para este mismo vencimiento y moneda
    if saldo_actual_activo:
        total_a_pagar = monto_saldo_actual + monto_saldos_anteriores
    else:
        total_a_pagar = monto_cuotas + monto_saldos_anteriores

    if total_a_pagar <= Decimal("0"):
        if cuotas_otra_moneda:
            raise HTTPException(
                status_code=400,
                detail=f"No hay deuda pendiente en {moneda_str}. Quedan {len(cuotas_otra_moneda)} cuota(s) en otra moneda pendientes de pago."
            )
        raise HTTPException(status_code=400, detail="Este resumen ya está completamente saldado.")

    # 4. Validar monto si se proporcionó
    excedente = None
    if monto is not None:
        if monto <= Decimal("0"):
            raise HTTPException(status_code=400, detail="El monto a pagar tiene que ser mayor a cero.")
        if monto > total_a_pagar:
            if pesificar:
                raise HTTPException(
                    status_code=400,
                    detail=f"El monto a pagar ({formatear_monto(monto, moneda_a_pagar)}) no puede superar el total a pagar del resumen ({formatear_monto(total_a_pagar, moneda_a_pagar)})."
                )
            if diferencia_tipo == "compras_no_cargadas" and not diferencia_categoria_id:
                raise HTTPException(
                    status_code=400,
                    detail="Elegí la categoría de las compras que no cargaste."
                )
            monto_pago = total_a_pagar
            excedente = monto - total_a_pagar
        else:
            monto_pago = monto
    else:
        monto_pago = total_a_pagar

    # 5. Determinar billetera de débito, modo pesificación y cotización (Tareas 3.3, 3.4, 3.5, 3.6, 3.7)
    es_pesificacion = False
    monto_convertido = None
    monto_percepcion = None
    cotizacion = None
    tipo_dolar = None

    if moneda_a_pagar == Moneda.ARS:
        billetera_pago_id = billetera_id or tarjeta.billetera_id
        billetera_pago = db.get(Billetera, billetera_pago_id)
        if not billetera_pago:
            raise HTTPException(status_code=404, detail="No encontramos la billetera seleccionada.")
        if getattr(billetera_pago, "es_inversion", False):
            raise HTTPException(
                status_code=400,
                detail="Esta billetera no admite pagos de tarjeta ni cuotas."
            )
        if billetera_pago.moneda != Moneda.ARS:
            raise HTTPException(status_code=400, detail="Para pagar en pesos debés seleccionar una billetera en pesos.")
        monto_debito = monto_pago
        moneda_debito = Moneda.ARS
    elif moneda_a_pagar == Moneda.USD:
        if not pesificar:
            # Opción a): Pagar en dólares desde billetera USD
            if not billetera_id:
                billeteras_usd = db.query(Billetera).filter(
                    Billetera.usuario_id == usuario_id,
                    Billetera.moneda == Moneda.USD,
                    Billetera.estado == "activa",
                    Billetera.es_inversion == False
                ).all()
                if not billeteras_usd:
                    raise HTTPException(
                        status_code=400,
                        detail="No tenés ninguna billetera en dólares disponible para realizar este pago. Podés pesificar los consumos en dólares para pagarlos en pesos desde tu cuenta bancaria."
                    )
                billetera_pago = billeteras_usd[0]
            else:
                billetera_pago = db.query(Billetera).filter(
                    Billetera.id == billetera_id,
                    Billetera.usuario_id == usuario_id
                ).first()
                if not billetera_pago:
                    raise HTTPException(status_code=404, detail="No encontramos la billetera seleccionada.")
                if getattr(billetera_pago, "es_inversion", False):
                    raise HTTPException(
                        status_code=400,
                        detail="Esta billetera no admite pagos de tarjeta ni cuotas."
                    )
                if billetera_pago.moneda != Moneda.USD:
                    raise HTTPException(
                        status_code=400,
                        detail="La billetera seleccionada debe ser en dólares (USD). Si preferís pagar en pesos, elegí la opción de pesificar."
                    )
            billetera_pago_id = billetera_pago.id
            monto_debito = monto_pago
            moneda_debito = Moneda.USD
        else:
            # Opción b): Pesificar consumos en USD
            es_pesificacion = True
            billetera_pago_id = billetera_id or tarjeta.billetera_id
            billetera_pago = db.get(Billetera, billetera_pago_id)
            if not billetera_pago:
                raise HTTPException(status_code=404, detail="No encontramos la billetera seleccionada.")
            if getattr(billetera_pago, "es_inversion", False):
                raise HTTPException(
                    status_code=400,
                    detail="Esta billetera no admite pagos de tarjeta ni cuotas."
                )
            if billetera_pago.moneda != Moneda.ARS:
                raise HTTPException(status_code=400, detail="Para pesificar consumos en dólares debés usar una billetera en pesos.")

            # Cotización dólar oficial de la fecha de cierre (Tarea 3.5 y 3.6)
            from app.services.dolar_service import obtener_cotizacion_por_fecha
            fecha_cierre = calcular_fecha_cierre_de_vencimiento(
                limite_vencimiento, tarjeta.dia_cierre, tarjeta.dia_vencimiento
            )

            if cotizacion_personalizada is not None and cotizacion_personalizada > Decimal("0"):
                cotizacion = cotizacion_personalizada
            else:
                cot_obj = obtener_cotizacion_por_fecha(db, "oficial", fecha_cierre)
                if not cot_obj:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No hay cotización oficial disponible para la fecha de cierre ({fecha_cierre}). Por favor, ingresá la cotización manualmente."
                    )
                cotizacion = Decimal(str(cot_obj.promedio or cot_obj.venta))

            tipo_dolar = "oficial"
            if monto_pesos_personalizado is not None and monto_pesos_personalizado > Decimal("0"):
                monto_convertido = monto_pesos_personalizado
            else:
                monto_convertido = (monto_pago * cotizacion).quantize(Decimal("0.01"))

            porcentaje_percep = getattr(tarjeta, "percepcion_moneda_extranjera", Decimal("30.00"))
            if monto_percepcion_personalizado is not None and monto_percepcion_personalizado >= Decimal("0"):
                monto_percepcion = monto_percepcion_personalizado
            else:
                monto_percepcion = (monto_convertido * (porcentaje_percep / Decimal("100"))).quantize(Decimal("0.01"))

            monto_debito = monto_convertido
            moneda_debito = Moneda.ARS

    # 6. Detectar si ya existe una transacción de pago PENDIENTE para este resumen, vencimiento y moneda
    from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion, EstadoVerificacionTransaccion
    tx_existente = db.query(Transaccion).filter(
        Transaccion.tarjeta_id == tarjeta.id,
        Transaccion.pago_resumen_vencimiento == limite_vencimiento,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE,
        Transaccion.moneda == moneda_debito
    ).first()

    # 7. Buscar categoría "Banco" y subcategorías
    from app.models.categoria import Categoria
    from app.models.subcategoria import Subcategoria

    categoria = db.query(Categoria).filter(Categoria.nombre.ilike("Banco")).first()
    subcategoria = None
    if categoria:
        subcategoria = db.query(Subcategoria).filter(
            Subcategoria.categoria_id == categoria.id,
            Subcategoria.nombre.ilike("Tarjeta%de%crédito") | Subcategoria.nombre.ilike("Tarjetas%de%crédito")
        ).first()

    from app.schemas.transaccion import TransaccionCreate
    from app.services import transaccion_service

    ultimos_4 = tarjeta.nombre[-4:] if len(tarjeta.nombre) >= 4 else tarjeta.nombre
    if es_pesificacion:
        descripcion_pago = f"Pago resumen {ultimos_4} ({formatear_monto(monto_pago, Moneda.USD)})"
    elif moneda_a_pagar == Moneda.USD:
        descripcion_pago = f"Pago resumen {ultimos_4} (USD)"
    else:
        descripcion_pago = f"Pago resumen {ultimos_4}"

    fecha_transaccion = fecha_pago or transaccion_service._hoy_argentina()

    try:
        # Reutilizar transacción pendiente si existe
        if tx_existente:
            tx = tx_existente
            tx.monto = monto_debito
            tx.fecha = fecha_transaccion
            tx.descripcion = descripcion_pago
            if es_pesificacion:
                tx.monto_original = monto_pago
                tx.moneda_original = Moneda.USD
                tx.cotizacion_aplicada = cotizacion
                tx.tipo_dolar_usado = tipo_dolar
            if categoria:
                tx.categoria_id = categoria.id
            if subcategoria:
                tx.subcategoria_id = subcategoria.id
            tx.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA

            billetera = db.get(Billetera, tx.billetera_id)
            if billetera:
                billetera.saldo_actual -= monto_debito
        else:
            tx_data = TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=monto_debito,
                moneda=moneda_debito,
                fecha=fecha_transaccion,
                descripcion=descripcion_pago,
                categoria_id=categoria.id if categoria else None,
                subcategoria_id=subcategoria.id if subcategoria else None,
                metodo_pago=MetodoPago.DEBITO,
                billetera_id=billetera_pago_id,
                tarjeta_id=tarjeta.id,
                es_cuota_hija=False,
                es_padre_cuotas=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                pago_resumen_vencimiento=limite_vencimiento,
                monto_original=monto_pago if es_pesificacion else None,
                moneda_original=Moneda.USD if es_pesificacion else None,
                cotizacion_aplicada=cotizacion if es_pesificacion else None,
                tipo_dolar_usado=tipo_dolar if es_pesificacion else None
            )
            tx = transaccion_service.crear_transaccion(db, usuario_id, tx_data, commit=False)

        # Tarea 4: Registrar percepción impositiva como gasto propio si hubo pesificación
        tx_percepcion = None
        if es_pesificacion and monto_percepcion is not None and monto_percepcion > Decimal("0"):
            subcat_impuestos = _subcategoria_impuestos_banco(db, categoria)

            tx_percepcion_data = TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=monto_percepcion,
                moneda=Moneda.ARS,
                fecha=fecha_transaccion,
                descripcion=f"Percepción compras exterior ({porcentaje_percep:.0f}%) - Pago resumen {ultimos_4}",
                categoria_id=categoria.id if categoria else None,
                subcategoria_id=subcat_impuestos.id if subcat_impuestos else (subcategoria.id if subcategoria else None),
                metodo_pago=MetodoPago.DEBITO,
                billetera_id=billetera_pago_id,
                tarjeta_id=tarjeta.id,
                es_cuota_hija=False,
                es_padre_cuotas=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                pago_origen_id=tx.id
            )
            tx_percepcion = transaccion_service.crear_transaccion(db, usuario_id, tx_percepcion_data, commit=False)

        # Registrar egreso por excedente/diferencia vinculada al pago si hubo monto mayor al total
        if excedente is not None and excedente > Decimal("0"):
            if diferencia_tipo == "cargos_banco":
                subcat_cargos = _subcategoria_impuestos_banco(db, categoria)
                cat_excedente_id = categoria.id if categoria else None
                subcat_excedente_id = subcat_cargos.id if subcat_cargos else None
                desc_excedente = f"Cargos del resumen {ultimos_4}"
            else:
                cat_excedente_id = diferencia_categoria_id
                subcat_excedente_id = diferencia_subcategoria_id
                desc_excedente = f"Compras no cargadas - Resumen {ultimos_4}"

            tx_excedente_data = TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=excedente,
                moneda=moneda_debito,
                fecha=fecha_transaccion,
                descripcion=desc_excedente,
                categoria_id=cat_excedente_id,
                subcategoria_id=subcat_excedente_id,
                metodo_pago=MetodoPago.DEBITO,
                billetera_id=billetera_pago_id,
                tarjeta_id=tarjeta.id,
                es_cuota_hija=False,
                es_padre_cuotas=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                pago_origen_id=tx.id,
                pago_resumen_vencimiento=None
            )
            transaccion_service.crear_transaccion(db, usuario_id, tx_excedente_data, commit=False)

        # 8. Aplicación del pago sobre cuotas y saldos de la moneda pagada (en monto_pago original)
        monto_disponible = monto_pago

        # 8.1 Orden bancario: El saldo arrastrado anterior de esta moneda se cancela primero
        for s_ant in saldos_anteriores_activos:
            if monto_disponible <= Decimal("0"):
                break
            aplicar = min(monto_disponible, s_ant.monto_restante)
            s_ant.monto_restante -= aplicar
            if s_ant.monto_restante <= Decimal("0"):
                s_ant.monto_restante = Decimal("0")
                s_ant.estado = EstadoSaldoArrastrado.SALDADO
            pago_red = PagoSaldoArrastrado(
                saldo_arrastrado_id=s_ant.id,
                transaccion_pago_id=tx.id,
                monto_aplicado=aplicar
            )
            db.add(pago_red)
            monto_disponible -= aplicar

        saldo_generado = None
        saldo_restante_final = None

        if saldo_actual_activo:
            # Segundo pago parcial sobre el mismo resumen
            if monto_disponible > Decimal("0"):
                aplicar = min(monto_disponible, saldo_actual_activo.monto_restante)
                saldo_actual_activo.monto_restante -= aplicar
                if saldo_actual_activo.monto_restante <= Decimal("0"):
                    saldo_actual_activo.monto_restante = Decimal("0")
                    saldo_actual_activo.estado = EstadoSaldoArrastrado.SALDADO
                pago_red = PagoSaldoArrastrado(
                    saldo_arrastrado_id=saldo_actual_activo.id,
                    transaccion_pago_id=tx.id,
                    monto_aplicado=aplicar
                )
                db.add(pago_red)
                monto_disponible -= aplicar
            saldo_restante_final = saldo_actual_activo.monto_restante
        else:
            # Primer pago sobre este resumen para esta moneda:
            for cuota in cuotas_coincidentes:
                cuota.pagada = True
                cuota.transaccion_pago_id = tx.id

            # Tarea 3.8 y 3.9: Si el pago no cubrió el total a pagar, lo que queda impago se registra
            # como saldo arrastrado conservando la moneda del resumen (un saldo en dólares se arrastra en dólares).
            if monto_pago < total_a_pagar:
                saldo_generado = total_a_pagar - monto_pago
                saldo_restante_final = saldo_generado
                nuevo_saldo = SaldoArrastradoTarjeta(
                    tarjeta_id=tarjeta.id,
                    fecha_vencimiento_resumen=limite_vencimiento,
                    monto_inicial=saldo_generado,
                    monto_restante=saldo_generado,
                    moneda=moneda_a_pagar,
                    estado=EstadoSaldoArrastrado.ACTIVO,
                    transaccion_origen_id=tx.id
                )
                db.add(nuevo_saldo)

        if commit:
            db.commit()
            db.refresh(tx)
        else:
            db.flush()

        pendientes = []
        for c in cuotas_otra_moneda:
            c_moneda = (
                c.grupo.moneda.value if hasattr(c.grupo.moneda, "value") else str(c.grupo.moneda)
            ) if (c.grupo and c.grupo.moneda) else "USD"
            desc_final, _ = get_info_transaccion(c)
            m = c.monto_real if c.monto_real is not None else c.monto_proyectado
            pendientes.append(CuotaPendienteOtraMoneda(
                id=c.transaccion_id,
                descripcion=desc_final,
                monto=m,
                moneda=c_moneda,
                numero_cuota=c.numero_cuota,
                total_cuotas=c.grupo.cantidad_cuotas if c.grupo else 1,
                fecha_vencimiento=c.fecha_vencimiento
            ))

        mensaje_adv = None
        if cuotas_otra_moneda:
            otra_m_nombre = "dólares" if moneda_str == "ARS" else "pesos"
            mensaje_adv = f"Se pagaron {len(cuotas_coincidentes)} cuota(s) en {moneda_str}. Quedaron {len(cuotas_otra_moneda)} cuota(s) en {otra_m_nombre} pendientes de pago."

        setattr(tx, "cuotas_pagadas_count", len(cuotas_coincidentes))
        setattr(tx, "moneda_pagada", moneda_str)
        setattr(tx, "monto_pagado", monto_pago)
        setattr(tx, "saldo_arrastrado_generado", saldo_generado)
        setattr(tx, "saldo_arrastrado_restante", saldo_restante_final)
        setattr(tx, "cuotas_pendientes_otra_moneda", pendientes)
        setattr(tx, "mensaje_advertencia", mensaje_adv)
        setattr(tx, "monto_diferencia", excedente)

        if es_pesificacion:
            setattr(tx, "transaccion_percepcion_id", tx_percepcion.id if tx_percepcion else None)
            setattr(tx, "monto_percepcion", monto_percepcion)
            setattr(tx, "monto_convertido_pesos", monto_convertido)
            setattr(tx, "monto_pesos_total", (monto_convertido + monto_percepcion) if monto_percepcion else monto_convertido)
            setattr(tx, "monto_original", monto_pago)
            setattr(tx, "moneda_original", moneda_str)
            setattr(tx, "cotizacion_aplicada", cotizacion)
            setattr(tx, "tipo_dolar_usado", tipo_dolar)

        return tx
    except Exception:
        db.rollback()
        logger.exception("Error al pagar resumen de tarjeta %s", tarjeta_id)
        raise



def simular_pesificacion(
    db: Session,
    usuario_id: UUID,
    tarjeta_id: UUID,
    fecha_resumen: date | None = None,
    monto_usd: Decimal | None = None
) -> SimularPesificacionResponse:
    """
    Simula la pesificación del saldo en dólares de un resumen de tarjeta.
    Propone la cotización oficial de cierre, calcula monto convertido, percepción y total en pesos.
    """
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    if not tarjeta:
        raise HTTPException(status_code=404, detail="No encontramos esa tarjeta.")

    if fecha_resumen is not None:
        limite_vencimiento = fecha_resumen
    else:
        hoy = hoy_argentina()
        limite_vencimiento = calcular_fecha_vencimiento_proximo(tarjeta, hoy)

    fecha_cierre = calcular_fecha_cierre_de_vencimiento(
        limite_vencimiento, tarjeta.dia_cierre, tarjeta.dia_vencimiento
    )

    if monto_usd is None or monto_usd <= Decimal("0"):
        res = calcular_resumen_actual(db, tarjeta)
        bloque_usd = res.totales_por_moneda.get("USD")
        monto_usd = bloque_usd.total_a_pagar if bloque_usd else Decimal("0")

    porcentaje_percep = getattr(tarjeta, "percepcion_moneda_extranjera", Decimal("30.00"))

    from app.services.dolar_service import obtener_cotizacion_por_fecha
    cot_obj = obtener_cotizacion_por_fecha(db, "oficial", fecha_cierre)
    if cot_obj is not None:
        cot_val = Decimal(str(cot_obj.promedio or cot_obj.venta))
        monto_conv = (monto_usd * cot_val).quantize(Decimal("0.01"))
        monto_percep = (monto_conv * (porcentaje_percep / Decimal("100"))).quantize(Decimal("0.01"))
        total_ars = monto_conv + monto_percep
        return SimularPesificacionResponse(
            fecha_cierre=fecha_cierre,
            monto_usd=monto_usd,
            cotizacion_oficial=cot_val,
            cotizacion_disponible=True,
            porcentaje_percepcion=porcentaje_percep,
            monto_convertido_ars=monto_conv,
            monto_percepcion_ars=monto_percep,
            total_estimado_ars=total_ars
        )
    else:
        return SimularPesificacionResponse(
            fecha_cierre=fecha_cierre,
            monto_usd=monto_usd,
            cotizacion_oficial=None,
            cotizacion_disponible=False,
            porcentaje_percepcion=porcentaje_percep,
            monto_convertido_ars=None,
            monto_percepcion_ars=None,
            total_estimado_ars=None
        )

