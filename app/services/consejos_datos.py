# -*- coding: utf-8 -*-
"""
app/services/consejos_datos.py
Módulo de recolección de datos para el motor de consejos.
Solo lectura: sin commit, flush ni llamadas de red.
"""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.meta import Meta, EstadoMeta
from app.models.transaccion import Transaccion
from app.models.usuario import Usuario
from app.services import gasto_filas, presupuesto_sugerencias
from app.services.consejos_reglas import DatosConsejos
from app.services.definiciones_service import condicion_ingreso
from app.services.dolar_service import obtener_cotizacion_por_fecha
from app.services.fin_de_mes_service import calcular_fin_de_mes
from app.services.inflacion_esperada_service import InflacionEsperada, MESES


def _quitar_tildes(texto: str) -> str:
    """Convierte a minúsculas y remueve acentos/diacríticos."""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    ).lower()


def recolectar(
    db: Session,
    usuario: Usuario,
    hoy: date,
    inflacion: InflacionEsperada | None = None,
) -> DatosConsejos:
    """
    Recolecta todos los datos necesarios para evaluar los consejos de un usuario.
    Solo lectura: sin commit, sin flush, sin red.
    """
    moneda_principal = (
        usuario.moneda_principal.value
        if hasattr(usuario.moneda_principal, "value")
        else str(usuario.moneda_principal)
    ) if usuario.moneda_principal else "ARS"

    # 1. Fin de mes
    fdm = calcular_fin_de_mes(db, usuario, hoy=hoy)
    libre_ars = fdm.libre_ars
    saldo_hoy_ars = fdm.saldo_hoy
    datos_viejos = fdm.frescura.datos_viejos
    cobertura = fdm.cobertura
    factor_cobertura = fdm.factor_cobertura
    ciclo_actual = (fdm.inicio_ciclo, fdm.fin_ciclo)

    # 1b. IPC
    ipc = gasto_filas.indices_ipc(db)

    # 2. Fecha mínima de transacciones
    min_fecha = db.query(func.min(Transaccion.fecha)).filter(
        Transaccion.usuario_id == usuario.id
    ).scalar()

    if min_fecha is None:
        return DatosConsejos(
            hoy=hoy,
            hay_datos=False,
            moneda_principal=moneda_principal,
            datos_viejos=datos_viejos,
            cobertura=cobertura,
            ciclo_actual=ciclo_actual,
            cerrados=[],
            filas=[],
            ingresos_45=0,
            libre_ars=libre_ars,
            saldo_hoy_ars=saldo_hoy_ars,
            inversiones_ars=Decimal("0"),
            usd_total=Decimal("0"),
            cotizacion_compra=None,
            tasa_inflacion=None,
            mes_inflacion=None,
            fuente_inflacion=None,
            billetera_inversion=None,
            tiene_meta_colchon=False,
            factor_cobertura=Decimal("1"),
            ipc={},
        )

    # 3. Cerrados: desde el ciclo anterior hacia atrás con get_ciclo_fechas, hasta 6
    cerrados = gasto_filas.ciclos_cerrados(usuario, fdm.inicio_ciclo, min_fecha, hasta=6)

    # 4. Filas de gasto: UNA consulta
    limite_89 = hoy - timedelta(days=89)
    desde_filas = min(cerrados[-1][0], limite_89) if cerrados else limite_89
    filas = gasto_filas.filas_gasto(db, usuario.id, desde_filas, hoy, hoy)

    # 5. Ingresos en los últimos 45 días (desde hoy - 44 hasta hoy inclusive)
    desde_ingresos = hoy - timedelta(days=44)
    cond_i = condicion_ingreso(
        usuario.id,
        desde=desde_ingresos,
        hasta=hoy,
        moneda=None,
        hoy=hoy,
    )
    ingresos_45 = db.query(func.count(Transaccion.id)).filter(cond_i).scalar() or 0

    # 6. Billeteras ACTIVAS del usuario
    bils = db.query(Billetera).filter(
        Billetera.usuario_id == usuario.id,
        Billetera.estado == EstadoBilletera.ACTIVA,
    ).all()

    inversiones_ars = Decimal("0")
    usd_total = Decimal("0")
    bils_inv_ars_pos: list[Billetera] = []

    for b in bils:
        saldo = Decimal(str(b.saldo_actual))
        b_moneda = b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)
        if b_moneda == "ARS":
            if b.es_inversion:
                inversiones_ars += saldo
                if saldo > Decimal("0"):
                    bils_inv_ars_pos.append(b)
        elif b_moneda == "USD":
            usd_total += saldo

    billetera_inversion = None
    if bils_inv_ars_pos:
        bils_inv_ars_pos.sort(key=lambda b: Decimal(str(b.saldo_actual)), reverse=True)
        billetera_inversion = bils_inv_ars_pos[0].nombre

    # 7. Cotización oficial compra de hoy
    cot = obtener_cotizacion_por_fecha(db, "oficial", hoy)
    cotizacion_compra = Decimal(str(cot.compra)) if cot and cot.compra is not None else None

    # 8. Metas de colchón activas
    metas_activas = db.query(Meta).filter(
        Meta.usuario_id == usuario.id,
        Meta.estado == EstadoMeta.ACTIVA,
    ).all()
    tiene_meta_colchon = any(
        any(k in _quitar_tildes(m.nombre) for k in ("imprevist", "emergenc", "colchon"))
        for m in metas_activas
    )

    # 9. Inflación esperada
    tasa_inflacion = None
    mes_inflacion = None
    fuente_inflacion = None
    if inflacion is not None and inflacion.tasas:
        tasa_inflacion = inflacion.tasas[0]
        mes_siguiente_idx = (hoy.month % 12)
        mes_inflacion = MESES[mes_siguiente_idx]
        fuente_inflacion = inflacion.fuente_texto

    ritmos = presupuesto_sugerencias.ritmos_activos(db, usuario, hoy)
    categorias_con_presupuesto, nombres_presupuestos = (
        presupuesto_sugerencias.categorias_con_presupuesto(db, usuario.id)
    )
    ids_categorias = presupuesto_sugerencias.ids_categorias_egreso(db)

    return DatosConsejos(
        hoy=hoy,
        hay_datos=True,
        moneda_principal=moneda_principal,
        datos_viejos=datos_viejos,
        cobertura=cobertura,
        ciclo_actual=ciclo_actual,
        cerrados=cerrados,
        filas=filas,
        ingresos_45=ingresos_45,
        libre_ars=libre_ars,
        saldo_hoy_ars=saldo_hoy_ars,
        inversiones_ars=inversiones_ars,
        usd_total=usd_total,
        cotizacion_compra=cotizacion_compra,
        tasa_inflacion=tasa_inflacion,
        mes_inflacion=mes_inflacion,
        fuente_inflacion=fuente_inflacion,
        billetera_inversion=billetera_inversion,
        tiene_meta_colchon=tiene_meta_colchon,
        factor_cobertura=factor_cobertura,
        ipc=ipc,
        ritmos=ritmos,
        categorias_con_presupuesto=categorias_con_presupuesto,
        nombres_presupuestos=nombres_presupuestos,
        ids_categorias=ids_categorias,
    )
