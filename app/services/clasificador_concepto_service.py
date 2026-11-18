# -*- coding: utf-8 -*-
"""
app/services/clasificador_concepto_service.py — Clasificador de conceptos con IA.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.catalogo_categorias import ARBOL_CATEGORIAS, resolver_alias
from app.core.config import settings
from app.core.constants import CATEGORIAS_SISTEMA
from app.models.uso_ia import UsoIA
from app.models.usuario import Usuario
from app.services import categoria_service
from app.services.categoria_busqueda_service import normalizar_busqueda
from app.services.openai_client import get_openai_client
from app.services.uso_ia_service import registrar_uso_ia, verificar_cupo_ia
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina

logger = logging.getLogger(__name__)

TOPE_DIARIO_CLASIFICADOR = 60
MAX_CACHE_TEXTOS = 500
MAX_OPCIONES_IA = 3
MAX_CHARS_TEXTO = 120
MAX_TOKENS_CLASIFICADOR = 120
TIMEOUT_CLASIFICADOR = 5.0
TEMPERATURE_CLASIFICADOR = 0

SEPARADOR_OPCION = " \u203a "

SYSTEM_PROMPT_CLASIFICADOR = (
    "Sos el clasificador de gastos e ingresos de Argentum, una app de finanzas "
    "personales de Argentina. La persona escribe en pocas palabras un gasto o un "
    "ingreso, como lo dir\u00eda hablando. Eleg\u00ed de la lista las opciones que mejor lo "
    "describen: primero la que mejor encaja y, solo si tambi\u00e9n podr\u00edan ser, hasta "
    "dos m\u00e1s. Eleg\u00ed siempre la subcategor\u00eda m\u00e1s espec\u00edfica; us\u00e1 "
    "\"Categor\u00eda \u203a Otros\" solo si sab\u00e9s el rubro pero ninguna subcategor\u00eda "
    "encaja. Entend\u00e9 el concepto aunque no est\u00e9 en los ejemplos: los ejemplos de "
    "cada subcategor\u00eda son orientativos.\n"
    "\n"
    "REGLAS DE CATEGOR\u00cdA (OBLIGATORIAS):\n"
    "- El concepto decide la categor\u00eda, nunca la forma de pago: tarjeta, visa, "
    "master, d\u00e9bito, transferencia o Mercado Pago no son categor\u00edas.\n"
    "- Plata que el usuario le pasa a una persona sin decir para qu\u00e9 "
    "(\"le transfer\u00ed 3000 a Manuel\"): egreso con categor\u00eda \"Pagos a personas\". "
    "Si dice para qu\u00e9 (\"le transfer\u00ed 3000 a Manuel por el bud\u00edn\"), va la "
    "categor\u00eda del concepto.\n"
    "- Plata que una persona le pasa al usuario sin decir para qu\u00e9 "
    "(\"Manuel me pas\u00f3 3000\"): ingreso con categor\u00eda \"Cobros de personas\".\n"
    "- Si es de la familia (mam\u00e1, pap\u00e1, mi vieja, mi viejo, mis viejos, abuela, "
    "abuelo, hijo, hija), la categor\u00eda es \"Ayuda familiar\", en egreso o en "
    "ingreso.\n"
    "- Si sab\u00e9s el rubro pero no la subcategor\u00eda, devolv\u00e9 el nombre de la "
    "categor\u00eda (ej: \"Alimentaci\u00f3n\"), nunca \"Otros\".\n"
    "\n"
    "CONCEPTOS RAROS O DESCONOCIDOS:\n"
    "Si el usuario menciona un gasto o ingreso con un concepto que no conoc\u00e9s, "
    "no existe o no tiene sentido financiero conocido (ej: 'un coso cu\u00e1ntico "
    "intergal\u00e1ctico', 'la mar en coche', 'sarasa', etc.), clasificalo SIEMPRE como "
    "\"Otros \u203a Otros\". NUNCA inventes categor\u00edas o subcategor\u00edas inexistentes."
)

_cache_prompts: dict[tuple[str, str], str] = {}
_cache_clasificador: dict[tuple[str, str, str], list[tuple[str, str | None]]] = {}


def limpiar_caches_clasificador() -> None:
    """Limpia las cach\u00e9s en memoria del clasificador (uso en tests)."""
    _cache_prompts.clear()
    _cache_clasificador.clear()


def _tipo_a_str(valor: Any) -> str:
    """Convierte un tipo de categor\u00eda (enum o str) a min\u00fasculas."""
    if hasattr(valor, "value"):
        return str(valor.value).lower()
    return str(valor).lower()


def _categorias_y_subcategorias_visibles(
    db: Session,
    tipo: str,
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Devuelve categor\u00edas visibles del tipo con sus subcategor\u00edas ordenadas."""
    if tipo not in ("egreso", "ingreso"):
        return []
    cats_globales, subs_globales = categoria_service.obtener_categorias_globales(db)

    cats_visibles: list[dict[str, Any]] = []
    for cat in cats_globales:
        if _tipo_a_str(cat["tipo"]) != tipo:
            continue
        nombre = cat["nombre"]
        if nombre == "Ahorro" or nombre in CATEGORIAS_SISTEMA:
            continue
        if resolver_alias(nombre) in CATEGORIAS_SISTEMA:
            continue
        cats_visibles.append(cat)

    cats_visibles.sort(key=lambda c: (c.get("orden", 0), c["nombre"]))

    subs_por_cat: dict[str, list[dict[str, Any]]] = {}
    for sub in subs_globales:
        if sub.get("es_sistema"):
            continue
        cid = str(sub["categoria_id"])
        subs_por_cat.setdefault(cid, []).append(sub)

    resultado: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for cat in cats_visibles:
        subs = sorted(
            subs_por_cat.get(str(cat["id"]), []),
            key=lambda s: (s.get("orden", 0), s["nombre"]),
        )
        resultado.append((cat, subs))
    return resultado


