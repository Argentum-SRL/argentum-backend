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


def p14_caso_1(datos):
    """P14.1 'gasté 5000 en el kiosco': registro directo en el acto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        
        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        db.refresh(b_db)
        saldo_diff = b_db.saldo_actual - s0

        row = conn.execute(
            text("SELECT id, accion_ejecutada FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()

        tx_nueva = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP).order_by(Transaccion.id.desc())
        ).scalars().first()

        accion_ok = (row and tx_nueva and row["accion_ejecutada"] == str(tx_nueva.id))

        from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)

        resp_ok = resp.startswith("Listo.") and ("¿Va?" not in resp)

        return (
            f"Resp ok: {resp_ok} | Txs: {txs_creadas} | Saldo: {saldo_diff} | "
            f"Accion ejecutada: {accion_ok} | Propuesta pendiente: {prop_pendiente is None}"
        )
    return run_isolated(test)


def p14_caso_2(datos):
    """P14.2 lote de 2: registra directo 2 movimientos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 8000 en la verdulería"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        row = conn.execute(
            text("SELECT accion_ejecutada FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()

        accion_val = row["accion_ejecutada"] if row else ""
        es_lote_accion = accion_val.startswith("lote:") and len(accion_val.replace("lote:", "").split(",")) == 2
        resp_ok = resp.startswith("Listo, 2 movimientos")

        return f"Resp ok: {resp_ok} | Txs: {txs_creadas} | Accion lote: {es_lote_accion}"
    return run_isolated(test)


def p14_caso_3(datos):
    """P14.3 ingreso simple: registra en el acto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cobré 800000 de sueldo"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        db.refresh(b_db)
        saldo_diff = b_db.saldo_actual - s0
        resp_ok = resp.startswith("Listo.") and ("¿Va?" not in resp)

        return f"Resp ok: {resp_ok} | Txs: {txs_creadas} | Saldo: +{saldo_diff}"
    return run_isolated(test)


def p14_caso_4(datos):
    """P14.4 compra con tarjeta de crédito en cuotas: NO registra directo, pide confirmación"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré una tele en 12 cuotas de 80000"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        tx_intermedio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_antes_si = tx_intermedio - tx_antes

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues_si = tx_final - tx_intermedio

        pide_conf = ("¿Va?" in resp1 or "¿Confirmás?" in resp1 or "Voy a anotar" in resp1)
        return (
            f"Pide conf: {pide_conf} | Txs antes de si: {txs_antes_si} | "
            f"Txs despues de si: {txs_despues_si > 0} | Confirma ok: {'Listo' in resp2}"
        )
    return run_isolated(test)


def p14_caso_5(datos):
    """P14.5 el mismo gasto dos veces seguidas: el segundo pregunta por duplicado y no registra"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Primer gasto -> registro directo
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        tx_medio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Segundo gasto idéntico -> detección duplicado
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp_dup = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        pregunta_dup = "¿Es un movimiento nuevo o se te repitió?" in resp_dup or "ya registraste" in resp_dup
        txs_totales = tx_final - tx_antes

        return f"Pregunta duplicado: {pregunta_dup} | Txs totales: {txs_totales}"
    return run_isolated(test)


def p14_caso_6(datos):
    """P14.6 confianza 0.70: resultado idéntico a HEAD y 0 transacciones"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        resp_head_esperada = "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nSi fue con otra, decime cuál."
        coincide_head = (resp.strip() == resp_head_esperada.strip())

        return f"Coincide con HEAD: {coincide_head} | Txs creadas: {txs_creadas}"
    return run_isolated(test)


def p14_caso_7(datos):
    """P14.7 'sí' justo después de un registro directo: mensaje claro y 0 transacciones nuevas"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        # Registro directo
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        tx_medio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Usuario manda "sí"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_si = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_nuevas = tx_final - tx_medio

        esperado = "Ya quedó anotado. Si hay algo mal, decime qué corregir."
        resp_ok = (resp_si.strip() == esperado.strip())

        return f"Resp ok: {resp_ok} | Txs nuevas: {txs_nuevas}"
    return run_isolated(test)


def p14_caso_8(datos):
    """P14.8 registro directo, 'deshacer' y 'sí': borra transacción y restaura saldo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Registro directo
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        # Deshacer
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "deshacer"), time.perf_counter())
        resp_deshacer = respuestas[-1][1] if respuestas else ""

        # Sí
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        db.refresh(b_db)

        txs_restantes = tx_despues - tx_antes
        saldo_restaurado = (b_db.saldo_actual == s0)
        conf_ok = "Listo" in resp_conf and ("eliminad" in resp_conf or "borrad" in resp_conf or "anulad" in resp_conf)

        return f"Deshacer ok: {conf_ok} | Txs restantes: {txs_restantes} | Saldo restaurado: {saldo_restaurado}"
    return run_isolated(test)


def p14_caso_9(datos):
    """P14.9 registro directo y 'eran 3000 no 5000': corrige monto y saldo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual

        # Registro directo de 5000
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        # Corregir monto a 3000
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eran 3000 no 5000"), time.perf_counter())
        resp_corr = respuestas[-1][1] if respuestas else ""

        # Sí a la propuesta de corrección
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        db.refresh(b_db)
        tx = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)
        ).scalars().first()

        monto_corregido = (tx.monto == Decimal("3000")) if tx else False
        saldo_ok = (b_db.saldo_actual == s0 - Decimal("3000"))

        return f"Monto corregido: {monto_corregido} | Saldo ok: {saldo_ok} | Confirmacion: {'Listo' in resp_conf}"
    return run_isolated(test)


def p14_caso_10(datos):
    """P14.10 falla forzada: rollback limpio, 0 transacciones, saldo intacto, ninguna propuesta y sí posterior inocuo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        with patch("app.routers.whatsapp.registro._confirmar_propuesta_transaccion", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        sin_listo = "Listo" not in resp1
        db.refresh(b_db)
        saldo_intacto = (b_db.saldo_actual == s0)

        tx_medio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_medio - tx_antes

        from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)

        # Enviar "sí" posterior
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues_si = tx_final - tx_medio

        return (
            f"Sin listo: {sin_listo} | Txs creadas: {txs_creadas} | Saldo intacto: {saldo_intacto} | "
            f"Propuesta pendiente: {prop_pendiente is None} | Txs despues si: {txs_despues_si}"
        )
    return run_isolated(test)


