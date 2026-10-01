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


GRABACIONES_P16 = {
    "P16.1": {
        "escenario_id": "P16.1",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 15000 a mi meta Fondo de Emergencia",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 15000,
                        "moneda": "ARS",
                        "meta": "Fondo de Emergencia",
                        "descripcion": "Fondo de Emergencia",
                        "billetera": None,
                        "billetera_origen": None,
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a aportar $15.000 a tu meta 'Fondo de Emergencia' desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
    "P16.2": {
        "escenario_id": "P16.2",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 10000 a la meta Vacaciones",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 10000,
                        "moneda": "ARS",
                        "meta": "Vacaciones",
                        "descripcion": "Vacaciones",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Tenés más de una meta que coincide con 'Vacaciones'. ¿A cuál te referís?",
                },
            }
        ],
    },
    "P16.3": {
        "escenario_id": "P16.3",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 5000 a la meta Casa en la playa",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 5000,
                        "moneda": "ARS",
                        "meta": "Casa en la playa",
                        "descripcion": "Casa en la playa",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "No encontré ninguna meta con el nombre 'Casa en la playa'.",
                },
            }
        ],
    },
    "P16.4": {
        "escenario_id": "P16.4",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 5000 a la meta Curso de Inglés",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 5000,
                        "moneda": "ARS",
                        "meta": "Curso de Inglés",
                        "descripcion": "Curso de Inglés",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a aportar $5.000 a tu meta 'Curso de Inglés' desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
    "P16.5": {
        "escenario_id": "P16.5",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 15000 a mi meta Fondo de Emergencia",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 15000,
                        "moneda": "ARS",
                        "meta": "Fondo de Emergencia",
                        "descripcion": "Fondo de Emergencia",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a aportar $15.000 a tu meta 'Fondo de Emergencia' desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
    "P16.6": {
        "escenario_id": "P16.6",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 15000 a mi meta Fondo de Emergencia",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 15000,
                        "moneda": "ARS",
                        "meta": "Fondo de Emergencia",
                        "descripcion": "Fondo de Emergencia",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a aportar $15.000 a tu meta 'Fondo de Emergencia' desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
}


def p16_caso_1(datos):
    """P16.1 Aporte simple con meta y monto claros: propone, confirma, aumenta monto_actual y crea tx"""
    from app.models.meta import Meta
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        meta = db.execute(select(Meta).where(Meta.usuario_id == u.id, Meta.nombre == "Fondo de Emergencia")).scalar_one()
        m_ini = meta.monto_actual
        b_db = db.get(Billetera, b_gal.id)
        s_ini = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 15000 a mi meta Fondo de Emergencia"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""

        db.refresh(meta)
        db.refresh(b_db)
        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        tx_nueva = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.movimiento_meta_id.is_not(None)).order_by(Transaccion.fecha_creacion.desc())
        ).scalars().first()

        prop_ok = "¿Confirmás?" in prop and "15.000" in prop and "Fondo de Emergencia" in prop
        conf_ok = "Listo." in conf and "Aporté $15.000" in conf and "Fondo de Emergencia" in conf
        monto_sumado = int(meta.monto_actual - m_ini)
        tx_ok = (tx_despues == tx_antes + 1) and (tx_nueva.descripcion == "Aporte a la meta: Fondo de Emergencia") and (tx_nueva.movimiento_meta_id is not None)
        saldo_ok = (b_db.saldo_actual == s_ini - Decimal("15000"))

        return f"Propuesta ok: {prop_ok} | Confirmado ok: {conf_ok} | Monto sumado: {monto_sumado} | Tx desc ok: {tx_ok} | Saldo Galicia ok: {saldo_ok}"
    return run_isolated(test)


def p16_caso_2(datos):
    """P16.2 Meta ambigua: pregunta cuál antes de proponer"""
    from app.models.meta import Meta
    from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        m1 = Meta(usuario_id=u.id, nombre="Vacaciones Brasil", monto_objetivo=100000, moneda="ARS", monto_actual=0, estado="activa")
        m2 = Meta(usuario_id=u.id, nombre="Vacaciones Bariloche", monto_objetivo=100000, moneda="ARS", monto_actual=0, estado="activa")
        db.add_all([m1, m2])
        db.commit()

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 10000 a la meta Vacaciones"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)
        es_ambigua = "Encontré más de una meta parecida:" in resp and "Vacaciones Bariloche" in resp and "Vacaciones Brasil" in resp

        return f"Pregunta ambigua: {es_ambigua} | Txs creadas: {tx_despues - tx_antes} | Sin propuesta pendiente: {prop_pendiente is None}"
    return run_isolated(test)


