"""
Suite consolidada de regresión de WhatsApp para Argentum.
Ejecuta todos los escenarios acumulados (Puntos 3, 4, 5, 6 y 7) usando exclusivamente testingadmin@argentum.com
con verificación automática y rollback total.
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
from app.models.tarjeta_credito import TarjetaCredito
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transferencia_interna import TransferenciaInterna
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
    MetodoPago,
)
from app.routers.whatsapp_ia import (
    _procesar_webhook_whatsapp_sync,
    _construir_propuesta_transaccion,
    _confirmar_propuesta_transaccion,
    _resolver_categoria_y_subcategoria,
)
from app.services import ai_service
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.schemas.suscripcion import SuscripcionCreate
from app.services import suscripcion_service

USUARIO_PRUEBAS_EMAIL = "testingadmin@argentum.com"
TELEFONO_TEST = "+5491100000000"

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

def _mock_buscar_usuario_testingadmin(from_number, db_session):
    u = db_session.execute(
        select(Usuario).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)
    ).scalar_one_or_none()
    if not u or u.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Intento de resolución a usuario no-testingadmin: {getattr(u, 'email', None)}")
    return u

def run_isolated(fn):
    """Ejecuta una función en una transacción aislada que siempre termina en rollback."""
    conn = engine.connect()
    trans = conn.begin()
    conn.execute(text("ALTER TABLE tarjetas_credito ADD COLUMN IF NOT EXISTS apodo VARCHAR(50);"))
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

def resolver_datos_base(db: Session):
    u = db.execute(select(Usuario).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)).scalars().first()
    if not u:
        raise RuntimeError(f"ABORT CRITICO: Usuario de pruebas no encontrado en base de datos: {USUARIO_PRUEBAS_EMAIL}")
    if u.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Usuario resuelto no es testingadmin: {u.email}")
    
    bills = db.execute(select(Billetera).where(Billetera.usuario_id == u.id)).scalars().all()
    return {
        USUARIO_PRUEBAS_EMAIL: {
            "usuario": u,
            "billeteras": {b.nombre: b for b in bills},
        }
    }

def obtener_conteos_base(db: Session):
    tx_cnt = db.execute(select(func.count(Transaccion.id))).scalar()
    conv_cnt = db.execute(select(func.count(ConversacionWpp.id))).scalar()
    msg_cnt = db.execute(select(text("count(*)")).select_from(text("mensajes_whatsapp_procesados"))).scalar()
    saldos = {}
    for b in db.execute(select(Billetera).order_by(Billetera.id)).scalars().all():
        saldos[str(b.id)] = b.saldo_actual
    return {"tx": tx_cnt, "conv": conv_cnt, "msg": msg_cnt, "saldos": saldos}

# Baseline documentado de diferencias aceptadas en reconciliación.
# Proviene del alta histórica de datos (junio 2026), donde las billeteras
# Galicia de mrm291201 y su réplica testingadmin tienen saldo_inicial en 0
# pero sus movimientos bancarios acumulados difieren en -$800.941 respecto al saldo guardado.
# La suite fallará si aparece una diferencia NUEVA o si alguna de estas cambia.
DIFERENCIAS_RECONCILIACION_BASELINE = {
    ("mrm291201@gmail.com", "Galicia"): Decimal("-800941.00"),
    ("testingadmin@argentum.com", "Galicia"): Decimal("-800941.00"),
}

def verificar_reconciliacion_billeteras(db: Session):
    """
    Compara el saldo_actual guardado de cada una de las 21 billeteras contra
    el saldo calculado a partir de sus movimientos confirmados y transferencias:
      saldo_inicial + sum(ingresos) - sum(egresos) + sum(tr_in) - sum(tr_out)
    donde las transacciones computadas son aquellas que afectan saldo (no crédito,
    no pendientes, fecha <= hoy).
    Compara contra el baseline conocido de diferencias históricas y reporta
    discrepancias solo si surge una diferencia NUEVA o cambia una existente.
    """
    from app.models.billetera import Billetera
    from app.models.usuario import Usuario
    from app.utils.fecha import hoy_argentina
    
    hoy = hoy_argentina()
    billeteras = db.execute(
        select(Billetera, Usuario.email)
        .join(Usuario, Billetera.usuario_id == Usuario.id)
        .order_by(Usuario.email, Billetera.nombre)
    ).all()
    
    discrepancias_no_esperadas = []
    detalles = []
    
    for b, email in billeteras:
        s_guardado = b.saldo_actual
        s_inicial = b.saldo_inicial or Decimal("0.00")
        
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
        esperado_diff = DIFERENCIAS_RECONCILIACION_BASELINE.get((email, b.nombre), Decimal("0.00"))
        coincide_con_baseline = (diff == esperado_diff)
        
        item = {
            "email": email,
            "billetera": b.nombre,
            "guardado": s_guardado,
            "calculado": s_calc,
            "diferencia": diff,
            "esperado_diff": esperado_diff,
            "ok": coincide_con_baseline
        }
        detalles.append(item)
        if not coincide_con_baseline:
            discrepancias_no_esperadas.append(item)
            
    return len(discrepancias_no_esperadas) == 0, discrepancias_no_esperadas, detalles

SALDOS_REFERENCIA_21 = {
    ("albanopavia@gmail.com", "Banco Nación"): Decimal("577000.00"),
    ("albanopavia@gmail.com", "Efectivo ARS"): Decimal("0.00"),
    ("albanopavia@gmail.com", "Efectivo USD"): Decimal("0.00"),
    ("albanopavia@gmail.com", "Mercado Pago"): Decimal("0.00"),
    ("albanopavia@gmail.com", "Naranja X"): Decimal("0.00"),
    ("angieperiolo@hotmail.com", "Efectivo ARS"): Decimal("800.00"),
    ("angieperiolo@hotmail.com", "Efectivo USD"): Decimal("0.00"),
    ("angieperiolo@hotmail.com", "Mercado Pago"): Decimal("51148.00"),
    ("giordaninosebas@gmail.com", "Efectivo ARS"): Decimal("-30060.00"),
    ("giordaninosebas@gmail.com", "Efectivo USD"): Decimal("0.00"),
    ("mrm291201@gmail.com", "Balanz"): Decimal("453000.00"),
    ("mrm291201@gmail.com", "Efectivo ARS"): Decimal("0.00"),
    ("mrm291201@gmail.com", "Efectivo USD"): Decimal("0.00"),
    ("mrm291201@gmail.com", "Galicia"): Decimal("1311289.73"),
    ("mrm291201@gmail.com", "Santander JJ"): Decimal("0.00"),
    ("sebastiangiordaninoformoso@gmail.com", "Efectivo ARS"): Decimal("0.00"),
    ("sebastiangiordaninoformoso@gmail.com", "Efectivo USD"): Decimal("0.00"),
    ("testingadmin@argentum.com", "Efectivo ARS"): Decimal("0.00"),
    ("testingadmin@argentum.com", "Efectivo USD"): Decimal("0.00"),
    ("testingadmin@argentum.com", "Galicia"): Decimal("502514.00"),
    ("testingadmin@argentum.com", "Santander"): Decimal("84270.29"),
}

def verificar_saldos_contra_referencia(db: Session):
    """
    Compara los saldos actuales de las 21 billeteras contra los valores
    de referencia históricos y reporta si alguno difiere.
    """
    from app.models.billetera import Billetera
    from app.models.usuario import Usuario

    billeteras = db.execute(
        select(Billetera, Usuario.email)
        .join(Usuario, Billetera.usuario_id == Usuario.id)
        .order_by(Usuario.email, Billetera.nombre)
    ).all()

    desvios = []
    detalles = []
    for b, email in billeteras:
        ref = SALDOS_REFERENCIA_21.get((email, b.nombre))
        actual = b.saldo_actual
        diff = actual - ref if ref is not None else None
        item = {
            "email": email,
            "billetera": b.nombre,
            "actual": actual,
            "referencia": ref,
            "diff": diff
        }
        detalles.append(item)
        if ref is not None and actual != ref:
            desvios.append(item)
    return len(desvios) == 0, desvios, detalles


# ==============================================================================
# ESCENARIOS PUNTO 3: Resolución de Billeteras (10 casos)
# ==============================================================================

def p3_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        # Opción 2 en menú de testingadmin (Efectivo ARS, Galicia, Santander) es Galicia
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "Santander"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "9"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_6(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        hace_31_min = datetime.now(timezone.utc) - timedelta(minutes=31)
        conv_vencida = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_venc_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo ARS\n2. Galicia\n3. Santander",
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
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_8(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nSi fue con otra, decime cuál.",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no, fue en Santander"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_9(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cobré 800000 de sueldo"), time.perf_counter())
        pregunta = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "1"), time.perf_counter())
        propuesta = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        return f"{pregunta}\n---\n{propuesta}"
    return run_isolated(test)

def p3_caso_10(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND nombre IN ('Galicia', 'Santander')"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 4: Cambios de tema y gestión de slots (8 casos + 6 variantes de no)
# ==============================================================================

def p4_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "hola"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 12000 en verdulería"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        respuestas.clear()
        txs_antes = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "dale"), time.perf_counter())
        txs_despues = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        resp = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        return f"{resp} (txs_creadas={txs_despues - txs_antes})"
    return run_isolated(test)

def p4_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "buenas"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_6(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté en pizza"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        hace_31_min = datetime.now(timezone.utc) - timedelta(minutes=31)
        conv_vencida = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_venc_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo ARS\n2. Galicia\n3. Santander",
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
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_8(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_variante_no(datos, msg_variante, billetera_propuesta="Galicia"):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot=f"Voy a anotar $5.000 en Kiosco desde {billetera_propuesta}. ¿Va?",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": billetera_propuesta},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, msg_variante), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 5: Prevención de Duplicados y Concurrencia (8 casos)
# ==============================================================================

def p5_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "es nuevo"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "es un error"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
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
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en la farmacia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_6_concurrente(datos):
    """
    Prueba concurrencia real garantizando limpieza absoluta e infalible de base de datos.
    Usa exclusivamente testingadmin@argentum.com y su billetera Galicia.
    Registra snapshot exacto de IDs de transacciones antes y después, y borra la diferencia.
    """
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    saldo_original = b.saldo_actual
    
    # Snapshot de transacciones existentes antes de la prueba
    snap_db = SessionLocal()
    tx_ids_antes = set(snap_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
    snap_db.close()

    pid = uuid.uuid4()
    p_wamid = f"reg_c6_prop_{uuid.uuid4().hex}"
    wamid1 = f"reg_c6_conf_1_{uuid.uuid4().hex}"
    wamid2 = f"reg_c6_conf_2_{uuid.uuid4().hex}"

    db = SessionLocal()
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

    def _mock_buscar_concurrente(tel, db_sess):
        usr = db_sess.query(Usuario).filter(Usuario.email == USUARIO_PRUEBAS_EMAIL).first()
        if not usr or usr.email != USUARIO_PRUEBAS_EMAIL:
            raise RuntimeError(f"ABORT CRITICO: Concurrencia intentó usar otro usuario: {getattr(usr, 'email', None)}")
        return usr

    bar = threading.Barrier(2)
    def worker(wid, wamid_val):
        payload = make_payload(TELEFONO_TEST, "sí", wamid_val)
        bar.wait()
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

    try:
        with patch("app.routers.whatsapp_ia._buscar_usuario_por_telefono", side_effect=_mock_buscar_concurrente), \
             patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=mock_envio), \
             patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
            th1 = threading.Thread(target=worker, args=(1, wamid1))
            th2 = threading.Thread(target=worker, args=(2, wamid2))
            th1.start()
            th2.start()
            th1.join()
            th2.join()
    finally:
        # Limpieza infalible: identificar exactamente las transacciones creadas por diferencia de conjuntos
        clean_db = SessionLocal()
        tx_ids_despues = set(clean_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        creadas = tx_ids_despues - tx_ids_antes
        for tx_id in creadas:
            clean_db.execute(text("DELETE FROM transacciones WHERE id = :txid"), {"txid": tx_id})

        # Limpieza de mensajes procesados y conversaciones creadas en esta prueba
        clean_db.execute(text("DELETE FROM mensajes_whatsapp_procesados WHERE wamid IN (:w1, :w2, :pw)"), {"w1": wamid1, "w2": wamid2, "pw": p_wamid})
        clean_db.execute(text("DELETE FROM conversaciones_wpp WHERE usuario_id = :uid AND (wamid IN (:w1, :w2, :pw) OR id = :pid)"), {"uid": u.id, "w1": wamid1, "w2": wamid2, "pw": p_wamid, "pid": pid})
        clean_db.execute(text("UPDATE billeteras SET saldo_actual = :s WHERE id = :bid"), {"s": saldo_original, "bid": b.id})
        clean_db.commit()

        # Verificación estricta post-limpieza
        tx_post = set(clean_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        b_actual = clean_db.execute(select(Billetera).where(Billetera.id == b.id)).scalar_one()
        clean_db.close()

        if tx_post != tx_ids_antes:
            sobrantes = tx_post - tx_ids_antes
            raise RuntimeError(f"Limpieza falló: transacciones residuales no borradas: {sobrantes}")
        if b_actual.saldo_actual != saldo_original:
            raise RuntimeError(f"Limpieza falló: saldo billetera {b_actual.saldo_actual} != original {saldo_original}")

    # Verificar que exactamente 1 confirmó con éxito y 1 fue rechazada por ya confirmada
    exitos = [r for r in resp_c6 if "registrado" in r.lower()]
    dups = [r for r in resp_c6 if "ya fue confirmada" in r.lower() or "no tenés ninguna operación pendiente" in r.lower()]
    return f"Exitos={len(exitos)}, Rechazados_por_concurrencia={len(dups)}"

def p5_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y otros 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_cuotas(datos):
    from app.models.transaccion import MetodoPago
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
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
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 6: Veracidad en Fechas, Monedas, Lotes y Descarte (10 casos)
# ==============================================================================

def p6_ejecutar_caso(datos, nombre_caso, ent):
    def test(conn, Session, respuestas):
        db = Session()
        u = db.execute(select(Usuario).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)).scalar_one()

        b_nom = ent.get("billetera_destino") if ent.get("tipo") == "ingreso" else ent.get("billetera_origen")
        b_obj = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == b_nom)).scalars().first()
        b_mon = b_obj.moneda if b_obj else (Moneda.USD if "USD" in (b_nom or "") else Moneda.ARS)

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
# ESCENARIOS PUNTO 7: Categorías estrictas, jerga argentina y descripciones
# ==============================================================================

def p7_ejecutar_caso(datos, mensaje, cat_esperada, desc_esperada):
    def test(conn, Session, respuestas):
        db = Session()
        u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
        res = ai_service.procesar_mensaje(mensaje, u, db)
        cat_obtenida = res.get("entidades", {}).get("categoria")
        desc_obtenida = res.get("entidades", {}).get("descripcion")

        cat_id, sub_id = _resolver_categoria_y_subcategoria(cat_obtenida, u.id, db, tipo="egreso")
        if not cat_id:
            return f"Error: no se pudo resolver categoría {cat_obtenida}"

        return f"Cat: {cat_obtenida} | Desc: {desc_obtenida}"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 8: Deshacer y Corregir movimientos por WhatsApp (11 casos)
# ==============================================================================

def p8_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        
        b_db = db.get(Billetera, b.id)
        s0 = b_db.saldo_actual

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        db.refresh(b_db)
        txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().all()
        assert len(txs) == 1
        assert b_db.saldo_actual == s0 - Decimal("5000")

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        resp_propuesta = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_confirmacion = respuestas[-1][1] if respuestas else ""

        txs_post = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().all()
        db.refresh(b_db)
        
        ok_borrado = (len(txs_post) == 0)
        ok_saldo = (b_db.saldo_actual == s0)

        return f"Propuesta:\n{resp_propuesta}\nConfirmación:\n{resp_confirmacion}\nBorrado: {ok_borrado} | Saldo restaurado: {ok_saldo}"
    return run_isolated(test)

def p8_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("DELETE FROM transacciones WHERE usuario_id = :uid AND origen = 'ia_wpp'"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p8_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p8_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        b_db = db.get(Billetera, b.id)
        s0 = b_db.saldo_actual

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 en supermercado"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eran 3.000 no 30.000"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        db.refresh(b_db)
        
        ok_monto = (tx.monto == Decimal("3000"))
        ok_saldo = (b_db.saldo_actual == s0 - Decimal("3000"))

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nMonto corregido: {ok_monto} | Saldo ajustado (+27k): {ok_saldo}"
    return run_isolated(test)

def p8_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eso era supermercado"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        sub = db.get(Subcategoria, tx.subcategoria_id) if tx and tx.subcategoria_id else None
        cat = db.get(Categoria, tx.categoria_id) if tx and tx.categoria_id else None
        cat_nom = sub.nombre if sub else (cat.nombre if cat else "")

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nCategoría final: {cat_nom}"
    return run_isolated(test)

def p8_caso_6(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    b_san = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Santander"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_g_db = db.get(Billetera, b_gal.id)
        b_s_db = db.get(Billetera, b_san.id)
        s0_gal = b_g_db.saldo_actual
        s0_san = b_s_db.saldo_actual

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "fue con Santander"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        db.refresh(b_g_db)
        db.refresh(b_s_db)

        ok_bill = (tx.billetera_id == b_san.id)
        ok_gal = (b_g_db.saldo_actual == s0_gal)
        ok_san = (b_s_db.saldo_actual == s0_san - Decimal("5000"))

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nBilletera Santander: {ok_bill} | Saldo Galicia revertido: {ok_gal} | Saldo Santander descontado: {ok_san}"
    return run_isolated(test)

def p8_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "fue ayer"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        from app.utils.fecha import hoy_argentina
        ayer = hoy_argentina() - timedelta(days=1)
        ok_fecha = (tx.fecha == ayer)

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nFecha ayer: {ok_fecha}"
    return run_isolated(test)

def p8_caso_8(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_san = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Santander"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 10000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eran 3000 con Santander"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        ok_monto = (tx.monto == Decimal("3000"))
        ok_bill = (tx.billetera_id == b_san.id)

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nMonto 3000: {ok_monto} | Santander: {ok_bill}"
    return run_isolated(test)

def p8_caso_9(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
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
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=2),
            es_recurrente=False,
            es_cuota_hija=True,
            es_padre_cuotas=False,
        )
        db.add(tx_cuota)
        db.flush()

        conv = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_cuota_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="cuota",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Listo.",
            intent_detectado="registrar_transaccion",
            entidades={},
            slot_filling_activo=False,
            accion_ejecutada=str(tx_cuota.id),
            confianza=Decimal("1.000"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        db.add(conv)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p8_caso_10(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")

        hace_35_min = datetime.now(timezone.utc) - timedelta(minutes=35)
        tx_vieja = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="Supermercado",
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=hace_35_min,
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db.add(tx_vieja)
        db.flush()

        conv = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_old_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="supermercado 30000",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Listo.",
            intent_detectado="registrar_transaccion",
            entidades={},
            slot_filling_activo=False,
            accion_ejecutada=str(tx_vieja.id),
            confianza=Decimal("1.000"),
            fecha=hace_35_min,
        )
        db.add(conv)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eran 3.000 no 30.000"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p8_caso_11(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_ef = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Efectivo ARS"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        # 1. Movimiento previo A registrado en Efectivo ARS
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")
        tx_prev = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("1000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="Golosinas",
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=b_ef.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=5),
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db.add(tx_prev)
        db.flush()

        conv_prev = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prev_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="golosinas 1000",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Listo. $1.000 en Kiosco desde Efectivo ARS — registrado.",
            intent_detectado="registrar_transaccion",
            entidades={},
            slot_filling_activo=False,
            accion_ejecutada=str(tx_prev.id),
            confianza=Decimal("1.000"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        db.add(conv_prev)
        db.commit()

        # 2. Enviar un nuevo gasto que queda como propuesta pendiente (Galicia)
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        # 3. Decir "no, fue en Santander"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no, fue en Santander"), time.perf_counter())
        resp_corr_prop = respuestas[-1][1] if respuestas else ""

        # Verificar que el movimiento previo A sigue intacto en Efectivo ARS
        db.refresh(tx_prev)
        ok_previa = (tx_prev and tx_prev.billetera_id == b_ef.id)

        return f"Respuesta:\n{resp_corr_prop}\nMovimiento anterior intacto en Efectivo ARS: {ok_previa}"
    return run_isolated(test)

# --- PUNTO 9A: Tarjetas de crédito y cuotas ---
def p9_caso_1(datos):
    """'gasté 30000 con la Amex': resuelve la tarjeta única, no descuenta saldo, crea una cuota."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        saldo_antes = b_gal.saldo_actual
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 con la Amex"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""
        
        db = Session()
        b_act = db.execute(select(Billetera).where(Billetera.id == b_gal.id)).scalar_one()
        saldo_intacto = (b_act.saldo_actual == saldo_antes)
        tx_padre = db.execute(
            select(Transaccion).where(
                Transaccion.usuario_id == u.id,
                Transaccion.origen == OrigenTransaccion.IA_WPP,
                Transaccion.es_padre_cuotas == True
            )
        ).first()
        cuotas = db.execute(select(Cuota).join(GrupoCuotas).where(GrupoCuotas.usuario_id == u.id)).all()
        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nSaldo intacto: {saldo_intacto} | Es padre: {tx_padre is not None} | Cuotas creadas: {len(cuotas) > 0}"
    return run_isolated(test)

