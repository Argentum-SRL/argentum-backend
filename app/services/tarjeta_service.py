import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID
from calendar import monthrange
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

logger = logging.getLogger(__name__)

from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.billetera import Billetera
from app.models.transaccion import Transaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.saldo_arrastrado import SaldoArrastradoTarjeta, PagoSaldoArrastrado, EstadoSaldoArrastrado
from app.models.usuario import Moneda
from app.schemas.tarjeta_credito import (
    TarjetaCreditoCreate, 
    TarjetaCreditoUpdate,
    ResumenTarjeta,
    CuotaResumen,
    ResumenFuturo,
    ResumenAnterior,
    ItemSaldoArrastrado,
    BloqueResumenMoneda,
    SimularPesificacionResponse,
    CuotaPendienteOtraMoneda,
    ResultadoPagoTarjeta
)

MESES_ES = {
    "January": "Enero", "February": "Febrero", "March": "Marzo",
    "April": "Abril", "May": "Mayo", "June": "Junio",
    "July": "Julio", "August": "Agosto", "September": "Septiembre",
    "October": "Octubre", "November": "Noviembre", "December": "Diciembre"
}


def calcular_primer_vencimiento(
    fecha_compra: date,
    dia_cierre: int,
    dia_vencimiento: int,
    proximo_resumen: bool = False
) -> date:
    """
    Calcula la fecha del primer vencimiento de una compra con tarjeta.
    Si la compra es antes o el mismo día del cierre, entra en el cierre de ese mes.
    Si es después del cierre, entra en el cierre del mes siguiente.
    La fecha de vencimiento dependerá de si el vencimiento es en el mismo mes del cierre o al siguiente.

    DECISIÓN DE PRODUCTO:
    - El vencimiento de una tarjeta se ajusta al primer día hábil SIGUIENTE (posterior) cuando
      cae sábado, domingo o feriado bancario en Argentina.
    - El cierre NO se ajusta por día hábil: los bancos comerciales cierran sus ciclos en fecha fija.
    """
    # 1. Determinar el mes de cierre correspondiente a la compra
    if fecha_compra.day <= dia_cierre:
        mes_cierre = fecha_compra
    else:
        mes_cierre = fecha_compra + relativedelta(months=1)

    # 2. Determinar el mes de vencimiento a partir del mes de cierre
    if dia_vencimiento <= dia_cierre:
        mes_vencimiento = mes_cierre + relativedelta(months=1)
    else:
        mes_vencimiento = mes_cierre

    # 3. Sumar mes adicional si se solicita diferir al próximo resumen
    if proximo_resumen:
        mes_vencimiento = mes_vencimiento + relativedelta(months=1)

    ultimo_dia = monthrange(mes_vencimiento.year, mes_vencimiento.month)[1]
    dia_real = min(dia_vencimiento, ultimo_dia)
    fecha_nominal = mes_vencimiento.replace(day=dia_real)

    from app.services.dias_habiles_service import ajustar_fecha_habil_sync
    return ajustar_fecha_habil_sync(fecha_nominal, direccion="posterior")


def calcular_fecha_vencimiento_proximo(tarjeta: TarjetaCredito, hoy: date | None = None) -> date:
    """Devuelve la fecha del próximo vencimiento de la tarjeta a partir de hoy (ajustada a día hábil posterior)."""
    if hoy is None:
        hoy = hoy_argentina()
    from app.services.dias_habiles_service import ajustar_fecha_habil_sync

    ultimo_dia_mes = monthrange(hoy.year, hoy.month)[1]
    dia_venc = min(tarjeta.dia_vencimiento, ultimo_dia_mes)
    venc_nominal = date(hoy.year, hoy.month, dia_venc)
    venc = ajustar_fecha_habil_sync(venc_nominal, direccion="posterior")

    if hoy > venc:
        proximo_mes = hoy + relativedelta(months=1)
        ultimo_dia_proximo = monthrange(proximo_mes.year, proximo_mes.month)[1]
        dia_venc_proximo = min(tarjeta.dia_vencimiento, ultimo_dia_proximo)
        venc_nominal_prox = date(proximo_mes.year, proximo_mes.month, dia_venc_proximo)
        venc = ajustar_fecha_habil_sync(venc_nominal_prox, direccion="posterior")
    return venc


def obtener_tarjetas(db: Session, usuario_id: UUID) -> list[TarjetaCredito]:
    return db.query(TarjetaCredito).filter(
        TarjetaCredito.usuario_id == usuario_id,
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA
    ).all()


