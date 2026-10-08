"""
scripts/regresion/suite/escenarios_p19.py

Escenarios del Punto 19: Extracción estructurada y confirmación de movimientos
a partir de imágenes enviadas por WhatsApp (fase4c1).
Todos los escenarios usan extracciones simuladas (sin llamadas a OpenAI ni Meta).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import time
from unittest.mock import patch

from sqlalchemy import func, select, text

from app.models.billetera import Billetera
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda
from app.routers.whatsapp.extraccion_documento import (
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


def p19_caso_1(datos):
    """P19.1: ticket único, propuesta con billetera nombrada, 'sí' y un solo movimiento registrado."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        from sqlalchemy import func
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="ticket_compra",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("4500"),
                    moneda="ARS",
                    descripcion="Farmacia Central",
                    sentido="egreso",
                    categoria="Farmacia",
                )
            ],
            billetera_texto="Galicia",
            vencimiento=None,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        propuesta_ok = "Galicia" in resp1 and "¿Va?" in resp1 and "$4.500" in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        registrado_ok = "Listo" in resp2 and "Galicia" in resp2

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Propuesta billetera nombrada: {propuesta_ok} | Registrado tras sí: {registrado_ok} | Total txs: {txs_creadas}"

    return run_isolated(test)


def p19_caso_2(datos):
    """P19.2: captura con 3 movimientos, propuesta de lote y 'sí' con 3 registrados."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Panadería Artesanal",
                    sentido="egreso",
                    categoria="Supermercado",
                ),
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("3500"),
                    moneda="ARS",
                    descripcion="Verdulería Don Pepe",
                    sentido="egreso",
                    categoria="Verdulería",
                ),
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("1200"),
                    moneda="ARS",
                    descripcion="Kiosco de la Esquina",
                    sentido="egreso",
                    categoria="Kiosco",
                ),
            ],
            billetera_texto="Galicia",
            vencimiento=None,
            total_vistos=3,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        propuesta_ok = "3 movimientos" in resp1 and "¿Va?" in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        registrado_ok = "Listo, 3" in resp2 and "Galicia" in resp2

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Propuesta lote 3: {propuesta_ok} | Registrados tras sí: {registrado_ok} | Total txs: {txs_creadas}"

    return run_isolated(test)


def p19_caso_3(datos):
    """P19.3: captura con 3 movimientos donde 1 ya existe, propuesta de 2 y línea 'Ya tenías cargado'."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()

        bill_galicia = db.execute(
            select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")
        ).scalars().first()

        # Crear transacción previa idéntica
        tx_existente = Transaccion(
            usuario_id=u.id,
            monto=Decimal("2000"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Panadería Artesanal",
            billetera_id=bill_galicia.id if bill_galicia else None,
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        db.add(tx_existente)
        db.commit()

        txs_con_previa = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Panadería Artesanal",
                    sentido="egreso",
                    categoria="Supermercado",
                ),
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("3500"),
                    moneda="ARS",
                    descripcion="Verdulería Don Pepe",
                    sentido="egreso",
                    categoria="Verdulería",
                ),
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("1200"),
                    moneda="ARS",
                    descripcion="Kiosco de la Esquina",
                    sentido="egreso",
                    categoria="Kiosco",
                ),
            ],
            billetera_texto="Galicia",
            vencimiento=None,
            total_vistos=3,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        propuesta_2_ok = "2 movimientos" in resp1 and "¿Va?" in resp1
        linea_dup_ok = (
            "Ya tenías cargado: Panadería Artesanal (hoy, $2.000)." in resp1
            and "Si es otro movimiento igual, mandámelo escrito." in resp1
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        registrados_ok = "Listo, 2" in resp2

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_con_previa

        return f"Propuesta 2 movs: {propuesta_2_ok} | Linea ya tenias cargado: {linea_dup_ok} | Registrados tras sí: {registrados_ok} | Total txs: {txs_creadas == 2}"

    return run_isolated(test)


def p19_caso_4(datos):
    """P19.4: todos duplicados, sin propuesta confirmable pendiente."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()

        bill_galicia = db.execute(
            select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")
        ).scalars().first()

        tx1 = Transaccion(
            usuario_id=u.id,
            monto=Decimal("2000"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Panadería Artesanal",
            billetera_id=bill_galicia.id if bill_galicia else None,
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        tx2 = Transaccion(
            usuario_id=u.id,
            monto=Decimal("3500"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Verdulería Don Pepe",
            billetera_id=bill_galicia.id if bill_galicia else None,
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        db.add_all([tx1, tx2])
        db.commit()

        txs_con_previas = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Panadería Artesanal",
                    sentido="egreso",
                    categoria="Supermercado",
                ),
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("3500"),
                    moneda="ARS",
                    descripcion="Verdulería Don Pepe",
                    sentido="egreso",
                    categoria="Verdulería",
                ),
            ],
            billetera_texto="Galicia",
            vencimiento=None,
            total_vistos=2,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        resp_dup_ok = resp1 == "Ya tenías cargado todo lo que veo en la imagen."

        # El usuario responde 'sí' pero no hay propuesta pendiente
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        sin_pendientes_ok = "No tenés ninguna" in resp2 or "no entendí" in resp2.lower()

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_con_previas

        return f"Respuesta todos duplicados: {resp_dup_ok} | Sin propuesta pendiente tras sí: {sin_pendientes_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def p19_caso_5(datos):
    """P19.5: 12 movimientos, propuesta de 10 con aviso."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)

        movs_10 = [
            MovimientoExtraido(
                fecha=hoy,
                monto=Decimal(f"{i * 100}"),
                moneda="ARS",
                descripcion=f"Item {i}",
                sentido="egreso",
                categoria="Otros",
            )
            for i in range(1, 11)
        ]

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=movs_10,
            billetera_texto="Galicia",
            vencimiento=None,
            total_vistos=12,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        propuesta_10_ok = "10 movimientos" in resp1 and "¿Va?" in resp1
        aviso_tope_ok = "Vi 12 movimientos y cargo los primeros 10. Mandame otra captura con el resto." in resp1

        return f"Propuesta 10 movs: {propuesta_10_ok} | Aviso tope 12: {aviso_tope_ok}"

    return run_isolated(test)


def p19_caso_6(datos):
    """P19.6: imagen ilegible, texto 'No pude leer el comprobante. Mandame los datos en texto.' y nada pendiente."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(None, "ILEGIBLE")):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        texto_ilegible_ok = resp1 == "No pude leer el comprobante. Mandame los datos en texto."

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        nada_pendiente_ok = "No tenés ninguna" in resp2 or "no entendí" in resp2.lower()

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Texto ilegible: {texto_ilegible_ok} | Nada pendiente tras sí: {nada_pendiente_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def p19_caso_7(datos):
    """P19.7: factura_servicio, propuesta de gasto con la línea 'Si todavía no la pagaste, respondé no y no la cargo.'"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        from app.models.factura import Factura
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())
        txs_antes = len(tx_ids_antes)

        venc = hoy + timedelta(days=10)
        f_str = venc.strftime("%d/%m")

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("14200"),
                    moneda="ARS",
                    descripcion="Edesur",
                    sentido="egreso",
                    categoria="Servicios",
                )
            ],
            billetera_texto="Galicia",
            vencimiento=venc,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        esperado_resp1 = (
            f"Factura de Edesur por $14.200, vence el {f_str}.\n"
            "¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Servicios desde Galicia.\n"
            "Si fue con otra, decime cuál.\n"
            "Si me decís que no, te la anoto en la web para que no se te pase."
        )
        linea_factura_ok = resp1 == esperado_resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        esperado_resp2 = "Listo. $14.200 en Otros desde Galicia — registrado."

        fac_ids_desp = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())
        facs_creadas = len(fac_ids_desp - fac_ids_antes)

        registrado_ok = resp2 == esperado_resp2 and facs_creadas == 0

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Linea si no la pagaste: {linea_factura_ok} | Registrado tras sí: {registrado_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def p19_caso_8(datos):
    """P19.8: 'no' tras la propuesta no registra nada."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="ticket_compra",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("3000"),
                    moneda="ARS",
                    descripcion="Farmacia San José",
                    sentido="egreso",
                    categoria="Farmacia",
                )
            ],
            billetera_texto="Galicia",
            vencimiento=None,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp_no = respuestas[-1][1] if respuestas else ""
        resp_cancel_ok = "cancelado" in resp_no.lower() or "listo" in resp_no.lower()

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Respuesta cancelado tras no: {resp_cancel_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def p19_caso_9(datos):
    """P19.9: comprobante_transferencia con billetera_texto 'Santander' ignora la billetera del documento, asume principal Galicia, y 'sí' registra 1 movimiento."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="comprobante_transferencia",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("15000"),
                    moneda="ARS",
                    descripcion="Transferencia Recibida",
                    sentido="ingreso",
                    categoria="Otros Ingresos",
                )
            ],
            billetera_texto="Santander",
            vencimiento=None,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        propuesta_ok = "Galicia" in resp1 and "Santander" not in resp1 and "¿Va?" in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        registrado_ok = "Listo" in resp2 and "Galicia" in resp2

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Propuesta Galicia sin Santander con va: {propuesta_ok} | Registrado tras sí: {registrado_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)
