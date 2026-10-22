from datetime import date, timezone
from decimal import Decimal
import logging
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.transaccion import (
    Transaccion,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
    TipoTransaccion,
    MetodoPago,
)
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import TarjetaCredito
from app.models.billetera import Billetera
from app.models.usuario import Moneda
from app.services.suscripcion_service import (
    obtener_precio_vigente,
    calcular_siguiente_cobro,
    normalizar_proximo_cobro,
)
from app.services.dolar_service import obtener_cotizacion_por_fecha
from app.services import calendario_tarjeta_service, cuotas_service
from app.services.saldo_billetera_service import aplicar_impacto_saldo
from app.utils.fecha import hoy_argentina, TZ_ARGENTINA

logger = logging.getLogger(__name__)


def contar_periodos_pendientes(fecha_proximo: date, frecuencia: str, hoy: date) -> int:
    """
    Cuenta cuántos períodos caen todavía en o antes de hoy.
    """
    count = 0
    cur = fecha_proximo
    while cur <= hoy:
        count += 1
        cur = calcular_siguiente_cobro(cur, frecuencia)
    return count


def _resolver_monto_y_conversion(
    db: Session,
    monto_precio: Decimal,
    moneda_precio_raw: any,
    moneda_destino_raw: any,
    fecha_cobro: date,
) -> tuple[Decimal, Moneda, Decimal | None, Moneda | None, Decimal | None, str | None] | None:
    """
    Resuelve el monto y campos de trazabilidad multimoneda.
    Retorna:
      (monto_final, moneda_final, monto_original, moneda_original, cotizacion_aplicada, tipo_dolar_usado)
    O None si se requería cotización y no se encontró (debe dejarse pendiente).
    """
    moneda_precio_str = moneda_precio_raw.value if hasattr(moneda_precio_raw, "value") else str(moneda_precio_raw)
    moneda_destino_str = moneda_destino_raw.value if hasattr(moneda_destino_raw, "value") else str(moneda_destino_raw)

    moneda_final_enum = Moneda.USD if moneda_destino_str == "USD" else Moneda.ARS
    moneda_orig_enum = Moneda.USD if moneda_precio_str == "USD" else Moneda.ARS

    if moneda_precio_str == moneda_destino_str:
        return (monto_precio, moneda_final_enum, None, None, None, None)

    # Monedas distintas: buscar cotización histórica oficial / tarjeta
    cot_obj = obtener_cotizacion_por_fecha(db, "tarjeta", fecha_cobro)
    if not cot_obj:
        cot_obj = obtener_cotizacion_por_fecha(db, "oficial", fecha_cobro)

    if not cot_obj:
        logger.warning(
            f"No hay cotización disponible para la fecha {fecha_cobro}. "
            "No se puede realizar la conversión multimoneda sin inventar valores."
        )
        return None

    cotizacion = Decimal(str(cot_obj.promedio or cot_obj.venta or cot_obj.compra))
    if cotizacion <= Decimal("0"):
        return None

    tipo_dolar = cot_obj.tipo

    if moneda_precio_str == "USD" and moneda_destino_str == "ARS":
        monto_final = (monto_precio * cotizacion).quantize(Decimal("0.01"))
    elif moneda_precio_str == "ARS" and moneda_destino_str == "USD":
        monto_final = (monto_precio / cotizacion).quantize(Decimal("0.01"))
    else:
        return None

    return (
        monto_final,
        moneda_final_enum,
        monto_precio,
        moneda_orig_enum,
        cotizacion,
        tipo_dolar,
    )