def obtener_tarjetas_por_billetera(db: Session, usuario_id: UUID, billetera_id: UUID) -> list[TarjetaCredito]:
    return db.query(TarjetaCredito).filter(
        TarjetaCredito.usuario_id == usuario_id,
        TarjetaCredito.billetera_id == billetera_id,
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA
    ).all()


def crear_tarjeta(db: Session, usuario_id: UUID, data: TarjetaCreditoCreate) -> TarjetaCredito:
    # Validar que la billetera pertenece al usuario
    billetera = db.query(Billetera).filter(
        Billetera.id == data.billetera_id,
        Billetera.usuario_id == usuario_id
    ).first()
    
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")
    
    # Validar que la billetera no sea de efectivo
    if billetera.es_efectivo:
        raise HTTPException(
            status_code=400, 
            detail="Las billeteras de efectivo no pueden tener tarjetas."
        )

    # Validar que la moneda de la tarjeta coincida con la de la billetera
    if data.moneda != billetera.moneda:
        raise HTTPException(
            status_code=400,
            detail="La moneda de la tarjeta debe coincidir con la moneda de la billetera asociada."
        )

    nueva_tarjeta = TarjetaCredito(
        usuario_id=usuario_id,
        billetera_id=data.billetera_id,
        nombre=data.nombre,
        apodo=data.apodo,
        red=data.red,
        dia_cierre=data.dia_cierre,
        dia_vencimiento=data.dia_vencimiento,
        limite_credito=data.limite_credito,
        moneda=data.moneda,
        percepcion_moneda_extranjera=data.percepcion_moneda_extranjera,
        color=data.color
    )
    
    db.add(nueva_tarjeta)
    db.commit()
    db.refresh(nueva_tarjeta)
    return nueva_tarjeta


def actualizar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID, data: TarjetaCreditoUpdate) -> TarjetaCredito:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="No encontramos esa tarjeta.")
    
    update_data = data.model_dump(exclude_unset=True)

    if "moneda" in update_data and update_data["moneda"] is not None and update_data["moneda"] != tarjeta.moneda:
        if tarjeta.billetera and update_data["moneda"] != tarjeta.billetera.moneda:
            raise HTTPException(
                status_code=400,
                detail="La moneda de la tarjeta debe coincidir con la moneda de la billetera asociada."
            )
        tiene_tx = db.query(Transaccion).filter(Transaccion.tarjeta_id == tarjeta.id).first()
        if tiene_tx:
            raise HTTPException(
                status_code=400,
                detail="No podés cambiar la moneda de una tarjeta que ya tiene transacciones registradas."
            )

    cierre_cambio = "dia_cierre" in update_data and update_data["dia_cierre"] != tarjeta.dia_cierre
    venc_cambio = "dia_vencimiento" in update_data and update_data["dia_vencimiento"] != tarjeta.dia_vencimiento

    nuevo_dia_vencimiento = update_data.get("dia_vencimiento", tarjeta.dia_vencimiento)

    for key, value in update_data.items():
        setattr(tarjeta, key, value)
    
    cuotas_recalculadas = 0
    if cierre_cambio or venc_cambio:
        hoy = hoy_argentina()
        # Recalcular cuotas NO pagadas cuyo vencimiento sea posterior a hoy
        cuotas_futuras = (
            db.query(Cuota)
            .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
            .options(joinedload(Cuota.transaccion))
            .filter(
                GrupoCuotas.tarjeta_id == tarjeta.id,
                Cuota.pagada == False,
                Cuota.fecha_vencimiento > hoy
            )
            .all()
        )
        from app.services.dias_habiles_service import ajustar_fecha_habil_sync
        for c in cuotas_futuras:
            anio_c = c.fecha_vencimiento.year
            mes_c = c.fecha_vencimiento.month
            ultimo_dia_mes = monthrange(anio_c, mes_c)[1]
            dia_real = min(nuevo_dia_vencimiento, ultimo_dia_mes)
            f_nom = date(anio_c, mes_c, dia_real)
            nueva_fecha = ajustar_fecha_habil_sync(f_nom, direccion="posterior")

            if c.fecha_vencimiento != nueva_fecha:
                c.fecha_vencimiento = nueva_fecha
                if c.transaccion:
                    c.transaccion.fecha = nueva_fecha
                cuotas_recalculadas += 1

    db.commit()
    db.refresh(tarjeta)
    setattr(tarjeta, "cuotas_recalculadas", cuotas_recalculadas)
    return tarjeta


