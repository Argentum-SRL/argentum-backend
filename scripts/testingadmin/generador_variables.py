"""
Módulo Generador de Gastos Variables del Día a Día, Costumbres y Temporadas.

Cumple con:
4.5 Gastos del día a día con fechas y montos irregulares:
    - Supermercado entre 4 y 8 veces por ciclo.
    - Almacén y verdulería con compras frecuentes.
    - Farmacia sin día fijo (1 a 3 veces por ciclo).
    - Cargas de SUBE.
    - Nafta cada 10 a 20 días.
4.6 Costumbres, irregulares y con montos que varían (nunca el mismo monto):
    - Delivery entre 4 y 12 veces por ciclo.
    - Café entre 8 y 20 veces por ciclo.
    - Salidas, mayormente los fines de semana.
    - Viajes en taxi o app (Cabify / Uber).
4.12 Temporadas:
    - Diciembre con más gastos (regalos, reuniones y fiestas).
    - Enero con vacaciones (gastos recreativos, hotelería, peajes).
4.13 Carga incompleta:
    - En el ciclo de mayo de 2026 (Ciclo 10) se registra solo cerca del 40%
      de los gastos del día a día y de las costumbres. Los fijos no se alteran.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda
from app.schemas.transaccion import TransaccionCreate
from app.services import transaccion_service
from app.utils.fecha import hoy_argentina
from scripts.testingadmin.datos_base import CatalogoEntidades


def generar_variables(
    db: Session,
    cat: CatalogoEntidades,
    rng: random.Random,
    fecha_corte: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Genera gastos del día a día, costumbres y estacionales a lo largo de los 14 ciclos.
    Ninguna transacción se crea con fecha posterior a fecha_corte (por defecto hoy_argentina).
    """
    if fecha_corte is None:
        fecha_corte = hoy_argentina()

    transacciones_creadas: List[Transaccion] = []

    frecuencias_costumbres = {
        "delivery": [],
        "cafe": [],
        "salidas": [],
        "taxi_app": [],
    }
    frecuencias_dia_a_dia = {
        "supermercado": [],
        "almacen_verduleria": [],
        "farmacia": [],
        "sube": [],
        "nafta": [],
    }

    for idx, ciclo in enumerate(cat.ciclos, start=1):
        ini = ciclo.fecha_inicio
        fin = ciclo.fecha_fin
        duracion = ciclo.dias
        mes = ciclo.mes_ancla
        anio = ciclo.anio_ancla

        # Factor inflacionario suave según avance en el tiempo
        factor_inflacion = Decimal("1.00") + Decimal(str(idx * 0.025))

        # Flags de temporadas y condiciones especiales
        es_diciembre = (mes == 12)
        es_enero = (mes == 1)
        # Ciclo 10 es Mayo 2026 (2026-04-30 al 2026-05-28): carga incompleta (~40%)
        es_carga_incompleta = (ciclo.numero == 10)
        factor_carga = 0.40 if es_carga_incompleta else 1.00

        # Función auxiliar para obtener fechas válidas dentro del ciclo
        def rand_fecha() -> date:
            offset = rng.randint(0, max(0, duracion - 1))
            return ini + timedelta(days=offset)

        # -------------------------------------------------------------
        # 1. Supermercado (4 a 8 veces por ciclo)
        # -------------------------------------------------------------
        cant_super = int(round(rng.randint(5, 7) * factor_carga))
        cant_super = max(2 if es_carga_incompleta else 4, cant_super)
        frecuencias_dia_a_dia["supermercado"].append({"ciclo": idx, "cantidad": cant_super})

        for _ in range(cant_super):
            f_tx = rand_fecha()
            if f_tx > fecha_corte:
                continue
            base = Decimal(str(rng.randint(38000, 78000)))
            if es_diciembre:
                base += Decimal("25000.00")  # Compras navideñas
            monto = (base * factor_inflacion).quantize(Decimal("0.01"))
            desc_sup = rng.choice([
                "Supermercado Coto Almagro",
                "Carrefour Market compra semanal",
                "Jumbo compras alimentos y hogar",
                "Disco provisiones semanales",
            ])
            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion=desc_sup,
                    categoria_id=cat.cat_alim.id,
                    subcategoria_id=cat.sub_super.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 2. Almacén y Verdulería (compras de cercanía, frecuentemente en efectivo)
        # -------------------------------------------------------------
        cant_almacen = int(round(rng.randint(6, 9) * factor_carga))
        cant_almacen = max(2 if es_carga_incompleta else 4, cant_almacen)
        frecuencias_dia_a_dia["almacen_verduleria"].append({"ciclo": idx, "cantidad": cant_almacen})

        for _ in range(cant_almacen):
            f_tx = rand_fecha()
            if f_tx > fecha_corte:
                continue
            base = Decimal(str(rng.randint(6500, 16500)))
            monto = (base * factor_inflacion).quantize(Decimal("0.01"))
            es_verdu = rng.random() > 0.5
            desc = "Verdulería y frutería de barrio" if es_verdu else "Almacén y fiambrería barrial"
            subc = cat.sub_verdu if es_verdu else cat.sub_super
            # Mayormente pagado en efectivo desde Efectivo ARS
            b_pago = cat.b_efectivo_ars if rng.random() > 0.35 else cat.b_galicia

            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion=desc,
                    categoria_id=cat.cat_alim.id,
                    subcategoria_id=subc.id,
                    metodo_pago=MetodoPago.EFECTIVO if b_pago.es_efectivo else MetodoPago.DEBITO,
                    billetera_id=b_pago.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 3. Farmacia (sin día fijo, 1 a 3 veces por ciclo)
        # -------------------------------------------------------------
        cant_farma = int(round(rng.randint(1, 3) * factor_carga))
        cant_farma = max(1, cant_farma)
        frecuencias_dia_a_dia["farmacia"].append({"ciclo": idx, "cantidad": cant_farma})

        for _ in range(cant_farma):
            f_tx = rand_fecha()
            if f_tx > fecha_corte:
                continue
            base = Decimal(str(rng.randint(8500, 24000)))
            monto = (base * factor_inflacion).quantize(Decimal("0.01"))
            desc = rng.choice(["Farmacity medicamentos y cuidado", "Farmacia de turno analgésicos", "Farmacia Central"])
            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion=desc,
                    categoria_id=cat.cat_salud.id,
                    subcategoria_id=cat.sub_farmacia.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 4. Cargas de SUBE (transporte público)
        # -------------------------------------------------------------
        cant_sube = int(round(rng.randint(4, 6) * factor_carga))
        cant_sube = max(2, cant_sube)
        frecuencias_dia_a_dia["sube"].append({"ciclo": idx, "cantidad": cant_sube})

        for _ in range(cant_sube):
            f_tx = rand_fecha()
            if f_tx > fecha_corte:
                continue
            monto = Decimal(str(rng.choice([4000, 5000, 6000, 7500])))
            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion="Carga saldo tarjeta SUBE transporte",
                    categoria_id=cat.cat_transp.id,
                    subcategoria_id=cat.sub_transp_pub.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 5. Nafta (cada 10 a 20 días: ~2 a 3 veces por ciclo)
        # -------------------------------------------------------------
        cant_nafta = int(round(rng.randint(2, 3) * factor_carga))
        cant_nafta = max(1, cant_nafta)
        frecuencias_dia_a_dia["nafta"].append({"ciclo": idx, "cantidad": cant_nafta})

        for _ in range(cant_nafta):
            f_tx = rand_fecha()
            if f_tx > fecha_corte:
                continue
            base = Decimal(str(rng.randint(38000, 52000)))
            if es_enero:
                base += Decimal("18000.00")  # Nafta en ruta por vacaciones
            monto = (base * factor_inflacion).quantize(Decimal("0.01"))
            desc = rng.choice(["YPF Infinia combustible", "Shell V-Power Nafta", "Axion Energy combustible"])
            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion=desc,
                    categoria_id=cat.cat_transp.id,
                    subcategoria_id=cat.sub_comb.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 6. Costumbres: Delivery (4 a 12 veces por ciclo)
        # -------------------------------------------------------------
        cant_deliv = int(round(rng.randint(6, 9) * factor_carga))
        cant_deliv = max(3 if es_carga_incompleta else 5, cant_deliv)
        frecuencias_costumbres["delivery"].append({"ciclo": idx, "cantidad": cant_deliv})

        for _ in range(cant_deliv):
            f_tx = rand_fecha()
            if f_tx > fecha_corte:
                continue
            base = Decimal(str(rng.randint(12500, 24500)))
            monto = (base * factor_inflacion).quantize(Decimal("0.01"))
            desc = rng.choice([
                "PedidosYa cena delivery",
                "Rappi empanadas y pizza",
                "Sushi delivery cena fin de semana",
                "Hamburguesas delivery nocturno",
            ])
            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion=desc,
                    categoria_id=cat.cat_gastro.id,
                    subcategoria_id=cat.sub_delivery.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 7. Costumbres: Café (8 a 20 veces por ciclo)
        # -------------------------------------------------------------
        cant_cafe = int(round(rng.randint(11, 16) * factor_carga))
        cant_cafe = max(4 if es_carga_incompleta else 8, cant_cafe)
        frecuencias_costumbres["cafe"].append({"ciclo": idx, "cantidad": cant_cafe})

        for _ in range(cant_cafe):
            f_tx = rand_fecha()
            if f_tx > fecha_corte:
                continue
            base = Decimal(str(rng.randint(3200, 5800)))
            monto = (base * factor_inflacion).quantize(Decimal("0.01"))
            desc = rng.choice([
                "Café de especialidad y medialuna",
                "Starbucks café americano",
                "Cafetería café al paso coworking",
                "Café con tostadas mañana",
            ])
            b_pago = cat.b_efectivo_ars if rng.random() > 0.4 else cat.b_galicia
            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion=desc,
                    categoria_id=cat.cat_gastro.id,
                    subcategoria_id=cat.sub_cafe.id,
                    metodo_pago=MetodoPago.EFECTIVO if b_pago.es_efectivo else MetodoPago.DEBITO,
                    billetera_id=b_pago.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 8. Costumbres: Salidas (mayormente fines de semana)
        # -------------------------------------------------------------
        cant_salidas = int(round(rng.randint(3, 5) * factor_carga))
        if es_diciembre:
            cant_salidas += 2  # Más salidas en diciembre
        cant_salidas = max(2, cant_salidas)
        frecuencias_costumbres["salidas"].append({"ciclo": idx, "cantidad": cant_salidas})

        for _ in range(cant_salidas):
            f_tx = rand_fecha()
            # Ajustar preferentemente a viernes (4), sábado (5) o domingo (6)
            if f_tx.weekday() < 4 and duracion > 7:
                dias_a_sumar = (4 - f_tx.weekday()) % 7
                f_candidata = f_tx + timedelta(days=dias_a_sumar)
                if f_candidata <= fin and f_candidata <= fecha_corte:
                    f_tx = f_candidata

            if f_tx > fecha_corte:
                continue

            base = Decimal(str(rng.randint(26000, 52000)))
            monto = (base * factor_inflacion).quantize(Decimal("0.01"))
            desc = rng.choice([
                "Cena con amigos en restaurante Palermo",
                "Bar cervecero y picada fin de semana",
                "Entradas de cine y pochoclos",
                "Salida teatro Corrientes",
            ])
            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion=desc,
                    categoria_id=cat.cat_recreo.id,
                    subcategoria_id=cat.sub_salidas.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 9. Costumbres: Viajes en Taxi o App (Cabify / Uber)
        # -------------------------------------------------------------
        cant_taxi = int(round(rng.randint(3, 6) * factor_carga))
        cant_taxi = max(1 if es_carga_incompleta else 2, cant_taxi)
        frecuencias_costumbres["taxi_app"].append({"ciclo": idx, "cantidad": cant_taxi})

        for _ in range(cant_taxi):
            f_tx = rand_fecha()
            if f_tx > fecha_corte:
                continue
            base = Decimal(str(rng.randint(5500, 14500)))
            monto = (base * factor_inflacion).quantize(Decimal("0.01"))
            desc = rng.choice([
                "Cabify viaje ida reunión",
                "Uber viaje de regreso noche",
                "Taxi radio viaje centro",
            ])
            tx = transaccion_service.crear_transaccion(
                db, cat.user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=monto,
                    moneda=Moneda.ARS,
                    fecha=f_tx,
                    descripcion=desc,
                    categoria_id=cat.cat_transp.id,
                    subcategoria_id=cat.sub_taxi.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    es_recurrente=False,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
                commit=False,
            )
            transacciones_creadas.append(tx)

        # -------------------------------------------------------------
        # 10. Temporadas Específicas: Diciembre y Enero
        # -------------------------------------------------------------
        if es_diciembre:
            # Regalos de navidad y fin de año
            f_regalos = date(anio, 12, rng.randint(20, 23))
            if f_regalos <= fecha_corte:
                m_regalos = (Decimal("115000.00") * factor_inflacion).quantize(Decimal("0.01"))
                tx_reg = transaccion_service.crear_transaccion(
                    db, cat.user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_regalos,
                        moneda=Moneda.ARS,
                        fecha=f_regalos,
                        descripcion="Regalos navideños y de fin de año familia",
                        categoria_id=cat.cat_otros_egr.id,
                        subcategoria_id=cat.sub_cuidado.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=cat.b_galicia.id,
                        es_recurrente=False,
                        origen=OrigenTransaccion.MANUAL,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    ),
                    commit=False,
                )
                transacciones_creadas.append(tx_reg)

        if es_enero:
            # Gastos de vacaciones
            f_vac = date(anio, 1, rng.randint(10, 22))
            if f_vac <= fecha_corte:
                m_vac = (Decimal("185000.00") * factor_inflacion).quantize(Decimal("0.01"))
                tx_vac = transaccion_service.crear_transaccion(
                    db, cat.user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_vac,
                        moneda=Moneda.ARS,
                        fecha=f_vac,
                        descripcion="Alojamiento y actividades vacaciones de verano",
                        categoria_id=cat.cat_recreo.id,
                        subcategoria_id=cat.sub_salidas.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=cat.b_galicia.id,
                        es_recurrente=False,
                        origen=OrigenTransaccion.MANUAL,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    ),
                    commit=False,
                )
                transacciones_creadas.append(tx_vac)

    db.flush()

    return {
        "transacciones": transacciones_creadas,
        "verdad_variables": {
            "gastos_dia_a_dia": {
                "supermercado_frecuencia_media": sum(x["cantidad"] for x in frecuencias_dia_a_dia["supermercado"]) / len(cat.ciclos),
                "almacen_frecuencia_media": sum(x["cantidad"] for x in frecuencias_dia_a_dia["almacen_verduleria"]) / len(cat.ciclos),
                "farmacia_frecuencia_media": sum(x["cantidad"] for x in frecuencias_dia_a_dia["farmacia"]) / len(cat.ciclos),
                "sube_frecuencia_media": sum(x["cantidad"] for x in frecuencias_dia_a_dia["sube"]) / len(cat.ciclos),
                "nafta_frecuencia_media": sum(x["cantidad"] for x in frecuencias_dia_a_dia["nafta"]) / len(cat.ciclos),
            },
            "costumbres": {
                "delivery_frecuencia_media": sum(x["cantidad"] for x in frecuencias_costumbres["delivery"]) / len(cat.ciclos),
                "cafe_frecuencia_media": sum(x["cantidad"] for x in frecuencias_costumbres["cafe"]) / len(cat.ciclos),
                "salidas_frecuencia_media": sum(x["cantidad"] for x in frecuencias_costumbres["salidas"]) / len(cat.ciclos),
                "taxi_app_frecuencia_media": sum(x["cantidad"] for x in frecuencias_costumbres["taxi_app"]) / len(cat.ciclos),
            },
            "ciclo_carga_incompleta": {
                "numero_ciclo": 10,
                "periodo": "Mayo 2026",
                "porcentaje_registrado": "40%",
            },
            "temporadas": {
                "diciembre": "Mayor gasto en salidas, regalos navideños y fin de año",
                "enero": "Vacaciones con alojamiento y combustible",
            },
        },
    }