def p9_caso_2(datos):
    """'gasté 30000 con la Visa': pregunta cuál de las dos."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 con la Visa"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_3(datos):
    """'gasté 30000 con la del Santander': resuelve la 5077."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 con la del Santander"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_4(datos):
    """'compré una tele en 12 cuotas de 80000': propone 12 cuotas de 80.000, total 960.000."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré una tele en 12 cuotas de 80000"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_5(datos):
    """'gasté 80000 en 12 cuotas': propone 12 cuotas de 6.666,67, total 80.000."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 80000 en 12 cuotas"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_6(datos):
    """'gasté 5000 con Galicia': sigue siendo la billetera, no la tarjeta."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 con Galicia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_7(datos):
    """'gasté 5000 con la Visa del Galicia': resuelve la tarjeta 1506."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 con la Visa del Galicia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_8(datos):
    """'pagué el resumen de la tarjeta': explica que se hace desde la web, no registra nada."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pagué el resumen de la tarjeta"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        db = Session()
        txs = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalar()
        return f"{resp} | Txs creadas: {txs}"
    return run_isolated(test)

def p9_caso_9(datos):
    """'gasté 5000 con la tarjeta de débito': NO es crédito."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 con la tarjeta de débito"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_10(datos):
    """Registrar un consumo con tarjeta y deshacerlo: verifica que se borren padre, grupo y cuotas."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 con la Amex"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        # Deshacer
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        r_borra = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        r_conf = respuestas[-1][1] if respuestas else ""
        
        db = Session()
        padres = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalar()
        return f"Propuesta deshacer:\n{r_borra}\nConfirmación:\n{r_conf}\nPadres restantes: {padres}"
    return run_isolated(test)

def p9_caso_11(datos):
    """Un usuario sin tarjetas dice 'gasté 5000 con la tarjeta': mensaje claro."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE tarjetas_credito SET estado = 'archivada' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 con la tarjeta"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_12(datos):
    """'gasté 3 gambas en el remis': ya no debe interpretarse como 3.000."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 3 gambas en el remis"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        no_es_3000 = ("3.000" not in resp and "3000" not in resp)
        return f"No interpretado como 3000: {no_es_3000} | Respuesta: {resp}"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 9B: Transferencias, Extracciones y Dólares (12 casos)
# ==============================================================================

def p9b_caso_1(datos):
    """pasé 10 mil de Galicia a Santander: crea transferencia, ajusta los dos saldos, cero gastos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 10 mil de Galicia a Santander"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        
        cero_gastos = (txs_ini == txs_fin)
        saldos_ok = (bg_fin == bg_ini - Decimal("10000") and bs_fin == bs_ini + Decimal("10000"))
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok} | Cero gastos: {cero_gastos}"
    return run_isolated(test)

