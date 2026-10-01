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


def p12_caso_1(datos):
    """consultar_balance: 'cuál es mi balance' detecta intent y devuelve balance real del dashboard"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuál es mi balance"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        tiene_datos_reales = "En este ciclo llevás ingresados" in resp and "(balance:" in resp
        return f"Intent: {intent} | Datos reales: {tiene_datos_reales}"
    return run_isolated(test)


def p12_caso_2(datos):
    """consultar_cotizacion: 'a cuánto está el dólar' detecta intent y devuelve cotizaciones reales con mock"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    mock_cotizaciones = {
        "cotizaciones": {
            "blue": {"venta": 1450.0},
            "oficial": {"venta": 1050.0},
            "mep": {"venta": 1400.0},
        }
    }
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.dolar_service.get_cotizaciones_dolar", return_value=mock_cotizaciones):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "a cuánto está el dólar"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        tiene_cotizacion = (
            "Cotizaciones del dólar:" in resp
            and "Dólar Blue: $1.450" in resp
            and "MEP: $1.400" in resp
            and "Oficial: $1.050" in resp
        )
        return f"Intent: {intent} | Cotizacion fija ok: {tiene_cotizacion}"
    return run_isolated(test)


def p12_caso_3(datos):
    """consultar_saldo: 'cuánto tengo' detecta intent y devuelve saldo real del dashboard"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto tengo"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services.dashboard_service import get_dashboard_resumen
            from app.routers.whatsapp.parsers import _fmt
            resumen = get_dashboard_resumen(db, u)
            disp = resumen["disponible_real"]
            ars_total_str = _fmt(disp["ars"]["saldo_billeteras"])
            ars_disp_str = _fmt(disp["ars"]["disponible"])
            datos_reales = (
                "LLM_INVENTADO_999" not in resp
                and ars_total_str in resp
                and ars_disp_str in resp
            )
            if disp["usd"]["saldo_billeteras"] > 0 or disp["usd"]["disponible"] > 0:
                usd_tot_str = _fmt(disp["usd"]["saldo_billeteras"], Moneda.USD)
                usd_disp_str = _fmt(disp["usd"]["disponible"], Moneda.USD)
                datos_reales = datos_reales and (usd_tot_str in resp) and (usd_disp_str in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Datos reales: {datos_reales}"
    return run_isolated(test)


def p12_caso_4(datos):
    """consultar_proyeccion: 'cuál es mi proyección financiera' detecta intent y devuelve proyección real"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuál es mi proyección financiera"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_msg = "No pude calcular tu proyección en este momento. Probá de nuevo en unos minutos."
        proyeccion_ok = bool(resp) and ("LLM_INVENTADO_999" not in resp) and (falla_msg not in resp)
        return f"Intent: {intent} | Proyeccion ok: {proyeccion_ok}"
    return run_isolated(test)


def p12_caso_5(datos):
    """consultar_saldo: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.contexto_financiero_service._calcular_saldo_disponible_sync", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto tengo"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude consultar tu saldo en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)


def p12_caso_6(datos):
    """consultar_balance: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.dashboard_service.calcular_balance_ciclo", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuál es mi balance mensual"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude calcular tu balance en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)


def p12_caso_7(datos):
    """consultar_proyeccion: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.proyeccion_service.calcular_proyeccion", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuál es mi proyección financiera"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude calcular tu proyección en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)


def p12_caso_8(datos):
    """consultar_cotizacion: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.dolar_service.get_cotizaciones_dolar", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "a cuánto cotiza el dólar hoy"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude obtener la cotización del dólar en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)


def p12_caso_9(datos):
    """consultar_cotizacion: cotizaciones vacías devuelve mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.dolar_service.get_cotizaciones_dolar", return_value={"cotizaciones": {}}):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "a cuánto cotiza el dólar hoy"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude obtener la cotización del dólar en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)


def p12_caso_10(datos):
    """consultar_meta: 'cómo va mi meta' detecta intent y devuelve metas reales"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cómo va mi meta"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services.ai_service import construir_contexto_financiero
            from app.routers.whatsapp.parsers import _fmt
            ctx = construir_contexto_financiero(u, db)
            metas = sorted(ctx.get("metas_activas", []), key=lambda x: x["nombre"])
            if not metas:
                datos_reales = (resp == "No tenés metas activas.") and ("LLM_INVENTADO_999" not in resp)
            else:
                datos_reales = ("LLM_INVENTADO_999" not in resp) and resp.startswith("Tus metas activas:")
                for m in metas[:8]:
                    mon = Moneda.USD if m["moneda"] == "USD" else Moneda.ARS
                    obj_str = _fmt(m["objetivo"], mon)
                    acum_str = _fmt(m["acumulado"], mon)
                    datos_reales = datos_reales and (m["nombre"] in resp) and (obj_str in resp) and (acum_str in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Datos reales: {datos_reales}"
    return run_isolated(test)


def p12_caso_11(datos):
    """consultar_presupuesto: 'cómo va mi presupuesto' detecta intent y devuelve presupuestos reales"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cómo va mi presupuesto"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services.ai_service import construir_contexto_financiero
            from app.routers.whatsapp.parsers import _fmt
            ctx = construir_contexto_financiero(u, db)
            presupuestos = sorted(ctx.get("presupuestos_activos", []), key=lambda x: x["nombre"])
            if not presupuestos:
                datos_reales = (resp == "No tenés presupuestos activos.") and ("LLM_INVENTADO_999" not in resp)
            else:
                datos_reales = ("LLM_INVENTADO_999" not in resp) and resp.startswith("Tus presupuestos activos:")
                for p in presupuestos[:8]:
                    mon = Moneda.USD if p["moneda"] == "USD" else Moneda.ARS
                    lim_str = _fmt(p["limite"], mon)
                    usado_str = _fmt(p["monto_usado"], mon)
                    datos_reales = datos_reales and (p["nombre"] in resp) and ("usaste" in resp) and (usado_str in resp) and (lim_str in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Datos reales: {datos_reales}"
    return run_isolated(test)


def p12_caso_12(datos):
    """consultar_meta: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.contexto_financiero_service._resumen_metas_activas_sync", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cómo va mi meta"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude consultar tus metas en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)


def p12_caso_13(datos):
    """consultar_presupuesto: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.contexto_financiero_service._resumen_presupuestos_activos_sync", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cómo va mi presupuesto"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude consultar tus presupuestos en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)
