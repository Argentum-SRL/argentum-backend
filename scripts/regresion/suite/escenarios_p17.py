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


def p17_caso_1(datos):
    """P17.1 con un movimiento ya registrado, 'El 23 de septiembre gasté 5.456 pesos en Uber.' -> nuevo registro Taxi/Apps fecha 23/09, no corrección"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 1000 en el kiosco"), time.perf_counter())
        if respuestas and "¿" in respuestas[-1][1]:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        tx_prev = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc())).scalars().first()
        prev_id = tx_prev.id
        prev_monto = tx_prev.monto

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "El 23 de septiembre gasté 5.456 pesos en Uber."), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""
        if "¿" in resp1 and "corregir" not in resp1.lower():
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc())).scalars().all()
        nuevo = txs[0] if txs else None
        prev_actual = db.get(Transaccion, prev_id)

        no_propuesta_corregir = "corregir" not in resp1.lower()
        monto_ok = nuevo is not None and nuevo.monto == Decimal("5456")
        subcat = db.get(Subcategoria, nuevo.subcategoria_id) if nuevo and nuevo.subcategoria_id else None
        subcat_ok = subcat is not None and subcat.nombre == "Taxi / Apps"
        fecha_ok = nuevo is not None and nuevo.fecha.day == 23 and nuevo.fecha.month == 9
        anterior_intacto = prev_actual is not None and prev_actual.monto == prev_monto and len(txs) >= 2

        return f"No correccion: {no_propuesta_corregir} | Nuevo monto 5456: {monto_ok} | Subcat Taxi/Apps: {subcat_ok} | Fecha 23/09: {fecha_ok} | Anterior intacto: {anterior_intacto}"
    return run_isolated(test)


def p17_caso_2(datos):
    """P17.2 con un movimiento ya registrado, lote de 3 (2 egresos y 1 ingreso), todos con fecha 27/09"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 1000 en el kiosco"), time.perf_counter())
        if respuestas and "¿" in respuestas[-1][1]:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "El 27 de septiembre gasté 2.900 pesos en Uber y gasté 16.900 pesos de los chinos. Después mi mamá me transfirió 300.000 pesos."),
            time.perf_counter()
        )
        resp1 = respuestas[-1][1] if respuestas else ""
        if "¿" in resp1 and "corregir" not in resp1.lower():
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc()).limit(3)
        ).scalars().all()

        cant_ok = len(txs) == 3
        egresos = [t for t in txs if t.tipo == TipoTransaccion.EGRESO]
        ingresos = [t for t in txs if t.tipo == TipoTransaccion.INGRESO]
        tipos_ok = len(egresos) == 2 and len(ingresos) == 1
        fechas_ok = all(t.fecha.day == 27 and t.fecha.month == 9 for t in txs)

        return f"Lote 3 txs: {cant_ok} | 2 egresos 1 ingreso: {tipos_ok} | Todas 27/09: {fechas_ok}"
    return run_isolated(test)


def p17_caso_3(datos):
    """P17.3 con un movimiento ya registrado, lote de 2 egresos con fecha 27/09"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 1000 en el kiosco"), time.perf_counter())
        if respuestas and "¿" in respuestas[-1][1]:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "Bien, y por último, el 27 de septiembre gasté 10.097 pesos en Uber y gasté 45.144 en el chino."),
            time.perf_counter()
        )
        resp1 = respuestas[-1][1] if respuestas else ""
        if "¿" in resp1 and "corregir" not in resp1.lower():
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc()).limit(2)
        ).scalars().all()

        cant_ok = len(txs) == 2
        egresos_ok = all(t.tipo == TipoTransaccion.EGRESO for t in txs)
        fechas_ok = all(t.fecha.day == 27 and t.fecha.month == 9 for t in txs)

        return f"Lote 2 txs: {cant_ok} | 2 egresos: {egresos_ok} | Ambas 27/09: {fechas_ok}"
    return run_isolated(test)


def p17_caso_4(datos):
    """P17.4 con ingreso de 300.000 de hoy, 'ese ingreso es del 27/09' -> propuesta con fecha 27/09 en Ahora, y al confirmar cambia la fecha"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "mi mamá me transfirió 300.000 pesos"), time.perf_counter())
        if respuestas and "¿" in respuestas[-1][1]:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "ese ingreso es del 27/09"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        prop_visible_fecha = ("27" in resp_prop and ("septiembre" in resp_prop.lower() or "09" in resp_prop)) and "corregir" in resp_prop.lower()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        conf_ok = "corregido" in resp_conf.lower() or "listo" in resp_conf.lower()

        tx = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.tipo == TipoTransaccion.INGRESO).order_by(Transaccion.fecha_creacion.desc())
        ).scalars().first()
        fecha_ok = tx is not None and tx.fecha.day == 27 and tx.fecha.month == 9

        return f"Propuesta visible fecha: {prop_visible_fecha} | Confirmado ok: {conf_ok} | Fecha actualizada: {fecha_ok}"
    return run_isolated(test)