def p9b_caso_2(datos):
    """me transferí 20000 a Santander: pregunta el origen o usa la principal, según corresponda"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "me transferí 20000 a Santander"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_3(datos):
    """saqué 50000 del cajero: transfiere de la cuenta al efectivo, cero gastos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        be_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo ARS", Billetera.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "saqué 50000 del cajero"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        be_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo ARS", Billetera.usuario_id == u.id)).scalar()
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        
        saldos_ok = (bg_fin == bg_ini - Decimal("50000") and be_fin == be_ini + Decimal("50000"))
        cero_gastos = (txs_ini == txs_fin)
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok} | Cero gastos: {cero_gastos}"
    return run_isolated(test)

def p9b_caso_4(datos):
    """saqué 50000 del cajero con un usuario sin billetera de efectivo: mensaje claro, no registra"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND es_efectivo = true"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        trs_ini = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "saqué 50000 del cajero"), time.perf_counter())
        msg = respuestas[-1][1] if respuestas else ""
        
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        trs_fin = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        no_registro = (txs_ini == txs_fin and trs_ini == trs_fin)
        return f"Respuesta: {msg} | No registra: {no_registro}"
    return run_isolated(test)

def p9b_caso_5(datos):
    """compré 100 dólares a 1500: transfiere 150.000 pesos y suma 100 dólares"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 1500"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        
        saldos_ok = (bg_fin == bg_ini - Decimal("150000") and bu_fin == bu_ini + Decimal("100"))
        cero_gastos = (txs_ini == txs_fin)
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok} | Cero gastos: {cero_gastos}"
    return run_isolated(test)

def p9b_caso_6(datos):
    """compré 100 dólares: pregunta la cotización o los pesos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_7(datos):
    """vendí 50 dólares a 1450: transfiere al revés"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET saldo_actual = 100 WHERE usuario_id = :uid AND nombre = 'Efectivo USD'"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "vendí 50 dólares a 1450"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()
        
        saldos_ok = (bu_fin == bu_ini - Decimal("50") and bg_fin == bg_ini + Decimal("72500"))
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok}"
    return run_isolated(test)

def p9b_caso_8(datos):
    """compré 100 dólares a 5: advierte que la cotización es absurda"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 5"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_9(datos):
    """le transferí 5000 a mi hermano: es un gasto, no una transferencia"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        trs_ini = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "le transferí 5000 a mi hermano"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        trs_fin = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        es_gasto = (txs_fin == txs_ini + 1 and trs_fin == trs_ini)
        return f"Propuesta: {prop} | Confirmación: {conf} | Es gasto: {es_gasto}"
    return run_isolated(test)

