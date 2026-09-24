"""
Diagnostico previo para testingadmin@argentum.com
"""
from __future__ import annotations

import sys
import os
from datetime import date
from decimal import Decimal

sys.path.insert(0, ".")
import logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("app").setLevel(logging.WARNING)

from sqlalchemy import text, func
from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.models.transaccion import Transaccion, TipoTransaccion
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta
from app.models.presupuesto import Presupuesto
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.suscripcion import Suscripcion
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.perfil_financiero import PerfilFinanciero
from app.services.proyeccion_service import calcular_proyeccion
from app.services.perfil_financiero_service import obtener_perfil
from app.utils.fecha import hoy_argentina

USUARIO_AUTORIZADO_EMAIL = "testingadmin@argentum.com"
USUARIO_AUTORIZADO_ID = "4c2ed62e-c22e-4d21-9bf9-ec6705f2c6fa"

def diagnostico():
    db = SessionLocal()
    try:
        user = db.query(Usuario).filter(Usuario.email == USUARIO_AUTORIZADO_EMAIL).first()
        if not user:
            raise RuntimeError(f"ABORT: Usuario {USUARIO_AUTORIZADO_EMAIL} no encontrado.")
        if str(user.id) != USUARIO_AUTORIZADO_ID:
            raise RuntimeError(f"ABORT: ID de usuario no coincide ({user.id} vs {USUARIO_AUTORIZADO_ID}).")

        print("=== DIAGNOSTICO PREVIO TESTINGADMIN ===")
        print(f"Usuario: {user.email} (ID: {user.id})")
        print(f"Fecha hoy_argentina(): {hoy_argentina()}")

        # 0.1 Conteos actuales
        cant_tx = db.query(func.count(Transaccion.id)).filter(Transaccion.usuario_id == user.id).scalar()
        cant_metas = db.query(func.count(Meta.id)).filter(Meta.usuario_id == user.id).scalar()
        cant_mov_metas = db.query(func.count(MovimientoMeta.id)).join(Meta).filter(Meta.usuario_id == user.id).scalar()
        cant_presupuestos = db.query(func.count(Presupuesto.id)).filter(Presupuesto.usuario_id == user.id).scalar()
        cant_periodos_pres = db.query(func.count(PeriodoPresupuesto.id)).join(Presupuesto).filter(Presupuesto.usuario_id == user.id).scalar()
        cant_grupos_cuotas = db.query(func.count(GrupoCuotas.id)).filter(GrupoCuotas.usuario_id == user.id).scalar()
        cant_cuotas = db.query(func.count(Cuota.id)).join(GrupoCuotas).filter(GrupoCuotas.usuario_id == user.id).scalar()
        cant_suscripciones = db.query(func.count(Suscripcion.id)).filter(Suscripcion.usuario_id == user.id).scalar()

        print("\n--- CONTEOS GENERALES ---")
        print(f"Transacciones: {cant_tx}")
        print(f"Metas: {cant_metas} (Movimientos de meta: {cant_mov_metas})")
        print(f"Presupuestos: {cant_presupuestos} (Periodos: {cant_periodos_pres})")
        print(f"Grupos de cuotas: {cant_grupos_cuotas} (Cuotas: {cant_cuotas})")
        print(f"Suscripciones: {cant_suscripciones}")

        # Transacciones: desglose por fechas y tags
        print("\n--- TRANSACCIONES DETALLE ---")
        min_max_date = db.query(func.min(Transaccion.fecha), func.max(Transaccion.fecha)).filter(Transaccion.usuario_id == user.id).first()
        print(f"Rango fechas transacciones: {min_max_date[0]} a {min_max_date[1]}")
        
        cant_historico = db.query(func.count(Transaccion.id)).filter(
            Transaccion.usuario_id == user.id,
            Transaccion.descripcion.like("%[Histórico]%")
        ).scalar()
        cant_no_historico = cant_tx - cant_historico
        print(f"Transacciones con '[Histórico]': {cant_historico}")
        print(f"Transacciones sin '[Histórico]': {cant_no_historico}")

        # Distribucion por mes
        print("\n--- TRANSACCIONES POR MES (Año-Mes | Total | Ingresos | Egresos) ---")
        rows_mes = db.execute(text("""
            SELECT 
                to_char(fecha, 'YYYY-MM') as mes,
                count(*) as total,
                count(case when tipo = 'ingreso' then 1 end) as ingresos,
                count(case when tipo = 'egreso' then 1 end) as egresos,
                sum(case when tipo = 'ingreso' then monto else 0 end) as total_ingreso,
                sum(case when tipo = 'egreso' then monto else 0 end) as total_egreso
            FROM transacciones
            WHERE usuario_id = :uid
            GROUP BY to_char(fecha, 'YYYY-MM')
            ORDER BY mes
        """), {"uid": user.id}).fetchall()
        for r in rows_mes:
            print(f"{r.mes}: Total={r.total} (Ing={r.ingresos}, Egr={r.egresos}) | Monto Ing=${r.total_ingreso:,.2f} | Monto Egr=${r.total_egreso:,.2f}")

        # Billeteras
        print("\n--- BILLETERAS ---")
        billeteras = db.query(Billetera).filter(Billetera.usuario_id == user.id).all()
        for b in billeteras:
            print(f"Billetera: {b.nombre} | ID: {b.id} | Moneda: {b.moneda.value} | Saldo: ${b.saldo_actual:,.2f} | Es Efectivo: {b.es_efectivo} | Es Inversion: {b.es_inversion} | TNA: {b.tna}")

        # Tarjetas
        print("\n--- TARJETAS ---")
        tarjetas = db.query(TarjetaCredito).filter(TarjetaCredito.usuario_id == user.id).all()
        for t in tarjetas:
            print(f"Tarjeta: {t.nombre} | Apodo: {t.apodo} | ID: {t.id} | Red: {t.red.value} | Cierre: {t.dia_cierre} | Vto: {t.dia_vencimiento} | Billetera ID: {t.billetera_id} | Percepcion USD: {t.percepcion_moneda_extranjera}%")

        # Metas
        print("\n--- METAS ---")
        metas = db.query(Meta).filter(Meta.usuario_id == user.id).all()
        for m in metas:
            print(f"Meta: {m.nombre} | ID: {m.id} | Obj: ${m.monto_objetivo:,.2f} | Actual: ${m.monto_actual:,.2f} | Moneda: {m.moneda.value} | Fecha limite: {m.fecha_limite} | Estado: {m.estado.value}")
            movs = db.query(MovimientoMeta).filter(MovimientoMeta.meta_id == m.id).order_by(MovimientoMeta.fecha).all()
            for mv in movs:
                print(f"   Mov: {mv.tipo.value} | Fecha: {mv.fecha} | Monto: ${mv.monto:,.2f} | Moneda: {mv.moneda_movimiento.value} | Billetera ID: {mv.billetera_id}")

        # Presupuestos
        print("\n--- PRESUPUESTOS ---")
        presupuestos = db.query(Presupuesto).filter(Presupuesto.usuario_id == user.id).all()
        for p in presupuestos:
            cats = db.query(PresupuestoCategoria).filter(PresupuestoCategoria.presupuesto_id == p.id).all()
            cat_info = ", ".join([f"Cat:{c.categoria_id} Sub:{c.subcategoria_id}" for c in cats])
            print(f"Presupuesto: {p.nombre} | ID: {p.id} | Monto: ${p.monto:,.2f} | Periodo: {p.periodo.value} | Categorias: [{cat_info}]")
            periodos = db.query(PeriodoPresupuesto).filter(PeriodoPresupuesto.presupuesto_id == p.id).order_by(PeriodoPresupuesto.fecha_inicio).all()
            for pr in periodos:
                print(f"   Periodo: {pr.fecha_inicio} a {pr.fecha_fin} | Limite: ${pr.monto_limite:,.2f} | Usado: ${pr.monto_usado:,.2f} | Superado: {pr.superado}")

        # Grupos de cuotas
        print("\n--- GRUPOS DE CUOTAS ---")
        gcs = db.query(GrupoCuotas).filter(GrupoCuotas.usuario_id == user.id).all()
        for g in gcs:
            cuotas_g = db.query(Cuota).filter(Cuota.grupo_id == g.id).order_by(Cuota.numero_cuota).all()
            pagadas = sum(1 for c in cuotas_g if c.pagada)
            print(f"GrupoCuotas: {g.descripcion} | ID: {g.id} | Total: ${g.monto_total:,.2f} | Cant: {g.cantidad_cuotas} (Pagadas: {pagadas}, Pendientes: {len(cuotas_g)-pagadas}) | Estado: {g.estado.value} | Tarjeta ID: {g.tarjeta_id}")
            for c in cuotas_g:
                m_real_str = f"${c.monto_real:,.2f}" if c.monto_real is not None else "None"
                print(f"   Cuota {c.numero_cuota}/{g.cantidad_cuotas}: Vto: {c.fecha_vencimiento} | Monto Proy: ${c.monto_proyectado:,.2f} | Monto Real: {m_real_str} | Pagada: {c.pagada}")

        # Suscripciones
        print("\n--- SUSCRIPCIONES ---")
        from app.models.historial_suscripcion import HistorialSuscripcion
        subs = db.query(Suscripcion).filter(Suscripcion.usuario_id == user.id).all()
        for s in subs:
            hists = db.query(HistorialSuscripcion).filter(HistorialSuscripcion.suscripcion_id == s.id).order_by(HistorialSuscripcion.vigente_desde).all()
            hist_str = ", ".join([f"${h.monto:,.2f} {h.moneda.value} desde {h.vigente_desde}" for h in hists])
            print(f"Suscripcion: {s.nombre} | ID: {s.id} | Frecuencia: {s.frecuencia.value} | Estado: {s.estado.value} | Prox cobro: {s.proximo_cobro} | Historial: [{hist_str}]")

        # Perfil y proyeccion
        print("\n--- PERFIL FINANCIERO Y PROYECCION ---")
        perfil = db.query(PerfilFinanciero).filter(PerfilFinanciero.usuario_id == user.id).first()
        if perfil:
            print(f"Perfil: Tasa Ahorro ARS={perfil.tasa_ahorro_ars}, Ratio Cuotas ARS={perfil.ratio_cuotas_ars}, Impulsividad ARS={perfil.score_impulsividad_ars}")
        else:
            print("Perfil: None")

        proy = calcular_proyeccion(db, user)
        if proy and "ars" in proy:
            p_ars = proy["ars"]
            pasa_calibracion = p_ars.get("pasa_calibracion", False) if isinstance(p_ars, dict) else getattr(p_ars, "pasa_calibracion", False)
            print(f"Proyeccion ARS pasa_calibracion: {pasa_calibracion}")
            if isinstance(p_ars, dict):
                print(f"Periodos proyectados: {len(p_ars.get('periodos', []))}")
                calib = p_ars.get('calibracion') or {}
                print(f"Detalle calibracion: pasa_puerta_3_ciclos={calib.get('pasa_puerta_3_ciclos')} pasa_puerta_6_ciclos={calib.get('pasa_puerta_6_ciclos')} total_evaluables={calib.get('total_ciclos_evaluables')}")
        else:
            print("Proyeccion ARS: None")

        # Detectar recurrentes
        print("\n--- DETECTOR DE RECURRENTES (STREAMS ACTUALES) ---")
        from app.utils.finanzas import clasificar_gastos, generar_ciclos_mensuales
        from app.models.tools import IPCCache
        txs_all = db.query(Transaccion).filter(Transaccion.usuario_id == user.id).all()
        ipc_records = db.query(IPCCache).all()
        ciclos = generar_ciclos_mensuales(date(2025, 8, 1), hoy_argentina())
        clasif = clasificar_gastos(
            transacciones=txs_all,
            ciclos=ciclos,
            ipc_records=ipc_records,
            fecha_destino=hoy_argentina(),
        )
        print(f"Comprometidos: {len(clasif.comprometidos)}")
        print(f"Habitos: {len(clasif.habitos)}")
        print(f"Streams detectados: {len(clasif.streams)}")
        for s in clasif.streams:
            print(f"   Stream: {s.descripcion} | Cat: {s.categoria} | Clase: {s.clase} | Frec: {s.frecuencia} | Estado: {s.estado} | Ocurr: {s.cantidad_ocurrencias} | Monto Mediano: ${s.monto_mediano_deflactado:,.2f}")

    finally:
        db.close()

if __name__ == "__main__":
    diagnostico()
