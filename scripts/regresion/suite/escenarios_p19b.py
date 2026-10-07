"""
scripts/regresion/suite/escenarios_p19b.py

Escenarios del Punto 19 (parte B: P19.10 a P19.21): Extracción estructurada y confirmación de movimientos
a partir de imágenes enviadas por WhatsApp (fase4c1).
Todos los escenarios usan extracciones simuladas (sin llamadas a OpenAI ni Meta).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time
from unittest.mock import patch

from sqlalchemy import func, select, text

from app.models.billetera import Billetera
from app.models.rendimiento_billetera import RendimientoBilletera
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
from scripts.regresion.suite.escenarios_p19 import _preparar_base_escenario


def p19_caso_10(datos):
    """P19.10: captura con 2 gastos y 1 rendimiento (billetera_texto null, y testingadmin tiene UNA billetera que rinde).
    La propuesta trae la línea 'Además anoto el rendimiento...' y tras 'sí' se crean 2 transacciones y 1 fila nueva
    en rendimientos_billetera, sin transacción extra y con el saldo de esa billetera subiendo exactamente el monto."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        # Buscar la única billetera que rinde de testingadmin
        b_inv_id = conn.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == u.id,
                Billetera.es_inversion == True,
            )
        ).scalar()
        conn.execute(
            text("UPDATE billeteras SET tna = NULL WHERE usuario_id = :uid AND id != :bid"),
            {"uid": u.id, "bid": b_inv_id},
        )
        conn.execute(
            text("UPDATE billeteras SET fecha_ultimo_rendimiento = :fur WHERE id = :bid"),
            {"fur": datetime.now(timezone.utc) - timedelta(days=10), "bid": b_inv_id},
        )

        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_antes = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_inv_id)).scalar()
        saldo_antes = conn.execute(select(Billetera.saldo_actual).where(Billetera.id == b_inv_id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Kiosco San José",
                    sentido="egreso",
                    categoria="Kiosco",
                ),
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("3500"),
                    moneda="ARS",
                    descripcion="Farmacia Central",
                    sentido="egreso",
                    categoria="Farmacia",
                ),
            ],
            billetera_texto=None,
            vencimiento=None,
            total_vistos=2,
            rendimientos=[
                MovimientoExtraido(
                    fecha=ayer,
                    monto=Decimal("500.00"),
                    moneda="ARS",
                    descripcion="Rendimientos",
                    sentido="rendimiento",
                    categoria=None,
                )
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        propuesta_ok = (
            "Además anoto el rendimiento de $500 en Ahorro con rendimiento" in resp1
            and "no cuenta como ingreso" in resp1
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        registrado_ok = "Listo" in resp2 and "Rendimiento de $500 anotado en Ahorro con rendimiento" in resp2

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_despues = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_inv_id)).scalar()
        saldo_despues = conn.execute(select(Billetera.saldo_actual).where(Billetera.id == b_inv_id)).scalar()

        txs_creadas = txs_despues - txs_antes
        rends_creados = rends_despues - rends_antes
        saldo_subio = (saldo_despues - saldo_antes) == Decimal("500.00")

        return f"Propuesta rendimiento: {propuesta_ok} | Registrado tras sí: {registrado_ok} | Txs creadas: {txs_creadas} | Rends creados: {rends_creados} | Saldo subio: {saldo_subio}"

    return run_isolated(test)


