"""
tests/test_pdf_documento.py
Pruebas unitarias de lectura de PDF con pypdf, regex de montos y fechas,
verificación determinística contra texto, validaciones 4d y descarga controlada por tamaño.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import io
from unittest.mock import MagicMock, patch

from PIL import Image
from pypdf import PdfWriter

from app.routers.whatsapp.extraccion_documento import (
    CuotaExtraida,
    MovimientoExtraido,
    ResultadoExtraccion,
    _procesar_y_validar_respuesta_extraccion,
)
from app.routers.whatsapp.media import descargar_documento_meta
from app.routers.whatsapp.pdf_documento import (
    fechas_en_texto,
    leer_texto_pdf,
    montos_en_texto,
    verificar_extraccion_contra_texto,
)
from app.utils.fecha import hoy_argentina


def _crear_pdf_con_lineas(lineas: list[str]) -> bytes:
    """Genera un archivo PDF válido en memoria con texto legible por pypdf."""
    stream_content = "BT\n/F1 12 Tf\n50 750 Td\n"
    for i, linea in enumerate(lineas):
        l_escaped = linea.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i == 0:
            stream_content += f"({l_escaped}) Tj\n"
        else:
            stream_content += f"0 -20 Td\n({l_escaped}) Tj\n"
    stream_content += "ET\n"
    stream_bytes = stream_content.encode("latin-1")

    parts: list[bytes] = [b"%PDF-1.4\n"]
    offsets: list[int] = [0]

    # obj 1: Catalog
    offsets.append(sum(len(p) for p in parts))
    parts.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # obj 2: Pages
    offsets.append(sum(len(p) for p in parts))
    parts.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

    # obj 3: Page
    offsets.append(sum(len(p) for p in parts))
    parts.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )

    # obj 4: Contents
    offsets.append(sum(len(p) for p in parts))
    parts.append(
        b"4 0 obj\n<< /Length " + str(len(stream_bytes)).encode("ascii") + b" >>\nstream\n"
        + stream_bytes + b"\nendstream\nendobj\n"
    )

    # obj 5: Font
    offsets.append(sum(len(p) for p in parts))
    parts.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    # xref
    xref_offset = sum(len(p) for p in parts)
    parts.append(b"xref\n0 6\n")
    parts.append(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        parts.append(f"{off:010d} 00000 n \n".encode("ascii"))

    parts.append(b"trailer\n<< /Size 6 /Root 1 0 R >>\n")
    parts.append(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))

    return b"".join(parts)


def test_parsing_montos_y_fechas_sintetico():
    """C1.1: Verifica extracción de importes argentinos y fechas válidas en PDF sintético."""
    lineas = [
        "Cuota 1 13/10/2026 $*77.597,44",
        "TOTAL $**155.194,88",
        "TOTAL A PAGAR hasta el 13/10/2026 25.015,01",
        "F 0081-34498428 15/09/2023 2087.49",
        "Relleno para superar los 100 caracteres sin espacios minimos del parser de pdf en tests.",
    ]
    pdf_bytes = _crear_pdf_con_lineas(lineas)
    texto, error = leer_texto_pdf(pdf_bytes)
    assert error is None
    assert texto is not None

    montos = montos_en_texto(texto)
    assert montos == {Decimal("77597.44"), Decimal("155194.88"), Decimal("25015.01")}

    fechas = fechas_en_texto(texto)
    assert date(2026, 10, 13) in fechas


def test_leer_texto_pdf_casos():
    """C1.2: Casos de borde de leer_texto_pdf: páginas, imágenes, claves y bytes corruptos."""
    # 7 páginas: DEMASIADAS_PAGINAS
    w7 = PdfWriter()
    for _ in range(7):
        w7.add_blank_page(width=72, height=72)
    b7 = io.BytesIO()
    w7.write(b7)
    txt7, err7 = leer_texto_pdf(b7.getvalue())
    assert txt7 is None
    assert err7 == "DEMASIADAS_PAGINAS"

    # solo imagen: SIN_TEXTO
    img = Image.new("RGB", (100, 100), color="white")
    b_img = io.BytesIO()
    img.save(b_img, format="PDF")
    txt_img, err_img = leer_texto_pdf(b_img.getvalue())
    assert txt_img is None
    assert err_img == "SIN_TEXTO"

    # cifrado con clave: PDF_CON_CLAVE
    wc = PdfWriter()
    wc.add_blank_page(width=72, height=72)
    wc.encrypt("password123")
    bc = io.BytesIO()
    wc.write(bc)
    txt_c, err_c = leer_texto_pdf(bc.getvalue())
    assert txt_c is None
    assert err_c == "PDF_CON_CLAVE"

    # bytes basura: PDF_ILEGIBLE
    txt_b, err_b = leer_texto_pdf(b"not a valid pdf content")
    assert txt_b is None
    assert err_b == "PDF_ILEGIBLE"

    # 1 página válida: el texto
    lineas = [
        "Linea de prueba para validar que el texto se extraiga correctamente en una pagina valida.",
        "Segunda linea de texto con mas caracteres para superar con creces los 100 caracteres sin espacio.",
    ]
    pdf_valido = _crear_pdf_con_lineas(lineas)
    txt_v, err_v = leer_texto_pdf(pdf_valido)
    assert err_v is None
    assert txt_v is not None
    assert "Linea de prueba" in txt_v


def test_verificar_extraccion_contra_texto():
    """C1.3: Control de montos y fechas extraídas contra texto crudo del PDF."""
    texto = (
        "Factura EPE\n"
        "Vencimiento 13/10/2026\n"
        "Total $*77.597,44\n"
        "Cuota 2 Vto 12/11/2026 Importe 77.597,44\n"
    )

    # monto y fecha presentes: True
    res_ok = ResultadoExtraccion(
        documento_tipo="factura_servicio",
        movimientos=[
            MovimientoExtraido(
                fecha=date(2026, 10, 13),
                monto=Decimal("77597.44"),
                moneda="ARS",
                descripcion="EPE",
                sentido="egreso",
                categoria="Luz",
            )
        ],
        billetera_texto=None,
        vencimiento=date(2026, 10, 13),
        total_vistos=1,
    )
    assert verificar_extraccion_contra_texto(res_ok, texto) is True

    # monto ausente: False
    res_monto_malo = ResultadoExtraccion(
        documento_tipo="factura_servicio",
        movimientos=[
            MovimientoExtraido(
                fecha=date(2026, 10, 13),
                monto=Decimal("99999.99"),
                moneda="ARS",
                descripcion="EPE",
                sentido="egreso",
                categoria="Luz",
            )
        ],
        billetera_texto=None,
        vencimiento=date(2026, 10, 13),
        total_vistos=1,
    )
    assert verificar_extraccion_contra_texto(res_monto_malo, texto) is False

    # fecha ausente: False
    res_fecha_mala = ResultadoExtraccion(
        documento_tipo="factura_servicio",
        movimientos=[
            MovimientoExtraido(
                fecha=date(2026, 10, 13),
                monto=Decimal("77597.44"),
                moneda="ARS",
                descripcion="EPE",
                sentido="egreso",
                categoria="Luz",
            )
        ],
        billetera_texto=None,
        vencimiento=date(2026, 12, 25),
        total_vistos=1,
    )
    assert verificar_extraccion_contra_texto(res_fecha_mala, texto) is False

    # una cuota con monto ausente: False
    res_cuota_monto_malo = ResultadoExtraccion(
        documento_tipo="factura_servicio",
        movimientos=[
            MovimientoExtraido(
                fecha=date(2026, 10, 13),
                monto=Decimal("77597.44"),
                moneda="ARS",
                descripcion="EPE",
                sentido="egreso",
                categoria="Luz",
            )
        ],
        billetera_texto=None,
        vencimiento=date(2026, 10, 13),
        total_vistos=1,
        cuotas=[
            CuotaExtraida(vencimiento=date(2026, 10, 13), monto=Decimal("77597.44")),
            CuotaExtraida(vencimiento=date(2026, 11, 12), monto=Decimal("88888.88")),
        ],
    )
    assert verificar_extraccion_contra_texto(res_cuota_monto_malo, texto) is False


def test_validaciones_4d():
    """C1.4: Validaciones determinísticas de categoría, fechas y cuotas según reglas 4d."""
    hoy = hoy_argentina()
    categorias_permitidas = ["Luz", "Gas", "Agua", "Internet"]

    # 4d.1 "Servicios": None (no está en categorias_permitidas)
    data1 = {
        "legible": True,
        "documento_tipo": "factura_servicio",
        "billetera_texto": None,
        "vencimiento": hoy.isoformat(),
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 1000.0,
                "moneda": "ARS",
                "descripcion": "EPE",
                "sentido": "egreso",
                "categoria": "Servicios",
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": False,
            }
        ],
        "cuotas": [],
    }
    r1, _ = _procesar_y_validar_respuesta_extraccion(data1, categorias_permitidas, hoy)
    assert r1 is not None
    assert r1.movimientos[0].categoria is None

    # 4d.2 "luz": "Luz" (coincide normalizado -> toma "Luz")
    data2 = dict(data1)
    data2["movimientos"] = [dict(data1["movimientos"][0], categoria="luz")]
    r2, _ = _procesar_y_validar_respuesta_extraccion(data2, categorias_permitidas, hoy)
    assert r2 is not None
    assert r2.movimientos[0].categoria == "Luz"

    # 4d.3 vencimiento en hoy + 200: None
    data3 = dict(data1)
    data3["vencimiento"] = (hoy + timedelta(days=200)).isoformat()
    r3, _ = _procesar_y_validar_respuesta_extraccion(data3, categorias_permitidas, hoy)
    assert r3 is not None
    assert r3.vencimiento is None

    # 4d.4 vencimiento en hoy - 61: None
    data4 = dict(data1)
    data4["vencimiento"] = (hoy - timedelta(days=61)).isoformat()
    r4, _ = _procesar_y_validar_respuesta_extraccion(data4, categorias_permitidas, hoy)
    assert r4 is not None
    assert r4.vencimiento is None

    # 4d.5 vencimiento "2026-13-40": None
    data5 = dict(data1)
    data5["vencimiento"] = "2026-13-40"
    r5, _ = _procesar_y_validar_respuesta_extraccion(data5, categorias_permitidas, hoy)
    assert r5 is not None
    assert r5.vencimiento is None

    # 4d.6 cuotas con una fecha fuera de la ventana: [] y vencimiento None
    data6 = dict(data1)
    data6["vencimiento"] = (hoy + timedelta(days=10)).isoformat()
    data6["cuotas"] = [
        {"vencimiento": (hoy + timedelta(days=10)).isoformat(), "monto": 500.0},
        {"vencimiento": (hoy + timedelta(days=130)).isoformat(), "monto": 500.0},
    ]
    r6, _ = _procesar_y_validar_respuesta_extraccion(data6, categorias_permitidas, hoy)
    assert r6 is not None
    assert r6.cuotas == []
    assert r6.vencimiento is None

    # 4d.7 ticket_compra con vencimiento: None
    data7 = dict(data1)
    data7["documento_tipo"] = "ticket_compra"
    data7["vencimiento"] = (hoy + timedelta(days=10)).isoformat()
    r7, _ = _procesar_y_validar_respuesta_extraccion(data7, categorias_permitidas, hoy)
    assert r7 is not None
    assert r7.vencimiento is None
    assert r7.cuotas == []


def test_descargar_documento_meta_cliente_falso():
    """C1.5: Descarga con control de tamaño en dos pasos (11MB meta, 1MB ok, 11MB content)."""
    # 5.1 file_size de 11 MB: TAMANO_EXCEDIDO y 0 llamadas de descarga
    mock_client1 = MagicMock()
    mock_res_meta1 = MagicMock()
    mock_res_meta1.json.return_value = {
        "url": "https://cdn.example.com/file.pdf",
        "mime_type": "application/pdf",
        "file_size": 11 * 1024 * 1024,
    }
    mock_res_meta1.raise_for_status.return_value = None
    mock_client1.get.return_value = mock_res_meta1

    with patch("app.routers.whatsapp.media.settings") as mock_settings, \
         patch("app.routers.whatsapp.media.get_meta_http_client", return_value=mock_client1):
        mock_settings.WHATSAPP_ACCESS_TOKEN = "fake_token"
        b1, mime1, err1 = descargar_documento_meta("media_1", max_bytes=10 * 1024 * 1024)
        assert b1 is None
        assert err1 == "TAMANO_EXCEDIDO"
        assert mock_client1.get.call_count == 1
        assert "graph.facebook.com" in mock_client1.get.call_args_list[0].args[0]

    # 5.2 file_size de 1 MB: los bytes
    mock_client2 = MagicMock()
    mock_res_meta2 = MagicMock()
    mock_res_meta2.json.return_value = {
        "url": "https://cdn.example.com/file.pdf",
        "mime_type": "application/pdf",
        "file_size": 1 * 1024 * 1024,
    }
    mock_res_media2 = MagicMock()
    mock_res_media2.content = b"pdf_content_1mb"
    mock_res_media2.raise_for_status.return_value = None
    mock_client2.get.side_effect = [mock_res_meta2, mock_res_media2]

    with patch("app.routers.whatsapp.media.settings") as mock_settings, \
         patch("app.routers.whatsapp.media.get_meta_http_client", return_value=mock_client2):
        mock_settings.WHATSAPP_ACCESS_TOKEN = "fake_token"
        b2, mime2, err2 = descargar_documento_meta("media_2", max_bytes=10 * 1024 * 1024)
        assert b2 == b"pdf_content_1mb"
        assert mime2 == "application/pdf"
        assert err2 is None
        assert mock_client2.get.call_count == 2

    # 5.3 sin file_size y 11 MB bajados: TAMANO_EXCEDIDO
    mock_client3 = MagicMock()
    mock_res_meta3 = MagicMock()
    mock_res_meta3.json.return_value = {
        "url": "https://cdn.example.com/file.pdf",
        "mime_type": "application/pdf",
    }
    mock_res_media3 = MagicMock()
    mock_res_media3.content = b"x" * (11 * 1024 * 1024)
    mock_res_media3.raise_for_status.return_value = None
    mock_client3.get.side_effect = [mock_res_meta3, mock_res_media3]

    with patch("app.routers.whatsapp.media.settings") as mock_settings, \
         patch("app.routers.whatsapp.media.get_meta_http_client", return_value=mock_client3):
        mock_settings.WHATSAPP_ACCESS_TOKEN = "fake_token"
        b3, mime3, err3 = descargar_documento_meta("media_3", max_bytes=10 * 1024 * 1024)
        assert b3 is None
        assert err3 == "TAMANO_EXCEDIDO"
        assert mock_client3.get.call_count == 2


# =============================================================================
# C1. Validaciones de _procesar_y_validar_respuesta_extraccion (Fase 4c2b1_c)
# =============================================================================

def test_factura_servicio_contraparte_usuario_no_se_omite():
    """C1.1: Factura de servicio con contraparte_es_usuario=True: el movimiento queda, sin omitidos."""
    hoy = hoy_argentina()
    data = {
        "legible": True,
        "documento_tipo": "factura_servicio",
        "billetera_texto": None,
        "vencimiento": hoy.isoformat(),
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 101807.80,
                "moneda": "ARS",
                "descripcion": "Personal",
                "sentido": "egreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": True,
            }
        ],
        "cuotas": [],
    }
    res, err = _procesar_y_validar_respuesta_extraccion(data, None, hoy)
    assert err is None
    assert res is not None
    assert len(res.movimientos) == 1
    assert res.omitidos == []
    assert res.movimientos[0].contraparte_es_usuario is True
    assert res.movimientos[0].sentido == "egreso"


def test_factura_servicio_medio_pago_credito_no_se_omite():
    """C1.2: Factura de servicio con medio_pago='Visa crédito': el movimiento queda, sin omitidos."""
    hoy = hoy_argentina()
    data = {
        "legible": True,
        "documento_tipo": "factura_servicio",
        "billetera_texto": None,
        "vencimiento": hoy.isoformat(),
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 101807.80,
                "moneda": "ARS",
                "descripcion": "Personal",
                "sentido": "egreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": "Visa crédito",
                "estado": None,
                "contraparte_es_usuario": False,
            }
        ],
        "cuotas": [],
    }
    res, err = _procesar_y_validar_respuesta_extraccion(data, None, hoy)
    assert err is None
    assert res is not None
    assert len(res.movimientos) == 1
    assert res.omitidos == []
    assert res.movimientos[0].medio_pago == "Visa crédito"
    assert res.movimientos[0].sentido == "egreso"


def test_factura_servicio_estado_pendiente_no_se_omite():
    """C1.3: Factura de servicio con estado='Pendiente': el movimiento queda, sin omitidos."""
    hoy = hoy_argentina()
    data = {
        "legible": True,
        "documento_tipo": "factura_servicio",
        "billetera_texto": None,
        "vencimiento": hoy.isoformat(),
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 101807.80,
                "moneda": "ARS",
                "descripcion": "Personal",
                "sentido": "egreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": "Pendiente",
                "contraparte_es_usuario": False,
            }
        ],
        "cuotas": [],
    }
    res, err = _procesar_y_validar_respuesta_extraccion(data, None, hoy)
    assert err is None
    assert res is not None
    assert len(res.movimientos) == 1
    assert res.omitidos == []
    assert res.movimientos[0].estado == "Pendiente"
    assert res.movimientos[0].sentido == "egreso"


def test_factura_servicio_sentido_ingreso_queda_egreso():
    """C1.4: Factura de servicio con sentido='ingreso': queda como egreso."""
    hoy = hoy_argentina()
    data = {
        "legible": True,
        "documento_tipo": "factura_servicio",
        "billetera_texto": None,
        "vencimiento": hoy.isoformat(),
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 101807.80,
                "moneda": "ARS",
                "descripcion": "Personal",
                "sentido": "ingreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": False,
            }
        ],
        "cuotas": [],
    }
    res, err = _procesar_y_validar_respuesta_extraccion(data, None, hoy)
    assert err is None
    assert res is not None
    assert len(res.movimientos) == 1
    assert res.omitidos == []
    assert res.movimientos[0].sentido == "egreso"


def test_captura_actividad_contraparte_usuario_sigue_omitida():
    """C1.5: Captura de actividad con contraparte_es_usuario=True: sigue omitida como hoy."""
    hoy = hoy_argentina()
    data = {
        "legible": True,
        "documento_tipo": "captura_actividad",
        "billetera_texto": "Mercado Pago",
        "vencimiento": None,
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 5000.0,
                "moneda": "ARS",
                "descripcion": "Sebastián Giordanino",
                "sentido": "egreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": True,
            }
        ],
        "cuotas": [],
    }
    res, err = _procesar_y_validar_respuesta_extraccion(data, None, hoy)
    assert err is None
    assert res is not None
    assert res.movimientos == []
    assert len(res.omitidos) == 1
    assert res.omitidos[0]["motivo"] == "pase_propio"


def test_factura_servicio_un_movimiento_con_cuotas_toma_primera():
    """C1.6: Factura de servicio con 1 movimiento de 155194.88 y cuotas (hoy+36, hoy+6) toma hoy+6."""
    hoy = hoy_argentina()
    data = {
        "legible": True,
        "documento_tipo": "factura_servicio",
        "billetera_texto": None,
        "vencimiento": (hoy + timedelta(days=36)).isoformat(),
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 155194.88,
                "moneda": "ARS",
                "descripcion": "EPE",
                "sentido": "egreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": False,
            }
        ],
        "cuotas": [
            {"vencimiento": (hoy + timedelta(days=36)).isoformat(), "monto": 77597.44},
            {"vencimiento": (hoy + timedelta(days=6)).isoformat(), "monto": 77597.44},
        ],
    }
    res, err = _procesar_y_validar_respuesta_extraccion(data, None, hoy)
    assert err is None
    assert res is not None
    assert len(res.movimientos) == 1
    assert res.movimientos[0].monto == Decimal("77597.44")
    assert res.vencimiento == hoy + timedelta(days=6)


def test_factura_servicio_dos_movimientos_con_cuotas_queda_un_movimiento_vencimiento_temprano():
    """C1: Factura de servicio con 2 movimientos de 77597.44, vencimiento null y cuotas (hoy+36, hoy+6)."""
    hoy = hoy_argentina()
    data = {
        "legible": True,
        "documento_tipo": "factura_servicio",
        "billetera_texto": None,
        "vencimiento": None,
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 77597.44,
                "moneda": "ARS",
                "descripcion": "EPE 1",
                "sentido": "egreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": False,
            },
            {
                "fecha": hoy.isoformat(),
                "monto": 77597.44,
                "moneda": "ARS",
                "descripcion": "EPE 2",
                "sentido": "egreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": False,
            },
        ],
        "cuotas": [
            {"vencimiento": (hoy + timedelta(days=36)).isoformat(), "monto": 77597.44},
            {"vencimiento": (hoy + timedelta(days=6)).isoformat(), "monto": 77597.44},
        ],
    }
    res, err = _procesar_y_validar_respuesta_extraccion(data, None, hoy)
    assert err is None
    assert res is not None
    assert len(res.movimientos) == 1
    assert res.movimientos[0].monto == Decimal("77597.44")
    assert res.vencimiento == hoy + timedelta(days=6)
    assert res.total_vistos == 1


# =============================================================================
# C2. Tests nuevos (Fase 4c2b1_d)
# =============================================================================

def test_factura_servicio_un_movimiento_vencimiento_null_con_cuotas():
    """C2.1: Factura de servicio con 1 movimiento de 155194.88, vencimiento null y cuotas (hoy+36, hoy+6)."""
    hoy = hoy_argentina()
    data = {
        "legible": True,
        "documento_tipo": "factura_servicio",
        "billetera_texto": None,
        "vencimiento": None,
        "movimientos": [
            {
                "fecha": hoy.isoformat(),
                "monto": 155194.88,
                "moneda": "ARS",
                "descripcion": "EPE",
                "sentido": "egreso",
                "categoria": None,
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": False,
            }
        ],
        "cuotas": [
            {"vencimiento": (hoy + timedelta(days=36)).isoformat(), "monto": 77597.44},
            {"vencimiento": (hoy + timedelta(days=6)).isoformat(), "monto": 77597.44},
        ],
    }
    res, err = _procesar_y_validar_respuesta_extraccion(data, None, hoy)
    assert err is None
    assert res is not None
    assert len(res.movimientos) == 1
    assert res.movimientos[0].monto == Decimal("77597.44")
    assert res.vencimiento == hoy + timedelta(days=6)
    assert res.total_vistos == 1


def test_verificar_comprobante_transferencia_vencimiento_none():
    """C2.2: Verificar con comprobante_transferencia, monto en el texto y vencimiento None: True."""
    texto = "Comprobante de transferencia\nImporte: $ 15.000,00\nDestinatario: Juan Perez\n"
    res = ResultadoExtraccion(
        documento_tipo="comprobante_transferencia",
        movimientos=[
            MovimientoExtraido(
                fecha=date(2026, 10, 8),
                monto=Decimal("15000.00"),
                moneda="ARS",
                descripcion="Juan Perez",
                sentido="egreso",
                categoria=None,
            )
        ],
        billetera_texto=None,
        vencimiento=None,
        total_vistos=1,
    )
    assert verificar_extraccion_contra_texto(res, texto) is True


def test_verificar_factura_servicio_vencimiento_none():
    """C2.3: Verificar con factura_servicio, vencimiento None y montos en el texto: True."""
    texto = "Factura de servicio\nTotal a pagar $ 25.015,01\n"
    res = ResultadoExtraccion(
        documento_tipo="factura_servicio",
        movimientos=[
            MovimientoExtraido(
                fecha=date(2026, 10, 8),
                monto=Decimal("25015.01"),
                moneda="ARS",
                descripcion="Litoral Gas",
                sentido="egreso",
                categoria=None,
            )
        ],
        billetera_texto=None,
        vencimiento=None,
        total_vistos=1,
    )
    assert verificar_extraccion_contra_texto(res, texto) is True


def test_verificar_vencimiento_no_en_texto():
    """C2.4: Verificar con un vencimiento que no está en el texto: False."""
    texto = "Factura de servicio\nTotal a pagar $ 25.015,01\nFecha de emision: 01/10/2026\n"
    res = ResultadoExtraccion(
        documento_tipo="factura_servicio",
        movimientos=[
            MovimientoExtraido(
                fecha=date(2026, 10, 8),
                monto=Decimal("25015.01"),
                moneda="ARS",
                descripcion="Litoral Gas",
                sentido="egreso",
                categoria=None,
            )
        ],
        billetera_texto=None,
        vencimiento=date(2026, 12, 15),
        total_vistos=1,
    )
    assert verificar_extraccion_contra_texto(res, texto) is False


def test_verificar_cuota_fecha_no_en_texto():
    """C2.5: Verificar con una cuota cuya fecha no está en el texto: False."""
    texto = (
        "Factura EPE\n"
        "Total $ 77.597,44\n"
        "Vencimiento 13/10/2026\n"
    )
    res = ResultadoExtraccion(
        documento_tipo="factura_servicio",
        movimientos=[
            MovimientoExtraido(
                fecha=date(2026, 10, 13),
                monto=Decimal("77597.44"),
                moneda="ARS",
                descripcion="EPE",
                sentido="egreso",
                categoria=None,
            )
        ],
        billetera_texto=None,
        vencimiento=date(2026, 10, 13),
        total_vistos=1,
        cuotas=[
            CuotaExtraida(vencimiento=date(2026, 10, 13), monto=Decimal("77597.44")),
            CuotaExtraida(vencimiento=date(2026, 11, 25), monto=Decimal("77597.44")),
        ],
    )
    assert verificar_extraccion_contra_texto(res, texto) is False


def test_fecha_solo_fiscal():
    """C2: Pruebas de fecha_solo_fiscal con texto sintético."""
    from app.routers.whatsapp.pdf_documento import fecha_solo_fiscal

    texto_sintetico = (
        "TOTAL A PAGAR hasta el 13/10/2026\n"
        "C.E.S.P. Nro.: 37390006516961\n"
        "F.Vto.: 07/10/2026\n"
        "C.A.E. Nº 86373133575314 Fecha Vto. C.A.E. 24/09/2026"
    )

    # 07/10/2026: True
    assert fecha_solo_fiscal("07/10/2026", texto_sintetico) is True
    assert fecha_solo_fiscal(date(2026, 10, 7), texto_sintetico) is True

    # 13/10/2026: False
    assert fecha_solo_fiscal("13/10/2026", texto_sintetico) is False
    assert fecha_solo_fiscal(date(2026, 10, 13), texto_sintetico) is False

    # 24/09/2026: True
    assert fecha_solo_fiscal("24/09/2026", texto_sintetico) is True
    assert fecha_solo_fiscal(date(2026, 9, 24), texto_sintetico) is True

    # una fecha ausente: False
    assert fecha_solo_fiscal("01/01/2026", texto_sintetico) is False
    assert fecha_solo_fiscal(date(2026, 1, 1), texto_sintetico) is False

    # una fecha que aparece una vez junto a C.E.S.P. y otra después de "Vencimiento:": False
    texto_mixto = (
        "C.E.S.P. Nro.: 37390006516961\n"
        "F.Vto.: 07/10/2026\n"
        "Vencimiento: 07/10/2026\n"
    )
    assert fecha_solo_fiscal("07/10/2026", texto_mixto) is False
    assert fecha_solo_fiscal(date(2026, 10, 7), texto_mixto) is False