def opciones_validas(db: Session, tipo: str) -> list[str]:
    """
    Arma la lista ordenada de opciones v\u00e1lidas 'Categor\u00eda \u203a Subcategor\u00eda'
    y 'Categor\u00eda \u203a Otros' para el tipo pedido desde la base.
    """
    opciones: list[str] = []
    for cat, subs in _categorias_y_subcategorias_visibles(db, tipo):
        cat_nombre = cat["nombre"]
        for sub in subs:
            if sub["nombre"] == "Otros":
                continue
            opciones.append(f"{cat_nombre}{SEPARADOR_OPCION}{sub['nombre']}")
        opciones.append(f"{cat_nombre}{SEPARADOR_OPCION}Otros")
    return opciones


def construir_system_prompt(db: Session, tipo: str) -> str:
    """
    Arma el prompt del sistema del clasificador para un tipo, combinando
    SYSTEM_PROMPT_CLASIFICADOR y el listado del cat\u00e1logo, cacheado por (tipo, hash).
    """
    opciones = opciones_validas(db, tipo)
    hash_op = hashlib.sha256("\n".join(opciones).encode("utf-8")).hexdigest()
    clave = (tipo, hash_op)
    if clave in _cache_prompts:
        return _cache_prompts[clave]

    desc_sub_map: dict[tuple[str, str, str], str] = {
        (cat.nombre, cat.tipo, sub.nombre): sub.descripcion
        for cat in ARBOL_CATEGORIAS
        for sub in cat.subcategorias
    }
    desc_otros_map: dict[tuple[str, str], str | None] = {
        (cat.nombre, cat.tipo): cat.descripcion_otros
        for cat in ARBOL_CATEGORIAS
    }

    lineas = [
        SYSTEM_PROMPT_CLASIFICADOR.rstrip(),
        "",
        "LISTADO OFICIAL DE CATEGOR\u00cdAS Y SUBCATEGOR\u00cdAS PERMITIDAS:",
        "Al categorizar, deb\u00e9s elegir siempre la subcategor\u00eda m\u00e1s espec\u00edfica "
        "o la categor\u00eda correspondiente de este listado:",
    ]

    for cat, subs in _categorias_y_subcategorias_visibles(db, tipo):
        cat_nombre = cat["nombre"]
        cat_canon = resolver_alias(cat_nombre)
        desc_otros = desc_otros_map.get((cat_canon, tipo)) or desc_otros_map.get(
            (cat_nombre, tipo)
        )
        if desc_otros:
            lineas.append(
                f"- {cat_nombre} ({tipo}). Otros de esta categor\u00eda: {desc_otros}"
            )
        else:
            lineas.append(f"- {cat_nombre} ({tipo})")

        for sub in subs:
            sub_nombre = sub["nombre"]
            if sub_nombre == "Otros":
                continue
            sub_canon = resolver_alias(sub_nombre)
            sub_desc = (
                desc_sub_map.get((cat_canon, tipo, sub_canon))
                or desc_sub_map.get((cat_nombre, tipo, sub_nombre))
                or ""
            )
            if sub_desc:
                lineas.append(f"  - {sub_nombre}: {sub_desc}")
            else:
                lineas.append(f"  - {sub_nombre}")

    prompt = "\n".join(lineas) + "\n"
    _cache_prompts[clave] = prompt
    return prompt


