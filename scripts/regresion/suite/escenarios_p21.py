"""
scripts/regresion/suite/escenarios_p21.py

Escenarios del Punto 21: Flujo de facturas con vencimiento en WhatsApp (Fase 4c2b2b).
- P21.1: Foto de factura con vencimiento (hoy + 5) y respuesta "no" -> 1 factura pendiente, 0 movimientos.
- P21.2: Foto de factura con vencimiento (hoy + 5) y respuesta "sí" -> 1 movimiento, 0 facturas.
- P21.3: PDF de factura con 2 cuotas y respuesta "no" -> 2 facturas pendientes, 0 movimientos.
- P21.4: PDF de factura con 2 cuotas y respuesta "sí" -> 1 movimiento, 1 factura (2da cuota).
- P21.5: Foto de factura vencida (hoy - 3) y respuesta "no" -> propuesta "venció el" y "como vencida", 1 factura.
- P21.6: Foto P21.1, corrección a otra billetera (Santander) y "sí" -> gasto desde Santander, 0 facturas.
- P21.7: P21.1 dos veces con "no" -> segunda respuesta "Esa factura ya la tenía anotada", 1 factura total.
- P21.8: Foto de factura sin vencimiento y "no" -> propuesta con línea explicativa y cancelación habitual, 0 facturas.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import time
from unittest.mock import patch

from sqlalchemy import select, text

from app.models.billetera import Billetera
from app.models.factura import Factura
from app.models.subcategoria import Subcategoria
from app.models.transaccion import Transaccion
from app.routers.whatsapp.extraccion_documento import (
    CuotaExtraida,
    MovimientoExtraido,
    ResultadoExtraccion,
)
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from app.utils.fecha import hoy_argentina
from scripts.regresion.suite.comun import (
    TELEFONO_TEST,
    USUARIO_PRUEBAS_EMAIL,
    make_payload,
    make_payload_image,
    run_isolated,
)
from scripts.regresion.suite.escenarios_p20 import (
    _crear_pdf_texto,
    make_payload_document,
)


def _preparar_base_escenario(conn, uid):
    conn.execute(
        text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"),
        {"uid": uid},
    )
    conn.execute(
        text(
            "UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"
        ),
        {"uid": uid},
    )


def p21_caso_1(datos):
    """P21.1: Foto de factura 'Aguas Santafesinas', $12.345,67, Agua, vence hoy + 5, y 'no' -> 0 movimientos y 1 factura pendiente, whatsapp_foto."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())

        venc = hoy + timedelta(days=5)
        f_str = venc.strftime("%d/%m")

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("12345.67"),
                    moneda="ARS",
                    descripcion="Aguas Santafesinas",
                    sentido="egreso",
                    categoria="Agua",
                )
            ],
            billetera_texto=None,
            vencimiento=venc,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        esperado_resp1 = (
            f"Factura de Aguas Santafesinas por $12.345,67, vence el {f_str}.\n"
            f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
            f"Si fue con otra, decime cuál.\n"
            f"Si me decís que no, te la anoto en la web para que no se te pase."
        )
        prop_ok = resp1 == esperado_resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        esperado_resp2 = "Listo, te la anoto en la web. Te aviso 3 días antes y el día del vencimiento."
        resp_ok = resp2 == esperado_resp2

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        nuevas_facs = db.execute(select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes))).scalars().all()

        fac = nuevas_facs[0] if nuevas_facs else None
        subcat = db.get(Subcategoria, fac.subcategoria_id) if fac and fac.subcategoria_id else None
        db.close()

        movs_ok = len(nuevas_txs) == 0
        fac_ok = (
            len(nuevas_facs) == 1
            and fac is not None
            and fac.descripcion == "Aguas Santafesinas"
            and fac.monto == Decimal("12345.67")
            and fac.fecha_vencimiento == venc
            and fac.estado == "pendiente"
            and fac.origen == "whatsapp_foto"
            and subcat is not None
            and subcat.nombre == "Agua"
        )

        return f"Propuesta: {prop_ok} | Respuesta: {resp_ok} | Movimientos: {movs_ok} | Factura: {fac_ok}"

    return run_isolated(test)


