from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.transaccion import Transaccion
from app.services.suscripcion_service import (
    obtener_precio_vigente,
    calcular_siguiente_cobro
)

def procesar_cobros_suscripciones(db: Session) -> None:
    hoy = date.today()

    # Solo suscripciones activas con billetera vinculada que vencen hoy
    suscripciones = db.query(Suscripcion).filter(
        Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        Suscripcion.billetera_id.isnot(None),
        Suscripcion.proximo_cobro <= hoy  # Usamos <= por seguridad si el job no corrió un día
    ).all()

    for suscripcion in suscripciones:
        # ── Idempotencia ──────────────────────────────────
        # Evitamos duplicar si el job corre dos veces el mismo día
        ya_existe = db.query(Transaccion).filter(
            Transaccion.usuario_id == suscripcion.usuario_id,
            Transaccion.fecha == hoy,
            Transaccion.estado_verificacion == 'pendiente',
            Transaccion.descripcion == suscripcion.nombre,
            Transaccion.billetera_id == suscripcion.billetera_id
        ).first()

        if ya_existe:
            continue

        # ── Obtener precio vigente ─────────────────────────
        precio = obtener_precio_vigente(db, suscripcion.id, hoy)

        # Si no hay precio configurado, no podemos procesar el cobro ni avanzar
        if not precio or precio.monto <= 0:
            continue

        # ── Crear transacción pendiente ───────────────────
        tx = Transaccion(
            usuario_id=suscripcion.usuario_id,
            tipo='egreso',
            monto=precio.monto,
            moneda=precio.moneda,
            fecha=hoy,
            descripcion=suscripcion.nombre,
            categoria_id=suscripcion.categoria_id,
            billetera_id=suscripcion.billetera_id,
            metodo_pago='debito',
            origen='recurrente',
            estado_verificacion='pendiente',
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False
        )
        db.add(tx)

        # ── Avanzar proximo_cobro ─────────────────────────
        # Avanzamos la fecha basándonos en la frecuencia definida
        suscripcion.proximo_cobro = calcular_siguiente_cobro(
            suscripcion.proximo_cobro, suscripcion.frecuencia.value
        )

    db.commit()
