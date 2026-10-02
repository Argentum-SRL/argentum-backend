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

_colector_salidas = None

def p5_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)


def p5_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "es nuevo"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)


def p5_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "es un error"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)


def p5_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")
        tx_antigua = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            fecha=hoy_argentina(),
            descripcion="TEST_REG_ANTIGUA",
            metodo_pago="efectivo",
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(hours=2, minutes=5),
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db.add(tx_antigua)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)


def p5_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en la farmacia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)


def p5_caso_6_concurrente(datos):
    """
    Prueba concurrencia real garantizando limpieza absoluta e infalible de base de datos.
    Usa exclusivamente testingadmin@argentum.com y su billetera Galicia.
    Registra snapshot exacto de IDs de transacciones antes y después, y borra la diferencia.
    """
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    saldo_original = b.saldo_actual
    
    # Snapshot de transacciones existentes antes de la prueba
    snap_db = SessionLocal()
    tx_ids_antes = set(snap_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
    snap_db.close()

    pid = uuid.uuid4()
    p_wamid = f"wamid_reg_c6_prop_{uuid.uuid4().hex}"
    wamid1 = f"wamid_reg_c6_conf_1_{uuid.uuid4().hex}"
    wamid2 = f"wamid_reg_c6_conf_2_{uuid.uuid4().hex}"

    db = SessionLocal()
    prop = ConversacionWpp(
        id=pid,
        usuario_id=u.id,
        wamid=p_wamid,
        mensaje_usuario="gasté 5000 en el kiosco",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot=f"Voy a anotar $5.000 en Kiosco desde {b.nombre}. ¿Va?",
        intent_detectado="registrar_transaccion",
        entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": b.nombre},
        slot_filling_activo=False,
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(prop)
    db.commit()
    db.close()

    resp_c6 = []
    lock = threading.Lock()
    def mock_envio(to, msg):
        with lock:
            resp_c6.append(msg)
            if _colector_salidas is not None:
                _colector_salidas.registrar_mensaje(msg)

    def _mock_buscar_concurrente(tel, db_sess):
        usr = db_sess.query(Usuario).filter(Usuario.email == USUARIO_PRUEBAS_EMAIL).first()
        if not usr or usr.email != USUARIO_PRUEBAS_EMAIL:
            raise RuntimeError(f"ABORT CRITICO: Concurrencia intentó usar otro usuario: {getattr(usr, 'email', None)}")
        return usr

    bar = threading.Barrier(2)
    def worker(wid, wamid_val):
        payload = make_payload(TELEFONO_TEST, "sí", wamid_val)
        bar.wait()
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

    try:
        # Modularización WhatsApp: Un solo camino de envío vía whatsapp_service.enviar_whatsapp.
        with patch("app.routers.whatsapp_ia._buscar_usuario_por_telefono", side_effect=_mock_buscar_concurrente), \
             patch("app.services.whatsapp_service.enviar_whatsapp", side_effect=mock_envio), \
             patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
            th1 = threading.Thread(target=worker, args=(1, wamid1))
            th2 = threading.Thread(target=worker, args=(2, wamid2))
            th1.start()
            th2.start()
            th1.join()
            th2.join()
    finally:
        # Captura de foto de salidas antes de borrar
        if _colector_salidas is not None and _colector_salidas.escenario_actual == "P5.6":
            snap_c6 = SessionLocal()
            try:
                tx_ids_despues_c6 = set(snap_c6.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
                creadas_c6 = tx_ids_despues_c6 - tx_ids_antes
                tx_nuevas = snap_c6.execute(select(Transaccion).where(Transaccion.id.in_(creadas_c6))).scalars().all() if creadas_c6 else []
                cat_map = dict(snap_c6.execute(select(Categoria.id, Categoria.nombre)).all())
                subcat_map = dict(snap_c6.execute(select(Subcategoria.id, Subcategoria.nombre)).all())
                bill_map = dict(snap_c6.execute(select(Billetera.id, Billetera.nombre)).all())
                movs = []
                for tx in tx_nuevas:
                    movs.append({
                        "tipo": tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo),
                        "monto": float(tx.monto),
                        "moneda": tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda),
                        "fecha": tx.fecha.isoformat() if hasattr(tx.fecha, "isoformat") else str(tx.fecha),
                        "categoria": cat_map.get(tx.categoria_id),
                        "subcategoria": subcat_map.get(tx.subcategoria_id),
                        "billetera": bill_map.get(tx.billetera_id),
                        "descripcion": tx.descripcion,
                    })
                movs.sort(key=lambda m: (m["fecha"], m["tipo"], m["monto"], m["descripcion"], m["billetera"] or "", m["categoria"] or "", m["subcategoria"] or ""))

                conv_nuevas = snap_c6.execute(
                    select(ConversacionWpp).where(
                        ConversacionWpp.usuario_id == u.id,
                        (ConversacionWpp.wamid.in_([wamid1, wamid2, p_wamid])) | (ConversacionWpp.id == pid)
                    )
                ).scalars().all()
                convs = []
                for c in conv_nuevas:
                    convs.append({
                        "intent": c.intent_detectado,
                        "accion_ejecutada": _normalizar_accion_ejecutada(c.accion_ejecutada),
                    })
                convs.sort(key=lambda c: (c["intent"] or "", c["accion_ejecutada"] or ""))
                _colector_salidas.registrar_salida_escenario("P5.6", movs, convs)
            finally:
                snap_c6.close()

        # Limpieza infalible: identificar exactamente las transacciones creadas por diferencia de conjuntos
        clean_db = SessionLocal()
        tx_ids_despues = set(clean_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        creadas = tx_ids_despues - tx_ids_antes
        for tx_id in creadas:
            clean_db.execute(text("DELETE FROM transacciones WHERE id = :txid"), {"txid": tx_id})

        # Limpieza de mensajes procesados y conversaciones creadas en esta prueba
        clean_db.execute(text("DELETE FROM mensajes_whatsapp_procesados WHERE wamid IN (:w1, :w2, :pw)"), {"w1": wamid1, "w2": wamid2, "pw": p_wamid})
        clean_db.execute(text("DELETE FROM conversaciones_wpp WHERE usuario_id = :uid AND (wamid IN (:w1, :w2, :pw) OR id = :pid)"), {"uid": u.id, "w1": wamid1, "w2": wamid2, "pw": p_wamid, "pid": pid})
        clean_db.execute(text("UPDATE billeteras SET saldo_actual = :s WHERE id = :bid"), {"s": saldo_original, "bid": b.id})
        clean_db.commit()

        # Verificación estricta post-limpieza
        tx_post = set(clean_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        b_actual = clean_db.execute(select(Billetera).where(Billetera.id == b.id)).scalar_one()
        clean_db.close()

        if tx_post != tx_ids_antes:
            sobrantes = tx_post - tx_ids_antes
            raise RuntimeError(f"Limpieza falló: transacciones residuales no borradas: {sobrantes}")
        if b_actual.saldo_actual != saldo_original:
            raise RuntimeError(f"Limpieza falló: saldo billetera {b_actual.saldo_actual} != original {saldo_original}")

    # Verificar que exactamente 1 confirmó con éxito y 1 fue rechazada por ya confirmada
    exitos = [r for r in resp_c6 if "registrado" in r.lower()]
    dups = [r for r in resp_c6 if "ya fue confirmada" in r.lower() or "no tenés ninguna operación pendiente" in r.lower()]
    return f"Exitos={len(exitos)}, Rechazados_por_concurrencia={len(dups)}"


def p5_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y otros 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)


def p5_caso_cuotas(datos):
    from app.models.transaccion import MetodoPago
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
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=5),
            es_cuota_hija=True,
            es_padre_cuotas=False,
        )
        db.add(tx_cuota)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)
