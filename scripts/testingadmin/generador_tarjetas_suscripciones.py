"""
Módulo Generador de Tarjetas, Compras en Cuotas, Pago de Resúmenes y Suscripciones.

Cumple con:
4.7 Tarjetas:
  a. Varias compras en 1 pago por ciclo.
  b. Compras en cuotas:
     - Smart TV 55" en 12 cuotas con interés (Visa Santander, arrancó en agosto 2026).
     - Heladera en 12 cuotas sin interés (Visa Galicia, arrancó en octubre 2025).
     - Zapatillas en 3 cuotas (Visa Galicia, arrancó en mayo 2026).
  c. Resumen de cada tarjeta pagado todos los meses con el servicio real `tarjeta_service.pagar_resumen_tarjeta`.
  d. Un mes con pago parcial (abril 2026), generando saldo arrastrado que se cancela al mes siguiente.
  e. Ninguna cuota ya vencida queda impaga.
4.8 Suscripciones:
  a. Cobros históricos vinculados con `suscripcion_id` y su medio de pago.
  b. Netflix Estándar con cambio de precio el 01/03/2026 ($8.500 -> $13.500).
  c. Spotify Individual con un cobro corregido a $6.900 mientras el precio sigue en $6.200.
  d. Google One Nube 2TB anual ($42.000).
4.15 Dos o tres compras con tarjeta en dólares (Amex Galicia).
"""
from __future__ import annotations

import random
from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.dias_habiles_service import ajustar_fecha_habil_sync

from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.suscripcion import FrecuenciaSuscripcion, Suscripcion
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda
from app.schemas.suscripcion import SuscripcionCreate
from app.schemas.transaccion import InfoCuotas, TransaccionCreate, TransaccionUpdate
from app.services import cuotas_service, suscripcion_service, tarjeta_service, transaccion_service
from app.utils.fecha import hoy_argentina
from scripts.testingadmin.datos_base import CatalogoEntidades


