from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session, joinedload
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    OrigenTransaccion,
    EstadoVerificacionTransaccion
)
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.services.tarjeta_service import calcular_resumen_actual, calcular_fecha_vencimiento_proximo
from app.utils.fecha import hoy_argentina

def procesar_vencimientos_tarjetas(db: Session) -> None:
    hoy = hoy_argentina()

    # Buscar tarjetas activas cuyo vencimiento (ajustado a día hábil posterior) cae HOY
    tarjetas_activas = db.query(TarjetaCredito).filter(
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA
    ).all()
    tarjetas = [t for t in tarjetas_activas if calcular_fecha_vencimiento_proximo(t, hoy) == hoy]

    if not tarjetas:
        return

    # Optimización N+1: Pre-cargar todas las cuotas del último año para cálculo exacto
    from dateutil.relativedelta import relativedelta
    one_year_ago = hoy - relativedelta(years=1)
    all_cuotas = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .options(
            joinedload(Cuota.transaccion).joinedload(Transaccion.subcategoria),
            joinedload(Cuota.grupo)
        )
        .filter(
            GrupoCuotas.tarjeta_id.in_([t.id for t in tarjetas]) if tarjetas else False,
            Cuota.fecha_vencimiento >= one_year_ago
        )
        .all()
    )

    cuotas_por_tarjeta = {}
    for c in all_cuotas:
        tid = c.grupo.tarjeta_id
        if tid not in cuotas_por_tarjeta:
            cuotas_por_tarjeta[tid] = []
        cuotas_por_tarjeta[tid].append(c)

    for tarjeta in tarjetas:

        # ── Idempotencia estricta: verificar que no existe ya una transacción de pago ──────
        # Detecta cualquier transacción de pago vinculada a este vencimiento sin importar su estado
        ya_existe = db.query(Transaccion).filter(
            Transaccion.tarjeta_id == tarjeta.id,
            Transaccion.pago_resumen_vencimiento == hoy,
            Transaccion.tipo == TipoTransaccion.EGRESO
        ).first()

        if ya_existe:
            continue

        # ── Calcular total a pagar del resumen actual (incluye atrasadas si las hubiera) ───
        resumen = calcular_resumen_actual(db, tarjeta, cuotas_preloaded=cuotas_por_tarjeta.get(tarjeta.id, []))
        total = resumen.total_a_pagar_resumen_actual

        if total <= 0:
            continue

        # ── Mes en español para la descripción ───────────
        MESES = {
            1:'Enero', 2:'Febrero', 3:'Marzo', 4:'Abril',
            5:'Mayo', 6:'Junio', 7:'Julio', 8:'Agosto',
            9:'Septiembre', 10:'Octubre', 11:'Noviembre', 12:'Diciembre'
        }
        mes_label = MESES[hoy.month]

        # ── Crear transacción pendiente vinculada al vencimiento del resumen ───
        tx = Transaccion(
            usuario_id=tarjeta.usuario_id,
            tipo=TipoTransaccion.EGRESO,
            monto=total,
            moneda=tarjeta.moneda,
            fecha=hoy,
            descripcion=f'Resumen {tarjeta.nombre} — {mes_label} {hoy.year}',
            billetera_id=tarjeta.billetera_id,
            tarjeta_id=tarjeta.id,
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.RECURRENTE,
            estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False,
            pago_resumen_vencimiento=hoy
        )
        db.add(tx)

    db.commit()