def _cobrar_suscripcion(db: Session, suscripcion: Suscripcion, hoy: date, primer_vencimiento: date | None = None) -> bool:
    """
    Crea la transacción (y cuota si es tarjeta) para un período de cobro.
    Avanza proximo_cobro al siguiente período exactamente desde la fecha que se está cobrando.
    Usa la fecha real del período como fecha de la transacción, para cotización y para vencimiento.
    Precio: el vigente en la fecha del período; si no hay, el vigente hoy.
    NO hace db.commit() — el llamador es responsable de commitear.
    """
    fecha_periodo_a_cobrar = suscripcion.proximo_cobro
    precio = obtener_precio_vigente(db, suscripcion.id, fecha_periodo_a_cobrar)
    if not precio:
        precio = obtener_precio_vigente(db, suscripcion.id, hoy)
    if not precio or precio.monto <= 0:
        return False

    if suscripcion.billetera_id:
        billetera = db.query(Billetera).filter(Billetera.id == suscripcion.billetera_id).first()
        if not billetera:
            return False

        datos_conversion = _resolver_monto_y_conversion(
            db, precio.monto, precio.moneda, billetera.moneda, fecha_periodo_a_cobrar
        )
        if datos_conversion is None:
            # Sin cotización disponible: no inventar valor, dejar pendiente
            return False

        (
            monto_final,
            moneda_final,
            monto_original,
            moneda_original,
            cotizacion_aplicada,
            tipo_dolar_usado,
        ) = datos_conversion

        tx = Transaccion(
            usuario_id=suscripcion.usuario_id,
            tipo=TipoTransaccion.EGRESO,
            monto=monto_final,
            moneda=moneda_final,
            fecha=fecha_periodo_a_cobrar,
            descripcion=suscripcion.nombre,
            categoria_id=suscripcion.categoria_id,
            subcategoria_id=suscripcion.subcategoria_id,
            billetera_id=suscripcion.billetera_id,
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.RECURRENTE,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            suscripcion_id=suscripcion.id,
            es_cuota_hija=False,
            es_padre_cuotas=False,
            monto_original=monto_original,
            moneda_original=moneda_original,
            cotizacion_aplicada=cotizacion_aplicada,
            tipo_dolar_usado=tipo_dolar_usado,
        )
        db.add(tx)
        # Debitar el saldo mediante la puerta única respetando la foto
        aplicar_impacto_saldo(billetera, -monto_final, fecha_periodo_a_cobrar)

    elif suscripcion.tarjeta_id:
        tarjeta = db.query(TarjetaCredito).filter(TarjetaCredito.id == suscripcion.tarjeta_id).first()
        if not tarjeta:
            return False

        billetera = db.query(Billetera).filter(Billetera.id == tarjeta.billetera_id).first()
        if not billetera:
            return False

        moneda_destino = billetera.moneda
        datos_conversion = _resolver_monto_y_conversion(
            db, precio.monto, precio.moneda, moneda_destino, fecha_periodo_a_cobrar
        )
        if datos_conversion is None:
            # Sin cotización disponible: no inventar valor, dejar pendiente
            return False

        (
            monto_final,
            moneda_final,
            monto_original,
            moneda_original,
            cotizacion_aplicada,
            tipo_dolar_usado,
        ) = datos_conversion

        # Si no se pasa primer_vencimiento explícito, calcularlo desde la fecha del período a cobrar
        if primer_vencimiento is None:
            _, primer_vencimiento = calendario_tarjeta_service.resumen_de_compra(
                tarjeta, fecha_periodo_a_cobrar, proximo_resumen=False
            )

        tx = Transaccion(
            usuario_id=suscripcion.usuario_id,
            tipo=TipoTransaccion.EGRESO,
            monto=monto_final,
            moneda=moneda_final,
            fecha=fecha_periodo_a_cobrar,
            descripcion=suscripcion.nombre,
            categoria_id=suscripcion.categoria_id,
            subcategoria_id=suscripcion.subcategoria_id,
            billetera_id=tarjeta.billetera_id,
            tarjeta_id=suscripcion.tarjeta_id,
            metodo_pago=MetodoPago.CREDITO,
            origen=OrigenTransaccion.RECURRENTE,
            estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
            suscripcion_id=suscripcion.id,
            es_cuota_hija=False,
            es_padre_cuotas=True,
            monto_original=monto_original,
            moneda_original=moneda_original,
            cotizacion_aplicada=cotizacion_aplicada,
            tipo_dolar_usado=tipo_dolar_usado,
        )
        db.add(tx)
        db.flush()

        grupo = GrupoCuotas(
            usuario_id=suscripcion.usuario_id,
            transaccion_padre_id=tx.id,
            tarjeta_id=suscripcion.tarjeta_id,
            descripcion=suscripcion.nombre,
            monto_total=monto_final,
            cantidad_cuotas=1,
            tiene_interes=False,
            tasa_interes=None,
            total_financiado=monto_final,
            moneda=moneda_final,
            primer_vencimiento=primer_vencimiento,
        )
        db.add(grupo)
        db.flush()

        tx.grupo_cuotas_id = grupo.id

        cuotas_service.crear_cuotas(
            db=db,
            transaccion_padre=tx,
            grupo=grupo,
            cantidad_cuotas=1,
            primer_vencimiento=primer_vencimiento,
            monto_cuota=monto_final,
            usuario_id=str(suscripcion.usuario_id),
            cuota_inicial=1,
        )
    else:
        return False

    # Avanzar la fecha exactamente un ciclo desde el período que se cobró
    frecuencia_str = suscripcion.frecuencia.value if hasattr(suscripcion.frecuencia, "value") else str(suscripcion.frecuencia)
    suscripcion.proximo_cobro = calcular_siguiente_cobro(fecha_periodo_a_cobrar, frecuencia_str)

    return True