def p21_caso_2(datos):
    """P21.2: Lo mismo con 'sí' -> 'Listo. $12.345,67 en Agua desde Galicia — registrado.', 1 movimiento de hoy y 0 facturas."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())

        venc = hoy + timedelta(days=5)
        f_str = venc.strftime("%d/%m")

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("12345.67"),
                    moneda="ARS",
                    descripcion="Aguas Santafesinas",
                    sentido="egreso",
                    categoria="Agua",
                )
            ],
            billetera_texto=None,
            vencimiento=venc,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        esperado_resp1 = (
            f"Factura de Aguas Santafesinas por $12.345,67, vence el {f_str}.\n"
            f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
            f"Si fue con otra, decime cuál.\n"
            f"Si me decís que no, te la anoto en la web para que no se te pase."
        )
        prop_ok = resp1 == esperado_resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        esperado_resp2 = "Listo. $12.345,67 en Agua desde Galicia — registrado."
        resp_ok = resp2 == esperado_resp2

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        nuevas_facs = db.execute(select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes))).scalars().all()

        tx = nuevas_txs[0] if nuevas_txs else None
        subcat = db.get(Subcategoria, tx.subcategoria_id) if tx and tx.subcategoria_id else None
        billetera = db.get(Billetera, tx.billetera_id) if tx and tx.billetera_id else None
        db.close()

        mov_ok = (
            len(nuevas_txs) == 1
            and tx is not None
            and tx.monto == Decimal("12345.67")
            and tx.fecha == hoy
            and subcat is not None
            and subcat.nombre == "Agua"
            and billetera is not None
            and billetera.nombre == "Galicia"
        )
        facs_ok = len(nuevas_facs) == 0

        return f"Propuesta: {prop_ok} | Respuesta: {resp_ok} | Movimiento: {mov_ok} | Facturas: {facs_ok}"

    return run_isolated(test)


def p21_caso_3(datos):
    """P21.3: PDF de EPE con 2 cuotas de $77.597,44 (hoy + 6 y hoy + 36) y 'no' -> 2 facturas pendientes, whatsapp_pdf, y 0 movimientos."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())

        venc1 = hoy + timedelta(days=6)
        venc2 = hoy + timedelta(days=36)
        f1_str = venc1.strftime("%d/%m")
        f2_str = venc2.strftime("%d/%m")

        lineas = [
            "Empresa Provincial de la Energia de Santa Fe EPE",
            f"Factura de Servicio Electrico Cuota 1 {venc1.strftime('%d/%m/%Y')} $*77.597,44",
            f"Cuota 2 {venc2.strftime('%d/%m/%Y')} $*77.597,44 Total a pagar Periodo 10/2026",
        ]
        pdf_bytes = _crear_pdf_texto(lineas)

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("77597.44"),
                    moneda="ARS",
                    descripcion="EPE",
                    sentido="egreso",
                    categoria="Luz",
                )
            ],
            billetera_texto=None,
            vencimiento=venc1,
            cuotas=[
                CuotaExtraida(vencimiento=venc1, monto=Decimal("77597.44")),
                CuotaExtraida(vencimiento=venc2, monto=Decimal("77597.44")),
            ],
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.media.descargar_documento_meta", return_value=(pdf_bytes, "application/pdf", None)), \
             patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_texto_pdf", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_document(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        esperado_resp1 = (
            f"Factura de EPE en 2 cuotas de $77.597,44: vencen el {f1_str} y el {f2_str}.\n"
            f"¿Ya pagaste la primera? Si me decís que sí, la anoto como gasto de hoy en Luz desde Galicia y te anoto la segunda en la web.\n"
            f"Si fue con otra, decime cuál.\n"
            f"Si me decís que no, te anoto las dos en la web para que no se te pasen."
        )
        prop_ok = resp1 == esperado_resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        esperado_resp2 = "Listo, te anoto las 2 cuotas en la web. Te aviso 3 días antes y el día de cada vencimiento."
        resp_ok = resp2 == esperado_resp2

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        nuevas_facs = db.execute(select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes)).order_by(Factura.fecha_vencimiento.asc())).scalars().all()

        movs_ok = len(nuevas_txs) == 0
        facs_ok = False
        if len(nuevas_facs) == 2:
            f1, f2 = nuevas_facs
            subcat1 = db.get(Subcategoria, f1.subcategoria_id) if f1.subcategoria_id else None
            subcat2 = db.get(Subcategoria, f2.subcategoria_id) if f2.subcategoria_id else None
            facs_ok = (
                f1.descripcion == "EPE"
                and f1.monto == Decimal("77597.44")
                and f1.fecha_vencimiento == venc1
                and f1.estado == "pendiente"
                and f1.origen == "whatsapp_pdf"
                and subcat1 is not None and subcat1.nombre == "Luz"
                and f2.descripcion == "EPE"
                and f2.monto == Decimal("77597.44")
                and f2.fecha_vencimiento == venc2
                and f2.estado == "pendiente"
                and f2.origen == "whatsapp_pdf"
                and subcat2 is not None and subcat2.nombre == "Luz"
            )
        db.close()

        return f"Propuesta: {prop_ok} | Respuesta: {resp_ok} | Movimientos: {movs_ok} | Facturas: {facs_ok}"

    return run_isolated(test)


