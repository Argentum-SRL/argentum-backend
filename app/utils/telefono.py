def normalizar_telefono_ar(telefono: str) -> str:
    """
    Devuelve el número en formato canónico: solo dígitos, sin '54' de país,
    sin '9' de móvil, sin '0' de larga distancia.
    Ej: '+5493411234567', '03411234567', '5493411234567', '3411234567'
        todos devuelven '3411234567'.
    """
    if not telefono:
        return ""
    digitos = "".join(c for c in telefono if c.isdigit())
    if digitos.startswith("54"):
        digitos = digitos[2:]
    if digitos.startswith("9"):
        digitos = digitos[1:]
    if digitos.startswith("0"):
        digitos = digitos[1:]
    return digitos