def p9b_caso_10(datos):
    """gasté 5000 en el kiosco: sigue siendo un gasto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_11(datos):
    """Registrar una transferencia y deshacerla: los dos saldos vuelven"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        
        # Transferir
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 10 mil de Galicia a Santander"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        
        # Deshacer
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        prop_undo = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf_undo = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        
        saldos_vuelven = (bg_ini == bg_fin and bs_ini == bs_fin)
        return f"Propuesta undo: {prop_undo} | Confirmación undo: {conf_undo} | Saldos intactos: {saldos_vuelven}"
    return run_isolated(test)

def p9b_caso_12(datos):
    """Transferencia con origen y destino iguales: se rechaza"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 10 mil de Galicia a Galicia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_13(datos):
    """compré 5 dólares y responder 7500: debe registrar 5 dólares a 1.500, no rechazar"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 5 dólares"), time.perf_counter())
        r1 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "7500"), time.perf_counter())
        r2 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        r3 = respuestas[-1][1] if respuestas else ""

        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()

        saldos_ok = (bu_fin == bu_ini + Decimal("5") and bg_fin == bg_ini - Decimal("7500"))
        return f"R1: {r1} | R2: {r2} | R3: {r3} | Saldos: {saldos_ok}"
    return run_isolated(test)

def p9b_caso_14(datos):
    """compré 100 dólares y responder 1500: cotización unitaria"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares"), time.perf_counter())
        r1 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "1500"), time.perf_counter())
        r2 = respuestas[-1][1] if respuestas else ""

        return f"R1: {r1} | R2: {r2}"
    return run_isolated(test)

def p9b_caso_15(datos):
    """compré 100 dólares y responder 150000: monto total"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares"), time.perf_counter())
        r1 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "150000"), time.perf_counter())
        r2 = respuestas[-1][1] if respuestas else ""

        return f"R1: {r1} | R2: {r2}"
    return run_isolated(test)

def p9b_caso_16(datos):
    """compré 100 dólares a 15: debe advertir, con la cotización de referencia en el mensaje"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 15"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_17(datos):
    """compré 100 dólares a 1500 con la tabla de cotizaciones vacía: no rechaza, pide confirmación"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("DELETE FROM cotizaciones_dolar"))

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 1500"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)


def p10_caso_1(datos):
    """empecé a pagar 5000 de Disney+: pregunta la frecuencia, crea la suscripción, no cobra nada"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "empecé a pagar 5000 de Disney+"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "mensual"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp3 = respuestas[-1][1] if respuestas else ""

        sub = db.execute(select(Suscripcion).where(Suscripcion.usuario_id == u.id, Suscripcion.nombre == "Disney+")).scalars().first()
        txs_sub = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id, Transaccion.suscripcion_id.is_not(None))).scalar()

        return (
            f"Pregunta frecuencia: {'¿Con qué frecuencia' in resp1}\n"
            f"Propuesta: {'Voy a programar la suscripción a Disney+' in resp2}\n"
            f"Confirmación: {'Listo. Suscripción a Disney+' in resp3}\n"
            f"Sub creada: {sub is not None and sub.estado == EstadoSuscripcion.ACTIVA}\n"
            f"Txs cobro generadas: {txs_sub}"
        )
    return run_isolated(test)

def p10_caso_2(datos):
    """gasté 5000 en Disney+: sigue siendo un gasto suelto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en Disney+"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p10_caso_3(datos):
    """me suscribí a Netflix por 9000 por mes: crea con frecuencia mensual confirmada"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "me suscribí a Netflix por 9000 por mes"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        sub = db.execute(select(Suscripcion).where(Suscripcion.usuario_id == u.id, Suscripcion.nombre == "Netflix")).scalars().first()
        return (
            f"Propuesta: {'Voy a programar la suscripción a Netflix: $9.000 mensual' in resp_prop}\n"
            f"Confirmación: {'Listo. Suscripción a Netflix' in resp_conf}\n"
            f"Frecuencia mensual: {sub is not None and sub.frecuencia == FrecuenciaSuscripcion.MENSUAL}"
        )
    return run_isolated(test)

def p10_caso_4(datos):
    """pagué el Spotify: pregunta si es gasto único o suscripción"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pagué el Spotify"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p10_caso_5(datos):
    """me suscribí a ChatGPT por 20 dólares por mes con medio de pago en pesos: crea la suscripción en dólares"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "me suscribí a ChatGPT por 20 dólares por mes"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        sub = db.execute(select(Suscripcion).where(Suscripcion.usuario_id == u.id, Suscripcion.nombre.ilike("%ChatGPT%"))).scalars().first()
        hist = db.execute(select(HistorialSuscripcion).where(HistorialSuscripcion.suscripcion_id == sub.id)).scalars().first() if sub else None
        bill = db.get(Billetera, sub.billetera_id) if sub and sub.billetera_id else None

        mon_val = hist.moneda.value if hist and hasattr(hist.moneda, "value") else (hist.moneda if hist else None)
        return (
            f"Propuesta: {'US$20' in resp_prop or 'USD 20' in resp_prop}\n"
            f"Sub creada: {sub is not None}\n"
            f"Moneda sub: {mon_val}\n"
            f"Medio de pago pesos: {bill.moneda == Moneda.ARS if bill else False}"
        )
    return run_isolated(test)

def p10_caso_6(datos):
    """di de baja Netflix sin tenerla: mensaje claro"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "di de baja Netflix"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p10_caso_7(datos):
    """di de baja la suscripción existente: confirma y la da de baja"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        data_create = SuscripcionCreate(
            nombre="Netflix",
            monto=Decimal("9000"),
            moneda="ARS",
            frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"),
            billetera_id=b_gal.id,
        )
        sub = suscripcion_service.crear_suscripcion(db, u.id, data_create)

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "di de baja Netflix"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        db.refresh(sub)
        return (
            f"Pregunta confirmación: {'¿Confirmás dar de baja la suscripción a Netflix' in resp_prop}\n"
            f"Confirmación: {'Listo, dimos de baja tu suscripción a Netflix.' in resp_conf}\n"
            f"Estado final: {sub.estado.value}"
        )
    return run_isolated(test)

def p10_caso_8(datos):
    """aumentó Netflix, ahora son 12000: muestra el precio anterior y el nuevo"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        data_create = SuscripcionCreate(
            nombre="Netflix",
            monto=Decimal("9000"),
            moneda="ARS",
            frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"),
            billetera_id=b_gal.id,
        )
        sub = suscripcion_service.crear_suscripcion(db, u.id, data_create)

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aumentó Netflix, ahora son 12000"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        precio_vigente = suscripcion_service.obtener_precio_vigente(db, sub.id)
        return (
            f"Propuesta muestra ambos: {'de $9.000 a $12.000' in resp_prop}\n"
            f"Confirmación: {'actualicé el precio de Netflix a $12.000' in resp_conf}\n"
            f"Precio en base: {precio_vigente.monto if precio_vigente else None}"
        )
    return run_isolated(test)

def p10_caso_9(datos):
    """cuánto gasto en suscripciones: lista y total mensual"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        suscripcion_service.crear_suscripcion(db, u.id, SuscripcionCreate(
            nombre="Netflix", monto=Decimal("9000"), moneda="ARS", frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"), billetera_id=b_gal.id
        ))
        suscripcion_service.crear_suscripcion(db, u.id, SuscripcionCreate(
            nombre="Spotify", monto=Decimal("3500"), moneda="ARS", frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"), billetera_id=b_gal.id
        ))

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasto en suscripciones"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        sin_saldos_billetera = "Galicia" not in resp and "saldo" not in resp.lower()
        return (
            f"Lista activa: {'Netflix' in resp and 'Spotify' in resp}\n"
            f"Total mensual: {'$12.500' in resp}\n"
            f"Sin saldos billetera: {sin_saldos_billetera}"
        )
    return run_isolated(test)

def p10_caso_10(datos):
    """Registrar un gasto igual a una suscripción ya cobrada este período: avisa antes"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        sub = suscripcion_service.crear_suscripcion(db, u.id, SuscripcionCreate(
            nombre="Netflix", monto=Decimal("9000"), moneda="ARS", frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"), billetera_id=b_gal.id
        ))
        tx_cobro = Transaccion(
            usuario_id=u.id,
            billetera_id=b_gal.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("9000"),
            moneda=Moneda.ARS,
            descripcion="Netflix",
            fecha=hoy_argentina(),
            origen=OrigenTransaccion.RECURRENTE,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            suscripcion_id=sub.id,
        )
        db.add(tx_cobro)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 9000 en Netflix"), time.perf_counter())
        resp_aviso = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "es un gasto aparte"), time.perf_counter())
        resp_reg = respuestas[-1][1] if respuestas else ""

        return (
            f"Aviso cobro previo: {'ya se cobró automáticamente' in resp_aviso and 'este período' in resp_aviso}\n"
            f"Registro tras confirmación: {'registrado' in resp_reg}"
        )
    return run_isolated(test)

