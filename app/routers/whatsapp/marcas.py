"""
Módulo para el ajuste determinístico de categorías según marcas comerciales (Taxi/Apps vs Delivery).
"""
import re
from app.utils.texto import normalizar_texto

# Marcas de transporte (Taxi / Apps)
_PATRON_TRANSPORTE = re.compile(r"\b(uber|didi|cabify|taxi|remis)\b", re.IGNORECASE)

# Palabras que indican comida/delivery en apps de transporte mixto (como Didi Food, Uber Eats)
_PATRON_DELIVERY_MIXTO = re.compile(r"\b(eats|food|comida|pedido|delivery)\b", re.IGNORECASE)

# Marcas de gastronomía pura (Delivery)
_PATRON_DELIVERY_PURO = re.compile(r"\b(rappi|pedidosya|pedidos\s+ya|peya)\b", re.IGNORECASE)

CAT_TAXI_APPS = "Transporte > Taxi / Apps"
CAT_DELIVERY = "Gastronomía > Delivery"


def ajustar_categoria_marcas(movimiento: dict) -> dict:
    """
    Ajusta la categoría de un movimiento según su descripción y el texto del ítem:
    - uber, didi, cabify, taxi, remis -> "Transporte > Taxi / Apps";
      salvo que el ítem diga eats, food, comida, pedido o delivery -> "Gastronomía > Delivery".
    - rappi, pedidosya, pedidos ya, peya -> "Gastronomía > Delivery".
    Modifica el diccionario en su lugar y lo retorna.
    """
    if not isinstance(movimiento, dict):
        return movimiento

    descripcion = movimiento.get("descripcion") or ""
    item = movimiento.get("item") or ""

    texto_completo = f"{descripcion} {item}"
    norm = normalizar_texto(texto_completo)

    # 1. Delivery puro (Rappi, PedidosYa, Peya)
    if _PATRON_DELIVERY_PURO.search(norm):
        movimiento["categoria"] = CAT_DELIVERY
        return movimiento

    # 2. Transporte (Uber, Didi, Cabify, Taxi, Remis)
    if _PATRON_TRANSPORTE.search(norm):
        if _PATRON_DELIVERY_MIXTO.search(norm):
            movimiento["categoria"] = CAT_DELIVERY
        else:
            movimiento["categoria"] = CAT_TAXI_APPS
        return movimiento

    return movimiento
