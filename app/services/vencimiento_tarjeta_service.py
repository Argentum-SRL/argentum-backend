from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.transaccion import Transaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.services.tarjeta_service import calcular_resumen_actual

def procesar_vencimientos_tarjetas(db: Session) -> None:
    hoy = date.today()

    # Buscar tarjetas activas cuyo día de vencimiento es hoy
    tarjetas = db.query(TarjetaCredito).filter(
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA,
        TarjetaCredito.dia_vencimiento == hoy.day
    ).all()

    for tarjeta in tarjetas:

        # ── Idempotencia: verificar que no existe ya ──────
        # Buscamos una transacción pendiente de este mismo día para esta tarjeta
        ya_existe = db.query(Transaccion).filter(
            Transaccion.tarjeta_id == tarjeta.id,
            Transaccion.fecha == hoy,
            Transaccion.estado_verificacion == 'pendiente',
            Transaccion.tipo == 'egreso'
        ).first()

        if ya_existe:
            continue

        # ── Calcular total del resumen actual ─────────────
        resumen = calcular_resumen_actual(db, tarjeta)
        total = resumen.total_comprometido_resumen_actual

        if total <= 0:
            continue

        # ── Mes en español para la descripción ───────────
        MESES = {
            1:'Enero', 2:'Febrero', 3:'Marzo', 4:'Abril',
            5:'Mayo', 6:'Junio', 7:'Julio', 8:'Agosto',
            9:'Septiembre', 10:'Octubre', 11:'Noviembre', 12:'Diciembre'
        }
        mes_label = MESES[hoy.month]

        # ── Crear transacción pendiente ───────────────────
        # La transacción se crea asociada a la billetera de la tarjeta
        # El origen 'recurrente' ayuda a identificarla como automática
        tx = Transaccion(
            usuario_id=tarjeta.usuario_id,
            tipo='egreso',
            monto=total,
            moneda=tarjeta.moneda,
            fecha=hoy,
            descripcion=f'Resumen {tarjeta.nombre} — {mes_label} {hoy.year}',
            billetera_id=tarjeta.billetera_id,
            tarjeta_id=tarjeta.id,
            metodo_pago='credito', # Es el pago de la tarjeta
            origen='recurrente',
            estado_verificacion='pendiente',
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False
        )
        db.add(tx)

    db.commit()
