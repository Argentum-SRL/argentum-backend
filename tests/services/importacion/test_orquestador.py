from unittest.mock import patch, MagicMock
from app.services.importacion.orquestador import procesar_resumen
from app.services.importacion.schemas import ResultadoParseo


def test_orquestador_exito_especifico_confianza_alta():
    """
    Verifica que si se detecta un banco específico (galicia) y el parser específico
    devuelve un resultado con confianza >= 0.4, se retorna ese resultado directamente,
    y NO se invoca al parser genérico de fallback.
    """
    mock_res = ResultadoParseo(
        banco="galicia",
        confianza=0.8,
        capa_usada="deterministic",
        escalado=False
    )
    
    with patch("app.services.importacion.orquestador.extraer_texto_pdf", return_value="Banco Galicia") as mock_extract, \
         patch("app.services.importacion.orquestador.detectar_banco", return_value="galicia") as mock_detect, \
         patch("app.services.importacion.orquestador.parsear_galicia", return_value=mock_res) as mock_galicia, \
         patch("app.services.importacion.orquestador.parsear_generico") as mock_generico:
         
        res = procesar_resumen(b"pdf_dummy_bytes")
        
        assert res.banco == "galicia"
        assert res.confianza == 0.8
        assert res.escalado is False
        
        mock_extract.assert_called_once_with(b"pdf_dummy_bytes")
        mock_detect.assert_called_once_with("Banco Galicia")
        mock_galicia.assert_called_once_with(b"pdf_dummy_bytes")
        mock_generico.assert_not_called()


def test_orquestador_banco_desconocido_directo_a_generico():
    """
    Verifica que si el banco no es reconocido (devuelve 'generico' o similar),
    se invoca directamente al parser genérico, sin llamar a los parsers específicos.
    """
    mock_res = ResultadoParseo(
        banco="generico",
        confianza=0.7,
        capa_usada="llm_text_generico",
        escalado=False
    )
    
    with patch("app.services.importacion.orquestador.extraer_texto_pdf", return_value="Banco Macro") as mock_extract, \
         patch("app.services.importacion.orquestador.detectar_banco", return_value="generico") as mock_detect, \
         patch("app.services.importacion.orquestador.parsear_galicia") as mock_galicia, \
         patch("app.services.importacion.orquestador.parsear_generico", return_value=mock_res) as mock_generico:
         
        res = procesar_resumen(b"pdf_dummy_bytes")
        
        assert res.banco == "generico"
        assert res.confianza == 0.7
        assert res.escalado is False
        
        mock_extract.assert_called_once_with(b"pdf_dummy_bytes")
        mock_detect.assert_called_once_with("Banco Macro")
        mock_generico.assert_called_once_with(b"pdf_dummy_bytes")
        mock_galicia.assert_not_called()


def test_orquestador_especifico_falla_y_escala_a_generico():
    """
    Verifica que si el parser específico devuelve baja confianza (< 0.4),
    se escala automáticamente al parser genérico como fallback y el resultado
    final tiene escalado=True.
    """
    mock_res_galicia = ResultadoParseo(
        banco="galicia",
        confianza=0.2,
        capa_usada="deterministic",
        escalado=False
    )
    mock_res_generico = ResultadoParseo(
        banco="generico",
        confianza=0.9,
        capa_usada="llm_text_generico",
        escalado=False
    )
    
    with patch("app.services.importacion.orquestador.extraer_texto_pdf", return_value="Banco Galicia") as mock_extract, \
         patch("app.services.importacion.orquestador.detectar_banco", return_value="galicia") as mock_detect, \
         patch("app.services.importacion.orquestador.parsear_galicia", return_value=mock_res_galicia) as mock_galicia, \
         patch("app.services.importacion.orquestador.parsear_generico", return_value=mock_res_generico) as mock_generico:
         
        res = procesar_resumen(b"pdf_dummy_bytes")
        
        assert res.banco == "generico"
        assert res.confianza == 0.9
        assert res.escalado is True  # Marcado como escalado
        
        mock_extract.assert_called_once_with(b"pdf_dummy_bytes")
        mock_detect.assert_called_once_with("Banco Galicia")
        mock_galicia.assert_called_once_with(b"pdf_dummy_bytes")
        mock_generico.assert_called_once_with(b"pdf_dummy_bytes")


