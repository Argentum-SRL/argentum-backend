import os
from datetime import date
from decimal import Decimal
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


# Ruta del PDF real de pruebas
REAL_PDF_PATH = "tests/fixtures/bna_visa_sample.pdf"


@pytest.mark.skipif(not os.path.exists(REAL_PDF_PATH), reason="Archivo bna_visa_sample.pdf no encontrado en fixtures.")
def test_parsear_bna_visa_real_pdf():
    """
    Test de integración con el PDF real de BNA Visa, verificando
    que se extraigan correctamente los metadatos y las transacciones esperadas.
    """
    with open(REAL_PDF_PATH, "rb") as f:
        pdf_bytes = f.read()

    res = parsear_bna_visa(pdf_bytes)

    assert res.banco == "bna_visa"
    assert res.titular_detectado == "LIA FERNAND FORMOSO"
    assert res.ultimos_4_digitos == "6841"
    assert res.periodo_desde == date(2026, 4, 23)
    assert res.periodo_hasta == date(2026, 5, 21)
    assert res.confianza == 0.95
    assert res.capa_usada == "deterministic"

    # Esperamos exactamente 4 transacciones de consumo + 1 cargo bancario
    # Excluyendo "SU PAGO EN PESOS"
    assert len(res.transacciones) == 5

    consumos = [t for t in res.transacciones if not t.es_cargo_bancario]
    cargos = [t for t in res.transacciones if t.es_cargo_bancario]

    assert len(consumos) == 4
    assert len(cargos) == 1

    # 1. ARGENTINA COLOR
    argentina_color = next(t for t in consumos if "ARGENTINA COLOR" in t.descripcion)
    assert argentina_color.fecha == date(2025, 5, 13)
    assert argentina_color.descripcion == "ARGENTINA COLOR"
    assert argentina_color.cuota_actual == 13
    assert argentina_color.cuota_total == 24
    assert argentina_color.monto == Decimal("27429.00")
    assert argentina_color.moneda == "ARS"

    # 2. GRAELLS NELSON
    graells = next(t for t in consumos if "GRAELLS" in t.descripcion)
    assert graells.fecha == date(2026, 2, 23)
    assert graells.descripcion == "GRAELLS NELSON"
    assert graells.cuota_actual == 3
    assert graells.cuota_total == 3
    assert graells.monto == Decimal("53333.33")
    assert graells.moneda == "ARS"

    # 3. FARMAONLINE
    farmaonline = next(t for t in consumos if "FARMAONLINE" in t.descripcion)
    assert farmaonline.fecha == date(2026, 5, 11)
    assert farmaonline.descripcion == "FARMAONLINE"
    assert farmaonline.cuota_actual is None
    assert farmaonline.cuota_total is None
    assert farmaonline.monto == Decimal("52721.51")
    assert farmaonline.moneda == "ARS"

    # 4. LA EXCELENCIA SRL
    excelencia = next(t for t in consumos if "EXCELENCIA" in t.descripcion)
    assert excelencia.fecha == date(2026, 5, 15)
    assert excelencia.descripcion == "LA EXCELENCIA SRL"
    assert excelencia.cuota_actual is None
    assert excelencia.cuota_total is None
    assert excelencia.monto == Decimal("60000.00")
    assert excelencia.moneda == "ARS"

    # 5. IMPUESTO DE SELLOS (cargo bancario)
    impuesto = cargos[0]
    assert impuesto.fecha == date(2026, 5, 21)
    assert impuesto.descripcion == "IMPUESTO DE SELLOS"
    assert impuesto.cuota_actual is None
    assert impuesto.cuota_total is None
    assert impuesto.monto == Decimal("193.48")
    assert impuesto.moneda == "ARS"
    assert impuesto.es_cargo_bancario is True

    # Verificar que no hay ningún pago ("SU PAGO EN PESOS")
    assert not any("PAGO" in t.descripcion.upper() for t in res.transacciones)