def p21_caso_4(datos):
    """P21.4: Lo mismo con 'sí' -> el 'Listo' con la línea de la segunda cuota, 1 movimiento de 77.597,44 y 1 factura (hoy + 36)."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())

        venc1 = hoy + timedelta(days=6)
        venc2 = hoy + timedelta(days=36)
        f1_str = venc1.strftime("%d/%m")
        f2_str = venc2.strftime("%d/%m")

        lineas = [
            "Empresa Provincial de la Energia de Santa Fe EPE",
            f"Factura de Servicio Electrico Cuota 1 {venc1.strftime('%d/%m/%Y')} $*77.597,44",
            f"Cuota 2 {venc2.strftime('%d/%m/%Y')} $*77.597,44 Total a pagar Periodo 10/2026",
        ]
        pdf_bytes = _crear_pdf_texto(lineas)

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("77597.44"),
                    moneda="ARS",
                    descripcion="EPE",
                    sentido="egreso",
                    categoria="Luz",
                )
            ],
            billetera_texto=None,
            vencimiento=venc1,
            cuotas=[
                CuotaExtraida(vencimiento=venc1, monto=Decimal("77597.44")),
                CuotaExtraida(vencimiento=venc2, monto=Decimal("77597.44")),
            ],
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.media.descargar_documento_meta", return_value=(pdf_bytes, "application/pdf", None)), \
             patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_texto_pdf", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_document(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        esperado_resp1 = (
            f"Factura de EPE en 2 cuotas de $77.597,44: vencen el {f1_str} y el {f2_str}.\n"
            f"¿Ya pagaste la primera? Si me decís que sí, la anoto como gasto de hoy en Luz desde Galicia y te anoto la segunda en la web.\n"
            f"Si fue con otra, decime cuál.\n"
            f"Si me decís que no, te anoto las dos en la web para que no se te pasen."
        )
        prop_ok = resp1 == esperado_resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        esperado_resp2 = (
            f"Listo. $77.597,44 en Luz desde Galicia — registrado.\n"
            f"Te anoté la segunda cuota ($77.597,44, vence el {f2_str}) en la web."
        )
        resp_ok = resp2 == esperado_resp2

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        nuevas_facs = db.execute(select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes))).scalars().all()

        tx = nuevas_txs[0] if nuevas_txs else None
        subcat_tx = db.get(Subcategoria, tx.subcategoria_id) if tx and tx.subcategoria_id else None
        mov_ok = (
            len(nuevas_txs) == 1
            and tx is not None
            and tx.monto == Decimal("77597.44")
            and tx.fecha == hoy
            and subcat_tx is not None and subcat_tx.nombre == "Luz"
        )

        fac = nuevas_facs[0] if nuevas_facs else None
        subcat_fac = db.get(Subcategoria, fac.subcategoria_id) if fac and fac.subcategoria_id else None
        fac_ok = (
            len(nuevas_facs) == 1
            and fac is not None
            and fac.descripcion == "EPE"
            and fac.monto == Decimal("77597.44")
            and fac.fecha_vencimiento == venc2
            and fac.estado == "pendiente"
            and fac.origen == "whatsapp_pdf"
            and subcat_fac is not None and subcat_fac.nombre == "Luz"
        )
        db.close()

        return f"Propuesta: {prop_ok} | Respuesta: {resp_ok} | Movimiento: {mov_ok} | Factura: {fac_ok}"

    return run_isolated(test)


def p21_caso_5(datos):
    """P21.5: Foto de factura vencida (hoy - 3) y 'no' -> propuesta con 'venció el' y respuesta 'como vencida'. 1 factura."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())

        venc = hoy - timedelta(days=3)
        f_str = venc.strftime("%d/%m")

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("12345.67"),
                    moneda="ARS",
                    descripcion="Aguas Santafesinas",
                    sentido="egreso",
                    categoria="Agua",
                )
            ],
            billetera_texto=None,
            vencimiento=venc,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        esperado_resp1 = (
            f"Factura de Aguas Santafesinas por $12.345,67, venció el {f_str}.\n"
            f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
            f"Si fue con otra, decime cuál.\n"
            f"Si me decís que no, te la anoto en la web para que no se te pase."
        )
        prop_ok = resp1 == esperado_resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        esperado_resp2 = "Listo, te la anoto en la web como vencida."
        resp_ok = resp2 == esperado_resp2

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        nuevas_facs = db.execute(select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes))).scalars().all()

        fac = nuevas_facs[0] if nuevas_facs else None
        db.close()

        movs_ok = len(nuevas_txs) == 0
        fac_ok = (
            len(nuevas_facs) == 1
            and fac is not None
            and fac.descripcion == "Aguas Santafesinas"
            and fac.monto == Decimal("12345.67")
            and fac.fecha_vencimiento == venc
            and fac.estado == "pendiente"
        )

        return f"Propuesta: {prop_ok} | Respuesta: {resp_ok} | Movimientos: {movs_ok} | Factura: {fac_ok}"

    return run_isolated(test)


