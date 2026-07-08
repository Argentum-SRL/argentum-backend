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