def generar_tarjetas_y_suscripciones(
    db: Session,
    cat: CatalogoEntidades,
    rng: random.Random,
    fecha_corte: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Crea las suscripciones, compras en 1 pago, cuotas, cobros periódicos y
    ejecuta los pagos reales de resúmenes de tarjeta mes a mes.
    Ninguna transacción se crea con fecha posterior a fecha_corte (por defecto hoy_argentina).
    """
    if fecha_corte is None:
        fecha_corte = hoy_argentina()

    transacciones_creadas: List[Transaccion] = []
    resumenes_pagados_info: List[Dict[str, Any]] = []

    # =========================================================================
    # 1. SUSCRIPCIONES Y SUS COBROS HISTÓRICOS
    # =========================================================================
    # 1.1 Netflix Estándar (tarjeta Visa Galicia)
    sub_netflix = suscripcion_service.crear_suscripcion(
        db,
        cat.user.id,
        SuscripcionCreate(
            nombre="Netflix Estándar",
            monto=Decimal("13500.00"),
            moneda=Moneda.ARS,
            frecuencia=FrecuenciaSuscripcion.MENSUAL,
            categoria_id=cat.cat_recreo.id,
            subcategoria_id=cat.sub_hobbies.id,
            tarjeta_id=cat.t_visa_galicia.id,
            billetera_id=None,
            proximo_cobro=date(2026, 10, 18),
        ),
    )
    # Historial de precio: $8.500 desde 2025-08-01, $13.500 desde 2026-03-01
    h_ant = db.query(HistorialSuscripcion).filter(HistorialSuscripcion.suscripcion_id == sub_netflix.id).first()
    h_ant.monto = Decimal("8500.00")
    h_ant.vigente_desde = date(2025, 8, 1)

    h_nuevo = HistorialSuscripcion(
        suscripcion_id=sub_netflix.id,
        monto=Decimal("13500.00"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 3, 1),
    )
    db.add(h_nuevo)

    # 1.2 Spotify Individual (tarjeta Visa Galicia)
    sub_spotify = suscripcion_service.crear_suscripcion(
        db,
        cat.user.id,
        SuscripcionCreate(
            nombre="Spotify Individual",
            monto=Decimal("6200.00"),
            moneda=Moneda.ARS,
            frecuencia=FrecuenciaSuscripcion.MENSUAL,
            categoria_id=cat.cat_recreo.id,
            subcategoria_id=cat.sub_hobbies.id,
            tarjeta_id=cat.t_visa_galicia.id,
            billetera_id=None,
            proximo_cobro=date(2026, 10, 12),
        ),
    )
    h_spot = db.query(HistorialSuscripcion).filter(HistorialSuscripcion.suscripcion_id == sub_spotify.id).first()
    h_spot.vigente_desde = date(2025, 8, 1)

    # 1.3 Google One Nube 2TB (anual, tarjeta Visa Galicia)
    sub_google = suscripcion_service.crear_suscripcion(
        db,
        cat.user.id,
        SuscripcionCreate(
            nombre="Google One Nube 2TB",
            monto=Decimal("42000.00"),
            moneda=Moneda.ARS,
            frecuencia=FrecuenciaSuscripcion.ANUAL,
            categoria_id=cat.cat_comun.id,
            subcategoria_id=cat.sub_internet.id,
            tarjeta_id=cat.t_visa_galicia.id,
            billetera_id=None,
            proximo_cobro=date(2026, 11, 20),
        ),
    )
    db.commit()

    # Generación de cobros de suscripción mensuales a lo largo del tiempo
    # Lista de meses desde agosto 2025 hasta septiembre 2026
    meses_historia = [
        (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
        (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5),
        (2026, 6), (2026, 7), (2026, 8), (2026, 9),
    ]

    for anio, mes in meses_historia:
        # Netflix: cobro día 18
        precio_netf = Decimal("13500.00") if (anio == 2026 and mes >= 3) else Decimal("8500.00")
        f_netf = date(anio, mes, 18)
        if f_netf <= fecha_corte:
            tx_netf = transaccion_service.crear_transaccion(
                db,
                cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=precio_netf,
                    moneda=Moneda.ARS,
                    fecha=f_netf,
                    descripcion="Netflix Estándar",
                    categoria_id=cat.cat_recreo.id,
                    subcategoria_id=cat.sub_hobbies.id,
                    metodo_pago=MetodoPago.CREDITO,
                    tarjeta_id=cat.t_visa_galicia.id,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=True,
                    suscripcion_id=sub_netflix.id,
                    origen=OrigenTransaccion.RECURRENTE,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=True,
            )
            transacciones_creadas.append(tx_netf)

        # Spotify: cobro día 12
        # REGLA 4.8 c / DECISIÓN 3: se genera un cobro normal a 6.200 y después se edita su monto a 6.900
        # con el camino real de edición de la app, manteniendo la descripción "Spotify Individual".
        f_spot = date(anio, mes, 12)
        if f_spot <= fecha_corte:
            tx_spot = transaccion_service.crear_transaccion(
                db,
                cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=Decimal("6200.00"),
                    moneda=Moneda.ARS,
                    fecha=f_spot,
                    descripcion="Spotify Individual",
                    categoria_id=cat.cat_recreo.id,
                    subcategoria_id=cat.sub_hobbies.id,
                    metodo_pago=MetodoPago.CREDITO,
                    tarjeta_id=cat.t_visa_galicia.id,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=True,
                    suscripcion_id=sub_spotify.id,
                    origen=OrigenTransaccion.RECURRENTE,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=True,
            )
            if anio == 2026 and mes == 6:
                # Decisión 3: cobro corregido de Spotify a 6.900 mediante camino real de edición de compras de tarjeta
                from app.services import cuotas_service
                from app.schemas.grupos_cuotas import GrupoCuotasUpdate
                grupo_spot = db.query(GrupoCuotas).filter(GrupoCuotas.id == tx_spot.grupo_cuotas_id).first()
                if grupo_spot:
                    cuotas_service.actualizar_grupo(
                        db,
                        grupo_spot,
                        GrupoCuotasUpdate(monto_total_nuevo=Decimal("6900.00")),
                    )
            transacciones_creadas.append(tx_spot)

    # Google One: cobro anual el 20/11/2025
    f_google = date(2025, 11, 20)
    if f_google <= fecha_corte:
        tx_goog = transaccion_service.crear_transaccion(
            db,
            cat.user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("42000.00"),
                moneda=Moneda.ARS,
                fecha=f_google,
                descripcion="Google One Nube 2TB",
                categoria_id=cat.cat_comun.id,
                subcategoria_id=cat.sub_internet.id,
                metodo_pago=MetodoPago.CREDITO,
                tarjeta_id=cat.t_visa_galicia.id,
                billetera_id=cat.b_galicia.id,
                es_recurrente=True,
                suscripcion_id=sub_google.id,
                origen=OrigenTransaccion.RECURRENTE,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
            commit=True,
        )
        transacciones_creadas.append(tx_goog)

    # =========================================================================
    # 2. COMPRAS EN CUOTAS
    # =========================================================================
    # 2.1 Heladera No Frost 12 cuotas sin interés (Visa Galicia) - 2025-10-15
    # Vencimientos: del 2025-11-13 al 2026-10-13 (11 cuotas vencidas y pagadas a sep 2026)
    tx_hela = transaccion_service.crear_transaccion(
        db,
        cat.user.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("1200000.00"),
            moneda=Moneda.ARS,
            fecha=date(2025, 10, 15),
            descripcion="Heladera No Frost Whirlpool 12 cuotas sin interés",
            categoria_id=cat.cat_hogar_eq.id,
            subcategoria_id=cat.sub_muebles.id,
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=cat.b_galicia.id,
            tarjeta_id=cat.t_visa_galicia.id,
            primer_vencimiento_manual=date(2025, 11, 13),
            es_padre_cuotas=True,
            info_cuotas=InfoCuotas(
                cantidad_cuotas=12,
                cuota_inicial=1,
                tiene_interes=False,
                monto_total=Decimal("1200000.00"),
            ),
        ),
        commit=True,
    )
    transacciones_creadas.append(tx_hela)

    # 2.2 Zapatillas deportivas 3 cuotas (Visa Galicia) - 2026-05-20
    # Vencimientos: jun, jul, ago 2026 (todas vencidas y pagadas)
    tx_zapa = transaccion_service.crear_transaccion(
        db,
        cat.user.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("150000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 5, 20),
            descripcion="Zapatillas deportivas Nike 3 cuotas",
            categoria_id=cat.cat_indum.id,
            subcategoria_id=cat.sub_calzado.id,
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=cat.b_galicia.id,
            tarjeta_id=cat.t_visa_galicia.id,
            primer_vencimiento_manual=date(2026, 6, 13),
            es_padre_cuotas=True,
            info_cuotas=InfoCuotas(
                cantidad_cuotas=3,
                cuota_inicial=1,
                tiene_interes=False,
                monto_total=Decimal("150000.00"),
            ),
        ),
        commit=True,
    )
    transacciones_creadas.append(tx_zapa)

    # 2.3 Smart TV 55" 4K 12 cuotas con interés (Visa Santander) - 2026-08-10
    # Primer vencimiento: 2026-09-02 (pagado en sep 2026; resto pendiente futuro)
    tx_tv = transaccion_service.crear_transaccion(
        db,
        cat.user.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("780000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 8, 10),
            descripcion="Smart TV 55 Pulgadas 4K 12 cuotas",
            categoria_id=cat.cat_hogar_eq.id,
            subcategoria_id=cat.sub_muebles.id,
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=cat.b_santander.id,
            tarjeta_id=cat.t_visa_santander.id,
            primer_vencimiento_manual=date(2026, 9, 2),
            es_padre_cuotas=True,
            info_cuotas=InfoCuotas(
                cantidad_cuotas=12,
                cuota_inicial=1,
                tiene_interes=True,
                tasa_interes=Decimal("4.50"),
                monto_total=Decimal("780000.00"),
            ),
        ),
        commit=True,
    )
    transacciones_creadas.append(tx_tv)

    # =========================================================================
    # 3. COMPRAS EN 1 PAGO CON TARJETA
    # =========================================================================
    compras_1_pago = [
        # 2025
        (date(2025, 8, 14), Decimal("48000.00"), "Indumentaria remera y abrigo", cat.cat_indum, cat.sub_ropa, cat.t_visa_galicia, cat.b_galicia),
        (date(2025, 8, 22), Decimal("32000.00"), "Librería y papelería técnica", cat.cat_recreo, cat.sub_hobbies, cat.t_visa_galicia, cat.b_galicia),
        (date(2025, 9, 10), Decimal("62000.00"), "Cena restaurante festejo", cat.cat_gastro, cat.sub_resto, cat.t_visa_galicia, cat.b_galicia),
        (date(2025, 10, 18), Decimal("41000.00"), "Accesorios computación teclado", cat.cat_hogar_eq, cat.sub_muebles, cat.t_visa_galicia, cat.b_galicia),
        (date(2025, 11, 15), Decimal("55000.00"), "Ropa calzado urbano", cat.cat_indum, cat.sub_calzado, cat.t_visa_galicia, cat.b_galicia),
        (date(2025, 12, 12), Decimal("85000.00"), "Compras navideñas shopping", cat.cat_otros_egr, cat.sub_cuidado, cat.t_visa_galicia, cat.b_galicia),
        # 2026
        (date(2026, 1, 14), Decimal("72000.00"), "Gastos playa y parador verano", cat.cat_recreo, cat.sub_salidas, cat.t_visa_galicia, cat.b_galicia),
        (date(2026, 2, 10), Decimal("38000.00"), "Almuerzo ejecutivo restaurante", cat.cat_gastro, cat.sub_resto, cat.t_visa_galicia, cat.b_galicia),
        (date(2026, 3, 16), Decimal("59000.00"), "Camisa y pantalón oficina", cat.cat_indum, cat.sub_ropa, cat.t_visa_galicia, cat.b_galicia),
        (date(2026, 4, 11), Decimal("47000.00"), "Mantenimiento auto repuestos", cat.cat_transp, cat.sub_comb, cat.t_visa_galicia, cat.b_galicia),
        (date(2026, 5, 15), Decimal("52000.00"), "Cena amigos restaurante", cat.cat_gastro, cat.sub_resto, cat.t_visa_galicia, cat.b_galicia),
        (date(2026, 6, 14), Decimal("68000.00"), "Campera invierno liquidación", cat.cat_indum, cat.sub_ropa, cat.t_visa_galicia, cat.b_galicia),
        (date(2026, 7, 16), Decimal("44000.00"), "Cena amigos sushi", cat.cat_gastro, cat.sub_resto, cat.t_visa_galicia, cat.b_galicia),
        (date(2026, 8, 14), Decimal("58000.00"), "Salida bar fin de semana", cat.cat_recreo, cat.sub_salidas, cat.t_visa_galicia, cat.b_galicia),
        (date(2026, 9, 10), Decimal("42000.00"), "Almuerzo fin de semana restaurante", cat.cat_gastro, cat.sub_resto, cat.t_visa_galicia, cat.b_galicia),
        # Santander
        (date(2025, 9, 20), Decimal("39000.00"), "Supermercado compras con descuento Santander", cat.cat_alim, cat.sub_super, cat.t_visa_santander, cat.b_santander),
        (date(2025, 12, 18), Decimal("54000.00"), "Regalos fin de año promo Santander", cat.cat_otros_egr, cat.sub_cuidado, cat.t_visa_santander, cat.b_santander),
        (date(2026, 3, 22), Decimal("46000.00"), "Combustible promo Santander", cat.cat_transp, cat.sub_comb, cat.t_visa_santander, cat.b_santander),
        (date(2026, 7, 21), Decimal("61000.00"), "Indumentaria abrigo promo Santander", cat.cat_indum, cat.sub_ropa, cat.t_visa_santander, cat.b_santander),
    ]

    for f_pago, monto_p, desc_p, cat_p, subcat_p, tarj_p, bill_p in compras_1_pago:
        if f_pago > fecha_corte:
            continue
        tx_1p = transaccion_service.crear_transaccion(
            db,
            cat.user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=monto_p,
                moneda=Moneda.ARS,
                fecha=f_pago,
                descripcion=desc_p,
                categoria_id=cat_p.id,
                subcategoria_id=subcat_p.id,
                metodo_pago=MetodoPago.CREDITO,
                tarjeta_id=tarj_p.id,
                billetera_id=bill_p.id,
                es_recurrente=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
            commit=True,
        )
        transacciones_creadas.append(tx_1p)

    # =========================================================================
    # 4. COMPRAS EN DÓLARES CON TARJETA (Amex Galicia)
    # =========================================================================
    consumos_usd = [
        (date(2025, 9, 15), Decimal("35.00"), "AWS Cloud Infrastructure Hosting USD"),
        (date(2026, 1, 12), Decimal("48.00"), "Udemy Online Courses Tech Specialization USD"),
        (date(2026, 6, 18), Decimal("29.00"), "GitHub Copilot Developer SaaS USD"),
    ]

    for f_usd, m_usd, desc_usd in consumos_usd:
        if f_usd > fecha_corte:
            continue
        tx_u = transaccion_service.crear_transaccion(
            db,
            cat.user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=m_usd,
                moneda=Moneda.USD,
                fecha=f_usd,
                descripcion=desc_usd,
                categoria_id=cat.cat_comun.id,
                subcategoria_id=cat.sub_internet.id,
                metodo_pago=MetodoPago.CREDITO,
                tarjeta_id=cat.t_amex_galicia.id,
                billetera_id=cat.b_efectivo_usd.id,
                es_recurrente=False,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
            commit=True,
        )
        transacciones_creadas.append(tx_u)

    # =========================================================================
    # 5. PAGO MENSUAL DE RESÚMENES CON EL SERVICIO REAL
    # =========================================================================
    # Para Visa Galicia y Amex Galicia: vencimiento día 13 de cada mes
    # Para Visa Santander: vencimiento día 2 de cada mes
    # Simulación cronológica mensual desde agosto 2025 hasta septiembre 2026:
    for anio, mes in meses_historia:
        # Vencimiento Visa Galicia y Amex Galicia: día 13 (ajustado a hábil posterior)
        u_gal = monthrange(anio, mes)[1]
        vto_galicia = ajustar_fecha_habil_sync(
            date(anio, mes, min(cat.t_visa_galicia.dia_vencimiento, u_gal)),
            direccion="posterior"
        )

        if vto_galicia <= fecha_corte:
            # Mes especial con Pago Parcial: Abril 2026
            # REGLA 4.7 d: Un mes con pago parcial, solo si el sistema maneja saldo arrastrado
            if anio == 2026 and mes == 4:
                # Obtener deuda total pendiente para calcular el 70% de pago
                cuotas_pend = db.query(Cuota).join(GrupoCuotas).filter(
                    GrupoCuotas.tarjeta_id == cat.t_visa_galicia.id,
                    Cuota.pagada == False,
                    Cuota.fecha_vencimiento <= vto_galicia,
                ).all()
                total_vto = sum(c.monto_real or c.monto_proyectado for c in cuotas_pend)
                if total_vto > Decimal("0"):
                    pago_parcial = (total_vto * Decimal("0.70")).quantize(Decimal("0.01"))
                    try:
                        tx_pago = tarjeta_service.pagar_resumen_tarjeta(
                            db=db,
                            usuario_id=cat.user.id,
                            tarjeta_id=cat.t_visa_galicia.id,
                            fecha_pago=vto_galicia,
                            fecha_resumen=vto_galicia,
                            monto=pago_parcial,
                            moneda=Moneda.ARS,
                            billetera_id=cat.b_galicia.id,
                        )
                        transacciones_creadas.append(tx_pago)
                        resumenes_pagados_info.append({
                            "tarjeta": "Visa Galicia (•••• 1506)",
                            "vencimiento": vto_galicia.isoformat(),
                            "tipo": "pago_parcial",
                            "monto_pagado": str(pago_parcial),
                            "total_resumen": str(total_vto),
                        })
                    except HTTPException as e:
                        pass
            else:
                # Pago total habitual
                try:
                    tx_pago = tarjeta_service.pagar_resumen_tarjeta(
                        db=db,
                        usuario_id=cat.user.id,
                        tarjeta_id=cat.t_visa_galicia.id,
                        fecha_pago=vto_galicia,
                        fecha_resumen=vto_galicia,
                        monto=None,  # Pago total
                        moneda=Moneda.ARS,
                        billetera_id=cat.b_galicia.id,
                    )
                    transacciones_creadas.append(tx_pago)
                    resumenes_pagados_info.append({
                        "tarjeta": "Visa Galicia (•••• 1506)",
                        "vencimiento": vto_galicia.isoformat(),
                        "tipo": "pago_total",
                        "monto_pagado": str(tx_pago.monto),
                    })
                except HTTPException as e:
                    pass  # Si no hay deuda para este resumen, no hace falta pagar

            # Vencimiento Amex Galicia en dólares: día 13 (ajustado a hábil posterior)
            # Pagamos consumos en dólares desde Efectivo USD
            try:
                tx_amex = tarjeta_service.pagar_resumen_tarjeta(
                    db=db,
                    usuario_id=cat.user.id,
                    tarjeta_id=cat.t_amex_galicia.id,
                    fecha_pago=vto_galicia,
                    fecha_resumen=vto_galicia,
                    monto=None,
                    moneda=Moneda.USD,
                    billetera_id=cat.b_efectivo_usd.id,
                    pesificar=False,
                )
                transacciones_creadas.append(tx_amex)
                resumenes_pagados_info.append({
                    "tarjeta": "Amex Galicia (•••• 2745)",
                    "vencimiento": vto_galicia.isoformat(),
                    "tipo": "pago_total_usd",
                    "monto_pagado": str(tx_amex.monto),
                })
            except HTTPException as e:
                pass

        # Vencimiento Visa Santander: día 2 (ajustado a hábil posterior)
        u_san = monthrange(anio, mes)[1]
        vto_santander = ajustar_fecha_habil_sync(
            date(anio, mes, min(cat.t_visa_santander.dia_vencimiento, u_san)),
            direccion="posterior"
        )
        if vto_santander <= fecha_corte:
            try:
                tx_san = tarjeta_service.pagar_resumen_tarjeta(
                    db=db,
                    usuario_id=cat.user.id,
                    tarjeta_id=cat.t_visa_santander.id,
                    fecha_pago=vto_santander,
                    fecha_resumen=vto_santander,
                    monto=None,
                    moneda=Moneda.ARS,
                    billetera_id=cat.b_santander.id,
                )
                transacciones_creadas.append(tx_san)
                resumenes_pagados_info.append({
                    "tarjeta": "Visa Santander (•••• 5077)",
                    "vencimiento": vto_santander.isoformat(),
                    "tipo": "pago_total",
                    "monto_pagado": str(tx_san.monto),
                })
            except HTTPException as e:
                pass

    # Asegurar commit final de todas las transacciones y cuotas
    db.commit()

    return {
        "transacciones": transacciones_creadas,
        "verdad_tarjetas_suscripciones": {
            "suscripciones": [
                {
                    "nombre": "Netflix Estándar",
                    "medio_pago": "Visa Galicia (•••• 1506)",
                    "precio_original": "8500.00",
                    "precio_actual": "13500.00",
                    "fecha_aumento": "2026-03-01",
                },
                {
                    "nombre": "Spotify Individual",
                    "medio_pago": "Visa Galicia (•••• 1506)",
                    "precio": "6200.00",
                    "cobro_editado": {
                        "fecha": "2026-06-12",
                        "monto_cobrado": "6900.00",
                        "precio_guardado": "6200.00",
                    },
                },
                {
                    "nombre": "Google One Nube 2TB",
                    "frecuencia": "anual",
                    "medio_pago": "Visa Galicia (•••• 1506)",
                    "precio": "42000.00",
                    "fecha_cobro": "2025-11-20",
                },
            ],
            "compras_cuotas": [
                {
                    "descripcion": "Heladera No Frost Whirlpool",
                    "cuotas": 12,
                    "interes": False,
                    "tarjeta": "Visa Galicia (•••• 1506)",
                    "monto_total": "1200000.00",
                    "primer_vto": "2025-11-13",
                },
                {
                    "descripcion": "Zapatillas deportivas Nike",
                    "cuotas": 3,
                    "interes": False,
                    "tarjeta": "Visa Galicia (•••• 1506)",
                    "monto_total": "150000.00",
                    "primer_vto": "2026-06-13",
                },
                {
                    "descripcion": "Smart TV 55 Pulgadas 4K",
                    "cuotas": 12,
                    "interes": True,
                    "tasa_interes": "4.50%",
                    "tarjeta": "Visa Santander (•••• 5077)",
                    "monto_total": "780000.00",
                    "primer_vto": "2026-09-02",
                },
            ],
            "pago_parcial_saldo_arrastrado": {
                "tarjeta": "Visa Galicia (•••• 1506)",
                "mes_parcial": "2026-04",
                "mes_cancelacion_total": "2026-05",
            },
            "resumenes_pagados": resumenes_pagados_info,
        },
    }
