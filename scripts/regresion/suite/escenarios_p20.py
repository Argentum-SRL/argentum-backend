"""
scripts/regresion/suite/escenarios_p20.py

Escenarios del Punto 20: Lectura y verificación de comprobantes en PDF (fase4c2b1).
Todos los escenarios usan extracciones simuladas (sin llamadas a OpenAI ni Meta).
La lectura de PDF con pypdf y la verificación corren de verdad.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import io
import json
import time
from typing import Any
from unittest.mock import patch
import uuid

from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import func, select, text

from app.models.categoria import Categoria
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
    run_isolated,
)


def make_payload_document(
    from_number: str = TELEFONO_TEST,
    media_id: str = "media_doc_test",
    filename: str = "factura.pdf",
    mime_type: str = "application/pdf",
    caption: str = "",
    wamid: str | None = None,
) -> bytes:
    if not wamid:
        wamid = f"wamid_doc_{uuid.uuid4().hex[:12]}"
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": from_number or TELEFONO_TEST,
                                    "type": "document",
                                    "document": {
                                        "id": media_id,
                                        "mime_type": mime_type,
                                        "filename": filename,
                                        "caption": caption,
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


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


def _crear_pdf_texto(lineas: list[str]) -> bytes:
    escaped_lines = []
    for l in lineas:
        esc = l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        escaped_lines.append(f"({esc}) Tj")
    stream_text = "BT /F1 12 Tf 50 700 Td " + " T* ".join(escaped_lines) + " ET"
    stream_bytes = stream_text.encode("latin-1")
    stream_len = len(stream_bytes)

    header = b"%PDF-1.4\n"
    o1 = len(header)
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    o2 = o1 + len(obj1)
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    o3 = o2 + len(obj2)
    obj3 = b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    o4 = o3 + len(obj3)
    obj4 = f"4 0 obj\n<< /Length {stream_len} >>\nstream\n{stream_text}\nendstream\nendobj\n".encode("latin-1")
    o5 = o4 + len(obj4)
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    xref_pos = o5 + len(obj5)
    xref = (
        f"xref\n0 6\n0000000000 65535 f \n"
        f"{o1:010d} 00000 n \n"
        f"{o2:010d} 00000 n \n"
        f"{o3:010d} 00000 n \n"
        f"{o4:010d} 00000 n \n"
        f"{o5:010d} 00000 n \n"
        f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    ).encode("latin-1")
    return header + obj1 + obj2 + obj3 + obj4 + obj5 + xref


def _crear_pdf_escaneado() -> bytes:
    img = Image.new("RGB", (100, 100), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def _crear_pdf_paginas(n: int) -> bytes:
    writer = PdfWriter()
    for _ in range(n):
        writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def p20_caso_1(datos):
    """P20.1: PDF de EPE (cuota 1 en hoy + 6, cuota 2 en hoy + 36, $*77.597,44) con extracción falsa coherente, después 'sí'."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        from app.models.factura import Factura
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        fac_ids_antes = set(conn.execute(select(Factura.id).where(Factura.usuario_id == u.id)).scalars().all())
        txs_antes = len(tx_ids_antes)

        venc1 = hoy + timedelta(days=6)
        venc2 = hoy + timedelta(days=36)
        f1_str = venc1.strftime("%d/%m/%Y")
        f2_str = venc2.strftime("%d/%m/%Y")
        f1 = venc1.strftime("%d/%m")
        f2 = venc2.strftime("%d/%m")

        lineas = [
            "Empresa Provincial de la Energia de Santa Fe EPE",
            f"Factura de Servicio Electrico Cuota 1 {f1_str} $*77.597,44",
            f"Cuota 2 {f2_str} $*77.597,44 Total a pagar Periodo 10/2026",
            "Liquidacion de Servicios Publicos Usuario Residencial Medidor 4829104",
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
            billetera_texto="Galicia",
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
            f"Factura de EPE en 2 cuotas de $77.597,44: vencen el {f1} y el {f2}.\n"
            "¿Ya pagaste la primera? Si me decís que sí, la anoto como gasto de hoy en Luz desde Galicia y te anoto la segunda en la web.\n"
            "Si fue con otra, decime cuál.\n"
            "Si me decís que no, te anoto las dos en la web para que no se te pasen."
        )
        propuesta_ok = resp1 == esperado_resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""
        esperado_resp2 = (
            f"Listo. $77.597,44 en Luz desde Galicia — registrado.\n"
            f"Te anoté la segunda cuota ($77.597,44, vence el {f2}) en la web."
        )
        registrado_ok = resp2 == esperado_resp2

        db = Session()
        nuevas_txs = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))
        ).scalars().all()
        nuevas_facs = db.execute(
            select(Factura).where(Factura.usuario_id == u.id, Factura.id.not_in(fac_ids_antes))
        ).scalars().all()

        tx = nuevas_txs[0] if nuevas_txs else None
        cat = db.get(Categoria, tx.categoria_id) if tx and tx.categoria_id else None
        subcat = db.get(Subcategoria, tx.subcategoria_id) if tx and tx.subcategoria_id else None

        fac = nuevas_facs[0] if nuevas_facs else None
        db.close()

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        mov_ok = (
            txs_creadas == 1
            and tx is not None
            and tx.monto == Decimal("77597.44")
            and cat is not None and cat.nombre == "Vivienda"
            and subcat is not None and subcat.nombre == "Luz"
        )
        fac_ok = (
            len(nuevas_facs) == 1
            and fac is not None
            and fac.monto == Decimal("77597.44")
            and fac.fecha_vencimiento == venc2
            and fac.estado == "pendiente"
        )
        tx_ok = mov_ok and fac_ok

        return f"Propuesta: {propuesta_ok} | Registrado tras sí: {registrado_ok} | Movimiento Luz: {tx_ok}"

    return run_isolated(test)


