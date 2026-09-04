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


def _tabla_saldo_arrastrado_existe(db: Session) -> bool:
    try:
        from sqlalchemy import inspect
        bind = db.get_bind()
        return inspect(bind).has_table("saldos_arrastrados_tarjeta")
    except Exception:
        return False


def calcular_resumen_actual(db: Session, tarjeta: TarjetaCredito, cuotas_preloaded: list[Cuota] = None) -> ResumenTarjeta:
    hoy = hoy_argentina()

    # ── Calcular fecha de vencimiento próximo (ajustada a día hábil) ─────────────
    fecha_vencimiento_proximo = calcular_fecha_vencimiento_proximo(tarjeta, hoy)

    # ── Calcular fecha de cierre próximo ──────────────────
    # El cierre debe corresponder al período de vencimiento próximo (sin ajuste hábil)
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

    # ── Agrupar cuotas por período de resumen ─────────────────────────
    from app.services.dias_habiles_service import ajustar_fecha_habil_sync

    # Período del Resumen Actual: (venc_anterior, fecha_vencimiento_proximo]
    proximo_mes_ant = date(fecha_vencimiento_proximo.year, fecha_vencimiento_proximo.month, 1) - relativedelta(months=1)
    ultimo_dia_anterior = monthrange(proximo_mes_ant.year, proximo_mes_ant.month)[1]
    venc_ant_nom = date(proximo_mes_ant.year, proximo_mes_ant.month, min(tarjeta.dia_vencimiento, ultimo_dia_anterior))
    venc_anterior = ajustar_fecha_habil_sync(venc_ant_nom, direccion="posterior")

    # Período del Próximo Resumen: (fecha_vencimiento_proximo, venc_siguiente]
    proximo_mes_sig = date(fecha_vencimiento_proximo.year, fecha_vencimiento_proximo.month, 1) + relativedelta(months=1)
    ultimo_dia_siguiente = monthrange(proximo_mes_sig.year, proximo_mes_sig.month)[1]
    venc_sig_nom = date(proximo_mes_sig.year, proximo_mes_sig.month, min(tarjeta.dia_vencimiento, ultimo_dia_siguiente))
    venc_siguiente = ajustar_fecha_habil_sync(venc_sig_nom, direccion="posterior")

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

        f_cuota = cuota.fecha_vencimiento

        if venc_anterior < f_cuota <= fecha_vencimiento_proximo:
            # Resumen actual: cae en el período propio del resumen actual
            cuotas_actual.append(cuota_data)
        elif fecha_vencimiento_proximo < f_cuota <= venc_siguiente:
            # Resumen siguiente: cae en el período propio del próximo resumen
            cuotas_siguiente.append(cuota_data)
        elif f_cuota <= venc_anterior:
            # Resumen anterior: identificar el período mensual de cierre/vencimiento que lo contiene
            base_m = date(f_cuota.year, f_cuota.month, 1)
            p_year, p_month, p_venc = base_m.year, base_m.month, None
            for offset in [0, -1, 1, 2, -2]:
                m_curr = base_m + relativedelta(months=offset)
                m_prev = m_curr - relativedelta(months=1)
                u_p = monthrange(m_prev.year, m_prev.month)[1]
                v_p = ajustar_fecha_habil_sync(date(m_prev.year, m_prev.month, min(tarjeta.dia_vencimiento, u_p)), direccion="posterior")
                u_c = monthrange(m_curr.year, m_curr.month)[1]
                v_c = ajustar_fecha_habil_sync(date(m_curr.year, m_curr.month, min(tarjeta.dia_vencimiento, u_c)), direccion="posterior")
                if v_p < f_cuota <= v_c:
                    p_year, p_month, p_venc = m_curr.year, m_curr.month, v_c
                    break
            if p_venc is None:
                p_venc = f_cuota

            venc_key = f"{p_year}-{p_month:02d}"
            nombre_mes_es = MESES_ES.get(p_venc.strftime("%B"), p_venc.strftime("%B"))
            mes_label = f"{nombre_mes_es} {p_year}"
            
            if venc_key not in anteriores_dict:
                cierre_date = calcular_fecha_cierre_de_vencimiento(
                    p_venc, tarjeta.dia_cierre, tarjeta.dia_vencimiento
                )
                anteriores_dict[venc_key] = {
                    "mes": mes_label,
                    "fecha_vencimiento": p_venc,
                    "fecha_cierre": cierre_date,
                    "total": Decimal(0),
                    "moneda": tarjeta.moneda.value,
                    "pagado": True,
                    "cuotas": [],
                    "total_ars": Decimal(0),
                    "total_usd": Decimal(0),
                    "totales_por_moneda": {"ARS": Decimal(0), "USD": Decimal(0)}
                }
            
            # Tarea 2.3: Separar por moneda en resúmenes anteriores (no mezclar ARS + USD)
            if cuota_data.moneda == "ARS":
                anteriores_dict[venc_key]["total_ars"] += cuota_data.monto
                anteriores_dict[venc_key]["totales_por_moneda"]["ARS"] += cuota_data.monto
            elif cuota_data.moneda == "USD":
                anteriores_dict[venc_key]["total_usd"] += cuota_data.monto
                anteriores_dict[venc_key]["totales_por_moneda"]["USD"] += cuota_data.monto

            if cuota_data.moneda == tarjeta.moneda.value:
                anteriores_dict[venc_key]["total"] += cuota_data.monto

            if not cuota.pagada:
                anteriores_dict[venc_key]["pagado"] = False
            anteriores_dict[venc_key]["cuotas"].append(cuota_data)
        else:
            # Resumen futuro: identificar el período mensual que lo contiene
            base_m = date(f_cuota.year, f_cuota.month, 1)
            p_year, p_month, p_venc = base_m.year, base_m.month, None
            for offset in [0, 1, -1, 2, -2]:
                m_curr = base_m + relativedelta(months=offset)
                m_prev = m_curr - relativedelta(months=1)
                u_p = monthrange(m_prev.year, m_prev.month)[1]
                v_p = ajustar_fecha_habil_sync(date(m_prev.year, m_prev.month, min(tarjeta.dia_vencimiento, u_p)), direccion="posterior")
                u_c = monthrange(m_curr.year, m_curr.month)[1]
                v_c = ajustar_fecha_habil_sync(date(m_curr.year, m_curr.month, min(tarjeta.dia_vencimiento, u_c)), direccion="posterior")
                if v_p < f_cuota <= v_c:
                    p_year, p_month, p_venc = m_curr.year, m_curr.month, v_c
                    break
            if p_venc is None:
                p_venc = f_cuota

            mes_key = f"{p_year}-{p_month:02d}"
            nombre_mes_es = MESES_ES.get(p_venc.strftime("%B"), p_venc.strftime("%B"))
            mes_label = f"{nombre_mes_es} {p_year}"
            
            if mes_key not in futuros_dict:
                futuros_dict[mes_key] = {
                    "mes": mes_label,
                    "mes_fecha": date(p_year, p_month, 1),
                    "total": Decimal(0),
                    "moneda": tarjeta.moneda.value,
                    "cantidad_cuotas": 0,
                    "cuotas": [],
                    "total_ars": Decimal(0),
                    "total_usd": Decimal(0),
                    "totales_por_moneda": {"ARS": Decimal(0), "USD": Decimal(0)}
                }

            # Tarea 2.3: Separar por moneda en resúmenes futuros
            if cuota_data.moneda == "ARS":
                futuros_dict[mes_key]["total_ars"] += cuota_data.monto
                futuros_dict[mes_key]["totales_por_moneda"]["ARS"] += cuota_data.monto
            elif cuota_data.moneda == "USD":
                futuros_dict[mes_key]["total_usd"] += cuota_data.monto
                futuros_dict[mes_key]["totales_por_moneda"]["USD"] += cuota_data.monto

            if cuota_data.moneda == tarjeta.moneda.value:
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
    # Excluir cuotas ya pagadas de los totales pendientes del resumen actual y siguiente
    total_actual_tarjeta = sum(c.monto for c in cuotas_actual if c.moneda == tarjeta_moneda_str and not c.pagada)
    total_sig_tarjeta = sum(c.monto for c in cuotas_siguiente if c.moneda == tarjeta_moneda_str and not c.pagada)
    total_actual_ars = sum(c.monto for c in cuotas_actual if c.moneda == "ARS" and not c.pagada)
    total_actual_usd = sum(c.monto for c in cuotas_actual if c.moneda == "USD" and not c.pagada)
    total_sig_ars = sum(c.monto for c in cuotas_siguiente if c.moneda == "ARS" and not c.pagada)
    total_sig_usd = sum(c.monto for c in cuotas_siguiente if c.moneda == "USD" and not c.pagada)

    # Totales originales completos (incluyendo pagadas) para referencia en UI
    total_orig_actual = sum(c.monto for c in cuotas_actual if c.moneda == tarjeta_moneda_str)
    total_orig_sig = sum(c.monto for c in cuotas_siguiente if c.moneda == tarjeta_moneda_str)
    total_orig_actual_ars = sum(c.monto for c in cuotas_actual if c.moneda == "ARS")
    total_orig_actual_usd = sum(c.monto for c in cuotas_actual if c.moneda == "USD")

    # Deuda vencida impaga de resúmenes anteriores desglosada por moneda
    total_deuda_vencida_ars = sum(
        c.monto
        for ra in resumenes_anteriores
        for c in ra.cuotas
        if not c.pagada and c.moneda == "ARS"
    )
    total_deuda_vencida_usd = sum(
        c.monto
        for ra in resumenes_anteriores
        for c in ra.cuotas
        if not c.pagada and c.moneda == "USD"
    )
    total_deuda_vencida_anterior = total_deuda_vencida_ars if tarjeta_moneda_str == "ARS" else total_deuda_vencida_usd

    # Saldos arrastrados (financiados) activos de resúmenes anteriores o del actual
    if _tabla_saldo_arrastrado_existe(db):
        saldos_activos = (
            db.query(SaldoArrastradoTarjeta)
            .filter(
                SaldoArrastradoTarjeta.tarjeta_id == tarjeta.id,
                SaldoArrastradoTarjeta.estado == EstadoSaldoArrastrado.ACTIVO,
                SaldoArrastradoTarjeta.fecha_vencimiento_resumen <= fecha_vencimiento_proximo
            )
            .order_by(SaldoArrastradoTarjeta.fecha_vencimiento_resumen.asc())
            .all()
        )
    else:
        saldos_activos = []

    items_saldo: list[ItemSaldoArrastrado] = []
    items_saldo_ars: list[ItemSaldoArrastrado] = []
    items_saldo_usd: list[ItemSaldoArrastrado] = []
    total_saldo_arrastrado_ars = Decimal("0")
    total_saldo_arrastrado_usd = Decimal("0")

    for s in saldos_activos:
        s_moneda = s.moneda.value if hasattr(s.moneda, "value") else str(s.moneda)
        f_orig = s.fecha_vencimiento_resumen
        nombre_mes = MESES_ES.get(f_orig.strftime("%B"), f_orig.strftime("%B"))
        item = ItemSaldoArrastrado(
            id=s.id,
            fecha_vencimiento_origen=f_orig,
            monto_inicial=s.monto_inicial,
            monto_restante=s.monto_restante,
            moneda=s_moneda,
            descripcion=f"Saldo financiado resumen {nombre_mes} {f_orig.year}"
        )
        if s_moneda == "ARS":
            total_saldo_arrastrado_ars += s.monto_restante
            items_saldo_ars.append(item)
        elif s_moneda == "USD":
            total_saldo_arrastrado_usd += s.monto_restante
            items_saldo_usd.append(item)

        if s_moneda == tarjeta_moneda_str:
            items_saldo.append(item)

    total_saldo_arrastrado = total_saldo_arrastrado_ars if tarjeta_moneda_str == "ARS" else total_saldo_arrastrado_usd

    # Total a pagar del resumen actual por moneda
    total_a_pagar_ars = total_actual_ars + total_deuda_vencida_ars + total_saldo_arrastrado_ars
    total_a_pagar_usd = total_actual_usd + total_deuda_vencida_usd + total_saldo_arrastrado_usd
    total_a_pagar_resumen_actual = total_a_pagar_ars if tarjeta_moneda_str == "ARS" else total_a_pagar_usd

    # Fórmula estándar de pago mínimo estimado (Tarea 4.1):
    # 10% consumos de un pago y del saldo financiado, 60% cuotas que vencen en el período,
    # y 100% de cargos, comisiones, intereses y deuda vencida impaga.
    minimo_ars = Decimal("0")
    for c in cuotas_actual:
        if c.moneda == "ARS" and not c.pagada:
            sub_nom = (c.subcategoria_nombre or "").lower()
            desc_nom = (c.descripcion or "").lower()
            es_cargo_interes = any(k in sub_nom or k in desc_nom for k in ["interes", "comision", "cargo", "impuesto"])
            if es_cargo_interes:
                minimo_ars += c.monto
            elif c.total_cuotas == 1:
                minimo_ars += c.monto * Decimal("0.10")
            else:
                minimo_ars += c.monto * Decimal("0.60")
    minimo_ars += total_saldo_arrastrado_ars * Decimal("0.10")
    minimo_ars += total_deuda_vencida_ars
    minimo_ars = min(minimo_ars, total_a_pagar_ars)
    minimo_ars = max(Decimal("0"), minimo_ars).quantize(Decimal("0.01"))

    minimo_usd = Decimal("0")
    for c in cuotas_actual:
        if c.moneda == "USD" and not c.pagada:
            sub_nom = (c.subcategoria_nombre or "").lower()
            desc_nom = (c.descripcion or "").lower()
            es_cargo_interes = any(k in sub_nom or k in desc_nom for k in ["interes", "comision", "cargo", "impuesto"])
            if es_cargo_interes:
                minimo_usd += c.monto
            elif c.total_cuotas == 1:
                minimo_usd += c.monto * Decimal("0.10")
            else:
                minimo_usd += c.monto * Decimal("0.60")
    minimo_usd += total_saldo_arrastrado_usd * Decimal("0.10")
    minimo_usd += total_deuda_vencida_usd
    minimo_usd = min(minimo_usd, total_a_pagar_usd)
    minimo_usd = max(Decimal("0"), minimo_usd).quantize(Decimal("0.01"))

    pago_minimo_estimado = minimo_ars if tarjeta_moneda_str == "ARS" else minimo_usd

    # Tarea 2.5: Cotización oficial y percepción para el total en dólares
    from app.services.dolar_service import obtener_cotizacion_por_fecha
    cot_oficial_obj = obtener_cotizacion_por_fecha(db, "oficial", fecha_cierre_proximo)
    cot_oficial_val = None
    if cot_oficial_obj:
        cot_oficial_val = Decimal(str(cot_oficial_obj.promedio or cot_oficial_obj.venta))

    porcentaje_percep = getattr(tarjeta, "percepcion_moneda_extranjera", Decimal("30.00"))
    total_estimado_ars = None
    if cot_oficial_val is not None and total_a_pagar_usd > Decimal("0"):
        monto_conv = total_a_pagar_usd * cot_oficial_val
        monto_percep = monto_conv * (porcentaje_percep / Decimal("100"))
        total_estimado_ars = (monto_conv + monto_percep).quantize(Decimal("0.01"))

    bloque_ars = BloqueResumenMoneda(
        moneda="ARS",
        total_cuotas_periodo=total_actual_ars,
        total_original_periodo=total_orig_actual_ars,
        total_deuda_vencida_anterior=total_deuda_vencida_ars,
        saldo_arrastrado_impago=total_saldo_arrastrado_ars,
        items_saldo_arrastrado=items_saldo_ars,
        total_a_pagar=total_a_pagar_ars,
        pago_minimo_estimado=minimo_ars
    )

    bloque_usd = BloqueResumenMoneda(
        moneda="USD",
        total_cuotas_periodo=total_actual_usd,
        total_original_periodo=total_orig_actual_usd,
        total_deuda_vencida_anterior=total_deuda_vencida_usd,
        saldo_arrastrado_impago=total_saldo_arrastrado_usd,
        items_saldo_arrastrado=items_saldo_usd,
        total_a_pagar=total_a_pagar_usd,
        pago_minimo_estimado=minimo_usd,
        cotizacion_oficial_estimada=cot_oficial_val,
        porcentaje_percepcion=porcentaje_percep,
        total_estimado_ars=total_estimado_ars
    )

    totales_por_moneda = {
        "ARS": bloque_ars,
        "USD": bloque_usd
    }

    return ResumenTarjeta(
        fecha_cierre_proximo=fecha_cierre_proximo,
        fecha_vencimiento_proximo=fecha_vencimiento_proximo,
        total_comprometido_resumen_actual=total_actual_tarjeta,
        total_comprometido_resumen_siguiente=total_sig_tarjeta,
        total_original_resumen_actual=total_orig_actual,
        total_original_resumen_siguiente=total_orig_sig,
        total_deuda_vencida_anterior=total_deuda_vencida_anterior,
        saldo_arrastrado_impago=total_saldo_arrastrado,
        items_saldo_arrastrado=items_saldo,
        total_a_pagar_resumen_actual=total_a_pagar_resumen_actual,
        pago_minimo_estimado=pago_minimo_estimado,
        pago_minimo_es_estimado=True,
        pago_minimo_aclaracion="Monto de referencia orientativo. El valor definitivo lo establece la entidad bancaria en el resumen de cuenta.",
        total_actual_ars=total_actual_ars,
        total_actual_usd=total_actual_usd,
        total_siguiente_ars=total_sig_ars,
        total_siguiente_usd=total_sig_usd,
        totales_moneda_actual={"ARS": total_actual_ars, "USD": total_actual_usd},
        totales_moneda_siguiente={"ARS": total_sig_ars, "USD": total_sig_usd},
        totales_por_moneda=totales_por_moneda,
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
    fecha_resumen: date | None = None,
    monto: Decimal | None = None,
    moneda: Moneda | None = None,
    billetera_id: UUID | None = None,
    pesificar: bool = False,
    cotizacion_personalizada: Decimal | None = None,
    monto_pesos_personalizado: Decimal | None = None,
    monto_percepcion_personalizado: Decimal | None = None
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
    if monto is not None:
        if monto <= Decimal("0"):
            raise HTTPException(status_code=400, detail="El monto a pagar tiene que ser mayor a cero.")
        if monto > total_a_pagar:
            raise HTTPException(
                status_code=400,
                detail=f"El monto a pagar ({formatear_monto(monto, moneda_a_pagar)}) no puede superar el total a pagar del resumen ({formatear_monto(total_a_pagar, moneda_a_pagar)})."
            )
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
                    Billetera.estado == "activa"
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
        descripcion_pago = f"Pago resumen {ultimos_4} (USD {monto_pago:,.2f})"
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
                es_recurrente=False,
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
            subcat_impuestos = db.query(Subcategoria).filter(
                Subcategoria.categoria_id == categoria.id,
                Subcategoria.nombre.ilike("Impuestos")
            ).first() if categoria else None

            if not subcat_impuestos and categoria:
                subcat_impuestos = Subcategoria(
                    categoria_id=categoria.id,
                    nombre="Impuestos",
                    orden=10
                )
                db.add(subcat_impuestos)
                db.flush()

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
                es_recurrente=False,
                es_cuota_hija=False,
                es_padre_cuotas=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                pago_origen_id=tx.id
            )
            tx_percepcion = transaccion_service.crear_transaccion(db, usuario_id, tx_percepcion_data, commit=False)

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

        db.commit()
        db.refresh(tx)

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