def p21_caso_6(datos):
    """P21.6: P21.1, después el nombre de otra billetera (Santander), y 'sí' -> propuesta corregida mantiene texto de factura sin 'Si fue con otra', gasto desde esa billetera."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())

        venc = hoy + timedelta(days=5)
        f_str = venc.strftime("%d/%m")

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("12345.67"),
                    moneda="ARS",
                    descripcion="Aguas Santafesinas",
                    sentido="egreso",
                    categoria="Agua",
                )
            ],
            billetera_texto=None,
            vencimiento=venc,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        esperado_resp1 = (
            f"Factura de Aguas Santafesinas por $12.345,67, vence el {f_str}.\n"
            f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
            f"Si fue con otra, decime cuál.\n"
            f"Si me decís que no, te la anoto en la web para que no se te pase."
        )
        prop1_ok = resp1 == esperado_resp1

        # Corrección de billetera a Santander (testingadmin no tiene Mercado Pago por defecto)
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "Santander"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        esperado_resp2 = (
            f"Factura de Aguas Santafesinas por $12.345,67, vence el {f_str}.\n"
            f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Santander.\n"
            f"Si me decís que no, te la anoto en la web para que no se te pase."
        )
        prop2_ok = resp2 == esperado_resp2

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp3 = respuestas[-1][1] if respuestas else ""
        esperado_resp3 = "Listo. $12.345,67 en Agua desde Santander — registrado."
        resp3_ok = resp3 == esperado_resp3

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        nuevas_facs = db.execute(select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes))).scalars().all()

        tx = nuevas_txs[0] if nuevas_txs else None
        billetera = db.get(Billetera, tx.billetera_id) if tx and tx.billetera_id else None
        db.close()

        mov_ok = (
            len(nuevas_txs) == 1
            and tx is not None
            and tx.monto == Decimal("12345.67")
            and tx.fecha == hoy
            and billetera is not None
            and billetera.nombre == "Santander"
        )
        facs_ok = len(nuevas_facs) == 0

        return f"Propuesta1: {prop1_ok} | Propuesta2: {prop2_ok} | Registrado: {resp3_ok} | Movimiento: {mov_ok} | Facturas: {facs_ok}"

    return run_isolated(test)


def p21_caso_7(datos):
    """P21.7: P21.1 dos veces con 'no' -> la segunda respuesta es 'Esa factura ya la tenía anotada.' y hay 1 factura."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())

        venc = hoy + timedelta(days=5)

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("12345.67"),
                    moneda="ARS",
                    descripcion="Aguas Santafesinas",
                    sentido="egreso",
                    categoria="Agua",
                )
            ],
            billetera_texto=None,
            vencimiento=venc,
            total_vistos=1,
        )

        # Primera vez
        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""
        resp1_ok = resp1 == "Listo, te la anoto en la web. Te aviso 3 días antes y el día del vencimiento."

        # Segunda vez: misma factura
        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        resp2_ok = resp2 == "Esa factura ya la tenía anotada."

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        nuevas_facs = db.execute(select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes))).scalars().all()
        db.close()

        movs_ok = len(nuevas_txs) == 0
        facs_ok = len(nuevas_facs) == 1

        return f"PrimeraResp: {resp1_ok} | SegundaResp: {resp2_ok} | Movimientos: {movs_ok} | Facturas: {facs_ok}"

    return run_isolated(test)