def p10_caso_11(datos):
    """Crear una suscripción de un servicio que ya tiene activa: avisa"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        suscripcion_service.crear_suscripcion(db, u.id, SuscripcionCreate(
            nombre="Netflix", monto=Decimal("9000"), moneda="ARS", frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"), billetera_id=b_gal.id
        ))

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "me suscribí a Netflix por 9000 por mes"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        return (
            f"Aviso existente: {'Ya tenés una suscripción activa a Netflix' in resp and '¿Querés registrar otra igual o te referías a la existente?' in resp}"
        )
    return run_isolated(test)

def p10_caso_12(datos):
    """Un servicio que no está en el catálogo: lo acepta igual"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "empecé a pagar 15000 del Gimnasio del barrio por mes"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        return (
            f"Propuesta servicio no-catálogo: {'Voy a programar la suscripción a Gimnasio del barrio: $15.000 mensual' in resp}"
        )
    return run_isolated(test)


# ==============================================================================
# RUNNER GENERAL DE SUITE
# ==============================================================================



# ==============================================================================
# ESCENARIOS PUNTO 11: Lotes y Multi-Operación (10 casos)
# ==============================================================================

def p11_caso_1(datos):
    """Dos gastos con billeteras distintas nombradas explícitamente"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco con Galicia y 8000 en la verdulería con Santander"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""
        return (
            f"Propuesta: {'5.000 en Kiosco desde Galicia' in resp_prop and '8.000 en Verdulería desde Santander' in resp_prop} | "
            f"Confirmacion: {'5.000 en Kiosco desde Galicia' in resp_conf and '8.000 en Verdulería desde Santander' in resp_conf}"
        )
    return run_isolated(test)

def p11_caso_2(datos):
    """Dos gastos sin billetera, con el usuario teniendo principal"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 8000 en la verdulería"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        return (
            f"Propuesta principal: {'2 movimientos desde Galicia: $5.000 en Kiosco, $8.000 en Verdulería' in resp_prop and 'Si fue con otra, decime cuál.' in resp_prop}"
        )
    return run_isolated(test)

def p11_caso_3(datos):
    """Dos gastos sin billetera, sin principal: pregunta una vez"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 8000 en la verdulería"), time.perf_counter())
        resp_preg = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        return (
            f"Pregunta una vez: {'¿Desde qué billetera salieron los gastos?' in resp_preg} | "
            f"Propuesta resuelta: {'2 movimientos desde Galicia: $5.000 en Kiosco, $8.000 en Verdulería' in resp_prop}"
        )
    return run_isolated(test)

def p11_caso_4(datos):
    """Tres gastos donde solo uno nombra billetera"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco con Galicia, 3000 en la panadería y 4000 en la verdulería"), time.perf_counter())
        resp_preg = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "3"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        return (
            f"Pregunta faltantes: {'¿Desde qué billetera salieron los gastos?' in resp_preg} | "
            f"Propuesta mixta: {'5.000 en Kiosco desde Galicia' in resp_prop and 'Santander' in resp_prop}"
        )
    return run_isolated(test)

def p11_caso_5(datos):
    """Un gasto y un ingreso en el mismo mensaje"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cobré 100000 de sueldo y pagué 30000 de alquiler"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""
        return (
            f"Propuesta signos: {'+$100.000' in resp_prop and '-$30.000' in resp_prop} | "
            f"Confirmacion signos: {'+$100.000' in resp_conf and '-$30.000' in resp_conf}"
        )
    return run_isolated(test)

def p11_caso_6(datos):
    """Un lote con un consumo de tarjeta y un gasto normal"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco con Galicia y 30000 en zapatillas con la Amex"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        return (
            f"Lote tarjeta y gasto: {'$5.000 en Kiosco desde Galicia' in resp_prop and 'con tarjeta •••• 2745' in resp_prop}"
        )
    return run_isolated(test)

def p11_caso_7(datos):
    """Doce movimientos: rechaza con mensaje claro"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        msg_12 = "gasté 100 en kiosco, 200 en pan, 300 en leche, 400 en carne, 500 en verdura, 600 en cafe, 700 en taxi, 800 en bar, 900 en cena, 1000 en cine, 1100 en nafta y 1200 en peaje"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, msg_12), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        return (
            f"Rechazo tope: {'El límite es de 10 movimientos por mensaje' in resp and 'web de Argentum' in resp}"
        )
    return run_isolated(test)

def p11_caso_8(datos):
    """Un lote donde una operación está en otra moneda sin billetera de esa moneda"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND moneda = 'USD'"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 50 dólares en un libro"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        return (
            f"Aviso descarte y propuesta: {'No se pudo registrar' in resp and 'dólares' in resp and 'Voy a anotar $5.000 en Kiosco desde Galicia' in resp}"
        )
    return run_isolated(test)

def p11_caso_9(datos):
    """Un lote con dos movimientos idénticos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 5000 en el kiosco"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        return (
            f"Deteccion duplicado interno: {'Mandaste 2 movimientos iguales de $5.000 en Kiosco' in resp and '¿Son dos gastos distintos o se te repitió?' in resp}"
        )
    return run_isolated(test)

def p11_caso_10(datos):
    """Un lote seguido de 'borrá eso'"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 8000 en la verdulería"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        resp_prop_undo = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf_undo = respuestas[-1][1] if respuestas else ""
        return (
            f"Propuesta deshacer lote: {'eliminar los 2 movimientos' in resp_prop_undo} | "
            f"Confirmacion deshacer lote: {'Listo, 2 movimientos eliminados.' in resp_conf_undo}"
        )
    return run_isolated(test)

