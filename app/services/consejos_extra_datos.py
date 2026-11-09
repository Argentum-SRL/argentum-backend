# -*- coding: utf-8 -*-
"""
app/services/consejos_extra_datos.py
Recolección de datos adicionales para los consejos C8 a C13 (fase_i2b_consejos).
Solo lectura: sin commit, flush ni llamadas de red.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.meta import Meta, EstadoMeta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.saldo_arrastrado import SaldoArrastradoTarjeta, EstadoSaldoArrastrado
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.services import gasto_filas
from app.services.consejos_extra import (
    DatosExtra,
    IngresoDato,
    MetaDato,
    ResumenUsdDato,
    SaldoArrastradoDato,
)
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_ingreso
from app.services.dolar_service import obtener_cotizacion_por_fecha
from app.services.ingreso_habitual_service import obtener_ingreso_habitual
from app.services.resumen_tarjeta_service import calcular_resumen_actual
from app.utils.fecha import TZ_ARGENTINA


def recolectar_extra(
    db: Session,
    usuario: Usuario,
    hoy: date,
    cerrados: list[tuple[date, date]],
    ciclo_actual: tuple[date, date],
) -> DatosExtra:
    """
    Recolecta datos extendidos para consejos C8 a C13.
    Solo lectura: sin commit, sin flush, sin red.
    """
    # 1. Saldos arrastrados activos con monto_restante > 0 de las tarjetas del usuario (también archivadas)
    saldos_db = (
        db.query(SaldoArrastradoTarjeta, TarjetaCredito.nombre)
        .join(TarjetaCredito, SaldoArrastradoTarjeta.tarjeta_id == TarjetaCredito.id)
        .filter(
            TarjetaCredito.usuario_id == usuario.id,
            SaldoArrastradoTarjeta.estado == EstadoSaldoArrastrado.ACTIVO,
            SaldoArrastradoTarjeta.monto_restante > Decimal("0"),
        )
        .all()
    )
    saldos = tuple(
        SaldoArrastradoDato(
            tarjeta=tarjeta_nom,
            tarjeta_id=str(s.tarjeta_id),
            moneda=s.moneda.value if hasattr(s.moneda, "value") else str(s.moneda),
            monto_restante=Decimal(str(s.monto_restante)),
            vencimiento=s.fecha_vencimiento_resumen,
        )
        for s, tarjeta_nom in saldos_db
    )

    # 2. Resúmenes USD de tarjetas de crédito activas
    tarjetas_activas = (
        db.query(TarjetaCredito)
        .filter(
            TarjetaCredito.usuario_id == usuario.id,
            TarjetaCredito.estado == EstadoTarjeta.ACTIVA,
        )
        .all()
    )
    resumenes_usd_lista = []
    for t in tarjetas_activas:
        r = calcular_resumen_actual(db, t)
        resumenes_usd_lista.append(
            ResumenUsdDato(
                tarjeta=t.nombre,
                tarjeta_id=str(t.id),
                cierre=r.fecha_cierre_proximo,
                vencimiento=r.fecha_vencimiento_proximo,
                usd_pendiente=Decimal(str(r.total_actual_usd)),
                percepcion_pct=Decimal(str(t.percepcion_moneda_extranjera)),
            )
        )
    resumenes_usd = tuple(resumenes_usd_lista)

    # 3. Dólares propios en billeteras activas USD que no son de inversión y cotización venta oficial
    billeteras_usd = (
        db.query(Billetera)
        .filter(
            Billetera.usuario_id == usuario.id,
            Billetera.moneda == Moneda.USD,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.es_inversion.is_(False),
        )
        .all()
    )
    usd_propios = sum((Decimal(str(b.saldo_actual)) for b in billeteras_usd), Decimal("0"))
    cot = obtener_cotizacion_por_fecha(db, "oficial", hoy)
    cotizacion_venta = Decimal(str(cot.venta)) if cot and cot.venta is not None else None

    # 4. Cuotas próximas (próximo ciclo y el siguiente)
    inicio1, fin1 = get_ciclo_fechas(usuario, ciclo_actual[1] + timedelta(days=1))
    inicio2, fin2 = get_ciclo_fechas(usuario, fin1 + timedelta(days=1))
    filas_proximas = gasto_filas.filas_gasto(db, usuario.id, inicio1, fin2, hoy=fin2)

    totales_c1 = {"ARS": Decimal("0"), "USD": Decimal("0")}
    totales_c2 = {"ARS": Decimal("0"), "USD": Decimal("0")}

    for f in filas_proximas:
        if f.es_cuota:
            if inicio1 <= f.fecha <= fin1:
                if f.moneda in totales_c1:
                    totales_c1[f.moneda] += f.monto
            elif inicio2 <= f.fecha <= fin2:
                if f.moneda in totales_c2:
                    totales_c2[f.moneda] += f.monto

    cuotas_proximas = (
        (inicio1, totales_c1),
        (inicio2, totales_c2),
    )

    # 5. Ingreso habitual por moneda (normalizado a mayúsculas ARS / USD)
    res_hab = obtener_ingreso_habitual(db, usuario, hoy=hoy, incluir_temprano=False)
    ingreso_habitual: dict[str, Decimal] = {}
    if isinstance(res_hab, dict):
        for mon_k, hab_val in res_hab.items():
            if hab_val.monto is not None and hab_val.monto > Decimal("0"):
                mon_str = (mon_k.value if hasattr(mon_k, "value") else str(mon_k)).upper()
                ingreso_habitual[mon_str] = Decimal(str(hab_val.monto))
    elif hasattr(res_hab, "monto") and res_hab.monto is not None and res_hab.monto > Decimal("0"):
        mon_str = (res_hab.moneda.value if hasattr(res_hab.moneda, "value") else str(res_hab.moneda)).upper()
        ingreso_habitual[mon_str] = Decimal(str(res_hab.monto))

    # 6. Ingresos en los últimos 7 días (hoy - 6 a hoy inclusive)
    desde_7 = hoy - timedelta(days=6)
    cond_i7 = condicion_ingreso(usuario.id, desde=desde_7, hasta=hoy, moneda=None, hoy=hoy)
    txs_ingresos = (
        db.query(Transaccion, Categoria.nombre, Subcategoria.nombre)
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
        .filter(cond_i7)
        .order_by(Transaccion.fecha.desc(), Transaccion.id.desc())
        .all()
    )
    ingresos_7 = tuple(
        IngresoDato(
            id=str(t.id),
            fecha=t.fecha,
            monto=Decimal(str(t.monto)),
            moneda=t.moneda.value if hasattr(t.moneda, "value") else str(t.moneda),
            categoria=cat_nom,
            subcategoria=sub_nom,
        )
        for t, cat_nom, sub_nom in txs_ingresos
    )

    # 7. Metas activas del usuario con aportes netos en cerrados[:3] y cantidad total de aportes
    metas_db = (
        db.query(Meta)
        .filter(
            Meta.usuario_id == usuario.id,
            Meta.estado == EstadoMeta.ACTIVA,
        )
        .all()
    )
    metas_lista = []
    for m in metas_db:
        fc_ar = m.fecha_creacion.astimezone(TZ_ARGENTINA).date() if m.fecha_creacion.tzinfo else m.fecha_creacion.date()
        dias_desde_creacion = (hoy - fc_ar).days
        m_moneda = m.moneda.value if hasattr(m.moneda, "value") else str(m.moneda)

        aportes_ciclos_list = []
        for c_ini, c_fin in cerrados[:3]:
            movs = (
                db.query(MovimientoMeta)
                .filter(
                    MovimientoMeta.meta_id == m.id,
                    MovimientoMeta.fecha >= c_ini,
                    MovimientoMeta.fecha <= c_fin,
                )
                .all()
            )
            tot_c = Decimal("0")
            for mov in movs:
                mov_mon = mov.moneda_movimiento.value if hasattr(mov.moneda_movimiento, "value") else str(mov.moneda_movimiento)
                if mov_mon == m_moneda:
                    monto_conv = Decimal(str(mov.monto))
                else:
                    if not mov.cotizacion_usada or mov.cotizacion_usada <= Decimal("0"):
                        continue
                    cot_usada = Decimal(str(mov.cotizacion_usada))
                    if m_moneda == "USD" and mov_mon == "ARS":
                        # ARS -> USD divide
                        monto_conv = Decimal(str(mov.monto)) / cot_usada
                    elif m_moneda == "ARS" and mov_mon == "USD":
                        # USD -> ARS multiplica
                        monto_conv = Decimal(str(mov.monto)) * cot_usada
                    else:
                        continue

                if mov.tipo == TipoMovimientoMeta.APORTE:
                    tot_c += monto_conv
                elif mov.tipo == TipoMovimientoMeta.RETIRO:
                    tot_c -= monto_conv

            aportes_ciclos_list.append(tot_c)

        cant_aportes = (
            db.query(func.count(MovimientoMeta.id))
            .filter(
                MovimientoMeta.meta_id == m.id,
                MovimientoMeta.tipo == TipoMovimientoMeta.APORTE,
            )
            .scalar()
            or 0
        )

        metas_lista.append(
            MetaDato(
                id=str(m.id),
                nombre=m.nombre,
                moneda=m_moneda,
                objetivo=Decimal(str(m.monto_objetivo)),
                actual=Decimal(str(m.monto_actual)),
                fecha_limite=m.fecha_limite,
                dias_desde_creacion=dias_desde_creacion,
                aportes_ciclos=tuple(aportes_ciclos_list),
                cantidad_aportes=cant_aportes,
            )
        )

    return DatosExtra(
        saldos=saldos,
        resumenes_usd=resumenes_usd,
        usd_propios=usd_propios,
        cotizacion_venta=cotizacion_venta,
        cuotas_proximas=cuotas_proximas,
        ingreso_habitual=ingreso_habitual,
        ingresos_7=ingresos_7,
        metas=tuple(metas_lista),
    )

