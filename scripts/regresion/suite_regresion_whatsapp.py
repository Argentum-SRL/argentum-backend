"""
Suite consolidada de regresión de WhatsApp para Argentum.
Ejecuta todos los escenarios acumulados (Puntos 3, 4, 5 y 6) con verificación automática y rollback total.
"""
import sys
import os
import json
import time
import uuid
import threading
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

# Asegurar path al backend
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import structlog
import logging
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

from sqlalchemy import select, text, func
from sqlalchemy.orm import sessionmaker, Session
from app.core.database import SessionLocal, engine
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
)
from app.routers.whatsapp_ia import (
    _procesar_webhook_whatsapp_sync,
    _construir_propuesta_transaccion,
    _confirmar_propuesta_transaccion,
    _resolver_categoria_y_subcategoria,
)

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
                                    "from": from_number,
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
    """Ejecuta una función en una transacción aislada que siempre termina en rollback."""
    conn = engine.connect()
    trans = conn.begin()
    respuestas = []
    BoundSession = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        with patch("app.routers.whatsapp_ia.SessionLocal", BoundSession), \
             patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=lambda t, m: respuestas.append((t, m))), \
             patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
            res = fn(conn, BoundSession, respuestas)
            return res
    finally:
        trans.rollback()
        conn.close()

def resolver_datos_base(db: Session):
    emails = ["mrm291201@gmail.com", "angieperiolo@hotmail.com", "giordaninosebas@gmail.com"]
    usuarios = {}
    for email in emails:
        u = db.execute(select(Usuario).where(Usuario.email == email)).scalars().first()
        if not u:
            raise RuntimeError(f"Usuario requerido no encontrado en base de datos: {email}")
        bills = db.execute(select(Billetera).where(Billetera.usuario_id == u.id)).scalars().all()
        usuarios[email] = {
            "usuario": u,
            "billeteras": {b.nombre: b for b in bills},
        }
    return usuarios

def obtener_conteos_base(db: Session):
    tx_cnt = db.execute(select(func.count(Transaccion.id))).scalar()
    conv_cnt = db.execute(select(func.count(ConversacionWpp.id))).scalar()
    msg_cnt = db.execute(select(text("count(*)")).select_from(text("mensajes_whatsapp_procesados"))).scalar()
    saldos = {}
    for b in db.execute(select(Billetera).order_by(Billetera.id)).scalars().all():
        saldos[b.id] = b.saldo_actual
    return {"tx": tx_cnt, "conv": conv_cnt, "msg": msg_cnt, "saldos": saldos}

# ==============================================================================
# ESCENARIOS PUNTO 3: Resolución de Billeteras (10 casos)
# ==============================================================================

