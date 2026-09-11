"""
Script de auditoría de solo lectura sobre la base de datos de Argentum.
Escribe su reporte exclusivamente en scripts/backups/auditoria_salida.txt.

Garantías de seguridad:
- Motor independiente con SET TRANSACTION READ ONLY en cada transacción.
- Rollback incondicional al finalizar cada bloque, commit bloqueado a nivel clase.
- Sin configuración a nivel de sesión (prohibido SET SESSION para evitar contaminar el pooler).
- Autoprueba inicial con CREATE TEMP TABLE que DEBE fallar para proceder.
- Sin importación de app.main ni componentes que inicialicen schedulers o jobs.
"""
from __future__ import annotations

import os
import sys
import time
import json
import traceback
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

# Asegurar path de ejecución en la raíz de argentum-backend
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Silenciar logs para que no interfieran ni se mezclen con el reporte
os.environ["LOG_LEVEL"] = "WARNING"
import logging
logging.disable(logging.WARNING)

from sqlalchemy import create_engine, event, select, text, func, or_, and_, case
from sqlalchemy.orm import sessionmaker, Session, joinedload

from app.core.config import settings
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.tarjeta_credito import TarjetaCredito
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.tools import IPCCache
from app.models.perfil_financiero import PerfilFinanciero

from app.utils.fecha import hoy_argentina, ahora_argentina
from app.utils.finanzas import (
    es_gasto_consumo,
    es_pago_resumen,
    es_aporte_meta,
    es_transferencia,
)
from app.services.analisis_financiero_service import (
    calcular_proyeccion_nueva,
    calcular_perfil_nuevo,
)
from app.services.proyeccion_service import calcular_proyeccion
from app.services.perfil_financiero_service import obtener_perfil
from scripts.seed_categorias import CATEGORIAS_SEED

OUTPUT_PATH = os.path.join(BASE_DIR, "scripts", "backups", "auditoria_salida.txt")

# ==============================================================================
# CONFIGURACIÓN DEL MOTOR DE SOLO LECTURA
# ==============================================================================
audit_engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
)

# Contador de consultas SQL para la sección R
sql_query_count = [0]

@event.listens_for(audit_engine, "before_cursor_execute")
def _intercept_sql_count(conn, cursor, statement, parameters, context, executemany):
    sql_query_count[0] += 1

@event.listens_for(audit_engine, "begin")
def _enforce_tx_read_only(conn):
    # SET TRANSACTION READ ONLY dura exactamente una transacción,
    # nunca contamina la sesión física en Supabase Transaction Pooler (puerto 6543).
    conn.execute(text("SET TRANSACTION READ ONLY"))


class ReadOnlySession(Session):
    def commit(self):
        raise RuntimeError("OPERACIÓN BLOQUEADA: commit() está prohibido en auditoría de solo lectura.")


AuditSessionFactory = sessionmaker(
    bind=audit_engine,
    class_=ReadOnlySession,
    autocommit=False,
    autoflush=False,
)


@contextmanager
def session_scope():
    """Maneja el ciclo de vida de una sesión asegurando rollback y cierre."""
    session = AuditSessionFactory()
    try:
        yield session
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        session.close()


def normalizar_texto(txt: str | None) -> str:
    if not txt:
        return ""
    norm = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


def formatear_monto(val: Decimal | float | None) -> str:
    if val is None:
        return "$0.00"
    if isinstance(val, float):
        val = Decimal(str(val))
    return f"${val:,.2f}"


