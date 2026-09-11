import os
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from app.services.importacion.utils import detectar_banco
from app.services.importacion.parser_bna_visa import parsear_bna_visa, parse_bna_date
from app.services.importacion.schemas import ResultadoParseo


def test_parse_bna_date():
    """
    Verifica que la función parse_bna_date convierta correctamente strings de fechas
    en sus correspondientes objetos date de Python.
    """
    # Formato con puntos
    assert parse_bna_date("13.05.25") == date(2025, 5, 13)
    assert parse_bna_date("05.05.2026") == date(2026, 5, 5)

    # Formato con meses abreviados en palabras
    assert parse_bna_date("21 May 26") == date(2026, 5, 21)
    assert parse_bna_date("23 Abr 26") == date(2026, 4, 23)
    assert parse_bna_date("06 May 26") == date(2026, 5, 6)

    # Casos inválidos
    assert parse_bna_date("") is None
    assert parse_bna_date("fecha invalida") is None


def test_parsear_bna_visa_empty_or_corrupt():
    """
    Verifica que la función parsear_bna_visa maneje de forma segura y defensiva
    las excepciones y los inputs vacíos/corruptos, retornando confianza 0.0 sin propagar errores.
    """
    # PDF vacío
    res = parsear_bna_visa(b"")
    assert isinstance(res, ResultadoParseo)
    assert res.banco == "bna_visa"
    assert res.confianza == 0.0
    assert len(res.transacciones) == 0

    # PDF corrupto
    res_corrupt = parsear_bna_visa(b"this is not a valid pdf file")
    assert isinstance(res_corrupt, ResultadoParseo)
    assert res_corrupt.banco == "bna_visa"
    assert res_corrupt.confianza == 0.0
    assert len(res_corrupt.transacciones) == 0


def test_parsear_bna_visa_mocked_logic():
    """
    Verifica el comportamiento del parser BNA Visa usando texto mockeado con datos sintéticos.
    Permite validar la extracción de titular, tarjeta, fechas de cierre, consumos en cuotas,
    cargos bancarios y exclusión de pagos sin depender de archivos físicos en disco.
    """
    sample_text = """
    BANCO DE LA NACION ARGENTINA - VISA SIGNATURE
    CIERRE ACTUAL: 21 May 26
    CIERRE ANTERIOR 23 Abr 26
    TARJETA 4567 Total Consumos de MARIA ELENA ALVAREZ 193583.84 0.00
    13.05.25 ELECTRO HOGAR C.13/24 27.429,00 0,00
    23.02.26 INDUMENTARIA MODA C.03/03 53.333,33 0,00
    11.05.26 FARMACIA CENTRAL 52.721,51 0,00
    15.05.26 LIBRERIA NACIONAL 60.000,00 0,00
    21.05.26 IMPUESTO DE SELLOS 193,48 0,00
    SU PAGO EN PESOS -100.000,00 0,00
    """

    with patch("pdfplumber.open") as mock_open:
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = sample_text
        mock_pdf.pages = [mock_page]
        mock_open.return_value.__enter__.return_value = mock_pdf

        res = parsear_bna_visa(b"dummy pdf bytes")

        assert res.banco == "bna_visa"
        assert res.titular_detectado == "MARIA ELENA ALVAREZ"
        assert res.ultimos_4_digitos == "4567"
        assert res.periodo_desde == date(2026, 4, 23)
        assert res.periodo_hasta == date(2026, 5, 21)
        assert res.confianza == 0.95
        assert res.capa_usada == "deterministic"

        # 4 consumos + 1 cargo bancario (excluyendo SU PAGO EN PESOS)
        assert len(res.transacciones) == 5

        consumos = [t for t in res.transacciones if not t.es_cargo_bancario]
        cargos = [t for t in res.transacciones if t.es_cargo_bancario]

        assert len(consumos) == 4
        assert len(cargos) == 1

        # 1. ELECTRO HOGAR
        electro = next(t for t in consumos if "ELECTRO HOGAR" in t.descripcion)
        assert electro.fecha == date(2025, 5, 13)
        assert electro.cuota_actual == 13
        assert electro.cuota_total == 24
        assert electro.monto == Decimal("27429.00")
        assert electro.moneda == "ARS"

        # 2. INDUMENTARIA MODA
        moda = next(t for t in consumos if "INDUMENTARIA MODA" in t.descripcion)
        assert moda.fecha == date(2026, 2, 23)
        assert moda.cuota_actual == 3
        assert moda.cuota_total == 3
        assert moda.monto == Decimal("53333.33")
        assert moda.moneda == "ARS"

        # 3. FARMACIA CENTRAL
        farmacia = next(t for t in consumos if "FARMACIA CENTRAL" in t.descripcion)
        assert farmacia.fecha == date(2026, 5, 11)
        assert farmacia.cuota_actual is None
        assert farmacia.monto == Decimal("52721.51")
        assert farmacia.moneda == "ARS"

        # 4. LIBRERIA NACIONAL
        libreria = next(t for t in consumos if "LIBRERIA NACIONAL" in t.descripcion)
        assert libreria.fecha == date(2026, 5, 15)
        assert libreria.monto == Decimal("60000.00")
        assert libreria.moneda == "ARS"

        # 5. IMPUESTO DE SELLOS
        impuesto = cargos[0]
        assert impuesto.fecha == date(2026, 5, 21)
        assert impuesto.descripcion == "IMPUESTO DE SELLOS"
        assert impuesto.monto == Decimal("193.48")
        assert impuesto.es_cargo_bancario is True

        # Confirmar exclusión de pagos
        assert not any("PAGO" in t.descripcion.upper() for t in res.transacciones)


# Ruta del PDF de pruebas de integración si existe localmente
REAL_PDF_PATH = "tests/fixtures/bna_visa_sample.pdf"


@pytest.mark.skipif(not os.path.exists(REAL_PDF_PATH), reason="Archivo bna_visa_sample.pdf no encontrado en fixtures.")
def test_parsear_bna_visa_real_pdf():
    """
    Test de integración con PDF si está presente en fixtures.
    """
    with open(REAL_PDF_PATH, "rb") as f:
        pdf_bytes = f.read()

    res = parsear_bna_visa(pdf_bytes)

    assert res.banco == "bna_visa"
    assert res.titular_detectado is not None
    assert res.ultimos_4_digitos is not None
    assert res.confianza == 0.95
    assert len(res.transacciones) > 0
    assert not any("PAGO" in t.descripcion.upper() for t in res.transacciones)
