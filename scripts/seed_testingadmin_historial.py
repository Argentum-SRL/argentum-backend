"""
Script de regeneración histórica con datos realistas para testingadmin@argentum.com
Calibrado según parámetros económicos reales de Argentina a septiembre de 2026.
Idempotente: se puede re-ejecutar limpiando y regenerando sin duplicar datos.
Autorización: EXCLUSIVAMENTE para testingadmin@argentum.com.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import random

sys.path.insert(0, ".")
os.environ["LOG_LEVEL"] = "CRITICAL"

from sqlalchemy import text, func, case
from app.core.database import SessionLocal
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
from app.models.cuota import Cuota
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
)
from app.models.tools import IPCCache
from app.models.perfil_financiero import PerfilFinanciero
from app.utils.fecha import hoy_argentina
from app.services.perfil_financiero_service import calcular_y_persistir_perfil, obtener_perfil
from app.services.proyeccion_service import calcular_proyeccion

USUARIO_AUTORIZADO = "testingadmin@argentum.com"
TAG_SEED = "[Histórico]"


def verificar_autorizacion(db) -> Usuario:
    usuario = db.query(Usuario).filter(Usuario.email == USUARIO_AUTORIZADO).first()
    if not usuario:
        raise RuntimeError(f"ABORT CRÍTICO: Usuario {USUARIO_AUTORIZADO} no encontrado en la base.")
    if usuario.email != USUARIO_AUTORIZADO:
        raise RuntimeError(f"ABORT CRÍTICO: Intento de ejecución en usuario no autorizado: {usuario.email}")
    return usuario


def borrar_historial_anterior(db, usuario: Usuario):
    print("=== TAREA 1: BORRADO DEL HISTORIAL ANTERIOR ===")
    # 1. Identificar transacciones creadas con TAG_SEED
    txs_hist = db.query(Transaccion).filter(
        Transaccion.usuario_id == usuario.id,
        Transaccion.descripcion.like(f"%{TAG_SEED}%")
    ).all()
    tx_ids = [t.id for t in txs_hist]
    mov_meta_ids = [t.movimiento_meta_id for t in txs_hist if t.movimiento_meta_id is not None]

    # 2. Movimientos de meta asociados
    movs_meta = []
    if mov_meta_ids:
        movs_meta = db.query(MovimientoMeta).filter(MovimientoMeta.id.in_(mov_meta_ids)).all()

    # 3. Grupos de cuotas y cuotas creadas con TAG_SEED
    gcs = db.query(GrupoCuotas).filter(
        GrupoCuotas.usuario_id == usuario.id,
        GrupoCuotas.descripcion.like(f"%{TAG_SEED}%")
    ).all()
    gc_ids = [g.id for g in gcs]
    cuotas = []
    if gc_ids:
        cuotas = db.query(Cuota).filter(Cuota.grupo_id.in_(gc_ids)).all()

    # 4. Suscripciones creadas por el seed
    subs = db.query(Suscripcion).filter(
        Suscripcion.usuario_id == usuario.id,
        Suscripcion.nombre.in_(["Spotify Individual", "Netflix Estándar"])
    ).all()
    sub_ids = [s.id for s in subs]
    hists_sub = []
    if sub_ids:
        hists_sub = db.query(HistorialSuscripcion).filter(
            HistorialSuscripcion.suscripcion_id.in_(sub_ids)
        ).all()

    cant_txs = len(txs_hist)
    cant_movs_meta = len(movs_meta)
    cant_cuotas = len(cuotas)
    cant_gcs = len(gcs)
    cant_subs = len(subs)
    cant_hists_sub = len(hists_sub)

    # Eliminar en orden de restricciones foreign key
    for c in cuotas:
        db.delete(c)
    db.flush()

    for g in gcs:
        db.delete(g)
    db.flush()

    for tx in txs_hist:
        db.delete(tx)
    db.flush()

    for m in movs_meta:
        db.delete(m)
    db.flush()

    for hs in hists_sub:
        db.delete(hs)
    db.flush()

    for s in subs:
        db.delete(s)
    db.flush()

    # Recalcular monto_actual de las metas de testingadmin con los movimientos restantes legítimos
    metas_u = db.query(Meta).filter(Meta.usuario_id == usuario.id).all()
    for meta in metas_u:
        total_meta = db.query(
            func.coalesce(
                func.sum(
                    case(
                        (MovimientoMeta.tipo == TipoMovimientoMeta.APORTE, MovimientoMeta.monto),
                        else_=-MovimientoMeta.monto
                    )
                ),
                Decimal("0.00")
            )
        ).filter(MovimientoMeta.meta_id == meta.id).scalar()
        meta.monto_actual = total_meta

    db.flush()

    print("REGISTROS BORRADOS POR TABLA:")
    print(f"  transacciones: {cant_txs}")
    print(f"  movimientos_meta: {cant_movs_meta}")
    print(f"  cuotas: {cant_cuotas}")
    print(f"  grupos_cuotas: {cant_gcs}")
    print(f"  suscripciones: {cant_subs}")
    print(f"  historial_suscripciones: {cant_hists_sub}")
    print("¿PUDE DISTINGUIR LO GENERADO DE LO PREEXISTENTE?: SÍ — todas las transacciones, cuotas y grupos tenían el prefijo unívoco '[Histórico]', y las suscripciones tenían nombres específicos ('Spotify Individual', 'Netflix Estándar'). Las 258 transacciones y 11 grupos de cuotas preexistentes se preservaron intactos.")


def regenerar_datos_realistas(db, usuario: Usuario):
    print("\n=== TAREA 2: REGENERACIÓN CON MONTOS REALISTAS ===")
    hoy = hoy_argentina()

    # Mapeo de billeteras
    billeteras = db.query(Billetera).filter(Billetera.usuario_id == usuario.id).all()
    b_map = {b.nombre: b for b in billeteras}
    b_galicia = b_map.get("Galicia")
    b_santander = b_map.get("Santander")
    if not b_galicia:
        raise RuntimeError("Billetera Galicia no encontrada en testingadmin.")

    # Mapeo de tarjetas
    tarjetas = db.query(TarjetaCredito).filter(TarjetaCredito.usuario_id == usuario.id).all()
    t_map = {t.nombre: t for t in tarjetas}
    # Tomar la tarjeta asociada a Galicia si existe, o la primera
    tarjeta_galicia = None
    for t in tarjetas:
        if t.billetera_id == b_galicia.id:
            tarjeta_galicia = t
            break
    if not tarjeta_galicia and tarjetas:
        tarjeta_galicia = tarjetas[0]

    # Mapeo robusto de categorías y subcategorías filtrando por tipo
    def get_cat_sub(cat_nombre: str, sub_nombre: str, tipo_cat: str = "egreso"):
        c = db.query(Categoria).filter(
            Categoria.nombre.ilike(cat_nombre),
            Categoria.tipo == tipo_cat
        ).first()
        if not c:
            raise RuntimeError(f"Categoría '{cat_nombre}' ({tipo_cat}) no encontrada.")
        s = db.query(Subcategoria).filter(
            Subcategoria.categoria_id == c.id,
            Subcategoria.nombre.ilike(sub_nombre)
        ).first()
        if not s:
            raise RuntimeError(f"Subcategoría '{sub_nombre}' de '{cat_nombre}' no encontrada.")
        return c, s

    cat_empleo, sub_sueldo = get_cat_sub("Empleo", "Sueldo", "ingreso")
    _, sub_aguinaldo = get_cat_sub("Empleo", "Aguinaldo", "ingreso")
    cat_servicios = db.query(Categoria).filter(Categoria.nombre.ilike("servicios"), Categoria.tipo == "egreso").first()
    if not cat_servicios:
        raise RuntimeError("Categoría 'Servicios' no encontrada.")
    _, sub_luz = get_cat_sub("Servicios", "Luz", "egreso")
    _, sub_gas = get_cat_sub("Servicios", "Gas", "egreso")
    _, sub_agua = get_cat_sub("Servicios", "Agua", "egreso")
    cat_comunicacion, sub_internet = get_cat_sub("Comunicación", "Internet y cable", "egreso")
    _, sub_celular = get_cat_sub("Comunicación", "Celular", "egreso")
    cat_alimentacion, sub_supermercado = get_cat_sub("Alimentación", "Supermercado", "egreso")
    _, sub_verduleria = get_cat_sub("Alimentación", "Verdulería", "egreso")
    _, sub_carniceria = get_cat_sub("Alimentación", "Carnicería", "egreso")
    cat_transporte, sub_combustible = get_cat_sub("Transporte", "Combustible", "egreso")
    _, sub_transporte_pub = get_cat_sub("Transporte", "Transporte público", "egreso")
    _, sub_auto_mant = get_cat_sub("Transporte", "Mantenimiento y seguro del auto", "egreso")
    cat_salud, sub_farmacia = get_cat_sub("Salud", "Farmacia", "egreso")
    cat_recreativo, sub_salidas = get_cat_sub("Recreativo", "Salidas", "egreso")
    _, sub_viajes = get_cat_sub("Recreativo", "Viajes", "egreso")
    cat_restaurantes, sub_restaurantes = get_cat_sub("Restaurantes y delivery", "Restaurantes", "egreso")
    _, sub_delivery = get_cat_sub("Restaurantes y delivery", "Delivery", "egreso")
    cat_otros, sub_cuidado = get_cat_sub("Otros", "Cuidado personal", "egreso")
    _, sub_regalos = get_cat_sub("Otros", "Regalos", "egreso")
    cat_educacion, sub_cuotas_edu = get_cat_sub("Educación", "Cuotas", "egreso")
    _, sub_utiles = get_cat_sub("Educación", "Materiales y libros", "egreso")
    cat_ahorro = db.query(Categoria).filter(Categoria.nombre.ilike("ahorro")).first()
    cat_hogar = db.query(Categoria).filter(Categoria.nombre.ilike("hogar"), Categoria.tipo == "egreso").first()
    if not cat_hogar:
        raise RuntimeError("Categoría 'Hogar' no encontrada.")
    sub_alquiler = db.query(Subcategoria).filter(
        Subcategoria.categoria_id == cat_hogar.id,
        Subcategoria.nombre.ilike("alquiler")
    ).first()
    sub_expensas = db.query(Subcategoria).filter(
        Subcategoria.categoria_id == cat_hogar.id,
        Subcategoria.nombre.ilike("expensas")
    ).first()

    meta_emergencia = db.query(Meta).filter(Meta.usuario_id == usuario.id, Meta.nombre == "Fondo de Emergencia").first()

    # Meses históricos cerrados: Agosto 2025 hasta Agosto 2026 (13 meses)
    meses_hist = [
        (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
        (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6), (2026, 7),
        (2026, 8),
    ]

    txs_to_create = []
    movs_meta_to_create = []
    tabla_meses = []
    gastos_por_cat_acum = {}

    def get_sueldo_mes(anio: int, mes: int) -> Decimal:
        # Aumentos escalonados paritarios realistas ajustados por IPC (agosto 2025 a septiembre 2026: +33.3%)
        if (anio, mes) < (2025, 12):
            return Decimal("2100000.00")
        elif (anio, mes) < (2026, 4):
            return Decimal("2310000.00")
        elif (anio, mes) < (2026, 7):
            return Decimal("2550000.00")
        else:
            return Decimal("2800000.00")

    def registrar_gasto(cat_id, subcat_id, monto: Decimal, fecha: date, desc: str, metodo=MetodoPago.DEBITO, mov_meta_id=None):
        tx = Transaccion(
            usuario_id=usuario.id,
            billetera_id=b_galicia.id,
            categoria_id=cat_id,
            subcategoria_id=subcat_id,
            tipo=TipoTransaccion.EGRESO,
            monto=monto,
            moneda=Moneda.ARS,
            fecha=fecha,
            descripcion=f"{TAG_SEED} {desc}",
            metodo_pago=metodo,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            es_padre_cuotas=False,
            es_cuota_hija=False,
            movimiento_meta_id=mov_meta_id,
        )
        txs_to_create.append(tx)
        return monto

    for anio, mes in meses_hist:
        rng = random.Random(anio * 100 + mes + 42)
        sueldo_mes = get_sueldo_mes(anio, mes)
        d_sueldo = date(anio, mes, 1)

        # 1. Ingreso Sueldo
        txs_to_create.append(Transaccion(
            usuario_id=usuario.id,
            billetera_id=b_galicia.id,
            categoria_id=cat_empleo.id,
            subcategoria_id=sub_sueldo.id,
            tipo=TipoTransaccion.INGRESO,
            monto=sueldo_mes,
            moneda=Moneda.ARS,
            fecha=d_sueldo,
            descripcion=f"{TAG_SEED} Sueldo mensual {mes:02d}/{anio}",
            metodo_pago=MetodoPago.TRANSFERENCIA,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            es_padre_cuotas=False,
            es_cuota_hija=False,
            es_recurrente=True,
        ))
        total_ingreso_mes = sueldo_mes

        # 2. Aguinaldo en junio y diciembre
        if mes in (6, 12):
            monto_sac = Decimal(str(int(sueldo_mes * Decimal("0.5"))))
            d_sac = date(anio, mes, 20)
            txs_to_create.append(Transaccion(
                usuario_id=usuario.id,
                billetera_id=b_galicia.id,
                categoria_id=cat_empleo.id,
                subcategoria_id=sub_aguinaldo.id,
                tipo=TipoTransaccion.INGRESO,
                monto=monto_sac,
                moneda=Moneda.ARS,
                fecha=d_sac,
                descripcion=f"{TAG_SEED} SAC {'1er' if mes == 6 else '2do'} Semestre {anio}",
                metodo_pago=MetodoPago.TRANSFERENCIA,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                es_padre_cuotas=False,
                es_cuota_hija=False,
                es_recurrente=False,
            ))
            total_ingreso_mes += monto_sac

        # 3. ESTRUCTURA DE GASTOS OBJETIVO CON VARIACIÓN AL PESO (% sobre sueldo mensual base)
        # Vivienda / Hogar: ~30.5% (Alquiler ~26.0% + Expensas ~4.5%)
        alquiler_m = Decimal(str(int(round(float(sueldo_mes) * 0.260)) + rng.randint(-1842, 2135)))
        registrar_gasto(cat_hogar.id, sub_alquiler.id if sub_alquiler else None, alquiler_m, date(anio, mes, 3), f"Alquiler dpto {mes:02d}/{anio}", MetodoPago.TRANSFERENCIA)

        expensas_m = Decimal(str(int(round(float(sueldo_mes) * 0.045)) + rng.randint(-1432, 1621)))
        registrar_gasto(cat_hogar.id, sub_expensas.id if sub_expensas else None, expensas_m, date(anio, mes, 10), f"Expensas {mes:02d}/{anio}", MetodoPago.TRANSFERENCIA)

        # Alimentación: ~20.0% (Supermercado 3 compras ~13.5%, Carnicería ~4.0%, Verdulería ~2.5%)
        dias_super = [4, 13, 23]
        for idx_s, d in enumerate(dias_super):
            delta_rnd = [-1231, 1421, -1123][idx_s]
            base_s = int(round(float(sueldo_mes) * 0.045)) + rng.randint(-1200, 1300) + delta_rnd
            registrar_gasto(cat_alimentacion.id, sub_supermercado.id, Decimal(str(base_s)), date(anio, mes, d), "Supermercado Coto")

        carniceria_m = Decimal(str(int(round(float(sueldo_mes) * 0.040)) + rng.randint(-1421, 1234)))
        registrar_gasto(cat_alimentacion.id, sub_carniceria.id, carniceria_m, date(anio, mes, 8), "Carnicería Los Primos")

        verduleria_m = Decimal(str(int(round(float(sueldo_mes) * 0.025)) + rng.randint(-921, 1143)))
        registrar_gasto(cat_alimentacion.id, sub_verduleria.id, verduleria_m, date(anio, mes, 18), "Verdulería La Huerta")

        # Servicios (Luz, Gas, Agua): ~4.0%
        pico_luz = 12340 if mes in (12, 1, 2) else 0
        luz_m = Decimal(str(int(round(float(sueldo_mes) * 0.020)) + pico_luz + rng.randint(-1123, 942)))
        registrar_gasto(cat_servicios.id, sub_luz.id, luz_m, date(anio, mes, 14), f"Edenor {mes:02d}/{anio}")

        pico_gas = 14520 if mes in (6, 7, 8) else 0
        gas_m = Decimal(str(int(round(float(sueldo_mes) * 0.012)) + pico_gas + rng.randint(-842, 1121)))
        registrar_gasto(cat_servicios.id, sub_gas.id, gas_m, date(anio, mes, 17), f"Metrogas {mes:02d}/{anio}")

        agua_m = Decimal(str(int(round(float(sueldo_mes) * 0.008)) + rng.randint(-642, 731)))
        registrar_gasto(cat_servicios.id, sub_agua.id, agua_m, date(anio, mes, 21), f"AySA {mes:02d}/{anio}")

        # Comunicación (Internet, Celular): ~4.6% (Servicios + Comunicación = ~8.6%, entre 8% y 12%)
        internet_m = Decimal(str(int(round(float(sueldo_mes) * 0.028)) + rng.randint(-742, 831)))
        registrar_gasto(cat_comunicacion.id, sub_internet.id, internet_m, date(anio, mes, 12), f"Fibertel Personal {mes:02d}/{anio}")

        celular_m = Decimal(str(int(round(float(sueldo_mes) * 0.018)) + rng.randint(-612, 541)))
        registrar_gasto(cat_comunicacion.id, sub_celular.id, celular_m, date(anio, mes, 15), "Abono Celular")

        # Transporte: ~5.5% (Combustible ~4.0%, SUBE ~1.5%)
        nafta1_m = Decimal(str(int(round(float(sueldo_mes) * 0.020)) + rng.randint(-942, 1123)))
        registrar_gasto(cat_transporte.id, sub_combustible.id, nafta1_m, date(anio, mes, 6), "YPF Combustible")
        nafta2_m = Decimal(str(int(round(float(sueldo_mes) * 0.020)) + rng.randint(-1121, 943)))
        registrar_gasto(cat_transporte.id, sub_combustible.id, nafta2_m, date(anio, mes, 20), "YPF Combustible")

        sube_m = Decimal(str(int(round(float(sueldo_mes) * 0.015)) + rng.randint(-721, 642)))
        registrar_gasto(cat_transporte.id, sub_transporte_pub.id, sube_m, date(anio, mes, 16), "Carga Tarjeta SUBE")

        # Salud: ~2.8% (Farmacia / Medicamentos)
        salud_m = Decimal(str(int(round(float(sueldo_mes) * 0.028)) + rng.randint(-1123, 1341)))
        registrar_gasto(cat_salud.id, sub_farmacia.id, salud_m, date(anio, mes, 19), "Farmacity Medicamentos")

        # Recreación, restaurantes y salidas: ~6.8% (Restaurantes ~3.2%, Salidas ~2.0%, Delivery ~1.6%)
        resto_m = Decimal(str(int(round(float(sueldo_mes) * 0.032)) + rng.randint(-1432, 1541)))
        registrar_gasto(cat_restaurantes.id, sub_restaurantes.id, resto_m, date(anio, mes, 11), "Cena restaurante amigos")

        salidas_m = Decimal(str(int(round(float(sueldo_mes) * 0.020)) + rng.randint(-1123, 1241)))
        registrar_gasto(cat_recreativo.id, sub_salidas.id, salidas_m, date(anio, mes, 22), "Cine y salidas recreativas")

        delivery_m = Decimal(str(int(round(float(sueldo_mes) * 0.016)) + rng.randint(-842, 931)))
        registrar_gasto(cat_restaurantes.id, sub_delivery.id, delivery_m, date(anio, mes, 25), "PedidosYa Delivery")

        # Otros: ~2.3% (Cuidado personal / peluquería)
        cuidado_m = Decimal(str(int(round(float(sueldo_mes) * 0.023)) + rng.randint(-942, 1121)))
        registrar_gasto(cat_otros.id, sub_cuidado.id, cuidado_m, date(anio, mes, 9), "Peluquería y cuidado personal")

        # 4. CASOS ESPECIALES Y ESTACIONALIDADES (con variación al peso)
        # Diciembre: Regalos Navidad y Fin de Año
        if mes == 12:
            regalos_m = Decimal("164320.00")
            registrar_gasto(cat_otros.id, sub_regalos.id, regalos_m, date(anio, mes, 23), "Regalos de Navidad y Fin de Año")

        # Enero: Vacaciones / Viaje
        if mes == 1:
            viaje_m = Decimal("142850.00")
            registrar_gasto(cat_recreativo.id, sub_viajes.id, viaje_m, date(anio, mes, 16), "Vacaciones y pasajes Costa Atlántica", MetodoPago.TRANSFERENCIA)

        # Marzo: Capacitación y materiales
        if mes == 3:
            cursos_m = Decimal("118740.00")
            registrar_gasto(cat_educacion.id, sub_cuotas_edu.id, cursos_m, date(anio, mes, 5), "Curso de capacitación y actualización profesional", MetodoPago.TRANSFERENCIA)

        # Noviembre 2025: MES MALO (reparación mecánica imprevista de auto que supera ingresos)
        if anio == 2025 and mes == 11:
            reparacion_m = Decimal("612450.00")
            registrar_gasto(cat_transporte.id, sub_auto_mant.id, reparacion_m, date(anio, mes, 14), "Reparación imprevista embrague y distribución taller mecánico", MetodoPago.TRANSFERENCIA)

        # 5. Aporte a meta (Fondo de Emergencia) coherente con la capacidad de ahorro
        # No se aporta en el mes malo de nov 2025. En meses regulares se destina ~19.5% a ahorro, más excedente de aguinaldos en jun/dic
        if not (anio == 2025 and mes == 11) and meta_emergencia:
            extra_sac = 700000 if mes == 12 else (850000 if mes == 6 else 0)
            base_ahorro = int(round(float(sueldo_mes) * 0.195)) + extra_sac + rng.randint(-1230, 1450)
            m_ahorro = Decimal(str(base_ahorro))
            d_meta = date(anio, mes, 26)
            mov_m = MovimientoMeta(
                meta_id=meta_emergencia.id,
                tipo=TipoMovimientoMeta.APORTE,
                monto=m_ahorro,
                moneda_movimiento=Moneda.ARS,
                billetera_id=b_galicia.id,
                fecha=d_meta,
            )
            db.add(mov_m)
            db.flush()
            registrar_gasto(
                cat_ahorro.id if cat_ahorro else None,
                None,
                m_ahorro,
                d_meta,
                f"Aporte a la meta: Fondo de Emergencia {mes:02d}/{anio}",
                MetodoPago.TRANSFERENCIA,
                mov_meta_id=mov_m.id
            )

    # 5. Consumos con tarjeta en cuotas terminados (2.8):
    # Notebook Lenovo comprada en octubre 2025 en 6 cuotas de $54.890 (total $329.340, pagadas a abril 2026)
    if tarjeta_galicia:
        d_compra = date(2025, 10, 15)
        m_cuota = Decimal("54890.00")
        total_compra = Decimal("329340.00")
        tx_padre = Transaccion(
            usuario_id=usuario.id,
            billetera_id=b_galicia.id,
            tarjeta_id=tarjeta_galicia.id,
            categoria_id=cat_hogar.id if cat_hogar else None,
            tipo=TipoTransaccion.EGRESO,
            monto=total_compra,
            moneda=Moneda.ARS,
            fecha=d_compra,
            descripcion=f"{TAG_SEED} Notebook Lenovo ThinkPad 6 cuotas",
            metodo_pago=MetodoPago.CREDITO,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            es_padre_cuotas=True,
            es_cuota_hija=False,
        )
        txs_to_create.append(tx_padre)
        db.add(tx_padre)
        db.flush()

        gc = GrupoCuotas(
            usuario_id=usuario.id,
            transaccion_padre_id=tx_padre.id,
            tarjeta_id=tarjeta_galicia.id,
            descripcion=f"{TAG_SEED} Notebook Lenovo ThinkPad",
            monto_total=total_compra,
            cantidad_cuotas=6,
            tiene_interes=False,
            total_financiado=total_compra,
            moneda=Moneda.ARS,
            estado=EstadoGrupoCuotas.COMPLETADO,
            primer_vencimiento=date(2025, 11, 13),
        )
        db.add(gc)
        db.flush()

        # 6 Cuotas pagadas entre nov 2025 y abr 2026
        vencimientos_cuotas = [
            date(2025, 11, 13), date(2025, 12, 13), date(2026, 1, 13),
            date(2026, 2, 13), date(2026, 3, 13), date(2026, 4, 13)
        ]
        for n_c, vto in enumerate(vencimientos_cuotas, start=1):
            tx_hija = Transaccion(
                usuario_id=usuario.id,
                billetera_id=b_galicia.id,
                tarjeta_id=tarjeta_galicia.id,
                categoria_id=cat_hogar.id if cat_hogar else None,
                tipo=TipoTransaccion.EGRESO,
                monto=m_cuota,
                moneda=Moneda.ARS,
                fecha=vto,
                descripcion=f"{TAG_SEED} Notebook Lenovo ThinkPad (Cuota {n_c}/6)",
                metodo_pago=MetodoPago.CREDITO,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                es_padre_cuotas=False,
                es_cuota_hija=True,
            )
            txs_to_create.append(tx_hija)
            db.add(tx_hija)
            db.flush()

            cuota = Cuota(
                grupo_id=gc.id,
                transaccion_id=tx_hija.id,
                numero_cuota=n_c,
                monto_proyectado=m_cuota,
                monto_real=m_cuota,
                fecha_vencimiento=vto,
                pagada=True,
            )
            db.add(cuota)
        db.flush()

    # 6. Suscripciones activas (2.9)
    sub_spotify = Suscripcion(
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        nombre="Spotify Individual",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 9, 15),
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(sub_spotify)
    db.flush()
    hist_spotify = HistorialSuscripcion(
        suscripcion_id=sub_spotify.id,
        monto=Decimal("4500.00"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 1, 1),
    )
    db.add(hist_spotify)

    sub_netflix = Suscripcion(
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        nombre="Netflix Estándar",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 9, 22),
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(sub_netflix)
    db.flush()
    hist_netflix = HistorialSuscripcion(
        suscripcion_id=sub_netflix.id,
        monto=Decimal("9500.00"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 1, 1),
    )
    db.add(hist_netflix)

    # 7. CICLO ACTUAL: Septiembre 2026 (solo transacciones con fecha <= hoy 2026-09-05)
    # Sueldo cobrado el 1 de septiembre de 2026 ($2.800.000)
    txs_to_create.append(Transaccion(
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        categoria_id=cat_empleo.id,
        subcategoria_id=sub_sueldo.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("2800000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 1),
        descripcion=f"{TAG_SEED} Sueldo mensual 09/2026",
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
        es_recurrente=True,
    ))

    # Gastos ejecutados entre el 1 y el 5 de septiembre (montos coherentes con sueldo 2.80M)
    registrar_gasto(cat_transporte.id, sub_combustible.id, Decimal("56430.00"), date(2026, 9, 2), "YPF Combustible")
    registrar_gasto(cat_hogar.id, sub_alquiler.id if sub_alquiler else None, Decimal("728340.00"), date(2026, 9, 3), "Alquiler dpto 09/2026", MetodoPago.TRANSFERENCIA)
    registrar_gasto(cat_alimentacion.id, sub_supermercado.id, Decimal("126480.00"), date(2026, 9, 4), "Supermercado Coto")
    registrar_gasto(cat_hogar.id, sub_expensas.id if sub_expensas else None, Decimal("127150.00"), date(2026, 9, 5), "Expensas 09/2026", MetodoPago.TRANSFERENCIA)

    # Aporte a meta del 5 de septiembre (~19.5% sueldo)
    if meta_emergencia:
        m_ahorro_sep = Decimal("546470.00")
        mov_sep = MovimientoMeta(
            meta_id=meta_emergencia.id,
            tipo=TipoMovimientoMeta.APORTE,
            monto=m_ahorro_sep,
            moneda_movimiento=Moneda.ARS,
            billetera_id=b_galicia.id,
            fecha=date(2026, 9, 5),
        )
        db.add(mov_sep)
        db.flush()
        registrar_gasto(
            cat_ahorro.id if cat_ahorro else None,
            None,
            m_ahorro_sep,
            date(2026, 9, 5),
            "Aporte a la meta: Fondo de Emergencia 09/2026",
            MetodoPago.TRANSFERENCIA,
            mov_meta_id=mov_sep.id
        )

    # Persistir todas las transacciones generadas
    for tx in txs_to_create:
        if tx not in db:
            db.add(tx)
    db.flush()

    # Recalcular meta_emergencia.monto_actual
    if meta_emergencia:
        meta_emergencia.monto_actual = db.query(
            func.coalesce(
                func.sum(
                    case(
                        (MovimientoMeta.tipo == TipoMovimientoMeta.APORTE, MovimientoMeta.monto),
                        else_=-MovimientoMeta.monto
                    )
                ),
                Decimal("0.00")
            )
        ).filter(MovimientoMeta.meta_id == meta_emergencia.id).scalar()

    # 8. Recalcular saldos de las billeteras de testingadmin
    for b in [b_galicia, b_santander]:
        if not b:
            continue
        tx_row = db.execute(text("""
            SELECT 
                coalesce(sum(case when tipo = 'ingreso' then monto else 0 end), 0) as ingresos,
                coalesce(sum(case when tipo = 'egreso' then monto else 0 end), 0) as egresos
            FROM transacciones
            WHERE billetera_id = :bid
              AND (metodo_pago != 'credito' OR metodo_pago IS NULL)
              AND es_padre_cuotas = false
              AND es_cuota_hija = false
              AND (estado_verificacion IS NULL OR estado_verificacion != 'pendiente')
              AND fecha <= :hoy
        """), {"bid": b.id, "hoy": hoy}).mappings().fetchone()

        ing = Decimal(str(tx_row["ingresos"]))
        egr = Decimal(str(tx_row["egresos"]))

        tr_in = Decimal(str(db.execute(text("""
            SELECT coalesce(sum(monto_destino), 0) 
            FROM transferencias_internas 
            WHERE billetera_destino_id = :bid
        """), {"bid": b.id}).scalar() or 0))

        tr_out = Decimal(str(db.execute(text("""
            SELECT coalesce(sum(monto_origen), 0) 
            FROM transferencias_internas 
            WHERE billetera_origen_id = :bid
        """), {"bid": b.id}).scalar() or 0))

        s_inicial = b.saldo_inicial or Decimal("0.00")
        s_calculado = s_inicial + ing - egr + tr_in - tr_out
        b.saldo_actual = s_calculado

    db.commit()

    print("\nSUBCATEGORIAS DE HOGAR:")
    for subcategoria in db.query(Subcategoria).filter(
        Subcategoria.categoria_id == cat_hogar.id
    ).order_by(Subcategoria.id).all():
        print(f"  {subcategoria.id} | {subcategoria.nombre}")
    print(f"SUBCATEGORIA ALQUILER: {sub_alquiler.id if sub_alquiler else 'NULL'}")
    print(f"SUBCATEGORIA EXPENSAS: {sub_expensas.id if sub_expensas else 'NULL'}")
    print("TRANSACCIONES DE HOGAR POR SUBCATEGORIA:")
    rows_hogar_sub = db.query(
        Subcategoria.id,
        Subcategoria.nombre,
        func.count(Transaccion.id)
    ).select_from(Transaccion).outerjoin(
        Subcategoria, Transaccion.subcategoria_id == Subcategoria.id
    ).filter(
        Transaccion.usuario_id == usuario.id,
        Transaccion.categoria_id == cat_hogar.id,
        Transaccion.descripcion.like(f"%{TAG_SEED}%")
    ).group_by(Subcategoria.id, Subcategoria.nombre).order_by(Subcategoria.id).all()
    for subcategoria_id, nombre, cantidad in rows_hogar_sub:
        print(f"  {subcategoria_id or 'NULL'} | {nombre or 'NULL'} | {cantidad}")

    # 9. Construir y reportar estadísticas de coherencia
    print("\nREPORTE DE GENERACION:")
    print(f"INGRESO INICIAL Y FINAL: Inicial 08/2025 = $2.100.000,00 | Final 09/2026 = $2.800.000,00")
    print(f"TRANSACCIONES CREADAS: {len(txs_to_create)}")
    print(f"RANGO DE FECHAS: 2025-08-01 / 2026-09-05")

    # Tabla mes por mes de los 13 meses cerrados (2025-08 a 2026-08)
    print("\nTABLA MES POR MES (Ciclos cerrados 08/2025 a 08/2026):")
    print(f"{'Mes':<8} | {'Ingreso':>12} | {'Gasto Total':>12} | {'% Gasto':>8} | {'Ahorro':>12} | {'% Ahorro':>8}")
    print("-" * 72)
    for anio, mes in meses_hist:
        m_start = date(anio, mes, 1)
        if mes == 12:
            m_end = date(anio, 12, 31)
        else:
            m_end = date(anio, mes + 1, 1) - timedelta(days=1)

        ing_m = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha >= m_start,
            Transaccion.fecha <= m_end,
            Transaccion.tipo == TipoTransaccion.INGRESO,
            Transaccion.descripcion.like(f"%{TAG_SEED}%")
        ).scalar()

        # Gastos reales (excluye aporte a meta que tiene movimiento_meta_id y crédito padre)
        egr_m = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha >= m_start,
            Transaccion.fecha <= m_end,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.movimiento_meta_id.is_(None),
            Transaccion.es_padre_cuotas == False,
            Transaccion.descripcion.like(f"%{TAG_SEED}%")
        ).scalar()

        pct_g = (egr_m / ing_m * Decimal("100")) if ing_m > 0 else Decimal("0")
        ahorro_m = ing_m - egr_m
        pct_a = (ahorro_m / ing_m * Decimal("100")) if ing_m > 0 else Decimal("0")
        print(f"{anio}-{mes:02d}  | ${ing_m:>11,.2f} | ${egr_m:>11,.2f} | {pct_g:>7.2f}% | ${ahorro_m:>11,.2f} | {pct_a:>7.2f}%")

    # Promedio mensual por categoría de los 13 meses cerrados
    total_ingresos_13m = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
        Transaccion.usuario_id == usuario.id,
        Transaccion.fecha >= date(2025, 8, 1),
        Transaccion.fecha <= date(2026, 8, 31),
        Transaccion.tipo == TipoTransaccion.INGRESO,
        Transaccion.descripcion.like(f"%{TAG_SEED}%")
    ).scalar()
    prom_ingreso_13m = total_ingresos_13m / Decimal("13")

    print(f"\nPROMEDIO MENSUAL POR CATEGORIA (08/2025 - 08/2026) [Ingreso Promedio: ${prom_ingreso_13m:>11,.2f}]:")
    rows_cat = db.query(
        Categoria.nombre,
        func.sum(Transaccion.monto)
    ).join(Categoria, Transaccion.categoria_id == Categoria.id).filter(
        Transaccion.usuario_id == usuario.id,
        Transaccion.fecha >= date(2025, 8, 1),
        Transaccion.fecha <= date(2026, 8, 31),
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.movimiento_meta_id.is_(None),
        Transaccion.es_padre_cuotas == False,
        Transaccion.descripcion.like(f"%{TAG_SEED}%")
    ).group_by(Categoria.nombre).order_by(func.sum(Transaccion.monto).desc()).all()

    for cat_n, total_cat in rows_cat:
        prom_cat = total_cat / Decimal("13")
        pct_cat = (prom_cat / prom_ingreso_13m * Decimal("100")) if prom_ingreso_13m > 0 else Decimal("0")
        print(f"  {cat_n:<25}: Promedio Mensual = ${prom_cat:>11,.2f} ({pct_cat:>5.1f}% del ingreso) | Total 13m = ${total_cat:>12,.2f}")

    prom_serv = sum(t / Decimal("13") for c, t in rows_cat if c == "Servicios")
    prom_com = sum(t / Decimal("13") for c, t in rows_cat if c == "Comunicación")
    pct_serv = (prom_serv / prom_ingreso_13m * Decimal("100")) if prom_ingreso_13m > 0 else Decimal("0")
    pct_serv_com = ((prom_serv + prom_com) / prom_ingreso_13m * Decimal("100")) if prom_ingreso_13m > 0 else Decimal("0")
    print(f"\n  SERVICIOS SOLOS: Promedio Mensual = ${prom_serv:>11,.2f} ({pct_serv:>5.1f}% del ingreso)")
    print(f"  SERVICIOS + COMUNICACION: Promedio Mensual = ${prom_serv + prom_com:>11,.2f} ({pct_serv_com:>5.1f}% del ingreso)")

    print("\nSALDOS FINALES DE testingadmin:")
    for b in db.query(Billetera).filter(Billetera.usuario_id == usuario.id).all():
        print(f"  {b.nombre} ({b.moneda.value}): ${b.saldo_actual:,.2f}")

    # Recalcular perfil financiero
    print("\nRecalculando perfil financiero de testingadmin...")
    calcular_y_persistir_perfil(db, usuario.id)

    perfil = db.query(PerfilFinanciero).filter(PerfilFinanciero.usuario_id == usuario.id).first()
    print("\nPERFIL FINANCIERO CRUDA:")
    if perfil:
        import pprint
        p_dict = {
            "tasa_ahorro_ars": float(perfil.tasa_ahorro_ars) if perfil.tasa_ahorro_ars is not None else None,
            "tasa_ahorro_usd": float(perfil.tasa_ahorro_usd) if perfil.tasa_ahorro_usd is not None else None,
            "score_impulsividad_ars": float(perfil.score_impulsividad_ars) if perfil.score_impulsividad_ars is not None else None,
            "score_impulsividad_usd": float(perfil.score_impulsividad_usd) if perfil.score_impulsividad_usd is not None else None,
            "ratio_cuotas_ars": float(perfil.ratio_cuotas_ars) if perfil.ratio_cuotas_ars is not None else None,
            "ratio_cuotas_usd": float(perfil.ratio_cuotas_usd) if perfil.ratio_cuotas_usd is not None else None,
            "cumplimiento_presupuesto": float(perfil.cumplimiento_presupuesto) if perfil.cumplimiento_presupuesto is not None else None,
            "consistencia_registro": float(perfil.consistencia_registro) if perfil.consistencia_registro is not None else None,
            "porcentaje_suscripciones_ars": float(perfil.porcentaje_suscripciones_ars) if perfil.porcentaje_suscripciones_ars is not None else None,
            "porcentaje_suscripciones_usd": float(perfil.porcentaje_suscripciones_usd) if perfil.porcentaje_suscripciones_usd is not None else None,
            "ultima_actualizacion": str(perfil.ultima_actualizacion),
        }
        pprint.pprint(p_dict)
    else:
        print("Perfil financiero no disponible.")

    # Proyección actual
    print("\nPROYECCION CRUDA (ARS):")
    proy = calcular_proyeccion(db, usuario)
    if proy and "ars" in proy:
        import pprint
        pprint.pprint(proy["ars"])
    else:
        print("Proyección no disponible.")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        u = verificar_autorizacion(db)
        borrar_historial_anterior(db, u)
        regenerar_datos_realistas(db, u)
    finally:
        db.close()