def p19_caso_11(datos):
    """P19.11: el rendimiento de esa fecha ya existe (crearlo antes con el servicio):
    línea 'Ya tenías el rendimiento...' y tras 'sí' no se crea otra fila."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        b_inv_id = conn.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == u.id,
                Billetera.es_inversion == True,
            )
        ).scalar()
        conn.execute(
            text("UPDATE billeteras SET tna = NULL WHERE usuario_id = :uid AND id != :bid"),
            {"uid": u.id, "bid": b_inv_id},
        )

        # Crear el rendimiento existente previo para ayer
        with Session() as sess:
            import app.services.rendimiento_billetera_service as rbs_service
            rbs_service.confirmar_rendimiento(
                sess,
                u.id,
                b_inv_id,
                monto=Decimal("500.00"),
                fecha=datetime(ayer.year, ayer.month, ayer.day, 12, 0, 0, tzinfo=timezone.utc),
                commit=True,
            )

        conn.execute(
            text("UPDATE billeteras SET fecha_ultimo_rendimiento = :fur WHERE id = :bid"),
            {"fur": datetime.now(timezone.utc) - timedelta(days=10), "bid": b_inv_id},
        )

        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_antes = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_inv_id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Kiosco San José",
                    sentido="egreso",
                    categoria="Kiosco",
                ),
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("3500"),
                    moneda="ARS",
                    descripcion="Farmacia Central",
                    sentido="egreso",
                    categoria="Farmacia",
                ),
            ],
            billetera_texto=None,
            vencimiento=None,
            total_vistos=2,
            rendimientos=[
                MovimientoExtraido(
                    fecha=ayer,
                    monto=Decimal("500.00"),
                    moneda="ARS",
                    descripcion="Rendimientos",
                    sentido="rendimiento",
                    categoria=None,
                )
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        linea_ya_tenias_ok = "Ya tenías el rendimiento de" in resp1 or "Ya tenías el rendimiento del" in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_despues = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_inv_id)).scalar()

        txs_creadas = txs_despues - txs_antes
        rends_creados = rends_despues - rends_antes

        return f"Linea ya tenias: {linea_ya_tenias_ok} | Txs creadas: {txs_creadas} | Rends creados: {rends_creados}"

    return run_isolated(test)


def p19_caso_12(datos):
    """P19.12: captura con solo rendimientos: mensaje fijo y nada confirmable."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_antes = conn.execute(select(func.count(RendimientoBilletera.id))).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[],
            billetera_texto=None,
            vencimiento=None,
            total_vistos=0,
            rendimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("500.00"),
                    moneda="ARS",
                    descripcion="Rendimientos",
                    sentido="rendimiento",
                    categoria=None,
                )
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        msg_ok = resp1 == "Veo solo rendimientos. Los cargás desde Billeteras o mandame los gastos o ingresos que quieras anotar."

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        nada_conf_ok = "No tenés ninguna operación pendiente para confirmar." in resp2

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_despues = conn.execute(select(func.count(RendimientoBilletera.id))).scalar()

        txs_creadas = txs_despues - txs_antes
        rends_creados = rends_despues - rends_antes

        return f"Mensaje solo rendimientos: {msg_ok} | Nada confirmable tras sí: {nada_conf_ok} | Txs creadas: {txs_creadas} | Rends creados: {rends_creados}"

    return run_isolated(test)


