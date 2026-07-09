"""
app/services/importacion/parser_bna_mastercard.py — Parser de resúmenes BNA Mastercard usando OpenAI.
"""
from datetime import date
from decimal import Decimal
import io
import json
import logging
import re
import pdfplumber

from app.services.importacion.schemas import ResultadoParseo, TransaccionCruda
from app.services.importacion.utils import sanitizar_texto_pdf
from app.services.openai_client import get_openai_client

logger = logging.getLogger(__name__)

MONTH_MAP = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12
}

SYSTEM_PROMPT = """Sos un parser de resúmenes de tarjeta Mastercard de Banco Nación (BNA) especializado en extraer transacciones en formato JSON estructurado.

Analizá el texto del resumen de tarjeta de crédito y extraé la lista de transacciones.

CRITERIOS DE EXCLUSIÓN CRÍTICOS:
- EXCLUIR por completo las líneas de PAGO del usuario (ej: "SU PAGO U$S -102,67", "PAGO CAJERO/INTERNET -1888000,00"). NO son gastos.
- EXCLUIR por completo las devoluciones o reintegros de impuestos/percepciones (ej: "DEV PER RG 4815 30% -42381,80"). NO son gastos.
- Ignorar subtotales, totales y subencabezados agrupadores como "COMPRAS DEL MES", "CUOTAS DEL MES", "DEBITOS AUTOMATICOS".

REGLAS DE PROCESAMIENTO:
1. Cargos Bancarios: SÍ se deben extraer e identificar con es_cargo_bancario = true. Ejemplos: "COM.ADM.DE.CUENTA", "IMPUESTO DE SELLOS", "I.V.A. 21,0%", "PERCEPCION IVA", "PERCEP.AFIP", "SANTA FE RG ...".
2. Asignación de Titular (titular_seccion): Cada transacción debe etiquetarse con el nombre del titular/adicional bajo cuya sección se encuentra en el texto. El titular principal figura al inicio o en "TOTAL TITULAR [NOMBRE]". Los adicionales figuran como "TOTAL ADICIONAL [NOMBRE]".
3. Cuotas: Si la descripción contiene un formato de cuotas "XX/YY" (ej: "03/06", "01/24"), extraé cuota_actual = XX y cuota_total = YY como enteros. Remové la indicación de cuotas de la descripción final.
4. Consumos en USD: Identificá los consumos realizados en el exterior o en dólares. Suelen tener el formato "(USA,USD, 7,49)" o similar en el texto de la línea, y el monto en la columna DOLAR. Marcá moneda = "USD" y monto = valor en USD (ej: 7.49).
5. Montos de Reversión: Si un consumo o cargo viene con signo negativo pero es una reversión (no un pago ni devolución tributaria), extraélo con el signo negativo correspondiente.
6. Nunca inventes transacciones que no figuren literalmente en el texto.
7. Formato de Fecha: Convertí las fechas al formato estándar YYYY-MM-DD. Si el año es de 2 dígitos (ej: 26), consideralo como 2026.

Estructura de Respuesta JSON:
{
  "transacciones": [
    {
      "fecha": "YYYY-MM-DD",
      "descripcion": "Nombre limpio del comercio / cargo",
      "monto": número decimal,
      "moneda": "ARS" o "USD",
      "cuota_actual": número entero o null,
      "cuota_total": número entero o null,
      "es_cargo_bancario": true o false,
      "titular_seccion": "Nombre completo del titular/adicional que realizó el consumo"
    }
  ]
}
"""


