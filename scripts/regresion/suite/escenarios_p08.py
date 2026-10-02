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
            fecha=hoy_argentina(),
            descripcion="Cuota 1/3 Kiosco",
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=2),
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
            fecha=hoy_argentina(),
            descripcion="Supermercado",
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=hace_35_min,
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
            fecha=hoy_argentina(),
            descripcion="Golosinas",
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=b_ef.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=5),
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