def p3_caso_1(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_2(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_3(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_4(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "Santander JJ"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_5(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "9"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_6(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_7(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        hace_31_min = datetime.now(timezone.utc) - timedelta(minutes=31)
        conv_vencida = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_venc_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Balanz\n2. Efectivo ARS",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            confianza=Decimal("0.900"),
            fecha=hace_31_min,
        )
        db.add(conv_vencida)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_8(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Efectivo ARS. ¿Va?\nSi fue con otra, decime cuál.",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Efectivo ARS"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "no, fue en Mercado Pago"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_9(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "cobré 800000 de sueldo"), time.perf_counter())
        pregunta = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "1"), time.perf_counter())
        propuesta = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        return f"{pregunta}\n---\n{propuesta}"
    return run_isolated(test)

def p3_caso_10(datos):
    u = datos["giordaninosebas@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 4: Cambios de tema y gestión de slots (8 casos + 6 variantes de no)
# ==============================================================================

def p4_caso_1(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "hola"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_2(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 12000 en verdulería"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_3(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Efectivo ARS. ¿Va?",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Efectivo ARS"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "no"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_4(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Efectivo ARS. ¿Va?",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Efectivo ARS"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "no"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "dale"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        count_tx = db.execute(
            select(text("count(*)")).select_from(Transaccion).where(
                Transaccion.usuario_id == u.id, Transaccion.monto == Decimal("5000")
            )
        ).scalar()
        return f"{resp} (txs_creadas={count_tx})"
    return run_isolated(test)

def p4_caso_5(datos):
    u = datos["giordaninosebas@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "buenas"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_6(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "cuánto gasté en pizza"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_7(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        hace_31_min = datetime.now(timezone.utc) - timedelta(minutes=31)
        conv_vencida = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_venc_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Balanz\n2. Efectivo ARS",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            confianza=Decimal("0.900"),
            fecha=hace_31_min,
        )
        db.add(conv_vencida)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_8(datos):
    u = datos["mrm291201@gmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "sí"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_variante_no(datos, msg_variante, email="angieperiolo@hotmail.com"):
    u = datos[email]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Efectivo ARS. ¿Va?\nSi fue con otra, decime cuál.",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Efectivo ARS"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, msg_variante), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 5: Control de Duplicados y Concurrencia (7 casos + cuotas)
# ==============================================================================

def p5_caso_1(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_2(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "es nuevo"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_3(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "es un error"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_4(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    b = datos["angieperiolo@hotmail.com"]["billeteras"]["Mercado Pago"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")
        tx_antigua = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="TEST_REG_ANTIGUA",
            metodo_pago="efectivo",
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(hours=2, minutes=5),
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db.add(tx_antigua)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_5(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en la farmacia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_6_concurrente(datos):
    """Prueba concurrencia real garantizando limpieza absoluta de base de datos."""
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    b = datos["angieperiolo@hotmail.com"]["billeteras"]["Mercado Pago"]
    saldo_original = b.saldo_actual
    t_inicio = datetime.now(timezone.utc) - timedelta(seconds=5)
    
    db = SessionLocal()
    pid = uuid.uuid4()
    p_wamid = f"reg_c6_prop_{uuid.uuid4().hex[:8]}"
    prop = ConversacionWpp(
        id=pid,
        usuario_id=u.id,
        wamid=p_wamid,
        mensaje_usuario="gasté 5000 en el kiosco",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot=f"Voy a anotar $5.000 en Kiosco desde {b.nombre}. ¿Va?",
        intent_detectado="registrar_transaccion",
        entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": b.nombre},
        slot_filling_activo=False,
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(prop)
    db.commit()
    db.close()

    resp_c6 = []
    lock = threading.Lock()
    def mock_envio(to, msg):
        with lock:
            resp_c6.append(msg)

    bar = threading.Barrier(2)
    def worker(wid):
        payload = make_payload(u.telefono_normalizado, "sí", f"reg_c6_conf_{wid}")
        bar.wait()
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

    try:
        with patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=mock_envio), \
             patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
            th1 = threading.Thread(target=worker, args=(1,))
            th2 = threading.Thread(target=worker, args=(2,))
            th1.start()
            th2.start()
            th1.join()
            th2.join()
    finally:
        # Limpieza absoluta de los registros concurrentes
        clean_db = SessionLocal()
        clean_db.execute(text("DELETE FROM transacciones WHERE usuario_id = :uid AND monto = 5000.00 AND fecha_creacion >= :tmin"), {"uid": u.id, "tmin": t_inicio})
        clean_db.execute(text("DELETE FROM mensajes_whatsapp_procesados WHERE wamid LIKE 'reg_c6_%'"))
        clean_db.execute(text("DELETE FROM conversaciones_wpp WHERE usuario_id = :uid AND (wamid LIKE 'reg_c6_%' OR id = :pid)"), {"uid": u.id, "pid": pid})
        clean_db.execute(text("UPDATE billeteras SET saldo_actual = :s WHERE id = :bid"), {"s": saldo_original, "bid": b.id})
        clean_db.commit()
        clean_db.close()

    # Verificar que exactamente 1 confirmó con éxito y 1 fue rechazada por ya confirmada
    exitos = [r for r in resp_c6 if "registrado" in r.lower()]
    dups = [r for r in resp_c6 if "ya fue confirmada" in r.lower() or "no tenés ninguna operación pendiente" in r.lower()]
    return f"Exitos={len(exitos)}, Rechazados_por_concurrencia={len(dups)}"

def p5_caso_7(datos):
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco y otros 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_cuotas(datos):
    from app.models.transaccion import MetodoPago
    u = datos["angieperiolo@hotmail.com"]["usuario"]
    b = datos["angieperiolo@hotmail.com"]["billeteras"]["Mercado Pago"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")
        tx_cuota = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="Cuota 1/3 Kiosco",
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=5),
            es_recurrente=False,
            es_cuota_hija=True,
            es_padre_cuotas=False,
        )
        db.add(tx_cuota)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(u.telefono_normalizado, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 6: Veracidad en Fechas, Monedas, Lotes y Descarte (10 casos)
# ==============================================================================

def p6_ejecutar_caso(datos, nombre_caso, ent):
    hoy = datetime.now(timezone.utc).date()
    ayer = hoy - timedelta(days=1)
    
    def test(conn, Session, respuestas):
        db = Session()
        if ent.get("moneda") == "USD" and "Efectivo USD" in (ent.get("billetera_origen") or ""):
            email = "mrm291201@gmail.com"
        elif "Galicia" in (ent.get("billetera_destino") or ""):
            email = "mrm291201@gmail.com"
        else:
            email = "angieperiolo@hotmail.com"
        u = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one()

        b_nom = ent.get("billetera_destino") if ent.get("tipo") == "ingreso" else ent.get("billetera_origen")
        b_obj = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == b_nom)).scalars().first()
        b_mon = b_obj.moneda if b_obj else (Moneda.USD if "USD" in b_nom else Moneda.ARS)

        entidades_caso = dict(ent)
        if "transacciones_adicionales" in ent:
            entidades_caso["transacciones_adicionales"] = [dict(x) for x in ent["transacciones_adicionales"]]

        propuesta_texto = _construir_propuesta_transaccion(entidades_caso, b_nom, se_asumio_principal=False, billetera_moneda=b_mon)

        if "No se puede registrar ningún movimiento." in propuesta_texto:
            return f"Propuesta:\n{propuesta_texto}\nConfirmación:\nNO_APLICA"

        pid = uuid.uuid4()
        conv = ConversacionWpp(
            id=pid,
            usuario_id=u.id,
            wamid=f"sim_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="simulacion",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot=propuesta_texto,
            intent_detectado="registrar_transaccion",
            entidades=entidades_caso,
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc),
        )
        db.add(conv)
        db.commit()

        tx, msg_confirm, ya_conf = _confirmar_propuesta_transaccion(u, db, propuesta_id=pid)
        return f"Propuesta:\n{propuesta_texto}\nConfirmación:\n{msg_confirm}"

    return run_isolated(test)

# ==============================================================================
# RUNNER GENERAL DE SUITE
# ==============================================================================

def correr_suite_completa():
    print("=== INICIANDO SUITE CONSOLIDADA DE REGRESION DE WHATSAPP ===")
    db = SessionLocal()
    datos = resolver_datos_base(db)
    conteos_inicio = obtener_conteos_base(db)
    db.close()

    hoy = datetime.now(timezone.utc).date()
    ayer = hoy - timedelta(days=1)

    escenarios = [
        # --- PUNTO 3 ---
        {
            "id": "P3.1",
            "punto": "Punto 3",
            "nombre": "Usuario con billetera principal dice 'gasté 5000 en el kiosco' sin nombrar billetera",
            "ejecutar": lambda: p3_caso_1(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nSi fue con otra, decime cuál.",
            "match": "exacto",
        },
        {
            "id": "P3.2",
            "punto": "Punto 3",
            "nombre": "Usuario sin billetera principal, lo mismo",
            "ejecutar": lambda: p3_caso_2(datos),
            "esperado": "¿Desde qué billetera salió la plata?\n\n1. Balanz\n2. Efectivo ARS\n3. Galicia\n4. Santander JJ",
            "match": "exacto",
        },
        {
            "id": "P3.3",
            "punto": "Punto 3",
            "nombre": "Responde '2' a un menú de billeteras",
            "ejecutar": lambda: p3_caso_3(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Efectivo ARS. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P3.4",
            "punto": "Punto 3",
            "nombre": "Responde con el nombre de la billetera en vez del número",
            "ejecutar": lambda: p3_caso_4(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Santander JJ. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P3.5",
            "punto": "Punto 3",
            "nombre": "Responde un número fuera de rango",
            "ejecutar": lambda: p3_caso_5(datos),
            "esperado": "Opción inválida. Elegí un número del 1 al 4.",
            "match": "exacto",
        },
        {
            "id": "P3.6",
            "punto": "Punto 3",
            "nombre": "Manda un número sin ninguna pregunta pendiente",
            "ejecutar": lambda: p3_caso_6(datos),
            "esperado": "Mandaste solo un número. Si querés registrar un movimiento, escribí el monto y el concepto (por ejemplo: 'gasté 5000 en el kiosco').",
            "match": "exacto",
        },
        {
            "id": "P3.7",
            "punto": "Punto 3",
            "nombre": "Responde a un menú 31 minutos después",
            "ejecutar": lambda: p3_caso_7(datos),
            "esperado": "Esa operación ya venció. Podés volver a mandarla.",
            "match": "exacto",
        },
        {
            "id": "P3.8",
            "punto": "Punto 3",
            "nombre": "Recibe una propuesta y responde 'no, fue en Mercado Pago'",
            "ejecutar": lambda: p3_caso_8(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Mercado Pago. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P3.9",
            "punto": "Punto 3",
            "nombre": "Dice 'cobré 800000 de sueldo' y elige billetera de destino",
            "ejecutar": lambda: p3_caso_9(datos),
            "esperado": "¿A qué billetera entró la plata?\n\n1. Balanz\n2. Efectivo ARS\n3. Galicia\n4. Santander JJ\n---\nVoy a registrar un ingreso de $800.000 en Sueldo a Balanz. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P3.10",
            "punto": "Punto 3",
            "nombre": "Usuario con una sola billetera en pesos dice 'gasté 5000'",
            "ejecutar": lambda: p3_caso_10(datos),
            "esperado": "Voy a anotar $5.000 en Otros desde Efectivo ARS. ¿Va?",
            "match": "exacto",
        },

        # --- PUNTO 4 ---
        {
            "id": "P4.1",
            "punto": "Punto 4",
            "nombre": "Operación a medias y manda 'hola'",
            "ejecutar": lambda: p4_caso_1(datos),
            "esperado": "Hola. Tenías una operación a medias (anotar $5.000 en Kiosco). Podés completarla o empezar de nuevo.\nTambién podés registrar otro gasto, ingreso o consultar tus saldos.",
            "match": "exacto",
        },
        {
            "id": "P4.2",
            "punto": "Punto 4",
            "nombre": "Operación a medias y manda un gasto distinto",
            "ejecutar": lambda: p4_caso_2(datos),
            "esperado": "Descarté la de $5.000 en Kiosco. Para los $12.000 en Verdulería:\n\n¿Desde qué billetera salió la plata?\n\n1. Balanz\n2. Efectivo ARS\n3. Galicia\n4. Santander JJ",
            "match": "exacto",
        },
        {
            "id": "P4.3",
            "punto": "Punto 4",
            "nombre": "Recibe una propuesta y responde 'no'",
            "ejecutar": lambda: p4_caso_3(datos),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },
        {
            "id": "P4.4",
            "punto": "Punto 4",
            "nombre": "Tras cancelar manda 'dale' (verifica 0 txs en BD)",
            "ejecutar": lambda: p4_caso_4(datos),
            "esperado": "No tenés ninguna operación pendiente para confirmar. (txs_creadas=0)",
            "match": "exacto",
        },
        {
            "id": "P4.5",
            "punto": "Punto 4",
            "nombre": "Manda 'buenas' sin nada pendiente",
            "ejecutar": lambda: p4_caso_5(datos),
            "esperado": "Hola. Podés registrar gastos, ingresos o consultar tus saldos y proyecciones. Por ejemplo: 'gasté 5000 en el kiosco'.",
            "match": "exacto",
        },
        {
            "id": "P4.6",
            "punto": "Punto 4",
            "nombre": "Manda 'cuánto gasté en pizza'",
            "ejecutar": lambda: p4_caso_6(datos),
            "esperado": "No entendí ese mensaje. Por ahora puedo registrar gastos e ingresos, o consultar tus saldos y proyecciones. Por ejemplo: 'gasté 5000 en el kiosco' o 'cuánta plata tengo'.",
            "match": "exacto",
        },
        {
            "id": "P4.7",
            "punto": "Punto 4",
            "nombre": "Responde 31 minutos después",
            "ejecutar": lambda: p4_caso_7(datos),
            "esperado": "Esa operación ya venció. Podés volver a mandarla.",
            "match": "exacto",
        },
        {
            "id": "P4.8",
            "punto": "Punto 4",
            "nombre": "Manda 'sí' sin nada pendiente",
            "ejecutar": lambda: p4_caso_8(datos),
            "esperado": "No tenés ninguna operación pendiente para confirmar.",
            "match": "exacto",
        },
        # Variantes de no
        {
            "id": "P4.VAR1",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no'",
            "ejecutar": lambda: p4_variante_no(datos, "no"),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },
        {
            "id": "P4.VAR2",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no, fue en Mercado Pago'",
            "ejecutar": lambda: p4_variante_no(datos, "no, fue en Mercado Pago"),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Mercado Pago. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P4.VAR3",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no fue en galicia'",
            "ejecutar": lambda: p4_variante_no(datos, "no fue en galicia", email="mrm291201@gmail.com"),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P4.VAR4",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no, cancelá'",
            "ejecutar": lambda: p4_variante_no(datos, "no, cancelá"),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },
        {
            "id": "P4.VAR5",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'nooo'",
            "ejecutar": lambda: p4_variante_no(datos, "nooo"),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },
        {
            "id": "P4.VAR6",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no gracias'",
            "ejecutar": lambda: p4_variante_no(datos, "no gracias"),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },

        # --- PUNTO 5 ---
        {
            "id": "P5.1",
            "punto": "Punto 5",
            "nombre": "Gasto repetido a los cinco minutos",
            "ejecutar": lambda: p5_caso_1(datos),
            "esperado": "¿Es un movimiento nuevo o se te repitió?",
            "match": "contiene",
        },
        {
            "id": "P5.2",
            "punto": "Punto 5",
            "nombre": "Ante la pregunta de duplicado, responde que es nuevo",
            "ejecutar": lambda: p5_caso_2(datos),
            "esperado": "Listo. $5.000 en Kiosco desde Mercado Pago — registrado.",
            "match": "contiene",
        },
        {
            "id": "P5.3",
            "punto": "Punto 5",
            "nombre": "Ante la pregunta de duplicado, responde que es un error",
            "ejecutar": lambda: p5_caso_3(datos),
            "esperado": "Listo, no anoto nada.",
            "match": "exacto",
        },
        {
            "id": "P5.4",
            "punto": "Punto 5",
            "nombre": "Gasto igual de hace dos horas (>1h)",
            "ejecutar": lambda: p5_caso_4(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Mercado Pago. ¿Va?\nSi fue con otra, decime cuál.",
            "match": "exacto",
        },
        {
            "id": "P5.5",
            "punto": "Punto 5",
            "nombre": "Mismo monto, otra categoría (sin advertencia)",
            "ejecutar": lambda: p5_caso_5(datos),
            "esperado": "Voy a anotar $5.000 en Farmacia desde Mercado Pago. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P5.6",
            "punto": "Punto 5",
            "nombre": "Dos confirmaciones concurrentes",
            "ejecutar": lambda: p5_caso_6_concurrente(datos),
            "esperado": "Exitos=1, Rechazados_por_concurrencia=1",
            "match": "exacto",
        },
        {
            "id": "P5.7",
            "punto": "Punto 5",
            "nombre": "Lote con dos movimientos idénticos",
            "ejecutar": lambda: p5_caso_7(datos),
            "esperado": "Mandaste 2 movimientos iguales de $5.000 en Kiosco desde Mercado Pago. ¿Son dos gastos distintos o se te repitió?",
            "match": "exacto",
        },
        {
            "id": "P5.CUOTAS",
            "punto": "Punto 5",
            "nombre": "Cuotas de tarjeta no disparan falso positivo de duplicado",
            "ejecutar": lambda: p5_caso_cuotas(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Mercado Pago. ¿Va?\nSi fue con otra, decime cuál.",
            "match": "exacto",
        },

        # --- PUNTO 6 ---
        {
            "id": "P6.1",
            "punto": "Punto 6",
            "nombre": "Gasto de hoy",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto hoy", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Mercado Pago", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Mercado Pago. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Mercado Pago — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.2",
            "punto": "Punto 6",
            "nombre": "Gasto de ayer",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto ayer", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Mercado Pago", "fecha": ayer.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Mercado Pago (ayer). ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Mercado Pago (ayer) — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.3",
            "punto": "Punto 6",
            "nombre": "Gasto del 31 de agosto",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto 31 agosto", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Mercado Pago", "fecha": "2026-08-31"
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Mercado Pago (el 31 de agosto). ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Mercado Pago (el 31 de agosto) — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.4",
            "punto": "Punto 6",
            "nombre": "Gasto de hace tres meses (>60 días)",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto 3 meses", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Mercado Pago", "fecha": "2026-06-03"
            }),
            "esperado": "Propuesta:\nNo puedo registrar movimientos de más de 60 días atrás. Va a quedar con fecha de hoy.\nVoy a anotar $5.000 en Kiosco desde Mercado Pago. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Mercado Pago — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.5",
            "punto": "Punto 6",
            "nombre": "Gasto con fecha futura",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto futuro", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Mercado Pago", "fecha": "2026-09-10"
            }),
            "esperado": "Propuesta:\nNo puedo registrar movimientos con fecha futura porque todavía no ocurrieron. Va a quedar con fecha de hoy.\nVoy a anotar $5.000 en Kiosco desde Mercado Pago. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Mercado Pago — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.6",
            "punto": "Punto 6",
            "nombre": "Gasto en dólares",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto USD", {
                "monto": 50, "moneda": "USD", "tipo": "egreso", "categoria": "Otros", "billetera_origen": "Efectivo USD", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar US$50 en Otros desde Efectivo USD. ¿Va?\nConfirmación:\nListo. US$50 en Otros desde Efectivo USD — registrado.\nLa billetera quedó en negativo.",
            "match": "exacto",
        },
        {
            "id": "P6.7",
            "punto": "Punto 6",
            "nombre": "Lote con uno descartado",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Lote descalce", {
                "monto": 1000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Efectivo ARS", "fecha": hoy.isoformat(),
                "transacciones_adicionales": [
                    {"monto": 2000, "moneda": "ARS", "tipo": "egreso", "categoria": "Panadería", "fecha": ayer.isoformat()},
                    {"monto": 10, "moneda": "USD", "tipo": "egreso", "categoria": "Farmacia", "fecha": hoy.isoformat()}
                ]
            }),
            "esperado": "Propuesta:\nNo se pudo registrar Farmacia de US$10 porque es en dólares y la billetera Efectivo ARS es en pesos.\nVoy a anotar 2 movimientos desde Efectivo ARS: $1.000 en Kiosco, $2.000 en Panadería (ayer). ¿Va?\nConfirmación:\nListo. 2 movimientos desde Efectivo ARS: $1.000 en Kiosco, $2.000 en Panadería (ayer) — registrados.\nLa billetera quedó en negativo.",
            "match": "exacto",
        },
        {
            "id": "P6.8",
            "punto": "Punto 6",
            "nombre": "Gasto que deja la billetera en negativo",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto negativo", {
                "monto": 60000, "moneda": "ARS", "tipo": "egreso", "categoria": "Supermercado", "billetera_origen": "Mercado Pago", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $60.000 en Supermercado desde Mercado Pago. ¿Va?\nConfirmación:\nListo. $60.000 en Supermercado desde Mercado Pago — registrado.\nLa billetera quedó en negativo.",
            "match": "exacto",
        },
        {
            "id": "P6.9",
            "punto": "Punto 6",
            "nombre": "Ingreso",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Ingreso", {
                "monto": 80000, "moneda": "ARS", "tipo": "ingreso", "categoria": "Sueldo", "billetera_destino": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a registrar un ingreso de $80.000 en Sueldo a Galicia. ¿Va?\nConfirmación:\nListo. Ingreso de $80.000 en Sueldo a Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.10",
            "punto": "Punto 6",
            "nombre": "Lote con todos descartados",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Lote todos descartados", {
                "monto": 10, "moneda": "USD", "tipo": "egreso", "categoria": "Farmacia", "billetera_origen": "Efectivo ARS", "fecha": hoy.isoformat(),
                "transacciones_adicionales": [
                    {"monto": 20, "moneda": "USD", "tipo": "egreso", "categoria": "Supermercado", "fecha": hoy.isoformat()}
                ]
            }),
            "esperado": "Propuesta:\nNo se pudo registrar Farmacia de US$10 porque es en dólares y la billetera Efectivo ARS es en pesos.\nNo se pudo registrar Supermercado de US$20 porque es en dólares y la billetera Efectivo ARS es en pesos.\nNo se puede registrar ningún movimiento.\nConfirmación:\nNO_APLICA",
            "match": "exacto",
        },
    ]

    total = len(escenarios)
    aprobados = 0
    fallidos = 0
    detalles_fallidos = []

    print(f"Total escenarios a ejecutar: {total}\n")

    for i, esc in enumerate(escenarios, 1):
        eid = esc["id"]
        punto = esc["punto"]
        nombre = esc["nombre"]
        esperado = esc["esperado"]
        match_tipo = esc["match"]

        t0 = time.perf_counter()
        try:
            obtenido = esc["ejecutar"]()
            dur = time.perf_counter() - t0

            if match_tipo == "exacto":
                pasa = (obtenido.strip() == esperado.strip())
            else:
                pasa = (esperado.strip() in obtenido.strip())

            if pasa:
                aprobados += 1
                print(f"[{eid}] {nombre}: APROBADO ({dur:.2f}s)")
            else:
                fallidos += 1
                print(f"[{eid}] {nombre}: FALLIDO ({dur:.2f}s)")
                detalles_fallidos.append({
                    "id": eid,
                    "nombre": nombre,
                    "esperado": esperado,
                    "obtenido": obtenido,
                })
        except Exception as e:
            dur = time.perf_counter() - t0
            fallidos += 1
            print(f"[{eid}] {nombre}: ERROR ({dur:.2f}s) -> {e}")
            detalles_fallidos.append({
                "id": eid,
                "nombre": nombre,
                "esperado": esperado,
                "obtenido": f"EXCEPCION: {type(e).__name__}: {e}",
            })

    print("\n=== RESUMEN DE EJECUCION ===")
    print(f"Total: {total} | Aprobados: {aprobados} | Fallidos: {fallidos}")

    if detalles_fallidos:
        print("\n=== DETALLE DE ESCENARIOS FALLIDOS ===")
        for d in detalles_fallidos:
            print(f"\n--- [{d['id']}] {d['nombre']} ---")
            print("ESPERADO:")
            print(d["esperado"])
            print("OBTENIDO:")
            print(d["obtenido"])

    # Verificación estricta de rollback y conteos
    db = SessionLocal()
    conteos_fin = obtener_conteos_base(db)
    db.close()

    print("\n=== VERIFICACION DE ROLLBACK Y CONTEOS ===")
    print(f"Transacciones: antes={conteos_inicio['tx']} | después={conteos_fin['tx']}")
    print(f"Conversaciones: antes={conteos_inicio['conv']} | después={conteos_fin['conv']}")
    print(f"Mensajes procesados: antes={conteos_inicio['msg']} | después={conteos_fin['msg']}")
    
    saldos_intactos = (conteos_inicio["saldos"] == conteos_fin["saldos"])
    print(f"Saldos de billeteras intactos: {'SÍ' if saldos_intactos else 'NO'}")
    
    sin_residuos = (
        conteos_inicio["tx"] == conteos_fin["tx"] and
        conteos_inicio["conv"] == conteos_fin["conv"] and
        conteos_inicio["msg"] == conteos_fin["msg"] and
        saldos_intactos
    )
    print(f"¿Rollback total verificado (cero residuo)?: {'SÍ' if sin_residuos else 'NO'}")

    return total, aprobados, fallidos, detalles_fallidos

if __name__ == "__main__":
    correr_suite_completa()