def test_orquestador_ambos_fallan_devuelve_confianza_cero():
    """
    Verifica que si tanto el específico como el genérico fallan (confianza 0.0),
    el resultado final tiene confianza 0.0 y escalado=True, sin entrar en bucle ni lanzar excepciones.
    """
    mock_res_galicia = ResultadoParseo(
        banco="galicia",
        confianza=0.0,
        capa_usada="deterministic",
        escalado=False
    )
    mock_res_generico = ResultadoParseo(
        banco="generico",
        confianza=0.0,
        capa_usada="llm_text_generico",
        escalado=False
    )
    
    with patch("app.services.importacion.orquestador.extraer_texto_pdf", return_value="Banco Galicia"), \
         patch("app.services.importacion.orquestador.detectar_banco", return_value="galicia"), \
         patch("app.services.importacion.orquestador.parsear_galicia", return_value=mock_res_galicia), \
         patch("app.services.importacion.orquestador.parsear_generico", return_value=mock_res_generico) as mock_generico:
         
        res = procesar_resumen(b"pdf_dummy_bytes")
        
        assert res.confianza == 0.0
        assert res.escalado is True
        mock_generico.assert_called_once_with(b"pdf_dummy_bytes")


def test_orquestador_excepcion_en_parser_especifico_escala_a_generico():
    """
    Verifica que si el parser específico lanza una excepción inesperada,
    el orquestador la maneja y escala al parser genérico.
    """
    mock_res_generico = ResultadoParseo(
        banco="generico",
        confianza=0.75,
        capa_usada="llm_text_generico",
        escalado=False
    )
    
    with patch("app.services.importacion.orquestador.extraer_texto_pdf", return_value="Banco Galicia"), \
         patch("app.services.importacion.orquestador.detectar_banco", return_value="galicia"), \
         patch("app.services.importacion.orquestador.parsear_galicia", side_effect=ValueError("Corrupt pdf error")), \
         patch("app.services.importacion.orquestador.parsear_generico", return_value=mock_res_generico) as mock_generico:
         
        res = procesar_resumen(b"pdf_dummy_bytes")
        
        assert res.banco == "generico"
        assert res.confianza == 0.75
        assert res.escalado is True
        mock_generico.assert_called_once_with(b"pdf_dummy_bytes")


def test_orquestador_excepcion_general_devuelve_cero_desconocido():
    """
    Verifica que si ocurre una excepción inesperada y crítica (por ejemplo,
    si el fallback genérico también lanza una excepción), el orquestador no
    la propaga y en su lugar retorna un ResultadoParseo seguro de confianza 0.0
    y banco 'desconocido'.
    """
    with patch("app.services.importacion.orquestador.extraer_texto_pdf", side_effect=RuntimeError("Fatal read error")):
        res = procesar_resumen(b"pdf_dummy_bytes")
        
        assert res.banco == "desconocido"
        assert res.confianza == 0.0
        assert res.capa_usada == "error"
        assert res.escalado is False


def test_orquestador_bna_visa_exito():
    """
    Verifica que si se detecta BNA Visa se invoque a su parser específico.
    """
    mock_res = ResultadoParseo(
        banco="bna_visa",
        confianza=0.9,
        capa_usada="deterministic"
    )
    with patch("app.services.importacion.orquestador.extraer_texto_pdf", return_value="Banco de la Nación VISA SIGNATURE"), \
         patch("app.services.importacion.orquestador.detectar_banco", return_value="bna_visa"), \
         patch("app.services.importacion.orquestador.parsear_bna_visa", return_value=mock_res) as mock_bna:
         
        res = procesar_resumen(b"pdf_dummy_bytes")
        
        assert res.banco == "bna_visa"
        assert res.confianza == 0.9
        mock_bna.assert_called_once_with(b"pdf_dummy_bytes")


def test_orquestador_bna_mastercard_exito():
    """
    Verifica que si se detecta BNA Mastercard se invoque a su parser específico.
    """
    mock_res = ResultadoParseo(
        banco="bna_mastercard",
        confianza=0.95,
        capa_usada="deterministic"
    )
    with patch("app.services.importacion.orquestador.extraer_texto_pdf", return_value="Banco Nacion MASTERCARD"), \
         patch("app.services.importacion.orquestador.detectar_banco", return_value="bna_mastercard"), \
         patch("app.services.importacion.orquestador.parsear_bna_mastercard", return_value=mock_res) as mock_bna:
         
        res = procesar_resumen(b"pdf_dummy_bytes")
        
        assert res.banco == "bna_mastercard"
        assert res.confianza == 0.95
        mock_bna.assert_called_once_with(b"pdf_dummy_bytes")