def p14_caso_11(datos):
    """P14.11 idempotencia: el mismo wamid dos veces resulta en 1 sola transacción"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        fixed_wamid = f"wamid_idemp_{uuid.uuid4().hex[:12]}"
        payload = make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco", wamid=fixed_wamid)

        # Primer envío
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

        # Segundo envío idéntico con el mismo wamid
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        return f"Txs creadas: {txs_creadas}"
    return run_isolated(test)


def p14_caso_12(datos):
    """P14.12 billetera ambigua: pregunta cuál; al responder registra en el acto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Primer mensaje sin principal -> pregunta billetera
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp_preg = respuestas[-1][1] if respuestas else ""

        tx_medio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_antes_eleccion = tx_medio - tx_antes

        # Responder eligiendo opción 2 (Galicia) -> registro directo
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp_reg = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues_eleccion = tx_final - tx_medio

        b_db = db.get(Billetera, b_gal.id)
        db.refresh(b_db)

        pregunta_ok = "¿Desde qué billetera" in resp_preg
        registro_ok = resp_reg.startswith("Listo.") and ("¿Va?" not in resp_reg)

        return (
            f"Pregunta ok: {pregunta_ok} | Txs antes eleccion: {txs_antes_eleccion} | "
            f"Registro ok: {registro_ok} | Txs creadas: {txs_despues_eleccion}"
        )
    return run_isolated(test)


def p14_caso_14(datos):
    """P14.14 lote con todos los ítems inválidos: mensaje claro, 0 transacciones y sin propuesta"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND moneda = 'USD'"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 50 dólares en un libro y 20 dólares en café"),
            time.perf_counter()
        )
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)

        msg_esperado = "No se puede registrar ningún movimiento." in resp

        return f"Msg ok: {msg_esperado} | Txs creadas: {txs_creadas} | Propuesta pendiente: {prop_pendiente is None}"
    return run_isolated(test)


def p14_caso_15(datos):
    """P14.15 lote con un ítem inválido y uno válido: registra el válido con aviso previo al Listo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND moneda = 'USD'"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 50 dólares en un libro"),
            time.perf_counter()
        )
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        aviso_descarte = "No se pudo registrar" in resp and "dólares" in resp
        listo_ok = "Listo." in resp and "¿Va?" not in resp
        orden_ok = resp.find("No se pudo registrar") < resp.find("Listo.") if (aviso_descarte and listo_ok) else False

        return f"Aviso descarte: {aviso_descarte} | Listo ok: {listo_ok} | Orden ok: {orden_ok} | Txs: {txs_creadas}"
    return run_isolated(test)


def p14_caso_13(datos):
    """P14.13 comprobante imagen con billetera pendiente: tras elegir billetera pide confirmación (0 txs) y recién con sí registra (1 tx)"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        conv_sf = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_sf_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="comprobante",
            tipo_mensaje=TipoMensajeWpp.IMAGEN,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo Pesos\n2. Galicia\n3. Santander",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"], "origen_imagen": True},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"], "origen_imagen": True},
            confianza=Decimal("0.900"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_sf)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        tx_despues_menu = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_antes_si = tx_despues_menu - tx_antes

        pide_conf = ("¿Va?" in resp1 or "¿Confirmás?" in resp1) and "Listo." not in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        tx_despues_si = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues = tx_despues_si - tx_antes
        confirma_ok = "Listo." in resp2

        return f"Pide conf: {pide_conf} | Txs antes si: {txs_antes_si} | Txs despues si: {txs_despues} | Confirma ok: {confirma_ok}"
    return run_isolated(test)


def p14_caso_16(datos):
    """P14.16 confianza baja con billetera pendiente: tras elegir billetera pide confirmación (0 txs) y recién con sí registra (1 tx)"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        conv_sf = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_sf_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo Pesos\n2. Galicia\n3. Santander",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"], "confianza_baja": True},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"], "confianza_baja": True},
            confianza=Decimal("0.900"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_sf)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        tx_despues_menu = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_antes_si = tx_despues_menu - tx_antes

        pide_conf = ("¿Va?" in resp1 or "¿Confirmás?" in resp1) and "Listo." not in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        tx_despues_si = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues = tx_despues_si - tx_antes
        confirma_ok = "Listo." in resp2

        return f"Pide conf: {pide_conf} | Txs antes si: {txs_antes_si} | Txs despues si: {txs_despues} | Confirma ok: {confirma_ok}"
    return run_isolated(test)


def p14_caso_17(datos):
    """P14.17 control sin marcas con billetera pendiente: tras elegir billetera registra directo (1 tx)"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        conv_sf = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_sf_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo Pesos\n2. Galicia\n3. Santander",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            confianza=Decimal("0.900"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_sf)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        tx_despues_menu = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues_menu - tx_antes

        registra_directo = "Listo." in resp1 and ("¿Va?" not in resp1 and "¿Confirmás?" not in resp1)

        return f"Registra directo: {registra_directo} | Txs: {txs_creadas}"
    return run_isolated(test)