def parse_bna_mastercard_date(date_str: str) -> date | None:
    """
    Convierte una fecha en texto (DD-Mmm-YY o DD-Mmm-YYYY) a un objeto date de Python.
    
    Parámetros:
        date_str (str): El texto que representa la fecha.

    Retorna:
        date | None: La fecha interpretada o None si no es válida.
    """
    if not date_str:
        return None
    date_str_clean = date_str.strip()
    match = re.match(r"^(\d{1,2})-([A-Za-z]{3,4})-(\d{2,4})", date_str_clean)
    if match:
        day = int(match.group(1))
        month_name = match.group(2).upper()[:3]
        year = int(match.group(3))
        month = MONTH_MAP.get(month_name)
        if month:
            if year < 100:
                year += 2000
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def parsear_bna_mastercard(pdf_bytes: bytes) -> ResultadoParseo:
    """
    Lee y procesa un archivo PDF correspondiente al resumen de una tarjeta Mastercard
    del Banco de la Nación Argentina (BNA) utilizando OpenAI GPT-4o-mini.
    
    Aplica sanitización de datos personales (PII) antes de enviar el texto al LLM.
    
    Parámetros:
        pdf_bytes (bytes): Los bytes del archivo PDF del resumen.
        
    Retorna:
        ResultadoParseo: Un objeto DTO con la metadata y transacciones extraídas.
    """
    try:
        if not pdf_bytes:
            return ResultadoParseo(
                banco="bna_mastercard",
                confianza=0.0,
                capa_usada="llm_text"
            )

        # 1. Extraer texto plano usando pdfplumber
        texto_original = ""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                texto_original += (page.extract_text() or "") + "\n"

        if not texto_original.strip():
            return ResultadoParseo(
                banco="bna_mastercard",
                confianza=0.0,
                capa_usada="llm_text"
            )

        # 2. Aplicar sanitización antes de armar el prompt
        texto_sanitizado = sanitizar_texto_pdf(texto_original)

        # 3. Extraer metadata simple con regex antes de llamar al LLM
        titular_detectado = None
        periodo_desde = None
        periodo_hasta = None
        adicionales = set()

        # Intentar extraer titular
        titular_match = re.search(r"^([A-Z\sÁÉÍÓÚÑ]+?)\s+HOJA\s+\d+/\d+", texto_original, re.MULTILINE)
        if titular_match:
            titular_detectado = titular_match.group(1).strip()
        else:
            titular_match = re.search(r"TOTAL TITULAR\s+([A-Z\sÁÉÍÓÚÑ]+?)(?:\s+[\d.,-]+)+$", texto_original, re.MULTILINE)
            if titular_match:
                titular_detectado = titular_match.group(1).strip()

        # Extraer adicionales
        for line in texto_original.split("\n"):
            m_adi = re.search(r"TOTAL ADICIONAL\s+([A-Z\sÁÉÍÓÚÑ]+?)(?:\s+[\d.,-]+)+$", line.strip())
            if m_adi:
                adicionales.add(m_adi.group(1).strip())

        # Extraer período
        desde_match = re.search(r"Cierre Anterior\s*:\s*(\d{1,2}-[A-Za-z]{3,4}-\d{2,4})", texto_original, re.IGNORECASE)
        hasta_match = re.search(r"Estado de cuenta al\s*:\s*(\d{1,2}-[A-Za-z]{3,4}-\d{2,4})", texto_original, re.IGNORECASE)

        if desde_match:
            periodo_desde = parse_bna_mastercard_date(desde_match.group(1))
        if hasta_match:
            periodo_hasta = parse_bna_mastercard_date(hasta_match.group(1))

        # 4. Asignar cliente y llamar a OpenAI
        client = get_openai_client()

        user_content = f"Texto del resumen a procesar:\n\n{texto_sanitizado}"
        
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
            logger.error("Respuesta vacía recibida desde OpenAI para BNA Mastercard.")
            return ResultadoParseo(
                banco="bna_mastercard",
                titular_detectado=titular_detectado,
                periodo_desde=periodo_desde,
                periodo_hasta=periodo_hasta,
                confianza=0.0,
                capa_usada="llm_text"
            )

        data = json.loads(content)

        # 5. Parsear transacciones
        transacciones = []
        for t in data.get("transacciones", []):
            try:
                fecha_str = t.get("fecha")
                if not fecha_str:
                    continue
                fecha_val = date.fromisoformat(fecha_str)

                monto_val = Decimal(str(t.get("monto", "0.00")))
                moneda_val = t.get("moneda", "ARS").upper()
                desc_val = t.get("descripcion", "").strip()

                cuota_act = int(t["cuota_actual"]) if t.get("cuota_actual") is not None else None
                cuota_tot = int(t["cuota_total"]) if t.get("cuota_total") is not None else None
                es_cargo = bool(t.get("es_cargo_bancario", False))
                titular_sec = t.get("titular_seccion")

                if not desc_val:
                    continue

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
                logger.warning(f"Error procesando una transacción individual del JSON: {t}. Error: {str(e)}")
                continue

        confianza = 0.7 if transacciones else 0.3

        return ResultadoParseo(
            banco="bna_mastercard",
            titular_detectado=titular_detectado,
            periodo_desde=periodo_desde,
            periodo_hasta=periodo_hasta,
            transacciones=transacciones,
            confianza=confianza,
            capa_usada="llm_text"
        )

    except Exception as e:
        logger.exception(f"Excepción no controlada durante el parseo de BNA Mastercard: {str(e)}")
        return ResultadoParseo(
            banco="bna_mastercard",
            confianza=0.0,
            capa_usada="llm_text"
        )