def construir_schema_clasificador(opciones: list[str]) -> dict[str, Any]:
    """Construye el JSON Schema estricto con enum cerrado de opciones."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "clasificador_concepto_response",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "opciones": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": opciones,
                        },
                    },
                },
                "required": ["opciones"],
                "additionalProperties": False,
            },
        },
    }


def llamar_clasificador(
    mensajes: list[dict[str, str]],
    schema: dict[str, Any],
    model: str | None = None,
    temperature: float = TEMPERATURE_CLASIFICADOR,
    max_tokens: int = MAX_TOKENS_CLASIFICADOR,
) -> Any:
    """Llama a OpenAI chat.completions con Structured Outputs y timeout de 5s."""
    cliente = get_openai_client().with_options(timeout=TIMEOUT_CLASIFICADOR)
    return cliente.chat.completions.create(
        model=model or settings.OPENAI_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=mensajes,
        response_format=schema,
    )


def _extraer_contenido_respuesta(respuesta: Any) -> str:
    """Extrae el texto JSON de la primera opci\u00f3n devuelta por OpenAI."""
    if isinstance(respuesta, dict):
        choices = respuesta.get("choices") or []
        if not choices:
            return "{}"
        msg = choices[0].get("message") or {}
        return msg.get("content") or "{}"
    choices = respuesta.choices or []
    if not choices:
        return "{}"
    return choices[0].message.content or "{}"


def _parsear_opciones(
    contenido: str,
    opciones_enum: list[str],
) -> list[tuple[str, str | None]]:
    """Valida contra el enum, deduplica y toma hasta 3 pares (cat, sub | None)."""
    permitidas = set(opciones_enum)
    try:
        datos = json.loads(contenido or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(datos, dict):
        return []
    crudo = datos.get("opciones")
    if not isinstance(crudo, list):
        return []

    pares: list[tuple[str, str | None]] = []
    vistos: set[tuple[str, str | None]] = set()
    for elem in crudo:
        if not isinstance(elem, str) or elem not in permitidas:
            continue
        if SEPARADOR_OPCION not in elem:
            continue
        cat_nombre, sub_nombre = elem.split(SEPARADOR_OPCION, 1)
        par: tuple[str, str | None] = (
            (cat_nombre, None) if sub_nombre == "Otros" else (cat_nombre, sub_nombre)
        )
        if par in vistos:
            continue
        vistos.add(par)
        pares.append(par)
        if len(pares) >= MAX_OPCIONES_IA:
            break
    return pares


def _clasificar_con_respuesta(
    db: Session,
    texto: str,
    tipo: str,
) -> tuple[list[tuple[str, str | None]], Any]:
    """Ejecuta la clasificaci\u00f3n contra OpenAI y devuelve (pares, respuesta)."""
    if tipo not in ("egreso", "ingreso"):
        return [], None
    texto_recortado = (texto or "").strip()[:MAX_CHARS_TEXTO]
    if not texto_recortado:
        return [], None
    opciones = opciones_validas(db, tipo)
    if not opciones:
        return [], None

    prompt = construir_system_prompt(db, tipo)
    schema = construir_schema_clasificador(opciones)
    mensajes = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": texto_recortado},
    ]
    respuesta = llamar_clasificador(
        mensajes,
        schema,
        model=settings.OPENAI_MODEL,
        temperature=TEMPERATURE_CLASIFICADOR,
        max_tokens=MAX_TOKENS_CLASIFICADOR,
    )
    contenido = _extraer_contenido_respuesta(respuesta)
    pares = _parsear_opciones(contenido, opciones)
    return pares, respuesta


def clasificar_sin_limites(
    db: Session,
    texto: str,
    tipo: str,
) -> list[tuple[str, str | None]]:
    """Clasifica un concepto sin cach\u00e9 ni cupos (uso en evaluaci\u00f3n y pruebas)."""
    pares, _ = _clasificar_con_respuesta(db, texto, tipo)
    return pares


def clasificar_concepto(
    db: Session,
    usuario: Usuario,
    texto: str,
    tipo: str,
) -> tuple[list[tuple[str, str | None]], Any]:
    """
    Clasifica un concepto respetando largo m\u00ednimo, cupo mensual, tope diario
    de 60 por usuario y cach\u00e9 de hasta 500 textos. Nunca propaga errores.
    """
    if tipo not in ("egreso", "ingreso"):
        return [], None
    texto_norm = normalizar_busqueda(texto)
    if len(texto_norm) < 3:
        return [], None

    permitido, _ = verificar_cupo_ia(db, usuario, "busqueda")
    if not permitido:
        return [], None

    hoy = hoy_argentina()
    inicio_dia_ar = datetime(
        hoy.year, hoy.month, hoy.day, 0, 0, 0, tzinfo=TZ_ARGENTINA
    )
    usos_hoy = (
        db.scalar(
            select(func.count(UsoIA.id)).where(
                UsoIA.usuario_id == usuario.id,
                UsoIA.funcion == "clasificar_concepto",
                UsoIA.fecha >= inicio_dia_ar,
            )
        )
        or 0
    )
    if usos_hoy >= TOPE_DIARIO_CLASIFICADOR:
        return [], None

    clave_cache = (settings.OPENAI_MODEL, tipo, texto_norm)
    if clave_cache in _cache_clasificador:
        return list(_cache_clasificador[clave_cache]), None

    try:
        pares, respuesta = _clasificar_con_respuesta(db, texto, tipo)
    except Exception:
        logger.exception("Error al clasificar concepto con IA")
        return [], None

    if len(_cache_clasificador) >= MAX_CACHE_TEXTOS:
        mas_viejo = next(iter(_cache_clasificador))
        del _cache_clasificador[mas_viejo]
    _cache_clasificador[clave_cache] = list(pares)

    if respuesta is not None:
        registrar_uso_ia(
            usuario.id,
            "clasificar_concepto",
            settings.OPENAI_MODEL,
            respuesta,
        )

    return pares, respuesta