def correr_suite_completa():
    print("=== INICIANDO SUITE CONSOLIDADA DE REGRESION DE WHATSAPP ===")
    print(f"Usuario exclusivo de pruebas: {USUARIO_PRUEBAS_EMAIL}")
    
    db = SessionLocal()
    datos = resolver_datos_base(db)
    u_admin = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    if u_admin.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Verificación de usuario fallida. Resuelto: {u_admin.email}")
    
    conteos_inicio = obtener_conteos_base(db)
    db.close()

    from app.utils.fecha import hoy_argentina
    hoy = hoy_argentina()
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
            "esperado": "¿Desde qué billetera salió la plata?\n\n1. Efectivo ARS\n2. Galicia\n3. Santander",
            "match": "exacto",
        },
        {
            "id": "P3.3",
            "punto": "Punto 3",
            "nombre": "Responde '2' a un menú de billeteras",
            "ejecutar": lambda: p3_caso_3(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P3.4",
            "punto": "Punto 3",
            "nombre": "Responde con el nombre de la billetera en vez del número",
            "ejecutar": lambda: p3_caso_4(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Santander. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P3.5",
            "punto": "Punto 3",
            "nombre": "Responde un número fuera de rango",
            "ejecutar": lambda: p3_caso_5(datos),
            "esperado": "Opción inválida. Elegí un número del 1 al 3.",
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
            "nombre": "Recibe una propuesta y responde 'no, fue en Santander'",
            "ejecutar": lambda: p3_caso_8(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Santander. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P3.9",
            "punto": "Punto 3",
            "nombre": "Dice 'cobré 800000 de sueldo' y elige billetera de destino",
            "ejecutar": lambda: p3_caso_9(datos),
            "esperado": "¿A qué billetera entró la plata?\n\n1. Efectivo ARS\n2. Galicia\n3. Santander\n---\nVoy a registrar un ingreso de $800.000 en Sueldo a Efectivo ARS. ¿Va?",
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
            "esperado": "Descarté la de $5.000 en Kiosco. Para los $12.000 en Verdulería:\n\n¿Desde qué billetera salió la plata?\n\n1. Efectivo ARS\n2. Galicia\n3. Santander",
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
            "nombre": "Variante de no: 'no, fue en Santander'",
            "ejecutar": lambda: p4_variante_no(datos, "no, fue en Santander", billetera_propuesta="Galicia"),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Santander. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P4.VAR3",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no fue en galicia'",
            "ejecutar": lambda: p4_variante_no(datos, "no fue en galicia", billetera_propuesta="Santander"),
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
            "esperado": "Listo. $5.000 en Kiosco desde Galicia — registrado.",
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
            "esperado": "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nSi fue con otra, decime cuál.",
            "match": "exacto",
        },
        {
            "id": "P5.5",
            "punto": "Punto 5",
            "nombre": "Mismo monto, otra categoría (sin advertencia)",
            "ejecutar": lambda: p5_caso_5(datos),
            "esperado": "Voy a anotar $5.000 en Farmacia desde Galicia. ¿Va?",
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
            "esperado": "Mandaste 2 movimientos iguales de $5.000 en Kiosco desde Galicia. ¿Son dos gastos distintos o se te repitió?",
            "match": "exacto",
        },
        {
            "id": "P5.CUOTAS",
            "punto": "Punto 5",
            "nombre": "Cuotas de tarjeta no disparan falso positivo de duplicado",
            "ejecutar": lambda: p5_caso_cuotas(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nSi fue con otra, decime cuál.",
            "match": "exacto",
        },

        # --- PUNTO 6 ---
        {
            "id": "P6.1",
            "punto": "Punto 6",
            "nombre": "Gasto de hoy",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto hoy", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.2",
            "punto": "Punto 6",
            "nombre": "Gasto de ayer",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto ayer", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": ayer.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Galicia (ayer). ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia (ayer) — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.3",
            "punto": "Punto 6",
            "nombre": "Gasto del 31 de agosto",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto 31 agosto", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": "2026-08-31"
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Galicia (el 31 de agosto). ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia (el 31 de agosto) — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.4",
            "punto": "Punto 6",
            "nombre": "Gasto de hace tres meses (>60 días)",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto 3 meses", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": "2026-06-03"
            }),
            "esperado": "Propuesta:\nNo puedo registrar movimientos de más de 60 días atrás. Va a quedar con fecha de hoy.\nVoy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.5",
            "punto": "Punto 6",
            "nombre": "Gasto con fecha futura",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto futuro", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": "2026-09-10"
            }),
            "esperado": "Propuesta:\nNo puedo registrar movimientos con fecha futura porque todavía no ocurrieron. Va a quedar con fecha de hoy.\nVoy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia — registrado.",
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
                "monto": 600000, "moneda": "ARS", "tipo": "egreso", "categoria": "Supermercado", "billetera_origen": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $600.000 en Supermercado desde Galicia. ¿Va?\nConfirmación:\nListo. $600.000 en Supermercado desde Galicia — registrado.\nLa billetera quedó en negativo.",
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

        # --- PUNTO 7: Categorías estructuradas, jerga argentina y descripciones ---
        {
            "id": "P7.1",
            "punto": "Punto 7",
            "nombre": "Golosinas -> Kiosco (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 5000 en golosinas", "Kiosco", "Golosinas"),
            "esperado": "Cat: Kiosco | Desc: Golosinas",
            "match": "exacto",
        },
        {
            "id": "P7.2",
            "punto": "Punto 7",
            "nombre": "Verdulería -> Verdulería (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 8000 en la verdulería", "Verdulería", "Verdulería"),
            "esperado": "Cat: Verdulería | Desc: Verdulería",
            "match": "exacto",
        },
        {
            "id": "P7.3",
            "punto": "Punto 7",
            "nombre": "Nafta -> Combustible (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "cargué 30000 de nafta", "Combustible", "Nafta"),
            "esperado": "Cat: Combustible | Desc: Nafta",
            "match": "exacto",
        },
        {
            "id": "P7.4",
            "punto": "Punto 7",
            "nombre": "Prepaga -> Obra social / Prepaga (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "pagué 12000 de la prepaga", "Obra social / Prepaga", "Prepaga"),
            "esperado": "Cat: Obra social / Prepaga | Desc: Prepaga",
            "match": "exacto",
        },
        {
            "id": "P7.5",
            "punto": "Punto 7",
            "nombre": "Bondi -> Transporte público (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 4000 en el bondi", "Transporte público", "Bondi"),
            "esperado": "Cat: Transporte público | Desc: Bondi",
            "match": "exacto",
        },
        {
            "id": "P7.6",
            "punto": "Punto 7",
            "nombre": "Corte de pelo -> Cuidado personal (jerga argentina + descripción preservada)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "me corté el pelo, 15000", "Cuidado personal", "Corte de pelo"),
            "esperado": "Cat: Cuidado personal | Desc: Corte de pelo",
            "match": "exacto",
        },
        {
            "id": "P7.7",
            "punto": "Punto 7",
            "nombre": "Concepto raro -> Otros (prohibición de categorías inventadas + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 2500 en un coso cuántico intergaláctico", "Otros", "Coso cuántico intergaláctico"),
            "esperado": "Cat: Otros | Desc: Coso cuántico intergaláctico",
            "match": "exacto",
        },

        # --- PUNTO 8: Deshacer y Corregir movimientos por WhatsApp ---
        {
            "id": "P8.1",
            "punto": "Punto 8",
            "nombre": "Registrar un gasto y decir 'borrá eso', confirmar, verificar borrado y reversión de saldo",
            "ejecutar": lambda: p8_caso_1(datos),
            "esperado": "Propuesta:\n¿Querés eliminar el último movimiento de $5.000 en Kiosco desde Galicia? ¿Confirmás?\nConfirmación:\nListo, movimiento eliminado.\nBorrado: True | Saldo restaurado: True",
            "match": "exacto",
        },
        {
            "id": "P8.2",
            "punto": "Punto 8",
            "nombre": "Decir 'borrá eso' sin nada registrado",
            "ejecutar": lambda: p8_caso_2(datos),
            "esperado": "No tenés ningún movimiento reciente registrado por WhatsApp para deshacer. Podés gestionarlo desde la web de Argentum.",
            "match": "exacto",
        },
        {
            "id": "P8.3",
            "punto": "Punto 8",
            "nombre": "Decir 'borrá eso' dos veces seguidas",
            "ejecutar": lambda: p8_caso_3(datos),
            "esperado": "No hay nada para deshacer.",
            "match": "exacto",
        },
        {
            "id": "P8.4",
            "punto": "Punto 8",
            "nombre": "Registrar gasto y decir 'eran 3.000 no 30.000', confirmar, verificar monto y saldo",
            "ejecutar": lambda: p8_caso_4(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $30.000 en Supermercado desde Galicia\nAhora: $3.000 en Supermercado desde Galicia\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nMonto corregido: True | Saldo ajustado (+27k): True",
            "match": "exacto",
        },
        {
            "id": "P8.5",
            "punto": "Punto 8",
            "nombre": "Registrar un gasto y decir 'eso era supermercado', verificar la categoría",
            "ejecutar": lambda: p8_caso_5(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $5.000 en Kiosco desde Galicia\nAhora: $5.000 en Supermercado desde Galicia\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nCategoría final: Supermercado",
            "match": "exacto",
        },
        {
            "id": "P8.6",
            "punto": "Punto 8",
            "nombre": "Registrar un gasto y decir 'fue con Santander', verificar billetera y los dos saldos",
            "ejecutar": lambda: p8_caso_6(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $5.000 en Kiosco desde Galicia\nAhora: $5.000 en Kiosco desde Santander\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nBilletera Santander: True | Saldo Galicia revertido: True | Saldo Santander descontado: True",
            "match": "exacto",
        },
        {
            "id": "P8.7",
            "punto": "Punto 8",
            "nombre": "Registrar un gasto y decir 'fue ayer', verificar la fecha",
            "ejecutar": lambda: p8_caso_7(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $5.000 en Kiosco desde Galicia\nAhora: $5.000 en Kiosco desde Galicia (ayer)\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nFecha ayer: True",
            "match": "exacto",
        },
        {
            "id": "P8.8",
            "punto": "Punto 8",
            "nombre": "Corregir dos campos en un mismo mensaje (monto y billetera)",
            "ejecutar": lambda: p8_caso_8(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $10.000 en Kiosco desde Galicia\nAhora: $3.000 en Kiosco desde Santander\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nMonto 3000: True | Santander: True",
            "match": "exacto",
        },
        {
            "id": "P8.9",
            "punto": "Punto 8",
            "nombre": "Intentar deshacer una cuota de tarjeta y verificar que se rechaza",
            "ejecutar": lambda: p8_caso_9(datos),
            "esperado": "Ese movimiento corresponde a una cuota de tarjeta y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum.",
            "match": "exacto",
        },
        {
            "id": "P8.10",
            "punto": "Punto 8",
            "nombre": "Intentar corregir pasado el plazo (>30 min)",
            "ejecutar": lambda: p8_caso_10(datos),
            "esperado": "El último movimiento fue hace más de 30 minutos. Para modificarlo, ingresá a la web de Argentum.",
            "match": "exacto",
        },
        {
            "id": "P8.11",
            "punto": "Punto 8",
            "nombre": "Con propuesta pendiente, 'no, fue en Santander' corrige propuesta y no movimiento anterior",
            "ejecutar": lambda: p8_caso_11(datos),
            "esperado": "Respuesta:\nVoy a anotar $5.000 en Kiosco desde Santander. ¿Va?\nMovimiento anterior intacto en Efectivo ARS: True",
            "match": "exacto",
        },

        # --- PUNTO 9A: Tarjetas de crédito y cuotas ---
        {
            "id": "P9.1",
            "punto": "Punto 9A",
            "nombre": "gasté 30000 con la Amex: resuelve la tarjeta única, no descuenta saldo, crea una cuota",
            "ejecutar": lambda: p9_caso_1(datos),
            "esperado": "Saldo intacto: True | Es padre: True | Cuotas creadas: True",
            "match": "contiene",
        },
        {
            "id": "P9.2",
            "punto": "Punto 9A",
            "nombre": "gasté 30000 con la Visa: pregunta cuál de las dos",
            "ejecutar": lambda: p9_caso_2(datos),
            "esperado": "¿Con qué tarjeta de crédito fue?\n1. •••• 1506 (Visa - Galicia)\n2. •••• 5077 (Visa - Santander)",
            "match": "exacto",
        },
        {
            "id": "P9.3",
            "punto": "Punto 9A",
            "nombre": "gasté 30000 con la del Santander: resuelve la 5077",
            "ejecutar": lambda: p9_caso_3(datos),
            "esperado": "con tarjeta •••• 5077",
            "match": "contiene",
        },
        {
            "id": "P9.4",
            "punto": "Punto 9A",
            "nombre": "compré una tele en 12 cuotas de 80000: propone 12 cuotas de 80.000, total 960.000",
            "ejecutar": lambda: p9_caso_4(datos),
            "esperado": "12 cuotas de $80.000 (total $960.000)",
            "match": "contiene",
        },
        {
            "id": "P9.5",
            "punto": "Punto 9A",
            "nombre": "gasté 80000 en 12 cuotas: propone 12 cuotas de 6.666,67, total 80.000",
            "ejecutar": lambda: p9_caso_5(datos),
            "esperado": "12 cuotas de $6.666,67 (total $80.000)",
            "match": "contiene",
        },
        {
            "id": "P9.6",
            "punto": "Punto 9A",
            "nombre": "gasté 5000 con Galicia: sigue siendo la billetera, no la tarjeta",
            "ejecutar": lambda: p9_caso_6(datos),
            "esperado": "desde Galicia",
            "match": "contiene",
        },
        {
            "id": "P9.7",
            "punto": "Punto 9A",
            "nombre": "gasté 5000 con la Visa del Galicia: resuelve la tarjeta 1506",
            "ejecutar": lambda: p9_caso_7(datos),
            "esperado": "con tarjeta •••• 1506",
            "match": "contiene",
        },
        {
            "id": "P9.8",
            "punto": "Punto 9A",
            "nombre": "pagué el resumen de la tarjeta: explica que se hace desde la web, no registra nada",
            "ejecutar": lambda: p9_caso_8(datos),
            "esperado": "El pago del resumen de la tarjeta se gestiona desde la web de Argentum. No se puede realizar por WhatsApp. | Txs creadas: 0",
            "match": "exacto",
        },
        {
            "id": "P9.9",
            "punto": "Punto 9A",
            "nombre": "gasté 5000 con la tarjeta de débito: NO es crédito",
            "ejecutar": lambda: p9_caso_9(datos),
            "esperado": "desde Galicia",
            "match": "contiene",
        },
        {
            "id": "P9.10",
            "punto": "Punto 9A",
            "nombre": "Registrar un consumo con tarjeta y deshacerlo: verifica que se borren padre, grupo y cuotas",
            "ejecutar": lambda: p9_caso_10(datos),
            "esperado": "Listo, movimiento eliminado.\nPadres restantes: 0",
            "match": "contiene",
        },
        {
            "id": "P9.11",
            "punto": "Punto 9A",
            "nombre": "Un usuario sin tarjetas dice 'gasté 5000 con la tarjeta': mensaje claro",
            "ejecutar": lambda: p9_caso_11(datos),
            "esperado": "No tenés ninguna tarjeta de crédito cargada en Argentum. Podés agregarla desde la web, o registrar este movimiento como un gasto común con alguna de tus billeteras.",
            "match": "exacto",
        },
        {
            "id": "P9.12",
            "punto": "Punto 9A",
            "nombre": "gasté 3 gambas en el remis: ya no debe interpretarse como 3.000",
            "ejecutar": lambda: p9_caso_12(datos),
            "esperado": "No interpretado como 3000: True",
            "match": "contiene",
        },

        # --- PUNTO 9B: Transferencias, Extracciones y Dólares ---
        {
            "id": "P9B.1",
            "punto": "Punto 9B",
            "nombre": "pasé 10 mil de Galicia a Santander: crea transferencia, ajusta los dos saldos, cero gastos",
            "ejecutar": lambda: p9b_caso_1(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.2",
            "punto": "Punto 9B",
            "nombre": "me transferí 20000 a Santander: pregunta el origen o usa la principal, según corresponda",
            "ejecutar": lambda: p9b_caso_2(datos),
            "esperado": "Voy a transferir $20.000 de Galicia a Santander. ¿Confirmás?",
            "match": "exacto",
        },
        {
            "id": "P9B.3",
            "punto": "Punto 9B",
            "nombre": "saqué 50000 del cajero: transfiere de la cuenta al efectivo, cero gastos",
            "ejecutar": lambda: p9b_caso_3(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.4",
            "punto": "Punto 9B",
            "nombre": "saqué 50000 del cajero con un usuario sin billetera de efectivo: mensaje claro, no registra",
            "ejecutar": lambda: p9b_caso_4(datos),
            "esperado": "No tenés ninguna billetera de efectivo en pesos. Podés crearla desde la web de Argentum. | No registra: True",
            "match": "contiene",
        },
        {
            "id": "P9B.5",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares a 1500: transfiere 150.000 pesos y suma 100 dólares",
            "ejecutar": lambda: p9b_caso_5(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.6",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares: pregunta la cotización o los pesos",
            "ejecutar": lambda: p9b_caso_6(datos),
            "esperado": "¿A qué cotización compraste o cuántos pesos pagaste?",
            "match": "exacto",
        },
        {
            "id": "P9B.7",
            "punto": "Punto 9B",
            "nombre": "vendí 50 dólares a 1450: transfiere al revés",
            "ejecutar": lambda: p9b_caso_7(datos),
            "esperado": "Saldos ajustados: True",
            "match": "contiene",
        },
        {
            "id": "P9B.8",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares a 5: advierte que la cotización es absurda",
            "ejecutar": lambda: p9b_caso_8(datos),
            "esperado": "La cotización de $5 por dólar no parece razonable",
            "match": "contiene",
        },
        {
            "id": "P9B.9",
            "punto": "Punto 9B",
            "nombre": "le transferí 5000 a mi hermano: es un gasto, no una transferencia",
            "ejecutar": lambda: p9b_caso_9(datos),
            "esperado": "Es gasto: True",
            "match": "contiene",
        },
        {
            "id": "P9B.10",
            "punto": "Punto 9B",
            "nombre": "gasté 5000 en el kiosco: sigue siendo un gasto",
            "ejecutar": lambda: p9b_caso_10(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?",
            "match": "contiene",
        },
        {
            "id": "P9B.11",
            "punto": "Punto 9B",
            "nombre": "Registrar una transferencia y deshacerla: los dos saldos vuelven",
            "ejecutar": lambda: p9b_caso_11(datos),
            "esperado": "Saldos intactos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.12",
            "punto": "Punto 9B",
            "nombre": "Transferencia con origen y destino iguales: se rechaza",
            "ejecutar": lambda: p9b_caso_12(datos),
            "esperado": "La billetera de origen y destino no pueden ser la misma.",
            "match": "exacto",
        },
        {
            "id": "P9B.13",
            "punto": "Punto 9B",
            "nombre": "compré 5 dólares y responder 7500: debe registrar 5 dólares a 1.500, no rechazar",
            "ejecutar": lambda: p9b_caso_13(datos),
            "esperado": "Saldos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.14",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares y responder 1500: cotización unitaria",
            "ejecutar": lambda: p9b_caso_14(datos),
            "esperado": "compra de USD 100 a $1.500: salen $150.000",
            "match": "contiene",
        },
        {
            "id": "P9B.15",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares y responder 150000: monto total",
            "ejecutar": lambda: p9b_caso_15(datos),
            "esperado": "compra de USD 100 a $1.500: salen $150.000",
            "match": "contiene",
        },
        {
            "id": "P9B.16",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares a 15: debe advertir, con la cotización de referencia en el mensaje",
            "ejecutar": lambda: p9b_caso_16(datos),
            "esperado": "la cotización de referencia es de",
            "match": "contiene",
        },
        {
            "id": "P9B.17",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares a 1500 con la tabla de cotizaciones vacía: no rechaza, pide confirmación",
            "ejecutar": lambda: p9b_caso_17(datos),
            "esperado": "Voy a registrar una compra de USD 100 a $1.500: salen $150.000",
            "match": "contiene",
        },

        # --- PUNTO 10: Suscripciones (Etapa B) ---
        {
            "id": "P10.1",
            "punto": "Punto 10",
            "nombre": "empecé a pagar 5000 de Disney+: pregunta la frecuencia, crea la suscripción, no cobra nada",
            "ejecutar": lambda: p10_caso_1(datos),
            "esperado": "Pregunta frecuencia: True\nPropuesta: True\nConfirmación: True\nSub creada: True\nTxs cobro generadas: 0",
            "match": "exacto",
        },
        {
            "id": "P10.2",
            "punto": "Punto 10",
            "nombre": "gasté 5000 en Disney+: sigue siendo un gasto suelto",
            "ejecutar": lambda: p10_caso_2(datos),
            "esperado": "Voy a anotar $5.000",
            "match": "contiene",
        },
        {
            "id": "P10.3",
            "punto": "Punto 10",
            "nombre": "me suscribí a Netflix por 9000 por mes: crea con frecuencia mensual confirmada",
            "ejecutar": lambda: p10_caso_3(datos),
            "esperado": "Propuesta: True\nConfirmación: True\nFrecuencia mensual: True",
            "match": "exacto",
        },
        {
            "id": "P10.4",
            "punto": "Punto 10",
            "nombre": "pagué el Spotify: pregunta si es gasto único o suscripción",
            "ejecutar": lambda: p10_caso_4(datos),
            "esperado": "¿Es un gasto único o una suscripción a Spotify?",
            "match": "exacto",
        },
        {
            "id": "P10.5",
            "punto": "Punto 10",
            "nombre": "me suscribí a ChatGPT por 20 dólares por mes con medio de pago en pesos: crea la suscripción en dólares",
            "ejecutar": lambda: p10_caso_5(datos),
            "esperado": "Propuesta: True\nSub creada: True\nMoneda sub: USD\nMedio de pago pesos: True",
            "match": "exacto",
        },
        {
            "id": "P10.6",
            "punto": "Punto 10",
            "nombre": "di de baja Netflix sin tenerla: mensaje claro",
            "ejecutar": lambda: p10_caso_6(datos),
            "esperado": "No tenés ninguna suscripción activa a Netflix.",
            "match": "exacto",
        },
        {
            "id": "P10.7",
            "punto": "Punto 10",
            "nombre": "di de baja la suscripción existente: confirma y la da de baja",
            "ejecutar": lambda: p10_caso_7(datos),
            "esperado": "Pregunta confirmación: True\nConfirmación: True\nEstado final: cancelada",
            "match": "exacto",
        },
        {
            "id": "P10.8",
            "punto": "Punto 10",
            "nombre": "aumentó Netflix, ahora son 12000: muestra el precio anterior y el nuevo",
            "ejecutar": lambda: p10_caso_8(datos),
            "esperado": "Propuesta muestra ambos: True\nConfirmación: True\nPrecio en base: 12000.00",
            "match": "exacto",
        },
        {
            "id": "P10.9",
            "punto": "Punto 10",
            "nombre": "cuánto gasto en suscripciones: lista y total mensual",
            "ejecutar": lambda: p10_caso_9(datos),
            "esperado": "Lista activa: True\nTotal mensual: True\nSin saldos billetera: True",
            "match": "exacto",
        },
        {
            "id": "P10.10",
            "punto": "Punto 10",
            "nombre": "Registrar un gasto igual a una suscripción ya cobrada este período: avisa antes",
            "ejecutar": lambda: p10_caso_10(datos),
            "esperado": "Aviso cobro previo: True\nRegistro tras confirmación: True",
            "match": "exacto",
        },
        {
            "id": "P10.11",
            "punto": "Punto 10",
            "nombre": "Crear una suscripción de un servicio que ya tiene activa: avisa",
            "ejecutar": lambda: p10_caso_11(datos),
            "esperado": "Aviso existente: True",
            "match": "exacto",
        },
        {
            "id": "P10.12",
            "punto": "Punto 10",
            "nombre": "Un servicio que no está en el catálogo: lo acepta igual",
            "ejecutar": lambda: p10_caso_12(datos),
            "esperado": "Propuesta servicio no-catálogo: True",
            "match": "exacto",
        },
        # --- PUNTO 11: Lotes y Multi-Operación (10 casos) ---
        {
            "id": "P11.1",
            "punto": "Punto 11",
            "nombre": "Dos gastos con billeteras distintas nombradas explícitamente",
            "ejecutar": lambda: p11_caso_1(datos),
            "esperado": "Propuesta: True | Confirmacion: True",
            "match": "exacto",
        },
        {
            "id": "P11.2",
            "punto": "Punto 11",
            "nombre": "Dos gastos sin billetera, con el usuario teniendo principal",
            "ejecutar": lambda: p11_caso_2(datos),
            "esperado": "Propuesta principal: True",
            "match": "exacto",
        },
        {
            "id": "P11.3",
            "punto": "Punto 11",
            "nombre": "Dos gastos sin billetera, sin principal: pregunta una vez",
            "ejecutar": lambda: p11_caso_3(datos),
            "esperado": "Pregunta una vez: True | Propuesta resuelta: True",
            "match": "exacto",
        },
        {
            "id": "P11.4",
            "punto": "Punto 11",
            "nombre": "Tres gastos donde solo uno nombra billetera",
            "ejecutar": lambda: p11_caso_4(datos),
            "esperado": "Pregunta faltantes: True | Propuesta mixta: True",
            "match": "exacto",
        },
        {
            "id": "P11.5",
            "punto": "Punto 11",
            "nombre": "Un gasto y un ingreso en el mismo mensaje",
            "ejecutar": lambda: p11_caso_5(datos),
            "esperado": "Propuesta signos: True | Confirmacion signos: True",
            "match": "exacto",
        },
        {
            "id": "P11.6",
            "punto": "Punto 11",
            "nombre": "Un lote con un consumo de tarjeta y un gasto normal",
            "ejecutar": lambda: p11_caso_6(datos),
            "esperado": "Lote tarjeta y gasto: True",
            "match": "exacto",
        },
        {
            "id": "P11.7",
            "punto": "Punto 11",
            "nombre": "Doce movimientos: rechaza con mensaje claro",
            "ejecutar": lambda: p11_caso_7(datos),
            "esperado": "Rechazo tope: True",
            "match": "exacto",
        },
        {
            "id": "P11.8",
            "punto": "Punto 11",
            "nombre": "Un lote donde una operación está en otra moneda sin billetera de esa moneda",
            "ejecutar": lambda: p11_caso_8(datos),
            "esperado": "Aviso descarte y propuesta: True",
            "match": "exacto",
        },
        {
            "id": "P11.9",
            "punto": "Punto 11",
            "nombre": "Un lote con dos movimientos idénticos",
            "ejecutar": lambda: p11_caso_9(datos),
            "esperado": "Deteccion duplicado interno: True",
            "match": "exacto",
        },
        {
            "id": "P11.10",
            "punto": "Punto 11",
            "nombre": "Un lote seguido de 'borrá eso'",
            "ejecutar": lambda: p11_caso_10(datos),
            "esperado": "Propuesta deshacer lote: True | Confirmacion deshacer lote: True",
            "match": "exacto",
        },
    ]

    total = len(escenarios)
    aprobados = 0
    omitidos = 0
    fallidos = 0
    detalles_fallidos = []

    print(f"Total escenarios: {total}\n")

    for i, esc in enumerate(escenarios, 1):
        eid = esc["id"]
        punto = esc["punto"]
        nombre = esc["nombre"]

        if esc.get("omitido"):
            omitidos += 1
            print(f"[{eid}] {nombre}: OMITIDO (Motivo: {esc['motivo']})")
            continue

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
    print(f"Total: {total} | Aprobados: {aprobados} | Omitidos: {omitidos} | Fallidos: {fallidos}")

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

    # Verificación de saldos contra referencia histórica de las 21 billeteras
    db_ref = SessionLocal()
    saldos_ref_ok, desvios_ref, detalles_ref = verificar_saldos_contra_referencia(db_ref)
    db_ref.close()

    print("\n=== VERIFICACION DE SALDOS CONTRA REFERENCIA HISTORICA (21 BILLETERAS) ===")
    for d in detalles_ref:
        st = "OK" if d["diff"] == Decimal("0.00") else f"DESVIO ({d['diff']})"
        print(f"  {d['email']} | {d['billetera']}: actual={d['actual']} | ref={d['referencia']} -> {st}")
    print(f"¿Todos los saldos coinciden con la referencia histórica?: {'SÍ' if saldos_ref_ok else 'NO'}")
    if not saldos_ref_ok:
        print(f"ALERTA: Se detectaron {len(desvios_ref)} billeteras con saldos alterados respecto a la referencia:")
        for desv in desvios_ref:
            print(f"  - {desv['email']} ({desv['billetera']}): actual={desv['actual']}, ref={desv['referencia']}, diff={desv['diff']}")

    # Verificación de reconciliación de saldos en todas las 21 billeteras
    db_rec = SessionLocal()
    rec_ok, discrepancias, detalles_rec = verificar_reconciliacion_billeteras(db_rec)
    db_rec.close()

    print("\n=== VERIFICACION DE RECONCILIACION (21 BILLETERAS) ===")
    for d in detalles_rec:
        if d["ok"]:
            st = f"OK (baseline {d['esperado_diff']:+.2f})" if d["esperado_diff"] != Decimal("0.00") else "OK"
        else:
            st = f"DESVIO_NO_ESPERADO (diff={d['diferencia']:+.2f}, esperado={d['esperado_diff']:+.2f})"
        print(f"  {d['email']} | {d['billetera']}: guardado={d['guardado']} | calc={d['calculado']} | diff={d['diferencia']} -> {st}")

    print(f"¿Reconciliación de todas las billeteras dentro del baseline?: {'SÍ' if rec_ok else 'NO'}")
    if not rec_ok:
        print(f"ALERTA: Se detectaron {len(discrepancias)} billeteras con desviaciones fuera del baseline:")
        for disc in discrepancias:
            print(f"  - {disc['email']} ({disc['billetera']}): guardado={disc['guardado']}, calculado={disc['calculado']}, diff={disc['diferencia']}, esperado={disc['esperado_diff']}")

    return total, aprobados, omitidos, fallidos, detalles_fallidos


if __name__ == "__main__":
    correr_suite_completa()
