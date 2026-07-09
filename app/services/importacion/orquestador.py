import logging
from app.services.importacion.schemas import ResultadoParseo
from app.services.importacion.utils import detectar_banco, extraer_texto_pdf
from app.services.importacion.parser_galicia import parsear_galicia
from app.services.importacion.parser_bna_visa import parsear_bna_visa
from app.services.importacion.parser_bna_mastercard import parsear_bna_mastercard
from app.services.importacion.parser_generico import parsear_generico

logger = logging.getLogger(__name__)


def procesar_resumen(pdf_bytes: bytes) -> ResultadoParseo:
    """
    Recibe los bytes de un PDF de resumen de tarjeta, detecta de qué banco es, 
    y le pide al parser correcto que lo procese; si ese parser no da un buen 
    resultado, prueba con un parser genérico como último recurso antes de rendirse.

    Esta función garantiza que nunca se lancen excepciones no controladas hacia el exterior,
    retornando en su lugar un resultado con confianza 0.0 y banco 'desconocido'.

    Parámetros:
        pdf_bytes (bytes): Los bytes del archivo PDF cargado.

    Retorna:
        ResultadoParseo: El resultado del procesamiento, incluyendo las transacciones
                         y el nivel de confianza general.
    """
    try:
        # 1. Extraer texto del PDF de manera liviana una sola vez para la detección de banco
        texto_pdf = extraer_texto_pdf(pdf_bytes)

        # 2. Detectar banco
        banco = detectar_banco(texto_pdf)
        logger.info(f"Banco detectado para el resumen: {banco}")

        # 3. Despachar al parser correspondiente
        resultado = None
        parser_inicial = banco

        try:
            if banco == "galicia":
                resultado = parsear_galicia(pdf_bytes)
            elif banco == "bna_visa":
                resultado = parsear_bna_visa(pdf_bytes)
            elif banco == "bna_mastercard":
                resultado = parsear_bna_mastercard(pdf_bytes)
            else:
                # Si no es reconocido, va directo al genérico
                parser_inicial = "generico"
                resultado = parsear_generico(pdf_bytes)
            
            logger.info(f"Procesado inicial completado usando el parser: {parser_inicial}. Confianza: {resultado.confianza}")
        except Exception as e:
            logger.error(f"Error inesperado al ejecutar el parser inicial ({parser_inicial}): {str(e)}", exc_info=True)
            if parser_inicial != "generico":
                # Forzamos una confianza de 0.0 para gatillar el fallback genérico
                resultado = ResultadoParseo(
                    banco=banco,
                    confianza=0.0,
                    capa_usada="deterministic",
                    escalado=False
                )
            else:
                raise

        # 4. Lógica de escalado si la confianza es baja (< 0.4) y el parser no fue el genérico
        if resultado.confianza < 0.4 and parser_inicial != "generico":
            logger.warning(
                f"Confianza baja ({resultado.confianza}) obtenida con el parser de '{banco}'. "
                f"Escalando automáticamente al parser genérico."
            )
            try:
                resultado_generico = parsear_generico(pdf_bytes)
                resultado_generico.escalado = True
                resultado = resultado_generico
                logger.info(f"Procesamiento por escalado genérico finalizado con confianza: {resultado.confianza}")
            except Exception as e_gen:
                logger.error(f"Error al intentar el escalado genérico como fallback: {str(e_gen)}", exc_info=True)
                # Si el fallback genérico también falla, devolvemos el resultado inicial de confianza baja/0.0
                resultado.confianza = 0.0
                resultado.escalado = True

        return resultado

    except Exception as exc:
        logger.critical(f"Error no controlado en el orquestador: {str(exc)}", exc_info=True)
        return ResultadoParseo(
            banco="desconocido",
            confianza=0.0,
            capa_usada="error"
        )