def archivar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID) -> TarjetaCredito:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="No encontramos esa tarjeta.")
    
    tarjeta.estado = EstadoTarjeta.ARCHIVADA
    db.commit()
    db.refresh(tarjeta)
    return tarjeta


def desarchivar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID) -> TarjetaCredito:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="No encontramos esa tarjeta.")
    
    tarjeta.estado = EstadoTarjeta.ACTIVA
    db.commit()
    db.refresh(tarjeta)
    return tarjeta


def eliminar_tarjeta(db: Session, usuario_id: UUID, tarjeta_id: UUID) -> None:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    
    if not tarjeta:
        raise HTTPException(status_code=404, detail="No encontramos esa tarjeta.")
    
    # Verificar si tiene transacciones registradas
    tiene_transacciones = db.query(Transaccion).filter(Transaccion.tarjeta_id == tarjeta_id).first()
    if tiene_transacciones:
        raise HTTPException(
            status_code=400, 
            detail="Esta tarjeta tiene transacciones registradas. Podés archivarla pero no eliminarla."
        )
    
    db.delete(tarjeta)
    db.commit()


def calcular_fecha_cierre_de_vencimiento(vencimiento: date, dia_cierre: int, dia_vencimiento: int) -> date:
    """
    Calcula la fecha de cierre de tarjeta que corresponde a una fecha de vencimiento dada.
    Si el día de vencimiento es menor o igual al día de cierre, el cierre es el mes anterior.
    Si el día de vencimiento es mayor, el cierre es el mismo mes.
    """
    if dia_vencimiento <= dia_cierre:
        mes_cierre = vencimiento - relativedelta(months=1)
    else:
        mes_cierre = vencimiento

    ultimo_dia = monthrange(mes_cierre.year, mes_cierre.month)[1]
    dia_real = min(dia_cierre, ultimo_dia)
    return mes_cierre.replace(day=dia_real)


def get_info_transaccion(cuota: Cuota) -> tuple[str, str | None]:
    """Obtiene descripción limpia y nombre de subcategoría para una cuota."""
    tx = cuota.transaccion
    if not tx:
        return "Sin descripción", None

    sub_nombre = tx.subcategoria.nombre if tx.subcategoria else None

    desc_limpia = tx.descripcion or ""
    import re
    desc_limpia = re.sub(r'\s*\(Cuota\s*\d+/\d+\)\s*$', '', desc_limpia).strip()

    final_desc = desc_limpia or sub_nombre or "Transacción"

    return final_desc, sub_nombre










def obtener_detalle_resumen_vencimiento(
    db: Session,
    tarjeta_id: UUID,
    fecha_vencimiento: date
) -> dict:
    """
    Responde para cualquier resumen (Tarea 1.5):
    - cuánto se facturó
    - cuánto se pagó
    - cuánto quedó debiendo
    - qué transacciones lo pagaron
    """
    tarjeta = db.query(TarjetaCredito).filter(TarjetaCredito.id == tarjeta_id).first()
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    from app.models.transaccion import TipoTransaccion
    transacciones_pago = db.query(Transaccion).filter(
        Transaccion.tarjeta_id == tarjeta_id,
        Transaccion.pago_resumen_vencimiento == fecha_vencimiento,
        Transaccion.tipo == TipoTransaccion.EGRESO
    ).all()

    monto_pagado = sum(t.monto for t in transacciones_pago)

    saldo_arrastrado = None
    from app.services.resumen_tarjeta_service import _tabla_saldo_arrastrado_existe
    if _tabla_saldo_arrastrado_existe(db):
        saldo_arrastrado = db.query(SaldoArrastradoTarjeta).filter(
            SaldoArrastradoTarjeta.tarjeta_id == tarjeta_id,
            SaldoArrastradoTarjeta.fecha_vencimiento_resumen == fecha_vencimiento
        ).first()

    cuotas = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(
            GrupoCuotas.tarjeta_id == tarjeta_id,
            Cuota.fecha_vencimiento == fecha_vencimiento
        )
        .all()
    )
    monto_cuotas = sum(
        (c.monto_real if c.monto_real is not None else c.monto_proyectado)
        for c in cuotas
    )

    if saldo_arrastrado:
        monto_facturado = monto_pagado + saldo_arrastrado.monto_restante
        monto_deuda_restante = saldo_arrastrado.monto_restante if saldo_arrastrado.estado == EstadoSaldoArrastrado.ACTIVO else Decimal("0")
    else:
        monto_facturado = monto_cuotas
        cuotas_impagas = sum(
            (c.monto_real if c.monto_real is not None else c.monto_proyectado)
            for c in cuotas if not c.pagada
        )
        monto_deuda_restante = cuotas_impagas

    return {
        "monto_facturado": monto_facturado,
        "monto_pagado": monto_pagado,
        "monto_deuda_restante": monto_deuda_restante,
        "transacciones_pago": transacciones_pago,
        "saldo_arrastrado": saldo_arrastrado
    }


