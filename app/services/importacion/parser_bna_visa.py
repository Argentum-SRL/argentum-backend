from datetime import date, datetime
from decimal import Decimal
import io
import re
import pdfplumber

from app.services.importacion.schemas import ResultadoParseo, TransaccionCruda

# Diccionario para mapear abreviaturas de meses en español a su número correspondiente.
MONTH_MAP = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12
}


def parse_bna_date(date_str: str) -> date | None:
    """
    Convierte una fecha en texto (representada en formato numérico o con nombre de mes abreviado) 
    a un objeto de fecha de Python.
    
    Esta función soporta dos formatos principales:
      1. Fechas separadas por puntos (ej: '13.05.25' o '13.05.2025').
      2. Fechas que contienen la abreviatura del mes en español (ej: '21 May 26' o '23 Abr 26').

    Parámetros:
        date_str (str): El texto que representa la fecha.

    Retorna:
        date | None: La fecha interpretada en un objeto date de Python, o None si no coincide con los formatos.
    """
    if not date_str:
        return None

    date_str_clean = date_str.strip()

    # 1. Intentar buscar formato con puntos (ej: DD.MM.AA o DD.MM.YYYY)
    dot_match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})", date_str_clean)
    if dot_match:
        day = int(dot_match.group(1))
        month = int(dot_match.group(2))
        year = int(dot_match.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    # 2. Intentar buscar formato con nombre de mes abreviado (ej: DD Mmm YY o DD Mmm YYYY)
    word_match = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,4})\s+(\d{2,4})", date_str_clean)
    if word_match:
        day = int(word_match.group(1))
        month_name = word_match.group(2).upper()[:3]
        year = int(word_match.group(3))
        month = MONTH_MAP.get(month_name)
        if month:
            if year < 100:
                year += 2000
            try:
                return date(year, month, day)
            except ValueError:
                return None

    return None


def parse_monto(monto_str: str) -> Decimal:
    """
    Convierte un valor de dinero en texto al formato numérico estándar (Decimal).
    
    Esta función limpia el texto del monto:
      - Quita los puntos que separan los miles (ej: 123.456,78 -> 123456,78).
      - Reemplaza la coma decimal por un punto (ej: 123456,78 -> 123456.78).
      - Detecta si el monto es negativo, ya sea que empiece o termine con un signo menos (ej: '-10,00' o '10,00-').
      
    Parámetros:
        monto_str (str): El texto del monto extraído del PDF.

    Retorna:
        Decimal: El número decimal correspondiente listo para cálculos matemáticos.
    """
    monto_str = monto_str.strip()
    is_negative = False

    if monto_str.endswith('-'):
        is_negative = True
        monto_str = monto_str[:-1]
    elif monto_str.startswith('-'):
        is_negative = True
        monto_str = monto_str[1:]

    monto_clean = monto_str.replace('.', '').replace(',', '.')
    val = Decimal(monto_clean)
    return -val if is_negative else val


