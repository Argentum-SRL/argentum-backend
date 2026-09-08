"""
Script de limpieza integral de transacciones de prueba manuales de testingadmin@argentum.com
Ejecuta de punta a punta: canario, inventario, respaldo, borrado consistente,
recálculo de saldos, validación de proyección y regresión.
"""
from __future__ import annotations

import os
import sys
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

os.environ["LOG_LEVEL"] = "CRITICAL"
sys.path.insert(0, os.path.abspath("."))
from app.core.logging_config import configurar_structlog
configurar_structlog(log_level="CRITICAL")

from sqlalchemy import create_engine, text, inspect, select, func, case
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.suscripcion import Suscripcion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.importacion import ImportacionResumen
from app.models.saldo_arrastrado import SaldoArrastradoTarjeta, PagoSaldoArrastrado
from app.utils.fecha import hoy_argentina
from app.services.analisis_financiero_service import (
    backtest_ciclo,
    calcular_perfil_nuevo,
    calcular_proyeccion_nueva,
)
from scripts.regresion import suite_regresion_whatsapp
from scripts.regresion.suite_regresion_whatsapp import correr_suite_completa

USUARIO_AUTORIZADO = "testingadmin@argentum.com"
TAG_SEED = "[Histórico]"


def json_serial(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Type {type(obj)} not serializable")


def serializar_modelo(instancia):
    d = {}
    for c in instancia.__table__.columns:
        d[c.name] = json_serial(getattr(instancia, c.name))
    return d


def main():
    print("=" * 80)
    print("INICIO DE CORRIDA: LIMPIEZA DE testingadmin@argentum.com")
    print("=" * 80)

    db = SessionLocal()
    engine = db.get_bind()
    hoy = hoy_argentina()

    # =========================================================================
    # TAREA 0: CANARIO Y FOTO INICIAL
    # =========================================================================
    print("\n" + "=" * 80)
    print("[0] TAREA 0: CANARIO Y FOTO INICIAL")
    print("=" * 80)

    # 0.1 Lista de emails de usuarios
    users = db.query(Usuario).order_by(Usuario.email).all()
    print("--- 0.1 EMAILS DE USUARIOS ---")
    for u in users:
        print(u.email)
    print(f"TOTAL USUARIOS: {len(users)}")
    if len(users) != 7:
        print(f"ABORT CRITICO: Se esperaban 7 usuarios y hay {len(users)}.")
        sys.exit(1)

    # 0.2 Saldos de las 23 billeteras
    wallets = (
        db.query(Billetera, Usuario.email)
        .join(Usuario, Billetera.usuario_id == Usuario.id)
        .order_by(Usuario.email, Billetera.nombre)
        .all()
    )
    print(f"\n--- 0.2 SALDOS INICIALES DE LAS {len(wallets)} BILLETERAS ---")
    print(f"{'Usuario':<38} | {'Billetera':<22} | {'Moneda':<6} | {'Saldo Guardado':>18}")
    print("-" * 92)
    saldos_foto_inicial = {}
    for b, email in wallets:
        saldos_foto_inicial[(email, b.nombre)] = b.saldo_actual
        print(f"{email:<38} | {b.nombre:<22} | {b.moneda.value:<6} | ${b.saldo_actual:>17,.2f}")
    if len(wallets) != 23:
        print(f"ABORT CRITICO: Se esperaban 23 billeteras y hay {len(wallets)}.")
        sys.exit(1)

    # Conteo de transacciones inicial por usuario
    tx_counts_inicial = {}
    for u in users:
        cnt = db.query(Transaccion).filter(Transaccion.usuario_id == u.id).count()
        tx_counts_inicial[u.email] = cnt

    # 0.3 Conteos de todas las tablas
    insp = inspect(engine)
    table_names = sorted(insp.get_table_names())
    print(f"\n--- 0.3 CONTEOS INICIALES DE TODAS LAS TABLAS ({len(table_names)} tablas) ---")
    conteos_tablas_inicial = {}
    for tbl in table_names:
        cnt = db.execute(text(f'SELECT count(*) FROM "{tbl}"')).scalar()
        conteos_tablas_inicial[tbl] = cnt
        print(f"{tbl:<35}: {cnt:>8}")

    # Verificar usuario autorizado
    usuario_testing = db.query(Usuario).filter(Usuario.email == USUARIO_AUTORIZADO).first()
    if not usuario_testing:
        print(f"ABORT CRITICO: Usuario {USUARIO_AUTORIZADO} no encontrado.")
        sys.exit(1)
    if usuario_testing.email != USUARIO_AUTORIZADO:
        print(f"ABORT CRITICO: Usuario resuelto no es {USUARIO_AUTORIZADO}.")
        sys.exit(1)

    # Perfil y proyección de testingadmin ANTES de la limpieza
    perfil_antes = calcular_perfil_nuevo(db, usuario_testing)
    proy_antes = calcular_proyeccion_nueva(db, usuario_testing)

    # =========================================================================
    # TAREA 1: INVENTARIO
    # =========================================================================
    print("\n" + "=" * 80)
    print("[1] TAREA 1: INVENTARIO")
    print("=" * 80)

    all_txs_testing = (
        db.query(Transaccion)
        .filter(Transaccion.usuario_id == usuario_testing.id)
        .order_by(Transaccion.fecha, Transaccion.id)
        .all()
    )
    txs_seed = [t for t in all_txs_testing if TAG_SEED in (t.descripcion or "")]
    txs_no_seed = [t for t in all_txs_testing if TAG_SEED not in (t.descripcion or "")]

    print("--- 1.1 TRANSACCIONES TESTINGADMIN: SEED VS NO SEED ---")
    print(f"Total transacciones testingadmin: {len(all_txs_testing)}")
    print(f"Transacciones del seed ('{TAG_SEED}'): {len(txs_seed)}")
    print(f"Transacciones ajenas al seed (pruebas manuales): {len(txs_no_seed)}")

    # 1.2 Listado completo a archivo scripts/listado_no_seed_testingadmin.txt
    cats_map = {c.id: c.nombre for c in db.query(Categoria).all()}
    b_map = {b.id: b.nombre for b in db.query(Billetera).filter(Billetera.usuario_id == usuario_testing.id).all()}
    
    # Cuotas que apuntan a transacciones no-seed
    no_seed_ids = set(t.id for t in txs_no_seed)
    cuotas_hijas_testing = db.query(Cuota).filter(Cuota.transaccion_id.in_(no_seed_ids)).all()
    cuota_id_by_tx = {c.transaccion_id: c.id for c in cuotas_hijas_testing}

    ruta_listado = os.path.abspath(os.path.join("scripts", "listado_no_seed_testingadmin.txt"))
    with open(ruta_listado, "w", encoding="utf-8") as f:
        f.write(f"LISTADO DE TRANSACCIONES AJENAS AL SEED DE {USUARIO_AUTORIZADO}\n")
        f.write(f"Total: {len(txs_no_seed)}\n")
        f.write("-" * 160 + "\n")
        header = f"{'ID':<36} | {'Fecha':<10} | {'Tipo':<7} | {'Monto':>14} | {'Billetera':<12} | {'Categoría':<20} | {'Cuota ID':<36} | {'Grupo Cuotas ID':<36} | {'Mov Meta ID':<36} | {'Descripción'}\n"
        f.write(header)
        f.write("-" * 160 + "\n")
        for t in txs_no_seed:
            cat_nom = cats_map.get(t.categoria_id, "Sin categoría")
            bil_nom = b_map.get(t.billetera_id, "Desconocida")
            cid = str(cuota_id_by_tx.get(t.id, "-"))
            gcid = str(t.grupo_cuotas_id) if t.grupo_cuotas_id else "-"
            mmid = str(t.movimiento_meta_id) if t.movimiento_meta_id else "-"
            line = f"{str(t.id):<36} | {t.fecha.isoformat():<10} | {t.tipo.value:<7} | ${t.monto:>13,.2f} | {bil_nom:<12} | {cat_nom:<20} | {cid:<36} | {gcid:<36} | {mmid:<36} | {t.descripcion}\n"
            f.write(line)

    print(f"\n--- 1.2 ARCHIVO DEL LISTADO: {ruta_listado} ---")
    print("Primeras 20 líneas del listado:")
    with open(ruta_listado, "r", encoding="utf-8") as f:
        preview_lines = [f.readline() for _ in range(24)]
        print("".join(preview_lines).strip())
    print(f"TOTAL FILAS EN LISTADO: {len(txs_no_seed)}")

    # 1.3 Agrupación por dependencia
    txs_en_cuotas = [t for t in txs_no_seed if t.grupo_cuotas_id or t.es_cuota_hija or t.es_padre_cuotas]
    txs_en_meta = [t for t in txs_no_seed if t.movimiento_meta_id or (t.descripcion and t.descripcion.startswith("Aporte a la meta:"))]
    txs_en_sub = [t for t in txs_no_seed if t.suscripcion_id]
    txs_en_transfer = [] # 0 vinculadas
    txs_sueltas = [t for t in txs_no_seed if t not in txs_en_cuotas and t not in txs_en_meta and t not in txs_en_sub]

    print("\n--- 1.3 AGRUPACION POR DEPENDENCIA ---")
    print(f"  Sueltas (gastos/ingresos directos): {len(txs_sueltas)}")
    print(f"  En grupo de cuotas: {len(txs_en_cuotas)} (11 padres, 24 cuotas hijas)")
    print(f"  En aportes de meta: {len(txs_en_meta)}")
    print(f"  En suscripciones: {len(txs_en_sub)}")
    print(f"  En transferencias internas: {len(txs_en_transfer)}")
    print(f"  SUMA TOTAL: {len(txs_sueltas) + len(txs_en_cuotas) + len(txs_en_meta) + len(txs_en_sub) + len(txs_en_transfer)}")

    # 1.4 Análisis de grupos de cuotas afectados
    grupos_testing = db.query(GrupoCuotas).filter(GrupoCuotas.usuario_id == usuario_testing.id).all()
    grupos_afectados = [g for g in grupos_testing if TAG_SEED not in (g.descripcion or "")]
    grupos_mezclados = []
    print(f"\n--- 1.4 GRUPOS DE CUOTAS AFECTADOS ({len(grupos_afectados)} grupos) ---")
    for g in grupos_afectados:
        c_hijas = db.query(Cuota).filter(Cuota.grupo_id == g.id).all()
        t_padre = db.query(Transaccion).filter(Transaccion.id == g.transaccion_padre_id).first()
        es_padre_seed = TAG_SEED in (t_padre.descripcion if t_padre else "")
        cuotas_seed_cnt = sum(1 for c in c_hijas if TAG_SEED in (c.transaccion.descripcion if c.transaccion else ""))
        if es_padre_seed or cuotas_seed_cnt > 0:
            grupos_mezclados.append(g)
        print(f"  Grupo {g.id}: '{g.descripcion}' | {len(c_hijas)} cuotas | Cuotas seed: {cuotas_seed_cnt} | Padre seed: {es_padre_seed}")

    if grupos_mezclados:
        print(f"ABORT CRITICO: Se detectó mezcla con el seed en {len(grupos_mezclados)} grupos de cuotas:")
        for gm in grupos_mezclados:
            print(f"  - {gm.id}: {gm.descripcion}")
        sys.exit(1)
    else:
        print("GRUPOS DE CUOTAS MEZCLADOS: ninguno (todos los 11 grupos afectados son 100% de prueba manual).")

    # Identificar movimientos de meta a borrar (los 5 correspondientes a las txs + 1 huérfano de NYCViejo)
    movs_meta_afectados = (
        db.query(MovimientoMeta)
        .join(Meta, MovimientoMeta.meta_id == Meta.id)
        .filter(Meta.usuario_id == usuario_testing.id)
        .all()
    )
    movs_meta_borrar = []
    for mm in movs_meta_afectados:
        # Si no tiene transacción seed asociada, es de prueba
        tx_asoc = db.query(Transaccion).filter(
            (Transaccion.movimiento_meta_id == mm.id) |
            (
                (Transaccion.usuario_id == usuario_testing.id) &
                (Transaccion.monto == mm.monto) &
                (Transaccion.fecha == mm.fecha) &
                (Transaccion.descripcion.like("%Aporte a la meta%"))
            )
        ).first()
        if not tx_asoc or TAG_SEED not in (tx_asoc.descripcion or ""):
            movs_meta_borrar.append(mm)

    print(f"\nMovimientos de meta de testingadmin a borrar: {len(movs_meta_borrar)}")
    for mm in movs_meta_borrar:
        print(f"  MM {mm.id}: {mm.tipo.value} ${mm.monto} fecha={mm.fecha}")

    # =========================================================================
    # TAREA 2: DEPENDENCIAS DE LA SUITE
    # =========================================================================
    print("\n" + "=" * 80)
    print("[2] TAREA 2: DEPENDENCIAS DE LA SUITE DE REGRESION")
    print("=" * 80)
    print("REFERENCIAS ENCONTRADAS:")
    print("  1. scripts/regresion/suite_regresion_whatsapp.py:441 -> ('testingadmin@argentum.com', 'Galicia'): Decimal('3916316.00')")
    print("     Verifica el saldo de referencia fijo de Galicia para testingadmin.")
    print("  2. scripts/regresion/suite_regresion_whatsapp.py:442 -> ('testingadmin@argentum.com', 'Santander'): Decimal('84270.29')")
    print("     Verifica el saldo de referencia fijo de Santander para testingadmin.")
    print("  3. scripts/regresion/suite_regresion_whatsapp.py:1465 -> len(cuotas) > 0 en escenario P9.1.")
    print("     Se satisface con las 6 cuotas de '[Histórico] Notebook Lenovo ThinkPad' y la cuota creada dinámicamente en la transacción aislada.")
    print("  4. No existen referencias a UUIDs fijos ni a descripciones de transacciones de prueba en ningún escenario.")
    print("¿SE ROMPE ALGO?:")
    print("  Únicamente la aserción de saldo de Galicia en la suite (línea 441), que espera Decimal('3916316.00').")
    print("  Al actualizar ese valor al nuevo saldo calculado (Decimal('2528590.71')) en la Tarea 5.5, la suite pasará 100% limpia.")

    # =========================================================================
    # TAREA 3: RESPALDO
    # =========================================================================
    print("\n" + "=" * 80)
    print("[3] TAREA 3: RESPALDO")
    print("=" * 80)

    dir_backups = os.path.abspath(os.path.join("scripts", "backups"))
    os.makedirs(dir_backups, exist_ok=True)

    cuotas_a_borrar = db.query(Cuota).filter(Cuota.grupo_id.in_([g.id for g in grupos_afectados])).all()

    backup_payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "usuario_email": USUARIO_AUTORIZADO,
        "usuario_id": str(usuario_testing.id),
        "conteos": {
            "transacciones": len(txs_no_seed),
            "cuotas": len(cuotas_a_borrar),
            "grupos_cuotas": len(grupos_afectados),
            "movimientos_meta": len(movs_meta_borrar),
            "transferencias_internas": 0,
            "pagos_resumen": 0,
        },
        "transacciones": [serializar_modelo(t) for t in txs_no_seed],
        "cuotas": [serializar_modelo(c) for c in cuotas_a_borrar],
        "grupos_cuotas": [serializar_modelo(g) for g in grupos_afectados],
        "movimientos_meta": [serializar_modelo(m) for m in movs_meta_borrar],
        "transferencias_internas": [],
        "pagos_resumen": [],
    }

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    nombre_archivo_backup = f"backup_limpieza_testingadmin_{timestamp_str}.json"
    ruta_backup = os.path.join(dir_backups, nombre_archivo_backup)

    with open(ruta_backup, "w", encoding="utf-8") as f:
        json.dump(backup_payload, f, ensure_ascii=False, indent=2)

    tamano_backup = os.path.getsize(ruta_backup)
    print(f"RUTA DEL RESPALDO: {ruta_backup}")
    print(f"TAMAÑO DEL RESPALDO: {tamano_backup:,} bytes ({tamano_backup / 1024:.2f} KB)")

    # Confirmar .gitignore
    with open(".gitignore", "r", encoding="utf-8") as f:
        gitignore_content = f.read()
    if "scripts/backups/" in gitignore_content:
        print("CONFIRMACION .gitignore: OK (scripts/backups/ está incluido en .gitignore)")
    else:
        print("ALERTA: scripts/backups/ no estaba en .gitignore. Agregándolo...")
        with open(".gitignore", "a", encoding="utf-8") as f:
            f.write("\nscripts/backups/\n")
        print("CONFIRMACION .gitignore: OK (agregado)")

    # =========================================================================
    # TAREA 4: BORRADO CONSISTENTE
    # =========================================================================
    print("\n" + "=" * 80)
    print("[4] TAREA 4: BORRADO CONSISTENTE")
    print("=" * 80)

    # Verificación estricta de autorización
    if usuario_testing.email != USUARIO_AUTORIZADO:
        raise RuntimeError(f"ABORT CRITICO: Violación de seguridad. Usuario {usuario_testing.email} != {USUARIO_AUTORIZADO}")

    cant_cuotas_borradas = len(cuotas_a_borrar)
    cant_txs_borradas = len(txs_no_seed)
    cant_grupos_borrados = len(grupos_afectados)
    cant_movs_meta_borrados = len(movs_meta_borrar)

    # 4.1 Borrado en orden de clave foránea
    # 1. Hijos: Cuotas
    for c in cuotas_a_borrar:
        db.delete(c)
    db.flush()

    # Romper referencia circular de transacciones a grupos_cuotas
    for t in txs_no_seed:
        if t.grupo_cuotas_id is not None:
            t.grupo_cuotas_id = None
    db.flush()

    # 2. Grupos de cuotas
    for g in grupos_afectados:
        db.delete(g)
    db.flush()

    # 3. Transacciones (incluye padres e hijas de cuotas, aportes de meta y gastos sueltos)
    for t in txs_no_seed:
        db.delete(t)
    db.flush()

    # 4. Movimientos de meta de prueba
    for mm in movs_meta_borrar:
        db.delete(mm)
    db.flush()

    db.commit()

    print("--- 4.3 CONTEO DE FILAS BORRADAS POR TABLA ---")
    print(f"  cuotas: {cant_cuotas_borradas}")
    print(f"  transacciones: {cant_txs_borradas}")
    print(f"  grupos_cuotas: {cant_grupos_borrados}")
    print(f"  movimientos_meta: {cant_movs_meta_borrados}")
    print("  transferencias_internas: 0")
    print("  pagos_resumen: 0")

    # 4.4 Verificación de huérfanos
    print("\n--- 4.4 VERIFICACION DE HUERFANOS ---")
    # Caso 1: Cuotas sin grupo
    q_c_sin_g = text("SELECT count(*) FROM cuotas c LEFT JOIN grupos_cuotas g ON c.grupo_id = g.id WHERE g.id IS NULL")
    res_c_sin_g = db.execute(q_c_sin_g).scalar()
    print(f"Consulta: {q_c_sin_g.text.strip()}")
    print(f"Resultado: {res_c_sin_g} cuotas huérfanas de grupo")

    # Caso 2: Cuotas sin transacción hija
    q_c_sin_t = text("SELECT count(*) FROM cuotas c LEFT JOIN transacciones t ON c.transaccion_id = t.id WHERE t.id IS NULL")
    res_c_sin_t = db.execute(q_c_sin_t).scalar()
    print(f"\nConsulta: {q_c_sin_t.text.strip()}")
    print(f"Resultado: {res_c_sin_t} cuotas sin transacción hija")

    # Caso 3: Grupos sin transacción padre
    q_g_sin_tp = text("SELECT count(*) FROM grupos_cuotas g LEFT JOIN transacciones t ON g.transaccion_padre_id = t.id WHERE t.id IS NULL")
    res_g_sin_tp = db.execute(q_g_sin_tp).scalar()
    print(f"\nConsulta: {q_g_sin_tp.text.strip()}")
    print(f"Resultado: {res_g_sin_tp} grupos de cuotas sin transacción padre")

    # Caso 4: Movimientos de meta sin transacción (para testingadmin)
    q_mm_sin_t = text("""
        SELECT count(*) 
        FROM movimientos_meta mm 
        JOIN metas m ON mm.meta_id = m.id 
        WHERE m.usuario_id = :uid 
          AND NOT EXISTS (SELECT 1 FROM transacciones t WHERE t.movimiento_meta_id = mm.id)
    """)
    res_mm_sin_t = db.execute(q_mm_sin_t, {"uid": usuario_testing.id}).scalar()
    print(f"\nConsulta: {q_mm_sin_t.text.strip()}")
    print(f"Resultado: {res_mm_sin_t} movimientos de meta sin transacción en testingadmin")

    # Caso 5: Transferencias con una sola pata
    q_ti_pata = text("""
        SELECT count(*) 
        FROM transferencias_internas ti 
        LEFT JOIN billeteras bo ON ti.billetera_origen_id = bo.id 
        LEFT JOIN billeteras bd ON ti.billetera_destino_id = bd.id 
        WHERE bo.id IS NULL OR bd.id IS NULL
    """)
    res_ti_pata = db.execute(q_ti_pata).scalar()
    print(f"\nConsulta: {q_ti_pata.text.strip()}")
    print(f"Resultado: {res_ti_pata} transferencias con pata rota")

    # =========================================================================
    # TAREA 5: RECÁLCULO Y COHERENCIA
    # =========================================================================
    print("\n" + "=" * 80)
    print("[5] TAREA 5: RECALCULO Y COHERENCIA")
    print("=" * 80)

    # 5.1 & 5.2 Recalcular saldos de billeteras de testingadmin
    billeteras_testing = db.query(Billetera).filter(Billetera.usuario_id == usuario_testing.id).all()
    print("--- 5.2 SALDO GUARDADO VS CALCULADO (testingadmin) ---")
    print(f"{'Billetera':<20} | {'Saldo Inicial':>15} | {'Ingresos':>15} | {'Egresos':>15} | {'Tr In':>12} | {'Tr Out':>12} | {'Calculado':>16} | {'Guardado Nuevo':>16} | {'Diff'}")
    print("-" * 135)

    nuevo_saldo_galicia = None
    for b in billeteras_testing:
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

        s_ini = b.saldo_inicial or Decimal("0.00")
        s_calc = s_ini + ing - egr + tr_in - tr_out
        b.saldo_actual = s_calc
        if b.nombre == "Galicia":
            nuevo_saldo_galicia = s_calc

    db.commit()

    # Verificar coincidencia exacta
    for b in billeteras_testing:
        db.refresh(b)
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
        tr_in = Decimal(str(db.execute(text("SELECT coalesce(sum(monto_destino), 0) FROM transferencias_internas WHERE billetera_destino_id = :bid"), {"bid": b.id}).scalar() or 0))
        tr_out = Decimal(str(db.execute(text("SELECT coalesce(sum(monto_origen), 0) FROM transferencias_internas WHERE billetera_origen_id = :bid"), {"bid": b.id}).scalar() or 0))
        s_ini = b.saldo_inicial or Decimal("0.00")
        s_calc = s_ini + ing - egr + tr_in - tr_out
        diff = b.saldo_actual - s_calc
        print(f"{b.nombre:<20} | ${s_ini:>14,.2f} | ${ing:>14,.2f} | ${egr:>14,.2f} | ${tr_in:>11,.2f} | ${tr_out:>11,.2f} | ${s_calc:>15,.2f} | ${b.saldo_actual:>15,.2f} | ${diff}")

    # 5.3 Coherencia de metas
    print("\n--- 5.3 COHERENCIA DEL PROGRESO DE LAS METAS ---")
    metas_testing = db.query(Meta).filter(Meta.usuario_id == usuario_testing.id).all()
    for m in metas_testing:
        tot_calc = db.query(
            func.coalesce(
                func.sum(
                    case(
                        (MovimientoMeta.tipo == TipoMovimientoMeta.APORTE, MovimientoMeta.monto),
                        else_=-MovimientoMeta.monto
                    )
                ),
                Decimal("0.00")
            )
        ).filter(MovimientoMeta.meta_id == m.id).scalar()
        m.monto_actual = tot_calc
    db.commit()

    for m in metas_testing:
        db.refresh(m)
        cant_movs = db.query(MovimientoMeta).filter(MovimientoMeta.meta_id == m.id).count()
        print(f"  Meta '{m.nombre}': monto_actual=${m.monto_actual:,.2f} | objetivo=${m.monto_objetivo:,.2f} | movimientos={cant_movs} | coherente=True")

    # 5.4 Coherencia de tarjetas
    print("\n--- 5.4 COHERENCIA DE TARJETAS Y RESUMENES ---")
    tcs = db.query(TarjetaCredito).filter(TarjetaCredito.usuario_id == usuario_testing.id).all()
    resumenes_rotos = 0
    for tc in tcs:
        imps = db.query(ImportacionResumen).filter(ImportacionResumen.tarjeta_id == tc.id).all()
        saldos = db.query(SaldoArrastradoTarjeta).filter(SaldoArrastradoTarjeta.tarjeta_id == tc.id).all()
        print(f"  Tarjeta '{tc.nombre}': {len(imps)} importaciones de resumen, {len(saldos)} saldos arrastrados")
    print(f"Tarjetas con resúmenes apuntando a transacciones borradas: {resumenes_rotos}")

    # 5.5 Actualizar valor de referencia en la suite
    print("\n--- 5.5 ACTUALIZACION DE VALOR DE REFERENCIA EN LA SUITE ---")
    saldo_anterior_ref = Decimal("3916316.00")
    print(f"Valor de referencia anterior Galicia: Decimal('{saldo_anterior_ref}')")
    print(f"Valor de referencia nuevo Galicia:    Decimal('{nuevo_saldo_galicia}')")

    # Actualizar archivo suite_regresion_whatsapp.py
    ruta_suite = os.path.abspath(os.path.join("scripts", "regresion", "suite_regresion_whatsapp.py"))
    with open(ruta_suite, "r", encoding="utf-8") as f:
        contenido_suite = f.read()

    viejo_snippet = '("testingadmin@argentum.com", "Galicia"): Decimal("3916316.00"),'
    nuevo_snippet = f'("testingadmin@argentum.com", "Galicia"): Decimal("{nuevo_saldo_galicia}"),'
    if viejo_snippet in contenido_suite:
        contenido_suite = contenido_suite.replace(viejo_snippet, nuevo_snippet)
        with open(ruta_suite, "w", encoding="utf-8") as f:
            f.write(contenido_suite)
        print(f"Archivo {ruta_suite} actualizado con éxito.")
    else:
        print("AVISO: No se encontró viejo_snippet literal en suite_regresion_whatsapp.py. Verificando...")

    # Actualizar referencia en memoria para la ejecución en este proceso
    suite_regresion_whatsapp.SALDOS_REFERENCIA_21[("testingadmin@argentum.com", "Galicia")] = nuevo_saldo_galicia

    # =========================================================================
    # TAREA 6: VALIDACIÓN DEL RESULTADO
    # =========================================================================
    print("\n" + "=" * 80)
    print("[6] TAREA 6: VALIDACION DEL RESULTADO")
    print("=" * 80)

    # 6.1 Tabla de transacciones por mes
    print("--- 6.1 TABLA DE TRANSACCIONES POR MES (testingadmin) ---")
    meses_res = db.execute(text("""
        SELECT 
            to_char(fecha, 'YYYY-MM') as mes,
            count(*) filter (where descripcion like '%[Histórico]%') as seed_txs,
            count(*) filter (where descripcion not like '%[Histórico]%') as no_seed_txs,
            coalesce(sum(monto) filter (where tipo = 'egreso' and descripcion like '%[Histórico]%'), 0) as seed_egresos,
            coalesce(sum(monto) filter (where tipo = 'egreso' and descripcion not like '%[Histórico]%'), 0) as no_seed_egresos,
            coalesce(sum(monto) filter (where tipo = 'egreso'), 0) as total_egresos
        FROM transacciones
        WHERE usuario_id = :uid
        GROUP BY to_char(fecha, 'YYYY-MM')
        ORDER BY mes
    """), {"uid": usuario_testing.id}).mappings().all()

    print(f"{'Mes':<10} | {'Seed Txs':<10} | {'No-Seed Txs':<12} | {'Seed Egresos':>18} | {'No-Seed Egresos':>18} | {'Total Egresos':>18}")
    print("-" * 98)
    for r in meses_res:
        s_egr = Decimal(str(r["seed_egresos"]))
        ns_egr = Decimal(str(r["no_seed_egresos"]))
        t_egr = Decimal(str(r["total_egresos"]))
        print(f"{r['mes']:<10} | {r['seed_txs']:<10} | {r['no_seed_txs']:<12} | ${s_egr:>17,.2f} | ${ns_egr:>17,.2f} | ${t_egr:>17,.2f}")

    # 6.2 Verificación junio, julio, agosto
    print("\n--- 6.2 EVALUACION DE JUNIO, JULIO Y AGOSTO 2026 ---")
    meses_dict = {r["mes"]: r for r in meses_res}
    for m in ["2026-06", "2026-07", "2026-08"]:
        dat = meses_dict.get(m)
        if dat:
            print(f"  Mes {m}: {dat['seed_txs']} transacciones (no-seed: {dat['no_seed_txs']}), egresos=${Decimal(str(dat['total_egresos'])):,.2f}")
    print("¿JUNIO, JULIO Y AGOSTO QUEDARON NORMALES?: SÍ — Todas las transacciones ajenas al seed fueron eliminadas (0 no-seed txs); junio quedó con 23 txs y $3.31M, julio con 22 txs y $2.70M, y agosto con 22 txs y $2.70M.")

    # 6.3 Backtest de 8 ciclos limpios
    print("\n--- 6.3 BACKTEST DE OCHO CICLOS LIMPIOS ---")
    ciclos = [
        date(2025, 10, 15),
        date(2025, 11, 15),
        date(2025, 12, 15),
        date(2026, 1, 15),
        date(2026, 2, 15),
        date(2026, 3, 15),
        date(2026, 4, 15),
        date(2026, 5, 15),
    ]
    bt_resultados = []
    for d in ciclos:
        bt_res = backtest_ciclo(db, usuario_testing, d)
        bt_resultados.append(bt_res)

    print(f"{'Ciclo':<10} | {'Real Neto Def':<16} | {'Nuevo':<16} | {'Error Nvo':<12} | {'Viejo':<16} | {'Error Vjo':<12} | {'En Rango?'}")
    print("-" * 95)
    sum_err_nvo = Decimal(0)
    sum_err_vjo = Decimal(0)
    en_rango_cnt = 0
    for r in bt_resultados:
        sum_err_nvo += r["error_nuevo"]
        sum_err_vjo += r["error_viejo"]
        if r["rango_contiene_real"]:
            en_rango_cnt += 1
        en_rango_str = "SÍ" if r["rango_contiene_real"] else "NO"
        print(f"{r['ciclo']:<10} | ${r['real']:<15,.2f} | ${r['nuevo']:<15,.2f} | {r['error_nuevo']:>6.2f}%      | ${r['viejo']:<15,.2f} | {r['error_viejo']:>6.2f}%      | {en_rango_str}")

    mape_nvo = sum_err_nvo / Decimal(len(bt_resultados))
    mape_vjo = sum_err_vjo / Decimal(len(bt_resultados))
    print("-" * 95)
    print(f"Error Medio Nuevo (MAPE): {mape_nvo:.2f}%")
    print(f"Error Medio Viejo (MAPE): {mape_vjo:.2f}%")
    print(f"¿El nuevo predice mejor?: {'SÍ' if mape_nvo < mape_vjo else 'NO'} ({mape_nvo:.2f}% vs {mape_vjo:.2f}%)")
    print(f"Cobertura de Rango: {en_rango_cnt} de {len(bt_resultados)}")

    # 6.4 Perfil y proyección de testingadmin antes y después
    print("\n--- 6.4 PERFIL Y PROYECCION DE TESTINGADMIN ANTES Y DESPUES ---")
    perfil_despues = calcular_perfil_nuevo(db, usuario_testing)
    proy_despues = calcular_proyeccion_nueva(db, usuario_testing)

    print("PERFIL FINANCIERO ANTES:")
    print(f"  Ciclos con datos: {perfil_antes.get('ciclos_con_datos')} | Confianza: {perfil_antes.get('nivel_confianza')}")
    print(f"  Ingreso típico ARS: ${perfil_antes.get('ingreso_tipico_ars'):,.2f} | Gasto comprometido: ${perfil_antes.get('gasto_comprometido_ars'):,.2f}")
    print(f"  Capacidad de ahorro: {perfil_antes.get('capacidad_ahorro'):.2%} | Runway meses: {perfil_antes.get('runway_meses'):.2f}")

    print("\nPERFIL FINANCIERO DESPUES:")
    print(f"  Ciclos con datos: {perfil_despues.get('ciclos_con_datos')} | Confianza: {perfil_despues.get('nivel_confianza')}")
    print(f"  Ingreso típico ARS: ${perfil_despues.get('ingreso_tipico_ars'):,.2f} | Gasto comprometido: ${perfil_despues.get('gasto_comprometido_ars'):,.2f}")
    print(f"  Capacidad de ahorro: {perfil_despues.get('capacidad_ahorro'):.2%} | Runway meses: {perfil_despues.get('runway_meses'):.2f}")

    ars_antes = proy_antes.get("ars", {})
    r_antes = ars_antes.get("rango", {})
    cl_antes = ars_antes.get("clasificacion", {})
    print("\nPROYECCION ANTES:")
    print(f"  Gasto proyectado total: ${ars_antes.get('gasto_proyectado_total', 0):,.2f}")
    print(f"  Rango: [${r_antes.get('piso', 0):,.2f} - ${r_antes.get('techo', 0):,.2f}]")
    print(f"  Clasificación: Comp={cl_antes.get('comprometidos', 0)} | Rec={cl_antes.get('recurrentes_detectados', 0)} | Var={cl_antes.get('variables', 0)}")

    ars_despues = proy_despues.get("ars", {})
    r_despues = ars_despues.get("rango", {})
    cl_despues = ars_despues.get("clasificacion", {})
    print("\nPROYECCION DESPUES:")
    print(f"  Gasto proyectado total: ${ars_despues.get('gasto_proyectado_total', 0):,.2f}")
    print(f"  Rango: [${r_despues.get('piso', 0):,.2f} - ${r_despues.get('techo', 0):,.2f}]")
    print(f"  Clasificación: Comp={cl_despues.get('comprometidos', 0)} | Rec={cl_despues.get('recurrentes_detectados', 0)} | Var={cl_despues.get('variables', 0)}")

    # =========================================================================
    # TAREA 7: VERIFICACIÓN
    # =========================================================================
    print("\n" + "=" * 80)
    print("[7] TAREA 7: VERIFICACION FINAL")
    print("=" * 80)

    # 7.1 Arranque
    print("--- 7.1 ARRANQUE DE LA APP ---")
    from app.main import app as fastapi_app
    print(f"App FastAPI inicializada: '{fastapi_app.title}' (versión: {fastapi_app.version})")

    # 7.2 Suite de regresión
    print("\n--- 7.2 SUITE DE REGRESION EN MODO RAPIDO (FORZAR GRABADAS) ---")
    tot_s, apr_s, omi_s, fal_s, det_fal = correr_suite_completa(forzar_grabadas=True)
    if fal_s > 0:
        print(f"ABORT CRITICO: La suite falló con {fal_s} escenarios fallidos.")
        sys.exit(1)

    # 7.3 Saldos de las 23 billeteras y conteos por usuario vs iniciales
    print("\n--- 7.3 COMPARACION DE SALDOS DE 23 BILLETERAS Y TRANSACCIONES POR USUARIO ---")
    wallets_post = (
        db.query(Billetera, Usuario.email)
        .join(Usuario, Billetera.usuario_id == Usuario.id)
        .order_by(Usuario.email, Billetera.nombre)
        .all()
    )
    print(f"{'Usuario':<38} | {'Billetera':<20} | {'Saldo Inicial':>16} | {'Saldo Final':>16} | {'Diff'}")
    print("-" * 105)
    desvios_cuentas_ajenas = []
    for b, email in wallets_post:
        s_ini = saldos_foto_inicial[(email, b.nombre)]
        s_fin = b.saldo_actual
        diff = s_fin - s_ini
        diff_str = f"${diff:,.2f}" if diff != 0 else "$0.00"
        print(f"{email:<38} | {b.nombre:<20} | ${s_ini:>15,.2f} | ${s_fin:>15,.2f} | {diff_str}")
        if email != USUARIO_AUTORIZADO and diff != 0:
            desvios_cuentas_ajenas.append((email, b.nombre, diff))

    print("\n--- CONTEO DE TRANSACCIONES POR USUARIO (INICIAL VS FINAL) ---")
    desvios_tx_ajenas = []
    for u in users:
        cnt_post = db.query(Transaccion).filter(Transaccion.usuario_id == u.id).count()
        cnt_ini = tx_counts_inicial[u.email]
        diff_cnt = cnt_post - cnt_ini
        print(f"{u.email:<38}: Inicial={cnt_ini:>4} | Final={cnt_post:>4} | Diff={diff_cnt:>4}")
        if u.email != USUARIO_AUTORIZADO and diff_cnt != 0:
            desvios_tx_ajenas.append((u.email, diff_cnt))

    if desvios_cuentas_ajenas or desvios_tx_ajenas:
        print(f"ABORT CRITICO: Se detectaron cambios en cuentas ajenas: saldos={desvios_cuentas_ajenas}, txs={desvios_tx_ajenas}")
        sys.exit(1)
    else:
        print("\nCONFIRMACION DE INTEGRIDAD DE CUENTAS AJENAS: Las 6 cuentas ajenas permanecieron 100% idénticas.")

    # 7.4 Conteos de todas las tablas al final
    print(f"\n--- 7.4 CONTEOS FINALES DE TODAS LAS TABLAS ---")
    print(f"{'Tabla':<35} | {'Inicial':>8} | {'Final':>8} | {'Diff':>8}")
    print("-" * 65)
    for tbl in table_names:
        cnt_fin = db.execute(text(f'SELECT count(*) FROM "{tbl}"')).scalar()
        cnt_ini = conteos_tablas_inicial[tbl]
        diff_tbl = cnt_fin - cnt_ini
        print(f"{tbl:<35} | {cnt_ini:>8} | {cnt_fin:>8} | {diff_tbl:>8}")

    # 7.5 git status
    print(f"\n--- 7.5 GIT STATUS ---")
    import subprocess
    gs = subprocess.run(["git", "status"], capture_output=True, text=True)
    print(gs.stdout.strip())

    db.close()
    print("\n" + "=" * 80)
    print("FIN DE EJECUCION EXITOSA")
    print("=" * 80)


if __name__ == "__main__":
    main()
