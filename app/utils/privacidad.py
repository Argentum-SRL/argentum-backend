"""
app/utils/privacidad.py — Funciones utilitarias para proteger datos personales en logs y salidas.
"""
from __future__ import annotations


def enmascarar_email(email: str | None) -> str:
    """
    Enmascara una dirección de email para evitar registrar datos personales en logs.
    - None -> '(sin email)'
    - Vacío o sin '@' -> '***'
    - Email válido -> primeros 2 caracteres + '***@' + dominio
    """
    if email is None:
        return "(sin email)"
    email_str = email.strip()
    if not email_str or "@" not in email_str:
        return "***"
    usuario, dominio = email_str.split("@", 1)
    prefijo = usuario[:2]
    return f"{prefijo}***@{dominio}"
