"""
Script maestro de regeneracion integral de testingadmin@argentum.com
Cumple con todas las directivas: Graphify, Regla de Metodo, Autorizacion de Escritura,
y Decision de Producto.
"""
from __future__ import annotations

import os
import sys
import time
import random
import unicodedata
import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, ".")
os.environ["LOG_LEVEL"] = "CRITICAL"

import logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("app").setLevel(logging.WARNING)

from dateutil.relativedelta import relativedelta
from sqlalchemy import text, select, func, and_, or_, case
from app.core.database import SessionLocal
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.tarjeta_credito import TarjetaCredito, RedTarjeta, EstadoTarjeta
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.meta import Meta, EstadoMeta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.presupuesto import Presupuesto, EstadoPresupuesto, PeriodoPresupuestoTipo, RenovacionPresupuesto
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
from app.models.cuota import Cuota
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
)
from app.models.perfil_financiero import PerfilFinanciero
from app.models.calibracion_usuario import CalibracionUsuario
from app.models.tools import IPCCache
from app.utils.fecha import hoy_argentina
from app.schemas.transaccion import TransaccionCreate, InfoCuotas
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.schemas.meta import MetaCreate
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.schemas.presupuesto import PresupuestoCreate, PresupuestoCategoriaInput
from app.schemas.suscripcion import SuscripcionCreate

from app.services import (
    transaccion_service,
    cuotas_service,
    transferencia_service,
    meta_service,
    presupuesto_service,
    suscripcion_service,
    perfil_financiero_service,
    proyeccion_service,
    calibracion_service,
    rendimiento_billetera_service,
)
from app.services.analisis_financiero_service import calcular_perfil_nuevo, calcular_proyeccion_nueva
from app.utils.finanzas import clasificar_gastos, deflactar_monto, _indice_por_mes

USUARIO_AUTORIZADO_EMAIL = "testingadmin@argentum.com"
USUARIO_AUTORIZADO_ID = "4c2ed62e-c22e-4d21-9bf9-ec6705f2c6fa"

CUENTAS_PROTEGIDAS = [
    "mrm291201@gmail.com",
    "angieperiolo@hotmail.com",
    "giordaninosebas@gmail.com",
    "albanopavia@gmail.com",
    "benitezsantiago2001@gmail.com",
    "orlandodjsegovia@gmail.com",
]

def verificar_seguridad(db) -> Usuario:
    u = db.query(Usuario).filter(Usuario.email == USUARIO_AUTORIZADO_EMAIL).first()
    if not u:
        raise RuntimeError(f"ABORT CRITICO: Usuario {USUARIO_AUTORIZADO_EMAIL} no encontrado.")
    if str(u.id) != USUARIO_AUTORIZADO_ID:
        raise RuntimeError(f"ABORT CRITICO: ID de usuario no coincide ({u.id} vs {USUARIO_AUTORIZADO_ID}).")
    return u

def snapshot_cuentas_protegidas(db) -> dict[str, int]:
    uids = [u.id for u in db.query(Usuario.id).filter(Usuario.email.in_(CUENTAS_PROTEGIDAS)).all()]
    if not uids:
        return {}
    res = {}
    res["transacciones"] = db.query(Transaccion).filter(Transaccion.usuario_id.in_(uids)).count()
    res["metas"] = db.query(Meta).filter(Meta.usuario_id.in_(uids)).count()
    res["presupuestos"] = db.query(Presupuesto).filter(Presupuesto.usuario_id.in_(uids)).count()
    res["suscripciones"] = db.query(Suscripcion).filter(Suscripcion.usuario_id.in_(uids)).count()
    res["transferencias"] = db.query(TransferenciaInterna).filter(TransferenciaInterna.usuario_id.in_(uids)).count()
    res["billeteras"] = db.query(Billetera).filter(Billetera.usuario_id.in_(uids)).count()
    return res

def ejecutar_borrado(db, user: Usuario) -> dict[str, int]:
    print("=== TAREA 2: BORRADO TOTAL DE testingadmin ===")
    conteos_borrados = {}

    # Desvincular foreign keys de transacciones hacia grupos_cuotas, metas y suscripciones
    db.execute(text("UPDATE transacciones SET grupo_cuotas_id = NULL, movimiento_meta_id = NULL, suscripcion_id = NULL WHERE usuario_id = :uid"), {"uid": user.id})
    db.flush()

    # 1. Cuotas
    gc_ids = [g.id for g in db.query(GrupoCuotas.id).filter(GrupoCuotas.usuario_id == user.id).all()]
    cant_c = 0
    if gc_ids:
        cant_c = db.query(Cuota).filter(Cuota.grupo_id.in_(gc_ids)).delete(synchronize_session=False)
    db.flush()
    conteos_borrados["cuotas"] = cant_c

    # 2. Grupos de cuotas
    cant_gc = db.query(GrupoCuotas).filter(GrupoCuotas.usuario_id == user.id).delete(synchronize_session=False)
    db.flush()
    conteos_borrados["grupos_cuotas"] = cant_gc

    # 3. Transacciones
    cant_tx = db.query(Transaccion).filter(Transaccion.usuario_id == user.id).delete(synchronize_session=False)
    db.flush()
    conteos_borrados["transacciones"] = cant_tx

    # 4. Movimientos de Meta y Metas
    meta_ids = [m.id for m in db.query(Meta.id).filter(Meta.usuario_id == user.id).all()]
    cant_mm = 0
    if meta_ids:
        cant_mm = db.query(MovimientoMeta).filter(MovimientoMeta.meta_id.in_(meta_ids)).delete(synchronize_session=False)
    cant_m = db.query(Meta).filter(Meta.usuario_id == user.id).delete(synchronize_session=False)
    db.flush()
    conteos_borrados["movimientos_meta"] = cant_mm
    conteos_borrados["metas"] = cant_m

    # 5. Presupuestos, Categorias y Periodos
    p_ids = [p.id for p in db.query(Presupuesto.id).filter(Presupuesto.usuario_id == user.id).all()]
    cant_pp = 0
    cant_pc = 0
    if p_ids:
        cant_pp = db.query(PeriodoPresupuesto).filter(PeriodoPresupuesto.presupuesto_id.in_(p_ids)).delete(synchronize_session=False)
        cant_pc = db.query(PresupuestoCategoria).filter(PresupuestoCategoria.presupuesto_id.in_(p_ids)).delete(synchronize_session=False)
    cant_p = db.query(Presupuesto).filter(Presupuesto.usuario_id == user.id).delete(synchronize_session=False)
    db.flush()
    conteos_borrados["periodos_presupuesto"] = cant_pp
    conteos_borrados["presupuestos_categorias"] = cant_pc
    conteos_borrados["presupuestos"] = cant_p

    # 6. Suscripciones e Historial
    sub_ids = [s.id for s in db.query(Suscripcion.id).filter(Suscripcion.usuario_id == user.id).all()]
    cant_hs = 0
    if sub_ids:
        cant_hs = db.query(HistorialSuscripcion).filter(HistorialSuscripcion.suscripcion_id.in_(sub_ids)).delete(synchronize_session=False)
    cant_s = db.query(Suscripcion).filter(Suscripcion.usuario_id == user.id).delete(synchronize_session=False)
    db.flush()
    conteos_borrados["historial_suscripciones"] = cant_hs
    conteos_borrados["suscripciones"] = cant_s

    # 7. Transferencias internas
    cant_tr = db.query(TransferenciaInterna).filter(TransferenciaInterna.usuario_id == user.id).delete(synchronize_session=False)
    db.flush()
    conteos_borrados["transferencias_internas"] = cant_tr

    # 8. Calibraciones y Perfil
    cant_cal = db.query(CalibracionUsuario).filter(CalibracionUsuario.usuario_id == user.id).delete(synchronize_session=False)
    cant_perf = db.query(PerfilFinanciero).filter(PerfilFinanciero.usuario_id == user.id).delete(synchronize_session=False)
    db.flush()
    conteos_borrados["calibraciones_usuario"] = cant_cal
    conteos_borrados["perfiles_financieros"] = cant_perf

    # 9. Resetear saldos de billeteras existentes a 0
    billeteras = db.query(Billetera).filter(Billetera.usuario_id == user.id).all()
    for b in billeteras:
        b.saldo_actual = Decimal("0.00")
        b.saldo_inicial = Decimal("0.00")
    db.commit()

    print("REGISTROS BORRADOS POR TABLA:")
    for tbl, count in conteos_borrados.items():
        print(f"  {tbl}: {count}")
    return conteos_borrados