def p19_caso_13(datos):
    """P19.13: rendimiento con billetera_texto que resuelve a una billetera que no rinde (Galicia): línea de 'no pude anotar'."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_antes = conn.execute(select(func.count(RendimientoBilletera.id))).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Kiosco San José",
                    sentido="egreso",
                    categoria="Kiosco",
                )
            ],
            billetera_texto="Galicia",
            vencimiento=None,
            total_vistos=1,
            rendimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("500.00"),
                    moneda="ARS",
                    descripcion="Rendimientos",
                    sentido="rendimiento",
                    categoria=None,
                )
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        linea_no_pude_ok = "Vi un rendimiento de $500 que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras." in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_despues = conn.execute(select(func.count(RendimientoBilletera.id))).scalar()

        txs_creadas = txs_despues - txs_antes
        rends_creados = rends_despues - rends_antes

        return f"Linea no pude anotar: {linea_no_pude_ok} | Txs creadas: {txs_creadas} | Rends creados: {rends_creados}"

    return run_isolated(test)


def p19_caso_14(datos):
    """P19.14: 'no' tras la propuesta mixta: 0 transacciones y 0 rendimientos nuevos."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        b_inv_id = conn.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == u.id,
                Billetera.nombre == "Ahorro con rendimiento",
            )
        ).scalar_one()

        conn.execute(
            text("UPDATE billeteras SET tna = NULL WHERE usuario_id = :uid AND id != :bid"),
            {"uid": u.id, "bid": b_inv_id},
        )
        conn.execute(
            text("UPDATE billeteras SET fecha_ultimo_rendimiento = :fur WHERE id = :bid"),
            {"fur": datetime.now(timezone.utc) - timedelta(days=10), "bid": b_inv_id},
        )

        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_antes = conn.execute(select(func.count(RendimientoBilletera.id))).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Kiosco San José",
                    sentido="egreso",
                    categoria="Kiosco",
                ),
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("3500"),
                    moneda="ARS",
                    descripcion="Farmacia Central",
                    sentido="egreso",
                    categoria="Farmacia",
                ),
            ],
            billetera_texto=None,
            vencimiento=None,
            total_vistos=2,
            rendimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("500.00"),
                    moneda="ARS",
                    descripcion="Rendimientos",
                    sentido="rendimiento",
                    categoria=None,
                )
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp_no = respuestas[-1][1] if respuestas else ""
        cancelado_ok = "cancelado" in resp_no.lower() or "listo" in resp_no.lower()

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_despues = conn.execute(select(func.count(RendimientoBilletera.id))).scalar()

        txs_creadas = txs_despues - txs_antes
        rends_creados = rends_despues - rends_antes

        return f"Cancelado tras no: {cancelado_ok} | Txs creadas: {txs_creadas} | Rends creados: {rends_creados}"

    return run_isolated(test)


def p19_caso_15(datos):
    """P19.15: el rendimiento del documento tiene fecha menor o igual al ancla de la billetera:
    no se crea ninguna fila y el texto trae el aviso 'Ya tenías cargados los rendimientos de ...'."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        b_inv_id = conn.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == u.id,
                Billetera.es_inversion == True,
            )
        ).scalar()
        conn.execute(
            text("UPDATE billeteras SET tna = NULL WHERE usuario_id = :uid AND id != :bid"),
            {"uid": u.id, "bid": b_inv_id},
        )
        # Fijar ancla en ayer
        conn.execute(
            text("UPDATE billeteras SET fecha_ultimo_rendimiento = :fur WHERE id = :bid"),
            {"fur": datetime(ayer.year, ayer.month, ayer.day, 12, 0, 0, tzinfo=timezone.utc), "bid": b_inv_id},
        )

        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_antes = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_inv_id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Kiosco San José",
                    sentido="egreso",
                    categoria="Kiosco",
                )
            ],
            billetera_texto=None,
            vencimiento=None,
            total_vistos=1,
            rendimientos=[
                MovimientoExtraido(
                    fecha=ayer,
                    monto=Decimal("500.00"),
                    moneda="ARS",
                    descripcion="Rendimientos",
                    sentido="rendimiento",
                    categoria=None,
                )
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        aviso_cobertura_ok = "Ya tenías cargados los rendimientos de Ahorro con rendimiento hasta ayer." in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_despues = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_inv_id)).scalar()

        txs_creadas = txs_despues - txs_antes
        rends_creados = rends_despues - rends_antes

        return f"Aviso cobertura: {aviso_cobertura_ok} | Txs creadas: {txs_creadas} | Rends creados: {rends_creados}"

    return run_isolated(test)


def p19_caso_16(datos):
    """P19.16: rendimiento de hace 70 días: aviso de 'no pude anotar' y ninguna fila."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()
    fecha_70 = hoy - timedelta(days=70)

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        b_inv_id = conn.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == u.id,
                Billetera.es_inversion == True,
            )
        ).scalar()
        conn.execute(
            text("UPDATE billeteras SET tna = NULL WHERE usuario_id = :uid AND id != :bid"),
            {"uid": u.id, "bid": b_inv_id},
        )
        conn.execute(
            text("UPDATE billeteras SET fecha_ultimo_rendimiento = :fur WHERE id = :bid"),
            {"fur": datetime.now(timezone.utc) - timedelta(days=80), "bid": b_inv_id},
        )

        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_antes = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_inv_id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Kiosco San José",
                    sentido="egreso",
                    categoria="Kiosco",
                )
            ],
            billetera_texto=None,
            vencimiento=None,
            total_vistos=1,
            rendimientos=[
                MovimientoExtraido(
                    fecha=fecha_70,
                    monto=Decimal("500.00"),
                    moneda="ARS",
                    descripcion="Rendimientos",
                    sentido="rendimiento",
                    categoria=None,
                )
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        aviso_no_pude_ok = "Vi un rendimiento de $500 que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras." in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_despues = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_inv_id)).scalar()

        txs_creadas = txs_despues - txs_antes
        rends_creados = rends_despues - rends_antes

        return f"Aviso fecha vieja: {aviso_no_pude_ok} | Txs creadas: {txs_creadas} | Rends creados: {rends_creados}"

    return run_isolated(test)


