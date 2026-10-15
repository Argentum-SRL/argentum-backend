from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    EstadoVerificacionTransaccion,
)
from app.models.notificacion import (
    TipoNotificacion,
    NivelNotificacion,
)
from app.services import calendario_tarjeta_service, tarjeta_service
from app.services.notificacion_service import crear_notificacion
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto


def procesar_vencimientos_tarjetas(db: Session, hoy: date | None = None) -> None:
    """
    Job de vencimientos de resúmenes de tarjetas de crédito.
    Deja de crear transacciones automáticas.
    Para cada tarjeta activa y cada d en (0, 3, 7, 14, 21), toma el resumen cuyo
    vencimiento es hoy - d. Si ese resumen no tiene ningún pago CONFIRMADO (egreso con
    tarjeta_id y pago_resumen_vencimiento = ese vencimiento, estado confirmada o nulo)
    y lo que queda sin pagar es > 0, crea una notificación RESUMEN_IMPAGO (solo canal web).
    """
    if hoy is None:
        hoy = hoy_argentina()

    tarjetas_activas = db.query(TarjetaCredito).filter(
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA
    ).all()

    if not tarjetas_activas:
        return

    intervalos_dias = (0, 3, 7, 14, 21)

    for tarjeta in tarjetas_activas:
        # Obtener los resúmenes de la tarjeta en una ventana de fechas alrededor de hoy
        resumenes = calendario_tarjeta_service.resumenes(
            tarjeta,
            hoy - timedelta(days=90),
            hoy + timedelta(days=30),
        )

        for d in intervalos_dias:
            vencimiento_objetivo = hoy - timedelta(days=d)

            # Verificar si existe un resumen de esta tarjeta con ese vencimiento exacto
            tiene_resumen = any(venc == vencimiento_objetivo for _, venc in resumenes)
            if not tiene_resumen:
                continue

            # Verificar si existe un pago confirmado (o con estado nulo) para este vencimiento
            pago_confirmado = db.query(Transaccion).filter(
                Transaccion.tarjeta_id == tarjeta.id,
                Transaccion.pago_resumen_vencimiento == vencimiento_objetivo,
                Transaccion.tipo == TipoTransaccion.EGRESO,
                or_(
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                    Transaccion.estado_verificacion.is_(None),
                ),
            ).first()

            # Si hay un pago confirmado, aunque sea parcial, no se manda nada más
            if pago_confirmado:
                continue

            # Calcular el monto que queda sin pagar usando el detalle de resumen de la tarjeta
            detalle = tarjeta_service.obtener_detalle_resumen_vencimiento(db, tarjeta.id, vencimiento_objetivo)
            monto_sin_pagar = Decimal(str(detalle.get("monto_deuda_restante", Decimal("0"))))

            if monto_sin_pagar <= Decimal("0"):
                continue

            moneda_val = tarjeta.moneda.value if hasattr(tarjeta.moneda, "value") else str(tarjeta.moneda)
            monto_fmt = formatear_monto(monto_sin_pagar, moneda_val)
            venc_fmt = vencimiento_objetivo.strftime("%d/%m")
            mensaje = f"¿Pagaste el resumen de {tarjeta.nombre} de {monto_fmt} que venció el {venc_fmt}?"
            grupo = f"RESUMEN_IMPAGO_{tarjeta.id}_{vencimiento_objetivo:%Y%m%d}_{d}"
            deep_link = f"/app/billeteras/{tarjeta.billetera_id}?tab=credito&tarjeta_id={tarjeta.id}"

            crear_notificacion(
                db=db,
                usuario_id=tarjeta.usuario_id,
                tipo=TipoNotificacion.RESUMEN_IMPAGO,
                nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                mensaje=mensaje,
                entidad_tipo="tarjeta",
                entidad_id=tarjeta.id,
                deep_link=deep_link,
                canal_web=True,
                canal_whatsapp=False,
                canal_email=False,
                grupo_agrupacion_override=grupo,
                commit=False,
                datos_template={
                    "vencimiento": vencimiento_objetivo.isoformat(),
                    "monto": str(monto_sin_pagar),
                    "d": d,
                },
            )

    db.commit()