def p20_caso_2(datos):
    """P20.2: PDF escaneado: texto exacto de SIN_TEXTO y 0 movimientos."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        pdf_bytes = _crear_pdf_escaneado()

        respuestas.clear()
        with patch("app.routers.whatsapp.media.descargar_documento_meta", return_value=(pdf_bytes, "application/pdf", None)):
            _procesar_webhook_whatsapp_sync(make_payload_document(), time.perf_counter())

        resp = respuestas[-1][1] if respuestas else ""
        esperado = "Ese PDF es una imagen escaneada y no lo puedo leer. Mandame una captura de la factura."
        texto_ok = resp == esperado

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Texto exacto: {texto_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def p20_caso_3(datos):
    """P20.3: Descarga con TAMANO_EXCEDIDO: texto exacto y 0 movimientos."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        with patch("app.routers.whatsapp.media.descargar_documento_meta", return_value=(None, None, "TAMANO_EXCEDIDO")):
            _procesar_webhook_whatsapp_sync(make_payload_document(), time.perf_counter())

        resp = respuestas[-1][1] if respuestas else ""
        esperado = "El PDF es muy pesado (máximo 10 MB). Mandame una captura de la factura."
        texto_ok = resp == esperado

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Texto exacto: {texto_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def p20_caso_4(datos):
    """P20.4: Extracción falsa con un monto que no está en el PDF: texto exacto de la verificación y 0 movimientos."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    hoy = hoy_argentina()

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        venc = hoy + timedelta(days=10)
        f_str = venc.strftime("%d/%m/%Y")
        lineas = [
            "Empresa Provincial de la Energia de Santa Fe EPE",
            f"Factura de Servicio Electrico Cuota 1 {f_str} $*77.597,44",
            "Liquidacion de Servicios Publicos Usuario Residencial Medidor 4829104",
            "Total a pagar en pesos argentinos sin recargo hasta la fecha estipulada",
        ]
        pdf_bytes = _crear_pdf_texto(lineas)

        ext = ResultadoExtraccion(
            documento_tipo="factura_servicio",
            movimientos=[
                MovimientoExtraido(
                    fecha=hoy,
                    monto=Decimal("99999.00"),
                    moneda="ARS",
                    descripcion="EPE",
                    sentido="egreso",
                    categoria="Luz",
                )
            ],
            billetera_texto="Galicia",
            vencimiento=venc,
            total_vistos=1,
        )

        respuestas.clear()
        with patch("app.routers.whatsapp.media.descargar_documento_meta", return_value=(pdf_bytes, "application/pdf", None)), \
             patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_texto_pdf", return_value=(ext, None)):
            _procesar_webhook_whatsapp_sync(make_payload_document(), time.perf_counter())

        resp = respuestas[-1][1] if respuestas else ""
        esperado = "No pude confirmar el importe o el vencimiento en el PDF. Mandame una captura de la factura."
        texto_ok = resp == esperado

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Texto exacto: {texto_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def p20_caso_5(datos):
    """P20.5: Documento .docx: texto exacto y 0 movimientos."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        payload = make_payload_document(
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="factura.docx",
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

        resp = respuestas[-1][1] if respuestas else ""
        esperado = "Por ahora leo comprobantes en PDF o en foto. Mandame el PDF o una captura."
        texto_ok = resp == esperado

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Texto exacto: {texto_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def p20_caso_6(datos):
    """P20.6: PDF de 7 páginas: texto exacto y 0 movimientos."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        txs_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        pdf_bytes = _crear_pdf_paginas(7)

        respuestas.clear()
        with patch("app.routers.whatsapp.media.descargar_documento_meta", return_value=(pdf_bytes, "application/pdf", None)):
            _procesar_webhook_whatsapp_sync(make_payload_document(), time.perf_counter())

        resp = respuestas[-1][1] if respuestas else ""
        esperado = "El PDF tiene más de 6 páginas. Mandame solo la página con el total o una captura."
        texto_ok = resp == esperado

        txs_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = txs_despues - txs_antes

        return f"Texto exacto: {texto_ok} | Creadas: {txs_creadas}"

    return run_isolated(test)


def entradas_p20(datos: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna las entradas de catálogo para los escenarios P20.1 a P20.6."""
    return [
        {
            "id": "P20.1", "punto": "Punto 20", "match": "exacto",
            "nombre": "PDF de EPE con 2 cuotas, propuesta con línea de no pago y confirmación con sí",
            "ejecutar": lambda: p20_caso_1(datos),
            "esperado": "Propuesta: True | Registrado tras sí: True | Movimiento Luz: True",
        },
        {
            "id": "P20.2", "punto": "Punto 20", "match": "exacto",
            "nombre": "PDF escaneado sin texto seleccionable responde aviso de imagen escaneada",
            "ejecutar": lambda: p20_caso_2(datos),
            "esperado": "Texto exacto: True | Creadas: 0",
        },
        {
            "id": "P20.3", "punto": "Punto 20", "match": "exacto",
            "nombre": "Descarga de PDF con tamaño excedido responde aviso de peso máximo",
            "ejecutar": lambda: p20_caso_3(datos),
            "esperado": "Texto exacto: True | Creadas: 0",
        },
        {
            "id": "P20.4", "punto": "Punto 20", "match": "exacto",
            "nombre": "Extracción con monto no presente en texto del PDF rechaza con aviso de verificación",
            "ejecutar": lambda: p20_caso_4(datos),
            "esperado": "Texto exacto: True | Creadas: 0",
        },
        {
            "id": "P20.5", "punto": "Punto 20", "match": "exacto",
            "nombre": "Documento con formato no permitido (.docx) responde aviso de solo PDF o foto",
            "ejecutar": lambda: p20_caso_5(datos),
            "esperado": "Texto exacto: True | Creadas: 0",
        },
        {
            "id": "P20.6", "punto": "Punto 20", "match": "exacto",
            "nombre": "PDF de más de 6 páginas responde aviso de límite de páginas",
            "ejecutar": lambda: p20_caso_6(datos),
            "esperado": "Texto exacto: True | Creadas: 0",
        },
    ]
