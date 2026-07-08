import os
from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock
import pytest

from app.services.importacion.utils import detectar_banco
from app.services.importacion.parser_galicia import parsear_galicia
from app.services.importacion.schemas import ResultadoParseo


def test_detectar_banco():
    """
    Verifica que la función detectar_banco identifique correctamente el banco emisor
    basándose en las palabras clave presentes en el texto del PDF.
    """
    # Galicia
    assert detectar_banco("Este es un resumen de Banco Galicia para el cliente.") == "galicia"
    assert detectar_banco("Resumen de cuenta bancogalicia Visa Gold") == "galicia"
    
    # BNA Visa
    assert detectar_banco("Banco de la Nación Argentina - VISA SIGNATURE") == "bna_visa"
    assert detectar_banco("bna VISA SIGNATURE resumen mensual") == "bna_visa"
    
    # BNA Mastercard
    assert detectar_banco("Banco Nacion MASTERCARD Black") == "bna_mastercard"
    assert detectar_banco("Resumen BNA - MASTERCARD Internacional") == "bna_mastercard"
    
    # Genérico / Vacío
    assert detectar_banco("Otro banco diferente sin palabras clave") == "generico"
    assert detectar_banco("") == "generico"


def test_parsear_galicia_empty_or_corrupt():
    """
    Verifica que la función parsear_galicia maneje de forma totalmente segura y defensiva
    las excepciones y los inputs vacíos/corruptos, retornando confianza 0.0 sin propagar errores.
    """
    # PDF vacío
    res = parsear_galicia(b"")
    assert isinstance(res, ResultadoParseo)
    assert res.banco == "galicia"
    assert res.confianza == 0.0
    assert len(res.transacciones) == 0

    # PDF corrupto
    res_corrupt = parsear_galicia(b"this is not a valid pdf")
    assert isinstance(res_corrupt, ResultadoParseo)
    assert res_corrupt.banco == "galicia"
    assert res_corrupt.confianza == 0.0
    assert len(res_corrupt.transacciones) == 0


def test_parsear_galicia_mocked_logic():
    """
    Verifica el comportamiento del parser Galicia usando texto mockeado de pdfplumber.
    Permite validar la lógica de parseo, extracción de cuotas, limpieza de descripción y cargos
    sin depender de la presencia física de la fixture PDF en disco.
    """
    sample_text = """
    BANCO GALICIA
    DETALLE DE CARGOS EN PESOS
    DESDE EL 20-04-24 HASTA EL 20-05-24
    TARJETA 1506 Total Consumos de MANUE ROBA MARTINEZ
    15-05-24 *GRAELLS NELSON 03/03 12345678 60.866,66
    16-05-24 KLA ANONIMA 98765432 1.500,00
    17-05-24 *OTRO COMERCIO 11223344 350,50
    18-05-24 IMPUESTO DE SELLOS 545,20
    SU PAGO EN PESOS -15.000,00
    """
    
    with patch("pdfplumber.open") as mock_open:
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = sample_text
        mock_pdf.pages = [mock_page]
        mock_open.return_value.__enter__.return_value = mock_pdf
        
        res = parsear_galicia(b"dummy pdf bytes")
        
        assert res.banco == "galicia"
        assert res.titular_detectado == "MANUE ROBA MARTINEZ"
        assert res.ultimos_4_digitos == "1506"
        assert res.periodo_desde == date(2024, 4, 20)
        assert res.periodo_hasta == date(2024, 5, 20)
        assert res.confianza == 0.95
        
        # Deben haber 4 transacciones (3 consumos + 1 impuesto)
        # Excluyendo el pago "SU PAGO EN PESOS"
        assert len(res.transacciones) == 4
        
        consumos = [t for t in res.transacciones if not t.es_cargo_bancario]
        cargos = [t for t in res.transacciones if t.es_cargo_bancario]
        
        assert len(consumos) == 3
        assert len(cargos) == 1
        
        # Verificar detalles de GRAELLS NELSON
        graells = next(t for t in consumos if t.descripcion == "GRAELLS NELSON")
        assert graells.fecha == date(2024, 5, 15)
        assert graells.cuota_actual == 3
        assert graells.cuota_total == 3
        assert graells.monto == Decimal("60866.66")
        assert graells.moneda == "ARS"
        
        # Verificar detalles de LA ANONIMA (limpieza del prefijo K)
        anonima = next(t for t in consumos if "ANONIMA" in t.descripcion)
        assert anonima.descripcion == "LA ANONIMA"
        assert anonima.monto == Decimal("1500.00")
        
        # Verificar impuesto
        impuesto = cargos[0]
        assert impuesto.descripcion == "IMPUESTO DE SELLOS"
        assert impuesto.monto == Decimal("545.20")
        assert impuesto.es_cargo_bancario is True


# Ruta del PDF real de pruebas
REAL_PDF_PATH = "tests/fixtures/galicia_visa_sample.pdf"

@pytest.mark.skipif(not os.path.exists(REAL_PDF_PATH), reason="Archivo galicia_visa_sample.pdf no encontrado en fixtures.")
def test_parsear_galicia_real_pdf():
    """
    Test de integración con el PDF real si está presente en la carpeta de fixtures.
    """
    with open(REAL_PDF_PATH, "rb") as f:
        pdf_bytes = f.read()
        
    res = parsear_galicia(pdf_bytes)
    
    assert res.banco == "galicia"
    assert res.titular_detectado == "MANUE ROBA MARTINEZ"
    assert res.ultimos_4_digitos == "1506"
    assert res.confianza == 0.95
    
    # 3 transacciones de consumo + 1 cargo (Impuesto de sellos)
    assert len(res.transacciones) == 4
    
    consumos = [t for t in res.transacciones if not t.es_cargo_bancario]
    cargos = [t for t in res.transacciones if t.es_cargo_bancario]
    
    assert len(consumos) == 3
    assert len(cargos) == 1
    
    graells = next(t for t in consumos if "GRAELLS" in t.descripcion)
    assert graells.descripcion == "GRAELLS NELSON"
    assert graells.cuota_actual == 3
    assert graells.cuota_total == 3
    assert isinstance(graells.monto, Decimal)