def p19_caso_17(datos):
    """P19.17: una billetera con tna y es_inversion=false como única billetera que rinde:
    el rendimiento se anota en esa billetera tras 'sí'."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        # Desactivar inversión y tna en todas las billeteras
        conn.execute(
            text("UPDATE billeteras SET es_inversion = false, tna = NULL WHERE usuario_id = :uid"),
            {"uid": u.id},
        )
        # Dejar Galicia con tna=35.00 y es_inversion=false
        b_galicia_id = conn.execute(
            select(Billetera.id).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")
        ).scalar()
        conn.execute(
            text("UPDATE billeteras SET tna = 35.00, es_inversion = false, fecha_ultimo_rendimiento = :fur WHERE id = :bid"),
            {"bid": b_galicia_id, "fur": datetime.now(timezone.utc) - timedelta(days=10)},
        )

        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_antes = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_galicia_id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("2000"),
                    moneda="ARS",
                    descripcion="Kiosco San José",
                    sentido="egreso",
                    categoria="Kiosco",
                )
            ],
            billetera_texto=None,
            vencimiento=None,
            total_vistos=1,
            rendimientos=[
                MovimientoExtraido(
                    fecha=ayer,
                    monto=Decimal("500.00"),
                    moneda="ARS",
                    descripcion="Rendimientos",
                    sentido="rendimiento",
                    categoria=None,
                )
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        propuesta_ok = (
            "Además anoto el rendimiento de $500 en Galicia" in resp1
            and "no cuenta como ingreso" in resp1
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        registrado_ok = "Listo" in resp2 and "Rendimiento de $500 anotado en Galicia" in resp2

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        rends_despues = conn.execute(select(func.count(RendimientoBilletera.id)).where(RendimientoBilletera.billetera_id == b_galicia_id)).scalar()

        txs_creadas = txs_despues - txs_antes
        rends_creados = rends_despues - rends_antes

        return f"Propuesta tna: {propuesta_ok} | Registrado tras sí: {registrado_ok} | Txs creadas: {txs_creadas} | Rends creados: {rends_creados}"

    return run_isolated(test)


def p19_caso_18(datos):
    """P19.18: captura con 2 movimientos 'Dinero disponible' y 1 'Mastercard débito'.
    Propuesta nombrando Mercado Pago para los 2 primeros y principal para el 3ro."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        b_sec_id = conn.execute(
            select(Billetera.id).where(Billetera.usuario_id == u.id, Billetera.moneda == Moneda.ARS, Billetera.nombre != "Galicia", Billetera.es_inversion == False)
        ).scalar()
        conn.execute(text("UPDATE billeteras SET nombre = 'Mercado Pago', entidad_id = 'mercadopago' WHERE id = :bid"), {"bid": b_sec_id})
        b_galicia_id = conn.execute(select(Billetera.id).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalar()

        saldo_mp_antes = conn.execute(select(Billetera.saldo_actual).where(Billetera.id == b_sec_id)).scalar()
        saldo_gal_antes = conn.execute(select(Billetera.saldo_actual).where(Billetera.id == b_galicia_id)).scalar()
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[
                MovimientoExtraido(hoy, Decimal("10000"), "ARS", "Tienda Lunar", "egreso", None, "Compra", "Dinero disponible", "Aprobado", False),
                MovimientoExtraido(hoy, Decimal("5000"), "ARS", "Kiosco Norte", "egreso", None, "Compra", "Dinero disponible", "Aprobado", False),
                MovimientoExtraido(hoy, Decimal("3000"), "ARS", "Market Ya", "egreso", None, "Compra", "Mastercard débito", "Aprobado", False),
            ],
            billetera_texto=None, vencimiento=None, total_vistos=3,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        propuesta_ok = ("desde Mercado Pago" in resp1 and "desde Galicia" in resp1 and resp1.count("desde Mercado Pago") == 2 and resp1.count("desde Galicia") == 1)

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes
        txs_mp = conn.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.billetera_id == b_sec_id)).scalars().all()
        txs_gal = conn.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.billetera_id == b_galicia_id)).scalars().all()
        asignacion_billeteras_ok = (len(txs_mp) >= 2 and len(txs_gal) >= 1)

        delta_mp = conn.execute(select(Billetera.saldo_actual).where(Billetera.id == b_sec_id)).scalar() - saldo_mp_antes
        delta_gal = conn.execute(select(Billetera.saldo_actual).where(Billetera.id == b_galicia_id)).scalar() - saldo_gal_antes
        saldos_ok = (delta_mp == Decimal("-15000") and delta_gal == Decimal("-3000"))

        return f"Propuesta billeteras: {propuesta_ok} | Txs creadas: {txs_creadas} | Asignacion OK: {asignacion_billeteras_ok} | Saldos OK: {saldos_ok}"

    return run_isolated(test)


