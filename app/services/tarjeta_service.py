from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID
from calendar import monthrange
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

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
    Si la compra es antes o el mismo día del cierre, vence el mes siguiente.
    Si es después del cierre, vence a los dos meses.
    Si proximo_resumen es True, se le suma un mes adicional.
    """
    if fecha_compra.day <= dia_cierre:
        base = fecha_compra + relativedelta(months=1)
    else:
        base = fecha_compra + relativedelta(months=2)

    if proximo_resumen:
        base = base + relativedelta(months=1)

    ultimo_dia = monthrange(base.year, base.month)[1]
    dia_real = min(dia_vencimiento, ultimo_dia)

    return base.replace(day=dia_real)


def calcular_fecha_vencimiento_proximo(tarjeta: TarjetaCredito, hoy: date | None = None) -> date:
    """Devuelve la fecha del próximo vencimiento de la tarjeta a partir de hoy."""
    if hoy is None:
        hoy = date.today()
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
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
    update_data = data.model_dump(exclude_unset=True)
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
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
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
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
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
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    
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


def calcular_resumen_actual(db: Session, tarjeta: TarjetaCredito, cuotas_preloaded: list[Cuota] = None) -> ResumenTarjeta:
    hoy = date.today()

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

    # ── Obtener datos de la transacción vinculada ────────
    def get_info_transaccion(cuota: Cuota):
        # Usar la relación cargada en lugar de query manual
        tx = cuota.transaccion
        if not tx:
            return "Sin descripción", None
            
        # Intentar obtener el nombre de la subcategoría desde la relación
        sub_nombre = tx.subcategoria.nombre if tx.subcategoria else None
        
        # Limpiar la descripción: quitar el "(Cuota X/Y)" si existe
        # ya que lo mostraremos en el subtítulo
        desc_limpia = tx.descripcion
        import re
        desc_limpia = re.sub(r'\s*\(Cuota\s*\d+/\d+\)\s*$', '', desc_limpia).strip()
        
        # Si la descripción quedó vacía o es muy genérica, usar la subcategoría
        final_desc = desc_limpia or sub_nombre or "Transacción"
        
        return final_desc, sub_nombre

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
        
        cuota_data = CuotaResumen(
            id=cuota.transaccion_id,
            descripcion=desc_final,
            subcategoria_nombre=sub_nombre,
            numero_cuota=cuota.numero_cuota,
            total_cuotas=total_cuotas,
            monto=cuota.monto_real if cuota.monto_real is not None else cuota.monto_proyectado,
            moneda=tarjeta.moneda.value,
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

    return ResumenTarjeta(
        fecha_cierre_proximo=fecha_cierre_proximo,
        fecha_vencimiento_proximo=fecha_vencimiento_proximo,
        total_comprometido_resumen_actual=sum(c.monto for c in cuotas_actual),
        total_comprometido_resumen_siguiente=sum(c.monto for c in cuotas_siguiente),
        cuotas_resumen_actual=cuotas_actual,
        cuotas_resumen_siguiente=cuotas_siguiente,
        resumenes_futuros=resumenes_futuros,
        resumenes_anteriores=resumenes_anteriores
    )


def pagar_resumen_tarjeta(
    db: Session,
    usuario_id: UUID,
    tarjeta_id: UUID,
    fecha_pago: date | None = None
) -> Transaccion:
    # 1. Obtener la tarjeta
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    # 2. Calcular la fecha de vencimiento próximo para saber qué cuotas entran
    hoy = date.today()
    fecha_vencimiento_proximo = calcular_fecha_vencimiento_proximo(tarjeta, hoy)

    # 3. Obtener todas las cuotas de esta tarjeta que no estén pagadas y venzan en o antes de la fecha de vencimiento próximo
    cuotas_a_pagar = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(
            GrupoCuotas.tarjeta_id == tarjeta.id,
            Cuota.pagada == False,
            Cuota.fecha_vencimiento <= fecha_vencimiento_proximo
        )
        .all()
    )

    if not cuotas_a_pagar:
        raise HTTPException(status_code=400, detail="No hay saldo o cuotas pendientes de pago en el resumen actual.")

    # 4. Calcular el monto total a pagar
    monto_total = sum(
        (c.monto_real if c.monto_real is not None else c.monto_proyectado)
        for c in cuotas_a_pagar
    )

    if monto_total <= 0:
        raise HTTPException(status_code=400, detail="El monto del resumen a pagar debe ser mayor a cero.")

    # 5. Buscar la categoría "Banco" y subcategoría "Tarjeta de crédito"
    from app.models.categoria import Categoria
    from app.models.subcategoria import Subcategoria

    categoria = db.query(Categoria).filter(
        Categoria.nombre.ilike("Banco"),
        (Categoria.creador_id == usuario_id) | (Categoria.es_global == True)
    ).first()

    subcategoria = None
    if categoria:
        subcategoria = db.query(Subcategoria).filter(
            Subcategoria.categoria_id == categoria.id,
            Subcategoria.nombre.ilike("Tarjeta%de%crédito") | Subcategoria.nombre.ilike("Tarjetas%de%crédito"),
            (Subcategoria.creador_id == usuario_id) | (Subcategoria.es_global == True)
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

    # 7. Registrar la transacción
    tx = transaccion_service.crear_transaccion(db, usuario_id, tx_data)

    # 8. Marcar las cuotas como pagadas
    for cuota in cuotas_a_pagar:
        cuota.pagada = True

    db.commit()
    db.refresh(tx)
    return tx

