import time
import os
import sys
from unittest.mock import patch

sys.path.insert(0, ".")
os.environ["LOG_LEVEL"] = "CRITICAL"

from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.services.dashboard_service import get_cotizacion_usuario, get_resumen_completo
from app.services.contexto_financiero_service import _calcular_saldo_disponible_sync
from app.services.tools_service import get_current_ipc
from app.services.dolar_service import get_cotizaciones_dolar, _cache
from sqlalchemy import event

db = SessionLocal()
try:
    user = db.query(Usuario).filter(Usuario.email == "testingadmin@argentum.com").first()
    
    # Track queries
    query_count = 0
    def count_queries(conn, cursor, statement, parameters, context, executemany):
        global query_count
        query_count += 1
    
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count_queries)

    with open("scripts/tarea4_reporte_raw.txt", "w", encoding="utf-8") as f_out:
        print("=== PROFILING DASHBOARD COMPONENTS ===", file=f_out)
        
        # 1. COTIZACIONES DOLAR
        # Cold cache (calls internet)
        _cache.data = None
        _cache.expires_at = None
        query_count = 0
        t0 = time.perf_counter()
        cot_cold = get_cotizaciones_dolar()
        t_cold_ms = (time.perf_counter() - t0) * 1000
        queries_cot_cold = query_count
        
        # Warm cache (in-memory)
        query_count = 0
        t0 = time.perf_counter()
        cot_warm = get_cotizacion_usuario(user)
        t_warm_ms = (time.perf_counter() - t0) * 1000
        queries_cot_warm = query_count

        print(f"[COTIZACIONES] Cold (con llamada externa HTTP a dolarapi.com): {t_cold_ms:.2f} ms, {queries_cot_cold} queries", file=f_out)
        print(f"[COTIZACIONES] Warm (en memoria TTL 300s): {t_warm_ms:.2f} ms, {queries_cot_warm} queries", file=f_out)
        
        # 2. IPC CACHE
        query_count = 0
        t0 = time.perf_counter()
        ipc_res = get_current_ipc(db)
        t_ipc_ms = (time.perf_counter() - t0) * 1000
        queries_ipc = query_count
        print(f"[IPC] Consulta/Cache DB (get_current_ipc): {t_ipc_ms:.2f} ms, {queries_ipc} queries", file=f_out)

        # 3. SINCRONIZACIÓN BILLETERAS / DISPONIBLE
        query_count = 0
        t0 = time.perf_counter()
        disp = _calcular_saldo_disponible_sync(db, user.id)
        t_disp_ms = (time.perf_counter() - t0) * 1000
        queries_disp = query_count
        print(f"[SALDO DISPONIBLE / BILLETERAS (_calcular_saldo_disponible_sync)]: {t_disp_ms:.2f} ms, {queries_disp} queries", file=f_out)

        # 4. QUERY 1 DE get_resumen_completo (Billeteras + exists_tx + exists_tr)
        from sqlalchemy import select, exists
        from app.models.billetera import Billetera
        from app.models.transaccion import Transaccion
        from app.models.transferencia_interna import TransferenciaInterna
        exists_tx = exists().where(Transaccion.billetera_id == Billetera.id)
        exists_tr = exists().where((TransferenciaInterna.billetera_origen_id == Billetera.id) | (TransferenciaInterna.billetera_destino_id == Billetera.id))
        stmt_billeteras = select(Billetera, (exists_tx | exists_tr).label("has_tx")).where(Billetera.usuario_id == user.id)
        
        query_count = 0
        t0 = time.perf_counter()
        rows_billeteras = db.execute(stmt_billeteras).all()
        t_bill_ms = (time.perf_counter() - t0) * 1000
        queries_bill = query_count
        print(f"[BILLETERAS LIST + EXISTS TX/TR]: {t_bill_ms:.2f} ms, {queries_bill} queries", file=f_out)

        # Total dashboard full run
        query_count = 0
        t0 = time.perf_counter()
        full_res = get_resumen_completo(db, user)
        t_full_ms = (time.perf_counter() - t0) * 1000
        queries_full = query_count
        print(f"[TOTAL DASHBOARD FULL get_resumen_completo]: {t_full_ms:.2f} ms, {queries_full} queries", file=f_out)

finally:
    db.close()