def calcular_presion_futura(
    db: Session,
    usuario,
    meses: int = 6,
) -> dict:
    """
    Calcula la presión financiera futura: cuánto debe el usuario en cuotas
    de tarjeta por cada mes de vencimiento, para los próximos N meses.
    """
    from datetime import date
    from dateutil.relativedelta import relativedelta
    from decimal import Decimal
    from collections import defaultdict
    from sqlalchemy.orm import joinedload
    from app.models.usuario import Moneda

    hoy = hoy_argentina()
    fecha_limite = hoy + relativedelta(months=meses)

    # Obtener tarjetas activas del usuario
    tarjetas = db.query(TarjetaCredito).filter(
        TarjetaCredito.usuario_id == usuario.id,
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA,
    ).all()

    if not tarjetas:
        return {"meses": [], "total_comprometido": {"ars": 0.0, "usd": 0.0}}

    tarjeta_ids = [t.id for t in tarjetas]
    tarjeta_map = {t.id: t for t in tarjetas}

    # Obtener todas las cuotas no pagadas de las tarjetas del usuario
    # dentro del período de análisis, usando joinedload para evitar N+1
    cuotas = (
        db.query(Cuota)
        .options(joinedload(Cuota.grupo))
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(
            GrupoCuotas.tarjeta_id.in_(tarjeta_ids),
            Cuota.pagada == False,
            Cuota.fecha_vencimiento > hoy,
            Cuota.fecha_vencimiento <= fecha_limite,
        )
        .all()
    )

    # Agrupar por (año, mes) de vencimiento, luego por tarjeta
    por_mes = defaultdict(lambda: defaultdict(Decimal))

    for cuota in cuotas:
        grupo = cuota.grupo
        if not grupo or not grupo.tarjeta_id:
            continue

        mes_key = (cuota.fecha_vencimiento.year, cuota.fecha_vencimiento.month)
        monto = Decimal(str(cuota.monto_real if cuota.monto_real is not None else cuota.monto_proyectado or 0))
        por_mes[mes_key][grupo.tarjeta_id] += monto

    # Construir la respuesta ordenada
    resultado_meses = []
    total_comprometido_ars = Decimal("0")
    total_comprometido_usd = Decimal("0")

    for mes_key in sorted(por_mes.keys()):
        año, mes = mes_key
        detalle_tarjetas = []
        total_mes_ars = Decimal("0")
        total_mes_usd = Decimal("0")

        for tarjeta_id, monto in por_mes[mes_key].items():
            tarjeta = tarjeta_map.get(tarjeta_id)
            if not tarjeta:
                continue
            detalle_tarjetas.append({
                "tarjeta_id": str(tarjeta_id),
                "tarjeta_nombre": tarjeta.nombre,
                "total": float(monto),
                "moneda": tarjeta.moneda.value,
            })
            if tarjeta.moneda == Moneda.ARS:
                total_mes_ars += monto
            elif tarjeta.moneda == Moneda.USD:
                total_mes_usd += monto

        # Ordenar tarjetas por monto descendente
        detalle_tarjetas.sort(key=lambda x: x["total"], reverse=True)

        # Traducir mes a español y abreviar (e.g. Jun 2026)
        nombre_mes_en = date(año, mes, 1).strftime("%B")
        nombre_mes_es = MESES_ES.get(nombre_mes_en, nombre_mes_en)
        mes_abr = nombre_mes_es[:3].capitalize()

        resultado_meses.append({
            "anio": año,
            "mes": mes,
            "mes_label": f"{mes_abr} {año}",
            "total": {
                "ars": float(total_mes_ars),
                "usd": float(total_mes_usd),
            },
            "tarjetas": detalle_tarjetas,
        })
        total_comprometido_ars += total_mes_ars
        total_comprometido_usd += total_mes_usd

    return {
        "meses": resultado_meses,
        "total_comprometido": {
            "ars": float(total_comprometido_ars),
            "usd": float(total_comprometido_usd),
        },
    }

