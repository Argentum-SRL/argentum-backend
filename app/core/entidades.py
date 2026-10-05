"""
Catálogo oficial de entidades bancarias y billeteras de Argentum.
Sincronizado exactamente con el selector del frontend (src/lib/constants/banks.ts).
"""
from __future__ import annotations

from decimal import Decimal
import re
import unicodedata
from typing import Any, Optional

# Catálogo completo de 32 entidades con nueva estructura de opciones
ENTIDADES: dict[str, dict[str, Any]] = {
    "mercadopago": {
        "nombre": "Mercado Pago",
        "fuente": {
            "base": "Mercado Fondo - Clase A",
            "opciones": [
                {
                    "clave": "Mercado Fondo - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Mercado Fondo",
                    "tope": None,
                },
            ],
        },
    },
    "uala": {
        "nombre": "Ualá",
        "fuente": {
            "base": "UALA",
            "opciones": [
                {
                    "clave": "UALA",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Cuenta remunerada",
                    "tope": None,
                },
                {
                    "clave": "UALA PLUS 1",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Ualá Plus 1",
                    "tope": None,
                },
                {
                    "clave": "UALA PLUS 2",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Ualá Plus 2",
                    "tope": None,
                },
                {
                    "clave": "Ualintec Ahorro Pesos - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Ualintec Ahorro",
                    "tope": None,
                },
            ],
        },
    },
    "naranjax": {
        "nombre": "Naranja X",
        "fuente": {
            "base": "NARANJA X",
            "opciones": [
                {
                    "clave": "NARANJA X",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Cuenta remunerada",
                    "tope": None,
                },
            ],
        },
    },
    "personalpay": {
        "nombre": "Personal Pay",
        "fuente": {
            "base": "Delta Pesos - Clase A",
            "opciones": [
                {
                    "clave": "Delta Pesos - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Delta Pesos",
                    "tope": None,
                },
            ],
        },
    },
    "prex": {
        "nombre": "Prex",
        "fuente": {
            "base": "Allaria Ahorro - Clase E",
            "opciones": [
                {
                    "clave": "Allaria Ahorro - Clase E",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Allaria Ahorro",
                    "tope": None,
                },
            ],
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
            "base": "FIWIND",
            "opciones": [
                {
                    "clave": "FIWIND",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Cuenta remunerada",
                    "tope": None,
                },
            ],
        },
    },
    "brubank": {
        "nombre": "Brubank",
        "fuente": {
            "base": "BRUBANK",
            "opciones": [
                {
                    "clave": "BRUBANK",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Cuenta remunerada",
                    "tope": None,
                },
            ],
        },
    },
    "lemon": {
        "nombre": "Lemon",
        "fuente": {
            "base": "Vinci Compass Liquidez - Clase F",
            "opciones": [
                {
                    "clave": "Vinci Compass Liquidez - Clase F",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Vinci Compass Liquidez",
                    "tope": Decimal("2000000"),
                },
            ],
        },
    },
    "cuentadni": {
        "nombre": "Cuenta DNI",
        "fuente": None,
    },
    "galicia": {
        "nombre": "Galicia",
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "Fima Premium - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Fima Premium",
                    "tope": None,
                },
            ],
        },
    },
    "santander": {
        "nombre": "Santander",
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "Super Ahorro $ - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Super Ahorro $",
                    "tope": None,
                },
            ],
        },
    },
    "bbva": {
        "nombre": "BBVA",
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "FBA Money Market Pesos - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo FBA Money Market",
                    "tope": None,
                },
            ],
        },
    },
    "macro": {
        "nombre": "Macro",
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "Pionero Pesos - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Pionero Pesos",
                    "tope": None,
                },
            ],
        },
    },
    "nacion": {
        "nombre": "Banco Nación",
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "BNA",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Cuenta sueldo",
                    "tope": None,
                },
                {
                    "clave": "Pellegrini Renta Pesos - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Pellegrini Renta Pesos",
                    "tope": None,
                },
            ],
        },
    },
    "provincia": {
        "nombre": "Banco Provincia",
        "fuente": None,
    },
    "hipotecario": {
        "nombre": "Banco Hipotecario",
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "Toronto Trust Ahorro - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Toronto Trust Ahorro",
                    "tope": None,
                },
            ],
        },
    },
    "icbc": {
        "nombre": "ICBC",
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "Alpha Pesos - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Alpha Pesos",
                    "tope": None,
                },
            ],
        },
    },
    "hsbc": {
        "nombre": "HSBC",
        "fuente": None,
    },
    "supervielle": {
        "nombre": "Supervielle",
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "SUPERVIELLE",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Plan sueldo",
                    "tope": None,
                },
                {
                    "clave": "SUPERVIELLE HIT IOL",
                    "fuente": "argentinadatos_cuentas",
                    "etiqueta": "Cuenta Hit IOL",
                    "tope": None,
                },
                {
                    "clave": "Premier Renta CP en Pesos - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Premier Renta",
                    "tope": None,
                },
            ],
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
        "fuente": {
            "base": None,
            "opciones": [
                {
                    "clave": "Balanz Capital Money Market - Clase A",
                    "fuente": "argentinadatos_fci",
                    "etiqueta": "Fondo Balanz Money Market",
                    "tope": None,
                },
            ],
        },
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
    Devuelve (tipo_base, clave_base, claves_validas) para una entidad:
    - tipo_base: "cuenta", "fci" si clave_base no es None; si no, None
    - clave_base: clave base de tasa o None
    - claves_validas: lista de claves de todas las opciones en orden
    """
    if not entidad_id or entidad_id not in ENTIDADES:
        return (None, None, [])

    info = ENTIDADES[entidad_id]
    fuente_info = info.get("fuente")
    if not fuente_info or not isinstance(fuente_info, dict):
        return (None, None, [])

    base = fuente_info.get("base")
    opciones = fuente_info.get("opciones", [])
    claves_validas = [opt["clave"] for opt in opciones]

    tipo_base = None
    if base is not None:
        for opt in opciones:
            if opt["clave"] == base:
                fuente_opt = opt.get("fuente")
                if fuente_opt == "argentinadatos_cuentas":
                    tipo_base = "cuenta"
                elif fuente_opt == "argentinadatos_fci":
                    tipo_base = "fci"
                break

    return (tipo_base, base, claves_validas)


def opcion_de_entidad(entidad_id: Optional[str], clave: Optional[str]) -> Optional[dict[str, Any]]:
    """
    Busca y devuelve la opción de tasa correspondiente a una entidad y clave.
    """
    if not entidad_id or entidad_id not in ENTIDADES or not clave:
        return None

    info = ENTIDADES[entidad_id]
    fuente_info = info.get("fuente")
    if not fuente_info or not isinstance(fuente_info, dict):
        return None

    for opt in fuente_info.get("opciones", []):
        if opt["clave"] == clave:
            return opt
    return None

