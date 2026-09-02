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
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.billetera import Billetera
from app.models.transaccion import Transaccion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.schemas.tarjeta_credito import (
    TarjetaCreditoCreate, 
    TarjetaCreditoUpdate,
    ResumenTarjeta,
    CuotaResumen,
    ResumenFuturo,
    ResumenAnterior
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

    return mes_vencimiento.replace(day=dia_real)


def calcular_fecha_vencimiento_proximo(tarjeta: TarjetaCredito, hoy: date | None = None) -> date:
    """Devuelve la fecha del próximo vencimiento de la tarjeta a partir de hoy."""
    if hoy is None:
        hoy = hoy_argentina()
    ultimo_dia_mes = monthrange(hoy.year, hoy.month)[1]
    dia_venc = min(tarjeta.dia_vencimiento, ultimo_dia_mes)
    venc = date(hoy.year, hoy.month, dia_venc)
    if hoy > venc:
        proximo_mes = hoy + relativedelta(months=1)
        ultimo_dia_proximo = monthrange(proximo_mes.year, proximo_mes.month)[1]
        dia_venc_proximo = min(tarjeta.dia_vencimiento, ultimo_dia_proximo)
        venc = date(proximo_mes.year, proximo_mes.month, dia_venc_proximo)
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
        red=data.red,
        dia_cierre=data.dia_cierre,
        dia_vencimiento=data.dia_vencimiento,
        limite_credito=data.limite_credito,
        moneda=data.moneda,
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

    for key, value in update_data.items():
        setattr(tarjeta, key, value)
    
    db.commit()
    db.refresh(tarjeta)
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


def calcular_resumen_actual(db: Session, tarjeta: TarjetaCredito, cuotas_preloaded: list[Cuota] = None) -> ResumenTarjeta:
    hoy = hoy_argentina()

    # ── Calcular fecha de vencimiento próximo ─────────────
    # Usar el último día del mes si dia_vencimiento es mayor
    ultimo_dia_mes = monthrange(hoy.year, hoy.month)[1]
    dia_venc = min(tarjeta.dia_vencimiento, ultimo_dia_mes)
    
    venc = date(hoy.year, hoy.month, dia_venc)
    if hoy > venc:
        # Si ya pasó el vencimiento de este mes, ir al siguiente
        proximo_mes = hoy + relativedelta(months=1)
        ultimo_dia_proximo = monthrange(proximo_mes.year, proximo_mes.month)[1]
        dia_venc_proximo = min(tarjeta.dia_vencimiento, ultimo_dia_proximo)
        venc = date(proximo_mes.year, proximo_mes.month, dia_venc_proximo)
    
    fecha_vencimiento_proximo = venc

    # ── Calcular fecha de cierre próximo ──────────────────
    # El cierre debe corresponder al período de vencimiento próximo
    fecha_cierre_proximo = calcular_fecha_cierre_de_vencimiento(
        fecha_vencimiento_proximo, tarjeta.dia_cierre, tarjeta.dia_vencimiento
    )

    # ── Obtener todas las cuotas de esta tarjeta (incluidas las del último año) ──
    one_year_ago = hoy - relativedelta(years=1)
    if cuotas_preloaded is not None:
        cuotas = cuotas_preloaded
    else:
        # Cuota -> GrupoCuotas (tarjeta_id) -> filtrar por tarjeta
        cuotas = (
            db.query(Cuota)
            .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
            .options(
                joinedload(Cuota.transaccion).joinedload(Transaccion.subcategoria),
                joinedload(Cuota.grupo)
            )
            .filter(
                GrupoCuotas.tarjeta_id == tarjeta.id,
                Cuota.fecha_vencimiento >= one_year_ago
            )
            .order_by(Cuota.fecha_vencimiento)
            .all()
        )

    # ── Agrupar cuotas por resumen ─────────────────────────
    venc_siguiente = fecha_vencimiento_proximo + relativedelta(months=1)
    # Ajustar dia de vencimiento del mes siguiente
    ultimo_dia_siguiente = monthrange(venc_siguiente.year, venc_siguiente.month)[1]
    venc_siguiente = venc_siguiente.replace(day=min(tarjeta.dia_vencimiento, ultimo_dia_siguiente))

    cuotas_actual = []
    cuotas_siguiente = []
    anteriores_dict: dict[str, dict] = {}
    futuros_dict: dict[str, dict] = {}

    for cuota in cuotas:
        grupo = cuota.grupo
        total_cuotas = grupo.cantidad_cuotas if grupo else 1

        desc_final, sub_nombre = get_info_transaccion(cuota)
        
        cuota_moneda = (
            grupo.moneda.value if hasattr(grupo.moneda, "value") else str(grupo.moneda)
        ) if (grupo and grupo.moneda) else (
            tarjeta.moneda.value if hasattr(tarjeta.moneda, "value") else str(tarjeta.moneda)
        )

        cuota_data = CuotaResumen(
            id=cuota.transaccion_id,
            descripcion=desc_final,
            subcategoria_nombre=sub_nombre,
            numero_cuota=cuota.numero_cuota,
            total_cuotas=total_cuotas,
            monto=cuota.monto_real if cuota.monto_real is not None else cuota.monto_proyectado,
            moneda=cuota_moneda,
            fecha_vencimiento=cuota.fecha_vencimiento,
            pagada=cuota.pagada
        )

        if cuota.fecha_vencimiento < fecha_vencimiento_proximo:
            # Resumen anterior
            venc_key = cuota.fecha_vencimiento.strftime("%Y-%m")
            nombre_mes_en = cuota.fecha_vencimiento.strftime("%B")
            nombre_mes_es = MESES_ES.get(nombre_mes_en, nombre_mes_en)
            mes_label = f"{nombre_mes_es} {cuota.fecha_vencimiento.year}"
            
            if venc_key not in anteriores_dict:
                cierre_date = calcular_fecha_cierre_de_vencimiento(
                    cuota.fecha_vencimiento, tarjeta.dia_cierre, tarjeta.dia_vencimiento
                )
                anteriores_dict[venc_key] = {
                    "mes": mes_label,
                    "fecha_vencimiento": cuota.fecha_vencimiento,
                    "fecha_cierre": cierre_date,
                    "total": Decimal(0),
                    "moneda": tarjeta.moneda.value,
                    "pagado": True,
                    "cuotas": []
                }
            
            anteriores_dict[venc_key]["total"] += cuota_data.monto
            if not cuota.pagada:
                anteriores_dict[venc_key]["pagado"] = False
            anteriores_dict[venc_key]["cuotas"].append(cuota_data)

        elif cuota.fecha_vencimiento == fecha_vencimiento_proximo:
            cuotas_actual.append(cuota_data)
        elif fecha_vencimiento_proximo < cuota.fecha_vencimiento <= venc_siguiente:
            cuotas_siguiente.append(cuota_data)
        else:
            # Agrupar por mes futuro
            mes_key = cuota.fecha_vencimiento.strftime("%Y-%m")
            # Traducir mes a español
            nombre_mes_en = cuota.fecha_vencimiento.strftime("%B")
            nombre_mes_es = MESES_ES.get(nombre_mes_en, nombre_mes_en)
            mes_label = f"{nombre_mes_es} {cuota.fecha_vencimiento.year}"
            
            if mes_key not in futuros_dict:
                futuros_dict[mes_key] = {
                    "mes": mes_label,
                    "mes_fecha": date(cuota.fecha_vencimiento.year,
                                      cuota.fecha_vencimiento.month, 1),
                    "total": Decimal(0),
                    "moneda": tarjeta.moneda.value,
                    "cantidad_cuotas": 0,
                    "cuotas": []
                }
            futuros_dict[mes_key]["total"] += cuota_data.monto
            futuros_dict[mes_key]["cantidad_cuotas"] += 1
            futuros_dict[mes_key]["cuotas"].append(cuota_data)

    resumenes_anteriores = [
        ResumenAnterior(**v)
        for v in sorted(anteriores_dict.values(), key=lambda x: x["fecha_vencimiento"])
    ]

    resumenes_futuros = [
        ResumenFuturo(**v)
        for v in sorted(futuros_dict.values(), key=lambda x: x["mes_fecha"])
    ]

    tarjeta_moneda_str = tarjeta.moneda.value if hasattr(tarjeta.moneda, "value") else str(tarjeta.moneda)
    total_actual_tarjeta = sum(c.monto for c in cuotas_actual if c.moneda == tarjeta_moneda_str)
    total_sig_tarjeta = sum(c.monto for c in cuotas_siguiente if c.moneda == tarjeta_moneda_str)
    total_actual_ars = sum(c.monto for c in cuotas_actual if c.moneda == "ARS")
    total_actual_usd = sum(c.monto for c in cuotas_actual if c.moneda == "USD")
    total_sig_ars = sum(c.monto for c in cuotas_siguiente if c.moneda == "ARS")
    total_sig_usd = sum(c.monto for c in cuotas_siguiente if c.moneda == "USD")

    return ResumenTarjeta(
        fecha_cierre_proximo=fecha_cierre_proximo,
        fecha_vencimiento_proximo=fecha_vencimiento_proximo,
        total_comprometido_resumen_actual=total_actual_tarjeta,
        total_comprometido_resumen_siguiente=total_sig_tarjeta,
        total_actual_ars=total_actual_ars,
        total_actual_usd=total_actual_usd,
        total_siguiente_ars=total_sig_ars,
        total_siguiente_usd=total_sig_usd,
        totales_moneda_actual={"ARS": total_actual_ars, "USD": total_actual_usd},
        totales_moneda_siguiente={"ARS": total_sig_ars, "USD": total_sig_usd},
        cuotas_resumen_actual=cuotas_actual,
        cuotas_resumen_siguiente=cuotas_siguiente,
        resumenes_futuros=resumenes_futuros,
        resumenes_anteriores=resumenes_anteriores
    )


def pagar_resumen_tarjeta(
    db: Session,
    usuario_id: UUID,
    tarjeta_id: UUID,
    fecha_pago: date | None = None,
    fecha_resumen: date | None = None
) -> Transaccion:
    # 1. Obtener la tarjeta
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    if not tarjeta:
        raise HTTPException(status_code=404, detail="No encontramos esa tarjeta.")

    # 2. Calcular la fecha de vencimiento límite a pagar
    if fecha_resumen is not None:
        limite_vencimiento = fecha_resumen
    else:
        hoy = hoy_argentina()
        limite_vencimiento = calcular_fecha_vencimiento_proximo(tarjeta, hoy)

    # 3. Obtener todas las cuotas de esta tarjeta que no estén pagadas y venzan en o antes del límite
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

    if not cuotas_a_pagar:
        raise HTTPException(status_code=400, detail="No hay saldo o cuotas pendientes de pago en el resumen actual.")

    # 3.1 Agrupar por moneda: prohibido sumar monedas distintas
    tarjeta_moneda_str = tarjeta.moneda.value if hasattr(tarjeta.moneda, "value") else str(tarjeta.moneda)
    cuotas_coincidentes = []
    cuotas_otra_moneda = []

    for c in cuotas_a_pagar:
        c_moneda = (
            c.grupo.moneda.value if hasattr(c.grupo.moneda, "value") else str(c.grupo.moneda)
        ) if (c.grupo and c.grupo.moneda) else tarjeta_moneda_str
        if c_moneda == tarjeta_moneda_str:
            cuotas_coincidentes.append(c)
        else:
            cuotas_otra_moneda.append(c)

    if not cuotas_coincidentes:
        if cuotas_otra_moneda:
            raise HTTPException(
                status_code=400,
                detail=f"No hay cuotas pendientes en la moneda de la tarjeta ({tarjeta_moneda_str}). Quedan {len(cuotas_otra_moneda)} cuota(s) en otra moneda que no pueden pagarse con esta tarjeta/billetera."
            )
        raise HTTPException(status_code=400, detail="No hay saldo o cuotas pendientes de pago en el resumen actual.")

    # 4. Calcular el monto total a pagar ÚNICAMENTE de las cuotas en la moneda de la tarjeta
    monto_total = sum(
        (c.monto_real if c.monto_real is not None else c.monto_proyectado)
        for c in cuotas_coincidentes
    )

    if monto_total <= 0:
        raise HTTPException(status_code=400, detail="El monto del resumen a pagar debe ser mayor a cero.")

    # 5. Buscar la categoría "Banco" y subcategoría "Tarjeta de crédito"
    from app.models.categoria import Categoria
    from app.models.subcategoria import Subcategoria

    categoria = db.query(Categoria).filter(
        Categoria.nombre.ilike("Banco")
    ).first()

    subcategoria = None
    if categoria:
        subcategoria = db.query(Subcategoria).filter(
            Subcategoria.categoria_id == categoria.id,
            Subcategoria.nombre.ilike("Tarjeta%de%crédito") | Subcategoria.nombre.ilike("Tarjetas%de%crédito")
        ).first()

    # 6. Crear la transacción de egreso
    from app.schemas.transaccion import TransaccionCreate
    from app.services import transaccion_service
    from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion, EstadoVerificacionTransaccion

    ultimos_4 = tarjeta.nombre[-4:] if len(tarjeta.nombre) >= 4 else tarjeta.nombre
    descripcion_pago = f"Pago resumen {ultimos_4}"

    fecha_transaccion = fecha_pago or transaccion_service._hoy_argentina()

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=monto_total,
        moneda=tarjeta.moneda,
        fecha=fecha_transaccion,
        descripcion=descripcion_pago,
        categoria_id=categoria.id if categoria else None,
        subcategoria_id=subcategoria.id if subcategoria else None,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=tarjeta.billetera_id,
        tarjeta_id=tarjeta.id,
        es_recurrente=False,
        es_cuota_hija=False,
        es_padre_cuotas=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA
    )

    # 7. Registrar la transacción de forma atómica
    try:
        tx = transaccion_service.crear_transaccion(db, usuario_id, tx_data, commit=False)

        # 8. Marcar ÚNICAMENTE las cuotas coincidentes como pagadas
        for cuota in cuotas_coincidentes:
            cuota.pagada = True

        db.commit()
        db.refresh(tx)

        # 3.3 Construir información de cuotas en otra moneda no pagadas
        from app.schemas.tarjeta_credito import CuotaPendienteOtraMoneda
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
            mensaje_adv = f"Se pagaron {len(cuotas_coincidentes)} cuota(s) en {tarjeta_moneda_str}. Quedaron {len(cuotas_otra_moneda)} cuota(s) en otra moneda pendientes de pago porque la tarjeta es en {tarjeta_moneda_str}."

        setattr(tx, "cuotas_pagadas_count", len(cuotas_coincidentes))
        setattr(tx, "moneda_pagada", tarjeta_moneda_str)
        setattr(tx, "monto_pagado", monto_total)
        setattr(tx, "cuotas_pendientes_otra_moneda", pendientes)
        setattr(tx, "mensaje_advertencia", mensaje_adv)
        return tx
    except Exception:
        db.rollback()
        logger.exception("Error al pagar resumen de tarjeta %s", tarjeta_id)
        raise


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

