from __future__ import annotations

import time
import uuid
import re
import logging
import threading
from decimal import Decimal
from datetime import datetime, date, timezone, timedelta
from unittest.mock import patch

from sqlalchemy import select, text, func
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
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
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.schemas.suscripcion import SuscripcionCreate
from app.services import suscripcion_service, ai_service
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from app.routers.whatsapp.propuestas import _construir_propuesta_transaccion
from app.routers.whatsapp.registro import _confirmar_propuesta_transaccion
from app.routers.whatsapp.db_lookups import _resolver_categoria_y_subcategoria
from app.utils.fecha import hoy_argentina

from scripts.regresion.suite.comun import (
    USUARIO_PRUEBAS_EMAIL,
    TELEFONO_TEST,
    make_payload,
    run_isolated,
    _normalizar_accion_ejecutada,
)


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
        resp = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        patron = r"^(?:En este ciclo(?:\s*\(\d\d/\d\d\s+al\s+\d\d/\d\d\))?|Hoy)\s+(?:gastaste|no registraste gastos|no encontré gastos)"
        msg_ok = bool(re.match(patron, resp))
        return f"Intent: {intent} | Respuesta ok: {msg_ok}"
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