def p21_caso_8(datos):
    """P21.8: Foto de factura sin vencimiento y 'no' -> todo igual que hoy (línea 'Si todavía no la pagaste...' y cancelación). 0 facturas."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("12345.67"),
                    moneda="ARS",
                    descripcion="Aguas Santafesinas",
                    sentido="egreso",
                    categoria="Agua",
                )
            ],
            billetera_texto=None,
            vencimiento=None,
            cuotas=[],
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        esperado_resp1 = (
            "Voy a anotar $12.345,67 en Agua desde Galicia. ¿Va?\n"
            "Si fue con otra, decime cuál.\n"
            "Si todavía no la pagaste, respondé no y no la cargo."
        )
        prop_ok = resp1 == esperado_resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        cancel_ok = resp2 == "Listo, cancelado."

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        nuevas_facs = db.execute(select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes))).scalars().all()
        db.close()

        movs_ok = len(nuevas_txs) == 0
        facs_ok = len(nuevas_facs) == 0

        return f"Propuesta: {prop_ok} | Cancelado: {cancel_ok} | Movimientos: {movs_ok} | Facturas: {facs_ok}"

    return run_isolated(test)


def entradas_p21(datos) -> list[dict]:
    """Retorna las entradas de catálogo para los escenarios P21.1 a P21.8."""
    return [
        {
            "id": "P21.1", "punto": "Punto 21", "match": "exacto",
            "nombre": "Foto de factura con vencimiento y no crea factura pendiente",
            "ejecutar": lambda: p21_caso_1(datos),
            "esperado": "Propuesta: True | Respuesta: True | Movimientos: True | Factura: True",
        },
        {
            "id": "P21.2", "punto": "Punto 21", "match": "exacto",
            "nombre": "Foto de factura con vencimiento y sí registra movimiento sin factura",
            "ejecutar": lambda: p21_caso_2(datos),
            "esperado": "Propuesta: True | Respuesta: True | Movimiento: True | Facturas: True",
        },
        {
            "id": "P21.3", "punto": "Punto 21", "match": "exacto",
            "nombre": "PDF de EPE en 2 cuotas y no crea 2 facturas pendientes",
            "ejecutar": lambda: p21_caso_3(datos),
            "esperado": "Propuesta: True | Respuesta: True | Movimientos: True | Facturas: True",
        },
        {
            "id": "P21.4", "punto": "Punto 21", "match": "exacto",
            "nombre": "PDF de EPE en 2 cuotas y sí registra 1er cuota y anota 2da en web",
            "ejecutar": lambda: p21_caso_4(datos),
            "esperado": "Propuesta: True | Respuesta: True | Movimiento: True | Factura: True",
        },
        {
            "id": "P21.5", "punto": "Punto 21", "match": "exacto",
            "nombre": "Foto de factura vencida y no anota como vencida",
            "ejecutar": lambda: p21_caso_5(datos),
            "esperado": "Propuesta: True | Respuesta: True | Movimientos: True | Factura: True",
        },
        {
            "id": "P21.6", "punto": "Punto 21", "match": "exacto",
            "nombre": "Foto de factura con corrección de billetera a Santander y sí",
            "ejecutar": lambda: p21_caso_6(datos),
            "esperado": "Propuesta1: True | Propuesta2: True | Registrado: True | Movimiento: True | Facturas: True",
        },
        {
            "id": "P21.7", "punto": "Punto 21", "match": "exacto",
            "nombre": "Foto de factura dos veces con no detecta duplicado",
            "ejecutar": lambda: p21_caso_7(datos),
            "esperado": "PrimeraResp: True | SegundaResp: True | Movimientos: True | Facturas: True",
        },
        {
            "id": "P21.8", "punto": "Punto 21", "match": "exacto",
            "nombre": "Foto de factura sin vencimiento y no cancela habitualmente",
            "ejecutar": lambda: p21_caso_8(datos),
            "esperado": "Propuesta: True | Cancelado: True | Movimientos: True | Facturas: True",
        },
    ]