def p16_caso_3(datos):
    """P16.3 Meta inexistente: mensaje claro, no propone nada"""
    from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 5000 a la meta Casa en la playa"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)
        msg_claro = "No encontré ninguna meta con el nombre 'Casa en la playa'" in resp

        return f"Mensaje claro: {msg_claro} | Txs creadas: {tx_despues - tx_antes} | Sin propuesta pendiente: {prop_pendiente is None}"
    return run_isolated(test)


def p16_caso_4(datos):
    """P16.4 Monto que completa o supera el objetivo: confirma y felicita"""
    from app.models.meta import Meta
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        m_curso = Meta(usuario_id=u.id, nombre="Curso de Inglés", monto_objetivo=10000, moneda="ARS", monto_actual=8000, estado="activa")
        db.add(m_curso)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 5000 a la meta Curso de Inglés"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""

        db.refresh(m_curso)
        prop_ok = "¿Confirmás?" in prop and "Curso de Inglés" in prop
        felicita = "¡Felicitaciones! Completaste tu meta." in conf
        monto_actual_ok = (m_curso.monto_actual == Decimal("13000"))
        completada_ok = (m_curso.estado == "completada")

        return f"Propuesta ok: {prop_ok} | Felicita: {felicita} | Monto actual: {int(m_curso.monto_actual)} | Estado completada: {completada_ok}"
    return run_isolated(test)


def p16_caso_5(datos):
    """P16.5 'No' cancela la propuesta sin tocar la meta"""
    from app.models.meta import Meta
    from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        meta = db.execute(select(Meta).where(Meta.usuario_id == u.id, Meta.nombre == "Fondo de Emergencia")).scalar_one()
        m_ini = meta.monto_actual
        b_db = db.get(Billetera, b_gal.id)
        s_ini = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 15000 a mi meta Fondo de Emergencia"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp_canc = respuestas[-1][1] if respuestas else ""

        db.refresh(meta)
        db.refresh(b_db)
        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)

        cancelado_ok = "Listo, cancelado." in resp_canc
        meta_intacta = (meta.monto_actual == m_ini)
        saldo_intacta = (b_db.saldo_actual == s_ini)
        txs_creadas = tx_despues - tx_antes

        return f"Cancelado: {cancelado_ok} | Meta intacta: {meta_intacta} | Txs creadas: {txs_creadas} | Saldo intacto: {saldo_intacta}"
    return run_isolated(test)


def p16_caso_6(datos):
    """P16.6 Deshacer un aporte recién confirmado: revierte monto_actual y borra la transacción"""
    from app.models.meta import Meta
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        meta = db.execute(select(Meta).where(Meta.usuario_id == u.id, Meta.nombre == "Fondo de Emergencia")).scalar_one()
        m_ini = meta.monto_actual
        b_db = db.get(Billetera, b_gal.id)
        s_ini = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 15000 a mi meta Fondo de Emergencia"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        # Pedir deshacer
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        resp_prop_deshacer = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf_deshacer = respuestas[-1][1] if respuestas else ""

        db.refresh(meta)
        db.refresh(b_db)
        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        prop_desh_ok = "¿Querés eliminar el aporte" in resp_prop_deshacer and "Fondo de Emergencia" in resp_prop_deshacer
        conf_desh_ok = "Listo, movimiento eliminado." in resp_conf_deshacer
        meta_revertida = (meta.monto_actual == m_ini)
        tx_borrada = (tx_despues == tx_antes)
        saldo_restaurado = (b_db.saldo_actual == s_ini)

        return f"Propuesta deshacer ok: {prop_desh_ok} | Confirmacion deshacer ok: {conf_desh_ok} | Meta revertida: {meta_revertida} | Tx borrada: {tx_borrada} | Saldo restaurado: {saldo_restaurado}"
    return run_isolated(test)
