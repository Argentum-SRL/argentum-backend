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
        ok_prop = "Listo," in resp_prop and "5.000 en Kiosco desde Galicia" in resp_prop and "8.000 en Verdulería desde Santander" in resp_prop
        ok_conf = (resp_conf.strip() == "Ya quedó anotado. Si hay algo mal, decime qué corregir.")
        return (
            f"Propuesta: {ok_prop} | "
            f"Confirmacion: {ok_conf}"
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
            f"Propuesta principal: {'2 movimientos desde Galicia:\n\n- $5.000 en Kiosco\n- $8.000 en Verdulería' in resp_prop and 'Si fue con otra, decime cuál.' in resp_prop}"
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
            f"Propuesta resuelta: {'2 movimientos desde Galicia:\n\n- $5.000 en Kiosco\n- $8.000 en Verdulería' in resp_prop}"
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
        ok_prop = "Listo," in resp_prop and "+$100.000" in resp_prop and "-$30.000" in resp_prop
        ok_conf = (resp_conf.strip() == "Ya quedó anotado. Si hay algo mal, decime qué corregir.")
        return (
            f"Propuesta signos: {ok_prop} | "
            f"Confirmacion signos: {ok_conf}"
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
            f"Aviso descarte y propuesta: {'No se pudo registrar' in resp and 'dólares' in resp and 'Listo. $5.000 en Kiosco desde Galicia' in resp}"
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


def p11_caso_11(datos):
    """Lote de 3 gastos en un solo mensaje: confirma, crea 3 txs, valida accion_ejecutada > 100 caracteres"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Enviar mensaje con 3 gastos
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco, 8000 en la verdulería y 3000 en la panadería"),
            time.perf_counter()
        )

        # Confirmar con "sí"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "sí"),
            time.perf_counter()
        )
        resp_final = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        tx_creadas = tx_despues - tx_antes

        row = conn.execute(
            text("SELECT accion_ejecutada, length(accion_ejecutada) as largo FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        accion_len = row["largo"] if row and row["largo"] is not None else 0

        return (
            f"Txs creadas: {tx_creadas} | "
            f"Accion len ok: {accion_len > 100} | "
            f"Sin error: {'Hubo un problema' not in resp_final}"
        )
    return run_isolated(test)


def p11_caso_12(datos):
    """Falso positivo corregido: lote con 'pasé al kiosco' no bloquea por transferencia y registra movimientos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        texto_real = (
            "Hoy me pagaron 50.000 pesos porque le arreglé la compa a una amiga, "
            "con esos 50.000 pesos gasté 10 en la verdulería, me compré un maple de huevos por 4.500, "
            "fui a la carnicería y compré por 10.000 pesos más pechugas de pollo, "
            "pasé al kiosco y me compré un chocolate por 2.790."
        )
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, texto_real),
            time.perf_counter()
        )
        resp_inicial = respuestas[-1][1] if respuestas else ""

        tx_despues_1 = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        if tx_despues_1 == tx_antes and ("¿Confirmás" in resp_inicial or "confirmar" in resp_inicial.lower()):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "sí"),
                time.perf_counter()
            )

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        tx_creadas = tx_final - tx_antes

        conv = conn.execute(
            text("SELECT intent_detectado, mensaje_bot FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()

        no_bloqueado = "mandalas por separado" not in (conv["mensaje_bot"] if conv else "")
        sin_intent_invalido = (conv["intent_detectado"] != "mezcla_transferencia_invalida") if conv else False

        return (
            f"Falso positivo evitado: {no_bloqueado and sin_intent_invalido} | "
            f"Txs creadas ok: {tx_creadas >= 1}"
        )
    return run_isolated(test)


def p11_caso_13(datos):
    """Pago a un tercero dentro de un lote no bloquea: se registran los dos gastos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "le transferí 5000 a mi hermano y también gasté 3000 en el kiosco"),
            time.perf_counter()
        )
        if respuestas and "¿" in respuestas[-1][1] and "corregir" not in respuestas[-1][1].lower():
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        tx_creadas = tx_despues - tx_antes

        conv = conn.execute(
            text("SELECT intent_detectado, mensaje_bot FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()

        no_bloqueado = (
            conv is None
            or (
                conv["intent_detectado"] not in ("mezcla_transferencia_invalida", "no_mezclar_transferencias")
                and "mandalas por separado" not in (conv["mensaje_bot"] or "").lower()
            )
        )

        txs = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc()).limit(2)
        ).scalars().all()

        cant_ok = len(txs) == 2
        egresos_ok = all(t.tipo == TipoTransaccion.EGRESO for t in txs)
        montos = sorted([t.monto for t in txs])
        montos_ok = (montos == [Decimal("3000"), Decimal("5000")])

        return (
            f"No bloqueado: {no_bloqueado} | "
            f"Dos egresos: {cant_ok and egresos_ok} | "
            f"Montos 3000 y 5000: {montos_ok}"
        )
    return run_isolated(test)