def p17_caso_5(datos):
    """P17.5 'Del 13 de septiembre gasté 2.200 pesos en Uber y le transferí a una amiga 14.400 pesos.' -> lote 2 egresos, sin bloqueo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "Del 13 de septiembre gasté 2.200 pesos en Uber y le transferí a una amiga 14.400 pesos."),
            time.perf_counter()
        )
        resp1 = respuestas[-1][1] if respuestas else ""
        sin_bloqueo = "mandalas por separado" not in resp1.lower() and "no puedo mezclar" not in resp1.lower()

        if "¿" in resp1 and "corregir" not in resp1.lower():
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc()).limit(2)
        ).scalars().all()
        egresos_creados = len(txs) == 2 and all(t.tipo == TipoTransaccion.EGRESO for t in txs)

        return f"Sin bloqueo: {sin_bloqueo} | 2 egresos creados: {egresos_creados}"
    return run_isolated(test)


def p17_caso_6(datos):
    """P17.6 'gasté 2.200 en uber y transferí 14.400 de Galicia a Santander' -> bloqueado con MSG_NO_MEZCLAR_TRANSFERENCIAS"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 2.200 en uber y transferí 14.400 de Galicia a Santander"),
            time.perf_counter()
        )
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bloqueado = "mandalas por separado" in resp.lower() or "no puedo mezclar transferencias" in resp.lower()
        creadas = tx_despues - tx_antes

        return f"Bloqueo transferencias propias: {bloqueado} | Creadas: {creadas}"
    return run_isolated(test)


def p17_caso_7(datos):
    """P17.7 'gasté 3.200 en Didi' -> Transporte / Taxi / Apps"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 3.200 en Didi"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""
        if "¿" in resp1:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc())).scalars().first()
        subcat = db.get(Subcategoria, tx.subcategoria_id) if tx and tx.subcategoria_id else None
        cat = db.get(Categoria, tx.categoria_id) if tx and tx.categoria_id else None

        subcat_ok = subcat is not None and subcat.nombre == "Taxi / Apps"
        cat_ok = cat is not None and cat.nombre == "Transporte"

        return f"Subcategoria Taxi/Apps: {subcat_ok} | Categoria Transporte: {cat_ok}"
    return run_isolated(test)


def p17_caso_8(datos):
    """P17.8 'gasté 1.400 en didi, 27.630 en Rappi y 5.000 en Didi Food' -> Taxi / Apps, Delivery y Delivery"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 1.400 en didi, 27.630 en Rappi y 5.000 en Didi Food"),
            time.perf_counter()
        )
        resp1 = respuestas[-1][1] if respuestas else ""
        if "¿" in resp1:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc()).limit(3)).scalars().all()
        tx_map = {int(t.monto): t for t in txs}
        t_didi = tx_map.get(1400)
        t_rappi = tx_map.get(27630)
        t_food = tx_map.get(5000)

        s_didi = db.get(Subcategoria, t_didi.subcategoria_id) if t_didi and t_didi.subcategoria_id else None
        s_rappi = db.get(Subcategoria, t_rappi.subcategoria_id) if t_rappi and t_rappi.subcategoria_id else None
        s_food = db.get(Subcategoria, t_food.subcategoria_id) if t_food and t_food.subcategoria_id else None

        ok_didi = s_didi is not None and s_didi.nombre == "Taxi / Apps"
        ok_rappi = s_rappi is not None and s_rappi.nombre == "Delivery"
        ok_food = s_food is not None and s_food.nombre == "Delivery"

        ajustadas_ok = ok_didi and ok_rappi and ok_food
        return f"Categorias ajustadas ok: {ajustadas_ok}"
    return run_isolated(test)


