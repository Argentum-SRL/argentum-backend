"""
app/services/importacion/parser_generico.py — Parser genérico para resúmenes de cualquier banco usando OpenAI.
"""
from datetime import date
from decimal import Decimal
import io
import json
import logging
import pdfplumber

from app.services.importacion.schemas import ResultadoParseo, TransaccionCruda
from app.services.importacion.utils import sanitizar_texto_pdf
from app.services.openai_client import get_openai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sos un experto backend parser de resúmenes de tarjetas de crédito de bancos argentinos (como Macro, Santander, BBVA, Galicia, BNA, etc.). 
Tu objetivo es extraer información estructurada en formato JSON estricto a partir del texto de un resumen cuya estructura es totalmente desconocida.

Debes hacer tu mejor esfuerzo para identificar las transacciones, cargos y metadatos generales del resumen basándote en los siguientes conocimientos del dominio argentino:

1. Fechas: Suelen aparecer en formatos como DD-MM-AA, DD/MM/AA, DD.MM.AA o con el nombre/abreviatura del mes en español (ej: "15-May-26", "10-Oct-25").
2. Montos: En Argentina se suele usar el punto como separador de miles y la coma como separador decimal (ej: 1.234,56). Sin embargo, el texto extraído del PDF puede variar.
3. Cuotas: Busca patrones como "XX/YY", "CXX/YY", "C.XX/YY" en la descripción o en columnas de cuotas. Identifica cuota_actual = XX y cuota_total = YY como números enteros. Remueve la indicación de cuotas de la descripción.
4. Exclusiones críticas:
   - EXCLUIR por completo las líneas de PAGO del usuario (cancelación de deuda, ej: "SU PAGO", "PAGO CAJERO", "PAGO BANCO", "PAGO POR CAJERO AUTOMATICO", "PAGO MIS CUENTAS").
   - EXCLUIR por completo las líneas de DEVOLUCIÓN, REINTEGRO o BONIFICACIÓN de percepciones/impuestos (ej: "DEV PER ...", "REINTEGRO IMP ...", "DEV.PERCEP.RG"). No son transacciones de consumo ni cargos del banco.
   - Ignorar saldos anteriores, subtotales, totales de secciones, etc.
5. Cargos Bancarios: Cualquier cargo del banco como comisiones, mantenimiento, seguro de vida, IVA, impuesto de sellos y percepciones impositivas (ej: "IMPUESTO DE SELLOS", "IVA TASADO 21%", "PERCEP. RG 4815", "PERCEPCION IMPUESTO PAIS") SÍ se debe extraer, marcando es_cargo_bancario = true.
6. Monedas: Puede haber consumos en Pesos Argentinos (ARS) y en Dólares Estadounidenses (USD). Asigna la moneda correspondiente según la columna o indicador de la línea.
7. Titular por Sección (titular_seccion): Si el resumen separa consumos por titular (ej: "Consumos de NOMBRE", "TOTAL ADICIONAL NOMBRE"), asocia ese nombre a cada transacción en el campo titular_seccion.
8. Datos Generales (a nivel raíz del JSON):
   - titular_detectado: Nombre del titular principal de la cuenta del resumen de tarjeta. Suele aparecer en el encabezado o al lado de "TITULAR" o "Consumos de ...".
   - ultimos_4_digitos: Últimos 4 dígitos de la tarjeta de crédito principal.

