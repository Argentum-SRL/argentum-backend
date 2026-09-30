"""
Servicio para el cálculo y consulta del resumen de tarjetas de crédito.
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models.billetera import Billetera
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.saldo_arrastrado import (
    EstadoSaldoArrastrado,
    PagoSaldoArrastrado,
    SaldoArrastradoTarjeta,
)
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda
from app.schemas.tarjeta_credito import (
    BloqueResumenMoneda,
    CuotaPendienteOtraMoneda,
    CuotaResumen,
    ItemSaldoArrastrado,
    ResumenAnterior,
    ResumenFuturo,
    ResumenTarjeta,
)
from app.services.tarjeta_service import (
    MESES_ES,
    calcular_fecha_cierre_de_vencimiento,
    calcular_fecha_vencimiento_proximo,
    get_info_transaccion,
)
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto

logger = logging.getLogger(__name__)


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
            pagada=cuota.pagada,
            suscripcion_id=cuota.transaccion.suscripcion_id if cuota.transaccion else None,
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