def procesar_cobros_suscripciones(
    db: Session,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> dict:
    hoy = hoy_argentina()

    suscripciones = db.query(Suscripcion).filter(
        Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        or_(Suscripcion.billetera_id.isnot(None), Suscripcion.tarjeta_id.isnot(None)),
        Suscripcion.proximo_cobro <= hoy,
    ).all()

    cobradas = 0
    omitidas_idempotencia = 0
    pendientes_cotizacion = 0
    errores = 0
    detalles = []

    for suscripcion in suscripciones:
        fc = suscripcion.fecha_creacion
        if fc.tzinfo is None:
            fc = fc.replace(tzinfo=timezone.utc)
        fc_arg = fc.astimezone(TZ_ARGENTINA).date()

        tiene_tx = db.query(Transaccion).filter(Transaccion.suscripcion_id == suscripcion.id).first() is not None
        frecuencia_str = suscripcion.frecuencia.value if hasattr(suscripcion.frecuencia, "value") else str(suscripcion.frecuencia)

        # 5a. Si nunca se cobró y proximo_cobro es anterior al día de su fecha_creacion (en hora Argentina):
        # no cobra nada y pasa proximo_cobro a normalizar_proximo_cobro(...). Si queda en hoy, lo cobra como en la decisión 3.
        if (not tiene_tx) and (suscripcion.proximo_cobro < fc_arg):
            suscripcion.proximo_cobro = normalizar_proximo_cobro(suscripcion.proximo_cobro, frecuencia_str, hoy)
            if suscripcion.proximo_cobro == hoy:
                try:
                    with db.begin_nested():
                        exito = _cobrar_suscripcion(db, suscripcion, hoy)
                        if exito:
                            db.flush()
                            cobradas += 1
                            detalles.append({
                                "suscripcion_id": str(suscripcion.id),
                                "nombre": suscripcion.nombre,
                                "resultado": "cobrada",
                                "periodos_cobrados": 1,
                            })
                        else:
                            pendientes_cotizacion += 1
                            detalles.append({
                                "suscripcion_id": str(suscripcion.id),
                                "nombre": suscripcion.nombre,
                                "resultado": "pendiente_sin_cotizacion",
                            })
                except Exception as e:
                    errores += 1
                    logger.error(
                        f"Error al cobrar suscripción {suscripcion.id} del usuario {suscripcion.usuario_id}: {e}"
                    )
                    detalles.append({
                        "suscripcion_id": str(suscripcion.id),
                        "nombre": suscripcion.nombre,
                        "resultado": "error",
                        "error": str(e),
                    })
            else:
                detalles.append({
                    "suscripcion_id": str(suscripcion.id),
                    "nombre": suscripcion.nombre,
                    "resultado": "normalizada_futura",
                })
            continue

        # 5b. Si no: cobra todos los períodos pendientes en la misma corrida, cada uno con su fecha real, como mucho 12 por corrida.
        periodos_cobrados = 0
        periodos_procesados = 0
        suscripcion_error = False
        suscripcion_sin_cotiz = False
        suscripcion_idempotente = False

        while suscripcion.proximo_cobro <= hoy and periodos_procesados < 12:
            fecha_periodo = suscripcion.proximo_cobro

            # 5c. Idempotencia: si ya existe una transacción de esa suscripción con la fecha del período,
            # no crea otra y solo avanza la fecha.
            tx_existente = db.query(Transaccion).filter(
                Transaccion.suscripcion_id == suscripcion.id,
                Transaccion.fecha == fecha_periodo,
            ).first()

            if tx_existente:
                suscripcion.proximo_cobro = calcular_siguiente_cobro(fecha_periodo, frecuencia_str)
                suscripcion_idempotente = True
                periodos_procesados += 1
                continue

            try:
                with db.begin_nested():
                    exito = _cobrar_suscripcion(db, suscripcion, hoy)
                    if exito:
                        db.flush()
                        periodos_cobrados += 1
                        periodos_procesados += 1
                    else:
                        suscripcion_sin_cotiz = True
                        break
            except Exception as e:
                suscripcion_error = True
                logger.error(
                    f"Error al cobrar suscripción {suscripcion.id} del usuario {suscripcion.usuario_id}: {e}"
                )
                errores += 1
                detalles.append({
                    "suscripcion_id": str(suscripcion.id),
                    "nombre": suscripcion.nombre,
                    "resultado": "error",
                    "error": str(e),
                })
                break

        if periodos_cobrados > 0:
            cobradas += 1
            detalles.append({
                "suscripcion_id": str(suscripcion.id),
                "nombre": suscripcion.nombre,
                "resultado": "cobrada",
                "periodos_cobrados": periodos_cobrados,
            })
            # 5d. El aviso de "período atrasado" se manda solo si en la corrida se cobró más de un período,
            # una sola vez, con el texto exacto: "Anotamos {n} cobros de '{nombre}' que estaban pendientes."
            if periodos_cobrados > 1:
                from app.services.notificacion_service import crear_notificacion
                from app.models.notificacion import TipoNotificacion, NivelNotificacion

                crear_notificacion(
                    db=db,
                    usuario_id=suscripcion.usuario_id,
                    tipo=TipoNotificacion.SUSCRIPCION_HOY,
                    nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                    mensaje=f"Anotamos {periodos_cobrados} cobros de '{suscripcion.nombre}' que estaban pendientes.",
                    entidad_tipo="suscripcion",
                    entidad_id=suscripcion.id,
                    grupo_agrupacion_override=f"SUSCRIPCION_ATRASADA_{suscripcion.id}_{hoy.strftime('%Y%m%d')}",
                    commit=False,
                )
        elif suscripcion_sin_cotiz:
            pendientes_cotizacion += 1
            detalles.append({
                "suscripcion_id": str(suscripcion.id),
                "nombre": suscripcion.nombre,
                "resultado": "pendiente_sin_cotizacion",
            })
        elif suscripcion_idempotente and not suscripcion_error:
            omitidas_idempotencia += 1
            detalles.append({
                "suscripcion_id": str(suscripcion.id),
                "nombre": suscripcion.nombre,
                "resultado": "omitida_idempotencia",
            })

    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "total_encontradas": len(suscripciones),
        "cobradas": cobradas,
        "omitidas_idempotencia": omitidas_idempotencia,
        "pendientes_cotizacion": pendientes_cotizacion,
        "errores": errores,
        "detalles": detalles,
    }