def p19_caso_19(datos):
    """P19.19: captura con 1 común, 2 pases propios, 1 crédito y 1 rechazado.
    La propuesta trae 1 movimiento y las 3 líneas 'Salteé' con textos exactos."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        b_sec_id = conn.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == u.id, Billetera.moneda == Moneda.ARS,
                Billetera.nombre != "Galicia", Billetera.es_inversion == False,
            )
        ).scalar()
        if b_sec_id:
            conn.execute(text("UPDATE billeteras SET nombre = 'Mercado Pago', entidad_id = 'mercadopago' WHERE id = :bid"), {"bid": b_sec_id})

        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[MovimientoExtraido(hoy, Decimal("12000"), "ARS", "Farmacia Azul", "egreso", None, "Compra", "Dinero disponible", "Aprobado", False)],
            billetera_texto=None, vencimiento=None, total_vistos=1,
            omitidos=[
                {"motivo": "pase_propio", "descripcion": "Usuario Prueba", "monto": Decimal("15000"), "moneda": "ARS"},
                {"motivo": "pase_propio", "descripcion": "Ingreso de dinero", "monto": Decimal("25000"), "moneda": "ARS"},
                {"motivo": "credito", "descripcion": "Meli+", "monto": Decimal("18500"), "moneda": "ARS"},
                {"motivo": "no_aprobado", "descripcion": "Kiosco", "monto": Decimal("4000"), "moneda": "ARS"},
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        linea_pase_esperada = "Salteé 2 pases entre tus cuentas ($15.000, $25.000). Si querés registrarlos, mandame cada uno como una transferencia entre tus cuentas."
        linea_cred_esperada = "Salteé 1 pago con tarjeta de crédito (Meli+ $18.500): necesito la tarjeta y las cuotas. Mandámelo escrito."
        linea_rech_esperada = "Salteé 1 movimiento que no figura como aprobado."

        lineas_saltee_ok = (linea_pase_esperada in resp1 and linea_cred_esperada in resp1 and linea_rech_esperada in resp1)
        propuesta_mov_ok = "12.000" in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Propuesta comun: {propuesta_mov_ok} | Saltee exactos: {lineas_saltee_ok} | Txs creadas: {txs_creadas}"

    return run_isolated(test)


def p19_caso_20(datos):
    """P19.20: captura que solo trae omitidos: mensaje fijo más líneas, sin propuesta confirmable."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad", movimientos=[],
            billetera_texto=None, vencimiento=None, total_vistos=0,
            omitidos=[
                {"motivo": "pase_propio", "descripcion": "Ingreso de dinero", "monto": Decimal("5000"), "moneda": "ARS"},
                {"motivo": "credito", "descripcion": "Zara", "monto": Decimal("8000"), "moneda": "ARS"},
                {"motivo": "no_aprobado", "descripcion": "Cafetería", "monto": Decimal("2500"), "moneda": "ARS"},
            ],
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        linea_pase = "Salteé 1 pase entre tus cuentas ($5.000). Si querés registrarlos, mandame cada uno como una transferencia entre tus cuentas."
        linea_cred = "Salteé 1 pago con tarjeta de crédito (Zara $8.000): necesito la tarjeta y las cuotas. Mandámelo escrito."
        linea_rech = "Salteé 1 movimiento que no figura como aprobado."

        mensaje_ok = (
            resp1.startswith("No encontré movimientos para anotar en la imagen.")
            and linea_pase in resp1 and linea_cred in resp1 and linea_rech in resp1
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        txs_creadas = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar() - txs_antes
        sin_propuesta_confirmable = (txs_creadas == 0 and "No tenés ninguna operación pendiente" in resp2)

        return f"Mensaje omitidos: {mensaje_ok} | Sin propuesta confirmable: {sin_propuesta_confirmable} | Txs creadas: {txs_creadas}"

    return run_isolated(test)


def p19_caso_21(datos):
    """P19.21: captura donde un movimiento común coincide en monto y fecha con uno de OTRA billetera.
    NO se marca como duplicado (la comparación es por billetera del movimiento)."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        b_sec_id = conn.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == u.id, Billetera.moneda == Moneda.ARS,
                Billetera.nombre != "Galicia", Billetera.es_inversion == False,
            )
        ).scalar()
        assert b_sec_id is not None
        conn.execute(
            text("UPDATE billeteras SET nombre = 'Mercado Pago', entidad_id = 'mercadopago' WHERE id = :bid"),
            {"bid": b_sec_id},
        )
        b_galicia_id = conn.execute(
            select(Billetera.id).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")
        ).scalar()

        db = Session()
        tx_existente = Transaccion(
            usuario_id=u.id, monto=Decimal("15000"), moneda=Moneda.ARS, tipo=TipoTransaccion.EGRESO,
            fecha=hoy, descripcion="Supermercado Coto", billetera_id=b_galicia_id,
            metodo_pago=MetodoPago.DEBITO, origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        db.add(tx_existente)
        db.commit()
        db.close()

        ext = ResultadoExtraccion(
            documento_tipo="captura_actividad",
            movimientos=[MovimientoExtraido(hoy, Decimal("15000"), "ARS", "Supermercado Coto", "egreso", None, "Compra", "Dinero disponible", "Aprobado", False)],
            billetera_texto=None, vencimiento=None, total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_bytes", "image/jpeg")), \
             patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_image(), time.perf_counter())

        resp1 = respuestas[-1][1] if respuestas else ""
        no_es_duplicado = "Ya tenías cargado" not in resp1
        propone_en_mp = "desde Mercado Pago" in resp1 and "15.000" in resp1

        return f"No es duplicado: {no_es_duplicado} | Propone en MP: {propone_en_mp}"

    return run_isolated(test)
