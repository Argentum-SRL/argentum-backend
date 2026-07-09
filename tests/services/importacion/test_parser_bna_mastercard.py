import json
import os
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from app.services.importacion.utils import sanitizar_texto_pdf
from app.services.importacion.parser_bna_mastercard import parsear_bna_mastercard, parse_bna_mastercard_date
from app.services.importacion.schemas import ResultadoParseo


def test_parse_bna_mastercard_date():
    """
    Verifica que la conversión de fechas en texto funcione correctamente.
    """
    assert parse_bna_mastercard_date("21-May-26") == date(2026, 5, 21)
    assert parse_bna_mastercard_date("23-Abr-26") == date(2026, 4, 23)
    assert parse_bna_mastercard_date("06-May-2026") == date(2026, 5, 6)
    assert parse_bna_mastercard_date("") is None
    assert parse_bna_mastercard_date("invalida") is None


def test_sanitizar_texto_pdf():
    """
    Verifica que la función sanitizar_texto_pdf elimine domicilio, CUIT, DNI y cuenta
    mientras preserva los nombres de los titulares.
    """
    texto_simulado = """FORMOSO LIA FERNANDA HOJA 1/4
SALTA 1226
CUIT Entidad 30-50001091-2
02520 LAS ROSAS MASTERCARD BLACK
N° de Socio 071-0362853-0-2
CONSUMIDOR FINAL
Vencimiento actual : 03-Jun-26
Debitaremos de su c.ahorro 3312423929 el importe
DNI 12.345.678
JUAN PEREZ
"""
    texto_sanitizado = sanitizar_texto_pdf(texto_simulado)
    
    # Verificar que elimina datos sensibles
    assert "SALTA 1226" not in texto_sanitizado
    assert "30-50001091-2" not in texto_sanitizado
    assert "071-0362853-0-2" not in texto_sanitizado
    assert "3312423929" not in texto_sanitizado
    assert "12.345.678" not in texto_sanitizado
    
    # Verificar que preserva nombres e información funcional
    assert "FORMOSO LIA" in texto_sanitizado
    assert "JUAN PEREZ" in texto_sanitizado
    assert "MASTERCARD BLACK" in texto_sanitizado


def test_parsear_bna_mastercard_empty_or_corrupt():
    """
    Verifica el comportamiento seguro ante inputs de PDF vacíos o dañados.
    """
    # PDF vacío
    res = parsear_bna_mastercard(b"")
    assert isinstance(res, ResultadoParseo)
    assert res.banco == "bna_mastercard"
    assert res.confianza == 0.0
    assert len(res.transacciones) == 0

    # PDF corrupto
    res_corrupt = parsear_bna_mastercard(b"this is a corrupt pdf statement")
    assert isinstance(res_corrupt, ResultadoParseo)
    assert res_corrupt.banco == "bna_mastercard"
    assert res_corrupt.confianza == 0.0
    assert len(res_corrupt.transacciones) == 0


