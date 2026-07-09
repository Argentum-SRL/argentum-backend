import re


def detectar_banco(texto_pdf: str) -> str:
    """
    Detecta el banco emisor del resumen a partir del contenido de texto extraído del PDF.
    
    Esta función analiza el texto de forma insensible a mayúsculas y minúsculas y busca 
    ciertas palabras clave representativas para identificar a qué banco y tarjeta corresponde 
    el archivo.
    
    Lógica de detección:
        - Si contiene "Banco Galicia" o "bancogalicia" -> 'galicia'
        - Si contiene ("Banco de la Nación", "Banco Nacion", o "BNA") Y "VISA SIGNATURE" -> 'bna_visa'
        - Si contiene ("Banco de la Nación", "Banco Nacion", o "BNA") Y "MASTERCARD" -> 'bna_mastercard'
        - En cualquier otro caso, o si no hay coincidencia clara, devuelve -> 'generico'
        
    Parámetros:
        texto_pdf (str): El texto plano extraído de las páginas del PDF.
        
    Retorna:
        str: El identificador textual del banco ('galicia', 'bna_visa', 'bna_mastercard' o 'generico').
    """
    if not texto_pdf:
        return "generico"

    texto_lower = texto_pdf.lower()

    if "banco galicia" in texto_lower or "bancogalicia" in texto_lower:
        return "galicia"

    # Indicadores del Banco de la Nación Argentina (BNA)
    bna_indicadores = ["banco de la nación", "banco de la nacion", "banco nacion", "bna"]
    es_bna = any(indicador in texto_lower for indicador in bna_indicadores)

    if es_bna:
        if "visa signature" in texto_lower:
            return "bna_visa"
        if "mastercard" in texto_lower:
            return "bna_mastercard"

    return "generico"


def extraer_texto_pdf(pdf_bytes: bytes) -> str:
    """
    Extrae el texto inicial de un archivo PDF de manera rápida y segura.
    
    Esta función toma los datos crudos (bytes) de un archivo PDF y recupera
    el texto escrito en sus primeras 3 páginas. Se limita a las primeras
    páginas para asegurar que la lectura sea veloz y no ralentice el sistema, 
    ya que esto es suficiente para identificar a qué banco pertenece.
    
    Parámetros:
        pdf_bytes (bytes): Los bytes del archivo PDF cargado.
        
    Retorna:
        str: El texto extraído de las páginas analizadas, o un texto vacío en caso de error.
    """
    import io
    import pdfplumber

    if not pdf_bytes:
        return ""

    texto = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            # Leemos como máximo las primeras 3 páginas para optimizar el rendimiento
            for i, page in enumerate(pdf.pages):
                if i >= 3:
                    break
                texto += (page.extract_text() or "") + "\n"
    except Exception:
        # En caso de error (por ejemplo, si el PDF está dañado), devolvemos texto vacío de forma segura
        pass
    return texto





def sanitizar_texto_pdf(texto: str) -> str:
    """
    Sanitiza el texto extraído de un PDF eliminando datos personales sensibles
    como domicilio, CUIT, número de cuenta/socio y DNI, pero preserva los nombres
    de los titulares necesarios para clasificar consumos por persona.

    Esta función es de tipo best-effort (no garantiza eliminar el 100% de variantes
    posibles en todos los formatos de PDF).

    Parámetros:
        texto (str): El texto original del PDF.

    Retorna:
        str: El texto sanitizado.
    """
    if not texto:
        return ""

    lines = texto.split("\n")
    sanitized_lines = []

    # Expresiones regulares para sanitizar PII
    cuit_pattern = re.compile(r'\b\d{2}-\d{7,8}-\d\b')
    cuit_kw_pattern = re.compile(r'cuit\b.*', re.IGNORECASE)

    cuenta_patterns = [
        re.compile(r'n[°o]\s*de\s*socio\s*[\d-]+', re.IGNORECASE),
        re.compile(r'nro\.?\s*de\s*socio\s*[\d-]+', re.IGNORECASE),
        re.compile(r'c\.ahorro\s*\d+', re.IGNORECASE),
        re.compile(r'caja\s*de\s*ahorros?\s*\d+', re.IGNORECASE),
        re.compile(r'c\.cte\s*\d+', re.IGNORECASE),
        re.compile(r'n[°o]\s*de\s*cuenta\s*[\d-]+', re.IGNORECASE),
        re.compile(r'nro\.?\s*de\s*cuenta\s*[\d-]+', re.IGNORECASE),
    ]

    dni_pattern = re.compile(r'\bdni\s*(?:nro\.?|n[°o])?\s*[\d.]+', re.IGNORECASE)

    # Patrones para calle/número y código postal/localidad (en cabeceras)
    street_pattern = re.compile(r'^[A-Z\sÁÉÍÓÚÑ\.\,]+\s+\d+\s*$', re.IGNORECASE)
    postal_pattern = re.compile(r'^\d{4,5}\s+[A-Z\sÁÉÍÓÚÑ]+', re.IGNORECASE)

    skip_next_line = False

    for i, line in enumerate(lines):
        if skip_next_line:
            if not any(kw in line.lower() for kw in ("cuit", "socio", "resumen", "estado")):
                sanitized_lines.append("[DOMICILIO SANITIZADO]")
                skip_next_line = False
                continue
            skip_next_line = False

        line_strip = line.strip()

        # Si detectamos que finaliza con HOJA X/Y, salteamos la siguiente línea (que suele ser el domicilio)
        if re.search(r'HOJA\s+\d+/\d+', line_strip, re.IGNORECASE):
            skip_next_line = True

        # 1. Sanitizar CUIT
        if cuit_pattern.search(line_strip) or cuit_kw_pattern.search(line_strip):
            line_strip = "[CUIT SANITIZADO]"

        # 2. Sanitizar Cuentas/Socio
        for cp in cuenta_patterns:
            if cp.search(line_strip):
                line_strip = cp.sub("[CUENTA SANITIZADA]", line_strip)

        # 3. Sanitizar DNI
        if dni_pattern.search(line_strip):
            line_strip = dni_pattern.sub("[DNI SANITIZADO]", line_strip)

        # 4. Sanitizar domicilio en cabeceras basándonos en cercanía a 'HOJA X/Y'
        if (street_pattern.match(line_strip) or postal_pattern.match(line_strip)) and i < len(lines):
            cerca_de_hoja = False
            for offset in range(-3, 4):
                idx = i + offset
                if 0 <= idx < len(lines):
                    if re.search(r'HOJA\s+\d+/\d+', lines[idx], re.IGNORECASE):
                        cerca_de_hoja = True
                        break
            if cerca_de_hoja:
                if "MASTERCARD" in line_strip.upper() or "VISA" in line_strip.upper():
                    card_index = line_strip.upper().find("MASTERCARD")
                    if card_index == -1:
                        card_index = line_strip.upper().find("VISA")
                    line_strip = "[DOMICILIO SANITIZADO] " + line_strip[card_index:]
                else:
                    line_strip = "[DOMICILIO SANITIZADO]"

        sanitized_lines.append(line_strip)

    return "\n".join(sanitized_lines)
