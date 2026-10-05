"""
Catálogo oficial de entidades bancarias y billeteras de Argentum.
Sincronizado exactamente con el selector del frontend (src/lib/constants/banks.ts).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

# Catálogo completo de 32 entidades exactamente como en banks.ts
ENTIDADES: dict[str, dict[str, Any]] = {
    "mercadopago": {
        "nombre": "Mercado Pago",
        "fuente": {
            "tipo": "argentinadatos_fci",
            "fondo": "Mercado Fondo - Clase A",
        },
    },
    "uala": {
        "nombre": "Ualá",
        "fuente": {
            "tipo": "argentinadatos_cuentas",
            "base": "UALA",
            "niveles": ["UALA PLUS 1", "UALA PLUS 2"],
        },
    },
    "naranjax": {
        "nombre": "Naranja X",
        "fuente": {
            "tipo": "argentinadatos_cuentas",
            "base": "NARANJA X",
            "niveles": [],
        },
    },
    "personalpay": {
        "nombre": "Personal Pay",
        "fuente": None,
    },
    "prex": {
        "nombre": "Prex",
        "fuente": {
            "tipo": "argentinadatos_fci",
            "fondo": "Allaria Ahorro - Clase E",
        },
    },
    "paypal": {
        "nombre": "PayPal",
        "fuente": None,
    },
    "cocos": {
        "nombre": "Cocos",
        "fuente": None,
    },
    "arq": {
        "nombre": "ARQ",
        "fuente": None,
    },
    "ieb": {
        "nombre": "IEB+",
        "fuente": None,
    },
    "n1u": {
        "nombre": "N1U",
        "fuente": None,
    },
    "astropay": {
        "nombre": "Astropay",
        "fuente": None,
    },
    "letsbit": {
        "nombre": "LetsBit",
        "fuente": None,
    },
    "fiwind": {
        "nombre": "Fiwind",
        "fuente": {
            "tipo": "argentinadatos_cuentas",
            "base": "FIWIND",
            "niveles": [],
        },
    },
    "brubank": {
        "nombre": "Brubank",
        "fuente": {
            "tipo": "argentinadatos_cuentas",
            "base": "BRUBANK",
            "niveles": [],
        },
    },
    "lemon": {
        "nombre": "Lemon",
        "fuente": None,
    },
    "cuentadni": {
        "nombre": "Cuenta DNI",
        "fuente": None,
    },
    "galicia": {
        "nombre": "Galicia",
        "fuente": None,
    },
    "santander": {
        "nombre": "Santander",
        "fuente": None,
    },
    "bbva": {
        "nombre": "BBVA",
        "fuente": None,
    },
    "macro": {
        "nombre": "Macro",
        "fuente": None,
    },
    "nacion": {
        "nombre": "Banco Nación",
        "fuente": {
            "tipo": "argentinadatos_cuentas",
            "base": None,
            "niveles": ["BNA"],
        },
    },
    "provincia": {
        "nombre": "Banco Provincia",
        "fuente": None,
    },
    "hipotecario": {
        "nombre": "Banco Hipotecario",
        "fuente": None,
    },
    "icbc": {
        "nombre": "ICBC",
        "fuente": None,
    },
    "hsbc": {
        "nombre": "HSBC",
        "fuente": None,
    },
    "supervielle": {
        "nombre": "Supervielle",
        "fuente": {
            "tipo": "argentinadatos_cuentas",
            "base": None,
            "niveles": ["SUPERVIELLE", "SUPERVIELLE HIT IOL"],
        },
    },
    "bancosantafe": {
        "nombre": "Banco Santa Fe",
        "fuente": None,
    },
    "credicoop": {
        "nombre": "Banco Credicoop",
        "fuente": None,
    },
    "balanz": {
        "nombre": "Balanz",
        "fuente": None,
    },
    "iol": {
        "nombre": "IOL (InvertirOnline)",
        "fuente": None,
    },
    "ppi": {
        "nombre": "Portfolio Personal Inversiones",
        "fuente": None,
    },
    "cohen": {
        "nombre": "Cohen",
        "fuente": None,
    },
}


def normalizar_texto(texto: str) -> str:
    """Pasa a minúsculas, saca acentos y deja solo letras y números."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", ascii_text.lower())


def inferir_entidad(nombre: Optional[str]) -> Optional[str]:
    """
    Infiere el id de entidad a partir del nombre de la billetera.
    Pasa a minúsculas, saca acentos y deja solo letras y números;
    devuelve el id cuyo id o nombre normalizado coincide exacto, o None.
    """
    if not nombre:
        return None
    norm_nombre = normalizar_texto(nombre)
    if not norm_nombre:
        return None

    for ent_id, info in ENTIDADES.items():
        if norm_nombre == normalizar_texto(ent_id) or norm_nombre == normalizar_texto(info["nombre"]):
            return ent_id
    return None


def entidad_de_billetera(b: Any) -> Optional[str]:
    """
    Determina el id de entidad asociado a una billetera:
    b.entidad_id si está en ENTIDADES; si no, inferir_entidad(b.nombre).
    """
    if b is None:
        return None
    ent_id = getattr(b, "entidad_id", None)
    if ent_id and ent_id in ENTIDADES:
        return ent_id
    nombre = getattr(b, "nombre", None)
    return inferir_entidad(nombre)


def opciones_de_entidad(entidad_id: Optional[str]) -> tuple[str | None, str | None, list[str]]:
    """
    Devuelve (tipo, clave_base, claves_validas) para una entidad:
    - tipo: "cuenta", "fci" o None
    - clave_base: clave base de tasa (o fondo para fci) o None
    - claves_validas: lista de claves válidas en orden: base si existe, después los niveles; para fci, [fondo]
    """
    if not entidad_id or entidad_id not in ENTIDADES:
        return (None, None, [])

    info = ENTIDADES[entidad_id]
    fuente_info = info.get("fuente")
    if not fuente_info or not isinstance(fuente_info, dict):
        return (None, None, [])

    tipo_raw = fuente_info.get("tipo")
    if tipo_raw == "argentinadatos_cuentas":
        tipo = "cuenta"
        base = fuente_info.get("base")
        niveles = list(fuente_info.get("niveles", []))
        claves_validas = ([base] if base else []) + niveles
        return (tipo, base, claves_validas)
    elif tipo_raw == "argentinadatos_fci":
        tipo = "fci"
        fondo = fuente_info.get("fondo")
        claves_validas = [fondo] if fondo else []
        return (tipo, fondo, claves_validas)

    return (None, None, [])

