from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.utils.texto import normalizar_texto


_CATALOGO_PATH = Path(__file__).resolve().parent / "catalogo_suscripciones.json"


def _cargar_datos_catalogo() -> dict[str, Any]:
    with open(_CATALOGO_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"servicios": data, "reglas_categoria": []}
    return data


_DATOS_CATALOGO: dict[str, Any] = _cargar_datos_catalogo()
CATALOGO_SERVICIOS: list[dict[str, Any]] = _DATOS_CATALOGO.get("servicios", [])
REGLAS_CATEGORIA: list[dict[str, Any]] = _DATOS_CATALOGO.get("reglas_categoria", [])


def cargar_catalogo() -> list[dict[str, Any]]:
    """Carga los servicios del catálogo de suscripciones compartido."""
    return CATALOGO_SERVICIOS


def normalizar_para_sugerencia(texto: str) -> str:
    """
    Normalización determinística para sugerencia de categorías:
    - minúsculas y sin acentos (NFD, quitando las marcas)
    - todo lo que no sea letra a-z o dígito pasa a espacio
    - se colapsan los espacios y se hace trim.
    """
    if not texto:
        return ""
    # Minúsculas
    s = texto.lower()
    # NFD y quitar marcas de acentuación
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    # No a-z ni dígitos a espacio
    s = re.sub(r"[^a-z0-9]", " ", s)
    # Colapsar espacios y trim
    s = re.sub(r"\s+", " ", s).strip()
    return s


def buscar_servicio_por_texto(texto: str) -> Optional[dict[str, Any]]:
    """
    Busca un servicio en el catálogo por coincidencia exacta o por variantes,
    utilizando normalizar_texto.
    """
    if not texto:
        return None
    texto_norm = normalizar_texto(texto)
    for serv in CATALOGO_SERVICIOS:
        if normalizar_texto(serv["nombre"]) == texto_norm or normalizar_texto(serv["id"]) == texto_norm:
            return serv
        for v in serv.get("variantes", []):
            if normalizar_texto(v) == texto_norm:
                return serv
    return None


def identificar_servicio_en_texto(texto: str) -> Optional[dict[str, Any]]:
    """
    Busca si en el texto se menciona algún servicio del catálogo.
    Prueba primero match exacto y luego búsqueda por delimitadores de palabra,
    ordenando variantes de mayor a menor longitud.
    """
    if not texto:
        return None

    direct = buscar_servicio_por_texto(texto)
    if direct:
        return direct

    texto_norm = normalizar_texto(texto)
    candidatos = []
    for serv in CATALOGO_SERVICIOS:
        variantes = [serv["nombre"], serv["id"]] + serv.get("variantes", [])
        for v in variantes:
            v_norm = normalizar_texto(v)
            if len(v_norm) >= 3:
                candidatos.append((len(v_norm), v_norm, serv))

    candidatos.sort(key=lambda x: x[0], reverse=True)

    for _, v_norm, serv in candidatos:
        patron = r"(?:^|\s|[^\wáéíóúñ])" + re.escape(v_norm) + r"(?:$|\s|[^\wáéíóúñ])"
        if re.search(patron, texto_norm):
            return serv

    return None


def sugerir_categoria_suscripcion(nombre: str) -> tuple[str, Optional[str]]:
    """
    Reglas determinísticas para sugerir categoría y subcategoría según el nombre ingresado.
    Devuelve (categoria_nombre, subcategoria_nombre).
    """
    norm = normalizar_para_sugerencia(nombre)
    if not norm:
        return ("Otros", None)

    nombre_pad = f" {norm} "

    # Regla 0: Servicios no genéricos del catálogo
    for serv in CATALOGO_SERVICIOS:
        if serv.get("generico"):
            continue
        candidatos = [serv["nombre"], serv["id"]] + serv.get("variantes", [])
        for c in candidatos:
            c_norm = normalizar_para_sugerencia(c)
            if c_norm and f" {c_norm} " in nombre_pad:
                return (serv["categoria"], serv.get("subcategoria"))

    # Reglas 1 a 11
    for regla in REGLAS_CATEGORIA:
        coincide_regla = True
        for grupo in regla["grupos"]:
            coincide_grupo = False
            for clave in grupo:
                k_norm = normalizar_para_sugerencia(clave)
                if k_norm and f" {k_norm} " in nombre_pad:
                    coincide_grupo = True
                    break
            if not coincide_grupo:
                coincide_regla = False
                break

        if coincide_regla:
            return (regla["categoria"], regla.get("subcategoria"))

    # Sin coincidencia
    return ("Otros", None)


def resolver_categoria_sugerida(db: Session, nombre: str) -> tuple[UUID, Optional[UUID]]:
    """
    Resuelve los nombres de categoría y subcategoría sugeridos a sus IDs en la base de datos.
    Solo busca entre categorías de egreso por nombre exacto (case-insensitive).
    Si no resuelve, cae en 'Otros' sin subcategoría.
    """
    from app.models.categoria import Categoria, TipoCategoria
    from app.models.subcategoria import Subcategoria

    cat_nom, sub_nom = sugerir_categoria_suscripcion(nombre)

    cat = (
        db.query(Categoria)
        .filter(
            Categoria.tipo == TipoCategoria.EGRESO,
            func.lower(Categoria.nombre) == cat_nom.lower(),
        )
        .first()
    )

    if not cat:
        # Fallback a categoría 'Otros' de egreso
        cat_otros = (
            db.query(Categoria)
            .filter(
                Categoria.tipo == TipoCategoria.EGRESO,
                func.lower(Categoria.nombre) == "otros",
            )
            .first()
        )
        return (cat_otros.id, None) if cat_otros else (None, None)

    sub_id = None
    if sub_nom:
        sub = (
            db.query(Subcategoria)
            .filter(
                Subcategoria.categoria_id == cat.id,
                func.lower(Subcategoria.nombre) == sub_nom.lower(),
            )
            .first()
        )
        if sub:
            sub_id = sub.id

    return (cat.id, sub_id)