# ==============================================================================
# AUDITORÍA PRINCIPAL
# ==============================================================================
def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    out_file = open(OUTPUT_PATH, "w", encoding="utf-8")

    def log(msg: str = ""):
        out_file.write(f"{msg}\n")
        out_file.flush()

    log("=" * 80)
    log("REPORTE DE AUDITORÍA DE SOLO LECTURA — ARGENTUM")
    log(f"Fecha de ejecución: {ahora_argentina().isoformat()} (ART)")
    log("=" * 80)
    log()

    # --------------------------------------------------------------------------
    # AUTOPRUEBA DE SOLO LECTURA
    # --------------------------------------------------------------------------
    log("=== AUTOPRUEBA DE SOLO LECTURA ===")
    autoprueba_exitosa = False
    try:
        with audit_engine.connect() as conn:
            with conn.begin():
                # En after_begin se ejecuta SET TRANSACTION READ ONLY
                conn.execute(text("CREATE TEMP TABLE _prueba_solo_lectura_auditoria (id int)"))
    except Exception as e:
        # Falló la creación de tabla: PostgreSQL rechazó la escritura en la transacción de solo lectura
        autoprueba_exitosa = True
        log("SOLO LECTURA VERIFICADO")
        log(f"Confirmación técnica del rechazo: {type(e).__name__} - {str(e).strip()[:160]}")
    
    if not autoprueba_exitosa:
        log("FALLÓ AUTOPRUEBA: LA BASE PERMITE ESCRITURA. ABORTANDO INMEDIATAMENTE.")
        out_file.close()
        sys.exit(1)

    log()

    # --------------------------------------------------------------------------
    # SECCIÓN A: CANARIO
    # --------------------------------------------------------------------------
    log("=== SECCIÓN A: CANARIO DE USUARIOS ===")
    canario_env = os.environ.get("CANARIO_USUARIOS_EMAILS")
    if canario_env:
        usuarios_esperados = {e.strip() for e in canario_env.split(",") if e.strip()}
    else:
        usuarios_esperados = {
            "usuario1@argentum.test",
            "usuario2@argentum.test",
            "usuario3@argentum.test",
            "usuario4@argentum.test",
            "usuario5@argentum.test",
            "usuario6@argentum.test",
            "testingadmin@argentum.test",
        }
    try:
        with session_scope() as db:
            users = db.execute(select(Usuario).order_by(Usuario.email)).scalars().all()
            emails_en_db = set()
            for u in users:
                emails_en_db.add(u.email)
                tx_count = db.execute(
                    select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)
                ).scalar() or 0
                auth_prov = u.auth_provider.value if hasattr(u.auth_provider, "value") else str(u.auth_provider)
                log(f"Usuario: {u.email:<35} | Auth Provider: {auth_prov:<10} | Transacciones: {tx_count:>6}")

            if emails_en_db == usuarios_esperados:
                log("\nCANARIO OK")
            else:
                faltantes = usuarios_esperados - emails_en_db
                sobrantes = emails_en_db - usuarios_esperados
                log(f"\nCANARIO NO COINCIDE (Faltantes: {faltantes}, Sobrantes: {sobrantes})")
    except Exception as e:
        log(f"ERROR EN SECCIÓN A:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN B: CATÁLOGO
    # --------------------------------------------------------------------------
    log("=== SECCIÓN B: CATÁLOGO DE CATEGORÍAS Y SUBCATEGORÍAS ===")
    try:
        canonical_map: dict[tuple[str, str], set[str]] = {}
        for item in CATEGORIAS_SEED:
            cat_nom = item["nombre"].strip()
            cat_tipo = item["tipo"].strip().lower()
            canonical_map[(cat_nom, cat_tipo)] = set(item["subcategorias"])

        with session_scope() as db:
            cats = db.execute(
                select(Categoria).where(Categoria.estado == "activa").order_by(Categoria.tipo, Categoria.nombre)
            ).scalars().all()
            subcats = db.execute(
                select(Subcategoria).join(Categoria).where(Subcategoria.estado == "activa", Categoria.estado == "activa").order_by(Categoria.nombre, Subcategoria.nombre)
            ).scalars().all()

            log(f"Categorías activas en BD: {len(cats)}")
            log(f"Subcategorías activas en BD: {len(subcats)}")

            subcats_otros = [s for s in subcats if normalizar_texto(s.nombre) == "otros"]
            if subcats_otros:
                log(f"ALERTA: Se encontraron {len(subcats_otros)} subcategorías llamadas 'Otros':")
                for s in subcats_otros:
                    log(f"  - Subcategoría ID {s.id} en categoría ID {s.categoria_id}")
            else:
                log("Subcategorías llamadas 'Otros': NINGUNA")

            # Comparar contra canónico
            no_canonicas = []
            for s in subcats:
                cat = next((c for c in cats if c.id == s.categoria_id), None)
                if not cat:
                    no_canonicas.append(f"Subcategoría {s.nombre} vinculada a categoría inexistente o inactiva ({s.categoria_id})")
                    continue
                k = (cat.nombre.strip(), cat.tipo.strip().lower())
                if k not in canonical_map:
                    no_canonicas.append(f"Categoría no canónica: {cat.nombre} ({cat.tipo}) -> Subcategoría: {s.nombre}")
                elif s.nombre not in canonical_map[k]:
                    no_canonicas.append(f"Subcategoría no canónica en {cat.nombre} ({cat.tipo}): '{s.nombre}'")

            if no_canonicas:
                log(f"Subcategorías fuera del catálogo canónico ({len(no_canonicas)}):")
                for nc in no_canonicas:
                    log(f"  - {nc}")
            else:
                log("Todas las subcategorías activas coinciden estrictamente con el catálogo canónico oficial.")
    except Exception as e:
        log(f"ERROR EN SECCIÓN B:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN C: TESTINGADMIN MES POR MES
    # --------------------------------------------------------------------------
    log("=== SECCIÓN C: TESTINGADMIN MES POR MES ===")
    try:
        with session_scope() as db:
            u_test = db.execute(
                select(Usuario).where(Usuario.email == "testingadmin@argentum.com")
            ).scalar_one_or_none()

            if not u_test:
                log("testingadmin@argentum.com no existe en la base.")
            else:
                txs = db.execute(
                    select(Transaccion)
                    .options(
                        joinedload(Transaccion.categoria),
                        joinedload(Transaccion.subcategoria),
                        joinedload(Transaccion.billetera),
                    )
                    .where(Transaccion.usuario_id == u_test.id)
                    .order_by(Transaccion.fecha, Transaccion.fecha_creacion)
                ).scalars().all()

                if not txs:
                    log("Sin transacciones registradas para testingadmin.")
                else:
                    meses_dict: dict[str, list[Transaccion]] = {}
                    for tx in txs:
                        m_key = tx.fecha.strftime("%Y-%m")
                        meses_dict.setdefault(m_key, []).append(tx)

                    log(f"{'Mes':<7} | {'Movs':>5} | {'Ingresos':>14} | {'Gasto Motor':>14} | {'% Gasto':>8} | {'Ahorro':>14} | {'Cons. Tarj':>12} | {'Pagos Res':>12} | {'Aportes Meta':>12} | {'Transf':>12} | Estado")
                    log("-" * 140)

                    for m_key in sorted(meses_dict.keys()):
                        m_txs = meses_dict[m_key]
                        cant = len(m_txs)

                        # Ingresos confirmados
                        ing = sum((t.monto for t in m_txs if t.tipo == TipoTransaccion.INGRESO and t.estado_verificacion in (None, EstadoVerificacionTransaccion.CONFIRMADA)), Decimal("0.00"))

                        # Gasto según el motor (app/utils/finanzas.py: es_gasto_consumo)
                        gasto_motor = sum((t.monto for t in m_txs if es_gasto_consumo(t)), Decimal("0.00"))

                        pct = (gasto_motor / ing * Decimal("100")) if ing > 0 else Decimal("0.00")
                        ahorro = ing - gasto_motor

                        # Métricas complementarias por separado
                        # Consumos con tarjeta por fecha de compra (egresos tarjeta que no son pago resumen)
                        consumos_tarj = sum((t.monto for t in m_txs if t.tipo == TipoTransaccion.EGRESO and (t.metodo_pago == MetodoPago.CREDITO or t.tarjeta_id is not None) and not es_pago_resumen(t)), Decimal("0.00"))

                        pagos_resumen = sum((t.monto for t in m_txs if t.tipo == TipoTransaccion.EGRESO and es_pago_resumen(t)), Decimal("0.00"))
                        aportes_meta = sum((t.monto for t in m_txs if es_aporte_meta(t)), Decimal("0.00"))
                        transf = sum((t.monto for t in m_txs if es_transferencia(t)), Decimal("0.00"))

                        alerta = ""
                        if ing > 0:
                            if pct < Decimal("75.00") or pct > Decimal("90.00"):
                                alerta = f"[FUERA 75-90%: {pct:.1f}%]"
                        else:
                            alerta = "[SIN INGRESO]"

                        log(f"{m_key:<7} | {cant:>5} | {formatear_monto(ing):>14} | {formatear_monto(gasto_motor):>14} | {pct:>7.2f}% | {formatear_monto(ahorro):>14} | {formatear_monto(consumos_tarj):>12} | {formatear_monto(pagos_resumen):>12} | {formatear_monto(aportes_meta):>12} | {formatear_monto(transf):>12} | {alerta}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN C:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN D: PROMEDIO MENSUAL POR CATEGORÍA Y SUBCATEGORÍA
    # --------------------------------------------------------------------------
    log("=== SECCIÓN D: TESTINGADMIN PROMEDIO MENSUAL POR CATEGORÍA Y SUBCATEGORÍA (SIN MES EN CURSO) ===")
    try:
        with session_scope() as db:
            u_test = db.execute(
                select(Usuario).where(Usuario.email == "testingadmin@argentum.com")
            ).scalar_one_or_none()
            if u_test:
                mes_actual = hoy_argentina().strftime("%Y-%m")
                txs = db.execute(
                    select(Transaccion)
                    .options(
                        joinedload(Transaccion.categoria),
                        joinedload(Transaccion.subcategoria),
                    )
                    .where(Transaccion.usuario_id == u_test.id, Transaccion.fecha < date(int(mes_actual[:4]), int(mes_actual[5:7]), 1))
                ).scalars().all()

                # Meses cerrados con datos
                meses_cerrados = sorted({t.fecha.strftime("%Y-%m") for t in txs})
                n_meses = len(meses_cerrados)
                log(f"Mes en curso excluido: {mes_actual}")
                log(f"Cantidad de meses cerrados evaluados: {n_meses} ({', '.join(meses_cerrados)})")

                # Ingreso promedio en meses cerrados
                total_ingresos_cerrados = sum(
                    (t.monto for t in txs if t.tipo == TipoTransaccion.INGRESO and t.estado_verificacion in (None, EstadoVerificacionTransaccion.CONFIRMADA)),
                    Decimal("0.00")
                )
                ingreso_promedio_mensual = (total_ingresos_cerrados / Decimal(n_meses)) if n_meses > 0 else Decimal("0.00")
                log(f"Ingreso total cerrado: {formatear_monto(total_ingresos_cerrados)} | Ingreso mensual promedio: {formatear_monto(ingreso_promedio_mensual)}\n")

                # Agrupar gastos según el motor
                gastos_por_cat: dict[str, Decimal] = {}
                gastos_por_subcat: dict[tuple[str, str], Decimal] = {}

                for t in txs:
                    if not es_gasto_consumo(t):
                        continue
                    cat_nom = t.categoria.nombre if t.categoria else "Sin categoría"
                    sub_nom = t.subcategoria.nombre if t.subcategoria else "Sin subcategoría"

                    gastos_por_cat[cat_nom] = gastos_por_cat.get(cat_nom, Decimal("0.00")) + t.monto
                    gastos_por_subcat[(cat_nom, sub_nom)] = gastos_por_subcat.get((cat_nom, sub_nom), Decimal("0.00")) + t.monto

                log(f"{'Categoría / Subcategoría':<45} | {'Total Período':>15} | {'Promedio Mensual':>16} | {'% del Ingreso':>14}")
                log("-" * 98)

                for cat_nom in sorted(gastos_por_cat.keys(), key=lambda c: gastos_por_cat[c], reverse=True):
                    tot_cat = gastos_por_cat[cat_nom]
                    prom_cat = (tot_cat / Decimal(n_meses)) if n_meses > 0 else Decimal("0.00")
                    pct_cat = (prom_cat / ingreso_promedio_mensual * Decimal("100")) if ingreso_promedio_mensual > 0 else Decimal("0.00")
                    log(f"[CAT] {cat_nom:<39} | {formatear_monto(tot_cat):>15} | {formatear_monto(prom_cat):>16} | {pct_cat:>13.2f}%")

                    # Subcategorías de esta categoría
                    subs_de_cat = [k for k in gastos_por_subcat.keys() if k[0] == cat_nom]
                    for _, sub_nom in sorted(subs_de_cat, key=lambda k: gastos_por_subcat[k], reverse=True):
                        tot_sub = gastos_por_subcat[(cat_nom, sub_nom)]
                        prom_sub = (tot_sub / Decimal(n_meses)) if n_meses > 0 else Decimal("0.00")
                        pct_sub = (prom_sub / ingreso_promedio_mensual * Decimal("100")) if ingreso_promedio_mensual > 0 else Decimal("0.00")
                        log(f"      - {sub_nom:<37} | {formatear_monto(tot_sub):>15} | {formatear_monto(prom_sub):>16} | {pct_sub:>13.2f}%")
                    log()
    except Exception as e:
        log(f"ERROR EN SECCIÓN D:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN E: TODOS LOS INGRESOS DE TESTINGADMIN
    # --------------------------------------------------------------------------
    log("=== SECCIÓN E: TESTINGADMIN TODOS LOS INGRESOS ===")
    try:
        with session_scope() as db:
            u_test = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
            if u_test:
                ingresos = db.execute(
                    select(Transaccion)
                    .options(
                        joinedload(Transaccion.categoria),
                        joinedload(Transaccion.subcategoria),
                    )
                    .where(Transaccion.usuario_id == u_test.id, Transaccion.tipo == TipoTransaccion.INGRESO)
                    .order_by(Transaccion.fecha, Transaccion.fecha_creacion)
                ).scalars().all()

                log(f"Total ingresos registrados: {len(ingresos)}")
                log(f"{'Fecha':<10} | {'Monto':>14} | {'Categoría':<22} | {'Subcategoría':<22} | Descripción")
                log("-" * 110)
                for t in ingresos:
                    cat = t.categoria.nombre if t.categoria else "Sin categoría"
                    sub = t.subcategoria.nombre if t.subcategoria else "Sin subcategoría"
                    log(f"{t.fecha.isoformat():<10} | {formatear_monto(t.monto):>14} | {cat:<22} | {sub:<22} | {t.descripcion}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN E:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN F: MOVIMIENTOS CON "ALQUILER"
    # --------------------------------------------------------------------------
    log("=== SECCIÓN F: TESTINGADMIN MOVIMIENTOS CON 'ALQUILER' ===")
    try:
        with session_scope() as db:
            u_test = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
            if u_test:
                txs = db.execute(
                    select(Transaccion)
                    .options(
                        joinedload(Transaccion.categoria),
                        joinedload(Transaccion.subcategoria),
                    )
                    .where(Transaccion.usuario_id == u_test.id)
                    .order_by(Transaccion.fecha)
                ).scalars().all()

                alquileres = [t for t in txs if "alquiler" in normalizar_texto(t.descripcion)]
                log(f"Total movimientos encontrados con 'alquiler': {len(alquileres)}")
                log(f"{'Fecha':<10} | {'Monto':>14} | {'Categoría':<22} | {'Subcategoría':<25} | Descripción")
                log("-" * 115)
                for t in alquileres:
                    cat = t.categoria.nombre if t.categoria else "Sin categoría"
                    sub = t.subcategoria.nombre if t.subcategoria else "Sin subcategoría"
                    log(f"{t.fecha.isoformat():<10} | {formatear_monto(t.monto):>14} | {cat:<22} | {sub:<25} | {t.descripcion}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN F:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN G: 15 GASTOS MÁS GRANDES
    # --------------------------------------------------------------------------
    log("=== SECCIÓN G: TESTINGADMIN LOS 15 GASTOS MÁS GRANDES (SEGÚN MOTOR) ===")
    try:
        with session_scope() as db:
            u_test = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
            if u_test:
                txs = db.execute(
                    select(Transaccion)
                    .options(
                        joinedload(Transaccion.categoria),
                        joinedload(Transaccion.subcategoria),
                    )
                    .where(Transaccion.usuario_id == u_test.id)
                ).scalars().all()

                gastos_motor = [t for t in txs if es_gasto_consumo(t)]
                gastos_motor.sort(key=lambda t: t.monto, reverse=True)
                top15 = gastos_motor[:15]

                log(f"{'Ranking':<8} | {'Fecha':<10} | {'Monto':>14} | {'Categoría':<22} | Descripción")
                log("-" * 95)
                for idx, t in enumerate(top15, start=1):
                    cat = t.categoria.nombre if t.categoria else "Sin categoría"
                    log(f"#{idx:<7} | {t.fecha.isoformat():<10} | {formatear_monto(t.monto):>14} | {cat:<22} | {t.descripcion}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN G:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN H: MOVIMIENTOS POSTERIORES A HOY_ARGENTINA()
    # --------------------------------------------------------------------------
    log("=== SECCIÓN H: TESTINGADMIN MOVIMIENTOS CON FECHA POSTERIOR A HOY ===")
    hoy = hoy_argentina()
    try:
        with session_scope() as db:
            u_test = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
            if u_test:
                txs_futuras = db.execute(
                    select(Transaccion)
                    .where(Transaccion.usuario_id == u_test.id, Transaccion.fecha > hoy)
                    .order_by(Transaccion.fecha)
                ).scalars().all()

                log(f"Fecha de referencia hoy_argentina(): {hoy.isoformat()}")
                log(f"Movimientos con fecha > {hoy.isoformat()}: {len(txs_futuras)}")
                if txs_futuras:
                    log(f"{'Fecha':<10} | {'Monto':>14} | {'¿Grupo Cuotas?':<16} | {'¿Cuota Hija?':<14} | Descripción")
                    log("-" * 95)
                    for t in txs_futuras:
                        es_grupo = "SÍ" if t.grupo_cuotas_id else "NO"
                        es_hija = "SÍ" if t.es_cuota_hija else "NO"
                        log(f"{t.fecha.isoformat():<10} | {formatear_monto(t.monto):>14} | {es_grupo:<16} | {es_hija:<14} | {t.descripcion}")
                else:
                    log("Ningún movimiento con fecha posterior a hoy.")
    except Exception as e:
        log(f"ERROR EN SECCIÓN H:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN I: GRUPOS DE CUOTAS
    # --------------------------------------------------------------------------
    log("=== SECCIÓN I: TESTINGADMIN GRUPOS DE CUOTAS ===")
    try:
        with session_scope() as db:
            u_test = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
            if u_test:
                gcs = db.execute(
                    select(GrupoCuotas)
                    .options(
                        joinedload(GrupoCuotas.tarjeta),
                        joinedload(GrupoCuotas.cuotas),
                    )
                    .where(GrupoCuotas.usuario_id == u_test.id)
                    .order_by(GrupoCuotas.fecha_creacion)
                ).scalars().unique().all()

                log(f"Total grupos de cuotas: {len(gcs)}")
                log(f"{'Descripción':<35} | {'Tarjeta':<16} | {'Total Plan':>14} | {'Pag':>4} | {'Pend':>4} | {'Suma Cuotas':>14} | Estado")
                log("-" * 115)

                for g in gcs:
                    t_nom = g.tarjeta.nombre if g.tarjeta else "Sin tarjeta"
                    pagadas = sum(1 for c in g.cuotas if c.pagada)
                    pendientes = sum(1 for c in g.cuotas if not c.pagada)
                    suma_montos = sum((c.monto_real if (c.pagada and c.monto_real is not None) else c.monto_proyectado for c in g.cuotas), Decimal("0.00"))

                    cierra = True
                    motivos = []
                    if len(g.cuotas) != g.cantidad_cuotas:
                        cierra = False
                        motivos.append(f"cant_cuotas {len(g.cuotas)}!={g.cantidad_cuotas}")
                    if pagadas + pendientes != len(g.cuotas):
                        cierra = False
                        motivos.append("pagadas+pendientes!=total")
                    if abs(suma_montos - g.monto_total) > Decimal("0.05"):
                        cierra = False
                        motivos.append(f"suma ${suma_montos} != total ${g.monto_total}")

                    estado_str = "OK" if cierra else f"[NO CIERRA: {', '.join(motivos)}]"
                    log(f"{g.descripcion[:34]:<35} | {t_nom[:15]:<16} | {formatear_monto(g.monto_total):>14} | {pagadas:>4} | {pendientes:>4} | {formatear_monto(suma_montos):>14} | {estado_str}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN I:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN J: SUSCRIPCIONES ACTIVAS
    # --------------------------------------------------------------------------
    log("=== SECCIÓN J: TESTINGADMIN SUSCRIPCIONES ACTIVAS ===")
    try:
        with session_scope() as db:
            u_test = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
            if u_test:
                subs = db.execute(
                    select(Suscripcion)
                    .options(joinedload(Suscripcion.historial))
                    .where(Suscripcion.usuario_id == u_test.id, Suscripcion.estado == EstadoSuscripcion.ACTIVA)
                    .order_by(Suscripcion.nombre)
                ).scalars().unique().all()

                log(f"Suscripciones activas: {len(subs)}")
                log(f"{'Nombre':<25} | {'Monto':>14} | {'Frecuencia':<12} | Próximo Cobro")
                log("-" * 75)
                for s in subs:
                    # Último monto del historial
                    hists_sorted = sorted(s.historial, key=lambda h: h.vigente_desde, reverse=True)
                    monto = hists_sorted[0].monto if hists_sorted else Decimal("0.00")
                    frec = s.frecuencia.value if hasattr(s.frecuencia, "value") else str(s.frecuencia)
                    log(f"{s.nombre:<25} | {formatear_monto(monto):>14} | {frec:<12} | {s.proximo_cobro.isoformat()}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN J:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN K: METAS
    # --------------------------------------------------------------------------
    log("=== SECCIÓN K: TESTINGADMIN METAS (PROGRESO GUARDADO VS SUMA MOVIMIENTOS) ===")
    try:
        with session_scope() as db:
            u_test = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
            if u_test:
                metas = db.execute(
                    select(Meta).where(Meta.usuario_id == u_test.id).order_by(Meta.nombre)
                ).scalars().all()

                log(f"Total metas: {len(metas)}")
                log(f"{'Nombre Meta':<28} | {'Guardado':>15} | {'Suma Movs':>15} | {'Diferencia':>15} | Estado")
                log("-" * 92)
                for m in metas:
                    movs = db.execute(
                        select(MovimientoMeta).where(MovimientoMeta.meta_id == m.id)
                    ).scalars().all()
                    aportes = sum((mov.monto for mov in movs if mov.tipo == TipoMovimientoMeta.APORTE), Decimal("0.00"))
                    retiros = sum((mov.monto for mov in movs if mov.tipo == TipoMovimientoMeta.RETIRO), Decimal("0.00"))
                    suma_calc = aportes - retiros
                    diff = m.monto_actual - suma_calc
                    coincide = (diff == Decimal("0.00"))
                    estado_str = "OK" if coincide else f"[DESCUADRE: {formatear_monto(diff)}]"
                    log(f"{m.nombre:<28} | {formatear_monto(m.monto_actual):>15} | {formatear_monto(suma_calc):>15} | {formatear_monto(diff):>15} | {estado_str}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN K:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN L: INTEGRIDAD REFERENCIAL (TODOS LOS USUARIOS)
    # --------------------------------------------------------------------------
    log("=== SECCIÓN L: INTEGRIDAD REFERENCIAL (TODOS LOS USUARIOS) ===")
    try:
        with session_scope() as db:
            # 1. Cuotas sin grupo
            cuotas_huerfanas = db.execute(
                select(Cuota).outerjoin(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id).where(GrupoCuotas.id.is_(None))
            ).scalars().all()
            log(f"1. Cuotas sin grupo: {len(cuotas_huerfanas)}")
            for c in cuotas_huerfanas:
                log(f"   Cuota ID {c.id} con grupo_id huérfano {c.grupo_id}")

            # 2. Grupos sin cuotas
            grupos_vacios = db.execute(
                select(GrupoCuotas).outerjoin(Cuota, GrupoCuotas.id == Cuota.grupo_id).where(Cuota.id.is_(None))
            ).scalars().all()
            log(f"2. Grupos sin cuotas: {len(grupos_vacios)}")
            for g in grupos_vacios:
                log(f"   Grupo ID {g.id} ({g.descripcion}) de usuario {g.usuario_id}")

            # 3. Cuotas cuyo movimiento no existe
            cuotas_tx_fantasma = db.execute(
                select(Cuota).outerjoin(Transaccion, Cuota.transaccion_id == Transaccion.id).where(Cuota.transaccion_id.is_not(None), Transaccion.id.is_(None))
            ).scalars().all()
            log(f"3. Cuotas cuya transacción asociada no existe: {len(cuotas_tx_fantasma)}")
            for c in cuotas_tx_fantasma:
                log(f"   Cuota ID {c.id} con transaccion_id huérfana {c.transaccion_id}")

            # 4. Movimientos de meta sin transacción vinculada
            movs_meta_sin_tx = db.execute(
                select(MovimientoMeta).outerjoin(Transaccion, MovimientoMeta.id == Transaccion.movimiento_meta_id).where(Transaccion.id.is_(None))
            ).scalars().all()
            log(f"4. Movimientos de meta sin transacción vinculada: {len(movs_meta_sin_tx)}")
            for m in movs_meta_sin_tx:
                log(f"   MovimientoMeta ID {m.id} (Meta: {m.meta_id}, Monto: {m.monto})")

            # 5. Transferencias con una sola pata
            b_ids = select(Billetera.id).scalar_subquery()
            tr_invalidas = db.execute(
                select(TransferenciaInterna).where(
                    or_(
                        TransferenciaInterna.billetera_origen_id.not_in(b_ids),
                        TransferenciaInterna.billetera_destino_id.not_in(b_ids),
                        TransferenciaInterna.billetera_origen_id == TransferenciaInterna.billetera_destino_id
                    )
                )
            ).scalars().all()
            log(f"5. Transferencias internas inválidas (una sola pata / misma billetera / inexistente): {len(tr_invalidas)}")
            for tr in tr_invalidas:
                log(f"   Transferencia ID {tr.id} (Origen: {tr.billetera_origen_id}, Destino: {tr.billetera_destino_id})")

            # 6. Movimientos cuya billetera, tarjeta, categoría o subcategoría es de otro usuario o no existe
            tx_todos = db.execute(
                select(Transaccion)
                .options(
                    joinedload(Transaccion.billetera),
                    joinedload(Transaccion.tarjeta),
                    joinedload(Transaccion.categoria),
                    joinedload(Transaccion.subcategoria),
                )
            ).scalars().all()

            inconsistencias_tx = []
            for t in tx_todos:
                if not t.billetera or t.billetera.usuario_id != t.usuario_id:
                    inconsistencias_tx.append(f"Tx {t.id}: Billetera inexistente o de otro usuario ({t.billetera_id})")
                if t.tarjeta_id and (not t.tarjeta or t.tarjeta.usuario_id != t.usuario_id):
                    inconsistencias_tx.append(f"Tx {t.id}: Tarjeta inexistente o de otro usuario ({t.tarjeta_id})")
                if t.categoria_id and not t.categoria:
                    inconsistencias_tx.append(f"Tx {t.id}: Categoría inexistente ({t.categoria_id})")
                if t.subcategoria_id:
                    if not t.subcategoria:
                        inconsistencias_tx.append(f"Tx {t.id}: Subcategoría inexistente ({t.subcategoria_id})")
                    elif t.categoria_id and t.subcategoria.categoria_id != t.categoria_id:
                        inconsistencias_tx.append(f"Tx {t.id}: Subcategoría {t.subcategoria.nombre} no pertenece a categoría {t.categoria.nombre if t.categoria else t.categoria_id}")

            log(f"6. Transacciones con inconsistencias de usuario/entidad: {len(inconsistencias_tx)}")
            for inc in inconsistencias_tx:
                log(f"   {inc}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN L:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN M: DUPLICADOS (TODOS LOS USUARIOS)
    # --------------------------------------------------------------------------
    log("=== SECCIÓN M: DUPLICADOS (MISMO USUARIO, FECHA, MONTO, DESCRIPCIÓN, BILLETERA) ===")
    try:
        with session_scope() as db:
            dups_stmt = (
                select(
                    Transaccion.usuario_id,
                    Transaccion.fecha,
                    Transaccion.monto,
                    Transaccion.descripcion,
                    Transaccion.billetera_id,
                    func.count(Transaccion.id).label("cnt")
                )
                .group_by(
                    Transaccion.usuario_id,
                    Transaccion.fecha,
                    Transaccion.monto,
                    Transaccion.descripcion,
                    Transaccion.billetera_id
                )
                .having(func.count(Transaccion.id) > 1)
            )
            dups_groups = db.execute(dups_stmt).all()

            u_map = {u.id: u.email for u in db.execute(select(Usuario)).scalars().all()}
            dups_by_user: dict[str, int] = {}
            for row in dups_groups:
                email = u_map.get(row.usuario_id, str(row.usuario_id))
                dups_by_user[email] = dups_by_user.get(email, 0) + (row.cnt - 1)

            log("Cantidad de transacciones duplicadas por usuario:")
            for email in sorted(u_map.values()):
                cnt = dups_by_user.get(email, 0)
                log(f"  {email:<35}: {cnt} duplicadas")

            # Filas completas para testingadmin y las originadas por jobs
            log("\nDetalle de transacciones duplicadas para testingadmin o creadas por jobs:")
            u_test = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
            test_uid = u_test.id if u_test else None

            for row in dups_groups:
                es_test = (row.usuario_id == test_uid)
                # Buscar filas completas del grupo
                filas = db.execute(
                    select(Transaccion).where(
                        Transaccion.usuario_id == row.usuario_id,
                        Transaccion.fecha == row.fecha,
                        Transaccion.monto == row.monto,
                        Transaccion.descripcion == row.descripcion,
                        Transaccion.billetera_id == row.billetera_id
                    ).order_by(Transaccion.fecha_creacion)
                ).scalars().all()

                es_job = any(f.origen == OrigenTransaccion.RECURRENTE for f in filas)
                if es_test or es_job:
                    email_g = u_map.get(row.usuario_id, str(row.usuario_id))
                    log(f"\nGrupo duplicado ({len(filas)} filas) | Usuario: {email_g} | Fecha: {row.fecha} | Monto: {formatear_monto(row.monto)} | Desc: '{row.descripcion}'")
                    for f in filas:
                        log(f"  ID: {f.id} | Creado: {f.fecha_creacion.isoformat()} | Origen: {f.origen.value} | Verif: {f.estado_verificacion.value if f.estado_verificacion else 'NULL'}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN M:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN N: CONTAMINACIÓN DEL SEED
    # --------------------------------------------------------------------------
    log("=== SECCIÓN N: CONTAMINACIÓN DEL SEED ===")
    try:
        with session_scope() as db:
            users = db.execute(select(Usuario).order_by(Usuario.email)).scalars().all()

            log("Transacciones con prefijo '[Histórico]' por usuario:")
            for u in users:
                cnt_seed = db.execute(
                    select(func.count(Transaccion.id)).where(
                        Transaccion.usuario_id == u.id,
                        Transaccion.descripcion.like("%[Histórico]%")
                    )
                ).scalar() or 0
                log(f"  {u.email:<35}: {cnt_seed:>6} transacciones")

            # Para testingadmin: transacciones que NO tienen '[Histórico]' agrupadas por origen y mes
            u_test = next((u for u in users if u.email == "testingadmin@argentum.com"), None)
            if u_test:
                log("\ntestingadmin — Movimientos SIN '[Histórico]' agrupados por origen y mes:")
                txs_no_seed = db.execute(
                    select(Transaccion).where(
                        Transaccion.usuario_id == u_test.id,
                        ~Transaccion.descripcion.like("%[Histórico]%")
                    ).order_by(Transaccion.fecha)
                ).scalars().all()

                log(f"Total movimientos sin '[Histórico]' en testingadmin: {len(txs_no_seed)}")
                if txs_no_seed:
                    agrupado_no_seed: dict[tuple[str, str], list[Transaccion]] = {}
                    for t in txs_no_seed:
                        m_key = t.fecha.strftime("%Y-%m")
                        o_key = t.origen.value if hasattr(t.origen, "value") else str(t.origen)
                        agrupado_no_seed.setdefault((o_key, m_key), []).append(t)

                    log(f"{'Origen':<15} | {'Mes':<7} | {'Cantidad':>8} | {'Suma Montos':>16}")
                    log("-" * 55)
                    for (o_key, m_key) in sorted(agrupado_no_seed.keys()):
                        t_list = agrupado_no_seed[(o_key, m_key)]
                        m_suma = sum((t.monto for t in t_list), Decimal("0.00"))
                        log(f"{o_key:<15} | {m_key:<7} | {len(t_list):>8} | {formatear_monto(m_suma):>16}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN N:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN O: SALDOS DE TODAS LAS BILLETERAS DE TODOS LOS USUARIOS
    # --------------------------------------------------------------------------
    log("=== SECCIÓN O: RECONCILIACIÓN DE SALDOS DE TODAS LAS BILLETERAS ===")
    # Reutiliza la fórmula de scripts/regresion/suite_regresion_whatsapp.py:390-419
    try:
        with session_scope() as db:
            hoy = hoy_argentina()
            billeteras_rows = db.execute(
                select(Billetera, Usuario.email)
                .join(Usuario, Billetera.usuario_id == Usuario.id)
                .order_by(Usuario.email, Billetera.nombre)
            ).all()

            log(f"{'Usuario':<32} | {'Billetera':<18} | {'Moneda':<6} | {'Guardado':>15} | {'Calculado':>15} | {'Diferencia':>15} | Estado")
            log("-" * 125)

            for b, email in billeteras_rows:
                s_guardado = b.saldo_actual
                s_inicial = b.saldo_inicial or Decimal("0.00")

                # Transacciones que afectan saldo (no crédito, no pendientes, fecha <= hoy)
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

                ingresos = Decimal(str(tx_row["ingresos"]))
                egresos = Decimal(str(tx_row["egresos"]))

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

                s_calc = s_inicial + ingresos - egresos + tr_in - tr_out
                diff = s_guardado - s_calc
                estado = "OK" if diff == Decimal("0.00") else f"[DIF: {formatear_monto(diff)}]"

                mon = b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)
                log(f"{email:<32} | {b.nombre:<18} | {mon:<6} | {formatear_monto(s_guardado):>15} | {formatear_monto(s_calc):>15} | {formatear_monto(diff):>15} | {estado}")
    except Exception as e:
        log(f"ERROR EN SECCIÓN O:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN P: MOVIMIENTOS CREADOS POR JOBS EN LOS ÚLTIMOS 10 DÍAS
    # --------------------------------------------------------------------------
    log("=== SECCIÓN P: MOVIMIENTOS CREADOS POR JOBS EN LOS ÚLTIMOS 10 DÍAS ===")
    try:
        with session_scope() as db:
            limite_fecha = ahora_argentina() - timedelta(days=10)
            txs_jobs = db.execute(
                select(Transaccion, Usuario.email)
                .join(Usuario, Transaccion.usuario_id == Usuario.id)
                .where(
                    Transaccion.fecha_creacion >= limite_fecha,
                    Transaccion.origen == OrigenTransaccion.RECURRENTE
                )
                .order_by(Transaccion.fecha_creacion.desc())
            ).all()

            log(f"Período evaluado: desde {limite_fecha.isoformat()} hasta hoy")
            log(f"Total transacciones creadas por jobs: {len(txs_jobs)}")
            if txs_jobs:
                log(f"{'Created At (UTC)':<24} | {'Usuario':<32} | {'Monto':>14} | {'Origen':<12} | Descripción")
                log("-" * 120)
                for t, email in txs_jobs:
                    log(f"{t.fecha_creacion.isoformat():<24} | {email:<32} | {formatear_monto(t.monto):>14} | {t.origen.value:<12} | {t.descripcion}")
            else:
                log("Ningún movimiento generado por jobs en los últimos 10 días.")
    except Exception as e:
        log(f"ERROR EN SECCIÓN P:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN Q: ÚLTIMO MES DISPONIBLE EN IPC_CACHE
    # --------------------------------------------------------------------------
    log("=== SECCIÓN Q: ÚLTIMO DATO DISPONIBLE EN IPC_CACHE ===")
    try:
        with session_scope() as db:
            ultimo_ipc = db.execute(
                select(IPCCache).order_by(IPCCache.fecha_dato.desc()).limit(1)
            ).scalar_one_or_none()

            if ultimo_ipc:
                log(f"Último mes: {ultimo_ipc.fecha_dato}")
                log(f"Índice acumulado: {ultimo_ipc.indice_acumulado}")
                log(f"¿Es estimado?: {'SÍ' if ultimo_ipc.es_estimado else 'NO'}")
                log(f"Fuente: {ultimo_ipc.fuente}")
                log(f"Fecha actualización: {ultimo_ipc.fecha_actualizacion.isoformat()}")
            else:
                log("ALERTA: Tabla ipc_cache está vacía.")
    except Exception as e:
        log(f"ERROR EN SECCIÓN Q:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN R: PERFIL Y PROYECCIÓN DE LOS SIETE USUARIOS
    # --------------------------------------------------------------------------
    log("=== SECCIÓN R: PERFIL Y PROYECCIÓN DE LOS SIETE USUARIOS ===")
    try:
        with session_scope() as db:
            users_r = db.execute(
                select(Usuario).where(Usuario.email.in_(usuarios_esperados)).order_by(Usuario.email)
            ).scalars().all()

            for u in users_r:
                log("=" * 80)
                log(f"USUARIO: {u.email} (ID: {u.id})")
                log("=" * 80)

                # --- 1. PROYECCIÓN ---
                log("\n--- Proyección (calcular_proyeccion_nueva) ---")
                proy_res1, proy_res2 = None, None
                try:
                    # Corrida 1
                    sql_query_count[0] = 0
                    t0 = time.perf_counter()
                    proy_res1 = calcular_proyeccion_nueva(db, u)
                    t_proy1 = (time.perf_counter() - t0) * 1000.0
                    q_proy1 = sql_query_count[0]

                    # Corrida 2
                    sql_query_count[0] = 0
                    t0 = time.perf_counter()
                    proy_res2 = calcular_proyeccion_nueva(db, u)
                    t_proy2 = (time.perf_counter() - t0) * 1000.0
                    q_proy2 = sql_query_count[0]

                    calib_ars = proy_res1.get("ars", {}).get("calibracion", {}) if proy_res1 else {}
                    pasa_puerta = calib_ars.get("pasa_puerta", False)
                    motivo = calib_ars.get("motivo", "sin_motivo")
                    ciclos_eval = calib_ars.get("ciclos_evaluados", 0)

                    log(f"Corrida 1: {t_proy1:>8.2f} ms | Consultas SQL: {q_proy1:>3}")
                    log(f"Corrida 2: {t_proy2:>8.2f} ms | Consultas SQL: {q_proy2:>3}")
                    log(f"Puerta de calibración ARS: {'PASA' if pasa_puerta else 'NO PASA'} (Ciclos evaluados: {ciclos_eval}, Motivo: {motivo})")
                except Exception as e:
                    log(f"ERROR EN PROYECCIÓN PARA {u.email}:\n{traceback.format_exc()}")

                # --- 2. PERFIL ---
                log("\n--- Perfil Financiero (calcular_perfil_nuevo / obtener_perfil) ---")
                perfil_res1, perfil_res2 = None, None
                try:
                    # Corrida 1
                    sql_query_count[0] = 0
                    t0 = time.perf_counter()
                    perfil_res1 = calcular_perfil_nuevo(db, u)
                    t_perf1 = (time.perf_counter() - t0) * 1000.0
                    q_perf1 = sql_query_count[0]

                    # Corrida 2
                    sql_query_count[0] = 0
                    t0 = time.perf_counter()
                    perfil_res2 = calcular_perfil_nuevo(db, u)
                    t_perf2 = (time.perf_counter() - t0) * 1000.0
                    q_perf2 = sql_query_count[0]

                    log(f"Corrida 1: {t_perf1:>8.2f} ms | Consultas SQL: {q_perf1:>3}")
                    log(f"Corrida 2: {t_perf2:>8.2f} ms | Consultas SQL: {q_perf2:>3}")
                    log(f"Datos suficientes: {perfil_res1.get('datos_suficientes')} | Confianza: {perfil_res1.get('nivel_confianza')} | Ciclos con datos: {perfil_res1.get('ciclos_con_datos')}")
                except Exception as e:
                    log(f"ERROR EN PERFIL PARA {u.email}:\n{traceback.format_exc()}")

                # Prueba de obtener_perfil (para verificar si intenta escribir en base si no existe)
                try:
                    obtener_perfil(db, u.id)
                except Exception as e:
                    log(f"NOTA: obtener_perfil() falló o intentó escribir en base para {u.email}: {type(e).__name__} - {str(e).strip()[:140]}")

                # Para testingadmin: JSON completo serializado
                if u.email == "testingadmin@argentum.com":
                    log("\n--- JSON COMPLETO DE PROYECCIÓN (testingadmin) ---")
                    log(json.dumps(proy_res1, default=str, indent=2))
                    log("\n--- JSON COMPLETO DE PERFIL (testingadmin) ---")
                    log(json.dumps(perfil_res1, default=str, indent=2))

                log()
    except Exception as e:
        log(f"ERROR EN SECCIÓN R:\n{traceback.format_exc()}")
    log()

    # --------------------------------------------------------------------------
    # SECCIÓN S: CALIBRACIONES GUARDADAS (TABLA calibraciones_usuario)
    # --------------------------------------------------------------------------
    log("=== SECCIÓN S: CALIBRACIONES GUARDADAS (TABLA calibraciones_usuario) ===")
    try:
        with session_scope() as db:
            from app.models.calibracion_usuario import CalibracionUsuario
            calibs = db.execute(
                select(CalibracionUsuario, Usuario.email)
                .join(Usuario, CalibracionUsuario.usuario_id == Usuario.id)
                .order_by(Usuario.email, CalibracionUsuario.moneda)
            ).all()

            if not calibs:
                log("No hay registros en la tabla calibraciones_usuario.")
            else:
                log(f"Total registros: {len(calibs)}")
                for c, email in calibs:
                    log(
                        f"Usuario: {email:<30} | Moneda: {c.moneda:<4} | Inicio ciclo: {c.inicio_ciclo.isoformat()} | "
                        f"Pasa: {'SÍ' if c.pasa_puerta else 'NO':<2} | Motivo: {str(c.motivo):<25} | "
                        f"Ciclos: {c.ciclos_evaluados:>2} | Fecha cálculo: {c.fecha_calculo.isoformat() if c.fecha_calculo else 'None'} | "
                        f"Duración: {c.duracion_ms:>7.2f} ms"
                    )
    except Exception as e:
        log(f"ERROR EN SECCIÓN S:\n{traceback.format_exc()}")
    log()

    log("=" * 80)
    log("FIN DE LA AUDITORÍA DE SOLO LECTURA")
    log("=" * 80)
    out_file.close()
    print(f"[OK] Auditoría finalizada exitosamente. Salida guardada en: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