Formato de Respuesta JSON esperado:
{
  "titular_detectado": "Nombre completo del titular principal o null",
  "ultimos_4_digitos": "Últimos 4 dígitos de la tarjeta o null",
  "transacciones": [
    {
      "fecha": "YYYY-MM-DD",
      "descripcion": "Nombre del comercio o descripción del cargo bancario, limpio sin las cuotas",
      "monto": número decimal,
      "moneda": "ARS" o "USD",
      "cuota_actual": número entero o null,
      "cuota_total": número entero o null,
      "es_cargo_bancario": true o false,
      "titular_seccion": "Nombre del titular/adicional que realizó este consumo específico, o null"
    }
  ]
}
"""


def parsear_generico(pdf_bytes: bytes) -> ResultadoParseo:
    """
    Parsea un resumen de tarjeta de crédito de cualquier banco argentino no reconocido.
    
    Extrae el texto del PDF, aplica sanitización de PII, trunca el texto si excede
    el límite razonable y llama a OpenAI con el modelo gpt-4o-mini para obtener 
    las transacciones y metadatos de forma estructurada.
    
    Parámetros:
        pdf_bytes (bytes): El contenido binario del archivo PDF del resumen.
        
    Retorna:
        ResultadoParseo: El resultado del parseo con las transacciones crudas y metadatos,
                         con confianza = 0.5 si se procesó correctamente, o 0.0 en caso de error.
    """
    try:
        if not pdf_bytes:
            return ResultadoParseo(
                banco="generico",
                confianza=0.0,
                capa_usada="llm_text_generico"
            )

        # 1. Extraer texto completo del PDF con pdfplumber
        texto_original = ""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                texto_original += (page.extract_text() or "") + "\n"

        # 2. Si el texto extraído es menor a 100 caracteres, abortar directamente (ahorro de costos)
        if len(texto_original.strip()) < 100:
            logger.info("El texto extraído del PDF es menor a 100 caracteres. Se asume PDF escaneado/vacío.")
            return ResultadoParseo(
                banco="generico",
                confianza=0.0,
                capa_usada="llm_text_generico"
            )

        # 3. Aplicar sanitización de datos personales (PII)
        texto_sanitizado = sanitizar_texto_pdf(texto_original)

        # 4. Limitar texto a ~12000 caracteres para evitar exceder límites de tokens del modelo
        if len(texto_sanitizado) > 12000:
            logger.warning(f"Texto sanitizado excede el límite de 12000 caracteres ({len(texto_sanitizado)}). Truncando.")
            texto_sanitizado = texto_sanitizado[:12000]

        # 5. Obtener cliente de OpenAI
        client = get_openai_client()

        user_content = f"Texto del resumen a procesar:\n\n{texto_sanitizado}"

        # 6. Llamar al modelo gpt-4o-mini
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=6000,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content
        if not content:
            logger.error("Respuesta vacía desde OpenAI para el parser genérico.")
            return ResultadoParseo(
                banco="generico",
                confianza=0.0,
                capa_usada="llm_text_generico"
            )

        data = json.loads(content)

        # 7. Extraer metadatos
        titular_detectado = data.get("titular_detectado")
        if titular_detectado:
            titular_detectado = titular_detectado.strip()
            
        ultimos_4_digitos = data.get("ultimos_4_digitos")
        if ultimos_4_digitos:
            # Mantener solo los últimos 4 dígitos como string numérico limpio
            ultimos_4_digitos = "".join(filter(str.isdigit, str(ultimos_4_digitos)))[-4:]
            if not ultimos_4_digitos:
                ultimos_4_digitos = None

        # 8. Procesar transacciones
        transacciones = []
        for t in data.get("transacciones", []):
            try:
                fecha_str = t.get("fecha")
                if not fecha_str:
                    continue
                # Asegurar formato ISO AAAA-MM-DD
                fecha_val = date.fromisoformat(fecha_str)

                monto_val = Decimal(str(t.get("monto", "0.00")))
                moneda_val = str(t.get("moneda", "ARS")).upper().strip()
                desc_val = str(t.get("descripcion", "")).strip()

                if not desc_val:
                    continue

                cuota_act = int(t["cuota_actual"]) if t.get("cuota_actual") is not None else None
                cuota_tot = int(t["cuota_total"]) if t.get("cuota_total") is not None else None
                es_cargo = bool(t.get("es_cargo_bancario", False))
                titular_sec = t.get("titular_seccion")
                if titular_sec:
                    titular_sec = titular_sec.strip()

                transacciones.append(
                    TransaccionCruda(
                        fecha=fecha_val,
                        descripcion=desc_val,
                        monto=monto_val,
                        moneda=moneda_val,
                        cuota_actual=cuota_act,
                        cuota_total=cuota_tot,
                        es_cargo_bancario=es_cargo,
                        titular_seccion=titular_sec
                    )
                )
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Error parseando transacción individual en parser_generico: {t}. Error: {str(e)}")
                continue

        # Confianza tope de 0.5 en este parser fallback según especificaciones
        confianza = 0.5 if transacciones else 0.0

        return ResultadoParseo(
            banco="generico",
            titular_detectado=titular_detectado,
            ultimos_4_digitos=ultimos_4_digitos,
            transacciones=transacciones,
            confianza=confianza,
            capa_usada="llm_text_generico"
        )

    except Exception as e:
        logger.exception(f"Excepción no controlada en parsear_generico: {str(e)}")
        return ResultadoParseo(
            banco="generico",
            confianza=0.0,
            capa_usada="llm_text_generico"
        )
