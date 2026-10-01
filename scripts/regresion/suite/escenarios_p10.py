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


def _limpiar_subs(conn, u_id):
    conn.execute(text("DELETE FROM historial_suscripciones WHERE suscripcion_id IN (SELECT id FROM suscripciones WHERE usuario_id = :uid)"), {"uid": u_id})
    conn.execute(text("DELETE FROM suscripciones WHERE usuario_id = :uid"), {"uid": u_id})


def p10_caso_1(datos):
    """empecé a pagar 5000 de Disney+: pregunta la frecuencia, crea la suscripción, no cobra nada"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        _limpiar_subs(conn, u.id)
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
        txs_sub = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id, Transaccion.suscripcion_id == sub.id)).scalar() if sub else 0

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
        _limpiar_subs(conn, u.id)
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
        _limpiar_subs(conn, u.id)
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
        _limpiar_subs(conn, u.id)
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
        _limpiar_subs(conn, u.id)
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
        _limpiar_subs(conn, u.id)
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


def p10_caso_13(datos):
    """alta de gimnasio por WhatsApp con categoría sugerida Salud / Deportes y gimnasio"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        _limpiar_subs(conn, u.id)
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "empecé a pagar 45000 del gimnasio por mes"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""
        sub = db.execute(select(Suscripcion).where(Suscripcion.usuario_id == u.id, Suscripcion.nombre.ilike("%gimnasio%"))).scalars().first()
        cat = db.get(Categoria, sub.categoria_id) if sub and sub.categoria_id else None
        subcat = db.get(Subcategoria, sub.subcategoria_id) if sub and sub.subcategoria_id else None
        _limpiar_subs(conn, u.id)
        return (
            f"Cat: {cat.nombre if cat else ''} / {subcat.nombre if subcat else ''} | "
            f"Conf: {'La anoté en Salud / Deportes y gimnasio' in resp_conf}"
        )
    return run_isolated(test)
