from datetime import date
import logging
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.transaccion import Transaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import TarjetaCredito
from app.models.billetera import Billetera
from app.services.suscripcion_service import obtener_precio_vigente, calcular_siguiente_cobro
from app.services import cuotas_service, tarjeta_service
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)


def _cobrar_suscripcion(db: Session, suscripcion: Suscripcion, hoy: date, primer_vencimiento: date | None = None) -> bool:
    """
    Crea la transacción (y cuota si es tarjeta) para un período de cobro.
    Avanza proximo_cobro al siguiente período.
    NO hace db.commit() — el llamador es responsable de commitear.

    primer_vencimiento: fecha de vencimiento explícita para tarjeta.
      - Si se pasa None, se calcula con calcular_primer_vencimiento (comportamiento estándar).
      - Al crear la suscripción se pasa fecha_vencimiento_proximo de la tarjeta para que
        el primer cargo siempre caiga en el Resumen Actual.
    """
    precio = obtener_precio_vigente(db, suscripcion.id, hoy)
    if not precio or precio.monto <= 0:
        return False

    if suscripcion.billetera_id:
        billetera = db.query(Billetera).filter(Billetera.id == suscripcion.billetera_id).first()
        if not billetera:
            return False

        from app.services.transaccion_service import _validar_moneda_coincide
        _validar_moneda_coincide(precio.moneda, billetera)

        tx = Transaccion(
            usuario_id=suscripcion.usuario_id,
            tipo='egreso',
            monto=precio.monto,
            moneda=precio.moneda,
            fecha=hoy,
            descripcion=suscripcion.nombre,
            categoria_id=suscripcion.categoria_id,
            subcategoria_id=suscripcion.subcategoria_id,
            billetera_id=suscripcion.billetera_id,
            metodo_pago='debito',
            origen='recurrente',
            estado_verificacion='confirmada',
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db.add(tx)
        # Debitar el saldo inmediatamente — es un cobro automático confirmado
        billetera.saldo_actual -= precio.monto

    elif suscripcion.tarjeta_id:
        tarjeta = db.query(TarjetaCredito).filter(TarjetaCredito.id == suscripcion.tarjeta_id).first()
        if not tarjeta:
            return False

        billetera = db.query(Billetera).filter(Billetera.id == tarjeta.billetera_id).first()
        if not billetera:
            return False

        from app.services.transaccion_service import _validar_moneda_coincide
        _validar_moneda_coincide(precio.moneda, billetera)

        # Si no se pasa primer_vencimiento explícito, calcularlo desde la fecha del cobro
        if primer_vencimiento is None:
            primer_vencimiento = tarjeta_service.calcular_primer_vencimiento(
                hoy, tarjeta.dia_cierre, tarjeta.dia_vencimiento, False
            )

        tx = Transaccion(
            usuario_id=suscripcion.usuario_id,
            tipo='egreso',
            monto=precio.monto,
            moneda=precio.moneda,
            fecha=hoy,
            descripcion=suscripcion.nombre,
            categoria_id=suscripcion.categoria_id,
            subcategoria_id=suscripcion.subcategoria_id,
            billetera_id=tarjeta.billetera_id,
            tarjeta_id=suscripcion.tarjeta_id,
            metodo_pago='credito',
            origen='recurrente',
            estado_verificacion='pendiente',
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=True,
        )
        db.add(tx)
        db.flush()

        grupo = GrupoCuotas(
            usuario_id=suscripcion.usuario_id,
            transaccion_padre_id=tx.id,
            tarjeta_id=suscripcion.tarjeta_id,
            descripcion=suscripcion.nombre,
            monto_total=precio.monto,
            cantidad_cuotas=1,
            tiene_interes=False,
            tasa_interes=None,
            total_financiado=precio.monto,
            moneda=precio.moneda,
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
            monto_cuota=precio.monto,
            usuario_id=str(suscripcion.usuario_id),
            cuota_inicial=1,
        )
    else:
        return False

    suscripcion.proximo_cobro = calcular_siguiente_cobro(hoy, suscripcion.frecuencia.value)
    return True


def procesar_cobros_suscripciones(db: Session) -> None:
    hoy = hoy_argentina()

    suscripciones = db.query(Suscripcion).filter(
        Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        or_(Suscripcion.billetera_id.isnot(None), Suscripcion.tarjeta_id.isnot(None)),
        Suscripcion.proximo_cobro <= hoy,
    ).all()

    for suscripcion in suscripciones:
        # Idempotencia: no duplicar si el job corrió dos veces el mismo día
        query_existe = db.query(Transaccion).filter(
            Transaccion.usuario_id == suscripcion.usuario_id,
            Transaccion.fecha == hoy,
            Transaccion.estado_verificacion == 'pendiente',
            Transaccion.descripcion == suscripcion.nombre,
        )
        if suscripcion.billetera_id:
            query_existe = query_existe.filter(Transaccion.billetera_id == suscripcion.billetera_id)
        elif suscripcion.tarjeta_id:
            query_existe = query_existe.filter(Transaccion.es_padre_cuotas == True)

        if query_existe.first():
            continue

        try:
            _cobrar_suscripcion(db, suscripcion, hoy)
        except Exception as e:
            logger.error(
                f"Error al cobrar suscripción {suscripcion.id} del usuario {suscripcion.usuario_id}: {e}"
            )

    db.commit()
