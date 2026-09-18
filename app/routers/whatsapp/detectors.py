"""
Detectores determinísticos y heurísticos para el flujo de WhatsApp IA.
Funciones puras (sin I/O de base de datos directa salvo rate limit en memoria/redis).
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.core.catalogo_suscripciones import identificar_servicio_en_texto
from app.models.conversacion_wpp import ConversacionWpp
from app.routers.whatsapp.parsers import (
    _extraer_frecuencia_mencionada,
    _extraer_nombre_servicio,
    _parsear_monto_argentino,
)
from app.services.rate_limit_service import verificar_rate_limit
from app.utils.texto import normalizar_texto

SALUDOS_RIOPLATENSE = {
    "hola",
    "buenas",
    "buen dia",
    "buen día",
    "buenos dias",
    "buenos días",
    "buenas tardes",
    "buenas noches",
    "holis",
    "holi",
    "que tal",
    "qué tal",
    "buenas y santas",
    "como va",
    "cómo va",
    "como andas",
    "cómo andás",
    "que onda",
    "qué onda",
    "che",
    "che hola",
    "hola che",
    "hola buenas",
    "hola buen dia",
    "hola como va",
    "hola que tal",
    "buendia",
}

PALABRAS_CANCELACION = {
    "no",
    "cancela",
    "cancelá",
    "cancelar",
    "cancelalo",
    "cancelala",
    "deja",
    "dejá",
    "dejalo",
    "dejala",
    "olvidate",
    "olvidalo",
    "olvidala",
    "no importa",
    "nada",
    "borrar",
    "descarta",
    "descartar",
    "no cancela",
    "no gracias",
    "no quiero",
    "no hace falta",
}

PALABRAS_CONFIRMACION = {
    "si",
    "sí",
    "dale",
    "ok",
    "confirmo",
    "confirmar",
    "va",
    "listo",
    "de una",
    "correcto",
    "perfecto",
    "seh",
    "sip",
    "yes",
}

FRASES_DESHACER = {
    "borra eso",
    "borrala",
    "borralo",
    "borrar eso",
    "borrar el ultimo",
    "borra el ultimo",
    "borralo por favor",
    "elimina eso",
    "eliminalo",
    "eliminala",
    "eliminar eso",
    "eliminar el ultimo",
    "elimina el ultimo",
    "me equivoque",
    "me equivoqué",
    "eso estaba mal",
    "estaba mal",
    "anula eso",
    "anular eso",
    "anulalo",
    "anula el ultimo",
    "anular el ultimo",
    "cancela el ultimo",
    "cancelar el ultimo",
    "cancelalo el ultimo",
    "cancelar el ultimo movimiento",
    "cancela el ultimo movimiento",
    "cancelar el gasto",
    "cancela el gasto",
    "deshacer",
    "deshace eso",
    "deshacer el ultimo",
    "deshace el ultimo",
}

COOLDOWN_MINUTOS_NO_REGISTRADO = 15

def _es_saludo(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    return bool(norm and norm in SALUDOS_RIOPLATENSE)

def _es_cancelacion(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    if norm in PALABRAS_CANCELACION:
        return True
    if re.match(r"^no+$", norm):  # no, noo, nooo, noooo...
        return True
    if norm.startswith("no cancela") or norm.startswith("no gracias") or norm.startswith("no, cancela"):
        return True
    return False

def _es_confirmacion(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    return bool(norm and norm in PALABRAS_CONFIRMACION)

def _es_pedido_deshacer(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    if norm in FRASES_DESHACER:
        return True
    if re.match(r"^(?:por favor\s+)?(?:borra|elimina|anula|cancela|deshace)(?:r)?\s+(?:eso|el\s+ultimo|lo\s+ultimo|el\s+ultimo\s+movimiento|el\s+ultimo\s+gasto)(?:\s+por\s+favor)?$", norm):
        return True
    if re.match(r"^me\s+equivoque(?:\s+en\s+eso)?$", norm):
        return True
    return False

def _debe_responder_no_registrado(telefono_normalizado: str) -> bool:
    permitido, _, _ = verificar_rate_limit(
        accion="cooldown_no_registrado",
        identificador=telefono_normalizado,
        max_intentos=1,
        ventana_segundos=COOLDOWN_MINUTOS_NO_REGISTRADO * 60,
    )
    return permitido

def _es_pregunta_billetera(conv: ConversacionWpp | None) -> bool:
    if not conv or not conv.slot_filling_activo:
        return False
    estado = conv.slot_filling_estado or {}
    if estado.get("tipo_flujo") == "lote_slot_filling":
        return True
    datos_faltantes = estado.get("datos_faltantes", [])
    return any(d in datos_faltantes for d in ("billetera_origen", "billetera_destino", "billetera", "billetera_lote"))

def _es_pedido_pago_resumen(mensaje: str) -> bool:
    """Detecta si el usuario pide pagar el resumen de la tarjeta de crédito (Tarea 7)."""
    m = normalizar_texto(mensaje)
    frases = [
        "pague el resumen", "pague resumen", "pagar el resumen", "pagar resumen",
        "pago del resumen", "pago resumen", "pagar la tarjeta", "pague la tarjeta",
        "pagar tarjeta", "pague tarjeta", "abonar el resumen", "abonar resumen",
        "pago de resumen", "pagar el saldo de la tarjeta", "pague el saldo de la tarjeta",
    ]
    return any(f in m for f in frases)

def _es_confirmacion_nuevo_movimiento(mensaje: str) -> bool:
    """Verifica si el usuario confirma que el movimiento repetido es nuevo."""
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    if any(k in norm for k in ("es nuevo", "nuevo", "es otro", "otro", "son dos", "son distintos", "gasto nuevo", "movimiento nuevo", "es otra cosa")):
        return True
    if norm in PALABRAS_CONFIRMACION:
        return True
    return False

def _es_descarte_duplicado(mensaje: str) -> bool:
    """Verifica si el usuario indica que el movimiento repetido es un error o duplicado."""
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    if any(k in norm for k in ("error", "repitio", "repetido", "equivoque", "equivoqué", "no anotes", "no registres", "deja", "dejalo")):
        return True
    if _es_cancelacion(mensaje):
        return True
    return False

def _es_confirmacion_lote_ambos(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    if any(k in norm for k in ("son dos", "dos", "los dos", "ambos", "son distintos", "distintos", "anota los dos", "anota ambos")):
        return True
    if norm in PALABRAS_CONFIRMACION:
        return True
    return False

def _es_confirmacion_lote_uno_solo(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    return any(k in norm for k in ("es uno solo", "uno solo", "solo uno", "uno", "es uno", "fue uno solo", "fue uno", "anota uno", "anota solo uno"))

def _parece_intento_correccion(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    if re.search(r"(?:eran?|fue)?\s*\$?[\d\.,]+k?\s+no\s+\$?[\d\.,]+k?", norm):
        return True
    if re.search(r"^no,?\s+(?:eran?\s+)?\$?[\d\.,]+k?$", norm):
        return True
    if re.search(r"^(?:eso\s+era|era|en\s+realidad\s+era)\s+", norm):
        return True
    if re.search(r"^(?:fue\s+con|era\s+con|fue\s+en|era\s+en)\s+", norm):
        return True
    if re.search(r"^(?:fue\s+ayer|era\s+ayer|fue\s+anteayer|era\s+anteayer|fue\s+hoy)\b", norm):
        return True
    return False

def _es_senial_suscripcion(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    patrones = [
        r"\b(?:empece|empecé)\s+a\s+pagar\b",
        r"\b(?:me\s+suscribi|me\s+suscribí)\b",
        r"\b(?:me\s+abone|me\s+aboné)\b",
        r"\bcontrat[eé]\b",
        r"\bpago\s+todos\s+los\s+meses\b",
        r"\bpago\s+mensual\b",
        r"\bes\s+(?:mensual|anual|bimestral|trimestral|semestral)\b",
        r"\bse\s+debita\b",
        r"\bme\s+lo\s+descuentan\b",
        r"\bnueva\s+suscripci[oó]n\b",
        r"\bme\s+anot[eé]\b",
    ]
    return any(re.search(p, norm) for p in patrones)

def _es_senial_gasto_suelto(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    if _es_senial_suscripcion(mensaje):
        return False
    return bool(re.search(r"\b(?:gast[eé]|me\s+sali[oó])\b", norm))

def _es_pedido_baja_suscripcion(mensaje: str) -> tuple[bool, str | None]:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False, None
    if re.search(r"\b(?:di\s+de\s+baja|dar\s+de\s+baja|baja\s+de|cancele\s+la\s+suscripcion|cancele|cancel[eé]|ya\s+no\s+pago\s+mas|ya\s+no\s+pago\s+más|me\s+desuscribi|me\s+desuscribí)\b", norm):
        srv = _extraer_nombre_servicio(mensaje)
        return True, srv
    return False, None

def _es_cambio_precio_suscripcion(mensaje: str) -> tuple[bool, str | None, Decimal | None]:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False, None, None
    if re.search(r"\b(?:aument[oó]|aumento|ahora\s+sale|subi[oó]\s+a|subio\s+a|me\s+lo\s+aumentaron|cambi[oó]\s+de\s+precio)\b", norm):
        srv = _extraer_nombre_servicio(mensaje)
        m_num = re.search(r"(?:ahora\s+son|ahora\s+sale|a|subi[oó]\s+a|subio\s+a|en)\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\b", norm)
        monto = None
        if m_num:
            monto = _parsear_monto_argentino(m_num.group(1))
        else:
            m_alt = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\b", norm)
            if m_alt:
                monto = _parsear_monto_argentino(m_alt.group(1))
        return True, srv, monto
    return False, None, None

def _es_consulta_suscripciones(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    frases = [
        "cuanto gasto en suscripciones",
        "cuanto pago por mes en suscripciones",
        "cuanto pago en suscripciones",
        "que suscripciones tengo",
        "cuales son mis suscripciones",
        "mis suscripciones",
        "suscripciones activas",
        "cuanto pago por mes",
    ]
    return any(f in norm for f in frases)

def _detectar_ambiguedad_suscripcion(mensaje: str) -> tuple[bool, str | None]:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False, None
    if _es_senial_suscripcion(mensaje):
        return False, None
    if _extraer_frecuencia_mencionada(mensaje):
        return False, None
    if _es_senial_gasto_suelto(mensaje):
        return False, None

    srv = identificar_servicio_en_texto(mensaje)
    if srv:
        if re.search(r"\b(?:pagu[eé]|abone|abon[eé])\b", norm):
            return True, srv["nombre"]
        if norm in (normalizar_texto(srv["nombre"]), f"el {normalizar_texto(srv['nombre'])}", f"la {normalizar_texto(srv['nombre'])}"):
            return True, srv["nombre"]

    return False, None

def _es_confirmacion_gasto_aparte(mensaje: str) -> bool:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False
    frases = [
        "es un gasto aparte", "gasto aparte", "es aparte", "aparte",
        "es otro gasto", "es otro", "otro gasto", "anotalo igual",
        "anotarlo igual", "es nuevo", "nuevo", "si anotalo", "si, anotalo"
    ]
    return any(f in norm for f in frases)

def _es_intento_alta_suscripcion(mensaje: str) -> bool:
    if _es_senial_gasto_suelto(mensaje):
        return False
    if _es_senial_suscripcion(mensaje):
        return True
    frec = _extraer_frecuencia_mencionada(mensaje)
    if frec:
        srv = _extraer_nombre_servicio(mensaje)
        if srv:
            return True
    return False

