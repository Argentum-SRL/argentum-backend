import sys
import os
import json
import time
import uuid

# Asegurar path al backend
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import structlog
import logging
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

from unittest.mock import patch
from sqlalchemy import select, text, func
from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.models.usuario import Usuario
from app.models.transaccion import Transaccion
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync

USUARIO_PRUEBAS_EMAIL = "testingadmin@argentum.com"
TELEFONO_TEST = "+5491100000000"

_testingadmin_id = None

def _mock_buscar_usuario_testingadmin(from_number, db_session):
    global _testingadmin_id
    if _testingadmin_id is None:
        _testingadmin_id = db_session.execute(
            select(Usuario.id).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)
        ).scalar_one_or_none()
    u = db_session.get(Usuario, _testingadmin_id)
    if not u or u.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Intento de resolución a usuario no-testingadmin: {getattr(u, 'email', None)}")
    return u

def make_payload(from_number: str, message: str, wamid: str | None = None) -> bytes:
    if not wamid:
        wamid = f"wamid_reg_{uuid.uuid4().hex[:12]}"
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": from_number or TELEFONO_TEST,
                                    "type": "text",
                                    "text": {"body": message},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")

def run_isolated(fn):
    conn = engine.connect()
    trans = conn.begin()
    respuestas = []
    BoundSession = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        with patch("app.routers.whatsapp_ia.SessionLocal", BoundSession), \
             patch("app.routers.whatsapp_ia._buscar_usuario_por_telefono", side_effect=_mock_buscar_usuario_testingadmin), \
             patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=lambda t, m: respuestas.append((t, m))), \
             patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
            res = fn(conn, BoundSession, respuestas)
            return res
    finally:
        trans.rollback()
        conn.close()

def main():
    def test(conn, Session, respuestas):
        usuario_id = conn.execute(select(Usuario.id).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)).scalar_one_or_none()
        if not usuario_id:
            raise RuntimeError(f"Usuario {USUARIO_PRUEBAS_EMAIL} no encontrado")

        # Asegurar billetera principal Galicia y limpiar estado previo
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": usuario_id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": usuario_id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == usuario_id)).scalar()

        # Enviar mensaje con 3 gastos
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco, 8000 en la verdulería y 3000 en la panadería"),
            time.perf_counter()
        )
        resp_propuesta = respuestas[-1][1] if respuestas else ""

        # Confirmar con "sí"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "sí"),
            time.perf_counter()
        )
        resp_final = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == usuario_id)).scalar()
        tx_creadas = tx_despues - tx_antes

        # Obtener última conversación registrada
        row = conn.execute(
            text("SELECT accion_ejecutada, length(accion_ejecutada) as largo FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": usuario_id}
        ).mappings().first()

        accion_val = row["accion_ejecutada"] if row else None
        accion_len = row["largo"] if row else 0

        print(f"Respuesta propuesta del bot: {resp_propuesta}")
        print(f"Respuesta final del bot: {resp_final}")
        print(f"Cantidad de transacciones creadas: {tx_creadas}")
        print(f"accion_ejecutada valor: {accion_val}")
        print(f"accion_ejecutada largo: {accion_len}")

    run_isolated(test)

if __name__ == "__main__":
    main()
