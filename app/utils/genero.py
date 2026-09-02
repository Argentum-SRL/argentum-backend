"""
app/utils/genero.py — Utilidades para concordancia y flexión gramatical por género/sexo.
"""
from typing import Any


def normalizar_sexo(sexo: Any) -> str | None:
    """Extrae el valor string normalizado ('masculino', 'femenino', etc.) de un Enum o string."""
    if not sexo:
        return None
    if hasattr(sexo, "value"):
        return str(sexo.value).lower().strip()
    return str(sexo).lower().strip()


def flexionar_saludo(sexo: Any, base: str = "bienvenid") -> str:
    """
    Retorna el saludo flexionado con la primera letra en mayúscula o minúscula según base.
    Ejemplos:
      - flexionar_saludo('femenino', 'bienvenido') -> 'bienvenida'
      - flexionar_saludo('femenino', 'Bienvenido') -> 'Bienvenida'
      - flexionar_saludo('masculino', 'Bienvenido') -> 'Bienvenido'
      - flexionar_saludo('no_binario', 'Bienvenido') -> 'Te damos la bienvenida'
    """
    s = normalizar_sexo(sexo)
    is_upper = base and base[0].isupper()

    if s == "femenino":
        return "Bienvenida" if is_upper else "bienvenida"
    if s == "masculino":
        return "Bienvenido" if is_upper else "bienvenido"
    return "Te damos la bienvenida" if is_upper else "te damos la bienvenida"


def flexionar_palabra(sexo: Any, masculino: str, femenino: str, neutro: str | None = None) -> str:
    """
    Flexiona una palabra o frase según el sexo del usuario.
    Si es no_binario o no especificado, utiliza 'neutro' si fue provisto, o 'masculino' por defecto.
    """
    s = normalizar_sexo(sexo)
    if s == "femenino":
        return femenino
    if s == "masculino":
        return masculino
    return neutro if neutro is not None else masculino


def get_asunto_bienvenida(sexo: Any) -> str:
    """Retorna el asunto del correo de bienvenida acorde al sexo."""
    s = normalizar_sexo(sexo)
    if s == "femenino":
        return "¡Bienvenida a Argentum!"
    if s == "masculino":
        return "¡Bienvenido a Argentum!"
    return "¡Te damos la bienvenida a Argentum!"