def test_parsear_bna_mastercard_with_mock():
    """
    Verifica la llamada a OpenAI y el correcto mapeo de la respuesta a TransaccionCruda,
    además de verificar que la sanitización se aplica al texto enviado y las exclusiones al prompt.
    """
    pdf_text_simulado = """FORMOSO LIA FERNANDA HOJA 1/4
SALTA 1226
CUIT Entidad 30-50001091-2
02520 LAS ROSAS MASTERCARD BLACK
N° de Socio 071-0362853-0-2
05-May-26 PAGO CAJERO/INTERNET -1888000,00
13-May-26 MOD*TIENDABNA 01/24 09555 70445,86
19-May-26 COMPRA EN USD -10,00
21-May-26 IMPUESTO DE SELLOS 1.270,91
"""
    
    mock_response_json = {
        "transacciones": [
            {
                "fecha": "2026-05-13",
                "descripcion": "MOD*TIENDABNA",
                "monto": 70445.86,
                "moneda": "ARS",
                "cuota_actual": 1,
                "cuota_total": 24,
                "es_cargo_bancario": False,
                "titular_seccion": "FORMOSO LIA FERNANDA"
            },
            {
                "fecha": "2026-05-19",
                "descripcion": "COMPRA EN USD",
                "monto": -10.00,
                "moneda": "USD",
                "cuota_actual": None,
                "cuota_total": None,
                "es_cargo_bancario": False,
                "titular_seccion": "FORMOSO LIA FERNANDA"
            },
            {
                "fecha": "2026-05-21",
                "descripcion": "IMPUESTO DE SELLOS",
                "monto": 1270.91,
                "moneda": "ARS",
                "cuota_actual": None,
                "cuota_total": None,
                "es_cargo_bancario": True,
                "titular_seccion": "FORMOSO LIA FERNANDA"
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
         patch("app.services.importacion.parser_bna_mastercard.get_openai_client", return_value=mock_client):
        
        res = parsear_bna_mastercard(b"dummy pdf bytes")
        
        # 1. Verificar llamada a OpenAI
        assert mock_client.chat.completions.create.called
        call_args = mock_client.chat.completions.create.call_args[1]
        
        assert call_args["model"] == "gpt-4o-mini"
        assert call_args["max_tokens"] == 6000
        assert call_args["temperature"] == 0.1
        
        messages = call_args["messages"]
        system_content = messages[0]["content"]
        user_content = messages[1]["content"]
        
        # 2. Verificar sanitización
        assert "SALTA 1226" not in user_content
        assert "30-50001091-2" not in user_content
        assert "071-0362853-0-2" not in user_content
        
        # 3. Verificar instrucciones de exclusión en system prompt
        assert "SU PAGO" in system_content or "PAGO" in system_content
        assert "DEV PER RG" in system_content or "DEV" in system_content
        
        # 4. Verificar resultado final del parseo
        assert res.banco == "bna_mastercard"
        assert res.titular_detectado == "FORMOSO LIA FERNANDA"
        assert len(res.transacciones) == 3
        
        # Cuota
        t_cuota = next(t for t in res.transacciones if t.descripcion == "MOD*TIENDABNA")
        assert t_cuota.monto == Decimal("70445.86")
        assert t_cuota.cuota_actual == 1
        assert t_cuota.cuota_total == 24
        assert t_cuota.moneda == "ARS"
        assert t_cuota.es_cargo_bancario is False
        
        # Reversión
        t_rev = next(t for t in res.transacciones if t.descripcion == "COMPRA EN USD")
        assert t_rev.monto == Decimal("-10.00")
        assert t_rev.moneda == "USD"
        assert t_rev.es_cargo_bancario is False
        
        # Cargo bancario
        t_cargo = next(t for t in res.transacciones if t.descripcion == "IMPUESTO DE SELLOS")
        assert t_cargo.monto == Decimal("1270.91")
        assert t_cargo.es_cargo_bancario is True
        
        # 5. Confirmar que no hay transacciones de pago en el resultado
        assert not any("PAGO CAJERO" in t.descripcion for t in res.transacciones)


# Test de integración real marcado con skipif
REAL_PDF_PATH = "tests/fixtures/bna_mastercard_sample.pdf"


@pytest.mark.skipif(
    not os.path.exists(REAL_PDF_PATH) or not os.getenv("OPENAI_API_KEY"),
    reason="Archivo bna_mastercard_sample.pdf no encontrado o falta OPENAI_API_KEY en el entorno."
)
def test_parsear_bna_mastercard_real():
    """
    Test de integración real que ejecuta el pipeline completo contra el PDF de muestra.
    Solo corre si el archivo de fixture real existe y se cuenta con la clave de OpenAI.
    """
    with open(REAL_PDF_PATH, "rb") as f:
        pdf_bytes = f.read()
        
    res = parsear_bna_mastercard(pdf_bytes)
    
    assert res.banco == "bna_mastercard"
    assert res.titular_detectado == "FORMOSO LIA FERNANDA"
    assert res.periodo_desde == date(2026, 4, 23)
    assert res.periodo_hasta == date(2026, 5, 21)
    assert res.confianza >= 0.7
    assert len(res.transacciones) > 0
    
    # Comprobar tipos de datos y exclusiones
    for t in res.transacciones:
        assert isinstance(t.monto, Decimal)
        assert isinstance(t.fecha, date)
        assert t.moneda in ("ARS", "USD")
        assert not any(pago_keyword in t.descripcion.upper() for pago_keyword in ("SU PAGO", "PAGO CAJERO"))
        assert not any(dev_keyword in t.descripcion.upper() for dev_keyword in ("DEV PER", "DEV RG"))