def parsear_bna_visa(pdf_bytes: bytes) -> ResultadoParseo:
    """
    Lee y procesa un archivo PDF que contiene el resumen mensual de una tarjeta Visa del Banco de la Nación Argentina (BNA).
    
    Esta función extrae la información más importante del documento de manera automática:
      - El titular de la cuenta.
      - Los últimos 4 dígitos del número de tarjeta de crédito.
      - El período de facturación (fechas de inicio y fin).
      - La lista de consumos y cargos bancarios válidos realizados (excluyendo los pagos del cliente y créditos).
      
    El procesamiento se realiza de forma determinista leyendo el texto del PDF línea por línea mediante reglas específicas de formato (expresiones regulares).
    
    Parámetros:
        pdf_bytes (bytes): Los datos en binario del archivo PDF del resumen.
        
    Retorna:
        ResultadoParseo: Un objeto con todos los datos extraídos (titular, tarjeta, fechas, transacciones) y el nivel de confianza de la lectura.
    """
    try:
        if not pdf_bytes:
            return ResultadoParseo(
                banco="bna_visa",
                confianza=0.0,
                capa_usada="deterministic"
            )

        text = ""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"

        lines = text.split("\n")

        titular_detectado = None
        ultimos_4_digitos = None
        periodo_desde = None
        periodo_hasta = None
        transacciones = []

        # 1. Extraer período del resumen a partir de las etiquetas de cierre
        actual_match = re.search(r"CIERRE\s+ACTUAL:\s*(\d{1,2}\s+[A-Za-z]{3,4}\s+\d{2,4})", text, re.IGNORECASE)
        anterior_match = re.search(r"CIERRE\s+ANTERIOR\s+(\d{1,2}\s+[A-Za-z]{3,4}\s+\d{2,4})", text, re.IGNORECASE)

        if actual_match:
            periodo_hasta = parse_bna_date(actual_match.group(1))
        if anterior_match:
            periodo_desde = parse_bna_date(anterior_match.group(1))

        # 2. Procesar el documento línea por línea
        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue

            line_upper = line_strip.upper()

            # Extraer metadata de tarjeta y titular
            # Patrón: TARJETA [número] Total Consumos de [nombre] [monto_pesos] [monto_dolar]
            card_match = re.search(
                r"TARJETA\s+(\d+)\s+Total\s+Consumos\s+de\s+(.+?)(?:\s+[\d.,-]+)+$",
                line_strip,
                re.IGNORECASE
            )
            if card_match:
                card_str = card_match.group(1).strip()
                ultimos_4_digitos = card_str[-4:]
                titular_detectado = card_match.group(2).strip()
                continue

            # Buscar transacciones (líneas que inician con fecha DD.MM.YY o DD.MM.YYYY)
            date_match = re.match(r"^(\d{2}\.\d{2}\.\d{2,4})\b", line_strip)
            if not date_match:
                continue

            fecha_str = date_match.group(1)
            fecha = parse_bna_date(fecha_str)
            if not fecha:
                continue

            rest_line = line_strip[len(fecha_str):].strip()
            rest_upper = rest_line.upper()

            # Excluir líneas no deseadas (saldos anteriores, subtotales, totales, etc.)
            if any(exclude in rest_upper for exclude in ("SALDO ANTERIOR", "SU PAGO", "SUBTOTAL", "TOTAL CONSUMOS")):
                continue

            parts = rest_line.split()
            # Necesitamos al menos el detalle de transacción y los dos montos (pesos y dólares)
            # Mínimo 3 elementos en rest_line: [Detalle] [Monto Pesos] [Monto Dólar]
            if len(parts) < 3:
                continue

            # Las últimas dos columnas siempre corresponden a Pesos y Dólares
            dolar_str = parts[-1]
            pesos_str = parts[-2]

            try:
                pesos_monto = parse_monto(pesos_str)
                dolar_monto = parse_monto(dolar_str)
            except Exception:
                continue

            # Determinar la moneda y monto de la transacción
            if dolar_monto > Decimal("0.00"):
                moneda = "USD"
                monto = dolar_monto
            else:
                moneda = "ARS"
                monto = pesos_monto

            # Excluir montos negativos (representan pagos o créditos cargados al resumen)
            if monto < Decimal("0.00"):
                continue

            # Extraer opcionalmente el comprobante de la transacción si es un número y viene al inicio de la descripción
            if parts[0].isdigit():
                comprobante = parts[0]
                desc_parts = parts[1:-2]
            else:
                comprobante = None
                desc_parts = parts[:-2]

            # Construir la descripción inicial uniendo las palabras intermedias
            description = " ".join(desc_parts).strip()

            # Limpiar cualquier signo '$' suelto que preceda al monto y haya quedado al final de la descripción
            if description.endswith("$"):
                description = description[:-1].strip()

            if not description:
                continue

            # Determinar si la transacción es un cargo directo del banco
            es_cargo_bancario = "IMPUESTO DE SELLOS" in description.upper()

            # Extraer cuotas si el formato 'C.XX/YY' está presente en la descripción
            cuota_actual = None
            cuota_total = None
            cuota_match = re.search(r"C\.(\d{1,2})/(\d{1,2})", description, re.IGNORECASE)
            if cuota_match:
                cuota_actual = int(cuota_match.group(1))
                cuota_total = int(cuota_match.group(2))
                # Remover la cuota de la descripción
                description = description.replace(cuota_match.group(0), " ")

            # Limpiar espacios múltiples que hayan quedado tras remover las cuotas
            description = re.sub(r"\s+", " ", description).strip()

            transacciones.append(
                TransaccionCruda(
                    fecha=fecha,
                    descripcion=description,
                    monto=monto,
                    moneda=moneda,
                    cuota_actual=cuota_actual,
                    cuota_total=cuota_total,
                    es_cargo_bancario=es_cargo_bancario
                )
            )

        confianza = 0.95 if transacciones else 0.3

        return ResultadoParseo(
            banco="bna_visa",
            titular_detectado=titular_detectado,
            ultimos_4_digitos=ultimos_4_digitos,
            periodo_desde=periodo_desde,
            periodo_hasta=periodo_hasta,
            transacciones=transacciones,
            confianza=confianza,
            capa_usada="deterministic"
        )

    except Exception:
        return ResultadoParseo(
            banco="bna_visa",
            confianza=0.0,
            capa_usada="deterministic"
        )
