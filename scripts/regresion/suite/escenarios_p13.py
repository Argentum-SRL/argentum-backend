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


def p13_caso_1(datos):
    """consultar_gastos: 'cuánto gasté hoy' detecta intent y calcula gastos de hoy"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté hoy"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services import gastos_consulta_service
            from app.routers.whatsapp.gastos import _formatear_respuesta_gastos
            from app.utils.fecha import hoy_argentina
            hoy = hoy_argentina()
            res = gastos_consulta_service.calcular_gastos_periodo(db, u.id, hoy, hoy, top_n=3)
            msg_esp = _formatear_respuesta_gastos(
                "Hoy", None, False,
                float(res["ars"]["total"]), res["ars"]["cantidad"],
                float(res["usd"]["total"]), res["usd"]["cantidad"],
                res["top_categorias_ars"]
            )
            resp_ok = (resp == msg_esp) and ("LLM_INVENTADO_999" not in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Respuesta ok: {resp_ok}"
    return run_isolated(test)


def p13_caso_2(datos):
    """consultar_gastos: 'cuánto gasté este ciclo' coincide con balance de ciclo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté este ciclo"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services.dashboard_service import calcular_balance_ciclo
            from app.routers.whatsapp.parsers import _fmt
            from app.models.usuario import Moneda
            bal = calcular_balance_ciclo(db, u)
            egr_ars = bal["ars"]["egresos"]
            egr_usd = bal["usd"]["egresos"]
            sin_marcador = "LLM_INVENTADO_999" not in resp
            patron_inicio = r"^En este ciclo(?:\s*\(\d\d/\d\d\s+al\s+\d\d/\d\d\))?\s+"
            if egr_ars == 0 and egr_usd == 0:
                coincide = bool(re.match(patron_inicio + r"no registraste gastos\.", resp)) and sin_marcador
            else:
                coincide = bool(re.match(patron_inicio + r"gastaste\s+", resp)) and sin_marcador
                if egr_ars > 0:
                    coincide = coincide and (_fmt(egr_ars) in resp)
                if egr_usd > 0:
                    coincide = coincide and (_fmt(egr_usd, Moneda.USD) in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Coincide con balance: {coincide}"
    return run_isolated(test)


def p13_caso_3(datos):
    """consultar_gastos: 'cuánto gasté en pizza esta semana' filtra por descripción"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté en pizza esta semana"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        sin_marcador = "LLM_INVENTADO_999" not in resp
        ok = (
            (resp.startswith("Esta semana gastaste ") and resp.endswith(" en «pizza»."))
            or (resp == "Esta semana no encontré gastos que mencionen «pizza».")
        ) and sin_marcador
        return f"Intent: {intent} | Filtro por descripcion: {ok}"
    return run_isolated(test)


def p13_caso_4(datos):
    """consultar_gastos: 'cuánto gasté en supermercado el mes pasado' filtra por catálogo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté en supermercado el mes pasado"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        sin_marcador = "LLM_INVENTADO_999" not in resp
        patron_inicio = r"^El mes pasado(?:\s*\(\d\d/\d\d\s+al\s+\d\d/\d\d\))?\s+"
        ok = (
            (bool(re.match(patron_inicio + r"gastaste\s+", resp)) and resp.endswith(" en Supermercado."))
            or bool(re.match(patron_inicio + r"no registraste gastos en Supermercado\.", resp))
        ) and sin_marcador
        return f"Intent: {intent} | Filtro por catalogo: {ok}"
    return run_isolated(test)


def p13_caso_5(datos):
    """consultar_gastos: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.gastos_consulta_service.calcular_gastos_periodo", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté hoy"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude consultar tus gastos en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)
