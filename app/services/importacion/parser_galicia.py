from datetime import date, datetime
from decimal import Decimal
import io
import re
import pdfplumber

from app.services.importacion.schemas import ResultadoParseo, TransaccionCruda


def parse_date(date_str: str) -> date | None:
    """
    Convierte una representación en texto de una fecha (DD-MM-AA o similar)
    a un objeto date de Python.
    
    Prueba varios formatos con guión (-) y barra (/) de forma sucesiva.
    
    Parámetros:
        date_str (str): El string de fecha a parsear.
        
    Retorna:
        date | None: La fecha parseada o None si ningún formato es válido.
    """
    for fmt in ("%d-%m-%y", "%d-%m-%Y", "%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def parse_monto(monto_str: str) -> Decimal:
    """
    Convierte un string de monto con formato decimal argentino a Decimal.
    
    Remueve puntos de separación de miles, reemplaza la coma decimal por punto, 
    y maneja signos negativos iniciales o finales (por ejemplo, '120,50-').
    
    Parámetros:
        monto_str (str): El monto tal como viene en el texto extraído.
        
    Retorna:
        Decimal: El valor numérico decimal correspondiente.
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


def parsear_galicia(pdf_bytes: bytes) -> ResultadoParseo:
    """
    Parsea un archivo PDF correspondiente a un resumen de tarjeta Visa de Banco Galicia.
    
    Esta función realiza una lectura secuencial línea por línea del contenido extraído
    con pdfplumber, buscando mediante expresiones regulares la metadata principal del resumen
    (como el titular de la cuenta, tarjeta y período) y las transacciones individuales.
    
    Lógica de procesamiento:
        - Detecta las secciones de pesos y dólares a partir de palabras claves e inicializa 
          la moneda correspondiente.
        - Filtra transacciones inválidas como saldos anteriores, subtotales o líneas de pagos.
        - Limpia prefijos como '*' o 'K' en las descripciones de los comercios.
        - Clasifica los cargos bancarios como es_cargo_bancario=True si corresponden a Impuesto de Sellos.
        - Cuenta con control de errores total: cualquier excepción imprevista es atrapada
          para retornar un resultado vacío con nivel de confianza de 0.0.
          
    Parámetros:
        pdf_bytes (bytes): Los bytes del archivo PDF a procesar.
        
    Retorna:
        ResultadoParseo: Un DTO con toda la información extraída y métricas del procesamiento.
    """
    try:
        if not pdf_bytes:
            return ResultadoParseo(
                banco="galicia",
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

        # Intentar extraer período del resumen globalmente
        period_match = re.search(
            r"(?:DESDE|DEL)\s*(?:EL)?\s*(\d{2}[-/]\d{2}[-/]\d{2,4})\s*(?:HASTA|AL)\s*(?:EL)?\s*(\d{2}[-/]\d{2}[-/]\d{2,4})",
            text,
            re.IGNORECASE
        )
        if period_match:
            periodo_desde = parse_date(period_match.group(1))
            periodo_hasta = parse_date(period_match.group(2))

        current_currency = "ARS"

        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue

            line_upper = line_strip.upper()

            # 1. Detectar el cambio de moneda por sección
            if "PESOS" in line_upper and any(keyword in line_upper for keyword in ("DETALLE", "CONSUMOS", "SUBTOTAL", "CARGOS")):
                current_currency = "ARS"
            elif any(dolar_kw in line_upper for dolar_kw in ("DOLARES", "DÓLARES", "U$S")) and any(keyword in line_upper for keyword in ("DETALLE", "CONSUMOS", "SUBTOTAL", "CARGOS")):
                current_currency = "USD"

            # 2. Detectar metadatos de tarjeta y titular
            card_match = re.search(
                r"TARJETA\s+([\dX]+)\s+Total\s+Consumos\s+de\s+([A-Za-z\s\u00C0-\u00FF]+?)(?:\s+[\d.,-]+)*$",
                line_strip,
                re.IGNORECASE
            )
            if card_match:
                card_str = card_match.group(1).strip()
                ultimos_4_digitos = card_str[-4:]
                titular_detectado = card_match.group(2).strip()
                continue

            # 3. Detectar transacciones
            date_match = re.match(r"^(\d{2}-\d{2}-\d{2,4})\b", line_strip)
            if not date_match:
                continue

            fecha_str = date_match.group(1)
            fecha = parse_date(fecha_str)
            if not fecha:
                continue

            rest_line = line_strip[len(fecha_str):].strip()
            rest_upper = rest_line.upper()

            # Excluir líneas que son de saldo anterior, pagos o subtotales
            if any(exclude in rest_upper for exclude in ("SALDO ANTERIOR", "SU PAGO", "PAGO EN PESOS", "PAGO EN DOLARES", "SUBTOTAL", "TOTAL CONSUMOS")):
                continue

            # Extraer cuotas si existen (ej. 03/03)
            cuota_actual = None
            cuota_total = None
            cuota_match = re.search(r"\b(\d{1,2})/(\d{1,2})\b", rest_line)
            if cuota_match:
                cuota_actual = int(cuota_match.group(1))
                cuota_total = int(cuota_match.group(2))
                rest_line = rest_line.replace(cuota_match.group(0), " ")

            # Separar por palabras para aislar el monto y el comprobante
            parts = rest_line.split()
            if len(parts) < 2:
                continue

            # El último elemento es el monto
            monto_str = parts[-1]
            try:
                monto = parse_monto(monto_str)
            except Exception:
                continue

            # Comprobar si el elemento anterior al monto es el comprobante numérico
            has_comprobante = len(parts) > 2 and parts[-2].isdigit() and len(parts[-2]) >= 4

            if has_comprobante:
                desc_parts = parts[:-2]
            else:
                desc_parts = parts[:-1]

            description = " ".join(desc_parts).strip()
            if not description:
                continue

            # Limpiar prefijos de la descripción (* o K)
            description = re.sub(r"^[K*]\s*", "", description, flags=re.IGNORECASE).strip()

            # Determinar si es cargo bancario (ej: IMPUESTO DE SELLOS)
            es_cargo_bancario = "IMPUESTO DE SELLOS" in description.upper()

            transacciones.append(
                TransaccionCruda(
                    fecha=fecha,
                    descripcion=description,
                    monto=monto,
                    moneda=current_currency,
                    cuota_actual=cuota_actual,
                    cuota_total=cuota_total,
                    es_cargo_bancario=es_cargo_bancario
                )
            )

        confianza = 0.95 if transacciones else 0.3

        return ResultadoParseo(
            banco="galicia",
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
            banco="galicia",
            confianza=0.0,
            capa_usada="deterministic"
        )
