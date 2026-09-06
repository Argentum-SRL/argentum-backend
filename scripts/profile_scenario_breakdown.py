import time
import os
import sys
import json
from unittest.mock import patch
from decimal import Decimal
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")
os.environ["LOG_LEVEL"] = "CRITICAL"

from app.core.database import SessionLocal, engine
from app.models.usuario import Usuario
from app.models.billetera import Billetera
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from scripts.regresion.suite_regresion_whatsapp import USUARIO_PRUEBAS_EMAIL, TELEFONO_TEST

def _mock_buscar_usuario_testingadmin(from_number, db_session):
    u = db_session.execute(
        select(Usuario).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)
    ).scalar_one_or_none()
    if not u or u.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Intento de resolución a usuario no-testingadmin: {getattr(u, 'email', None)}")
    return u

def make_payload(from_number: str, text_body: str) -> bytes:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": f"wamid_test_{int(time.time()*1000)}",
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text_body},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")

db = SessionLocal()
try:
    u = db.execute(select(Usuario).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)).scalars().first()
    uid = u.id
finally:
    db.close()

# Measure breakdown across 5 runs of a typical scenario (e.g. p3_caso_3)
tiempos = {
    "connect_and_begin": [],
    "patch_setup": [],
    "prep_queries": [],
    "processing": [],
    "rollback": [],
    "conn_close": []
}

for i in range(5):
    t0 = time.perf_counter()
    conn = engine.connect()
    trans = conn.begin()
    t1 = time.perf_counter()
    
    respuestas = []
    BoundSession = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    with patch("app.routers.whatsapp_ia.SessionLocal", BoundSession), \
         patch("app.routers.whatsapp_ia._buscar_usuario_por_telefono", side_effect=_mock_buscar_usuario_testingadmin), \
         patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=lambda t, m: respuestas.append((t, m))), \
         patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
        t2 = time.perf_counter()
        
        # Prep queries
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": uid})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": uid})
        t3 = time.perf_counter()
        
        # Processing
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        t4 = time.perf_counter()
        
    t5 = time.perf_counter()
    trans.rollback()
    t6 = time.perf_counter()
    conn.close()
    t7 = time.perf_counter()
    
    tiempos["connect_and_begin"].append((t1 - t0) * 1000)
    tiempos["patch_setup"].append((t2 - t1) * 1000)
    tiempos["prep_queries"].append((t3 - t2) * 1000)
    tiempos["processing"].append((t4 - t3) * 1000)
    tiempos["rollback"].append((t6 - t5) * 1000)
    tiempos["conn_close"].append((t7 - t6) * 1000)

with open("scripts/tarea2_descomposicion_raw.txt", "w", encoding="utf-8") as f:
    print("=== DESCOMPOSICION REAL DEL TIEMPO POR ESCENARIO ===", file=f)
    for k, vals in tiempos.items():
        avg = sum(vals) / len(vals)
        print(f"{k}: promedio {avg:.2f} ms (muestras: {[round(x, 1) for x in vals]})", file=f)
    total_avg = sum(sum(vals)/len(vals) for vals in tiempos.values())
    print(f"TOTAL PROMEDIO POR ESCENARIO: {total_avg:.2f} ms", file=f)