def p17_caso_9(datos):
    """P17.9 'mi mamá me pasó 100000 el 22/09' y luego 'ayer mi mamá me pasó cien mil pesos' -> registra directo con fecha ayer, sin pregunta duplicado"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "mi mamá me pasó 100000 el 22/09"), time.perf_counter())
        if respuestas and "¿" in respuestas[-1][1]:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "ayer mi mamá me pasó cien mil pesos"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        sin_pregunta_dup = "¿Son dos" not in resp2 and "se te repitió" not in resp2 and "ya registraste" not in resp2
        if "¿" in resp2 and sin_pregunta_dup:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        row = conn.execute(
            text("SELECT entidades FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        fecha_ia_str = (row["entidades"] or {}).get("fecha") if row and row["entidades"] else None

        txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc()).limit(2)).scalars().all()
        tx_segundo = txs[0] if txs else None
        segundo_ingreso_ok = (tx_segundo is not None and tx_segundo.tipo == TipoTransaccion.INGRESO and tx_segundo.monto == Decimal("100000"))
        fecha_no_22 = (tx_segundo.fecha != date(2026, 9, 22)) if tx_segundo else False
        fecha_igual_ia = (tx_segundo.fecha.isoformat() == fecha_ia_str) if (tx_segundo and fecha_ia_str) else False

        return f"Sin pregunta duplicado: {sin_pregunta_dup} | Segundo ingreso: {segundo_ingreso_ok} | Fecha distinta 22/09: {fecha_no_22} | Fecha igual IA: {fecha_igual_ia}" 
    return run_isolated(test)


def p17_caso_10(datos):
    """P17.10 'gasté 4131 en Uber, un amigo me pasó 9000 y una amiga me pasó 9000' -> pregunta duplicado -> 'son dos ingresos distintos' -> confirmación con signos y categorías"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 4131 en Uber, un amigo me pasó 9000 y una amiga me pasó 9000"),
            time.perf_counter()
        )
        resp_dup = respuestas[-1][1] if respuestas else ""
        pregunta_ok = "Mandaste 2 movimientos iguales" in resp_dup or "¿Son dos ingresos distintos" in resp_dup or "se te repitió" in resp_dup

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "son dos ingresos distintos"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        signos_ok = ("+" in resp_conf or "-" in resp_conf) and ("Taxi / Apps" in resp_conf or "Uber" in resp_conf or "Otros" in resp_conf)

        return f"Pregunta duplicado ok: {pregunta_ok} | Confirmacion con signos: {signos_ok}"
    return run_isolated(test)


def p17_caso_11(datos):
    """P17.11 'gasté 5000 en el kiosco' y luego 'no, eran 3000' -> propuesta corrección monto y corrección real funciona"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        if respuestas and "¿" in respuestas[-1][1]:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no, eran 3000"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        prop_ok = "corregir" in resp_prop.lower() and "3.000" in resp_prop

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc())).scalars().first()
        monto_corregido = tx is not None and tx.monto == Decimal("3000")

        return f"Propuesta correccion monto: {prop_ok} | Monto corregido: {monto_corregido}"
    return run_isolated(test)


def p17_caso_12(datos):
    """P17.12 'le transferí 5000 a mi hermano' -> egreso"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "le transferí 5000 a mi hermano"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""
        if "¿" in resp1:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc())).scalars().first()
        tipo_ok = tx is not None and tx.tipo == TipoTransaccion.EGRESO and tx.monto == Decimal("5000")

        return f"Tipo egreso: {tipo_ok}"
    return run_isolated(test)


def p17_caso_13(datos):
    """P17.13 'Juan me transfirió 3000' -> ingreso"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "Juan me transfirió 3000"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""
        if "¿" in resp1:
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc())).scalars().first()
        tipo_ok = tx is not None and tx.tipo == TipoTransaccion.INGRESO and tx.monto == Decimal("3000")

        return f"Tipo ingreso: {tipo_ok}"
    return run_isolated(test)


def p17_caso_14(datos):
    """P17.14 'le pasé 5000 a Juan y transferí 20000 de Galicia a Santander' -> bloqueado con MSG_NO_MEZCLAR_TRANSFERENCIAS"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "le pasé 5000 a Juan y transferí 20000 de Galicia a Santander"),
            time.perf_counter()
        )
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bloqueado = "mandalas por separado" in resp.lower() or "no puedo mezclar transferencias" in resp.lower()
        creadas = tx_despues - tx_antes

        return f"Bloqueo transferencias propias: {bloqueado} | Creadas: {creadas}"
    return run_isolated(test)


def p17_caso_15(datos):
    """P17.15 'El 25/09 gasté 1.000 pesos en el kiosco y 2.000 pesos en la verdulería. El 27/09 gasté 3.000 pesos en Uber.' -> 3 movimientos con fechas 25/09, 25/09 y 27/09"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "El 25/09 gasté 1.000 pesos en el kiosco y 2.000 pesos en la verdulería. El 27/09 gasté 3.000 pesos en Uber."),
            time.perf_counter()
        )
        if respuestas and "¿" in respuestas[-1][1] and "corregir" not in respuestas[-1][1].lower():
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.fecha_creacion.desc()).limit(3)
        ).scalars().all()

        cant_ok = len(txs) == 3
        tx_uber = next((t for t in txs if t.monto == Decimal("3000")), None)
        tx_verduleria = next((t for t in txs if t.monto == Decimal("2000")), None)
        tx_kiosco = next((t for t in txs if t.monto == Decimal("1000")), None)

        fechas_ok = (
            tx_kiosco is not None and tx_kiosco.fecha.day == 25 and tx_kiosco.fecha.month == 9 and
            tx_verduleria is not None and tx_verduleria.fecha.day == 25 and tx_verduleria.fecha.month == 9 and
            tx_uber is not None and tx_uber.fecha.day == 27 and tx_uber.fecha.month == 9
        )

        return f"Lote 3 txs: {cant_ok} | Fechas 25/09 25/09 27/09: {fechas_ok}"
    return run_isolated(test)
