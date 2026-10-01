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

        # Captura temporal del aviso esperado de cotización histórica no encontrada
        dolar_logger = logging.getLogger("app.services.dolar_service")
        prop_original = dolar_logger.propagate
        registros_capturados = []

        class _HandlerAviso(logging.Handler):
            def emit(self, record):
                registros_capturados.append(record)

        handler = _HandlerAviso()
        dolar_logger.addHandler(handler)
        dolar_logger.propagate = False

        try:
            respuestas.clear()
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 1500"), time.perf_counter())
        finally:
            dolar_logger.removeHandler(handler)
            dolar_logger.propagate = prop_original

        # Verificar que el aviso esperado se haya emitido efectivamente
        aviso_emitido = any("No se encontró cotización histórica" in rec.getMessage() for rec in registros_capturados)
        assert aviso_emitido, "Se esperaba el aviso de cotización histórica no encontrada para la tabla vacía."

        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)


def p9b_caso_18(datos):
    """pasé 50000 de Galicia a Efectivo USD: intent genérico multi-moneda sin cotización debe preguntar, no acreditar 1:1"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        trs_ini = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 50000 de Galicia a Efectivo USD"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        trs_fin = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        no_acredito_1a1 = (trs_ini == trs_fin)
        return f"Pregunta: {resp} | Sin acreditar 1a1: {no_acredito_1a1}"
    return run_isolated(test)


def p9b_caso_19(datos):
    """pasé 50 de Efectivo USD a Galicia: intent genérico USD->ARS sin cotización debe preguntar, no acreditar 1:1"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        trs_ini = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 50 de Efectivo USD a Galicia"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        trs_fin = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        no_acredito_1a1 = (trs_ini == trs_fin)
        return f"Pregunta: {resp} | Sin acreditar 1a1: {no_acredito_1a1}"
    return run_isolated(test)


def p9b_caso_20(datos):
    """pasé 15000 de Galicia a Santander: transferencia misma moneda funciona directo sin preguntas adicionales"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 15000 de Galicia a Santander"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""

        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        cero_gastos = (txs_ini == txs_fin)
        saldos_ok = (bg_fin == bg_ini - Decimal("15000") and bs_fin == bs_ini + Decimal("15000"))
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok} | Cero gastos: {cero_gastos}"
    return run_isolated(test)