def regenerar_historial():
    t_inicio_total = time.perf_counter()
    db = SessionLocal()
    try:
        user = verificar_seguridad(db)
        snap_antes = snapshot_cuentas_protegidas(db)
        conteos_borrados = ejecutar_borrado(db, user)

        hoy = hoy_argentina()
        rng = random.Random(20260924) # Semilla fija reproducible

        # -------------------------------------------------------------
        # Catalogo de Billeteras
        # -------------------------------------------------------------
        billeteras = {b.nombre: b for b in db.query(Billetera).filter(Billetera.usuario_id == user.id).all()}
        b_galicia = billeteras["Galicia"]
        b_santander = billeteras["Santander"]
        b_efectivo_ars = billeteras["Efectivo ARS"]
        b_efectivo_usd = billeteras["Efectivo USD"]

        # -------------------------------------------------------------
        # TAREA 5.5: Billetera de Inversion
        # -------------------------------------------------------------
        b_inversion = db.query(Billetera).filter(
            Billetera.usuario_id == user.id,
            Billetera.nombre == "Ahorro con rendimiento"
        ).first()
        if not b_inversion:
            b_inversion = Billetera(
                usuario_id=user.id,
                nombre="Ahorro con rendimiento",
                moneda=Moneda.ARS,
                saldo_inicial=Decimal("0.00"),
                saldo_actual=Decimal("0.00"),
                es_principal=False,
                es_efectivo=False,
                es_inversion=True,
                tna=Decimal("34.00"),
                fecha_ultimo_rendimiento=datetime.now(timezone.utc),
                estado=EstadoBilletera.ACTIVA
            )
            db.add(b_inversion)
            db.commit()
            db.refresh(b_inversion)
        else:
            b_inversion.saldo_actual = Decimal("0.00")
            b_inversion.saldo_inicial = Decimal("0.00")
            b_inversion.tna = Decimal("34.00")
            db.commit()

        # -------------------------------------------------------------
        # Catalogo de Tarjetas
        # -------------------------------------------------------------
        tarjetas = {t.nombre: t for t in db.query(TarjetaCredito).filter(TarjetaCredito.usuario_id == user.id).all()}
        t_visa_galicia = tarjetas.get("•••• 1506") or [t for t in tarjetas.values() if t.red == RedTarjeta.VISA and t.billetera_id == b_galicia.id][0]
        t_amex_galicia = tarjetas.get("•••• 2745") or [t for t in tarjetas.values() if t.red == RedTarjeta.AMEX][0]
        t_visa_santander = tarjetas.get("•••• 5077") or [t for t in tarjetas.values() if t.billetera_id == b_santander.id][0]

        # -------------------------------------------------------------
        # Mapeo de Categorias y Subcategorias de la BD
        # -------------------------------------------------------------
        def find_cat(nombre: str, tipo: str = "egreso") -> Categoria:
            c = db.query(Categoria).filter(Categoria.nombre.ilike(nombre), Categoria.tipo == tipo).first()
            if not c:
                raise RuntimeError(f"Categoria {nombre} ({tipo}) no encontrada.")
            return c

        def find_sub(cat: Categoria, nombre: str) -> Subcategoria:
            s = db.query(Subcategoria).filter(Subcategoria.categoria_id == cat.id, Subcategoria.nombre.ilike(nombre)).first()
            if not s:
                raise RuntimeError(f"Subcategoria {nombre} de {cat.nombre} no encontrada.")
            return s

        cat_empleo = find_cat("Empleo", "ingreso")
        sub_sueldo = find_sub(cat_empleo, "Sueldo")
        sub_sac = find_sub(cat_empleo, "Aguinaldo")

        cat_indep = find_cat("Trabajo independiente", "ingreso")
        sub_honorarios = find_sub(cat_indep, "Honorarios")
        sub_venta = find_sub(cat_indep, "Venta de productos/servicios")

        cat_vivienda = find_cat("Vivienda", "egreso")
        sub_alquiler = find_sub(cat_vivienda, "Alquiler")
        sub_expensas = find_sub(cat_vivienda, "Expensas")
        sub_luz = find_sub(cat_vivienda, "Luz")
        sub_gas = find_sub(cat_vivienda, "Gas")
        sub_agua = find_sub(cat_vivienda, "Agua")

        cat_comun = find_cat("Comunicación", "egreso")
        sub_celular = find_sub(cat_comun, "Celular")
        sub_internet = find_sub(cat_comun, "Internet y cable")

        cat_alim = find_cat("Alimentación", "egreso")
        sub_super = find_sub(cat_alim, "Supermercado")
        sub_carne = find_sub(cat_alim, "Carnicería")
        sub_verdu = find_sub(cat_alim, "Verdulería")
        sub_kiosco = find_sub(cat_alim, "Kiosco")

        cat_gastro = find_cat("Gastronomía", "egreso")
        sub_resto = find_sub(cat_gastro, "Restaurantes")
        sub_delivery = find_sub(cat_gastro, "Delivery")
        sub_cafe = find_sub(cat_gastro, "Cafetería")

        cat_transp = find_cat("Transporte", "egreso")
        sub_comb = find_sub(cat_transp, "Combustible")
        sub_transp_pub = find_sub(cat_transp, "Transporte público")
        sub_taxi = find_sub(cat_transp, "Taxi / Apps")

        cat_salud = find_cat("Salud", "egreso")
        sub_gimnasio = find_sub(cat_salud, "Deportes y gimnasio")
        sub_farmacia = find_sub(cat_salud, "Farmacia")

        cat_indum = find_cat("Indumentaria", "egreso")
        sub_ropa = find_sub(cat_indum, "Ropa")
        sub_calzado = find_sub(cat_indum, "Calzado")

        cat_recreo = find_cat("Recreativo", "egreso")
        sub_salidas = find_sub(cat_recreo, "Salidas")
        sub_hobbies = find_sub(cat_recreo, "Hobbies y juegos")

        cat_hogar_eq = find_cat("Equipamiento del hogar", "egreso")
        sub_muebles = find_sub(cat_hogar_eq, "Muebles y electrodomésticos")
        sub_limpieza = find_sub(cat_hogar_eq, "Limpieza")

        cat_otros_egr = find_cat("Otros", "egreso")
        sub_cuidado = find_sub(cat_otros_egr, "Cuidado personal")

        # -------------------------------------------------------------
        # TAREA 6: METAS
        # 6.1 Fondo de emergencia: obj $3.500.000 ARS
        # 6.2 Viaje a Bariloche: obj $1.400.000 ARS, fecha limite 2027-01-31
        # -------------------------------------------------------------
        meta_emergencia = meta_service.crear_meta(
            db, user.id,
            MetaCreate(
                nombre="Fondo de Emergencia",
                monto_objetivo=Decimal("3500000.00"),
                moneda=Moneda.ARS,
                monto_actual=Decimal("0.00"),
                fecha_limite=None,
                color="#10B981",
                nota="Fondo de resguardo para emergencias e imprevistos"
            )
        )

        meta_bariloche = meta_service.crear_meta(
            db, user.id,
            MetaCreate(
                nombre="Viaje a Bariloche",
                monto_objetivo=Decimal("1400000.00"),
                moneda=Moneda.ARS,
                monto_actual=Decimal("0.00"),
                fecha_limite=date(2027, 1, 31),
                color="#3B82F6",
                nota="Vacaciones de verano en el sur"
            )
        )

        # -------------------------------------------------------------
        # TAREA 8: SUSCRIPCIONES
        # 8.1 Streaming video (Netflix) mensual con aumento a mitad
        # 8.2 Streaming musica (Spotify) mensual
        # 8.3 Servicio nube / antivirus anual
        # -------------------------------------------------------------
        sub_video = suscripcion_service.crear_suscripcion(
            db, user.id,
            SuscripcionCreate(
                nombre="Netflix Estándar",
                monto=Decimal("13500.00"),
                moneda=Moneda.ARS,
                frecuencia=FrecuenciaSuscripcion.MENSUAL,
                categoria_id=cat_recreo.id,
                subcategoria_id=sub_hobbies.id,
                tarjeta_id=t_visa_galicia.id,
                billetera_id=None,
                proximo_cobro=date(2026, 10, 18)
            )
        )
        h_ant = db.query(HistorialSuscripcion).filter(HistorialSuscripcion.suscripcion_id == sub_video.id).first()
        h_ant.monto = Decimal("8500.00")
        h_ant.vigente_desde = date(2025, 8, 1)
        h_nuevo = HistorialSuscripcion(
            suscripcion_id=sub_video.id,
            monto=Decimal("13500.00"),
            moneda=Moneda.ARS,
            vigente_desde=date(2026, 3, 1)
        )
        db.add(h_nuevo)

        sub_musica = suscripcion_service.crear_suscripcion(
            db, user.id,
            SuscripcionCreate(
                nombre="Spotify Individual",
                monto=Decimal("6200.00"),
                moneda=Moneda.ARS,
                frecuencia=FrecuenciaSuscripcion.MENSUAL,
                categoria_id=cat_recreo.id,
                subcategoria_id=sub_hobbies.id,
                tarjeta_id=t_visa_galicia.id,
                billetera_id=None,
                proximo_cobro=date(2026, 10, 12)
            )
        )
        h_mus = db.query(HistorialSuscripcion).filter(HistorialSuscripcion.suscripcion_id == sub_musica.id).first()
        h_mus.vigente_desde = date(2025, 8, 1)

        sub_anual = suscripcion_service.crear_suscripcion(
            db, user.id,
            SuscripcionCreate(
                nombre="Google One Nube 2TB",
                monto=Decimal("42000.00"),
                moneda=Moneda.ARS,
                frecuencia=FrecuenciaSuscripcion.ANUAL,
                categoria_id=cat_comun.id,
                subcategoria_id=sub_internet.id,
                tarjeta_id=t_visa_galicia.id,
                billetera_id=None,
                proximo_cobro=date(2026, 11, 20)
            )
        )
        db.commit()

        # -------------------------------------------------------------
        # TAREA 4: TARJETAS Y CUOTAS (Grupos especiales)
        # 4.1 Celular 12 cuotas (Visa Galicia) arrancó antes del inicio del historial (2025-07-20)
        #     Primer vencimiento 2025-11-13. A septiembre 2026 va por la cuota 11 de 12.
        # 4.2 Indumentaria invierno 3 cuotas sin interés (Visa Galicia) arrancó 2026-05-20
        # 4.3 Electrodoméstico 12 cuotas con interés (Visa Santander) arrancó 2026-08-10 (vto hasta 2027)
        # 4.4 Consumos en USD con Amex Galicia (5 consumos a lo largo del periodo)
        # -------------------------------------------------------------
        # 4.1 Celular en 12 cuotas
        tx_padre_cel = transaccion_service.crear_transaccion(
            db, user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("600000.00"),
                moneda=Moneda.ARS,
                fecha=date(2025, 7, 20),
                descripcion="Smartphone Samsung Galaxy S24 12 cuotas",
                categoria_id=cat_comun.id,
                subcategoria_id=sub_celular.id,
                metodo_pago=MetodoPago.CREDITO,
                billetera_id=b_galicia.id,
                tarjeta_id=t_visa_galicia.id,
                primer_vencimiento_manual=date(2025, 11, 13),
                es_padre_cuotas=True,
                info_cuotas=InfoCuotas(
                    cantidad_cuotas=12,
                    cuota_inicial=1,
                    tiene_interes=False,
                    monto_total=Decimal("600000.00")
                )
            ),
            commit=True
        )
        for c in db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre_cel.id).all():
            if c.fecha_vencimiento <= hoy:
                c.pagada = True
                c.monto_real = c.monto_proyectado
                c.transaccion.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
            else:
                c.pagada = False
                c.monto_real = None
                c.transaccion.estado_verificacion = EstadoVerificacionTransaccion.PENDIENTE
        gc_cel = db.query(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre_cel.id).first()
        gc_cel.estado = EstadoGrupoCuotas.ACTIVO
        db.commit()

        # 4.2 Indumentaria invierno: 3 cuotas sin interés (Visa Galicia) arrancó 2026-05-20 (vencen jun, jul, ago 2026)
        tx_padre_indum = transaccion_service.crear_transaccion(
            db, user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("195000.00"),
                moneda=Moneda.ARS,
                fecha=date(2026, 5, 20),
                descripcion="Campera y botas de abrigo 3 cuotas",
                categoria_id=cat_indum.id,
                subcategoria_id=sub_ropa.id,
                metodo_pago=MetodoPago.CREDITO,
                billetera_id=b_galicia.id,
                tarjeta_id=t_visa_galicia.id,
                primer_vencimiento_manual=date(2026, 6, 13),
                es_padre_cuotas=True,
                info_cuotas=InfoCuotas(
                    cantidad_cuotas=3,
                    cuota_inicial=1,
                    tiene_interes=False,
                    monto_total=Decimal("195000.00")
                )
            ),
            commit=True
        )
        for c in db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre_indum.id).all():
            if c.fecha_vencimiento <= hoy:
                c.pagada = True
                c.monto_real = c.monto_proyectado
                c.transaccion.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
        gc_indum = db.query(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre_indum.id).first()
        gc_indum.estado = EstadoGrupoCuotas.COMPLETADO
        db.commit()

        # 4.3 Electrodoméstico / Smart TV en 12 cuotas con interés (Visa Santander)
        tx_padre_tv = transaccion_service.crear_transaccion(
            db, user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("780000.00"),
                moneda=Moneda.ARS,
                fecha=date(2026, 8, 10),
                descripcion="Smart TV 55 Pulgadas 4K 12 cuotas",
                categoria_id=cat_hogar_eq.id,
                subcategoria_id=sub_muebles.id,
                metodo_pago=MetodoPago.CREDITO,
                billetera_id=b_santander.id,
                tarjeta_id=t_visa_santander.id,
                primer_vencimiento_manual=date(2026, 9, 2),
                es_padre_cuotas=True,
                info_cuotas=InfoCuotas(
                    cantidad_cuotas=12,
                    cuota_inicial=1,
                    tiene_interes=True,
                    tasa_interes=Decimal("4.50"), # 4.5% mensual
                    monto_total=Decimal("780000.00")
                )
            ),
            commit=True
        )
        for c in db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre_tv.id).all():
            if c.fecha_vencimiento <= hoy:
                c.pagada = True
                c.monto_real = c.monto_proyectado
                c.transaccion.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
            else:
                c.pagada = False
                c.monto_real = None
                c.transaccion.estado_verificacion = EstadoVerificacionTransaccion.PENDIENTE
        gc_tv = db.query(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre_tv.id).first()
        gc_tv.estado = EstadoGrupoCuotas.ACTIVO
        db.commit()

        # 4.4 / 5.2 Consumos en USD con Amex Galicia (5 consumos variados)
        consumos_usd = [
            (date(2025, 9, 15), Decimal("35.00"), "AWS Cloud Hosting y Servicios USD", date(2025, 10, 13)),
            (date(2025, 11, 20), Decimal("42.00"), "Udemy Online Courses Tech USD", date(2025, 12, 13)),
            (date(2026, 2, 14), Decimal("58.00"), "Namecheap Domains and SSL USD", date(2026, 3, 13)),
            (date(2026, 5, 18), Decimal("30.00"), "Midjourney AI Subscription USD", date(2026, 6, 13)),
            (date(2026, 8, 15), Decimal("48.00"), "GitHub Copilot y Cloud SaaS USD", date(2026, 9, 13)),
        ]
        for f_usd, m_usd, desc_usd, vto_usd in consumos_usd:
            tx_u = transaccion_service.crear_transaccion(
                db, user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=m_usd,
                    moneda=Moneda.USD,
                    fecha=f_usd,
                    descripcion=desc_usd,
                    categoria_id=cat_comun.id,
                    subcategoria_id=sub_internet.id,
                    metodo_pago=MetodoPago.CREDITO,
                    billetera_id=b_galicia.id,
                    tarjeta_id=t_amex_galicia.id,
                    primer_vencimiento_manual=vto_usd,
                    es_padre_cuotas=True,
                    info_cuotas=InfoCuotas(
                        cantidad_cuotas=1,
                        cuota_inicial=1,
                        tiene_interes=False,
                        monto_total=m_usd
                    )
                ),
                commit=True
            )
            for c_u in db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_u.id).all():
                if c_u.fecha_vencimiento <= hoy:
                    c_u.pagada = True
                    c_u.monto_real = c_u.monto_proyectado
                    c_u.transaccion.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
                    c_u.grupo.estado = EstadoGrupoCuotas.COMPLETADO
                else:
                    c_u.pagada = False
                    c_u.monto_real = None
                    c_u.transaccion.estado_verificacion = EstadoVerificacionTransaccion.PENDIENTE
        db.commit()

        # -------------------------------------------------------------
        # 5.3: Gastos chicos pagados directamente desde Efectivo USD
        # -------------------------------------------------------------
        transaccion_service.crear_transaccion(
            db, user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("25.00"),
                moneda=Moneda.USD,
                fecha=date(2026, 1, 25),
                descripcion="Snacks y café aeropuerto Ezeiza USD",
                categoria_id=cat_alim.id,
                subcategoria_id=sub_kiosco.id,
                metodo_pago=MetodoPago.EFECTIVO,
                billetera_id=b_efectivo_usd.id
            ),
            commit=True
        )
        transaccion_service.crear_transaccion(
            db, user.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("35.00"),
                moneda=Moneda.USD,
                fecha=date(2026, 7, 12),
                descripcion="Entrada recital internacional USD",
                categoria_id=cat_recreo.id,
                subcategoria_id=sub_salidas.id,
                metodo_pago=MetodoPago.EFECTIVO,
                billetera_id=b_efectivo_usd.id
            ),
            commit=True
        )

        # -------------------------------------------------------------
        # TAREA 3: GENERACION HISTORICA DE INGRESOS Y GASTOS (Mes a Mes)
        # Rango: 2025-08 a 2026-09 (hasta hoy_argentina())
        # -------------------------------------------------------------
        meses = [
            (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
            (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6), (2026, 7), (2026, 8),
            (2026, 9)
        ]

        def get_sueldo(anio: int, mes: int) -> Decimal:
            if (anio, mes) < (2025, 12):
                return Decimal("2150000.00")
            elif (anio, mes) < (2026, 4):
                return Decimal("2380000.00")
            elif (anio, mes) < (2026, 7):
                return Decimal("2620000.00")
            else:
                return Decimal("2850000.00")

        # Generar movimientos mes a mes
        for anio, mes in meses:
            es_mes_actual = (anio == 2026 and mes == 9)
            sueldo_m = get_sueldo(anio, mes)
            factor_sueldo = sueldo_m / Decimal("2850000.00")

            # 1. Sueldo el dia 1
            transaccion_service.crear_transaccion(
                db, user.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.INGRESO,
                    monto=sueldo_m,
                    moneda=Moneda.ARS,
                    fecha=date(anio, mes, 1),
                    descripcion=f"Sueldo mensual {mes:02d}/{anio}",
                    categoria_id=cat_empleo.id,
                    subcategoria_id=sub_sueldo.id,
                    metodo_pago=MetodoPago.TRANSFERENCIA,
                    billetera_id=b_galicia.id,
                    es_recurrente=True
                ),
                commit=False
            )

            # 2. SAC Aguinaldo en junio y diciembre el dia 20
            if mes in (6, 12):
                monto_sac = (sueldo_m * Decimal("0.50")).quantize(Decimal("0.01"))
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.INGRESO,
                        monto=monto_sac,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, 20),
                        descripcion=f"SAC {'1er' if mes == 6 else '2do'} Semestre {anio}",
                        categoria_id=cat_empleo.id,
                        subcategoria_id=sub_sac.id,
                        metodo_pago=MetodoPago.TRANSFERENCIA,
                        billetera_id=b_galicia.id,
                        es_recurrente=False
                    ),
                    commit=False
                )

            # 3. Ingresos extra chicos y puntuales
            if anio == 2025 and mes == 10:
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.INGRESO,
                        monto=Decimal("85000.00"),
                        moneda=Moneda.ARS,
                        fecha=date(2025, 10, 18),
                        descripcion="Venta monitor usado Dell 24 pulgadas",
                        categoria_id=cat_indep.id,
                        subcategoria_id=sub_venta.id,
                        metodo_pago=MetodoPago.TRANSFERENCIA,
                        billetera_id=b_galicia.id
                    ),
                    commit=False
                )
            if anio == 2026 and mes == 5:
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.INGRESO,
                        monto=Decimal("120000.00"),
                        moneda=Moneda.ARS,
                        fecha=date(2026, 5, 14),
                        descripcion="Changa soporte técnico red hogareña",
                        categoria_id=cat_indep.id,
                        subcategoria_id=sub_honorarios.id,
                        metodo_pago=MetodoPago.TRANSFERENCIA,
                        billetera_id=b_galicia.id
                    ),
                    commit=False
                )

            # -------------------------------------------------------------
            # GASTOS FIJOS / COMPROMISOS MENSUALES
            # -------------------------------------------------------------
            # Vivienda: Alquiler ~27.7% + Expensas ~4.9% (Total 32.6%)
            m_alquiler = (Decimal("790000.00") * factor_sueldo + Decimal(str(rng.randint(-1500, 1500)))).quantize(Decimal("0.01"))
            if date(anio, mes, 3) <= hoy:
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_alquiler,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, 3),
                        descripcion=f"Alquiler dpto Almagro {mes:02d}/{anio}",
                        categoria_id=cat_vivienda.id,
                        subcategoria_id=sub_alquiler.id,
                        metodo_pago=MetodoPago.TRANSFERENCIA,
                        billetera_id=b_galicia.id,
                        es_recurrente=False
                    ),
                    commit=False
                )

            m_expensas = (Decimal("140000.00") * factor_sueldo + Decimal(str(rng.randint(-1200, 1200)))).quantize(Decimal("0.01"))
            if date(anio, mes, 9) <= hoy:
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_expensas,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, 9),
                        descripcion=f"Expensas edificio {mes:02d}/{anio}",
                        categoria_id=cat_vivienda.id,
                        subcategoria_id=sub_expensas.id,
                        metodo_pago=MetodoPago.TRANSFERENCIA,
                        billetera_id=b_galicia.id,
                        es_recurrente=False
                    ),
                    commit=False
                )

            # Luz: mensual (~2.0%)
            m_luz = (Decimal("56000.00") * factor_sueldo + Decimal(str(rng.randint(-900, 900)))).quantize(Decimal("0.01"))
            if date(anio, mes, 14) <= hoy:
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_luz,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, 14),
                        descripcion=f"Edenor {mes:02d}/{anio}",
                        categoria_id=cat_vivienda.id,
                        subcategoria_id=sub_luz.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=b_galicia.id,
                        es_recurrente=False
                    ),
                    commit=False
                )

            # Gas: bimestral (meses pares: 2, 4, 6, 8, 10, 12)
            if mes % 2 == 0 and date(anio, mes, 16) <= hoy:
                pico_invierno = Decimal("15000.00") if mes in (6, 8) else Decimal("0")
                m_gas = (Decimal("32000.00") * factor_sueldo + pico_invierno + Decimal(str(rng.randint(-600, 600)))).quantize(Decimal("0.01"))
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_gas,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, 16),
                        descripcion=f"Metrogas {mes:02d}/{anio}",
                        categoria_id=cat_vivienda.id,
                        subcategoria_id=sub_gas.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=b_galicia.id,
                        es_recurrente=False
                    ),
                    commit=False
                )

            # Agua: bimestral (meses impares: 1, 3, 5, 7, 9, 11)
            if mes % 2 != 0 and date(anio, mes, 17) <= hoy:
                m_agua = (Decimal("26000.00") * factor_sueldo + Decimal(str(rng.randint(-500, 500)))).quantize(Decimal("0.01"))
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_agua,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, 17),
                        descripcion=f"AySA Agua {mes:02d}/{anio}",
                        categoria_id=cat_vivienda.id,
                        subcategoria_id=sub_agua.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=b_galicia.id,
                        es_recurrente=False
                    ),
                    commit=False
                )

            # Internet: mensual (~2.7%)
            m_net = (Decimal("78000.00") * factor_sueldo + Decimal(str(rng.randint(-800, 800)))).quantize(Decimal("0.01"))
            if date(anio, mes, 11) <= hoy:
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_net,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, 11),
                        descripcion=f"Fibertel Personal 300MB {mes:02d}/{anio}",
                        categoria_id=cat_comun.id,
                        subcategoria_id=sub_internet.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=b_galicia.id,
                        es_recurrente=False
                    ),
                    commit=False
                )

            # Celular: mensual (~1.7%)
            m_cel = (Decimal("48000.00") * factor_sueldo + Decimal(str(rng.randint(-600, 600)))).quantize(Decimal("0.01"))
            if date(anio, mes, 15) <= hoy:
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_cel,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, 15),
                        descripcion=f"Abono Celular Personal {mes:02d}/{anio}",
                        categoria_id=cat_comun.id,
                        subcategoria_id=sub_celular.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=b_galicia.id,
                        es_recurrente=False
                    ),
                    commit=False
                )

            # -------------------------------------------------------------
            # TAREA 3.5: GIMNASIO (Recurrente NO declarado para deteccion automatica)
            # Mismo rango de monto (~$48.000 deflactado), dia ~10, debito Galicia
            # -------------------------------------------------------------
            m_gym = (Decimal("48000.00") * factor_sueldo + Decimal(str(rng.randint(-400, 400)))).quantize(Decimal("0.01"))
            d_gym = date(anio, mes, 10)
            if d_gym <= hoy:
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_gym,
                        moneda=Moneda.ARS,
                        fecha=d_gym,
                        descripcion="Megatlon Cuota Gimnasio",
                        categoria_id=cat_salud.id,
                        subcategoria_id=sub_gimnasio.id,
                        metodo_pago=MetodoPago.DEBITO,
                        billetera_id=b_galicia.id,
                        es_recurrente=False # NO declarado!
                    ),
                    commit=False
                )

            # -------------------------------------------------------------
            # TAREA 5: TRANSFERENCIAS INTERNAS
            # 5.1 Compra de dólares TODOS los meses (USD 100)
            # 5.4 Retiro de cajero (Galicia -> Efectivo ARS, 2 veces por mes)
            # -------------------------------------------------------------
            d_atm1 = date(anio, mes, 5)
            if d_atm1 <= hoy:
                transferencia_service.crear_transferencia(
                    db, user.id,
                    TransferenciaInternaCreate(
                        billetera_origen_id=b_galicia.id,
                        billetera_destino_id=b_efectivo_ars.id,
                        monto=Decimal("60000.00"),
                        moneda=Moneda.ARS,
                        fecha=d_atm1,
                        notas="Extracción de cajero Banelco Galicia"
                    ),
                    commit=False
                )
            d_atm2 = date(anio, mes, 20)
            if d_atm2 <= hoy:
                m_atm2 = Decimal("80000.00") if (anio == 2026 and mes >= 6) else Decimal("50000.00")
                transferencia_service.crear_transferencia(
                    db, user.id,
                    TransferenciaInternaCreate(
                        billetera_origen_id=b_galicia.id,
                        billetera_destino_id=b_efectivo_ars.id,
                        monto=m_atm2,
                        moneda=Moneda.ARS,
                        fecha=d_atm2,
                        notas="Extracción de cajero Banelco Galicia"
                    ),
                    commit=False
                )

            # Compra USD TODOS los meses (Punto 5.1)
            d_usd = date(anio, mes, 24)
            if d_usd <= hoy:
                m_usd_compra = Decimal("100.00")
                cotiz_mes = (Decimal("1250.00") + Decimal(str((anio - 2025) * 12 + mes - 8)) * Decimal("18.00")).quantize(Decimal("0.01"))
                m_ars_salida = (m_usd_compra * cotiz_mes).quantize(Decimal("0.01"))
                transferencia_service.crear_transferencia(
                    db, user.id,
                    TransferenciaInternaCreate(
                        billetera_origen_id=b_galicia.id,
                        billetera_destino_id=b_efectivo_usd.id,
                        monto=m_ars_salida,
                        moneda=Moneda.ARS,
                        monto_destino=m_usd_compra,
                        fecha=d_usd,
                        notas="Compra dólares ahorro homebanking"
                    ),
                    commit=False
                )

            # -------------------------------------------------------------
            # TAREA 6: APORTES A METAS
            # 6.1 Fondo de emergencia mensual
            # 6.2 Viaje a Bariloche desde marzo 2026 + retiro parcial en mayo
            # -------------------------------------------------------------
            d_meta1 = date(anio, mes, 25)
            if d_meta1 <= hoy and not es_mes_actual:
                base_aporte_em = Decimal(str(210000 + rng.randint(-10000, 15000)))
                if mes in (6, 12):
                    base_aporte_em += Decimal("250000.00")
                meta_service.registrar_movimiento(
                    db, user.id, meta_emergencia.id,
                    MovimientoMetaCreate(
                        tipo=TipoMovimientoMeta.APORTE,
                        monto=base_aporte_em,
                        moneda_movimiento=Moneda.ARS,
                        billetera_id=b_galicia.id,
                        fecha=d_meta1
                    )
                )

            # Viaje Bariloche desde marzo 2026
            if (anio, mes) >= (2026, 3):
                d_bar = date(anio, mes, 26)
                if d_bar <= hoy and mes in (3, 4, 6, 7, 8):
                    m_bar = Decimal("180000.00") if mes != 6 else Decimal("350000.00")
                    meta_service.registrar_movimiento(
                        db, user.id, meta_bariloche.id,
                        MovimientoMetaCreate(
                            tipo=TipoMovimientoMeta.APORTE,
                            monto=m_bar,
                            moneda_movimiento=Moneda.ARS,
                            billetera_id=b_galicia.id,
                            fecha=d_bar
                        )
                    )
                # Retiro parcial en mayo 2026 para pagar seña del hotel
                if (anio, mes) == (2026, 5):
                    meta_service.registrar_movimiento(
                        db, user.id, meta_bariloche.id,
                        MovimientoMetaCreate(
                            tipo=TipoMovimientoMeta.RETIRO,
                            monto=Decimal("120000.00"),
                            moneda_movimiento=Moneda.ARS,
                            billetera_id=b_galicia.id,
                            fecha=date(2026, 5, 27)
                        )
                    )

            # -------------------------------------------------------------
            # TAREA 3.2 / 3.4: GASTOS VARIABLES (55 a 90 por mes)
            # -------------------------------------------------------------
            max_dia_mes = 22 if es_mes_actual else monthrange(anio, mes)[1]

            # Supermercado: 4 grandes compras por mes (~$95.000)
            dias_super = [3, 10, 17, 24] if not es_mes_actual else [4, 12, 18]
            for ds in dias_super:
                if ds <= max_dia_mes:
                    m_s = (Decimal("95000.00") * factor_sueldo + Decimal(str(rng.randint(-4000, 4500)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_s,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, ds),
                            descripcion=rng.choice(["Supermercado Coto", "Supermercado Dia", "Carrefour Market"]),
                            categoria_id=cat_alim.id,
                            subcategoria_id=sub_super.id,
                            metodo_pago=MetodoPago.DEBITO,
                            billetera_id=b_galicia.id
                        ),
                        commit=False
                    )

            # Carnicería y Verdulería semanales
            for sem in range(1, 5):
                d_c = min(max_dia_mes, sem * 7 - 2)
                d_v = min(max_dia_mes, sem * 7 - 4)
                if d_c <= max_dia_mes and d_c > 0:
                    m_c = (Decimal("26000.00") * factor_sueldo + Decimal(str(rng.randint(-1500, 1800)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_c,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, d_c),
                            descripcion="Carnicería Los Primos",
                            categoria_id=cat_alim.id,
                            subcategoria_id=sub_carne.id,
                            metodo_pago=MetodoPago.DEBITO,
                            billetera_id=b_galicia.id
                        ),
                        commit=False
                    )
                if d_v <= max_dia_mes and d_v > 0:
                    m_v = (Decimal("16000.00") * factor_sueldo + Decimal(str(rng.randint(-1200, 1200)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_v,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, d_v),
                            descripcion="Verdulería La Huerta",
                            categoria_id=cat_alim.id,
                            subcategoria_id=sub_verdu.id,
                            metodo_pago=MetodoPago.EFECTIVO,
                            billetera_id=b_efectivo_ars.id
                        ),
                        commit=False
                    )

            # Gasto hormiga (Kiosco / café / alfajores / snacks) 16 a 18 veces al mes
            dias_kiosco = sorted(rng.sample(range(1, max_dia_mes + 1), min(17, max_dia_mes)))
            for dk in dias_kiosco:
                m_k = (Decimal(str(rng.randint(2200, 4800))) * factor_sueldo).quantize(Decimal("0.01"))
                desc_k = rng.choice(["Café al paso", "Alfajor y gaseosa", "Kiosco Open 25", "Snacks y agua mineral"])
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_k,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, dk),
                        descripcion=desc_k,
                        categoria_id=cat_alim.id,
                        subcategoria_id=sub_kiosco.id,
                        metodo_pago=MetodoPago.EFECTIVO if dk % 2 == 0 else MetodoPago.DEBITO,
                        billetera_id=b_efectivo_ars.id if dk % 2 == 0 else b_galicia.id
                    ),
                    commit=False
                )

            # Gastronomía y salidas
            dias_gastro = [6, 12, 19, 23, 27] if not es_mes_actual else [5, 11, 19]
            for idx_g, dg in enumerate(dias_gastro):
                if dg <= max_dia_mes:
                    if idx_g == 0:
                        m_g = (Decimal("38000.00") * factor_sueldo + Decimal(str(rng.randint(-2000, 2500)))).quantize(Decimal("0.01"))
                        desc_g, sub_g = "Cena restaurante Palermo", sub_resto
                    elif idx_g == 1:
                        m_g = (Decimal("18500.00") * factor_sueldo + Decimal(str(rng.randint(-1200, 1500)))).quantize(Decimal("0.01"))
                        desc_g, sub_g = "PedidosYa Sushi delivery", sub_delivery
                    elif idx_g == 2:
                        m_g = (Decimal("12000.00") * factor_sueldo + Decimal(str(rng.randint(-800, 1000)))).quantize(Decimal("0.01"))
                        desc_g, sub_g = "Cafetería y tostados", sub_cafe
                    elif idx_g == 3:
                        m_g = (Decimal("19500.00") * factor_sueldo + Decimal(str(rng.randint(-1100, 1200)))).quantize(Decimal("0.01"))
                        desc_g, sub_g = "PedidosYa Hamburguesas", sub_delivery
                    else:
                        m_g = (Decimal("34000.00") * factor_sueldo + Decimal(str(rng.randint(-1800, 2000)))).quantize(Decimal("0.01"))
                        desc_g, sub_g = "Pizzería Güerrin cena amigos", sub_resto

                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_g,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, dg),
                            descripcion=desc_g,
                            categoria_id=cat_gastro.id,
                            subcategoria_id=sub_g.id,
                            metodo_pago=MetodoPago.DEBITO if idx_g % 2 == 0 else MetodoPago.CREDITO,
                            billetera_id=b_galicia.id,
                            tarjeta_id=t_visa_galicia.id if idx_g % 2 != 0 else None
                        ),
                        commit=False
                    )

            # Transporte:
            # 2 Cargas de Nafta YPF
            for d_nafta in ([7, 21] if not es_mes_actual else [6, 18]):
                if d_nafta <= max_dia_mes:
                    m_naf = (Decimal("48000.00") * factor_sueldo + Decimal(str(rng.randint(-1200, 1500)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_naf,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, d_nafta),
                            descripcion="YPF Combustible Nafta Súper",
                            categoria_id=cat_transp.id,
                            subcategoria_id=sub_comb.id,
                            metodo_pago=MetodoPago.DEBITO,
                            billetera_id=b_galicia.id
                        ),
                        commit=False
                    )

            # 4 a 5 Cargas SUBE
            dias_sube = [2, 8, 15, 22, 28] if not es_mes_actual else [3, 9, 16]
            for d_sb in dias_sube:
                if d_sb <= max_dia_mes:
                    m_sb = (Decimal("8500.00") * factor_sueldo + Decimal(str(rng.randint(-500, 600)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_sb,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, d_sb),
                            descripcion="Carga Tarjeta SUBE",
                            categoria_id=cat_transp.id,
                            subcategoria_id=sub_transp_pub.id,
                            metodo_pago=MetodoPago.DEBITO,
                            billetera_id=b_galicia.id
                        ),
                        commit=False
                    )

            # 1 a 2 Taxis / Cabify (crédito Santander)
            for d_tx in ([13, 26] if not es_mes_actual else [14]):
                if d_tx <= max_dia_mes:
                    m_tx = (Decimal("11500.00") * factor_sueldo + Decimal(str(rng.randint(-800, 800)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_tx,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, d_tx),
                            descripcion="Cabify viaje trabajo",
                            categoria_id=cat_transp.id,
                            subcategoria_id=sub_taxi.id,
                            metodo_pago=MetodoPago.CREDITO,
                            billetera_id=b_santander.id,
                            tarjeta_id=t_visa_santander.id
                        ),
                        commit=False
                    )

            # Salud: Farmacia 2 veces al mes
            for d_f in ([8, 22] if not es_mes_actual else [8]):
                if d_f <= max_dia_mes:
                    m_f = (Decimal("24000.00") * factor_sueldo + Decimal(str(rng.randint(-1500, 1500)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_f,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, d_f),
                            descripcion="Farmacity Medicamentos y vitaminas",
                            categoria_id=cat_salud.id,
                            subcategoria_id=sub_farmacia.id,
                            metodo_pago=MetodoPago.DEBITO,
                            billetera_id=b_galicia.id
                        ),
                        commit=False
                    )

            # Recreativo y salidas
            for d_sal in ([15, 28] if not es_mes_actual else [15]):
                if d_sal <= max_dia_mes:
                    m_sal = (Decimal("28000.00") * factor_sueldo + Decimal(str(rng.randint(-1800, 2000)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_sal,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, d_sal),
                            descripcion="Cine Hoyts y entradas teatro",
                            categoria_id=cat_recreo.id,
                            subcategoria_id=sub_salidas.id,
                            metodo_pago=MetodoPago.DEBITO,
                            billetera_id=b_galicia.id
                        ),
                        commit=False
                    )

            # Indumentaria / Cuidado personal: 1 vez al mes
            d_ind = min(max_dia_mes, 18)
            if d_ind <= max_dia_mes:
                m_ind = (Decimal("36000.00") * factor_sueldo + Decimal(str(rng.randint(-2000, 2200)))).quantize(Decimal("0.01"))
                transaccion_service.crear_transaccion(
                    db, user.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=m_ind,
                        moneda=Moneda.ARS,
                        fecha=date(anio, mes, d_ind),
                        descripcion="Peluquería y barbería Almagro",
                        categoria_id=cat_otros_egr.id,
                        subcategoria_id=sub_cuidado.id,
                        metodo_pago=MetodoPago.EFECTIVO,
                        billetera_id=b_efectivo_ars.id
                    ),
                    commit=False
                )

            # Ropa casual cada 2 meses (en 1 pago con Visa Santander)
            if mes % 2 == 0:
                d_rop = min(max_dia_mes, 21)
                if d_rop <= max_dia_mes:
                    m_rop = (Decimal("54000.00") * factor_sueldo + Decimal(str(rng.randint(-2500, 3000)))).quantize(Decimal("0.01"))
                    transaccion_service.crear_transaccion(
                        db, user.id,
                        TransaccionCreate(
                            tipo=TipoTransaccion.EGRESO,
                            monto=m_rop,
                            moneda=Moneda.ARS,
                            fecha=date(anio, mes, d_rop),
                            descripcion="Zara Indumentaria remera y accesorios",
                            categoria_id=cat_indum.id,
                            subcategoria_id=sub_ropa.id,
                            metodo_pago=MetodoPago.CREDITO,
                            billetera_id=b_santander.id,
                            tarjeta_id=t_visa_santander.id,
                            es_padre_cuotas=True,
                            info_cuotas=InfoCuotas(
                                cantidad_cuotas=1,
                                cuota_inicial=1,
                                tiene_interes=False,
                                monto_total=m_rop
                            )
                        ),
                        commit=False
                    )
                    for c_rop in db.query(Cuota).join(GrupoCuotas).join(Transaccion, GrupoCuotas.transaccion_padre_id == Transaccion.id).filter(Transaccion.fecha == date(anio, mes, d_rop)).all():
                        if c_rop.fecha_vencimiento <= hoy:
                            c_rop.pagada = True
                            c_rop.monto_real = c_rop.monto_proyectado
                            c_rop.transaccion.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
                            c_rop.grupo.estado = EstadoGrupoCuotas.COMPLETADO

            # Commit por lote al final de cada mes
            db.commit()

        # -------------------------------------------------------------
        # TAREA 5.5: Transferir fondos a billetera de inversion
        # $500.000 desde Galicia a "Ahorro con rendimiento"
        # -------------------------------------------------------------
        transferencia_service.crear_transferencia(
            db, user.id,
            TransferenciaInternaCreate(
                billetera_origen_id=b_galicia.id,
                billetera_destino_id=b_inversion.id,
                monto=Decimal("500000.00"),
                moneda=Moneda.ARS,
                fecha=date(2026, 9, 10),
                notas="Apartado fondo de emergencia a billetera de inversión con TNA"
            ),
            commit=True
        )

        # -------------------------------------------------------------
        # TAREA 7: PRESUPUESTOS
        # 7.1 Presupuesto Gastronomía (mensual $200.000, jul y ago históricos, ago > 80%)
        # 7.2 Presupuesto Indumentaria (mensual $150.000, ago < 80%)
        # -------------------------------------------------------------
        pres_gastro = presupuesto_service.crear_presupuesto(
            db, user.id,
            PresupuestoCreate(
                nombre="Gastronomía y Salidas",
                monto=Decimal("200000.00"),
                moneda=Moneda.ARS,
                periodo=PeriodoPresupuestoTipo.MENSUAL,
                renovacion=RenovacionPresupuesto.AUTOMATICA,
                categorias=[PresupuestoCategoriaInput(categoria_id=cat_gastro.id)]
            )
        )

        gasto_gastro_jul = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
            Transaccion.usuario_id == user.id,
            Transaccion.categoria_id == cat_gastro.id,
            Transaccion.fecha >= date(2026, 7, 1),
            Transaccion.fecha <= date(2026, 7, 31),
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.es_padre_cuotas == False
        ).scalar()
        p_jul = PeriodoPresupuesto(
            presupuesto_id=pres_gastro.id,
            fecha_inicio=date(2026, 7, 1),
            fecha_fin=date(2026, 7, 31),
            monto_limite=Decimal("200000.00"),
            monto_usado=gasto_gastro_jul,
            superado=(gasto_gastro_jul > Decimal("200000.00"))
        )
        db.add(p_jul)

        gasto_gastro_ago = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
            Transaccion.usuario_id == user.id,
            Transaccion.categoria_id == cat_gastro.id,
            Transaccion.fecha >= date(2026, 8, 1),
            Transaccion.fecha <= date(2026, 8, 31),
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.es_padre_cuotas == False
        ).scalar()
        limite_ago = Decimal("180000.00") if gasto_gastro_ago < Decimal("165000.00") else Decimal("200000.00")
        p_ago = PeriodoPresupuesto(
            presupuesto_id=pres_gastro.id,
            fecha_inicio=date(2026, 8, 1),
            fecha_fin=date(2026, 8, 31),
            monto_limite=limite_ago,
            monto_usado=gasto_gastro_ago,
            superado=(gasto_gastro_ago > limite_ago)
        )
        db.add(p_ago)

        pres_indum = presupuesto_service.crear_presupuesto(
            db, user.id,
            PresupuestoCreate(
                nombre="Indumentaria",
                monto=Decimal("150000.00"),
                moneda=Moneda.ARS,
                periodo=PeriodoPresupuestoTipo.MENSUAL,
                renovacion=RenovacionPresupuesto.AUTOMATICA,
                categorias=[PresupuestoCategoriaInput(categoria_id=cat_indum.id)]
            )
        )
        gasto_indum_ago = db.query(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).filter(
            Transaccion.usuario_id == user.id,
            Transaccion.categoria_id == cat_indum.id,
            Transaccion.fecha >= date(2026, 8, 1),
            Transaccion.fecha <= date(2026, 8, 31),
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.es_padre_cuotas == False
        ).scalar()
        p_ind_ago = PeriodoPresupuesto(
            presupuesto_id=pres_indum.id,
            fecha_inicio=date(2026, 8, 1),
            fecha_fin=date(2026, 8, 31),
            monto_limite=Decimal("150000.00"),
            monto_usado=gasto_indum_ago,
            superado=False
        )
        db.add(p_ind_ago)
        db.commit()

        # -------------------------------------------------------------
        # Recalcular saldos de billeteras
        # -------------------------------------------------------------
        for b in db.query(Billetera).filter(Billetera.usuario_id == user.id).all():
            tx_row = db.execute(text("""
                SELECT 
                    coalesce(sum(case when tipo = 'ingreso' then monto else 0 end), 0) as ingresos,
                    coalesce(sum(case when tipo = 'egreso' then monto else 0 end), 0) as egresos
                FROM transacciones
                WHERE billetera_id = :bid
                  AND (metodo_pago != 'credito' OR metodo_pago IS NULL)
                  AND es_padre_cuotas = false
                  AND (estado_verificacion IS NULL OR estado_verificacion != 'pendiente')
                  AND fecha <= :hoy
            """), {"bid": b.id, "hoy": hoy}).mappings().fetchone()

            ing = Decimal(str(tx_row["ingresos"]))
            egr = Decimal(str(tx_row["egresos"]))

            tr_in = Decimal(str(db.execute(text("""
                SELECT coalesce(sum(monto_destino), 0) 
                FROM transferencias_internas 
                WHERE billetera_destino_id = :bid AND fecha <= :hoy
            """), {"bid": b.id, "hoy": hoy}).scalar() or 0))

            tr_out = Decimal(str(db.execute(text("""
                SELECT coalesce(sum(monto_origen), 0) 
                FROM transferencias_internas 
                WHERE billetera_origen_id = :bid AND fecha <= :hoy
            """), {"bid": b.id, "hoy": hoy}).scalar() or 0))

            s_inicial = b.saldo_inicial or Decimal("0.00")
            b.saldo_actual = s_inicial + ing - egr + tr_in - tr_out

        db.commit()

        # -------------------------------------------------------------
        # Actualizar monto_actual de metas
        # -------------------------------------------------------------
        for meta in [meta_emergencia, meta_bariloche]:
            total_m = db.query(
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
            meta.monto_actual = total_m
        db.commit()

        # -------------------------------------------------------------
        # Recalcular perfil financiero y calibracion
        # -------------------------------------------------------------
        perfil_financiero_service.calcular_y_persistir_perfil(db, user.id)
        calibracion_service.calcular_y_guardar_calibracion_usuario(db, user.id, Moneda.ARS)
        db.commit()

        snap_despues = snapshot_cuentas_protegidas(db)
        cuentas_ajenas_modificadas = (snap_antes != snap_despues)

        t_total_seg = time.perf_counter() - t_inicio_total

        print("\n=== GENERACION COMPLETADA EXITOSAMENTE ===")
        print(f"TIEMPO TOTAL DE LA CORRIDA: {t_total_seg:.2f} s ({t_total_seg/60:.2f} min)")
        print(f"CUENTAS AJENAS MODIFICADAS: {'SI' if cuentas_ajenas_modificadas else 'NO'}")

    finally:
        db.close()

if __name__ == "__main__":
    regenerar_historial()
