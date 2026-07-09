import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from app.services.importacion.parser_generico import parsear_generico
from app.services.importacion.schemas import ResultadoParseo


def test_parsear_generico_empty_or_corrupt():
    """
    Verifica el comportamiento seguro ante inputs de PDF vacíos o dañados.
    """
    # PDF vacío
    res = parsear_generico(b"")
    assert isinstance(res, ResultadoParseo)
    assert res.banco == "generico"
    assert res.confianza == 0.0
    assert len(res.transacciones) == 0

    # PDF corrupto con texto menor a 100 caracteres
    res_corrupt = parsear_generico(b"Short corrupt PDF")
    assert isinstance(res_corrupt, ResultadoParseo)
    assert res_corrupt.banco == "generico"
    assert res_corrupt.confianza == 0.0
    assert len(res_corrupt.transacciones) == 0


def test_parsear_generico_under_100_chars_no_openai():
    """
    Verifica que si el texto extraído es menor a 100 caracteres, no se invoque a OpenAI
    y se retorne directamente confianza = 0.0.
    """
    pdf_text_simulado = "Este texto es muy corto. Tiene menos de cien caracteres."
    
    mock_page = MagicMock()
    mock_page.extract_text.return_value = pdf_text_simulado
    mock_pdf = MagicMock()
    mock_pdf.__enter__.return_value = mock_pdf
    mock_pdf.pages = [mock_page]
    
    mock_client = MagicMock()
    
    with patch("pdfplumber.open", return_value=mock_pdf), \
         patch("app.services.importacion.parser_generico.get_openai_client", return_value=mock_client):
        
        res = parsear_generico(b"dummy pdf bytes")
        
        # Verificar que NO se llamó al cliente de OpenAI
        mock_client.chat.completions.create.assert_not_called()
        
        # Verificar resultado
        assert res.banco == "generico"
        assert res.confianza == 0.0
        assert len(res.transacciones) == 0
        assert res.capa_usada == "llm_text_generico"


def test_parsear_generico_with_mock():
    """
    Verifica la llamada a OpenAI, la sanitización del texto de entrada,
    y el correcto mapeo de la respuesta JSON a las clases ResultadoParseo y TransaccionCruda.
    """
    # Texto simulado con datos personales (PII) para verificar sanitización
    pdf_text_simulado = """JUAN SEBASTIAN PEREZ HOJA 1/4
    AV CORRIENTES 1234
    CUIT: 20-12345678-9
    Nro de Socio 987-654321-0
    DNI 30.123.456
    
    Consumos del mes:
    10/05/2026 COMPRA SUPERMERCADO C02/06 15.000,00
    12/05/2026 AMAZON US 45,99 (USD)
    15/05/2026 IMPUESTO PAIS 4.500,00
    18/05/2026 PAGO CAJERO AUTOMATICO -15.000,00
    """
    
    mock_response_json = {
        "titular_detectado": "JUAN SEBASTIAN PEREZ",
        "ultimos_4_digitos": "4321",
        "transacciones": [
            {
                "fecha": "2026-05-10",
                "descripcion": "COMPRA SUPERMERCADO",
                "monto": 15000.00,
                "moneda": "ARS",
                "cuota_actual": 2,
                "cuota_total": 6,
                "es_cargo_bancario": False,
                "titular_seccion": "JUAN SEBASTIAN PEREZ"
            },
            {
                "fecha": "2026-05-12",
                "descripcion": "AMAZON US",
                "monto": 45.99,
                "moneda": "USD",
                "cuota_actual": None,
                "cuota_total": None,
                "es_cargo_bancario": False,
                "titular_seccion": "JUAN SEBASTIAN PEREZ"
            },
            {
                "fecha": "2026-05-15",
                "descripcion": "IMPUESTO PAIS",
                "monto": 4500.00,
                "moneda": "ARS",
                "cuota_actual": None,
                "cuota_total": None,
                "es_cargo_bancario": True,
                "titular_seccion": None
            }
        ]
    }
    
    mock_page = MagicMock()
    mock_page.extract_text.return_value = pdf_text_simulado
    mock_pdf = MagicMock()
    mock_pdf.__enter__.return_value = mock_pdf
    mock_pdf.pages = [mock_page]
    
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_response_json)
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    with patch("pdfplumber.open", return_value=mock_pdf), \
         patch("app.services.importacion.parser_generico.get_openai_client", return_value=mock_client):
        
        # Debemos usar un binario simulado de más de 100 caracteres para pasar el control de tamaño
        dummy_bytes = b"dummy bytes to simulate a sufficiently large PDF file " * 5
        res = parsear_generico(dummy_bytes)
        
        # 1. Verificar llamada a OpenAI
        assert mock_client.chat.completions.create.called
        call_args = mock_client.chat.completions.create.call_args[1]
        
        assert call_args["model"] == "gpt-4o-mini"
        assert call_args["max_tokens"] == 6000
        assert call_args["temperature"] == 0.1
        
        messages = call_args["messages"]
        user_content = messages[1]["content"]
        
        # 2. Verificar que se aplicó la sanitización en el texto enviado al LLM
        assert "AV CORRIENTES 1234" not in user_content
        assert "20-12345678-9" not in user_content
        assert "30.123.456" not in user_content
        assert "987-654321-0" not in user_content
        
        # El nombre del titular debe preservarse
        assert "JUAN SEBASTIAN PEREZ" in user_content
        
        # 3. Verificar mapeo del resultado
        assert res.banco == "generico"
        assert res.titular_detectado == "JUAN SEBASTIAN PEREZ"
        assert res.ultimos_4_digitos == "4321"
        assert res.confianza == 0.5  # Confianza fija de 0.5 para el parser genérico
        assert res.capa_usada == "llm_text_generico"
        assert len(res.transacciones) == 3
        
        # Compra cuotificada en ARS
        t1 = res.transacciones[0]
        assert t1.fecha == date(2026, 5, 10)
        assert t1.descripcion == "COMPRA SUPERMERCADO"
        assert t1.monto == Decimal("15000.00")
        assert t1.moneda == "ARS"
        assert t1.cuota_actual == 2
        assert t1.cuota_total == 6
        assert t1.es_cargo_bancario is False
        assert t1.titular_seccion == "JUAN SEBASTIAN PEREZ"
        
        # Compra en USD
        t2 = res.transacciones[1]
        assert t2.fecha == date(2026, 5, 12)
        assert t2.descripcion == "AMAZON US"
        assert t2.monto == Decimal("45.99")
        assert t2.moneda == "USD"
        assert t2.es_cargo_bancario is False
        
        # Cargo bancario
        t3 = res.transacciones[2]
        assert t3.fecha == date(2026, 5, 15)
        assert t3.descripcion == "IMPUESTO PAIS"
        assert t3.monto == Decimal("4500.00")
        assert t3.moneda == "ARS"
        assert t3.es_cargo_bancario is True
