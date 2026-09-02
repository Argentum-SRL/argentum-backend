from decimal import Decimal
from typing import Union
from app.models.usuario import Moneda


def formatear_monto(
    monto: Union[Decimal, float, int, None],
    moneda: Union[Moneda, str] = Moneda.ARS,
    con_decimales: bool | None = None
) -> str:
    """
    Formatea un monto numérico con símbolo según la moneda y formato estándar argentino.
    - Símbolo: '$' para ARS, 'US$' para USD.
    - Separador de miles: '.'
    - Separador decimal: ','
    - con_decimales:
        * None: muestra 2 decimales si el valor tiene parte fraccionaria, o 0 si es entero.
        * True: siempre muestra 2 decimales.
        * False: siempre redondea a entero (0 decimales).
    """
    if monto is None:
        monto = Decimal("0")

    moneda_str = moneda.value if hasattr(moneda, "value") else str(moneda).upper()
    simbolo = "US$" if moneda_str == "USD" else "$"

    val = float(monto)
    signo = "-" if val < 0 else ""
    abs_val = abs(val)

    if con_decimales is True:
        num_str = f"{abs_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    elif con_decimales is False:
        num_str = f"{abs_val:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        if abs_val % 1 != 0:
            num_str = f"{abs_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        else:
            num_str = f"{abs_val:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")

    return f"{signo}{simbolo}{num_str}"
